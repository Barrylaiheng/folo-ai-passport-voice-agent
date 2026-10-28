# 构建发布 zip: 干净最小包(不含整源码树)。
# 只含: 构建好的 exe + 出厂默认 voice-config.json + 托盘图标 + 简短 README。
# 顶层目录与 exe 名自动取自传入的 exe 路径, 不再硬编码版本。
# Usage: python make_release_zip.py <repo> <exe_path> <out_zip>
import sys, zipfile
from pathlib import Path

repo, exe, out = (Path(sys.argv[i]).resolve() for i in (1, 2, 3))
top = exe.stem + "/"  # e.g. AI-Passport-Voice-v0.4.0/
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
    # 1) 主程序(与配置/图标同目录, tray_icon/配置按 exe 目录加载)
    dst.write(exe, top + exe.name)
    # 2) 出厂默认配置: example 复制为 voice-config.json, 开箱即用零配置
    example_cfg = repo / "windows" / "voice-config.example.json"
    if example_cfg.is_file():
        dst.write(example_cfg, top + "voice-config.json")
        print("已写入出厂默认 voice-config.json")
    # 3) 托盘图标(缺它托盘退化成系统默认白框)
    ico = repo / "windows" / "passport-voice.ico"
    if ico.is_file():
        dst.write(ico, top + "passport-voice.ico")
        print("已写入托盘图标 passport-voice.ico")
    # 4) 简短发布说明(替代整源码树)
    readme = repo / "windows" / "RELEASE_README.txt"
    if readme.is_file():
        dst.write(readme, top + "README.txt")
        print("已写入 README.txt")
    # 5) PyInstaller onedir 运行时: 整个 _internal 目录(python311.dll + 全部 .pyd/依赖)。
    #    onedir 模式下 exe 只是引导器, 真解释器与依赖全在 _internal; 漏打会导致
    #    "Failed to load Python DLL ... _internal\python311.dll 找不到指定的模块"。
    #    (2026-10-28 事故: 上一版只打 4 文件, 用户下载解压后双击直接报此错)
    internal = exe.parent / "_internal"
    if internal.is_dir():
        n = 0
        for p in internal.rglob("*"):
            if p.is_file():
                arc = top + p.relative_to(exe.parent).as_posix()
                dst.write(p, arc)
                n += 1
        print(f"已写入 _internal 运行时({n} 个文件)")
    else:
        print("⚠️ 未找到 _internal 目录, 请先构建 onedir 产物再打 zip")
print("OK:", out)
