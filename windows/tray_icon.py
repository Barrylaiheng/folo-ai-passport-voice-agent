# -*- coding: utf-8 -*-
"""AI Passport 语音 · 系统托盘图标（纯 ctypes，零第三方依赖）。

目的: 转发器是无界面的后台服务, 用户"双击桌面图标 → 托盘出现小图标"即可
确认它在运行; 右键/左键点托盘可看实时状态/打开日志/干净退出 —— 不用再去找
隐藏的控制台窗口或开任务管理器。

实现: Shell_NotifyIconW + 隐藏窗口消息循环, 与 hotkey_forwarder.py 的
ctypes 风格一致。缺失图标文件或非 Windows 环境时静默降级(返回 False),
不影响转发器本体运行。

v2 改进(2026-10-26, 针对"托盘没图标/没内容"的反馈):
- 内嵌 32px 品牌图标(base64): 即便 passport-voice.ico 丢失也能显示出
  明显的橙色麦克风, 不再退化成系统默认白框;
- 动态 tooltip: 每 3 秒用 NIM_MODIFY 刷新悬停文字, 不再是静态"运行中";
- 菜单内容更丰富: 版本/设备状态/VBAN 推流目标/运行时长/触发模式 逐行
  显示(灰字), 加"打开日志/退出"两个可点项;
- 启动气泡通知: 启动后弹一次 Balloon, 明确告知"已启动+怎么用"。

已知取舍(刻意保持简单):
- Explorer 崩溃重启后托盘图标不会自动恢复(需重启转发器); v1 不处理
  TaskbarCreated 重建消息。
- "退出"走 os._exit(0): 转发器无状态(UDP 监听 + 注入线程), 先停注入器
  再硬退出, 避免 asyncio 跨线程取消的复杂度。
"""

import base64
import ctypes
import ctypes.wintypes as wt
import os
import threading

# --- Win32 常量 -----------------------------------------------------------
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIIF_INFO = 0x01
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
WM_APP_TRAY = 0x8000 + 1          # WM_APP+1, 自定义托盘回调消息
WM_TIMER = 0x0113
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0204
WM_LBUTTONDBLCLK = 0x0203
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_QUIT = 0x0012
MF_STRING = 0x00000000
MF_GRAYED = 0x00000001
MF_SEPARATOR = 0x00000800
TPM_RETURNCMD = 0x0100
TPM_RIGHTBUTTON = 0x0002
IDI_APPLICATION = 32512

# 托盘菜单命令 ID
IDM_STATUS = 100
IDM_LOG = 101
IDM_QUIT = 102

# 状态刷新间隔(毫秒) —— tooltip 每 3 秒跟随状态变化
STATUS_REFRESH_MS = 3000

# 内嵌兜底图标: 32x32 橙底白麦(与 passport-voice.ico 同款), base64 ICO。
# 仅当磁盘上的 passport-voice.ico 丢失时, 写到临时文件再加载。
_FALLBACK_ICO_B64 = (
    "AAABAAEAICAAAAEAIAD8AgAAFgAAAIlQTkcNChoKAAAADUlIRFIAAAAgAAAAIAgGAAAAc3p69AAA"
    "AsNJREFUeNrtl8tPE1EUxvlTuhQUNWrUaNQIEV8xGDVxZeJ/4IZKfeHEBwKi4APBRxzDwkQajYma"
    "qAtFghiNBTeaGFkgCyKlOtACfc30eM4d79j2zEtJu+Ikv7Rzz3e++3Vm2nQqKhbLpnJdewKIimgI"
    "LBDtj1fA1+bG9d0KAiVCcd/86i4FgRJjH0K/sjOAgC/Uw5AbHQJZ9J7W/MzoPQfomF+ObOd2FQFP"
    "uvdDLpWA4qI16jnOzPwQOuPzS1pTWYBMR52GgBd6fzc4FfXytdlwA+Sio6C/67XmRNAb+zQe4PI2"
    "8IMx/skxAPXytfpQr7Vppmuvpcs+bwMWIN1eC37wClCgv1Zv9TKPm8D4NmieqchDHiB1sQb8YIyP"
    "uAQYcdRn394TSB0LkGzbCn4wvrsEwF6xPvsxbPXSj06alySZ4AGygyoQyZ6DkH52QbxP3T8C861b"
    "CtBdAlCvWJ9BH9kjP1n8R2h6QjTST5tRPGyetg99MN+yuQDZsw8wzPTkIXvkLc4G7sV/iMZM48zA"
    "XYEYwrW55k0FSJ1tABd9sS//Gr5/YH7qrwOQDIcs09lLO2D2/EYLrwD5WpqVRZ7kLcLgXvwm7Gv8"
    "u2l7Hd4ocfOSvLkDiXMbLPSxiEuASIGWZs2bLi48rTC4FwuQOLs+b9PbAjKcu3UIqCdJvehwDEC9"
    "fC3Nkof0k2GoxwLEz6yDVL8pMrQJoGNbWmtFn30FaQZ7TnNyhvagYxZgRlkL8ZYaSI88Ea907ES8"
    "s17oZIkZXHOdKfJmAaZPr4F/Rdb/zLIAWtNq8CL56iZ4FWn8ePEAp1aBF5kvrz0DkMaPFwvw68RK"
    "KCcswM/jKzQEygT/QxI7tlxFoEzwv2SxUHUgFloG5aHa/hlhqnGpgkCJcX82iB6tUqLBKigJ6O3r"
    "6SgarAxMBivVyYYlGgILRCMv8lx86LWr3/yT8ycKI/YYAAAAAElFTkSuQmCC"
)

WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wt.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", ctypes.c_uint),
        ("uFlags", ctypes.c_uint),
        ("uCallbackMessage", ctypes.c_uint),
        ("hIcon", wt.HANDLE),
        ("szTip", ctypes.c_wchar * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", ctypes.c_wchar * 256),
        ("uVersion", ctypes.c_uint),
        ("szInfoTitle", ctypes.c_wchar * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_ubyte * 16),
        ("hBalloonIcon", wt.HANDLE),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class _TrayState:
    """托盘线程共享状态(简单命名空间)。"""

    def __init__(self, ico_path, log_path, on_quit, tooltip,
                 status_fn=None, balloon=None):
        self.ico_path = ico_path
        self.log_path = log_path
        self.on_quit = on_quit
        self.tooltip = (tooltip or "AI Passport 语音")[:127]
        self.status_fn = status_fn      # -> list[str], 菜单逐行显示的实时状态
        self.balloon = balloon          # (title, text) 启动气泡, 只弹一次
        self.balloon_shown = False
        self.hwnd = None
        self.nid = None
        self.hicon = None


def _load_icon(state):
    """优先磁盘图标; 丢失则把内嵌 base64 写到临时文件再加载; 再不行用系统默认。"""
    user32 = ctypes.windll.user32
    if state.ico_path and os.path.isfile(state.ico_path):
        hicon = user32.LoadImageW(
            None, state.ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if hicon:
            return hicon
    try:
        tmp = os.path.join(os.environ.get("TEMP", os.getcwd()),
                           "passport-voice-tray.ico")
        with open(tmp, "wb") as f:
            f.write(base64.b64decode(_FALLBACK_ICO_B64))
        hicon = user32.LoadImageW(
            None, tmp, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if hicon:
            return hicon
    except Exception:
        pass
    return user32.LoadIconW(None, IDI_APPLICATION)


def _status_lines(state):
    """取实时状态行(status_fn 返回 list[str]); 异常兜底。"""
    if not state.status_fn:
        return []
    try:
        lines = state.status_fn()
        if isinstance(lines, str):
            lines = [lines]
        return [str(x)[:110] for x in (lines or []) if x]
    except Exception:
        return []


def _refresh_tooltip(state):
    """用当前状态第一行刷新 tooltip(NIM_MODIFY, 不重建图标)。"""
    if not state.nid:
        return
    lines = _status_lines(state)
    tip = state.tooltip if not lines else f"{state.tooltip} · {lines[0]}"
    nid = state.nid
    nid.szTip = tip[:127]
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))


def _show_balloon(state):
    """启动气泡: 标题+正文, 只弹一次。"""
    if not state.nid or state.balloon_shown or not state.balloon:
        return
    title, text = state.balloon
    nid = state.nid
    nid.uFlags = NIF_INFO
    nid.szInfo = str(text)[:255]
    nid.szInfoTitle = str(title)[:63]
    nid.dwInfoFlags = NIIF_INFO
    ctypes.windll.shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
    state.balloon_shown = True


def _show_menu(state):
    """弹出托盘菜单, 返回选中的命令 ID(0=未选)。"""
    user32 = ctypes.windll.user32
    menu = user32.CreatePopupMenu()
    if not menu:
        return 0
    try:
        # 标题行(灰字)
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, IDM_STATUS,
                           "AI Passport · 随身 AI 语音")
        # 实时状态行(灰字, 每行一条)
        for line in _status_lines(state):
            user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, IDM_STATUS,
                               "　" + line)
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        if state.log_path and os.path.isfile(state.log_path):
            user32.AppendMenuW(menu, MF_STRING, IDM_LOG, "打开日志")
        user32.AppendMenuW(menu, MF_STRING, IDM_QUIT, "退出")
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        # KB135788: TrackPopupMenu 前必须 SetForegroundWindow, 否则菜单不消失
        user32.SetForegroundWindow(state.hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
            pt.x, pt.y, 0, state.hwnd, None)
        user32.PostMessageW(state.hwnd, WM_NULL, 0, 0)
        return cmd
    finally:
        user32.DestroyMenu(menu)


def _handle_menu(state, cmd):
    if cmd == IDM_LOG and state.log_path:
        try:
            os.startfile(state.log_path)
        except OSError:
            pass
    elif cmd == IDM_QUIT:
        shell32 = ctypes.windll.shell32
        if state.nid:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(state.nid))
        if state.on_quit:
            try:
                state.on_quit()
            except Exception:
                os._exit(0)
        os._exit(0)


def _wnd_proc_factory(state):
    user32 = ctypes.windll.user32
    # 64 位下 wparam/lparam 超过 32 位会 OverflowError, 必须显式声明签名
    user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint,
                                      ctypes.c_size_t, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t

    def _proc(hwnd, msg, wparam, lparam):
        if msg == WM_TIMER and wparam == 1:
            _refresh_tooltip(state)          # 周期刷新悬停状态
            return 0
        if msg == WM_APP_TRAY and lparam in (WM_RBUTTONUP, WM_LBUTTONUP,
                                             WM_LBUTTONDBLCLK):
            _refresh_tooltip(state)          # 打开菜单前也刷一遍, 保证最新
            _handle_menu(state, _show_menu(state))
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    return WNDPROC(_proc)  # 保存在闭包/局部引用, 防止 GC 回收回调


def _tray_thread(state):
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32   # Shell_NotifyIconW 在 shell32, 不在 user32
    kernel32 = ctypes.windll.kernel32
    hinst = kernel32.GetModuleHandleW(None)

    proc = _wnd_proc_factory(state)          # 引用必须活到线程结束
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc
    wc.hInstance = hinst
    wc.lpszClassName = "DaDaPassportVoiceTray"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        return

    # 隐藏的普通顶层窗口(不用 HWND_MESSAGE: 托盘回调需要它)
    state.hwnd = user32.CreateWindowExW(
        0, wc.lpszClassName, "AI Passport 语音", 0,
        0, 0, 0, 0, None, None, hinst, None)
    if not state.hwnd:
        return

    state.hicon = _load_icon(state)
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(nid)
    nid.hWnd = state.hwnd
    nid.uID = 1
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_APP_TRAY
    nid.hIcon = state.hicon
    nid.szTip = state.tooltip
    if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
        return
    state.nid = nid

    # 周期刷新 tooltip 的定时器 + 启动气泡(延迟 800ms, 等图标稳定)
    user32.SetTimer(state.hwnd, 1, STATUS_REFRESH_MS, None)
    user32.SetTimer(state.hwnd, 2, 800, None)

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_TIMER and msg.wParam == 2:
            user32.KillTimer(state.hwnd, 2)   # 气泡只弹一次
            _show_balloon(state)
            continue
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def start_tray(ico_path, log_path=None, on_quit=None,
               tooltip="AI Passport 语音 · 运行中", status_fn=None,
               balloon=None):
    """启动托盘线程(非阻塞)。成功返回 True, 失败返回 False(转发器照常跑)。

    status_fn: 无参函数, 返回 list[str] 实时状态行(菜单逐行显示,
               第一行同时进 tooltip)。
    balloon:   (标题, 正文) 启动后弹一次气泡通知, None = 不弹。
    """
    if os.name != "nt":
        return False
    state = _TrayState(ico_path, log_path, on_quit, tooltip, status_fn,
                       balloon)
    try:
        t = threading.Thread(target=_tray_thread, args=(state,),
                             daemon=True, name="passport-tray")
        t.start()
        # 给线程一点时间走到 Shell_NotifyIconW, 失败能在这里暴露
        t.join(0.3)
        if not t.is_alive():
            return False          # 线程已退出 = 注册/创建失败
        return True
    except Exception:
        return False
