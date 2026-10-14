#!/usr/bin/env python3
"""The measured QA pass for a finished video. A model cannot watch or listen, so it measures.

  python checks.py master.mp4 [--lufs -14] [--tp -1.0] [--write qa.txt]

Checks, each PASS / FAIL / NOTE:
  decode      the file decodes with no errors (resumed downloads and broken muxes fail here)
  durations   video and audio streams end together (within 0.1 s)
  loudness    integrated loudness near the target and true peak at or under the ceiling
  low end     how much of the mix sits under 70 Hz (rumble that turns to mud on a phone)
  tail        the sound in the last 4 s, in 50 ms steps: a drop to digital silence while the picture still runs
              means the mix stopped dead (a fade that never played)
  flashes     full frame light/dark flashes: more than 3 in any 1 s window is a photosensitivity risk
Prints a table and exits 1 when anything fails."""
import argparse
import json
import re
import subprocess
import sys

import numpy as np


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration,width,height,r_frame_rate:format=duration",
             "-of", "json", path])
    return json.loads(r.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--lufs", type=float, default=-14.0)
    ap.add_argument("--tp", type=float, default=-1.0)
    ap.add_argument("--write", default=None)
    a = ap.parse_args()
    rows = []

    def add(name, status, detail):
        rows.append((name, status, detail))

    # decode
    r = run(["ffmpeg", "-v", "error", "-i", a.video, "-f", "null", "-"])
    errs = [ln for ln in r.stderr.splitlines() if ln.strip()]
    add("decode", "PASS" if not errs else "FAIL", "clean" if not errs else f"{len(errs)} errors, first: {errs[0][:90]}")

    # durations
    info = probe(a.video)
    streams = {s["codec_type"]: s for s in info["streams"]}
    v = streams.get("video")
    au = streams.get("audio")
    vd = float(v.get("duration", info["format"]["duration"])) if v else 0.0
    if au:
        ad = float(au.get("duration", info["format"]["duration"]))
        add("durations", "PASS" if abs(vd - ad) <= 0.1 else "FAIL", f"video {vd:.2f} s, audio {ad:.2f} s")
    else:
        add("durations", "NOTE", f"video {vd:.2f} s, no audio stream")

    if au:
        # loudness
        r = run(["ffmpeg", "-hide_banner", "-nostats", "-i", a.video, "-af", "ebur128=peak=true", "-f", "null", "-"])
        tail = r.stderr[r.stderr.rfind("Summary:"):]
        i_m = re.search(r"I:\s*(-?[\d.]+) LUFS", tail)
        p_m = re.search(r"Peak:\s*(-?[\d.]+|-inf) dBFS", tail)
        lufs = float(i_m.group(1)) if i_m else float("nan")
        tp = float(p_m.group(1)) if p_m and p_m.group(1) != "-inf" else -99.0
        ok = abs(lufs - a.lufs) <= 1.0 and tp <= a.tp + 0.05
        add("loudness", "PASS" if ok else "FAIL", f"{lufs:.1f} LUFS (target {a.lufs:g}), true peak {tp:.1f} dBTP (max {a.tp:g})")

        # low end
        def vol(af):
            rr = run(["ffmpeg", "-hide_banner", "-i", a.video, "-vn", "-af", af + "volumedetect", "-f", "null", "-"])
            m = re.search(r"mean_volume:\s*(-?[\d.]+)", rr.stderr)
            return float(m.group(1)) if m else float("nan")
        full, low = vol(""), vol("lowpass=f=70,lowpass=f=70,")
        gap = full - low
        add("low end", "PASS" if gap >= 6 else "NOTE", f"under 70 Hz sits {gap:.1f} dB below the full mix (6+ is clean)")

        # tail
        sr = 8000
        raw = run_bytes(["ffmpeg", "-v", "error", "-sseof", "-4", "-i", a.video, "-vn", "-ac", "1", "-ar", str(sr),
                         "-f", "f32le", "-"])
        x = np.frombuffer(raw, np.float32)
        hop = int(sr * 0.05)
        n = len(x) // hop
        db = 20 * np.log10(np.sqrt((x[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9) if n else np.array([])
        dead = [i for i in range(len(db) - 2) if db[i] < -80]
        if len(db) and dead:
            add("tail", "FAIL", f"silence from {len(db) * 0.05 - dead[0] * 0.05:.2f} s before the end while the picture runs")
        elif len(db):
            add("tail", "PASS", f"last 4 s from {db[0]:.0f} dB to {db[-1]:.0f} dB, no dead stop")

    # flashes
    if v:
        w, h = 64, 36
        raw = run_bytes(["ffmpeg", "-v", "error", "-i", a.video, "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"])
        fr = np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(np.float32) / 255
        num, den = (v.get("r_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den or 1)
        mean = fr.mean((1, 2))
        jumps = np.where(np.abs(np.diff(mean)) > 0.2)[0]
        win = int(round(fps))
        worst = max((int(((jumps >= k) & (jumps < k + win)).sum()) for k in jumps), default=0)
        add("flashes", "PASS" if worst <= 3 else "FAIL", f"{len(jumps)} big light jumps, at most {worst} in any 1 s")

    width = max(len(r[0]) for r in rows)
    lines = [f"{n.ljust(width)}  {s:4s}  {d}" for n, s, d in rows]
    report = "\n".join(lines)
    print(report)
    if a.write:
        open(a.write, "w", encoding="utf-8").write(f"QA for {a.video}\n{report}\n")
    sys.exit(1 if any(s == "FAIL" for _, s, _ in rows) else 0)


def run_bytes(cmd):
    return subprocess.run(cmd, capture_output=True).stdout


if __name__ == "__main__":
    main()
