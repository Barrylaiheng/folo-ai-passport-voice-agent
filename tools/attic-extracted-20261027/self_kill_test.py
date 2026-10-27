# -*- coding: utf-8 -*-
"""复现实验: 模拟'双击 exe 两次', 验证第二个实例是否用 TerminateProcess 无声杀掉第一个实例。

仅用于本地取证, 不改业务逻辑。实验结束后自动清理本实验产生的实例。
"""
import subprocess, os, time, sys, ctypes, base64

BASE = r"C:\Users\Administrator\Desktop\ESP32\folo-ai-passport-voice-app\extracted\windows"
PYW = os.path.join(BASE, ".venv", "Scripts", "pythonw.exe")
LOG = os.path.join(BASE, "forwarder.log")
CNW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
kernel32 = ctypes.windll.kernel32

L = []  # 收集日志行, 末尾写证明文件


def say(msg):
    print(msg)
    L.append(msg)


def launch(tag):
    p = subprocess.Popen([PYW, "-u", "hotkey_forwarder.py"], cwd=BASE,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=CNW)
    say("[%s] 启动 pythonw PID=%d @ %s" % (tag, p.pid, _ts()))
    return p


def _ts():
    import datetime as _dt
    return _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def alive(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        kernel32.CloseHandle(h)
        return True
    return False


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def wait_bind(pid, timeout=12):
    start = log_size()
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not alive(pid):
            return False, "进程在绑定前就退出了"
        try:
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                f.seek(start)
                data = f.read()
        except OSError:
            data = ""
        if "已监听 0.0.0.0:33333" in data:
            return True, "已绑定"
        time.sleep(0.3)
    return False, "等待绑定超时"


def tail_log(start, needles):
    try:
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            f.seek(start)
            data = f.read()
    except OSError:
        return ""
    return "\n".join(ln for ln in data.splitlines()
                     if any(n in ln for n in needles))


def query_4689(pid):
    ps = (
        "$pid=%d;" % pid +
        "$evts=Get-WinEvent -FilterHashtable @{LogName='Security';Id=4689} "
        "-MaxEvents 80 -ErrorAction SilentlyContinue; "
        "if($evts){$hit=$false;foreach($e in $evts){"
        "$x=[xml]$e.ToXml();"
        "$pp=($x.Event.EventData.Data|Where-Object{$_.Name-eq 'ProcessId'}|Select-Object -ExpandProperty '#text');"
        "if([string]$pp -eq [string]$pid){$hit=$true;"
        "$pn=($x.Event.EventData.Data|Where-Object{$_.Name-eq 'ProcessName'}|Select-Object -ExpandProperty '#text');"
        "$subj=($x.Event.EventData.Data|Where-Object{$_.Name-eq 'SubjectUserName'}|Select-Object -ExpandProperty '#text');"
        "'  {0} 被终止 PID={1} BY={2} PROC={3}' -f $e.TimeCreated,$pp,$subj,$pn"
        "}}if(-not $hit){'(无 PID=%d 的 4689 终止事件)' -f $pid}}"
        "}else{'(无 4689 事件: 本机可能未开启进程终止审计)'}" % pid
    )
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-EncodedCommand",
                            base64.b64encode(ps.encode("utf-16-le")).decode("ascii")],
                           capture_output=True, text=True, timeout=40,
                           creationflags=CNW)
        return (r.stdout or "").strip()
    except Exception as e:
        return "(4689 查询失败: %s)" % e


def kill(pid):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                       capture_output=True, text=True, timeout=10,
                       creationflags=CNW)
    except Exception:
        pass


def main():
    say("=== AI Passport 双击自启动 自我查杀 复现实验 ===")
    say("启动命令(等同桌面'AI Passport 语音.lnk'): %s -u hotkey_forwarder.py" % PYW)
    say("工作目录: %s" % BASE)
    size0 = log_size()

    A = launch("A(第一次双击)")
    ok, why = wait_bind(A.pid)
    say("[A] 绑定结果: %s (%s) @ %s" % (ok, why, _ts()))
    if not ok:
        say("[!] A 未能绑定, 实验中止")
        if alive(A.pid):
            kill(A.pid)
            say("  已清理 A PID=%d" % A.pid)
        return

    B = launch("B(第二次双击)")
    time.sleep(0.5)
    sizeB = log_size()
    say("[B] 启动 PID=%d; 开始监测 A 是否存活..." % B.pid)

    t0 = time.time()
    killed_at = None
    while time.time() - t0 < 20:
        if not alive(A.pid):
            killed_at = time.time()
            say("[A] PID=%d 已消失 @ %s  (从 B 启动起 ~%.2fs)" %
                (A.pid, _ts(), killed_at - (t0)))
            break
        time.sleep(0.3)
    if killed_at is None:
        say("[A] PID=%d 在 20s 内仍然存活 —— 未触发自查杀" % A.pid)

    say("")
    say("--- B 的 guard / 端口清理日志(自 B 启动起) ---")
    guard = tail_log(sizeB, ["[guard]", "绑定失败", "已清理残留", "33333"])
    say(guard if guard else "(未找到 guard 日志)")

    say("")
    say("--- Windows Security 4689 进程终止取证 (被杀者 PID=%d) ---" % A.pid)
    say(query_4689(A.pid))

    say("")
    say("--- 清理本实验产生的实例 ---")
    for pid in (A.pid, B.pid):
        if alive(pid):
            kill(pid)
            say("  已结束 PID=%d" % pid)
        else:
            say("  PID=%d 已不在(无需清理)" % pid)

    say("")
    say("=== 实验结束 ===")

    proof = os.path.join(BASE, "self_kill_proof_2026-10-24.txt")
    try:
        with open(proof, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
        say("证明文件已写入: %s" % proof)
    except Exception as e:
        say("(写证明文件失败: %s)" % e)


if __name__ == "__main__":
    main()
