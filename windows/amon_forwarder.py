# -*- coding: utf-8 -*-
"""A 机监测器: 每 INTERVAL 秒记录两件事, 用于定位两机语音链路的"时灵时不灵"。

  1) FORWARDER : A 机转发器进程是否在 (AI-Passport 的 exe 或 python hotkey_forwarder)
  2) AUDIO     : A 机采集端(CABLE 的采集侧, 默认 "CABLE Output") 此刻有无实时音

对照逻辑(与 B 机 audio_monitor.py 对称):
  - 若本侧 AUDIO=YES 但 B 侧 AUDIO=NO  -> 音频没过网, 查网络/防火墙/6980
  - 若本侧 FORWARDER=DOWN             -> A 转发器掉了, 音频源头断了
  - 两侧 AUDIO 都 YES                 -> 链路全通, "不响应"必是 WorkBuddy 触发侧问题

依赖: sounddevice(+numpy)。A 机若有多块 CABLE, 用 --device 指定正确那块
      (2 声道 / 48000 的那块, 见 vban_sender.py 的设备选择说明)。

用法:
  python amon_forwarder.py --device "CABLE Output" --interval 2 --log amon.log
"""
import argparse
import subprocess
import sys
import time


def forwarder_alive():
    """A 机转发器进程是否在跑(兼容打包 exe 与源码 python 两种形态)。"""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq AI-Passport-Voice-v0.3.8.exe"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or ""
        if "AI-Passport" in out:
            return True
        out2 = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or ""
        return "hotkey_forwarder" in out2
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="A 机监测器(转发器存活 + CABLE 实时音)")
    ap.add_argument("--device", default="CABLE Output",
                    help="A 机采集设备(默认 CABLE Output, 即 Passport 声出现的那侧)")
    ap.add_argument("--interval", type=float, default=2.0, help="记录间隔秒")
    ap.add_argument("--log", default=None, help="日志文件路径(不填只打控制台)")
    ap.add_argument("--threshold", type=int, default=200,
                    help="判定'有音'的 int16 峰值阈值(默认 200)")
    args = ap.parse_args()
    import numpy as np
    import sounddevice as sd

    logf = open(args.log, "a", encoding="utf-8") if args.log else None

    def log(line):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        msg = f"[{ts}] {line}"
        print(msg)
        if logf:
            logf.write(msg + "\n")
            logf.flush()

    log(f"amon_forwarder 启动: device={args.device} interval={args.interval}s "
        f"threshold={args.threshold}")

    peak_state = {"peak": 0}

    def audio_cb(indata, frames, t, status):
        if status:
            return
        peak_state["peak"] = int(np.max(np.abs(indata)))

    try:
        stream = sd.InputStream(device=args.device, samplerate=48000,
                                channels=2, dtype="int16", blocksize=0,
                                callback=audio_cb)
        stream.start()
    except Exception as e:
        log(f"音频设备打开失败: {e} (用 --device 指定正确设备)")
        if logf:
            logf.close()
        sys.exit(1)

    try:
        while True:
            time.sleep(args.interval)
            alive = "UP" if forwarder_alive() else "DOWN"
            audio = "YES" if peak_state["peak"] >= args.threshold else "NO"
            log(f"FORWARDER={alive} AUDIO={audio} peak={peak_state['peak']}")
            peak_state["peak"] = 0
    except KeyboardInterrupt:
        log("amon_forwarder 停止")
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
        if logf:
            logf.close()


if __name__ == "__main__":
    main()
