#!/usr/bin/env python3
"""局域网多机协调器：让共享一台 AI Passport 的多台电脑自动切换持票权。

背景（用户诉求）
==============
同一局域网里 N 台电脑共用一枚 AI Passport 时，原本会因设备"谁最后 beacon 谁是主"
而每 ~1s 在两台间乱跳。用户要的是：**语音自动跟随"光标（焦点）停在哪台的 AI 工具
（WorkBuddy / Trae）上"**。

原理（纯 PC 端，不改动设备固件）
==============================
设备侧 peer 选择规则（udp_audio.c）极简：谁最后给它发 beacon，它就跟谁配对；且某台
3 秒没发 beacon 它就当那台掉了。因此**只要控制"哪台电脑在发 beacon"，就能控制设备跟
谁走**——完全在电脑端实现，不改用户不想重烧的固件。

每台电脑的转发器都跑一个 coordinator：
  - 通过协调端口（默认 UDP 33334）周期广播自己的极小状态：本机 IP、前台是否落在已
    配置的 Agent/AI 工具上（focus_ai）、前台标题、距上次活动多久（idle，相对量）。
  - 每台本地用同一套选举规则算出"当前唯一赢家"：在所有声明前台是 AI 工具的机器里，
    选 idle 最小（最近操作）的那台；并列用 IP 最小做确定性 tie-break。无人用 AI 工具
    时 sticky 保持上一任赢家（设备不至于掉线，下次拿起即用）。
  - 只有被算作赢家那台才向设备（端口 33333）发 beacon；其余停发。设备侧原样完成切换。

为什么广播 idle 而不是绝对时间戳
==============================
各机时钟不同步，绝对时间跨机不可比；idle（距上次活动的秒数，相对量）跨机天然可比，
无需 NTP 即可正确选出"最近/正在用的那台"。

录音中途切换保护
==============
本机正在录音（设备上键按住）时即便不再是焦点赢家也保住持票权，等松手后再让出，避免
断用户的录音。

线程模型
======
coordinator 用两个后台 daemon 线程做 UDP 收发（与转发器主 asyncio 循环解耦）；peers
字典用 RLock 保护。
"""

import ipaddress
import json
import socket
import sys
import threading
import time

try:
    from udp_transport import _local_ip_addrs as _imported_local_ip_addrs
except Exception:  # 独立单元测试时（未与 udp_transport 同目录）
    _imported_local_ip_addrs = None

COORD_TIMEOUT = 2.5            # 超过该时间未收到某 peer 广播则判其离线
COORD_BEACON_INTERVAL = 0.5    # 状态广播周期（秒）
COORD_MAGIC = b"\x00COORD"     # 协调包魔数，避免与别的 33334 服务混淆（首字节 0 防误判文本）


def _ip_key(ip):
    """把点分 IP 转成整数，便于确定性的最小 IP 比较。"""
    try:
        return int(ipaddress.ip_address(ip))
    except Exception:
        return 0


def elect_winner(local_ip, peers, last_winner_ip, recording):
    """纯函数：根据 peer 状态表选出当前赢家 IP，返回赢家 IP。

    规则：
      - 只认最近 COORD_TIMEOUT 内见过的 peer；
      - 候选 = 声明前台是 AI 工具（focus_ai）的机器；
      - 非空 → 选 (idle 最小, IP 最小) 的那台（idle 小=最近操作优先）；
      - 空（无人用 AI 工具）→ sticky：优先上一任赢家，否则 IP 最小（设备至少连一台）；
      - recording 豁免：本机正在录音且曾是赢家 → 强制本机赢，保护录音不被中途打断。
    """
    now = time.monotonic()
    live = {ip: p for ip, p in peers.items()
            if now - p.get("last_seen", 0) < COORD_TIMEOUT}
    if not live:
        return local_ip
    focused = [ip for ip, p in live.items() if p.get("focus_ai")]
    if focused:
        winner = min(focused, key=lambda ip: (live[ip].get("idle", 1e9),
                                             _ip_key(ip)))
    else:
        if last_winner_ip in live:
            winner = last_winner_ip
        else:
            winner = min(live.keys(), key=_ip_key)
    if recording and last_winner_ip == local_ip:
        winner = local_ip
    return winner


def make_local_ip():
    if _imported_local_ip_addrs:
        ips = _imported_local_ip_addrs()
        priv = [i for i in ips if i.startswith(("192.168.", "10.", "172."))]
        if priv:
            return priv[0]
    return "127.0.0.1"


class PeerCoordinator:
    """局域网多机协调器：广播本机状态 + 选举唯一赢家。"""

    def __init__(self, coord_port=33334, needles=None, local_ip=None):
        self.coord_port = coord_port
        self.needles = [(n or "").lower() for n in (needles or [])]
        self.local_ip = local_ip or make_local_ip()
        self._peers = {}                 # ip -> {focus_ai,title,idle,last_seen}; 含自己
        self._lock = threading.RLock()
        self._last_winner_ip = None
        self._stop = False
        self._sock = None
        self._threads = []
        # 本机活动状态
        self._local_focus_ai = False
        self._local_title = ""
        self._local_activity = time.monotonic()   # 最近一次前台变化/输入

    # -- 本机状态喂入（由转发器焦点观察器调用）--

    def update_local_focus(self, focus_ai, title):
        """喂入本机前台状态。focus_ai=True（前台是已配置 AI 工具）时刷新"最近活动"
        时间戳——因为持续停留在 AI 工具前台即视为正在用，idle 不会增长。"""
        now = time.monotonic()
        with self._lock:
            self._local_focus_ai = bool(focus_ai)
            self._local_title = title or ""
            if self._local_focus_ai:
                self._local_activity = now
            self._peers[self.local_ip] = {
                "focus_ai": self._local_focus_ai,
                "title": self._local_title,
                "idle": now - self._local_activity,
                "last_seen": now,
            }

    def note_local_input(self):
        """本机有键盘/鼠标输入时调用，刷新"最近活动"（辅助打破平局：真正在敲字的台
        idle 更小）。转发器若挂了全局输入钩子可调用；不调用也能靠焦点停留判断。"""
        now = time.monotonic()
        with self._lock:
            self._local_activity = now
            if self.local_ip in self._peers:
                self._peers[self.local_ip]["idle"] = 0.0
                self._peers[self.local_ip]["last_seen"] = now

    # -- 选举 --

    def is_winner(self, recording=False):
        with self._lock:
            peers = dict(self._peers)
            last = self._last_winner_ip
        winner = elect_winner(self.local_ip, peers, last, recording)
        with self._lock:
            self._last_winner_ip = winner
        return winner == self.local_ip

    def peer_count(self):
        now = time.monotonic()
        with self._lock:
            return sum(1 for p in self._peers.values()
                       if now - p.get("last_seen", 0) < COORD_TIMEOUT)

    def debug_state(self):
        with self._lock:
            return {
                "local_ip": self.local_ip,
                "last_winner_ip": self._last_winner_ip,
                "peers": {ip: dict(p) for ip, p in self._peers.items()},
            }

    # -- 生命周期 --

    def run(self):
        self._stop = False
        try:
            self._open_socket()
        except OSError:
            return
        t_recv = threading.Thread(target=self._recv_loop, name="coord-recv",
                                 daemon=True)
        t_snd = threading.Thread(target=self._send_loop, name="coord-send",
                                daemon=True)
        t_recv.start()
        t_snd.start()
        self._threads = [t_recv, t_snd]

    def stop(self):
        self._stop = True
        try:
            if self._sock is not None:
                self._sock.close()
        except Exception:
            pass

    def _open_socket(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self._sock.bind(("", self.coord_port))
        except OSError as e:
            print(f"[coord] 绑定协调端口 {self.coord_port} 失败: {e} "
                  f"（防火墙需允许本程序 UDP {self.coord_port} 入站/出站）",
                  file=sys.stderr)
            raise

    def _send_loop(self):
        targets = [("<broadcast>", self.coord_port)]
        while not self._stop:
            try:
                now = time.monotonic()
                with self._lock:
                    focus_ai = self._local_focus_ai
                    title = self._local_title
                    idle = now - self._local_activity
                payload = {
                    "v": 1,
                    "ip": self.local_ip,
                    "focus_ai": focus_ai,
                    "title": title,
                    "idle": round(idle, 2),
                }
                pkt = COORD_MAGIC + json.dumps(
                    payload, separators=(",", ":")).encode("utf-8")
                for _host, _port in targets:
                    try:
                        self._sock.sendto(pkt, (_host, _port))
                    except OSError:
                        pass
            except Exception:
                pass
            time.sleep(COORD_BEACON_INTERVAL)

    def _recv_loop(self):
        if self._sock is None:
            return
        self._sock.settimeout(0.5)
        while not self._stop:
            try:
                data, addr = self._sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) < len(COORD_MAGIC) or not data.startswith(COORD_MAGIC):
                continue
            src_ip = addr[0]
            if src_ip == self.local_ip:
                continue   # 自己的广播由 update_local_focus 维护
            try:
                body = json.loads(data[len(COORD_MAGIC):].decode("utf-8"))
            except Exception:
                continue
            ip = body.get("ip") or src_ip
            now = time.monotonic()
            with self._lock:
                self._peers[ip] = {
                    "focus_ai": bool(body.get("focus_ai")),
                    "title": body.get("title", ""),
                    "idle": float(body.get("idle", 1e9)),
                    "last_seen": now,
                }


if __name__ == "__main__":
    # 简易自检：起一个协调器，打印选举结果与 peer 数（Ctrl+C 退出）
    c = PeerCoordinator(needles=["workbuddy", "trae"])
    c.run()
    try:
        while True:
            time.sleep(2)
            print(f"[coord] 本机 {c.local_ip} 赢家? {c.is_winner()} "
                  f"在线 peer 数(含自己)={c.peer_count()}")
    except KeyboardInterrupt:
        c.stop()
        print("\n退出")
