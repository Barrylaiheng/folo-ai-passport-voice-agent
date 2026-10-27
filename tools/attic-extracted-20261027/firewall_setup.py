# -*- coding: utf-8 -*-
"""Windows 防火墙入站规则: 让 AI Passport 设备能与 PC 转发器通信。

设备 → PC 走 UDP:
  - 33333: AI Passport 语音/事件主通道(设备上行, PC 监听)
  - 6980:  VBAN 音频串流(多机音频用, 可选)

为什么需要它: Windows 防火墙在"公用网络"配置下常静默拦截入站 UDP,
表现为 PC 端转发器收不到设备心跳 → 设备屏显"等待电脑连接"。

设计要点:
  - 用**端口规则**而非程序规则: pythonw(脚本运行) 与 onefile exe(打包运行)
    两种启动方式都被覆盖, 且规则不因 exe 路径变化而失效。
  - 幂等: 同名规则已存在则跳过, 可反复执行无副作用。
  - 需要管理员权限: 非管理员时尝试提权重启自身; 失败则给出手动步骤。
"""
import ctypes
import os
import subprocess
import sys

# (端口, 规则显示名, 说明)
RULES = [
    (33333, "AI-Passport-Voice UDP 33333", "AI Passport 设备→PC 语音/事件通道"),
    (6980, "AI-Passport-Voice VBAN UDP 6980", "AI Passport VBAN 音频串流(多机)"),
]


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _rule_exists(name):
    """netsh 查询同名规则是否存在(按显示名匹配)。"""
    try:
        out = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule",
             "name=%s" % name],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return out.returncode == 0 and name in (out.stdout or "")
    except Exception:
        return False


def _add_rule(port, name, desc):
    if _rule_exists(name):
        print("[firewall] 规则已存在, 跳过: %s" % name)
        return True
    rc = subprocess.run(
        ["netsh", "advfirewall", "firewall", "add", "rule",
         "name=%s" % name, "dir=in", "action=allow",
         "protocol=UDP", "localport=%d" % port,
         "profile=any", "enable=yes", "desc=%s" % desc],
        capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if rc.returncode == 0:
        print("[firewall] 已添加入站规则: %s (UDP %d)" % (name, port))
        return True
    print("[firewall] 添加失败(%s): %s" % (name, (rc.stderr or rc.stdout).strip()))
    return False


def ensure_rules():
    """添加全部规则。返回进程退出码(0=成功)。"""
    if not is_admin():
        print("[firewall] 需要管理员权限才能修改防火墙。")
        # 尝试以管理员重启自身
        try:
            exe = sys.executable
            script = os.path.abspath(sys.argv[0])
            args = [a for a in sys.argv[1:] if a != "--setup-firewall"]
            ok = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe,
                '"%s" --setup-firewall %s' % (script, " ".join(args)),
                os.path.dirname(script), 1)
            if ok > 32:
                print("[firewall] 已请求管理员提权, 请在弹窗中允许。")
                return 0
        except Exception as e:
            print("[firewall] 提权失败: %s" % e)
        print(_manual_help())
        return 1
    ok = True
    for port, name, desc in RULES:
        ok = _add_rule(port, name, desc) and ok
    if ok:
        print("[firewall] 完成。设备现在应能连上 PC 转发器。")
        print("若仍连不上, 检查: 设备与 PC 是否同一 Wi-Fi/网段; 路由器是否开启"
              "AP 隔离(会阻断广播)。")
    else:
        print(_manual_help())
    return 0 if ok else 1


def _manual_help():
    return (
        "[firewall] 手动添加步骤: 以管理员打开 CMD/PowerShell →\n"
        "  netsh advfirewall firewall add rule name=\"AI-Passport-Voice UDP 33333\" "
        "dir=in action=allow protocol=UDP localport=33333\n"
        "  netsh advfirewall firewall add rule name=\"AI-Passport-Voice VBAN UDP 6980\" "
        "dir=in action=allow protocol=UDP localport=6980\n"
        "或用图形界面: Windows 安全中心 → 防火墙和网络保护 → 高级设置 → 入站规则"
        " → 新建规则 → 端口 → UDP 33333/6980 → 允许连接。"
    )


if __name__ == "__main__":
    sys.exit(ensure_rules())
