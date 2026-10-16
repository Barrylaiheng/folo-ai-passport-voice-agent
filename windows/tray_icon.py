# -*- coding: utf-8 -*-
"""AI Passport 语音 · 系统托盘图标（纯 ctypes，零第三方依赖）。

目的: 转发器是无界面的后台服务, 用户"双击桌面图标 → 托盘出现小图标"即可
确认它在运行; 右键/左键点托盘可打开日志或干净退出 —— 不用再去找隐藏的
控制台窗口或开任务管理器。

实现: Shell_NotifyIconW + 隐藏窗口消息循环, 与 hotkey_forwarder.py 的
ctypes 风格一致。缺失图标文件或非 Windows 环境时静默降级(返回 False),
不影响转发器本体运行。

已知取舍(刻意保持简单):
- Explorer 崩溃重启后托盘图标不会自动恢复(需重启转发器); v1 不处理
  TaskbarCreated 重建消息。
- "退出"走 os._exit(0): 转发器无状态(UDP 监听 + 注入线程), 先停注入器
  再硬退出, 避免 asyncio 跨线程取消的复杂度。
"""

import ctypes
import ctypes.wintypes as wt
import os
import threading

# --- Win32 常量 -----------------------------------------------------------
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x10
WM_APP_TRAY = 0x8000 + 1          # WM_APP+1, 自定义托盘回调消息
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

    def __init__(self, ico_path, log_path, on_quit, tooltip, status_fn=None):
        self.ico_path = ico_path
        self.log_path = log_path
        self.on_quit = on_quit
        self.tooltip = (tooltip or "AI Passport 语音")[:127]
        self.status_fn = status_fn      # 返回动态状态文本, 如 "设备在线 · 已运行 3 分钟"
        self.hwnd = None
        self.nid = None
        self.hicon = None


def _load_icon(state):
    user32 = ctypes.windll.user32
    if state.ico_path and os.path.isfile(state.ico_path):
        hicon = user32.LoadImageW(
            None, state.ico_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if hicon:
            return hicon
    return user32.LoadIconW(None, IDI_APPLICATION)


def _show_menu(state):
    """弹出托盘菜单, 返回选中的命令 ID(0=未选)。"""
    user32 = ctypes.windll.user32
    menu = user32.CreatePopupMenu()
    if not menu:
        return 0
    try:
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, IDM_STATUS,
                           "AI Passport 语音 · 运行中")
        if state.status_fn:
            try:
                info = str(state.status_fn())[:120]
                if info:
                    user32.AppendMenuW(menu, MF_STRING | MF_GRAYED,
                                       IDM_STATUS, info)
            except Exception:
                pass
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
        if msg == WM_APP_TRAY and lparam in (WM_RBUTTONUP, WM_LBUTTONUP,
                                             WM_LBUTTONDBLCLK):
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

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def start_tray(ico_path, log_path=None, on_quit=None,
               tooltip="AI Passport 语音 · 运行中", status_fn=None):
    """启动托盘线程(非阻塞)。成功返回 True, 失败返回 False(转发器照常跑)。"""
    if os.name != "nt":
        return False
    state = _TrayState(ico_path, log_path, on_quit, tooltip, status_fn)
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
