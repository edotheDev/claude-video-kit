#!/usr/bin/env python3
"""Fetch just the seconds a film needs from each chosen clip, at the source's quality.

ffmpeg seeks over HTTP, so a 3 s piece of a large file costs roughly 3 s of bytes. Each piece is
re-encoded once at a near-lossless CRF so the render reads a small, fast local file.

    python fetch_segments.py picks.json [--out DIR]   # picks: [{"id", "link", "in", "pre", "fetch"}]

Writes DIR/<id>.mp4 (default: clips/ beside picks.json) and prints what it got (resolution, duration). Skips pieces already fetched.
"""
import json
import os
import subprocess
import sys
import time

# Wikimedia asks every client to name itself with a contact URL; ffmpeg's default agent gets throttled (HTTP 429).
# Never put a personal address here: the project URL is the contact.
UA = os.environ.get("MOTION_UA", "BrandMotionKit/1.0 (+https://github.com/edotheDev/claude-video-kit) ffmpeg")



def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate:format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    try:
        j = json.loads(r.stdout)
        s = j["streams"][0]
        d = float(j["format"]["duration"])
        return (s["width"], s["height"], s["r_frame_rate"], d) if d >= 0.3 else None  # a failed pull can leave a valid-looking stub
    except Exception:
        return None


def main():
    picks = json.load(open(sys.argv[1], encoding="utf-8"))
    OUT = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "clips")
    os.makedirs(OUT, exist_ok=True)
    for p in picks:
        dst = os.path.join(OUT, f"{p['id']}.mp4")
        if os.path.exists(dst) and probe(dst):
            print("have", p["id"], probe(dst))
            continue
        start = max(0.0, float(p.get("in", 0.0)) - float(p.get("pre", 0.5)))
        length = float(p.get("fetch", 3.5))
        ok = False
        for attempt in range(4):
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-user_agent", UA, "-reconnect", "1", "-reconnect_streamed", "1",
                                "-reconnect_delay_max", "4", "-ss", f"{start:.3f}", "-i", p["link"], "-t", f"{length:.3f}",
                                "-an", "-c:v", "libx264", "-crf", "14", "-preset", "fast", "-pix_fmt", "yuv420p", dst],
                               capture_output=True, text=True)
            if r.returncode == 0 and probe(dst):
                ok = True
                break
            time.sleep(8 * (attempt + 1))  # back off, a 429 needs time
        print(("got " if ok else "FAILED ") + p["id"], probe(dst) if ok else r.stderr[-300:], flush=True)
        time.sleep(float(p.get("sleep", 2.0)))


if __name__ == "__main__":
    main()
