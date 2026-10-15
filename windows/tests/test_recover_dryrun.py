# -*- coding: utf-8 -*-
"""确定性找回机制干跑测试(不注入按键, 会短暂切换前台窗口)。

用途: 验证"焦点被任意程序抢走"场景下, _recover_agent 能否找回目标 Agent
主窗口并恢复前台。交互式诊断脚本, 需目标应用(如 Trae)正在运行。

用法: .venv\\Scripts\\python.exe tests\\test_recover_dryrun.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hotkey_forwarder as hf  # noqa: E402


def main():
    print('=== 1) 当前前台(模拟"抢焦点者") ===')
    hwnd, title = hf.get_foreground_window_title()
    exe = hf.get_foreground_process_name()
    print(f'  hwnd={hwnd} title={title!r} exe={exe!r}')

    app = {"title_contains": "Trae", "exe_contains": "Trae CN",
           "voice_key": "alt+v"}
    print(f'=== 2) find_app_windows({app["exe_contains"]}) 按面积枚举 ===')
    wins = hf.find_app_windows([app["title_contains"], app["exe_contains"]])
    for h, t in wins:
        print(f'  hwnd={h} title={t!r} proc={hf._window_process_name(h)!r}')
    if not wins:
        print('  [跳过] 目标应用未运行(请先启动 Trae 或改脚本里的 app 匹配)')
        return 0

    print('=== 3) _recover_agent 干跑(切前台 + UIA 焦点恢复, 不注键) ===')

    class FakeFwd(object):
        _last_agent_hwnd = None
        _last_agent_app = None
        _recover_agent = hf.Forwarder._recover_agent

    f = FakeFwd()
    rhwnd, rtitle = f._recover_agent(app)
    print(f'  recovered hwnd={rhwnd} title={rtitle!r}')
    if not rhwnd:
        print('  [FAIL] 找回失败')
        return 1

    print('=== 4) 找回后前台核验 ===')
    fh, ft = hf.get_foreground_window_title()
    fe = hf.get_foreground_process_name()
    ok = (fh == rhwnd) or (app["exe_contains"].lower() in (fe or '').lower())
    print(f'  前台 hwnd={fh} title={ft!r} exe={fe!r}')
    print('  [PASS] 前台已是目标应用' if ok else '  [WARN] 前台未切过去(可能被系统前台锁拦截)')
    return 0 if ok else 2


if __name__ == '__main__':
    sys.exit(main())
