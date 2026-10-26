# -*- coding: utf-8 -*-
"""B 机监测器: 每 INTERVAL 秒记录 B 机 CABLE Output(VB-Audio Virtual Cable 采集侧)
是否有实时音, 用于与 A 机 amon_forwarder.py 对称对照, 定位"时灵时不灵"。

对照逻辑(与 A 机 amon_forwarder.py 配合):
  - B 侧 AUDIO=YES  <-> A 确实把 Passport 声推到了 B(链路通)
  - B 侧 AUDIO=NO  且 A 侧 AUDIO=YES -> 音频没过网, 查 B 防火墙/6980 入站
  - 两侧都 YES 但 WorkBuddy 录音框"不响应热键" -> 问题在 WorkBuddy 触发侧, 非音频

依赖: sounddevice(+numpy)。B 机录音用的就是 CABLE Output(VB-Audio Virtual Cable)。

用法:
  python audio_monitor.py --device "CABLE Output" --interval 1 --log audio.log
"""
import argparse
import sys
import time


def main():
    ap = argparse.ArgumentParser(description="B 机监测器(CABLE Output 实时音)")
    ap.add_argument("--device", default="CABLE Output",
                    help="B 机采集设备(默认 CABLE Output, WorkBuddy 录音来源)")
    ap.add_argument("--interval", type=float, default=1.0, help="记录间隔秒")
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

    log(f"audio_monitor 启动: device={args.device} interval={args.interval}s "
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
            audio = "YES" if peak_state["peak"] >= args.threshold else "NO"
            log(f"AUDIO={audio} peak={peak_state['peak']}")
            peak_state["peak"] = 0
    except KeyboardInterrupt:
        log("audio_monitor 停止")
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
