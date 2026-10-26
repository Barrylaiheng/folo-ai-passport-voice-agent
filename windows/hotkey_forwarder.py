#!/usr/bin/env python3
"""热键+麦克风转发器: 把 AI Passport 设备变成"无线热键 + 无线麦克风"。

与 relay.py 的差异(架构见 passport-os/docs/voice-udp-channel.md):
  relay  = 收设备音频 → 火山 ASR → 注入文本(需要 ASR 密钥);
  本程序 = 收设备按键事件 → 注入系统热键(如右 Shift,调起语音输入法);
           收设备音频 → 解码 → 写虚拟声卡(VB-Cable),输入法把它当麦克风。
  不需要任何 ASR 密钥/账号 —— 识别由输入法自己完成。

设备「随身 AI 语音」按住 UP = voice.start + 音频流,松开 = voice.end。
本程序回报 pc.status 音频/热键配置就绪状态，设备据此允许按住说话。

用法:
  python companion/hotkey_forwarder.py                  # 读取配置；默认右Shift + hold
  python companion/hotkey_forwarder.py --mode hold      # 按住=热键按住
  python companion/hotkey_forwarder.py --key f9         # 换热键
  python companion/hotkey_forwarder.py --list-devices   # 列出声卡,找 CABLE
  python companion/hotkey_forwarder.py --no-audio       # 只做热键转发

虚拟声卡(麦克风路由): 需安装 VB-Audio Virtual Cable(https://vb-audio.com/Cable/),
安装后在语音输入法里把麦克风选为 "CABLE Output"。未安装时设备显示通道未就绪。
"""

import argparse
import asyncio
import ctypes
import json
import os
import queue
import sys
import threading
import time

# GitHub Windows runners and some legacy consoles default to cp1252.  Chinese
# status messages must never crash the forwarding loop or its release tests.
for _stream in (sys.stdout, sys.stderr):
    if getattr(_stream, "encoding", "").lower() not in ("utf-8", "utf8"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 程序"落脚目录": 源码运行=脚本目录; PyInstaller 单文件 exe=exe 所在目录
# (onefile 的 __file__ 指向临时解包目录, 日志/图标/VBAN/配置都必须跟着 exe 走)
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

import adpcm
from udp_transport import UdpTransport, UdpError, EVENT_UUID, AUDIO_UUID
from trae_cdp import CdpClient

# ---------------------------------------------------------------------------
# Windows 按键注入(ctypes SendInput, 零第三方依赖)
# ---------------------------------------------------------------------------

VK_SCAN = {
    # name: (VK, 扫描码, extended)。扫描码为 PS/2 set-1。
    "right-shift": (0xA1, 0x36, False),
    "left-shift": (0xA0, 0x2A, False),
    "right-ctrl": (0xA3, 0x1D, True),
    "right-alt": (0xA5, 0x38, True),
    "f6": (0x75, 0x40, False),
    "f9": (0x78, 0x43, False),
    "f10": (0x79, 0x44, False),
}

# 完整键位表:在 VK_SCAN 基础上补全 F1-F12/字母/数字/常用键,支持组合键。
# 组合键写法 "win+h"、"ctrl+shift+space"(修饰键在前,普通键最后)。
_KEY_EXTRA = {
    "left-ctrl": (0xA2, 0x1D, False),
    "left-alt": (0xA4, 0x38, False),
    "left-win": (0x5B, 0x5B, True),
    "right-win": (0x5C, 0x5C, True),
    "caps": (0x14, 0x3A, False),
    "space": (0x20, 0x39, False),
    "enter": (0x0D, 0x1C, False),
    "tab": (0x09, 0x0F, False),
    "backspace": (0x08, 0x0E, False),
    "escape": (0x1B, 0x01, False),
}
for _i, _scan in enumerate([0x3B, 0x3C, 0x3D, 0x3E, 0x3F, 0x40,
                            0x41, 0x42, 0x43, 0x44, 0x57, 0x58]):
    _KEY_EXTRA[f"f{_i + 1}"] = (0x70 + _i, _scan, False)
for _ch, _scan in zip("qwertyuiopasdfghjklzxcvbnm",
                      [0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17,
                       0x18, 0x19, 0x1E, 0x1F, 0x20, 0x21, 0x22, 0x23,
                       0x24, 0x25, 0x26, 0x2C, 0x2D, 0x2E, 0x2F, 0x30,
                       0x31, 0x32]):          # QWERTYUIOP ASDFGHJKL ZXCVBNM
    _KEY_EXTRA[_ch] = (0x41 + (ord(_ch) - 97), _scan, False)   # VK 'A'=0x41
for _i in range(10):
    _d = (_i + 1) % 10                         # '1','2',...,'9','0'
    _KEY_EXTRA[str(_d)] = (0x30 + _d, 0x02 + _i, False)
KEY_MAP = {**VK_SCAN, **_KEY_EXTRA}

# 组合键里的修饰键别名("win+h" 的 win = left-win)
MODIFIER_ALIASES = {"ctrl": "left-ctrl", "alt": "left-alt",
                    "shift": "left-shift", "win": "left-win"}
_MODIFIERS = {"left-ctrl", "right-ctrl", "left-alt", "right-alt",
              "left-shift", "right-shift", "left-win", "right-win"}


def resolve_key_spec(spec):
    """解析热键描述 → [(键名, VK, 扫描码, extended), ...]。

    单键 "right-shift"/"f9"/"h";组合键 "win+h"、"ctrl+shift+space"。
    组合键规则:最后一个必须是普通键,其余都是修饰键。
    """
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("热键为空")
    keys = []
    for i, part in enumerate(parts):
        name = MODIFIER_ALIASES.get(part, part)
        if name not in KEY_MAP:
            raise ValueError(f"未知键名 '{part}'")
        if i < len(parts) - 1 and name not in _MODIFIERS:
            raise ValueError(f"组合键里 '{part}' 必须是修饰键(ctrl/alt/shift/win),且放在前面")
        keys.append((name,) + KEY_MAP[name])
    return keys


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort),
                ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_void_p)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]   # 尺寸以 MOUSEINPUT 为准


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def attach_to_default_desktop():
    """将当前线程挂接至用户交互桌面 WinSta0\\Default(穿透沙箱桌面隔离)。"""
    if sys.platform == "win32":
        try:
            h_desk = ctypes.windll.user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_desk:
                ctypes.windll.user32.SetThreadDesktop(h_desk)
        except Exception:
            pass


class _KeyWorker:
    def __init__(self):
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="key_worker", daemon=True)
        self._thread.start()

    def _run(self):
        attach_to_default_desktop()
        while True:
            fn, args = self._q.get()
            try:
                fn(*args)
            except Exception as e:
                print(f"[key] Worker error: {e}", file=sys.stderr)
            finally:
                self._q.task_done()

    def post(self, fn, *args):
        self._q.put((fn, args))
        self._q.join()


_worker = None


def _get_worker():
    global _worker
    if _worker is None:
        _worker = _KeyWorker()
    return _worker


# 注入模式: False=扫描码事件(默认, 最接近真实键盘); True=纯 VK 事件(备用,
# 某些应用按 e.key/GetKeyState 判键时更吃 VK; 用 --inject-mode vk 切换)
_INJECT_VK_ONLY = False


def _do_send_key(vk, scan, extended, up):
    flags = (0 if _INJECT_VK_ONLY else KEYEVENTF_SCANCODE) | (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if up else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0,
                        dwExtraInfo=None)
    ret = ctypes.windll.user32.SendInput(1, ctypes.byref(inp),
                                         ctypes.sizeof(INPUT))
    if ret != 1:
        # SendInput 异常时降级走 keybd_event
        ctypes.windll.user32.keybd_event(vk & 0xFF, scan & 0xFF, flags, 0)


def _send_key(vk, scan, extended, up):
    _get_worker().post(_do_send_key, vk, scan, extended, up)


class KeyInjector:
    """热键注入器(支持单键及组合键,如 right-shift / win+h / ctrl+shift+space)。

    hold=设备按住UP说话期间热键保持按下,松开时抬起(适配"按住说话"类语音输入法,如讯飞/搜狗长按右Shift);
    tap=开启时点击热键(Down+60ms+Up),松开时再次点击以结束录音提交上屏(适配点击式语音键)。
    """

    def __init__(self, spec, mode):
        self.spec = spec
        self.mode = mode
        self._keys = resolve_key_spec(spec)   # [(键名, vk, scan, ext), ...]
        self._held = False
        self._active = False

    def start(self):
        attach_to_default_desktop()
        if self.mode == "hold":
            if not self._held:
                self._held = True
                for _name, vk, scan, ext in self._keys:
                    _send_key(vk, scan, ext, up=False)
                print(f"[key] {self.spec} 按下(hold, 开启语音输入)")
        else:
            if self._active:
                return
            self._active = True
            for _name, vk, scan, ext in self._keys:
                _send_key(vk, scan, ext, up=False)
            time.sleep(0.06)
            for _name, vk, scan, ext in reversed(self._keys):
                _send_key(vk, scan, ext, up=True)
            print(f"[key] {self.spec} 点击(tap, 开启语音输入)")

    def click(self):
        """发送单次按键点击(Down + 60ms + Up)，用于触发独立按键动作(如回车发送)。"""
        attach_to_default_desktop()
        for _name, vk, scan, ext in self._keys:
            _send_key(vk, scan, ext, up=False)
        time.sleep(0.06)
        for _name, vk, scan, ext in reversed(self._keys):
            _send_key(vk, scan, ext, up=True)
        print(f"[key] {self.spec} 点击(执行发送/确认)")

    def stop(self):
        attach_to_default_desktop()
        if self.mode == "hold" and self._held:
            self._held = False
            for _name, vk, scan, ext in reversed(self._keys):
                _send_key(vk, scan, ext, up=True)
            print(f"[key] {self.spec} 抬起(hold, 结束语音输入)")
        elif self.mode == "tap" and self._active:
            # 满足输入法"按下可开启语音输入，按任意键均可结束"：松开按键时再发一次点击以提交上屏
            self._active = False
            time.sleep(0.08)
            for _name, vk, scan, ext in self._keys:
                _send_key(vk, scan, ext, up=False)
            time.sleep(0.06)
            for _name, vk, scan, ext in reversed(self._keys):
                _send_key(vk, scan, ext, up=True)
            print(f"[key] {self.spec} 再次点击(tap, 结束语音输入)")


# ---------------------------------------------------------------------------
# 音频出口: 解码 ADPCM → 写虚拟声卡(VB-Cable 的 CABLE Input)
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
FRAME_BYTES = 3200                      # 1600 样本 × int16
SILENCE = b"\x00" * FRAME_BYTES
ENTER_DEBOUNCE_SECONDS = 0.35


class AudioSink:
    """把设备音频块解码后按实时节奏写进虚拟声卡。

    独立写线程 + 有界队列: 写线程 stream.write 天然按实时节拍消费,
    队列只吸收 UDP 抖动;溢出丢最旧(保延迟,宁丢勿积)。
    """

    def __init__(self, device_substr, blocks_max=5):
        self.enabled = False
        self.device_name = None
        self._q = queue.Queue(maxsize=blocks_max)
        try:
            import sounddevice as sd
        except (ImportError, OSError) as e:
            print(f"[audio] sounddevice 不可用({e}),麦克风通道未就绪",
                  file=sys.stderr)
            return
        outputs = [(i, d["name"]) for i, d in enumerate(sd.query_devices())
                   if d["max_output_channels"] > 0
                   and device_substr.lower() in d["name"].lower()]
        if not outputs:
            print("[audio] 没找到虚拟声卡(含 '%s' 的播放设备)。\n"
                  "  请安装 VB-Audio Virtual Cable: https://vb-audio.com/Cable/\n"
                  "  安装后重新运行本程序,并在语音输入法里把麦克风选为 'CABLE Output'。\n"
                  "  硬件麦克风模式暂不可用。" % device_substr, file=sys.stderr)
            return
        # One Windows device can expose MME, DirectSound and WASAPI endpoints.
        # Try another matching endpoint if the first cannot open at 16 kHz.
        for idx, name in outputs:
            stream = None
            try:
                stream = sd.RawOutputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                    device=idx, blocksize=0)
                stream.start()
                self._stream = stream
                self.device_name = name
                break
            except Exception as e:
                if stream is not None:
                    stream.close()
                print(f"[audio] 打开 [{idx}] '{name}' 失败: {e}", file=sys.stderr)
        else:
            return
        self.enabled = True
        threading.Thread(target=self._writer, name="audio_writer",
                         daemon=True).start()
        print(f"[audio] 麦克风路由就绪 → '{name}'"
              " (输入法里把麦克风选为对应的 'CABLE Output')")

    @staticmethod
    def _find_output(sd, substr):
        try:
            default_out = sd.default.output[1] if sd.default.output else -1
        except Exception:
            default_out = -1
        found = (None, None)
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and substr.lower() in d["name"].lower():
                found = (i, d["name"])
                break
        if found[0] is None:
            return (None, None)
        _ = default_out
        return found

    def write_block(self, adpcm_block):
        """AUDIO 载荷 [seq][0x80]+804B → 解码入队。"""
        if not self.enabled:
            return
        try:
            samples = adpcm.decode_block(adpcm_block)
        except Exception as e:
            print(f"[audio] 解码失败: {e}", file=sys.stderr)
            return
        pcm = bytearray(len(samples) * 2)
        for i, v in enumerate(samples):
            v = max(-32768, min(32767, int(v)))
            pcm[i * 2] = v & 0xFF
            pcm[i * 2 + 1] = (v >> 8) & 0xFF
        try:
            self._q.put_nowait(bytes(pcm))
        except queue.Full:
            try:
                self._q.get_nowait()   # 丢最旧,保延迟
                self._q.task_done()
                self._q.put_nowait(bytes(pcm))
            except queue.Empty:
                pass

    def _writer(self):
        while True:
            queued = True
            try:
                data = self._q.get(timeout=0.2)
            except queue.Empty:
                queued = False
                data = SILENCE         # 空闲补零,维持实时节拍
            try:
                self._stream.write(data)
            except Exception as e:
                self.enabled = False
                print(f"[audio] 写声卡失败: {e}", file=sys.stderr)
                self.clear()
                return
            finally:
                if queued:
                    self._q.task_done()

    def clear(self):
        while True:
            try:
                self._q.get_nowait()
                self._q.task_done()
            except queue.Empty:
                return

    def drain(self, timeout=0.8):
        # Deliver the final buffered syllables before releasing the input-method key.
        deadline = time.monotonic() + timeout
        while self.enabled and self._q.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        if self._q.unfinished_tasks:
            self.clear()


# ---------------------------------------------------------------------------
# 事件/音频 handler + 主循环
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Agent 直连模式: 自动聚焦目标窗口 + 可选自动回车发送
# (通用: 支持 WorkBuddy / TRAE / 任意程序, 由 voice-config.json 的 target_title 配置)
# ---------------------------------------------------------------------------

def focus_window_by_title(substr, click=None):
    """把标题含 substr 的窗口提到最前(尽力而为)。返回是否找到。

    Windows 对 SetForegroundWindow 有前台锁限制, 这里用 AttachThreadInput
    标准 workaround; 仍受系统策略约束, 失败时回退到仅 ShowWindow。
    click: 可选 (x, y) 绝对像素, 聚焦后点一下以定位输入框。
    """
    if not substr:
        return False
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    substr_l = substr.lower()
    found = []

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_longlong)

    def enum_cb(hwnd, lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and n > 0:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value or ""
            if substr_l in title.lower() and user32.IsWindowVisible(hwnd):
                found.append((hwnd, title))
        return True

    user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
    if not found:
        print(f"[focus] 未找到标题含 '{substr}' 的窗口(跳过聚焦)")
        return False
    hwnd, title = found[0]
    try:
        # 仅在最小化时还原；已可见/最大化的窗口不要 SW_RESTORE，否则会被“还原”成小窗(并丢失输入框焦点)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        else:
            user32.ShowWindow(hwnd, 5)   # SW_SHOW(保持最大化/原状态)
        fg = user32.GetForegroundWindow()
        if fg:
            fg_thread = user32.GetWindowThreadProcessId(fg, None)
            cur_thread = kernel32.GetCurrentThreadId()
            if fg_thread and fg_thread != cur_thread:
                user32.AttachThreadInput(fg_thread, cur_thread, True)
                user32.SetForegroundWindow(hwnd)
                user32.AttachThreadInput(fg_thread, cur_thread, False)
            else:
                user32.SetForegroundWindow(hwnd)
        else:
            user32.SetForegroundWindow(hwnd)
    except Exception as e:
        print(f"[focus] 聚焦异常(忽略): {e}")
    # 把光标塞进目标窗口的输入框(Edit/Document)，否则 WeType 无处打字
    focused_box = _focus_input_box(hwnd)
    if click:
        try:
            x, y = click
            time.sleep(0.15)
            w = user32.GetSystemMetrics(0)
            h = user32.GetSystemMetrics(1)
            mi = MOUSEINPUT(dx=int(x * 65535 / w), dy=int(y * 65535 / h),
                            mouseData=0,
                            dwFlags=0x8000 | 0x0001 | 0x0002 | 0x0004,
                            time=0, dwExtraInfo=None)
            inp = INPUT(type=1)
            inp.mi = mi
            ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
            print(f"[focus] 已按 focus_click 点击坐标 ({x},{y})")
        except Exception as e:
            print(f"[focus] 点击坐标失败(忽略): {e}")
    if focused_box:
        print(f"[focus] 已聚焦窗口并定位输入框: {title!r}")
    elif not click:
        print(f"[focus] 已聚焦窗口: {title!r} (未自动定位输入框;若文字没进框,用 focus_click 指定坐标或反馈给我)")
    else:
        print(f"[focus] 已聚焦窗口: {title!r}")
    return True


def _focus_input_box(hwnd):
    """尽量把光标放进目标窗口的可编辑输入框(Edit / Document)。失败静默返回 False。

    现代聊天/IDE 多为 Electron/Chromium(UIMonitor 后端)，pywinauto 仅作尽力而为；
    若自动定位失败，可改用配置里的 focus_click 指定输入框屏幕坐标。
    """
    try:
        import pywinauto
        from pywinauto.application import Application
    except Exception:
        return False
    try:
        try:
            app = Application(backend="uia").connect(handle=hwnd)
        except Exception:
            app = Application().connect(handle=hwnd)
        dlg = app.window(handle=hwnd)
        for cls in ("Edit", "Document"):
            try:
                ctrls = dlg.descendants(control_type=cls)
            except Exception:
                ctrls = []
            for c in ctrls:
                try:
                    if c.is_editable() if hasattr(c, "is_editable") else True:
                        c.set_focus()
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def mouse_click(abs_x, abs_y):
    """在屏幕绝对坐标 (abs_x, abs_y) 模拟一次左键点击(移动→按下→抬起)。"""
    user32 = ctypes.windll.user32
    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    dx = int(abs_x * 65535 / w)
    dy = int(abs_y * 65535 / h)
    for flags, delay in ((0x0001, 0), (0x0002, 0.06), (0x0004, 0)):  # MOVE,DOWN,UP
        mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=0, dwFlags=flags,
                        time=0, dwExtraInfo=None)
        inp = INPUT(type=1)
        inp.mi = mi
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        if delay:
            time.sleep(delay)


def get_window_text(hwnd):
    """返回指定窗口的标题文本(失败返回 "")。"""
    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value or ""


def get_foreground_window_title():
    """返回当前前台窗口的 (hwnd, 标题)。"""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return (None, "")
    return (hwnd, get_window_text(hwnd))


def _proc_name_for_pid(pid):
    """返回 pid 对应的进程 exe 完整路径(失败返回 "")。"""
    kernel32 = ctypes.windll.kernel32
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFO
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        ln = ctypes.c_uint32(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(ln)):
            return buf.value or ""
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(h)
    return ""


def get_foreground_process_name():
    """返回前台窗口所属进程的完整 exe 路径(失败返回 "")。

    为什么需要: TRAE 主窗口标题是 <工作区文件夹名> [管理员](如 'Agent [管理员]'),
    根本不含 'Trae' 字样 —— 只按标题匹配会把 TRAE 主窗口误判成"非已配置 Agent"
    而切到旁边那个标题带 Trae 的悬浮小窗, 导致语音键永远注入不到真正的工作台。
    改用进程名(Trae CN.exe)匹配与标题无关, 彻底根治。
    """
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = ctypes.c_uint32(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return _proc_name_for_pid(pid.value)


def _hwnd_exe_path(hwnd):
    """返回指定窗口所属进程的 exe 完整路径(失败返回 "")。

    版本感知热键要用: 焦点找回场景下前台可能是抢焦点者, 必须取
    目标 Agent 自己窗口的 exe, 而非总是取前台窗口。
    """
    try:
        user32 = ctypes.windll.user32
        pid = ctypes.c_uint32(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return _proc_name_for_pid(pid.value)
    except Exception:
        return ""


def _file_version(path):
    """读 exe 文件版本 → (major, minor, patch, build); 失败/无版本资源返回 ()。"""
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return ()
        data = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, data):
            return ()
        lp = ctypes.c_void_p(0)
        ln = ctypes.c_uint(0)
        if not ver.VerQueryValueW(data, "\\", ctypes.byref(lp), ctypes.byref(ln)):
            return ()
        if not lp or ln.value < 13 * 4:  # VS_FIXEDFILEINFO 至少 13 个 DWORD
            return ()
        # 布局: [0]签名 [1]结构版本 [2]FileVersionMS [3]FileVersionLS ...
        ms = ctypes.c_uint.from_address(lp.value + 8).value
        ls = ctypes.c_uint.from_address(lp.value + 12).value
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return ()


def _parse_version(text):
    """'5.8.3' → (5, 8, 3, 0); 解析失败返回 ()。"""
    parts = []
    for piece in str(text or "").replace("-", ".").replace("+", ".").split("."):
        if piece.isdigit():
            parts.append(int(piece))
        elif parts:
            break
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


# 内置"已知应用的语音热键版本规则"(按 exe 文件名小写匹配)。
# 背景: WorkBuddy 5.8.3 把「语音录音开关」默认热键从 ctrl+d(点击 toggle)
# 改成 **长按 Alt**(渲染层注册表 {"id":"toggle-voice-recording",
# "defaultWin":"Alt","voiceModifierOnly":true}; 设置页显示"长按 Alt";
# 实测短 tap 不触发, 须按住保持)。
# 原则(2026-10-16 拍板): 兼容性由转发器做, 不要求用户去应用里改热键。
# 用户在应用里显式改过热键的, 在 voice-config.json 该应用条目加
# "voice_key_rules": [] 即可关闭内置适配、回落到 voice_key。
BUILTIN_VOICE_KEY_RULES = {
    "workbuddy.exe": [
        {"since": "5.8.3", "key": "alt", "mode": "hold"},
    ],
}


def make_console_unfocusable():
    """把本程序控制台窗口设为 NOACTIVATE 并隐藏, 使其永远无法抢前台焦点。

    实测痛点: 控制台窗口(即使最小化)反复成为前台窗口, 抢走 TRAE 的焦点
    导致注入的语音快捷键落空。隐藏 + NOACTIVATE 双保险后它彻底出局;
    日志已全部写入 forwarder.log, 停止请用桌面"停止AI-Passport转发.cmd"。
    """
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                              style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        SW_HIDE = 0
        user32.ShowWindow(hwnd, SW_HIDE)
        print("[win] 控制台已隐藏并设为不可抢焦点; 日志见 forwarder.log")
    except Exception as e:
        print(f"[win] 控制台隐藏失败: {e}")


def find_topmost_app_window(title_contains_list):
    """按 Z 序从顶到底枚举可见窗口, 返回第一个标题含任一子串的 (hwnd, 标题)。

    子串匹配不区分大小写。用于"前台不是已配置 Agent 时自动找回 Agent 窗口"。
    """
    user32 = ctypes.windll.user32
    needles = [(s or "").lower() for s in title_contains_list if s]
    if not needles:
        return (None, "")
    result = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value or ""
        tl = title.lower()
        if any(nd in tl for nd in needles):
            result.append((hwnd, title))
            return False  # 找到最顶部的就停
        # 标题不含关键词时, 再按进程名匹配(TRAE 主窗口标题是工作区名, 不含 'Trae')
        pid = ctypes.c_uint32(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        exe = _proc_name_for_pid(pid.value).lower()
        if exe and any(nd in exe for nd in needles):
            result.append((hwnd, title))
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), None)
    if result:
        return result[0]
    return (None, "")


def _window_process_name(hwnd):
    """返回指定窗口所属进程的 exe 完整路径(失败返回 "")。"""
    pid = ctypes.c_uint32(0)
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return _proc_name_for_pid(pid.value)


def find_app_windows(title_contains_list):
    """枚举所有匹配(标题或进程名子串)的可见顶层窗口, 按屏幕面积从大到小返回。

    为什么按面积排序而不是 Z 序: TRAE 有个标题带 'Trae' 的悬浮小按钮窗,
    Z 序可能排在工作台之前, 按 Z 序找回会抓错窗口(踩过)。主工作台窗口
    几乎总是面积最大的 —— 这是比 Z 序稳得多的启发式。
    """
    user32 = ctypes.windll.user32
    needles = [(s or "").lower() for s in title_contains_list if s]
    if not needles:
        return []

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    result = []  # (area, hwnd, title)
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1) if n > 0 else None
        title = buf.value if buf else ""
        tl = (title or "").lower()
        exe = _window_process_name(hwnd).lower()
        if not any(nd in tl or nd in exe for nd in needles):
            return True
        r = _RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        area = max(0, r.right - r.left) * max(0, r.bottom - r.top)
        if area <= 0:  # 排除隐形/零面积窗
            return True
        result.append((area, hwnd, title))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), None)
    result.sort(key=lambda t: t[0], reverse=True)
    return [(h, t) for (_a, h, t) in result]


def switch_to_window(hwnd):
    """把指定窗口拉到前台并激活。优先 SwitchToThisWindow, 失败回落 SetForegroundWindow。

    返回是否确认成功。注意前台切换是异步生效的 —— 立即查 GetForegroundWindow
    常因竞态误报失败(2026-10-15 干跑实测), 故先小等再核验, 未成再重试一次。
    """
    user32 = ctypes.windll.user32
    try:
        user32.SwitchToThisWindow(hwnd, True)
    except Exception:
        pass
    for _ in range(2):
        for _ in range(8):  # 最多等 ~0.4s 让切换落地
            if user32.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.05)
        if user32.GetForegroundWindow() != hwnd:
            user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


class Forwarder:
    def __init__(self, args):
        self.args = args
        self.injector = KeyInjector(args.key, args.mode)
        self.trigger_mode = str(getattr(args, "trigger_mode", "mic") or "mic")
        self.apps = list(getattr(args, "apps", None) or [])
        self.send_key = str(getattr(args, "send_key", "enter") or "enter")
        self.stop_on_release = bool(getattr(args, "stop_on_release", True))
        # 跨网模式(2026-10-14): 穿透隧道公网地址(host:port) + 配对令牌。
        # remote_endpoint 非空时, device.hello 后自动推送 cfg.udp 存进设备 NVS。
        self.remote_endpoint = str(getattr(args, "remote_endpoint", "") or "").strip()
        self.pair_token = str(getattr(args, "pair_token", "") or "").strip()
        self._transport = None
        self.enter_injector = KeyInjector(self.send_key, "tap")
        self.sink = None if args.no_audio else AudioSink(args.output_substr)
        self.audio_blocks = 0
        self.active = False
        self.last_audio_at = 0.0
        self.last_seq = None
        self.last_enter_at = float("-inf")
        self.target_title = str(getattr(args, "target_title", "") or "")
        self.auto_enter = bool(getattr(args, "auto_enter", False))
        self.focus_click = getattr(args, "focus_click", None)
        # CDP(TRAE 等 Chromium webview 专用): 找配置了 cdp_port 的 app
        self.cdp_port = 0
        self.cdp_app = None
        for a in self.apps:
            if a.get("cdp_port"):
                self.cdp_port = int(a["cdp_port"])
                self.cdp_app = a
                break
        self.cdp = CdpClient(self.cdp_port or 9222)
        self._cdp_active = False  # CDP 是否正在录音(松手停录要走 CDP 完成)
        self.loop = None
        # 焦点观察器(2026-10-15 治本): 持续记录"用户最近一次所在前台的 Agent
        # 窗口"。按键瞬间"前台是谁"不可信(用户在开发任何东西时焦点随时会被
        # 终端/浏览器/被开发程序抢走), 找回目标必须以此为锚, 而非碰运气的前台。
        self._last_agent_hwnd = None
        self._last_agent_app = None

    def pc_status(self):
        return {"type": "pc.status", "audioReady": bool(self.sink and self.sink.enabled),
                "hotkeyReady": bool(self.injector._keys)}

    def finish(self, drain=False):
        self.active = False
        try:
            if self.sink:
                if drain:
                    self.sink.drain()
                else:
                    self.sink.clear()
        finally:
            self.injector.stop()
            # 长按型语音键(WorkBuddy 5.8.3 "长按 Alt")兜底: 断链/音频超时
            # 时若还按着, 必须抬起, 否则 Alt 卡死会干扰用户一切组合键。
            try:
                self._release_held_voice_key("链路复位")
            except Exception:
                pass

    def check_health(self, now=None):
        now = time.monotonic() if now is None else now
        # PING can remain alive when voice.end is lost. Audio silence timeout,
        # independent of link heartbeat, ensures a held key still gets released.
        if self.active and (now - self.last_audio_at > 2.5 or
                            (self.sink is not None and not self.sink.enabled)):
            print("[link] 语音通道中断，停止传音并释放快捷键")
            self.finish()

    def _match_app(self, title, exe=""):
        """按窗口标题子串 或 进程 exe 名子串 匹配已配置的 Agent。

        exe 匹配是 2026-10-14 的关键修复: TRAE 主窗口标题=<工作区名> [管理员],
        不含 'Trae', 只有进程名 'Trae CN.exe' 才稳。故 title_contains 与
        exe_contains 任一命中即算匹配。
        """
        tl = (title or "").lower()
        el = (exe or "").lower()
        for app in self.apps:
            tc = (app.get("title_contains") or "").lower()
            ec = (app.get("exe_contains") or "").lower()
            if (tc and tc in tl) or (ec and ec in el):
                return app
        return None

    def _exe_version(self, exe_path):
        """带缓存的 exe 版本查询(同一 exe 只读一次文件)。"""
        if not exe_path:
            return ()
        cache = getattr(self, "_ver_cache", None)
        if cache is None:
            cache = self._ver_cache = {}
        v = cache.get(exe_path)
        if v is None:
            v = _file_version(exe_path)
            cache[exe_path] = v
        return v

    @staticmethod
    def _match_voice_rule(app, exe_path, ver):
        """按 exe 版本匹配该应用的语音热键规则; 无规则返回 None。

        优先级: 配置显式 voice_key_rules > 内置 BUILTIN_VOICE_KEY_RULES。
        版本读不到(ver 为空)→ 不猜, 返回 None(回落 voice_key)。
        """
        rules = app.get("voice_key_rules")
        if rules is None:
            name = (exe_path or "").replace("/", "\\").rsplit("\\", 1)[-1].lower()
            rules = BUILTIN_VOICE_KEY_RULES.get(name)
        if not rules:
            return None
        best, best_since = None, ()
        for r in rules:
            since = _parse_version(r.get("since", "0"))
            if ver and ver >= since and since >= best_since:
                best, best_since = r, since
        return best

    def _resolve_voice_key(self, app, exe_path=""):
        """解析该应用当前生效的语音热键(版本感知, 2026-10-16)。"""
        if not app:
            return None
        rule = self._match_voice_rule(app, exe_path, self._exe_version(exe_path))
        if rule:
            return rule.get("key") or app.get("voice_key")
        return app.get("voice_key")

    def _resolve_voice_key_mode(self, app, exe_path=""):
        """解析语音键手势: 'tap'=点击式 toggle; 'hold'=长按(开录按下保持,停录抬起)。

        WorkBuddy 5.8.3 起「语音录音开关」显示为"长按 Alt", 短 tap 不触发,
        故内置规则带 mode=hold —— 恰与设备"按住 UP 说话"的语义天然对齐。
        """
        if not app:
            return "tap"
        m = app.get("voice_key_mode")
        if m:
            return m
        rule = self._match_voice_rule(app, exe_path, self._exe_version(exe_path))
        return (rule or {}).get("mode") or "tap"

    def _tap_key(self, spec, label):
        """向当前前台窗口注入一次完整按键点击, 用于触发/切换快捷键。

        时序模拟真人节奏(重要): 修饰键↓ → 30ms → 主键↓ → 80ms → 主键↑ → 30ms → 修饰键↑。
        Alt 组合键在 Chromium/Electron 里有菜单模式特殊处理, 修饰键与主键之间
        零间隔的合成序列可能被吞掉(实测 TRAE Alt+V 手动可触发、机器注入失效)。
        """
        try:
            keys = resolve_key_spec(spec)
        except ValueError as e:
            print(f"[key] 解析失败 {spec!r}: {e}")
            return False
        attach_to_default_desktop()
        mods, last = keys[:-1], keys[-1]
        for _name, vk, scan, ext in mods:
            _send_key(vk, scan, ext, up=False)
            time.sleep(0.03)
        _send_key(last[1], last[2], last[3], up=False)
        time.sleep(0.08)
        _send_key(last[1], last[2], last[3], up=True)
        time.sleep(0.03)
        for _name, vk, scan, ext in reversed(mods):
            _send_key(vk, scan, ext, up=True)
        mode_note = " (vk模式)" if _INJECT_VK_ONLY else ""
        print(f"[key] 注入 {spec}{mode_note}  ({label})")
        return True

    def _release_should_stop(self):
        """UP 松开时是否应注入停录动作。

        优先: 若当前是 CDP 录音中(如 TRAE), 松手必须停录(点 ✓ 完成), 与前台
        窗口无关 —— CDP 路线本就不依赖焦点。
        否则: per-app 覆盖优先(apps[].stop_on_release), 再回落全局。
        注意 exe 匹配(TRAE 标题不含 'Trae', 只有进程名 'Trae CN.exe' 才稳)。
        """
        if getattr(self, "_cdp_active", False):
            return True
        hwnd, title = get_foreground_window_title()
        exe = get_foreground_process_name() if hwnd else ""
        app = self._match_app(title, exe) if hwnd else None
        if app and isinstance(app.get("stop_on_release"), bool):
            return app["stop_on_release"]
        return self.stop_on_release

    def _schedule_cdp(self, action):
        """把 CDP 语音动作丢进事件循环异步跑(CDP 是 async 客户端)。

        返回 True=已调度; False=事件循环未就绪(由调用方回退键盘注入)。
        """
        if self.loop is None:
            print("[cdp] 事件循环未就绪, 回退键盘注入")
            return False
        asyncio.ensure_future(self._trae_voice(action), loop=self.loop)
        return True

    async def _trae_voice(self, action):
        """CDP 路线: 点 TRAE 聊天框麦克风(开录)/完成(停录并转写), 不碰焦点。

        实测(2026-10-14): button.rtcVoicePluginButton=麦克风开录;
        button.rtcVoiceToolbarBtnFinish=录音工具栏 "✓ 完成"(转写落进输入框)。
        """
        on = action.startswith("开始")
        try:
            ok = await self.cdp.ensure()
            if not ok:
                print("[cdp] TRAE 未以调试端口启动 → 回退键盘注入(请用桌面"
                      "「启动TRAE(调试端口).cmd」打开 TRAE 以启用 CDP)")
                self._cdp_keyboard_fallback(action)
                return
            try:
                await self.cdp._cmd("Page.bringToFront", {})
            except Exception:
                pass
            if on:
                res = await self.cdp.voice_start()
                self._cdp_active = bool(res.get("ok"))
                if res.get("ok"):
                    print("[cdp] TRAE 语音: 已点麦克风 button.rtcVoicePluginButton"
                          " → 开始录音")
                else:
                    print(f"[cdp] TRAE 麦克风未点成功: {res}; 回退键盘注入")
                    self._cdp_keyboard_fallback(action)
            else:
                res = await self.cdp.voice_finish()
                self._cdp_active = False
                if res.get("ok"):
                    print("[cdp] TRAE 语音: 已点 ✓ 完成 rtcVoiceToolbarBtnFinish"
                          " → 停止录音并转写进输入框")
                else:
                    # 不在录音时会找不到完成键; 这里不回落键盘(否则又重新开录)
                    print(f"[cdp] TRAE 完成键未点成功(可能本就没在录): {res}")
            st = await self.cdp.state()
            print(f"[cdp] 状态: {st}")
        except Exception as e:
            print(f"[cdp] 异常: {e}")

    def _cdp_keyboard_fallback(self, action):
        """CDP 不可用/找不到麦克风时, 回退到键盘注入。

        复用与主路径完全相同的确定性找回(_recover_agent): 前台被抢焦点时
        切回用户最近在用的 Agent 窗口 + UIA 恢复输入框焦点, 再注入快捷键;
        用户本来就在 Agent 里时不动焦点(手动点进输入框的场景)。
        """
        app = self.cdp_app
        if not (app and (app.get("voice_key")
                         or app.get("voice_key_rules") is not None)):
            return
        try:
            hwnd, title = get_foreground_window_title()
            exe = get_foreground_process_name() if hwnd else ""
            matched = self._match_app(title, exe) if hwnd else None
            if not (hwnd and matched):
                hwnd, title = self._recover_agent(app)
                if not hwnd:
                    print("[cdp] 回退注入失败: 找不回 Agent 窗口")
                    return
                exe = _hwnd_exe_path(hwnd)  # 用找回窗口自己的 exe 判版本
            elif matched is not app:
                exe = ""  # 前台是别的 Agent, 别拿它的 exe 判版本
            vk = self._resolve_voice_key(app, exe or "")
            if not vk:
                print("[cdp] 回退注入失败: 无生效语音热键")
                return
            self._tap_key(vk, action)
            print(f"[cdp] 已回退键盘注入 {vk}")
        except Exception as e:
            print(f"[cdp] 回退键盘注入失败: {e}")

    def _recover_agent(self, app):
        """确定性找回目标 Agent 窗口并恢复其输入框焦点(治本路径, 不依赖 CDP)。

        顺序(2026-10-15 重构):
          ① 焦点观察器记下的"用户最近所在的 Agent 窗口"(校验窗口仍存活且
             进程仍匹配) —— 焦点被任何程序抢走都能找对家;
          ② 否则按面积选该 Agent 最大的窗口(避开悬浮球小窗陷阱);
          ③ 切前台 + UIA 把光标塞回输入框(窗口级聚焦不够, TRAE 的 Alt+V
             只认输入框聚焦)。
        返回 (hwnd, title), 找不到返回 (None, "")。
        """
        user32 = ctypes.windll.user32
        hwnd = None
        last = getattr(self, "_last_agent_hwnd", None)
        if last and user32.IsWindow(last):
            tl = (get_window_text(last) or "").lower()
            el = (_window_process_name(last) or "").lower()
            nds = [(app.get("title_contains") or "").lower(),
                   (app.get("exe_contains") or "").lower()]
            nds = [n for n in nds if n]
            if nds and any(n in tl or n in el for n in nds):
                hwnd = last
        if hwnd is None:
            wins = find_app_windows([app.get("title_contains"),
                                     app.get("exe_contains")])
            if wins:
                hwnd = wins[0][0]
        if not hwnd:
            return (None, "")
        if not switch_to_window(hwnd):
            print("[voice-key] 警告: 切前台未确认成功, 仍尝试注入")
        time.sleep(0.25)  # 等焦点稳定
        t0 = time.monotonic()
        try:
            uia_ok = _focus_input_box(hwnd)
        except Exception:
            uia_ok = False
        print(f"[voice-key] UIA 输入框焦点恢复: "
              f"{'OK' if uia_ok else '失败(继续窗口级注入)'} "
              f"({(time.monotonic() - t0) * 1000:.0f}ms)")
        return (hwnd, get_window_text(hwnd))

    async def _focus_watcher(self):
        """焦点观察器: 每 0.7s 记录最近处于前台的已配置 Agent 窗口。

        治本关键(2026-10-15): 用户在 TRAE 里开发时, 焦点随时会被被跑的任务
        (终端/浏览器/被开发程序自身窗口)抢走。按键时"前台是谁"已不可信;
        以"用户最近一次所在 Agent 窗口"为找回锚点, 才能保证任何场景下
        按设备键 = 对准用户正在用的那个 Agent, 与用户在开发什么无关。
        """
        while True:
            try:
                await asyncio.sleep(0.7)
                hwnd, title = get_foreground_window_title()
                if not hwnd:
                    continue
                exe = get_foreground_process_name()
                app = self._match_app(title, exe)
                if app:
                    self._last_agent_hwnd = hwnd
                    self._last_agent_app = app
            except Exception:
                pass  # 观察器永不因单次异常退出

    def press_agent_voice_key(self, action):
        """通用 Agent 模式: 向目标 Agent 注入它的语音快捷键(键盘, 非鼠标)。

        用窗口标题/进程 exe 匹配 apps 配置里的 voice_key(如 WorkBuddy=ctrl+d /
        Trae=alt+v); 该键是 toggle 语义(按一次开/再按一次关), 故:
          UP 按下  → 注入一次 = 开始语音(由 Agent 自己录音+转写)
          UP 松开  → 再注入一次 = 停止语音
        AI Passport 的声音已通过 VB-Cable 作为系统默认麦克风送达,
        所以 Agent 录到的就是你说的话。无需任何鼠标坐标/校准。

        目标判定(2026-10-15 治本): 前台是 Agent → 直接注(用户焦点本来就在,
        不动它); 前台被抢(用户在开发任何东西时都会发生) → 焦点观察器锚定
        "用户最近在用的 Agent"确定性找回, 与抢焦点者是谁无关。
        """
        # 长按型语音键(WorkBuddy 5.8.3 "长按 Alt"): 停录=抬起即可, 无需找回
        # 窗口 —— 抬键是全局键盘状态, 与前台是谁无关, 抢先处理避免多余动作。
        if getattr(self, "_vk_held", None) and ("停止" in action or "松开" in action):
            self._release_held_voice_key(action)
            return
        hwnd, title = get_foreground_window_title()
        exe = get_foreground_process_name()
        app = self._match_app(title, exe) if hwnd else None
        fg_app = app  # 前台命中时 app 即它; 找回场景 app 会被换成目标 Agent
        # === CDP 快速路径(可选优化, 非依赖): 直接点麦克风, 不必切窗口/抢焦点 ===
        # 触发条件: ①前台就是配了 cdp_port 的应用; ②上一拍正是 CDP 开录的
        # (保证"松手停录"也走 CDP, 哪怕松手瞬间用户焦点已在别处)。
        # CDP 选页已严格化(只认工作台页), 连不上/连错自动落到下方键盘主路径。
        cdp_app = (app if (app and app.get("cdp_port"))
                   else (self.cdp_app if getattr(self, "_cdp_active", False)
                         else None))
        if self.cdp_port and cdp_app and self._schedule_cdp(action):
            return
        if not app or not app.get("voice_key"):
            thief = exe.rsplit(chr(92), 1)[-1] if exe else "?"
            print(f"[voice-key] 前台 {title!r}(进程 {thief!r}) "
                  f"不是已配置 Agent, 确定性找回...")
            # ① 焦点观察器锚定的"用户最近在用的 Agent"
            target = getattr(self, "_last_agent_app", None)
            if target is not None:
                wins = find_app_windows([target.get("title_contains"),
                                         target.get("exe_contains")])
                if not wins:
                    target = None
            # ② 保底: 按配置顺序找第一个有窗口的 Agent
            if target is None:
                for a in (self.apps or []):
                    if a.get("voice_key") and find_app_windows(
                            [a.get("title_contains"), a.get("exe_contains")]):
                        target = a
                        break
            if target is None:
                subs = ", ".join(repr(a.get("title_contains")) for a in self.apps)
                print(f"[voice-key] 找不到任何已配置 Agent 窗口"
                      f"(已配置: {subs}); 跳过注入")
                return
            app = target
            hwnd, title = self._recover_agent(app)
            if not hwnd:
                print(f"[voice-key] Agent {app.get('title_contains')!r} "
                      f"窗口找回失败; 跳过注入")
                return
            # 找回的若是 CDP 应用, 仍优先试 CDP 点麦克风(不碰焦点更稳)
            if self.cdp_port and app.get("cdp_port"):
                if self._schedule_cdp(action):
                    return
            print(f"[voice-key] 前台原本是其他窗口(被 {thief!r} 抢焦点), "
                  f"已确定性切回 {title!r}")
            # TRAE 这类 Agent 的语音快捷键可能只监听聊天输入框(webview)内的按键,
            # 光切窗口不够 → 可选 focus_key: 先注入唤起/聚焦聊天框的快捷键
            fk = app.get("focus_key")
            if fk:
                print(f"[voice-key] 先注入聊天聚焦键 {fk}")
                self._tap_key(fk, "聚焦聊天输入框")
                time.sleep(0.35)
        # 版本感知热键(2026-10-16): WorkBuddy 5.8.3+ 默认热键改为单独 alt。
        # 找回场景必须用目标 Agent 自己窗口的 exe 判版本(前台可能是抢焦点者)。
        exe_for_ver = exe if app is fg_app else _hwnd_exe_path(hwnd)
        vk = self._resolve_voice_key(app, exe_for_ver)
        if not vk:
            print(f"[voice-key] Agent {app.get('title_contains')!r} "
                  f"无生效语音热键; 跳过注入")
            return
        mode = self._resolve_voice_key_mode(app, exe_for_ver)
        if mode == "hold":
            self._press_hold_voice_key(vk, action)
            return
        print(f"[voice-key] 前台={title!r} → 注入 {vk}")
        self._tap_key(vk, action)

    def _press_hold_voice_key(self, vk, action):
        """长按型语音键: 开录=按下保持不放, 停录=抬起(见 _release_held_voice_key)。"""
        if "停止" in action or "松开" in action:
            self._release_held_voice_key(action)
            return
        if getattr(self, "_vk_held", None):
            return  # 已按住(漏收停录的兜底), 不重复按下
        keys = resolve_key_spec(vk)
        mods, last = keys[:-1], keys[-1]
        attach_to_default_desktop()
        for _n, v, s, e in mods:
            _send_key(v, s, e, up=False)
            time.sleep(0.03)
        _send_key(last[1], last[2], last[3], up=False)
        self._vk_held = (mods, last, vk)
        print(f"[key] {vk} 按住(长按型语音键, 开录; 设备松手后抬起)")

    def _release_held_voice_key(self, action=""):
        """抬起按住中的长按型语音键。断链/退出时也须调用, 防 Alt 卡死。"""
        held = getattr(self, "_vk_held", None)
        if not held:
            return
        mods, last, vk = held
        attach_to_default_desktop()
        _send_key(last[1], last[2], last[3], up=True)
        for _n, v, s, e in reversed(mods):
            _send_key(v, s, e, up=True)
        self._vk_held = None
        print(f"[key] {vk} 抬起(长按型语音键, {action or '停录'})")

    def on_event(self, payload):
        try:
            ev = json.loads(payload.decode("utf-8").strip())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(ev, dict):
            return
        name = ev.get("event", "")
        if name == "voice.start":
            if self.active:
                print("[evt] voice.start 被忽略: active 状态残留(上次 voice.end 未正常收尾), 已强制复位")
                self.active = False
            if self.sink is not None and not self.sink.enabled:
                print("[evt] voice.start 被忽略: 音频桥未启用(sink.enabled=False)")
                return
            print("[evt] 设备开始说话(voice.start)")
            if self.trigger_mode == "mic":
                # 通用 Agent 模式: 注入前台 Agent 的语音快捷键(它自己录音+转写)
                self.press_agent_voice_key("开始语音(UP按下)")
            else:
                # 旧模式: 聚焦目标窗口 + 用右Shift 调起微信输入法(WeType)注入文字
                if self.target_title:
                    focus_window_by_title(self.target_title, self.focus_click)
                self.injector.start()
            self.audio_blocks = 0
            self.last_seq = None
            self.last_audio_at = time.monotonic()
            if self.sink:
                self.sink.clear()
            self.active = True
        elif name == "voice.end":
            print(f"[evt] 设备结束说话(voice.end),收到音频块={self.audio_blocks}")
            if self.trigger_mode == "mic":
                # 停音频; 若配置了 stop_on_release, 再注入一次语音键停录(不发送)。
                # stop_on_release 支持全局或 per-app 覆盖: apps[].stop_on_release 优先。
                self.finish(drain=True)
                if self._release_should_stop():
                    self.press_agent_voice_key("停止语音(UP松开)")
            else:
                self.finish(drain=True)
                if self.auto_enter:
                    time.sleep(0.15)
                    self.enter_injector.click()
                    print("[evt] 自动回车发送(auto_enter)")
        elif name == "device.hello":
            # 心跳降噪(2026-10-15 二机实测反馈): 设备每秒回一个 hello, 逐条打印
            # 会把日志刷成千行噪音。只在线状态变化时记"上线", 其余每 30s 汇总一次。
            now = time.monotonic()
            gap = now - getattr(self, "_last_hello_at", -1e9)
            self._last_hello_at = now
            self._hello_count = getattr(self, "_hello_count", 0) + 1
            if gap > 5.0:
                self._hello_summary_base = self._hello_count
                self._last_hello_summary_at = now
                print("[evt] 设备上线(device.hello)")
            elif now - getattr(self, "_last_hello_summary_at", 0) > 30.0:
                self._last_hello_summary_at = now
                print(f"[evt] 设备在线正常(心跳累计 "
                      f"{self._hello_count - self._hello_summary_base} 次)")
            # 跨网配置推送: 配置了 remote_endpoint(穿透隧道地址)时, 趁设备在线
            # 把 隧道地址+配对令牌 下发存进设备 NVS —— 设备出门后据此直连,
            # 无需任何设备端配置 UI。在家(局域网)模式下此推送无副作用。
            if self.remote_endpoint:
                asyncio.ensure_future(self._push_udp_cfg())
        elif name == "key.action" and ev.get("action") == "enter":
            now = time.monotonic()
            if now - self.last_enter_at < ENTER_DEBOUNCE_SECONDS:
                print("[evt] 忽略重复的下键回车")
                return
            self.last_enter_at = now
            if self.trigger_mode == "mic":
                # 治本(2026-10-15): DOWN 发送也必须确定性对准 Agent —— 前台
                # 被抢(用户在开发别的东西)时, 先找回用户最近在用的 Agent 再
                # 发回车, 避免回车打进无关窗口。
                fh, ft = get_foreground_window_title()
                fe = get_foreground_process_name() if fh else ""
                if not (fh and self._match_app(ft, fe)):
                    t_app = getattr(self, "_last_agent_app", None)
                    if t_app is None:
                        for a in (self.apps or []):
                            if a.get("voice_key") and find_app_windows(
                                    [a.get("title_contains"),
                                     a.get("exe_contains")]):
                                t_app = a
                                break
                    if t_app is not None:
                        print("[evt] 下键时前台非 Agent, 确定性找回后发送")
                        self._recover_agent(t_app)
                # 通用模式: 下键=确认发送。若语音.end 丢失导致仍"在录",先兜底停录
                if self.active:
                    print("[evt] 下键对账: 先结束残留录音状态")
                    self.finish(drain=False)
                    self.press_agent_voice_key("停止残留语音")
                self.enter_injector.click()
            else:
                if self.auto_enter:
                    print("[evt] auto_enter 已开启, 下键回车由松手自动发送(忽略本次)")
                    return
                stale_voice = self.active or self.injector._held or self.injector._active
                if stale_voice:
                    print("[evt] 下键对账: 先结束残留语音状态")
                self.finish(drain=False)
                if stale_voice:
                    time.sleep(0.05)
                self.enter_injector.click()
        # status/agent.* 等其余事件与本程序无关,忽略

    def on_audio(self, payload):
        # 载荷 = [seq][0x80] + 804B ADPCM block;去掉 2B 帧头再解码
        # --no-audio 模式下电脑使用自己的麦克风，设备音频包直接丢弃。
        if self.active and len(payload) == 806 and payload[1] == 0x80:
            seq = payload[0]
            if self.last_seq is not None and not 0 < ((seq - self.last_seq) & 255) < 128:
                return
            self.last_seq = seq
            self.last_audio_at = time.monotonic()
            if self.sink is not None:
                self.sink.write_block(payload[2:])
            self.audio_blocks += 1
            if self.audio_blocks == 1:
                print("[audio] 已收到设备首个音频块并写入 VB-Cable")

    async def _push_udp_cfg(self):
        """把 隧道地址+令牌 推给设备存 NVS(cfg.udp 下行, 固件侧同步实现)。"""
        ep = self.remote_endpoint
        host, _, port_s = ep.partition(":")
        if not host:
            return
        port = int(port_s) if port_s.strip().isdigit() else 33333
        msg = {"type": "cfg.udp", "host": host, "port": port}
        if self.pair_token:
            msg["token"] = self.pair_token
        line = (json.dumps(msg) + "\n").encode()
        try:
            await self._transport.write_gatt_char(EVENT_UUID, line)
            print(f"[cfg] 已向设备推送跨网配置: {host}:{port}"
                  f"{', 含配对令牌' if self.pair_token else ''}(存设备 NVS)")
        except Exception as e:
            print(f"[cfg] 跨网配置推送失败(设备下次上线重试): {e}", file=sys.stderr)

    def on_disconnect(self):
        print("[link] 设备断开,热键复位;等待 beacon 自动重连...")
        self.finish()                 # 链路死了热键必须抬起来

    async def run(self):
        self.loop = asyncio.get_event_loop()
        # 焦点观察器(2026-10-15 治本): 持续锚定"用户最近在用的 Agent 窗口",
        # 保证按键找回与"用户此刻在开发什么/焦点被谁抢"完全无关。
        asyncio.ensure_future(self._focus_watcher())
        while True:
            try:
                transport = UdpTransport(port=self.args.port,
                                         device_wait_timeout=10 ** 9,
                                         device_ip=self.args.device_ip,
                                         pair_token=getattr(self, "pair_token", ""),
                                         auto_recover=False)
            except UdpError as e:
                # 端口被占=有残留转发器进程: 精准清理后重绑(不再借道 PowerShell,
                # 那条路在本机挂死过, 见 kill_stale_instances docstring)
                print(f"[guard] UDP {self.args.port} 绑定失败({e}), "
                      f"清理占用端口的残留进程后 3s 重试...")
                try:
                    kill_stale_instances(self.args.port)
                except Exception as ge:
                    print(f"[guard] 清理异常(忽略): {ge}")
                await asyncio.sleep(3)
                continue
            status_task = None
            # 链路断开信号:on_disconnect 由读线程经 call_soon_threadsafe 在
            # 事件循环线程里调用,这里 set asyncio.Event 唤醒下方 wait。
            # 修(2026-09-13):原 `await asyncio.Event().wait()` 等的是永远没人
            # set 的匿名事件 —— 设备静默 6s 判定断开后读线程退出、beacon 照发,
            # 本协程却永远卡死 → forwarder 变成"会发 beacon 但聋了"的僵尸,
            # 再也收不到设备(实测复现)。
            link_down = asyncio.Event()

            def on_disconnect():
                self.on_disconnect()
                link_down.set()

            try:
                await transport.scan_for_device(None, None)
                self._transport = transport
                await transport.connect("udp://",
                                        on_disconnect=on_disconnect)
                await transport.start_notify(EVENT_UUID, self.on_event)
                await transport.start_notify(AUDIO_UUID, self.on_audio)
                print("[link] 就绪: 等待设备按键/音频(设备上打开「AI语音」, "
                      "按住 UP 说话)")

                async def status():
                    last_status = 0.0
                    announced_status = None
                    while True:
                        self.check_health()
                        now = time.monotonic()
                        if now - last_status >= 1.0:
                            last_status = now
                            try:
                                current_status = self.pc_status()
                                await transport.write_gatt_char(EVENT_UUID,
                                    (json.dumps(current_status) + "\n").encode())
                                if current_status != announced_status:
                                    announced_status = current_status
                                    print("[link] PC 就绪状态已发送: "
                                          f"audio={current_status['audioReady']} "
                                          f"hotkey={current_status['hotkeyReady']}")
                            except Exception as e:
                                print(f"[link] PC 状态回报失败: {e}",
                                      file=sys.stderr)
                                self.finish()
                                link_down.set()
                                return
                        await asyncio.sleep(0.2)

                status_task = asyncio.create_task(status())
                await link_down.wait()   # 直到链路断(on_disconnect 已 set)
                link_down.clear()
            except UdpError as e:
                print(f"[link] {e};10s 后重试", file=sys.stderr)
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return
            finally:
                if status_task:
                    status_task.cancel()
                    try:
                        await status_task
                    except asyncio.CancelledError:
                        pass
                self.finish()
                try:
                    await transport.disconnect()
                except Exception:
                    pass
            await asyncio.sleep(0.5)


def load_local_config():
    """Portable config beside the executable/source; never needs the ASR GUI.

    加载优先级(确保「即插即用、零配置」):
      voice-config.json  >  config.local.json  >  voice-config.example.json(出厂默认)
    前两者被 .gitignore 忽略(用户私有), 发布包只含 example; 若用户未放真配置,
    自动回退 example 让程序开箱即用, 而非空配置空跑。
    """
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
    candidates = ["voice-config.json", "config.local.json", "voice-config.example.json"]
    used = None
    for name in candidates:
        p = os.path.join(base, name)
        if os.path.isfile(p):
            used = p
            break
    if used is None:
        print("[config] 未找到任何配置文件(含出厂 example), 程序将以空配置运行; "
              "如需自定义请复制 voice-config.example.json 为 voice-config.json")
        return {}
    try:
        with open(used, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        if os.path.basename(used) == "voice-config.example.json":
            print("[config] 未找到 voice-config.json, 已回退使用出厂默认配置(example); "
                  "如需自定义请另存为 voice-config.json 后修改")
        return cfg if isinstance(cfg, dict) else {}
    except Exception as e:
        print(f"[config] 读取 {used} 失败: {e}, 回退空配置")
        return {}


def list_audio_devices():
    try:
        import sounddevice as sd
    except (ImportError, OSError) as e:
        print(f"sounddevice 不可用: {e}")
        return
    for i, d in enumerate(sd.query_devices()):
        kind = "播放" if d["max_output_channels"] > 0 else "录音"
        print(f"  [{i}] {kind} {d['name']}")
    print("提示: 找名字含 'CABLE Input' 的播放设备 = 虚拟声卡已安装。")


def _config_path():
    base = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                           else os.path.abspath(__file__))
    return os.path.join(base, "voice-config.json")


def kill_stale_instances(port=33333):
    """杀掉占用 UDP 端口的残留转发器进程(纯 ctypes 查系统 UDP 表, 精准按 PID)。

    历史(2026-10-14 19:23 事故): 曾用 PowerShell Get-CimInstance 方案且放在
    启动必经路径上同步执行 —— 本机环境下 subprocess 挂死(20s 超时都不返回),
    两次启动全卡死在横幅之后、端口从未绑定 → 设备"联不上"。
    教训: ①启动关键路径上绝不 spawn 外部进程; ②只在端口真被占时才动手。
    现方案: GetExtendedUdpTable 查谁占着 port → 校验进程名含 python → Terminate。
    """
    import ctypes
    my_pid = os.getpid()
    AF_INET = 2
    UDP_TABLE_OWNER_PID = 1
    iphlpapi = ctypes.windll.iphlpapi
    kernel32 = ctypes.windll.kernel32

    class MIB_UDPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [("dwLocalAddr", ctypes.c_uint32),
                    ("dwLocalPort", ctypes.c_uint32),
                    ("dwOwningPid", ctypes.c_uint32)]

    size = ctypes.c_uint32(0)
    # 第一次调用取所需缓冲区大小(返回 122=ERROR_INSUFFICIENT_BUFFER)
    iphlpapi.GetExtendedUdpTable(None, ctypes.byref(size), False,
                                 AF_INET, UDP_TABLE_OWNER_PID, 0)
    if not size.value:
        print(f"[guard] 查询 UDP 表失败(缓冲区大小=0), 跳过清理")
        return
    buf = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedUdpTable(buf, ctypes.byref(size), False,
                                    AF_INET, UDP_TABLE_OWNER_PID, 0) != 0:
        print("[guard] 查询 UDP 表失败, 跳过清理")
        return
    n_entries = ctypes.cast(buf, ctypes.POINTER(ctypes.c_uint32))[0]
    rows = ctypes.cast(ctypes.byref(buf, 4),
                       ctypes.POINTER(MIB_UDPROW_OWNER_PID))
    port_net = ((port & 0xFF) << 8) | ((port >> 8) & 0xFF)  # htons(port)
    victims = []
    for i in range(n_entries):
        row = rows[i]
        if (row.dwLocalPort & 0xFFFF) == port_net and row.dwOwningPid != my_pid:
            victims.append(row.dwOwningPid)
    if not victims:
        print(f"[guard] 端口 {port} 无残留占用, 无需清理")
        return
    for pid in set(victims):
        # 安全阀: 只杀进程名含 python 的(避免误杀恰好抢了端口的其他软件)
        name_buf = ctypes.create_unicode_buffer(512)
        h = kernel32.OpenProcess(0x1000 | 0x0001, False, pid)  # QUERY_LIMITED|TERMINATE
        pname = ""
        if h:
            ln = ctypes.c_uint32(512)
            if kernel32.QueryFullProcessImageNameW(h, 0, name_buf,
                                                   ctypes.byref(ln)):
                pname = name_buf.value
        if "python" not in pname.lower():
            print(f"[guard] 端口 {port} 被 PID {pid}({pname or '未知'})占用, "
                  f"不是 python 转发器, 不动它")
            if h:
                kernel32.CloseHandle(h)
            continue
        if kernel32.TerminateProcess(h, 1):
            print(f"[guard] 已清理残留转发器: PID {pid}")
        else:
            print(f"[guard] 清理 PID {pid} 失败(忽略)")
        kernel32.CloseHandle(h)


# VBAN 推流实时状态(托盘菜单读取): target=目标地址, ok=发送端是否已拉起,
# proc=Popen 句柄(存活检测), mode=自研/兜底
_VBAN_STATE = {"target": None, "ok": False, "proc": None, "mode": ""}


def _vban_status_line():
    """托盘用: 一行 VBAN 推流状态(未配置=不显示)。"""
    st = _VBAN_STATE
    if not st["target"]:
        return None
    alive = False
    proc = st.get("proc")
    if proc is not None:
        try:
            alive = proc.poll() is None
        except Exception:
            alive = False
    if not st["ok"]:
        return f"音频串流: 未启动 ({st['target']})"
    tag = st.get("mode") or "发送端"
    return f"音频串流: {'推送中' if alive else '已停止'} → {st['target']} ({tag})"


def _maybe_start_vban(cfg):
    """多机场景: 自动拉起 VBAN 发送端, 把 Passport 声音推给其他电脑。

    发送端优先用本项目自研 vban_sender.py —— 按 VBAN 协议正确拼包头,
    修复第三方 VBANServer.exe 把 byte[6](声道数-1)错填 0xFF(=256 声道)
    导致官方 VBAN Receptor 一选流就闪退的问题。

    - 源码模式: 用 sys.executable 跑 vban_sender.py;
    - 冻结模式: 复用主 exe 自派发(sys.executable --vban-sender), 免去单独解释器;
    - 找不到 vban_sender.py 时, 回退旧 VBANServer.exe(有声道 bug, 仅兜底);
    - 目标 IP 从 voice-config.json 的 "vban": {"target","port","samplerate","channels"}
      读取, target 为空 = 不推流(纯单机); 无控制台窗口, 子进程也 CREATE_NO_WINDOW。
    """
    base = APP_DIR
    vban = cfg.get("vban") or {}
    target = str(vban.get("target") or "").strip()
    if not target:
        # 单机用户: 不要求必须存在发送端, 静默跳过
        _VBAN_STATE.update(target=None, ok=False, proc=None, mode="")
        return None
    _VBAN_STATE["target"] = target
    port = str(vban.get("port") or 6980)
    samplerate = str(vban.get("samplerate") or 48000)
    channels = str(vban.get("channels") or 2)

    import subprocess
    frozen = getattr(sys, "frozen", False)

    # 1) 优先自研发送端(修复 255 声道字节 bug)
    sender_script = os.path.join(base, "vban_sender.py")
    if frozen:
        # 冻结 exe: vban_sender 已随 hiddenimports 打进包里, 用 --vban-sender
        # 自派发即可 —— 不要求磁盘上有 vban_sender.py(onedir 里没有这个 .py,
        # 之前按文件判断导致打包版永远"未找到发送端, 跳过推流", 2026-10-26 修复)
        cmd = [sys.executable, "--vban-sender",
               "--target", target, "--port", port,
               "--samplerate", samplerate, "--channels", channels]
    elif os.path.isfile(sender_script):
        cmd = [sys.executable, sender_script,
               "--target", target, "--port", port,
               "--samplerate", samplerate, "--channels", channels]
        try:
            proc = subprocess.Popen(cmd, cwd=base,
                             stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _VBAN_STATE.update(ok=True, proc=proc, mode="自研发送端")
            print(f"[vban] 音频串流已启动(自研发送端): CABLE → {target}:{port} "
                  f"({samplerate}Hz/{channels}ch, 修复声道字节)")
            return target
        except Exception as e:
            print(f"[vban] 自研发送端启动失败({e}); 尝试回退 VBANServer.exe")

    # 2) 旧 VBANServer.exe 兜底(有声道字节 bug, 仅当无自研发送端时)
    exe = os.path.join(base, "vban", "VBANServer", "VBANServer.exe")
    if os.path.isfile(exe):
        try:
            probe = subprocess.run(["tasklist", "/FI", "IMAGENAME eq VBANServer.exe"],
                                   capture_output=True, text=True,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if "VBANServer" in (probe.stdout or ""):
                print("[vban] VBANServer 已在运行, 不重复启动")
                _VBAN_STATE.update(ok=True, proc=None, mode="旧VBANServer(已在运行)")
                return "already"
        except Exception:
            pass
        try:
            proc = subprocess.Popen(
                [exe, "--device", "CABLE", "--samplerate", samplerate,
                 "--channels", channels, "--target", target, "--port", port],
                cwd=os.path.join(base, "vban", "VBANServer"),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            _VBAN_STATE.update(ok=True, proc=proc, mode="旧VBANServer兜底")
            print(f"[vban] 音频串流已启动(旧VBANServer兜底): CABLE → {target}:{port}")
            return target
        except Exception as e:
            print(f"[vban] VBANServer 启动失败({e}); 仅影响多机音频, 不影响本机")
            return None
    print("[vban] 未找到任何发送端(vban_sender.py / VBANServer.exe), 跳过推流")
    return None


def main():
    # 自派发: 冻结 exe 以 --vban-sender 复用自身运行 VBAN 发送端子进程,
    # 免去单独解释器/打包 python 的复杂度(详见 _maybe_start_vban)
    if "--vban-sender" in sys.argv:
        sys.argv.remove("--vban-sender")
        try:
            import vban_sender
            vban_sender.main()
        except Exception:
            import traceback
            try:
                with open(os.path.join(APP_DIR, "vban_sender.log"), "a",
                          encoding="utf-8") as _f:
                    _f.write(traceback.format_exc() + "\n")
            except Exception:
                pass
        return

    # pythonw 静默运行(托盘模式)时 stdout/stderr 为 None, 后面任何 print
    # 都会崩, 先兜底重定向; 日志稍后统一切到 forwarder.log
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    cfg = load_local_config()
    default_key = str(cfg.get("hotkey_key") or "right-shift")
    default_mode = str(cfg.get("hotkey_mode") or "hold")
    default_target_title = str(cfg.get("target_title") or "")
    default_auto_enter = bool(cfg.get("auto_enter", False))
    default_trigger_mode = str(cfg.get("trigger_mode") or "mic")
    default_apps = cfg.get("apps") or []
    default_send_key = str(cfg.get("send_key") or "enter")
    default_stop_on_release = bool(cfg.get("stop_on_release", True))

    ap = argparse.ArgumentParser(
        description="AI Passport 热键+麦克风转发器(配置见 voice-config.json, "
                    "命令行参数优先)")
    ap.add_argument("--key", default=default_key,
                    help="注入的热键: 单键(right-shift/f6/h/1...)或组合键"
                         "(win+h / ctrl+shift+space);缺省取配置 hotkey_key")
    ap.add_argument("--mode", default=default_mode,
                    choices=["tap", "hold"],
                    help="hold=长按式触发(按住说话，松手结束，推荐); "
                         "tap=点击式触发(按一次开启，再按一次结束)")
    ap.add_argument("--port", type=int,
                    default=int(cfg["udp_port"]) if str(cfg.get("udp_port") or "").strip().isdigit() else 33333)
    ap.add_argument("--output-substr",
                    default=str(cfg.get("output_substr") or "CABLE Input"),
                    help="虚拟声卡播放设备名包含的子串")
    ap.add_argument("--device-ip",
                    default=str(cfg.get("device_ip") or ""),
                    help="设备目标IP(如 192.168.5.6,防路由器AP隔离阻断广播)")
    ap.add_argument("--pair-token",
                    default=str(cfg.get("pair_token") or ""),
                    help="配对令牌(跨网模式安全底线): 非空则校验设备上行包令牌尾,"
                         " beacon 携带 t 字段; 留空=局域网遗留模式全放行")
    ap.add_argument("--remote-endpoint",
                    default=str(cfg.get("remote_endpoint") or ""),
                    help="跨网隧道公网地址 host:port(如 SunnyNgrok/frp 给的地址);"
                         " 非空时 device.hello 后自动推送 cfg.udp 存进设备 NVS")
    ap.add_argument("--no-audio", action="store_true", help="只做热键转发")
    ap.add_argument("--target-title", default=default_target_title,
                    help="[text模式] 自动聚焦的窗口标题子串(留空=不聚焦)")
    ap.add_argument("--auto-enter", action="store_true", default=default_auto_enter,
                    help="[text模式] 松手后自动回车发送")
    ap.add_argument("--focus-click", default=str(cfg.get("focus_click") or ""),
                    help="[text模式] 聚焦后点击坐标 x,y(定位输入框)")
    ap.add_argument("--trigger-mode", default=default_trigger_mode,
                    choices=["mic", "text"],
                    help="mic=通用Agent模式(注入Agent自带语音快捷键, Agent自转录, 推荐); "
                         "text=旧模式(微信输入法WeType注入文字)")
    ap.add_argument("--test-key", metavar="SPEC",
                    help="隔离测试: 3 秒倒计时后向前台窗口注入一次快捷键(如 --test-key \"alt+v\"), "
                         "用于排查某 Agent 的语音键注入是否有效, 不启动转发器")
    ap.add_argument("--inject-mode", default="scan", choices=["scan", "vk"],
                    help="scan=扫描码事件(默认, 最接近真实键盘); vk=纯VK事件(备用, "
                         "scan 注入无效时试这个)")
    ap.add_argument("--send-key", default=default_send_key,
                    help="下键发送的按键, 默认 enter(回车)")
    ap.add_argument("--stop-on-release", action="store_true",
                    default=default_stop_on_release,
                    help="松手时自动再注入一次语音键停录(推荐)")
    ap.add_argument("--list-keys", action="store_true",
                    help="列出全部可用键名后退出")
    ap.add_argument("--list-devices", action="store_true",
                    help="列出本机声卡后退出")
    ap.add_argument("--diagnose", action="store_true",
                    help="检查依赖、声卡与端口，不模拟按键、不录音")
    args = ap.parse_args()

    if args.diagnose:
        from diagnose_voice import main as diagnose
        raise SystemExit(diagnose())

    global _INJECT_VK_ONLY
    _INJECT_VK_ONLY = (args.inject_mode == "vk")

    # 文件日志: 黑窗口打印的内容同步追加到 forwarder.log, 便于远程诊断
    class _Tee:
        def __init__(self, *streams):
            self.streams = streams

        def write(self, data):
            for s in self.streams:
                try:
                    s.write(data)
                except Exception:
                    pass

        def flush(self):
            for s in self.streams:
                try:
                    s.flush()
                except Exception:
                    pass

    _log_path = os.path.join(APP_DIR, "forwarder.log")
    try:
        _log_f = open(_log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = _Tee(sys.stdout, _log_f)
        sys.stderr = _Tee(sys.stderr, _log_f)
        print(f"\n===== 启动 {time.strftime('%Y-%m-%d %H:%M:%S')} =====")
        print("[ver] v2026-10-14.2230 (TRAE CDP 麦克风 + 跨网: 配对令牌尾/beacon t 字段/cfg.udp 自动推送)")
        print(f"[log] 日志同步写入: {_log_path}")
    except OSError as e:
        print(f"[log] 警告: 无法写日志文件({e}), 仅控制台输出")

    # 单实例守卫: 端口守卫(kill_stale_instances)依赖"旧实例已绑定端口",
    # 同一秒内双击两次时有竞态窗口(新实例还没绑端口就被查表), 这里用
    # 命名互斥体兜底 —— 持有者退出前, 任何重复启动直接自行退出
    try:
        _mutex = ctypes.windll.kernel32.CreateMutexW(
            None, False, "DaDaPassportVoiceMutex")
        if ctypes.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            print("[guard] 检测到转发器已在运行(看右下角托盘), 本次重复启动退出")
            time.sleep(2)
            return
    except Exception as _guard_err:
        print(f"[guard] 单实例守卫不可用({_guard_err}), 继续启动")

    make_console_unfocusable()

    # 多机音频串流: 有 vban/VBANServer 且配置了 vban.target 时自动拉起
    _maybe_start_vban(cfg)

    if args.test_key:
        fwd_probe = Forwarder.__new__(Forwarder)
        print("=" * 60)
        print("           快捷键注入隔离测试")
        print("=" * 60)
        print(f"目标键: {args.test_key}   注入模式: {args.inject_mode}")
        print("请在 5 秒内把光标点进目标窗口(如 TRAE 聊天输入框)...")
        for i in range(5, 0, -1):
            print(f"  {i}...")
            time.sleep(1)
        ok = fwd_probe._tap_key(args.test_key, "隔离测试")
        if ok:
            hwnd, title = get_foreground_window_title()
            print(f"已注入。当前前台窗口: {title!r}")
            print("观察目标 App 是否有反应; 若无反应可试: "
                  f'--test-key "{args.test_key}" --inject-mode vk')
        raise SystemExit(0 if ok else 1)

    if args.list_keys:
        print("可用键名(组合键用 + 连接,如 win+h):")
        names = sorted(KEY_MAP)
        width = max(len(n) for n in names) + 2
        for i in range(0, len(names), 6):
            print("  " + "".join(n.ljust(width) for n in names[i:i + 6]))
        print("修饰键: ctrl/alt/shift/win (left-/right- 前缀区分左右)")
        return

    if args.list_devices:
        list_audio_devices()
        return

    try:
        resolve_key_spec(args.key)
    except ValueError as e:
        ap.error(f"{e};用 --list-keys 查看全部键名")

    if getattr(args, "focus_click", ""):
        try:
            _fx, _fy = (int(v) for v in str(args.focus_click).split(","))
            args.focus_click = (_fx, _fy)
        except Exception:
            args.focus_click = None
    else:
        args.focus_click = None
    args.apps = default_apps
    fwd = Forwarder(args)
    print("=" * 66)
    print("           AI Passport · 随身 AI 语音服务已启动")
    print("=" * 66)
    if fwd.trigger_mode == "mic":
        print(" [通用 Agent 模式 mic · 键盘注入, 非鼠标点按钮]")
        print("   1. 硬件: AI Passport 进「随身 AI 语音」, 与电脑同一局域网")
        print("   2. 系统麦克风: 设为 CABLE Output (VB-Audio) —— Agent 录到的就是你")
        print("      通过 AI Passport 说的话")
        print("   3. 光标停在已配置的 Agent 输入框(WorkBuddy/Trae...),")
        _app_sum = " / ".join(
            f"{a.get('title_contains') or a.get('exe_contains')}="
            f"{a.get('voice_key')}"
            for a in (fwd.apps or []) if a.get("voice_key")) or "见配置"
        print(f"      UP 键按下 → 注入该 Agent 语音快捷键开始录({_app_sum}, Agent 自转录)")
        print("      注: 内置版本自适应 —— WorkBuddy 5.8.3+ 默认热键自动改注 alt;")
        print("         在应用里改过热键的, 配置该应用加 \"voice_key_rules\": [] 关闭适配")
        print("   4. 松开 UP → 再注入一次该语音键停录(不发送); 核对文字后按设备 DOWN 键回车发送")
        print("   5. 无需校准坐标; 只要前台窗口是已配置 Agent 即可(应用标题含 WorkBuddy / Trae)")
    else:
        print(" [旧模式 text / 微信输入法注入]")
        print("   1. 硬件: 开机并进入「随身 AI 语音」(或「AI语音」)")
        print("   2. 输入法: 语音识别麦克风选择 CABLE Output (VB-Audio)")
        print("   3. 说话: 鼠标点进电脑输入框，长按设备 UP 键说话，松手出字")
        print("   4. 松开 UP 结束语音；核对文字后，单击 DOWN 发送回车")
    print(" [提示] 保持此窗口开启即可使用；按 Ctrl+C 或关闭此窗口退出")
    print("=" * 66)
    print(f"模式: {fwd.trigger_mode}  发送键: {fwd.send_key}  "
          f"音频: {'关闭' if args.no_audio else ('→ ' + fwd.sink.device_name if fwd.sink.enabled else '未就绪(装 VB-Cable 后重启本程序)')}")
    # 系统托盘(可选): 右下角常驻图标, 显示运行状态, 菜单 = 状态/日志/退出
    _t0 = time.time()

    def _tray_status():
        """返回实时状态行(list[str]), 托盘菜单逐行显示, 第一行进 tooltip。"""
        lines = []
        try:
            age = time.time() - getattr(fwd, "_last_hello_at", -1e9)
            if age < 0:
                lines.append("设备: 等待连接…")
            elif age < 10:
                lines.append("设备: 在线 ✓")
            else:
                lines.append("设备: 未连接 ✗")
        except Exception:
            pass
        try:
            vline = _vban_status_line()
            if vline:
                lines.append(vline)
        except Exception:
            pass
        try:
            mins = int((time.time() - _t0) // 60)
            lines.append(f"已运行 {mins} 分钟 · 模式 {fwd.trigger_mode}")
        except Exception:
            pass
        return lines or ["运行中"]

    try:
        from tray_icon import start_tray

        def _on_tray_quit():
            try:
                fwd.injector.stop()
            except Exception:
                pass
            os._exit(0)  # 转发器无状态(UDP+注入线程), 停注入器后硬退出

        _ico = os.path.join(APP_DIR, "passport-voice.ico")
        if not os.path.isfile(_ico) and getattr(sys, "frozen", False):
            # onedir 模式 datas 打进 _internal/, APP_DIR(=exe 目录)里没有;
            # sys._MEIPASS 在 onedir 下指向 _internal, 兜底找一遍
            _cand = os.path.join(getattr(sys, "_MEIPASS", ""),
                                 "passport-voice.ico")
            if _cand and os.path.isfile(_cand):
                _ico = _cand
        if start_tray(_ico, log_path=_log_path, on_quit=_on_tray_quit,
                      status_fn=_tray_status,
                      balloon=("AI 护照语音已启动",
                               "长按设备 UP 键说话, 文字自动进当前窗口。"
                               "右键托盘图标可看状态/打开日志/退出。")):
            print("[tray] 托盘图标已启动: 右下角可看运行状态/打开日志/退出")
        else:
            print("[tray] 托盘不可用(缺 passport-voice.ico 或非 Windows), 仅后台运行")
    except Exception as _tray_err:
        print(f"[tray] 托盘启动失败({_tray_err}), 继续无托盘运行")
    try:
        asyncio.run(fwd.run())
    except KeyboardInterrupt:
        print("\n退出")
        fwd.injector.stop()


if __name__ == "__main__":
    main()
