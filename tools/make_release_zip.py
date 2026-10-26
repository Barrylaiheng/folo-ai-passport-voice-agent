# Build the release zip: git-archive tree + freshly built EXE.
# Usage: python make_release_zip.py <repo> <exe_path> <out_zip>
import subprocess, sys, zipfile
from pathlib import Path

repo, exe, out = (Path(sys.argv[i]).resolve() for i in (1, 2, 3))
tree_zip = out.with_suffix(".tree.zip")
subprocess.run(
    ["git", "-C", str(repo), "archive", "--format=zip", "-o", str(tree_zip), "HEAD"],
    check=True,
)

top = "AI-Passport-Voice-v0.3.8/"
with zipfile.ZipFile(tree_zip) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
    written = set()
    for info in src.infolist():
        dst.writestr(top + info.filename, src.read(info))
        written.add(top + info.filename)
    dst.write(exe, top + "windows/" + exe.name)
    # 托盘图标随包: 缺它托盘就退化成系统默认白框(2026-10-26 用户反馈)
    # git 树已提交 ico 时不重复写(避免 zip 内同名双条目)
    ico = repo / "windows" / "passport-voice.ico"
    ico_name = top + "windows/passport-voice.ico"
    if ico.is_file() and ico_name not in written:
        dst.write(str(ico), ico_name)
        print("已写入托盘图标 passport-voice.ico")
    elif ico_name in written:
        print("托盘图标已随 git 树打进 zip, 不重复写")
    # 出厂默认配置: 把 example 复制为 voice-config.json 一起发布, 保证开箱即用(零配置)
    example_cfg = repo / "windows" / "voice-config.example.json"
    if example_cfg.is_file():
        dst.write(str(example_cfg), top + "windows/voice-config.json")
        print("已写入出厂默认 voice-config.json (来自 example)")
tree_zip.unlink()
print("OK:", out)
