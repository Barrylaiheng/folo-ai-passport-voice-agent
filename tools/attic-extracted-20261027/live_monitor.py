# -*- coding: utf-8 -*-
"""
AI Passport 语音 · 活体监测器
- 启动即自动发现当前 hotkey_forwarder 的 主进程 + 子进程
- 每 5 秒探一次存活；每 60 秒打一条 "still alive" 点（证明监测没断）
- 任一进程消失 → 立即抓 Windows Security 事件 4689（退出状态码），写取证结论
- 用法: pythonw / python live_monitor.py   (后台运行即可)
"""
import subprocess, time, datetime, sys, os, json

LOG = r"C:\Users\Administrator\Desktop\ESP32\folo-ai-passport-voice-app\extracted\windows\live_monitor.log"
POLL = 5.0
TICK = 60.0  # 每 60s 写一次存活点

def ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def log(msg):
    line = f"[{ts()}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

EXE_NAME = "AI-Passport-Voice-v0.3.8.exe"

def discover():
    """返回 [(pid, role), ...] 当前 AI Passport 进程(支持 exe 版与 pythonw 脚本版)，role=main/worker"""
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | "
             "Where-Object { ($_.Name -eq '" + EXE_NAME + "') -or "
             "($_.Name -eq 'pythonw.exe' -and ($_.CommandLine -like '*hotkey_forwarder*')) } | "
             "Select-Object ProcessId,ParentProcessId,Name | ConvertTo-Json"],
            text=True, stderr=subprocess.STDOUT, timeout=30)
    except Exception as e:
        log(f"[discover] 查询失败: {e}")
        return []
    out = out.strip()
    if not out:
        return []
    try:
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
    except Exception:
        return []
    pids = {int(d["ProcessId"]): int(d.get("ParentProcessId", 0)) for d in data}
    # 主进程 = 父不在集合内；其余为 worker
    result = []
    for pid, ppid in pids.items():
        role = "main" if ppid not in pids else "worker"
        result.append((pid, role))
    # 保证 main 在前
    result.sort(key=lambda x: (0 if x[1] == "main" else 1))
    return result

def is_alive(pid):
    try:
        r = subprocess.run(
            ["tasklist", "/fi", f"PID eq {pid}", "/nh"],
            capture_output=True, text=True, timeout=15)
        return str(pid) in r.stdout
    except Exception:
        return False

def capture_4689(pids):
    """抓最近的 4689 事件，匹配给定 pid，返回文本结论"""
    try:
        ps = (
            "Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4689} "
            "-MaxEvents 80 -ErrorAction SilentlyContinue | "
            "ForEach-Object { "
            "  $pidMatch = ($_.Properties[5].Value); "
            "  [PSCustomObject]@{Time=$_.TimeCreated; Pid=$pidMatch; Msg=$_.Message} "
            "}"
        )
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True, stderr=subprocess.STDOUT, timeout=40)
    except Exception as e:
        return f"[4689] 查询失败: {e}"
    lines = out.splitlines()
    found = []
    for ln in lines:
        for p in pids:
            if f"Pid={p}" in ln or f"Process ID:\t\t{p}" in ln or f"Process ID:   {p}" in ln:
                found.append(ln.strip())
    if not found:
        return ("[4689] 未找到匹配 PID 的终止事件 —— 可能进程仍存活，或审计未记录到该次退出。"
                "若已确认消失且无记录，通常意味着它是被父进程树随同回收（如 explorer 注销）而非独立被 Terminate。")
    return "[4689] 命中终止事件:\n" + "\n".join(found[:20])

def main():
    log("=" * 60)
    log("活体监测器启动")
    targets = discover()
    if not targets:
        log("[!] 未发现任何 hotkey_forwarder 进程 —— 监测终止。请先双击启动程序。")
        return
    tracked = {}
    for pid, role in targets:
        tracked[pid] = {"role": role, "alive": True, "dead_at": None}
        log(f"  发现 {role:6s} 进程 PID={pid}")
    log(f"基线时间 {ts()}，开始监测 (POLL={POLL}s)")

    last_tick = time.time()
    while True:
        time.sleep(POLL)
        now = time.time()
        any_dead_event = False
        for pid, info in list(tracked.items()):
            alive = is_alive(pid)
            if alive:
                info["alive"] = True
            else:
                if info["alive"]:  # 刚死
                    info["alive"] = False
                    info["dead_at"] = ts()
                    any_dead_event = True
                    log(f"[DEATH] {info['role']} 进程 PID={pid} 于 {info['dead_at']} 消失！")
                    log(capture_4689([pid]))
        # 全部死光 → 出总结并结束
        if all(not info["alive"] for info in tracked.values()):
            alive_count = sum(1 for i in tracked.values() if i["alive"])
            if alive_count == 0:
                log("[SUMMARY] 所有监控进程均已退出。")
                # 区分: main 先死还是 worker 先死
                main_dead = tracked.get([p for p,i in tracked.items() if i['role']=='main'][0], {})
                log(f"  main 死亡时间: {main_dead.get('dead_at')}")
                dead_workers = [f"PID={p}@{i['dead_at']}" for p,i in tracked.items() if i['role']=='worker']
                if dead_workers:
                    log("  worker 死亡: " + ", ".join(dead_workers))
                log("监测结束。")
                return
        # 存活打点
        if now - last_tick >= TICK:
            last_tick = now
            up = int(now - start_wall)
            alive_list = ", ".join(f"{i['role']}={p}({'活' if i['alive'] else '死'})"
                                   for p, i in tracked.items())
            log(f"[tick +{up}s] 存活中: {alive_list}")

if __name__ == "__main__":
    start_wall = time.time()
    try:
        main()
    except KeyboardInterrupt:
        log("监测被手动中断。")
    except Exception as e:
        log(f"[FATAL] 监测器异常: {e}")
        raise
