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

top = "ai-passport-voice-agent-app-0.3.0/"
with zipfile.ZipFile(tree_zip) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
    for info in src.infolist():
        dst.writestr(top + info.filename, src.read(info))
    dst.write(exe, top + "windows/" + exe.name)
tree_zip.unlink()
print("OK:", out)
