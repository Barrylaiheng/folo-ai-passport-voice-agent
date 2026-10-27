#!/usr/bin/env python3
# 给 hotkey_forwarder.py 打「Agent 直连模式」补丁。
# 行为全部配置驱动，target_title 留空则完全沿用原行为，不破坏现有 EXE 逻辑。
import io, sys, os

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hotkey_forwarder.py")
with open(SRC, "rb") as f:
    raw = f.read()
text = raw.decode("utf-8")
had_crlf = b"\r\n" in raw
norm = text.replace("\r\n", "\n") if had_crlf else text

def apply(old, new):
    global norm
    if old not in norm:
        raise SystemExit(f"[patch] 未找到锚点:\n{old[:120]}")
    norm = norm.replace(old, new, 1)

# ---- 1. 插入 WindowFocus 助手 + 注释（在 class Forwarder 之前） ----
apply(
"""class Forwarder:""",
"""# ---------------------------------------------------------------------------
# Agent 直连模式: 自动聚焦目标窗口 + 可选自动回车发送
# (通用: 支持 WorkBuddy / TRAE / 任意程序, 由 voice-config.json 的 target_title 配置)
# ---------------------------------------------------------------------------

def focus_window_by_title(substr, click=None):
    \"\"\"把标题含 substr 的窗口提到最前(尽力而为)。返回是否找到。

    Windows 对 SetForegroundWindow 有前台锁限制, 这里用 AttachThreadInput
    标准 workaround; 仍受系统策略约束, 失败时回退到仅 ShowWindow。
    click: 可选 (x, y) 绝对像素, 聚焦后点一下以定位输入框。
    \"\"\"
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
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        fg = user32.GetForegroundWindow()
        if fg:
            fg_thread = kernel32.GetWindowThreadProcessId(fg, None)
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
        except Exception as e:
            print(f"[focus] 点击坐标失败(忽略): {e}")
    print(f"[focus] 已聚焦窗口: {title!r}")
    return True


class Forwarder:""")

# ---- 2. Forwarder.__init__ 增加字段 ----
apply(
"""        self.last_enter_at = float("-inf")""",
"""        self.last_enter_at = float("-inf")
        self.target_title = str(getattr(args, "target_title", "") or "")
        self.auto_enter = bool(getattr(args, "auto_enter", False))
        self.focus_click = getattr(args, "focus_click", None)""")

# ---- 3. voice.start: 注入聚焦 ----
apply(
"""            print("[evt] 设备开始说话(voice.start)")
            self.audio_blocks = 0""",
"""            print("[evt] 设备开始说话(voice.start)")
            if self.target_title:
                focus_window_by_title(self.target_title, self.focus_click)
            self.audio_blocks = 0""")

# ---- 4. voice.end: 自动回车发送 ----
apply(
"""        elif name == "voice.end":
            print(f"[evt] 设备结束说话(voice.end),收到音频块={self.audio_blocks}")
            self.finish(drain=True)""",
"""        elif name == "voice.end":
            print(f"[evt] 设备结束说话(voice.end),收到音频块={self.audio_blocks}")
            self.finish(drain=True)
            if self.auto_enter:
                time.sleep(0.15)
                self.enter_injector.click()
                print("[evt] 自动回车发送(auto_enter)")""")

# ---- 5. 下键回车: auto_enter 时忽略（避免双击发送） ----
apply(
"""        elif name == "key.action" and ev.get("action") == "enter":
            now = time.monotonic()
            if now - self.last_enter_at < ENTER_DEBOUNCE_SECONDS:
                print("[evt] 忽略重复的下键回车")
                return
            self.last_enter_at = now""",
"""        elif name == "key.action" and ev.get("action") == "enter":
            now = time.monotonic()
            if now - self.last_enter_at < ENTER_DEBOUNCE_SECONDS:
                print("[evt] 忽略重复的下键回车")
                return
            self.last_enter_at = now
            if self.auto_enter:
                print("[evt] auto_enter 已开启, 下键回车由松手自动发送(忽略本次)")
                return""")

# ---- 6. main(): 配置 + 参数 ----
apply(
"""    default_mode = str(cfg.get("hotkey_mode") or "hold")""",
"""    default_mode = str(cfg.get("hotkey_mode") or "hold")
    default_target_title = str(cfg.get("target_title") or "")
    default_auto_enter = bool(cfg.get("auto_enter", False))""")

apply(
"""    ap.add_argument("--no-audio", action="store_true", help="只做热键转发")""",
"""    ap.add_argument("--no-audio", action="store_true", help="只做热键转发")
    ap.add_argument("--target-title", default=default_target_title,
                    help="自动聚焦的窗口标题子串(留空=不聚焦, 沿用原行为)")
    ap.add_argument("--auto-enter", action="store_true", default=default_auto_enter,
                    help="松手后自动回车发送(配置 auto_enter 亦可开启)")
    ap.add_argument("--focus-click", default=str(cfg.get("focus_click") or ""),
                    help="聚焦后点击坐标 x,y(可选, 用于定位非自动聚焦的输入框)")""")

apply(
"""    fwd = Forwarder(args)""",
"""    if getattr(args, "focus_click", ""):
        try:
            _fx, _fy = (int(v) for v in str(args.focus_click).split(","))
            args.focus_click = (_fx, _fy)
        except Exception:
            args.focus_click = None
    else:
        args.focus_click = None
    fwd = Forwarder(args)""")

# ---- 7. 使用说明补充 ----
apply(
"""    print("   3. 说话: 鼠标点进电脑输入框，长按设备 UP 键说话，松手出字")""",
"""    print("   3. 说话: 鼠标点进电脑输入框，长按设备 UP 键说话，松手出字")
    print("   [Agent直连] 配置 target_title 后, 按住 UP 会自动聚焦目标窗口;")
    print("              auto_enter=true 时松手自动回车发送, 无需下键。")""")

out = norm
if had_crlf:
    out = out.replace("\n", "\r\n")
with open(SRC, "wb") as f:
    f.write(out.encode("utf-8"))
print("[patch] 应用成功 ->", SRC)
