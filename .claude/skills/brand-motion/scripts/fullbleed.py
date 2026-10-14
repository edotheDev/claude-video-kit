#!/usr/bin/env python3
"""
Full-bleed footage layout (brand-agnostic).

Observational footage fills the frame with no container. Each shot opens soft and pulls into focus.
Short lines float in the corners like a poster, a key word can be held in brackets, and textbook
indicators (a ring, a leader, a label) mark a detail. A final shot can turn the picture into grain,
let the grain find a pattern, and end on the logo.

Type, particles, grain, and the score come from motion_engine.py; footage decoding and the grade
from footage.py. Colors and fonts come from brand.json.

  python fullbleed.py board.json --out film.mp4 --size 1080x1350        # 4:5 (default size)
  python fullbleed.py board.json --out preview.mp4 --preview --sheet sheet.png
  python fullbleed.py board.json --out film.mp4 --remux                 # new score, picture untouched
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import motion_engine as sm  # noqa: E402
from footage import Clip, ease, grade  # noqa: E402

# Overlay colors, replaced from brand.json ("overlay") in main().
BONE = (250, 250, 250)      # ink on a dark frame
RICH = (24, 24, 24)         # ink on a light frame
SHADOW = (12, 12, 12)       # halo behind light ink
PAPER = (247, 247, 245)     # halo behind dark ink
GRADE_SHADOW = (24, 24, 24)


def is_dark(ground):
    bg = sm.GROUNDS[ground]["bg"]
    return 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2] < 128


def draw_callout(r, frame, co, ts):
    """Textbook indicator on the full frame: a ring on the detail, a leader, then the label."""
    at = co.get("at", 1.2)
    if ts < at:
        return
    W, H, s = r.W, r.H, r.scale
    p = ease((ts - at) / 0.45)
    tx, ty = co["to"][0] * W, co["to"][1] * H
    ex, ey = co["label_at"][0] * W, co["label_at"][1] * H
    color = BONE if co.get("tone", "light") == "light" else RICH
    x0, y0 = max(0, int(min(tx, ex) - 60 * s)), max(0, int(min(ty, ey) - 60 * s))
    x1, y1 = min(W, int(max(tx, ex) + 60 * s)), min(H, int(max(ty, ey) + 60 * s))
    lay = Image.new("L", (x1 - x0, y1 - y0), 0)
    d = ImageDraw.Draw(lay)
    rr = 13 * s
    d.ellipse([tx - x0 - rr, ty - y0 - rr, tx - x0 + rr, ty - y0 + rr], outline=255, width=max(1, int(2 * s)))
    ang = math.atan2(ey - ty, ex - tx)
    sx_, sy_ = tx + math.cos(ang) * rr, ty + math.sin(ang) * rr
    lx, ly = sx_ + (ex - sx_) * p, sy_ + (ey - sy_) * p
    d.line([(sx_ - x0, sy_ - y0), (lx - x0, ly - y0)], fill=255, width=max(1, int(2 * s)))
    a = (np.asarray(lay, np.float32) / 255.0 * min(1.0, (ts - at) / 0.15))[..., None]
    frame[y0:y1, x0:x1] = frame[y0:y1, x0:x1] * (1 - a) + np.array(color, np.float32) * a
    if p >= 1.0 and co.get("_patch"):
        r.composite(frame, co["_patch"], int((ts - at - 0.45) * 15))


def build(board, r, ground):
    pulse = float(board.get("pulse", 1.0))
    t = 0.0
    scenes = []
    for s in board["scenes"]:
        dur = float(s.get("seconds", s.get("pulses", 3) * pulse))
        s = dict(s, start=t, end=t + dur, dur=dur)
        t += dur
        s.setdefault("settle", 0.25)
        if "pattern" in s:
            s["pattern"] = sm.prepare_pattern(s["pattern"])
        items = []
        texts = list(s.get("texts", []))
        if s.get("time"):  # the only chrome: a small time label, top right
            texts.append({"text": s["time"], "font": "sans_small", "size": 26, "x": 0.917, "y": 0.045,
                          "anchor": "ra", "at": 0.0})
        on_dark = "clip" in s or is_dark(s.get("ground", ground))
        for it in texts:
            color = tuple(it.get("color", BONE if on_dark else RICH))
            halo = SHADOW if color == BONE else PAPER
            for delay, pieces in sm.layout_text(r, it):
                items.append({"patch": r.text_patches(pieces, color, halo), "at": it.get("at", 0.3) + delay})
        if s.get("logo"):
            lg = s["logo"]
            items.append({"patch": r.logo_patch(lg.get("width", 0.5), tuple(lg.get("color", BONE)), lg.get("y", 0.5),
                                                lg.get("src"), lg.get("x")), "at": lg.get("at", 1.2)})
        s["_items"] = items
        co = s.get("callout")
        if co:
            color = BONE if co.get("tone", "light") == "light" else RICH
            anchor = co.get("label_anchor", "lm" if co["label_at"][0] >= co["to"][0] else "rm")
            it = {"text": co["label"], "font": "sans", "size": 40,
                  "x": co["label_at"][0] + (14 if anchor[0] == "l" else -14) / 1080, "y": co["label_at"][1],
                  "anchor": anchor}
            for _, pieces in sm.layout_text(r, it):
                co["_patch"] = r.text_patches(pieces, color, SHADOW if color == BONE else PAPER)
        scenes.append(s)
    return scenes, t, pulse


def main():
    ap = argparse.ArgumentParser(description="Render a full-bleed footage storyboard.")
    ap.add_argument("storyboard")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1080x1350")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--brand", default="", help="path to brand.json (or a folder holding one)")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--remux", action="store_true")
    args = ap.parse_args()

    board = sm.resolve_paths(json.load(open(args.storyboard, encoding="utf-8")),
                             os.path.dirname(os.path.abspath(args.storyboard)))
    W, H = (int(v) for v in args.size.lower().split("x"))
    fps = args.fps
    if args.preview:
        W, H, fps = (W // 2) & ~1, (H // 2) & ~1, 15  # even sizes for H.264
    steps = max(1, round(sm.STEPS_PER_SEC / fps))
    brand = sm.load_brand(sm.find_brand(args.brand, args.storyboard))
    global BONE, RICH, SHADOW, PAPER, GRADE_SHADOW
    ov = brand["overlay"]
    BONE = tuple(ov.get("ink_light", BONE))
    RICH = tuple(ov.get("ink_dark", RICH))
    SHADOW = tuple(ov.get("halo_behind_light_ink", SHADOW))
    PAPER = tuple(ov.get("halo_behind_dark_ink", PAPER))
    GRADE_SHADOW = brand["grade_shadow"]
    r = sm.Renderer(W, H, brand, board.get("render"))
    ground = board.get("ground", brand["default_ground"])
    scenes, total, pulse = build(board, r, ground)
    tail = float(board.get("tail", 0.0))

    audio_path = os.path.splitext(args.out)[0] + "_score.wav"
    lufs = sm.write_score(audio_path, scenes, total + tail, pulse, style=board.get("score", "resonance"),
                          band_px=board.get("band", 9.0))
    print(f"  score: {audio_path} ({lufs} LUFS)")
    if args.remux:
        tmp = os.path.splitext(args.out)[0] + "_remux.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", args.out, "-i", audio_path, "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp], check=True)
        os.replace(tmp, args.out)
        print("remuxed:", args.out)
        return

    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(fps), "-i", "-", "-i", audio_path, "-c:a", "aac", "-b:a", "192k",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-maxrate", "24M", "-bufsize", "48M",
                            "-preset", "medium", "-movflags", "+faststart", "-shortest", args.out], stdin=subprocess.PIPE)
    base = os.path.dirname(os.path.abspath(args.storyboard))
    band_px = board.get("band", 9.0)
    bg = np.ones((H, W, 3), np.float32) * np.array(sm.GROUNDS[ground]["bg"], np.float32)
    sand, last = None, None
    prev_clip = False
    sheet, fi = [], 0
    for s in scenes:
        nf = int(round(s["dur"] * fps))
        clip = None
        if s.get("clip"):
            path = s["clip"] if os.path.isabs(s["clip"]) else os.path.join(base, s["clip"])
            clip = Clip(path, s.get("in", 0.0), s["dur"], fps, s.get("center", [0.5, 0.5]), int(W * 1.06), int(H * 1.06))
        fade_photo = "pattern" in s and not s.get("clip") and prev_clip and last is not None
        if "pattern" in s and (sand is None or s.get("reset", False)):
            n = int(board.get("particles_per_px", 0.14) * W * H)
            sand = sm.Sand(n, r.ax, r.ay, free=board.get("free_grains", 0.03), seed=board.get("seed", 7))
            if last is not None:  # the last shot turns to grain: light becomes grain on the dark ground
                lum = last.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32) / 255.0
                prob = (lum ** 1.6 + 0.01).ravel()
                prob /= prob.sum()
                idx = sand.rng.choice(prob.size, size=n, p=prob)
                py_, px_ = np.divmod(idx, W)
                sand.P[:, 0] = (px_ + sand.rng.random(n) - W / 2) / r.unit
                sand.P[:, 1] = (py_ + sand.rng.random(n) - H / 2) / r.unit
        for i in range(nf):
            ts = i / fps
            if clip is not None:
                img = clip.next()
                zoom = 1.06 - 0.045 * ease(ts / s.get("focus", 0.8)) - 0.015 * ts / s["dur"]
                cw, ch = int(W * zoom), int(H * zoom)
                ox, oy = (clip.w - cw) // 2, (clip.h - ch) // 2
                pim = Image.fromarray(img[oy:oy + ch, ox:ox + cw]).resize((W, H), Image.BILINEAR)
                blur = 24 * r.scale * (1 - ease(ts / s.get("focus", 0.8)))
                blur += 8 * r.scale * max(0.0, (ts - (s["dur"] - 0.2)) / 0.2)  # breathe out before the cut
                if blur > 0.3:
                    pim = pim.filter(sm.ImageFilter.GaussianBlur(blur))
                frame = grade(np.asarray(pim), s.get("warmth", 1.0), GRADE_SHADOW)
                last = frame
            else:
                sm.advance(sand, s, ts, steps, band_px)
                frame = r.sand_frame(sand.P, s.get("ground", ground), s.get("render"), None, bg_img=bg)
                gf = s.get("grain_fade")  # [start, end, keep]: the grain thins once the real mark resolves
                if gf and ts > gf[0]:
                    k = gf[2] + (1 - gf[2]) * (1 - ease((ts - gf[0]) / (gf[1] - gf[0])))
                    frame = bg * (1 - k) + frame * k
                if fade_photo and ts < 0.5:  # the photograph gives way to its own grain
                    a = 1 - ease(ts / 0.5)
                    frame = frame * (1 - a) + last * a
            if s.get("callout"):  # indicators work on footage and on grain
                draw_callout(r, frame, s["callout"], ts)
            for it in s["_items"]:
                if ts >= it["at"]:
                    r.composite(frame, it["patch"], int((ts - it["at"]) * 15))
            out = np.clip(r.add_grain(frame, fi), 0, 255).astype(np.uint8)
            enc.stdin.write(out.tobytes())
            if args.sheet and fi % max(1, fps // 2) == 0:
                sheet.append((f"{fi / fps:.1f}s", Image.fromarray(out)))
            if fi % (fps * 2) == 0:
                print(f"  {fi / fps:5.1f}s / {total:.1f}s", flush=True)
            fi += 1
        if clip is not None:
            clip.close()
        prev_clip = clip is not None
    black = np.zeros((H, W, 3), np.uint8).tobytes()
    for _ in range(int(round(tail * fps))):
        enc.stdin.write(black)
    enc.stdin.close()
    enc.wait()
    if args.sheet and sheet:
        sm.sheet_image(sheet, W, H).save(args.sheet)
    print("done:", args.out, f"{W}x{H} @ {fps}fps, {total:.1f}s")


if __name__ == "__main__":
    main()
