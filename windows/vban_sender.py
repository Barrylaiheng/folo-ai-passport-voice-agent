# -*- coding: utf-8 -*-
"""正确的 VBAN 音频发送端(PCM 16bit), 用于替换有 bug 的第三方 VBANServer.exe。

为什么需要它:
    原 A 机用第三方 VBANServer.exe(Stefano Rampazzo 的 .NET 工具)推流, 其包头
    byte[6](nbc=声道数-1) 被错填为 0xFF(=256 声道), 导致官方 VBAN Receptor 解析
    到非法包头即无容错退出(闪退)。本脚本按 VBAN 协议规范拼包头, 声道字节恒为
    (channels-1), 官方 Receptor 可正常选流。

VBAN 包头(28 字节)布局(与仓库 capture_vban.py / verify_vban.py 解析一致):
    [0:4]   b"VBAN" 魔数
    [4]     sr 字节 = (采样率索引 & 0x1F) | (数据格式 << 5)  格式 0=PCM
    [5]     nbs     = 每帧采样数 - 1   (256 -> 0xFF, 这是正常值!)
    [6]     nbc     = 声道数 - 1       (2 声道 -> 0x01, 旧工具错填 0xFF=BUG)
    [7]     fmt     = (编码 << 4) | (位深格式 & 0x07)  16bit -> 位深=1, 编码=0
    [8:24]  streamname(16 字节, 不足补 \\x00)
    [24:28] framecounter(4 字节小端, 每包 +1)

用法:
    python vban_sender.py --target 192.168.110.209 --port 6980 \\
        --stream Stream1 --samplerate 48000 --channels 2 --device "CABLE Output"
    (--device 为 A 机接收 Passport 声音的虚拟声卡采集端; 可用 --list 查看设备名)
"""
import argparse
import os
import socket
import struct
import sys
import time


def _log_path():
    """日志落到 exe/脚本同目录的 vban_sender.log, 冻结无控制台时也能排查。"""
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "vban_sender.log")


def vlog(msg):
    """诊断日志: 始终追加写 vban_sender.log(同目录, 冻结无控制台时排查必需),
    同时尽量打印到真实控制台(有则打印, 无则静默)。不依赖 sys.stdout 重定向,
    避免 PyInstaller 窗口应用 stdout="黑洞"导致日志丢失。
    """
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass
    try:
        print(str(msg))
    except Exception:
        pass


# 采样率 -> VBAN 采样率索引(VBAN 协议固定表, 与 verify_vban.py 一致)
SR_TABLE = {6000: 0, 8000: 7, 11025: 14, 12000: 1, 16000: 8, 22050: 15,
            24000: 2, 32000: 9, 44100: 16, 48000: 3, 64000: 10, 88200: 17,
            96000: 4, 128000: 11, 176400: 18, 192000: 5, 256000: 12,
            352800: 19, 384000: 6, 512000: 13, 705600: 20}


def build_header(sr_idx, nbs, nbc, streamname, framecounter):
    """拼 28 字节 VBAN 包头。nbc=声道数-1(关键修复点)。"""
    sr_byte = (sr_idx & 0x1F) | (0 << 5)          # 数据格式 0 = PCM
    nbs_byte = (nbs - 1) & 0xFF                   # 每帧采样数 - 1
    nbc_byte = (nbc - 1) & 0xFF                   # 声道数 - 1  (修复: 2声道=0x01)
    fmt_byte = (0 << 4) | (1 & 0x07)              # 编码 0(PCM) | 位深 1(16bit)
    name = streamname.encode("ascii", "ignore")[:16].ljust(16, b"\x00")
    return (b"VBAN" + bytes([sr_byte, nbs_byte, nbc_byte, fmt_byte]) +
            name + struct.pack("<I", framecounter & 0xFFFFFFFF))


def list_devices():
    import sounddevice as sd
    vlog("可用音频输入设备:")
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            vlog(f"  [{i}] {dev['name']}  "
                 f"in={dev['max_input_channels']} sr={dev['default_samplerate']}")


def main():
    ap = argparse.ArgumentParser(description="正确的 VBAN 发送端(PCM16)")
    ap.add_argument("--device", default="CABLE Output",
                    help="采集设备名(部分匹配), 默认 CABLE Output(A 机收 Passport 声)")
    ap.add_argument("--target", default=None,
                    help="接收端 IP(如 192.168.110.209); --list 时可省略")
    ap.add_argument("--port", type=int, default=6980)
    ap.add_argument("--stream", default="Stream1")
    ap.add_argument("--samplerate", type=int, default=48000)
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--nbs", type=int, default=256, help="每包采样帧数(默认 256)")
    ap.add_argument("--list", action="store_true", help="列出音频输入设备后退出")
    args = ap.parse_args()

    import sounddevice as sd

    if args.list:
        list_devices()
        return

    if not args.target:
        vlog("[vban-sender] 缺少 --target (接收端 IP)")
        sys.exit(2)

    # 设备解析: 数字=索引; 字符串=部分匹配, 优先挑 2 声道 / 48000 的 CABLE
    try:
        dev_idx = int(args.device)
    except ValueError:
        matches = [(i, d) for i, d in enumerate(sd.query_devices())
                   if args.device in d["name"] and d["max_input_channels"] > 0]
        if not matches:
            vlog(f"[vban-sender] 找不到匹配 '{args.device}' 的输入设备")
            sys.exit(2)
        pref = [m for m in matches if m[1]["max_input_channels"] == 2
                and int(m[1]["default_samplerate"]) == 48000]
        chosen = pref[0] if pref else matches[0]
        dev_idx = chosen[0]
        vlog(f"[vban-sender] 选定设备 [{dev_idx}] {chosen[1]['name']}")

    if args.samplerate not in SR_TABLE:
        vlog(f"[vban-sender] 不支持的采样率 {args.samplerate}(不在 VBAN 表)")
        sys.exit(2)
    sr_idx = SR_TABLE[args.samplerate]
    nbc, nbs = args.channels, args.nbs
    payload = nbs * nbc * 2                                  # 16bit
    # 打印将要发出的包头(用于人工核对 byte[6]=声道数-1)
    hdr0 = build_header(sr_idx, nbs, nbc, args.stream, 0)
    vlog(f"[vban-sender] 包头首包: {hdr0[:8].hex(' ')} "
         f"(byte5 nbs-1={hdr0[5]:#04x}, byte6 nbc-1={hdr0[6]:#04x} "
         f"应为 {nbc-1:#04x})")
    vlog(f"[vban-sender] 推流 -> {args.target}:{args.port} 流={args.stream} "
         f"{args.samplerate}Hz/{nbc}ch/16bit nbs={nbs} payload={payload}B")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    framecounter = 0

    def cb(indata, frames, time_info, status):
        nonlocal framecounter
        if status:
            vlog(f"[vban-sender] audio status: {status}")
        data = bytes(indata)                                  # int16 交织
        off = 0
        while off + payload <= len(data):
            hdr = build_header(sr_idx, nbs, nbc, args.stream, framecounter)
            sock.sendto(hdr + data[off:off + payload],
                        (args.target, args.port))
            framecounter += 1
            off += payload

    try:
        with sd.RawInputStream(device=dev_idx, samplerate=args.samplerate,
                               channels=nbc, dtype="int16", blocksize=nbs,
                               callback=cb):
            vlog("[vban-sender] 正在推流 (Ctrl+C 停止) ...")
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        vlog("\n[vban-sender] 已停止")
    except Exception as e:
        vlog(f"[vban-sender] 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
