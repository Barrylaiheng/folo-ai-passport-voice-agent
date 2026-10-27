# -*- coding: utf-8 -*-
"""看门狗(治标): 转发器被杀后自动拉起。

背景: AI Passport 转发器因"键盘注入+UIA"被火绒 HIPS/360 等行为级杀软静默终止
(见 docs/ANTIVIRUS_FIREWALL.md)。本脚本作为父进程监控转发器, 它一死就重启, 减少
你被卡住。注意: 这是治标 —— 真正安静需要去杀软加行为级白名单(见文档)或给程序
签名。白名单/签名生效后可不再用看门狗。

安全设计(避免热循环):
  - 转发器正常长跑后(>MIN_RUN_SECONDS)被杀 -> 一律重启(AV 杀属此类)。
  - 启动后极短时间(<MIN_RUN_SECONDS)就死 -> 视为启动崩溃, 连续 N 次后停止并写日志,
    不再空转, 避免 CPU/日志被打爆。
  - 上限 MAX_RESTARTS 次/进程生命周期, 超限停止(防极端情况下无限拉起)。

抓现行(2026-10-17 加): 每次转发器异常死亡, 自动抓死前 30s 的 Security 4689(进程终止)
事件 + 当前进程清单写 kill-evidence.log, 便于事后定位"谁杀的"(AV / 会话切换 / 手动结束)。

看门狗自身互斥: 只允许一个看门狗实例, 避免手动双击与计划任务重复拉起导致多份转发器。
"""
import base64
import os
import subprocess
import sys
import time

MIN_RUN_SECONDS = 10          # 短于此视为启动崩溃而非外部被杀
MAX_QUICK_DEATHS = 5          # 连续"启动即死"上限, 超过停止
MAX_RESTARTS = 200            # 单看门狗生命周期内重启上限
WD_MUTEX = "Global\\DaDaPassportVoiceWatchdogMutex"


def _app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _log(msg):
    """pythonw 下无控制台, print 会丢 -> 同时落盘 watchdog.log。"""
    line = "[watchdog] %s  @ %s" % (msg, time.strftime("%H:%M:%S"))
    print(line)
    try:
        with open(os.path.join(_app_dir(), "watchdog.log"), "a",
                  encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _single_instance():
    """进程级互斥: 仅允许一个看门狗。返回 True=本实例可继续。"""
    try:
        kernel32 = __import__("ctypes").windll.kernel32
        h = kernel32.CreateMutexW(None, 1, WD_MUTEX)
        if not h:
            return True  # 创建失败就放行, 不阻断
        err = kernel32.GetLastError()
        if err == 0xB7:  # ERROR_ALREADY_EXISTS
            _log("已有看门狗实例在运行(互斥 %s), 本实例退出" % WD_MUTEX)
            return False
        return True
    except Exception:
        return True


def _ps_run(script, timeout=30):
    """以 base64(UTF-16LE) 方式运行 PowerShell, 规避引号/中文编码问题。

    返回按系统码页(mbcs, 中文机=GBK)解码的文本; 任何失败返回空串。
    """
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-EncodedCommand", b64],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=timeout)
        return (p.stdout or b"").decode("mbcs", errors="replace")
    except Exception:
        return ""


def _collect_kill_evidence(rc, dt):
    """抓现行: 转发器异常死亡时, 记录死前 30s 的 Security 4689(进程终止)
    事件 + 当前进程清单, 写 kill-evidence.log, 便于事后定位"谁杀的"
    (AV / 会话切换 / 手动结束 / 其他看门狗之上的进程)。

    落盘 kill-evidence.log(追加)。任何失败都静默吞掉, 绝不拖垮看门狗。
    """
    app_dir = _app_dir()
    out_path = os.path.join(app_dir, "kill-evidence.log")
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(out_path, "a", encoding="utf-8", errors="replace") as f:
            f.write("\n========== 转发器死亡现场 @ %s ==========\n" % stamp)
            f.write("退出码=0x%08X (%s)  存活 %.0fs\n"
                    % (rc & 0xFFFFFFFF, rc, dt))
            f.write("--- Security 4689(进程终止) 死前 30s ---\n")
            try:
                ps = (
                    "$start=(Get-Date).AddSeconds(-30);"
                    "$evts=Get-WinEvent -FilterHashtable "
                    "@{LogName='Security';Id=4689;StartTime=$start} "
                    "-ErrorAction SilentlyContinue;"
                    "if($evts){foreach($e in $evts){"
                    "$x=[xml]$e.ToXml();"
                    "$pn=($x.Event.EventData.Data|Where-Object{$_.Name-eq "
                    "'ProcessName'}|Select-Object -ExpandProperty '#text');"
                    "$pidn=($x.Event.EventData.Data|Where-Object{$_.Name-eq "
                    "'ProcessId'}|Select-Object -ExpandProperty '#text');"
                    "$subj=($x.Event.EventData.Data|Where-Object{$_.Name-eq "
                    "'SubjectUserName'}|Select-Object -ExpandProperty '#text');"
                    "'{0} PID={1} BY={2} PROC={3}' -f "
                    "$e.TimeCreated,$pidn,$subj,$pn}}"
                    "}else{'  (无 4689 事件)'}"
                )
                for ln in _ps_run(ps).splitlines():
                    f.write("  %s\n" % ln)
            except Exception as e:
                f.write("  (4689 抓取失败: %s)\n" % e)
            f.write("--- 当前运行进程(name x 数量, top40) ---\n")
            try:
                ps2 = (
                    "Get-Process|Group-Object Name|Sort-Object Count "
                    "-Descending|Select-Object -First 40|ForEach-Object{"
                    "'  {0} x{1}' -f $_.Name,$_.Count}"
                )
                for ln in _ps_run(ps2).splitlines():
                    f.write("%s\n" % ln)
            except Exception as e:
                f.write("  (进程清单失败: %s)\n" % e)
            f.write("=============================================\n")
    except Exception:
        pass


def main():
    app_dir = _app_dir()
    if not _single_instance():
        return 0
    passthrough = [a for a in sys.argv[1:] if a not in ("--watchdog",)]
    if getattr(sys, "frozen", False):
        # exe(onedir/onefile)模式: 重启自己这个 exe(去掉 --watchdog 后子进程走正常模式)
        cmd = [sys.executable] + passthrough
        _log("启动(frozen), 监控 %s" % sys.executable)
    else:
        # 脚本模式: 用当前 pythonw 拉起同目录的 hotkey_forwarder.py
        pyw = sys.executable
        target = os.path.join(app_dir, "hotkey_forwarder.py")
        cmd = [pyw, target] + passthrough
        _log("启动, 监控 %s" % target)
    restarts = 0
    quick_deaths = 0
    while True:
        if restarts >= MAX_RESTARTS:
            _log("已达重启上限(%d), 停止。请排查根因(多半是杀软)" % MAX_RESTARTS)
            return 2
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(
                cmd, cwd=app_dir,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            _log("启动转发器失败: %s" % e)
            time.sleep(3)
            restarts += 1
            continue
        rc = p.wait()          # 退出码指纹: 0xC0000005=崩溃, 0=正常退出, 其他=被强杀
        dt = time.monotonic() - t0
        restarts += 1
        _log("转发器已退出: 退出码=0x%08X (%s), 存活 %.0fs"
             % (rc & 0xFFFFFFFF, rc, dt))
        # 抓现行: 仅当看起来像"被杀/崩溃"时采证(长跑后被杀 or 非0/1退出)
        if dt >= MIN_RUN_SECONDS or (rc & 0xFFFFFFFF) not in (0, 1):
            _collect_kill_evidence(rc, dt)
        if dt < MIN_RUN_SECONDS:
            quick_deaths += 1
            _log("转发器 %.1fs 内即退出(可能启动崩溃), 连续 %d/%d"
                 % (dt, quick_deaths, MAX_QUICK_DEATHS))
            if quick_deaths >= MAX_QUICK_DEATHS:
                _log("连续启动崩溃, 停止。请查看 forwarder.log 定位启动错误。")
                return 3
        else:
            quick_deaths = 0
            _log("转发器运行 %.0fs 后被终止, 2 秒后重启(第 %d 次)"
                 % (dt, restarts))
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
