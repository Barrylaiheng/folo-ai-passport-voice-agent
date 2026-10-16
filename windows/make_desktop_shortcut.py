# -*- coding: utf-8 -*-
"""在桌面创建「AI Passport 语音」快捷方式(直指 pythonw, 零窗口闪现)。"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
PYTHONW = os.path.join(BASE, ".venv", "Scripts", "pythonw.exe")
ICON = os.path.join(BASE, "passport-voice.ico")
LNK_NAME = "AI Passport 语音.lnk"


def desktop_dir():
    # 中文系统桌面在 OneDrive 重定向时也拿得到
    up = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    return up if os.path.isdir(up) else os.path.join(os.environ["USERPROFILE"], "Desktop")


def main():
    if not os.path.isfile(PYTHONW):
        print("[x] 未找到虚拟环境, 请先双击 install.cmd 完成安装")
        sys.exit(1)
    try:
        from win32com.client import Dispatch  # pywin32, install.cmd 已装
    except ImportError:
        print("[x] 缺少 pywin32, 请重新运行 install.cmd")
        sys.exit(1)

    lnk_path = os.path.join(desktop_dir(), LNK_NAME)
    shell = Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(lnk_path)
    sc.TargetPath = PYTHONW
    sc.Arguments = "-u hotkey_forwarder.py"
    sc.WorkingDirectory = BASE
    if os.path.isfile(ICON):
        sc.IconLocation = ICON + ",0"
    sc.Description = "AI Passport 语音(双击启动, 右下角托盘显示状态/退出)"
    sc.Save()
    print("[ok] 已创建桌面图标:", lnk_path)
    print("     以后双击它即可使用; 右下角托盘 = 运行状态 / 打开日志 / 退出")


if __name__ == "__main__":
    main()
