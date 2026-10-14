#!/usr/bin/env python3
"""Check that this machine can run the kit:  python tools/check_setup.py

Required: ffmpeg and ffprobe (with libass for captions), Python packages from requirements.txt, Node 20+.
Optional: the caption scripts' npm install, yt-dlp, Demucs, the rubberband filter, a filled in brand/brand.json."""
import importlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rows = []


def add(name, ok, detail, required=True):
    rows.append((name, "ok" if ok else ("MISSING" if required else "optional"), detail))


def out(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.stdout + r.stderr
    except Exception as e:  # noqa: BLE001
        return f"ERROR {e}"


for tool in ("ffmpeg", "ffprobe"):
    path = shutil.which(tool)
    add(tool, bool(path), out([tool, "-version"]).splitlines()[0][:70] if path else "install ffmpeg (see SETUP.md)")
if shutil.which("ffmpeg"):
    filters = out(["ffmpeg", "-hide_banner", "-filters"])
    add("ffmpeg libass", " ass " in filters, "needed to burn captions" if " ass " not in filters else "ass filter present")
    add("ffmpeg rubberband", "rubberband" in filters, "speed a voice with pitch kept (atempo works too)", False)

for mod, pkg in (("numpy", "numpy"), ("PIL", "pillow"), ("cv2", "opencv-python-headless"),
                 ("uharfbuzz", "uharfbuzz"), ("fontTools", "fonttools")):
    try:
        m = importlib.import_module(mod)
        add(f"python {pkg}", True, getattr(m, "__version__", "installed"))
    except ImportError:
        add(f"python {pkg}", False, "pip install -r requirements.txt")
for mod in ("soundfile", "librosa"):
    try:
        importlib.import_module(mod)
        add(f"python {mod}", True, "installed", False)
    except ImportError:
        add(f"python {mod}", False, "only for music-and-mix/footcheck.py", False)

node = shutil.which("node")
ver = out(["node", "--version"]).strip() if node else ""
major = int(ver.lstrip("v").split(".")[0]) if ver.startswith("v") else 0
add("node 20+", major >= 20, ver or "install Node.js 20 or newer")
nm = os.path.join(ROOT, ".claude", "skills", "captions", "scripts", "node_modules", "@huggingface", "transformers")
add("captions npm install", os.path.isdir(nm), "cd .claude/skills/captions/scripts && npm install", False)

add("yt-dlp", bool(shutil.which("yt-dlp")), "fetch sections of online video", False)
add("demucs", bool(shutil.which("demucs")), "vocal checks and stem splits (own venv recommended)", False)

brand = os.path.join(ROOT, "brand", "brand.json")
if os.path.exists(brand):
    cfg = json.load(open(brand, encoding="utf-8"))
    base = os.path.dirname(brand)
    missing = [p for p in list((cfg.get("fonts") or {}).values()) + [cfg.get("logo")]
               if p and not os.path.exists(p if os.path.isabs(p) else os.path.join(base, p))]
    add("brand/brand.json", not missing, "all files found" if not missing else f"{len(missing)} files not found: {missing[:3]}", False)
else:
    add("brand/brand.json", False, "copy brand/brand.example.json and fill it in (system fonts are used until then)", False)

w = max(len(r[0]) for r in rows)
for name, status, detail in rows:
    print(f"{name.ljust(w)}  {status:8s}  {detail}")
sys.exit(1 if any(s == "MISSING" for _, s, _ in rows) else 0)
