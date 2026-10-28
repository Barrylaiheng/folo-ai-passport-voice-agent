#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 机触发助手(btrigger_daemon): 接收 A 机跨机直通的触发事件, 在 B 机本地注入。

背景(联调事件簿 §11.3, 2026-10-27): 双机场景原路径 = A 机注入 Ctrl+D →
DeskFlow 按光标转发, 光标状态/焦点脱节时 Passport 触发失灵。根治 = A 机转发器
把触发事件经本助手直通到 B 机本地注入, 按键路径与光标所在屏幕解耦。
音频链路(VBAN → CABLE)不变, 本助手只管"按键"这半边。

协议(与 hotkey_forwarder.py 的 CrossTriggerSender 契约一致, 改一侧必须同步另一侧):
    UDP 0.0.0.0:33334(A 机默认目标端口, 独立于设备通道 33333 / VBAN 6980)
    报文 = [0x21][UTF-8 JSON 行][4B FNV-1a 令牌尾(仅 token 非空时存在)]
    事件:
      {"event":"voice_key","phase":"start"} → 注入一次语音键(开始录音, toggle)
      {"event":"voice_key","phase":"stop"}  → 再注入一次(停止录音)
      {"event":"enter"}                     → 注入回车(确认发送)

安全:
    - --token 非空时校验 4B 令牌尾(与 A 机 cross_trigger.token 一致), 不匹配丢弃;
    - 来源白名单: --allow IP 锁死; 否则学习首个通过校验的来源, 之后仅收该来源;
    - 误注入防线: 前台窗口标题/进程须匹配 --app-substr(默认 WorkBuddy)才注入。

用法(B 机, 纯标准库零依赖):
    python btrigger_daemon.py                          # 默认: WorkBuddy + ctrl+d
    python btrigger_daemon.py --token mysecret --allow 192.168.110.127
    python btrigger_daemon.py --dry-run                # 只解析打印, 不注入(自测)
    python btrigger_daemon.py --install-startup        # 写登录自启脚本并退出
日志: 同目录 btrigger.log(直写文件, 兼容窗口化运行)。
"""
import argparse
import ctypes
import json
import os
import socket
import sys
import threading
import time

CROSS_EVT_TYPE = 0x21
DEFAULT_PORT = 33334
RECV_BUF = 1024
BEACON_TYPE = 0x22            # B 机助手广播 beacon: A 机据此自动发现, 与设备 0x01-0x04 / 触发 0x21 区分
DEFAULT_BEACON_PORT = 33335


def get_local_ip():
    """本机局域网 IPv4(beacon 宣告用, 让 A 机学到 B 的 IP, 免手填)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

# ---------------------------------------------------------------- 日志 ------

_LOG = None


def vlog(msg):
    """直写文件日志(窗口化/无控制台运行时 stdout 是黑洞, 教训见 vban_sender)。"""
    global _LOG
    line = time.strftime("[%Y-%m-%d %H:%M:%S] ") + str(msg)
    try:
        print(line)
    except Exception:
        pass
    if _LOG is None:
        try:
            _LOG = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "btrigger.log"), "a", encoding="utf-8")
        except Exception:
            return
    try:
        _LOG.write(line + "\n")
        _LOG.flush()
    except Exception:
        pass


# ------------------------------------------------- 令牌(与发送端同构) -------

def _fnv1a4(token):
    """令牌字符串 → 4 字节校验尾(FNV-1a 32bit, 与 udp_transport 同构)。"""
    h = 0x811C9DC5
    for ch in str(token).encode("utf-8"):
        h ^= ch
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h.to_bytes(4, "little")


# ------------------------------------------------ 按键注入(ctypes) ---------

# VK + 扫描码(标准 PS/2 set 1)。只需修饰键(ctrl/alt/shift/win)+ 常用主键。
VK_SCAN = {
    "ctrl": (0x11, 0x1D, False), "control": (0x11, 0x1D, False),
    "alt": (0x12, 0x38, False), "shift": (0x10, 0x2A, False),
    "win": (0x5B, 0x5B, True),
    "enter": (0x0D, 0x1C, False), "return": (0x0D, 0x1C, False),
    "d": (0x44, 0x20, False), "v": (0x56, 0x2F, False), "h": (0x48, 0x23, False),
    "f6": (0x75, 0x40, False),
}


def resolve_key_spec(spec):
    """\"ctrl+d\" → [(name, vk, scan, extended)] 序列(修饰键在前, 主键最后)。"""
    keys = [k.strip().lower() for k in str(spec).split("+") if k.strip()]
    out = []
    for k in keys:
        if k not in VK_SCAN:
            raise ValueError(f"未知键名: {k} (支持: {', '.join(sorted(VK_SCAN))})")
        vk, scan, ext = VK_SCAN[k]
        out.append((k, vk, scan, ext))
    return out


def _send_key(vk, scan, extended, up):
    """扫描码事件注入(与 hotkey_forwarder._send_key 同款, 最接近真实键盘)。"""
    class _KINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

    class _INPUT(ctypes.Structure):
        class _U(ctypes.Union):
            _fields_ = [("ki", _KINPUT)]
        _anonymous_ = ("u",)
        _fields_ = [("type", ctypes.c_ulong), ("u", _U)]

    KEYEVENTF_SCANCODE = 0x0008
    KEYEVENTF_EXTENDEDKEY = 0x0001
    KEYEVENTF_KEYUP = 0x0002
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= KEYEVENTF_KEYUP
    ki = _KINPUT(0, scan, flags, 0, None)
    inp = _INPUT(1, _INPUT._U(ki=ki))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def tap_key(spec, action=""):
    """完整按下一组键再抬起(toggle 语义: WorkBuddy 按一次开/再按一次关)。"""
    keys = resolve_key_spec(spec)
    for _n, vk, scan, ext in keys:
        _send_key(vk, scan, ext, up=False)
        time.sleep(0.02)
    for _n, vk, scan, ext in reversed(keys):
        _send_key(vk, scan, ext, up=True)
    vlog(f"[key] 注入 {spec} ({action})")


# ------------------------------------------------ 前台窗口判定 --------------

def get_foreground_title():
    """前台窗口标题 + 进程 exe 名(判 app 匹配用; 失败返回 ("",""))。"""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = ""
        try:
            k32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False,
                                pid.value)
            if h:
                pbuf = ctypes.create_unicode_buffer(512)
                size = ctypes.c_ulong(512)
                if k32.QueryFullProcessImageNameW(h, 0, pbuf, ctypes.byref(size)):
                    exe = os.path.basename(pbuf.value)
                k32.CloseHandle(h)
        except Exception:
            pass
        return buf.value, exe
    except Exception:
        return "", ""


def app_matches(app_substr, title, exe):
    """前台标题或进程名包含 app_substr 即命中(大小写不敏感)。"""
    s = str(app_substr or "").strip().lower()
    if not s:
        return True
    return s in (title or "").lower() or s in (exe or "").lower()


# ------------------------------------------------ Agent 窗口找回/激活 -------
# v0.4.1(2026-10-28 B 机联调回执 13.3): 直通解决「按键跨机送达」后,
# 还剩「前台不是 Agent / Agent 窗口在后台」导致注入落空。与 A 机
# _recover_agent 同语义: 注入前把 Agent 窗口找回并激活, 兜死焦点问题。

def find_agent_window(app_substr):
    """枚举顶层可见窗口, 返回第一个标题含 app_substr 的 hwnd(找不到 0)。"""
    user32 = ctypes.windll.user32
    found = []

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            t = (buf.value or "").lower()
            if t and str(app_substr).strip().lower() in t:
                found.append(hwnd)
                return False
        except Exception:
            pass
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p,
                              ctypes.c_void_p)(_cb)
    try:
        user32.EnumWindows(proc, 0)
    except Exception:
        pass
    return found[0] if found else 0


def activate_agent_window(app_substr):
    """前台已匹配→True; 否则找回 Agent 窗口激活后再判(成功 True)。

    SetForegroundWindow 受前台锁限制, 先模拟一次 ALT 按放解锁(无副作用)。
    """
    title, exe = get_foreground_title()
    if app_matches(app_substr, title, exe):
        return True
    hwnd = find_agent_window(app_substr)
    if not hwnd:
        vlog(f"[focus] 找不到 {app_substr!r} 可见窗口 (前台 {title!r})")
        return False
    user32 = ctypes.windll.user32
    ok = False
    try:
        if user32.IsIconic(hwnd):            # 最小化先还原
            user32.ShowWindow(hwnd, 9)       # SW_RESTORE
            time.sleep(0.05)
        user32.keybd_event(0x12, 0, 0, 0)    # ALT down: 解前台锁
        user32.keybd_event(0x12, 0, 2, 0)    # ALT up
        ok = bool(user32.SetForegroundWindow(hwnd))
        time.sleep(0.08)
    except Exception as e:
        vlog(f"[focus] 激活异常: {e}")
    title2, exe2 = get_foreground_title()
    matched = app_matches(app_substr, title2, exe2)
    vlog(f"[focus] 找回 {app_substr!r}: {title!r}({exe!r}) → {title2!r}"
         f"(SetForeground={'ok' if ok else 'fail'}, 匹配={'是' if matched else '否'})")
    return matched


# ------------------------------------------------ 主循环 -------------------

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="B 机触发助手(接收 A 机直通事件并本地注入)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"监听端口(默认 {DEFAULT_PORT}, 须与 A 机 cross_trigger.port 一致)")
    ap.add_argument("--token", default="", help="配对令牌(与 A 机 cross_trigger.token 一致; 留空不校验)")
    ap.add_argument("--allow", default="", help="来源 IP 白名单(如 A 机 192.168.110.127); 留空=学习首个合法来源")
    ap.add_argument("--app-substr", default="WorkBuddy",
                    help="前台窗口/进程须包含此子串才注入语音键(默认 WorkBuddy)")
    ap.add_argument("--voice-key", default="ctrl+d",
                    help="语音键(默认 ctrl+d, 与 A 机 apps 配置一致)")
    ap.add_argument("--enter-key", default="enter", help="回车事件注入的键(默认 enter)")
    ap.add_argument("--dry-run", action="store_true",
                    help="只解析事件并打印, 不真正注入(链路自测用)")
    ap.add_argument("--install-startup", action="store_true",
                    help="在启动文件夹写登录自启 .bat 后退出")
    ap.add_argument("--beacon-port", type=int, default=DEFAULT_BEACON_PORT,
                    help="广播 beacon 端口(默认 33335, 供 A 机自动发现; 0=关闭)")
    args = ap.parse_args()

    if args.install_startup:
        startup = os.path.join(os.environ.get("APPDATA", ""),
                               "Microsoft", "Windows", "Start Menu",
                               "Programs", "Startup")
        bat = os.path.join(startup, "btrigger_daemon.bat")
        py = sys.executable
        with open(bat, "w", encoding="gbk", errors="replace") as f:
            f.write(f'@echo off\r\nstart "" /min "{py}" '
                    f'"{os.path.abspath(__file__)}" --token "{args.token}" '
                    f'--allow "{args.allow}" --app-substr "{args.app_substr}" '
                    f'--voice-key "{args.voice_key}" --port {args.port}\r\n')
        print(f"已写登录自启: {bat}")
        return

    tail = _fnv1a4(args.token) if str(args.token).strip() else b""
    allow = str(args.allow).strip()
    learned = None        # 学习到的合法来源 IP
    bogus = 0

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", args.port))
    except OSError as e:
        vlog(f"绑定 UDP {args.port} 失败: {e} (助手是否已在运行?)")
        sys.exit(1)
    vlog(f"btrigger_daemon 启动: 监听 0.0.0.0:{args.port} "
          f"app={args.app_substr!r} voice_key={args.voice_key} "
          f"token={'开' if tail else '关'} allow={allow or '(学习首个来源)'} "
          f"dry_run={args.dry_run}")

    # ---- B 机助手 beacon 广播(供 A 机自动发现/配对, 免手填 IP) ----
    _beacon_stop = threading.Event()
    if args.beacon_port and args.beacon_port > 0:
        _bp = args.beacon_port
        _pkt = (bytes((BEACON_TYPE,))
                + json.dumps({"role": "btrigger",
                              "host": socket.gethostname(),
                              "ip": get_local_ip(),
                              "port": args.port}).encode("utf-8"))
        def _beacon_loop():
            bs = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                bs.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except Exception:
                pass
            vlog(f"[beacon] 广播 B 机助手发现信号 → 255.255.255.255:{_bp}")
            while not _beacon_stop.is_set():
                try:
                    bs.sendto(_pkt, ("255.255.255.255", _bp))
                except Exception as e:
                    vlog(f"[beacon] 广播失败: {e}")
                _beacon_stop.wait(2.0)
        threading.Thread(target=_beacon_loop, daemon=True).start()

    sock.settimeout(5.0)
    while True:
        try:
            data, addr = sock.recvfrom(RECV_BUF)
        except socket.timeout:
            continue
        except OSError:
            break
        src = addr[0]
        # ---- 来源白名单 ----
        if allow and src != allow:
            bogus += 1
            if bogus <= 3 or bogus % 50 == 0:
                vlog(f"[sec] 丢弃非白名单来源 {src} (累计 {bogus})")
            continue
        if not allow and learned is not None and src != learned:
            bogus += 1
            if bogus <= 3 or bogus % 50 == 0:
                vlog(f"[sec] 丢弃陌生来源 {src} (已锁定 {learned}, 累计 {bogus})")
            continue
        # ---- 报文解析 ----
        if not data or data[0] != CROSS_EVT_TYPE:
            bogus += 1
            continue
        body = data[1:]
        if tail:
            if len(body) <= 4 or body[-4:] != tail:
                bogus += 1
                if bogus <= 3 or bogus % 50 == 0:
                    vlog(f"[sec] 令牌不匹配来自 {src} (累计 {bogus})")
                continue
            body = body[:-4]
        try:
            ev = json.loads(body.decode("utf-8").strip())
        except (UnicodeDecodeError, json.JSONDecodeError):
            bogus += 1
            continue
        if not isinstance(ev, dict):
            continue
        if learned is None and not allow:
            learned = src
            vlog(f"[sec] 已学习并锁定来源 {learned}")
        # ---- 事件执行 ----
        name = str(ev.get("event") or "")
        if name == "voice_key":
            phase = str(ev.get("phase") or "start")
            if args.dry_run:
                title, exe = get_foreground_title()
                hit = app_matches(args.app_substr, title, exe)
                vlog(f"[dry] voice_key/{phase}: 前台 {title!r} "
                     f"({'命中' if hit else '未命中→将找回激活'})")
                continue
            title, exe = get_foreground_title()
            if not app_matches(args.app_substr, title, exe):
                # 旧逻辑直接跳过 → B 机回执实证「已发但没录」; 现改为找回激活
                if not activate_agent_window(args.app_substr):
                    vlog(f"[evt] voice_key/{phase} 跳过: "
                         f"{args.app_substr!r} 窗口不存在或激活失败")
                    continue
            tap_key(args.voice_key, f"直通 {phase}")
        elif name == "enter":
            if args.dry_run:
                vlog("[dry] 收到 enter: 将注入回车")
                continue
            title, exe = get_foreground_title()
            if not app_matches(args.app_substr, title, exe):
                if not activate_agent_window(args.app_substr):
                    vlog("[evt] enter 跳过: Agent 窗口不存在或激活失败")
                    continue
            tap_key(args.enter_key, "直通回车")
        else:
            vlog(f"[evt] 未知事件 {name!r} 来自 {src}, 忽略")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        vlog("btrigger_daemon 停止")
