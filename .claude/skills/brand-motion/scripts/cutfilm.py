#!/usr/bin/env python3
"""cutfilm: a voiceover-led fast-cut film, the "launch film" grammar, in the brand's own language.

The voice drives everything. A builder script turns word timestamps and the music's beat grid into a
timeline JSON; this engine renders it. Every shot plays a TAKE: a clip already cut, cropped (4:3 or any
ratio), speed-changed and conformed to the film's frame rate by `prep` (see conform_takes), so the render
only ever reads frames forward.

Timeline keys (all times in film seconds):
  size [W, H], fps, duration, brand (path), seed
  shots        [{t0, t1, take | gen, z:[z0,z1], pan:[[x,y],[x,y]], fx:[...], grade:{...}, step, loop,
                 freeze, track:{tracks, spec}, dip}]        gen: ink | paper | field
  cuts         [{t, type: glitch|flash|whip|zoom|luma|fieldx|dissolve, dur, ...}]
  captions     [{words:[[text, t_in, t_out, flags]], t_out, y, size, max_w, color}]
  big          [{text | runs, t0, t1, role, axes, px, x, y, style, anim, ...}]
               style: fill | knockout | typewriter | ghost | echo | highlight
  graphics     [{type: hud_label | counter | network | graph | wave | swatches | label | ascii_logo, ...}]
  hud          {text, context:[[t, value]], margin}
  finish       {grain, scan, vignette, chroma}
  audio        {vo, vo_at, music, music_in, music_at, fade_out, duck_db, lufs}

FX on a shot (applied in order): pointcloud, edges, trail, slitscan, rgb, smear, flow, dither, ascii,
bw-free grade. They are the TouchDesigner vocabulary: dots from luminance, glowing contours, feedback,
time displacement, channel split, pixel streaks, motion vectors, 1-bit dither, a letter mosaic.

  python cutfilm.py timeline.json --out film.mp4            # master
  python cutfilm.py timeline.json --preview --out p.mp4       # half size, 15 fps
  python cutfilm.py timeline.json --times 1.2,5.5 --out s.png # a sheet of stills
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import motion_engine as sm  # noqa: E402
import typeset  # noqa: E402
from overlay import TrackOverlay  # noqa: E402

cv2.setNumThreads(2)


# ---------------------------------------------------------------- small helpers
def ss(a, b, x):
    if b == a:
        return 1.0 if x >= b else 0.0
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def ease_out(u):
    u = min(1.0, max(0.0, u))
    return 1 - (1 - u) ** 3


def lerp(a, b, u):
    return a + (b - a) * u


def keyframes(kf, t, default=0.0):
    """[[t, v], ...] piecewise smooth."""
    if not kf:
        return default
    if t <= kf[0][0]:
        return kf[0][1]
    for (t0, v0), (t1, v1) in zip(kf, kf[1:]):
        if t <= t1:
            return lerp(v0, v1, ss(t0, t1, t))
    return kf[-1][1]


def lum(img):
    return img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722


def over(dst, rgb, a):
    """dst float HxWx3, rgb (3,) or HxWx3, a HxW in 0..1"""
    a3 = a[..., None]
    dst *= (1 - a3)
    dst += np.asarray(rgb, np.float32) * a3
    return dst


# ---------------------------------------------------------------- takes
class Take:
    """Reads a conformed take forward; skips ahead by decoding, reopens only to go back."""

    def __init__(self, path, fps):
        self.path, self.fps = path, fps
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,nb_frames", "-of", "json", path], capture_output=True, text=True)
        st = json.loads(p.stdout)["streams"][0]
        self.w, self.h = int(st["width"]), int(st["height"])
        self.n = int(st.get("nb_frames") or 0)
        self.proc, self.k, self.last = None, -1, None

    def _open(self, k):
        self.close()
        self.proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{k / self.fps:.4f}", "-i", self.path,
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.k = k - 1

    def frame(self, k):
        k = max(0, k if not self.n else min(k, self.n - 1))
        if k == self.k and self.last is not None:
            return self.last
        if self.proc is None or k < self.k or k > self.k + 90:
            self._open(k)
        size = self.w * self.h * 3
        while self.k < k:
            buf = self.proc.stdout.read(size)
            if len(buf) < size:
                break
            self.k += 1
            if self.k == k:
                self.last = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)
        if self.last is None:
            self.last = np.zeros((self.h, self.w, 3), np.uint8)
        return self.last

    def close(self):
        if self.proc is not None:
            try:
                self.proc.stdout.close()
                self.proc.kill()
                self.proc.wait(timeout=2)
            except Exception:
                pass
        self.proc = None


def detect_box(path, t_in=0.0, dur=None, samples=10, shrink=0.035, left_extra=0.02):
    """The picture area inside a film scan's gate: black borders, sprocket holes and rounded corners are
    cut away. Returns [x0, y0, x1, y1] as fractions of the source frame."""
    p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height",
                                   "-select_streams", "v:0", "-of", "json", path], capture_output=True, text=True).stdout)
    total = float(p["format"]["duration"])
    w, h = 384, max(2, int(round(384 * p["streams"][0]["height"] / p["streams"][0]["width"] / 2)) * 2)
    a = t_in
    b = min(total, t_in + (dur or total)) if dur else total
    frames = []
    for k in range(samples):
        t = a + (b - a) * (k + 0.5) / samples
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={w}:{h}",
                              "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        if len(raw) == w * h:
            frames.append(np.frombuffer(raw, np.uint8).astype(np.float32))
    if not frames:
        return [0.0, 0.0, 1.0, 1.0]
    M = np.median(np.stack(frames), axis=0).reshape(h, w)
    col, row = np.median(M, axis=0), np.median(M, axis=1)
    def extent(v):
        ref = np.percentile(v, 75)
        on = np.where(v > max(14.0, 0.22 * ref))[0]
        return (on[0], on[-1] + 1) if len(on) else (0, len(v))
    x0, x1 = extent(col)
    y0, y1 = extent(row)
    fx0, fx1, fy0, fy1 = x0 / w, x1 / w, y0 / h, y1 / h
    return [round(fx0 + shrink + left_extra, 4), round(fy0 + shrink, 4), round(fx1 - shrink, 4), round(fy1 - shrink, 4)]


def probe_sar(path):
    """The source's sample aspect ratio as a float, or None when it is square or unset."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=sample_aspect_ratio",
                        "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    try:
        a, b = r.split(":")
        v = float(a) / float(b)
        return None if v <= 0 or abs(v - 1.0) < 0.01 else v
    except (ValueError, ZeroDivisionError):
        return None


def conform_takes(takes, base, W, H, fps, jobs=2):
    """takes: {name: {src, in, dur, speed, cx, cy, m, reverse, box, size, dar, deint}} -> base/<name>.mp4 at (W*m, H*m).
    box: [x0, y0, x1, y1] fractions of the source to keep before framing, or "auto" for film scans (detect_box).
    size: [w, h] conforms at (w*m, h*m) instead, e.g. a 4:3 take for an `inset` shot in a 9:16 film.
    Non-square pixels (DVD transfers: 720x480 at 8:9, 352x480 at 20:11) are made square first, from the stream's
    sample aspect ratio; `dar` (e.g. 4/3) forces the display aspect when a transfer has no SAR flag. Before 25 Sep
    2026 an anamorphic source was framed on its stored size (a 720x480 4:3 film came out 12.5% wide)."""
    import concurrent.futures as cf
    os.makedirs(base, exist_ok=True)
    for name, t in takes.items():
        if t.get("box") == "auto":
            t["box"] = detect_box(t["src"], float(t["in"]), float(t["dur"]) * float(t.get("speed", 1.0)))

    def one(name, t):
        out = os.path.join(base, name + ".mp4")
        if t.get("dar"):
            square = f"scale=trunc(ih*{float(t['dar']):.6f}/2)*2:ih,setsar=1"
        else:
            sar = probe_sar(t["src"])
            square = f"scale=trunc(iw*{sar:.6f}/2)*2:ih,setsar=1" if sar else ""
        sig = json.dumps({k: v for k, v in t.items() if k != "force"}, sort_keys=True) + f"|{W}x{H}@{fps}"
        if square:
            sig += "|" + square
        side = out + ".take"
        if (os.path.exists(out) and os.path.exists(side) and open(side).read() == sig
                and os.path.getmtime(out) > os.path.getmtime(t["src"]) and not t.get("force")):
            return name, "kept"
        m = float(t.get("m", 1.12))
        tw, th = t.get("size") or (W, H)
        cw, ch = int(round(tw * m / 2)) * 2, int(round(th * m / 2)) * 2
        sp = float(t.get("speed", 1.0))
        dur = float(t["dur"]) + 0.4
        cx, cy = float(t.get("cx", 0.5)), float(t.get("cy", 0.5))
        vf = [f"setpts=(PTS-STARTPTS)/{sp}", f"fps={fps}"]
        if t.get("deint"):   # interlaced DVD transfers (720x480 archive films)
            vf.insert(0, "yadif")
        if square:
            vf.append(square)
        if t.get("box"):
            x0, y0, x1, y1 = t["box"]
            vf.append(f"crop=iw*{x1 - x0:.4f}:ih*{y1 - y0:.4f}:iw*{x0:.4f}:ih*{y0:.4f}")
        vf += [f"scale={cw}:{ch}:force_original_aspect_ratio=increase:flags=lanczos",
              f"crop={cw}:{ch}:(in_w-{cw})*{cx}:(in_h-{ch})*{cy}"]
        if t.get("hflip"):
            vf.append("hflip")
        if t.get("reverse"):
            vf.append("reverse")
        vf.append("format=yuv420p")
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{float(t['in']):.3f}", "-t", f"{dur * sp:.3f}", "-i", t["src"],
               "-vf", ",".join(vf), "-an", "-c:v", "libx264", "-crf", "13", "-preset", "veryfast", "-g", "15", out]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            open(side, "w").write(sig)
        return name, ("ok" if r.returncode == 0 else "FAILED " + r.stderr[-300:])

    with cf.ThreadPoolExecutor(jobs) as ex:
        for name, res in ex.map(lambda kv: one(*kv), takes.items()):
            print(f"  take {name}: {res}", flush=True)


# ---------------------------------------------------------------- letter mosaic (brand letter field)
BLOCK_RE = None


def _blocked():
    global BLOCK_RE
    if BLOCK_RE is None:
        try:
            import fieldfilm
            BLOCK_RE = fieldfilm.BLOCKED
        except Exception:
            BLOCK_RE = re.compile(r"(?!)")
    return BLOCK_RE


class Mosaic:
    """Luminance -> glyphs from the brand alphabet (letters, digits, symbols; never a dash)."""

    def __init__(self, font_path, cell, ratio, alphabet, axes=None, seed=7):
        self.cw = int(cell)
        self.ch = int(round(cell * ratio))
        f = typeset.pil_font(font_path, int(self.ch * 0.78), axes)
        glyphs, cover = [], []
        for c in alphabet:
            im = Image.new("L", (self.cw, self.ch), 0)
            d = ImageDraw.Draw(im)
            d.text((self.cw / 2, self.ch * 0.52), c, font=f, fill=255, anchor="mm")
            a = np.asarray(im, np.float32) / 255.0
            glyphs.append(a)
            cover.append(a.mean())
        order = np.argsort(cover)
        self.chars = [alphabet[i] for i in order]
        self.atlas = np.stack([glyphs[i] for i in order])  # dim to dense
        self.n = len(self.chars)
        self.rng = np.random.default_rng(seed)

    def render(self, L, W, H, t, swap=0.2, gamma=0.9):
        """L: HxW luminance 0..1 at output size. Returns coverage HxW."""
        gw, gh = W // self.cw, H // self.ch
        small = cv2.resize(L, (gw, gh), interpolation=cv2.INTER_AREA)
        v = np.clip(small, 0, 1) ** gamma
        idx = np.clip((v * (self.n - 1) + (self.rng.random((gh, gw)) - 0.5) * swap * self.n * 0.25), 0, self.n - 1).astype(int)
        idx = self.clean(idx)
        tiles = self.atlas[idx] * np.clip(v * 1.6 + 0.15, 0, 1)[..., None, None]
        cov = tiles.transpose(0, 2, 1, 3).reshape(gh * self.ch, gw * self.cw)
        out = np.zeros((H, W), np.float32)
        oy, ox = (H - cov.shape[0]) // 2, (W - cov.shape[1]) // 2
        out[oy:oy + cov.shape[0], ox:ox + cov.shape[1]] = cov
        return out

    def clean(self, idx):
        rx = _blocked()
        for r in range(idx.shape[0]):
            row = "".join(self.chars[i] for i in idx[r])
            for m in rx.finditer(row):
                for j in range(m.start(), m.end()):
                    if self.chars[idx[r, j]].isalpha():
                        idx[r, j] = self.n // 2 if not self.chars[self.n // 2].isalpha() else 0
        return idx


# ---------------------------------------------------------------- the film
class Film:
    def __init__(self, tl, W, H, fps, base):
        self.tl, self.W, self.H, self.fps, self.base = tl, W, H, fps, base
        self.scale = W / float(tl["size"][0])
        self.brand = sm.load_brand(tl["brand"])
        self.cfg = self.brand["cfg"]
        g = self.cfg["grounds"]
        self.INK = np.array(g["ink"]["bg"], np.float32)
        self.PAPER = np.array(g["paper"]["bg"], np.float32)
        self.fonts = self.brand["fonts"]
        self.axes = self.brand.get("font_axes") or {}
        self.mono = self.fonts["mono"]
        self.takes = {}
        self.cache = {}
        self.state = {}
        self.rng = np.random.default_rng(int(tl.get("seed", 4)))
        lf = self.cfg.get("letter_field", {})
        self.mosaic = Mosaic(self.fonts[lf.get("font", "sans")], max(8, int(round(16 * self.scale))),
                             float(lf.get("cell_ratio", 1.18)), lf.get("alphabet", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
                             lf.get("axes"))
        self.shots = sorted(tl["shots"], key=lambda s: s["t0"])
        self.cuts = sorted(tl.get("cuts", []), key=lambda c: c["t"])
        self.tracks = {}
        self.wave = None
        a = tl.get("audio") or {}
        if a.get("vo"):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-i", self.path(a["vo"]), "-ac", "1", "-ar", "8000",
                                  "-f", "f32le", "-"], capture_output=True).stdout
            self.wave = (np.frombuffer(raw, np.float32), 8000, float(a.get("vo_at", 0.0)))
        self.logo = None
        if self.brand.get("logo"):
            self.logo = Image.open(self.brand["logo"]).convert("RGBA")

    # ---- paths, fonts, cached type
    def path(self, p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(self.base, p))

    def font(self, role, px):
        key = ("f", role, int(px))
        if key not in self.cache:
            self.cache[key] = ImageFont.truetype(self.fonts[role] if role in self.fonts else role, max(6, int(px)))
        return self.cache[key]

    def line(self, text, role, px, axes=None, tracking=0.0, runs=None, word_spacing=0.0):
        key = ("l", text, role, round(px, 1), json.dumps(axes, sort_keys=True), tracking,
               json.dumps(runs, sort_keys=True), word_spacing)
        if key not in self.cache:
            if runs:
                rr = [(r["text"], self.fonts[r.get("role", role)], r.get("axes", self.axes.get(r.get("role", role))),
                       float(r.get("scale", 1.0))) for r in runs]
                ln = typeset.set_runs(rr, px, tracking, word_spacing)
            else:
                ln = typeset.set_line(text, self.fonts[role], px, axes if axes is not None else self.axes.get(role),
                                      tracking, word_spacing=word_spacing)
            self.cache[key] = ln
        return self.cache[key]

    def take(self, name, owner=None):
        """One reader per (take, user), so a transition reading two shots never thrashes one pipe."""
        key = (name, owner)
        if key not in self.takes:
            self.takes[key] = Take(self.path(name), self.fps)
        return self.takes[key]

    def release_takes(self, t):
        """Close readers of shots that are over, so an 8 GB machine never holds forty ffmpeg pipes."""
        for key, tk in list(self.takes.items()):
            owner = key[1]
            if isinstance(owner, int) and owner < len(self.shots) and self.shots[owner]["t1"] + 0.5 < t:
                tk.close()
                del self.takes[key]

    # ---- which shot is live
    def shot_at(self, t):
        cur = None
        for s in self.shots:
            if s["t0"] <= t + 1e-6:
                cur = s
            else:
                break
        return cur

    def next_shot(self, s):
        i = self.shots.index(s)
        return self.shots[i + 1] if i + 1 < len(self.shots) else None

    # ---- one shot's picture at time t (may be outside [t0, t1] for transitions)
    def shot_frame(self, s, t):
        W, H = self.W, self.H
        u = (t - s["t0"]) / max(1e-6, s["t1"] - s["t0"])
        gen = s.get("gen")
        if gen == "ink" or gen is None and not s.get("take"):
            img = np.empty((H, W, 3), np.float32)
            img[:] = self.INK
        elif gen == "paper":
            img = np.empty((H, W, 3), np.float32)
            img[:] = self.PAPER
        else:
            tk = self.take(s["take"], self.shots.index(s))
            ts = t - s["t0"]
            if s.get("freeze") is not None:
                ts = min(ts, float(s["freeze"]))
            if s.get("loop"):
                a, b = s["loop"]
                if ts > b:
                    ts = a + (ts - a) % max(1e-3, b - a)
            k = int(math.floor(max(0.0, ts) * self.fps + 1e-6))
            if s.get("step"):
                k -= k % int(s["step"])
            k += int(round(float(s.get("k0", 0))))
            src = tk.frame(k)
            z0, z1 = s.get("z", [1.0, 1.06])
            z = lerp(z0, z1, ease_out(min(1, max(0, u))) if s.get("zease", "out") == "out" else min(1, max(0, u)))
            (px0, py0), (px1, py1) = s.get("pan", [[0, 0], [0, 0]])
            px, py = lerp(px0, px1, u), lerp(py0, py1, u)
            cw, ch = src.shape[1] / z, src.shape[0] / z
            cx = src.shape[1] / 2 + px * src.shape[1] * 0.5
            cy = src.shape[0] / 2 + py * src.shape[0] * 0.5
            x0 = int(round(min(max(0, cx - cw / 2), src.shape[1] - cw)))
            y0 = int(round(min(max(0, cy - ch / 2), src.shape[0] - ch)))
            crop = src[y0:y0 + int(round(ch)), x0:x0 + int(round(cw))]
            if s.get("inset"):
                img = self.inset_frame(s, t, crop)
            else:
                # how the tracking overlay maps onto this crop: X = X_full * z - x0 * W / cw
                self.state[(id(s), "xform")] = (src.shape[1] / cw, -x0 * W / cw, src.shape[0] / ch, -y0 * H / ch)
                interp = cv2.INTER_AREA if crop.shape[1] > W else cv2.INTER_LINEAR
                img = cv2.resize(crop, (W, H), interpolation=interp).astype(np.float32)
                img = self.grade(img, s.get("grade"))
        if not (s.get("inset") and s.get("take") and gen not in ("ink", "paper")):
            for fx in s.get("fx", []):
                img = self.apply_fx(fx, img, s, t)
        d = s.get("dip")
        if d:
            img *= 1 - float(keyframes(d, t) if isinstance(d, list) else d)
        return img

    def grade(self, img, g):
        g = dict(self.tl.get("grade") or {}, **(g or {}))
        sat, con = float(g.get("sat", 1.0)), float(g.get("con", 1.06))
        lift, gain = float(g.get("lift", 0.0)), float(g.get("gain", 1.0))
        if sat != 1.0:
            L = lum(img)[..., None]
            img = L + (img - L) * sat
        if con != 1.0:
            img = (img - 128.0) * con + 128.0
        if gain != 1.0:
            img *= gain
        warm = float(g.get("warm", 0.0))  # a film-print tilt: reds up, blues down, so mixed stocks sit together
        if warm:
            img[..., 0] *= 1 + warm
            img[..., 1] *= 1 + warm * 0.25
            img[..., 2] *= 1 - warm
        if lift:
            img = img * (1 - lift) + self.INK * lift
        return np.clip(img, 0, 255)

    def inset_frame(self, s, t, crop):
        """A tall frame from a wide or low resolution take (archive film is 4:3 and SD; a 9:16 crop of it would be a
        fourfold upscale): the sharp picture sits in a window across the frame, and the same picture continues above
        and below it as the brand's letter field.
        Shot key `inset`: {y: window centre (fraction of H, 0.5), x: 0.5, w: window width (fraction of W, 1.0),
        field: brightness of the letter field (0.5), swap: letter churn (0.3), holes: [[x0, y0, x1, y1], ...] frame
        fractions where the field clears (a label), feather: px (24)}. The window keeps the take's own aspect (conform
        it with a take `size`, e.g. [1080, 810]); the shot's fx run on the window alone (not flow or track, which draw
        on the whole frame)."""
        W, H = self.W, self.H
        ins = s["inset"] if isinstance(s["inset"], dict) else {}
        iw = int(round(W * float(ins.get("w", 1.0)) / 2)) * 2
        ih = min(H, int(round(iw * crop.shape[0] / crop.shape[1] / 2)) * 2)
        interp = cv2.INTER_AREA if crop.shape[1] > iw else cv2.INTER_LINEAR
        pic = self.grade(cv2.resize(crop, (iw, ih), interpolation=interp).astype(np.float32), s.get("grade"))
        # the letter field: the graded picture filled to the frame (soft is fine at the size of a letter cell)
        sc = max(W / iw, H / ih)
        fw, fh = int(math.ceil(iw * sc)), int(math.ceil(ih * sc))
        big = cv2.resize(pic, (fw, fh), interpolation=cv2.INTER_LINEAR)
        oy, ox = (fh - H) // 2, (fw - W) // 2
        big = big[oy:oy + H, ox:ox + W]
        cov = self.mosaic.render(lum(big) / 255.0, W, H, t, swap=float(ins.get("swap", 0.3)))
        if ins.get("holes"):
            key = ("insethole", json.dumps(ins["holes"]), float(ins.get("feather", 24)), W, H)
            if key not in self.cache:
                m = np.ones((H, W), np.float32)
                for x0f, y0f, x1f, y1f in ins["holes"]:
                    m[int(y0f * H):int(y1f * H), int(x0f * W):int(x1f * W)] = 0.0
                f = float(ins.get("feather", 24)) * self.scale
                self.cache[key] = cv2.GaussianBlur(m, (0, 0), f / 2) if f >= 1 else m
            cov = cov * self.cache[key]
        img = np.empty((H, W, 3), np.float32)
        img[:] = self.INK
        glow = np.clip(cv2.GaussianBlur(big, (0, 0), 1.0) * 1.3, 0, 255)
        img = img * (1 - cov[..., None]) + glow * cov[..., None]
        img = self.INK + (img - self.INK) * float(ins.get("field", 0.5))
        # the window: the shot's fx on the picture alone, at the window's own size
        W0, H0 = self.W, self.H
        self.W, self.H = iw, ih
        try:
            for fx in s.get("fx", []):
                pic = self.apply_fx(fx, pic, s, t)
        finally:
            self.W, self.H = W0, H0
        ix0 = min(max(0, int(round(float(ins.get("x", 0.5)) * W - iw / 2))), W - iw)
        iy0 = min(max(0, int(round(float(ins.get("y", 0.5)) * H - ih / 2))), H - ih)
        img[iy0:iy0 + ih, ix0:ix0 + iw] = pic
        return img

    # ---- FX
    def apply_fx(self, fx, img, s, t):
        fx = {k: (keyframes(v, t) if k in ("mix", "dim") and isinstance(v, list) else v) for k, v in fx.items()}
        kind = fx["type"]
        on = fx.get("on")
        if on and not (on[0] <= t < on[1]):
            return img
        W, H = self.W, self.H
        key = (id(s), kind)
        if kind == "pointcloud":
            gx = int(fx.get("grid", 150))
            gy = int(round(gx * H / W))
            small = cv2.resize(img, (gx, gy), interpolation=cv2.INTER_AREA)
            L = lum(small) / 255.0
            ys, xs = np.mgrid[0:gy, 0:gx].astype(np.float32)
            X = (xs - gx / 2 + 0.5) * (W / gx) * float(fx.get("spread", 1.0))
            Y = (ys - gy / 2 + 0.5) * (H / gy) * float(fx.get("spread", 1.0))
            Z = (L - 0.5) * float(fx.get("depth", 260)) * self.scale
            a = lerp(*fx.get("rot", [-0.25, 0.25]), ss(s["t0"], s["t1"], t))
            b = float(fx.get("tilt", 0.18))
            X1 = X * math.cos(a) + Z * math.sin(a)
            Z1 = -X * math.sin(a) + Z * math.cos(a)
            Y1 = Y * math.cos(b) - Z1 * math.sin(b)
            Z2 = Y * math.sin(b) + Z1 * math.cos(b)
            f = 1500 * self.scale
            sc = f / (f + Z2)
            px = (W / 2 + X1 * sc).astype(int)
            py = (H / 2 + Y1 * sc).astype(int)
            keep = (L > float(fx.get("floor", 0.07))) & (px >= 0) & (px < W) & (py >= 0) & (py < H)
            col = small / 255.0 if fx.get("color", "src") == "src" else np.ones_like(small)
            inten = (L ** 1.1)[..., None] * col
            acc = np.zeros((H, W, 3), np.float32)
            np.add.at(acc, (py[keep], px[keep]), inten[keep])
            r = max(1, int(round(float(fx.get("dot", 1.4)) * self.scale)))
            acc = cv2.dilate(acc, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
            glow = cv2.GaussianBlur(acc, (0, 0), 3 * self.scale)
            out = np.empty_like(img)
            out[:] = self.INK
            out += (acc * 255 * float(fx.get("gain", 1.25)) + glow * 255 * 0.5)
            mix = float(fx.get("mix", 1.0))
            return np.clip(img * (1 - mix) + out * mix, 0, 255)
        if kind == "edges":
            half = cv2.resize(img, (W // 2, H // 2), interpolation=cv2.INTER_AREA).astype(np.uint8)
            gray = cv2.cvtColor(half, cv2.COLOR_RGB2GRAY)
            gray = cv2.GaussianBlur(gray, (0, 0), 1.2)
            e = cv2.Canny(gray, int(fx.get("lo", 40)), int(fx.get("hi", 110))).astype(np.float32) / 255.0
            e = cv2.resize(e, (W, H), interpolation=cv2.INTER_LINEAR)
            glow = cv2.GaussianBlur(e, (0, 0), 4 * self.scale)
            col = np.array(fx.get("color", self.PAPER), np.float32)
            dim = float(fx.get("dim", 0.35))
            out = img * dim
            a = np.clip(e * 1.3 + glow * 0.9, 0, 1)
            return over(out, col, a * float(fx.get("mix", 1.0)))
        if kind == "trail":
            st = self.state.get(key)
            if st is None or st[0] > t:
                acc = img.copy()
            else:
                acc = st[1]
                zoom = float(fx.get("zoom", 1.018))
                M = cv2.getRotationMatrix2D((W / 2, H / 2), float(fx.get("rotate", 0.0)), zoom)
                M[0, 2] += float(fx.get("dx", 0.0)) * self.scale
                acc = cv2.warpAffine(acc, M, (W, H), borderMode=cv2.BORDER_REPLICATE)
                acc = np.maximum(img, acc * float(fx.get("decay", 0.86)))
            self.state[key] = (t, acc)
            return acc
        if kind == "slitscan":
            buf = self.state.setdefault(key, [])
            buf.append(img.astype(np.uint8))
            n = int(fx.get("frames", 14))
            del buf[:-n]
            if len(buf) < 2:
                return img
            m = len(buf)
            rows = (np.arange(H) * (m - 1) / (H - 1)).astype(int) if fx.get("dir", "v") == "v" else None
            stack = np.stack(buf)
            if rows is not None:
                out = stack[m - 1 - rows, np.arange(H)]
            else:
                cols = (np.arange(W) * (m - 1) / (W - 1)).astype(int)
                out = stack[m - 1 - cols[None, :].repeat(H, 0), np.arange(H)[:, None], np.arange(W)[None, :]]
            return out.astype(np.float32)
        if kind == "rgb":
            dpx = float(fx.get("px", 6)) * self.scale
            j = float(fx.get("jitter", 0.0))
            if j:
                dpx *= 0.4 + 0.6 * self.rng.random() * j
            dx = int(round(dpx))
            out = img.copy()
            out[..., 0] = np.roll(img[..., 0], dx, axis=1)
            out[..., 2] = np.roll(img[..., 2], -dx, axis=1)
            return out
        if kind == "smear":
            L = lum(img)
            m = (L > float(fx.get("thr", 170))).astype(np.float32)
            bright = img * m[..., None]
            k = max(3, int(float(fx.get("len", 60)) * self.scale)) | 1
            ker = np.full((1, k), 1.0 / k, np.float32)  # a motion streak, not a block
            st = cv2.filter2D(bright, -1, ker) * float(fx.get("gain", 2.6))
            return np.clip(img + st * 0.55, 0, 255)
        if kind == "flow":
            return img  # drawn as an overlay (needs the previous frame), see draw_flow
        if kind == "dither":
            L = lum(img) / 255.0
            bayer = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32) / 16.0
            cell = max(1, int(round(float(fx.get("cell", 3)) * self.scale)))
            small = cv2.resize(L, (W // cell, H // cell), interpolation=cv2.INTER_AREA)
            th = np.tile(bayer, (small.shape[0] // 4 + 1, small.shape[1] // 4 + 1))[:small.shape[0], :small.shape[1]]
            bit = (small > th).astype(np.float32)
            bit = cv2.resize(bit, (W, H), interpolation=cv2.INTER_NEAREST)
            out = np.empty_like(img)
            out[:] = self.INK
            out = over(out, self.PAPER, bit * 0.92)
            mix = float(fx.get("mix", 1.0))
            return img * (1 - mix) + out * mix
        if kind == "ascii":
            L = lum(img) / 255.0
            cov = self.mosaic.render(L, W, H, t, swap=float(fx.get("swap", 0.25)))
            out = np.empty_like(img)
            out[:] = self.INK
            col = img if fx.get("color", "src") == "src" else self.PAPER
            if isinstance(col, np.ndarray) and col.ndim == 3:
                glow = cv2.GaussianBlur(col, (0, 0), 1.0) * 1.3
                out = out * (1 - cov[..., None]) + np.clip(glow, 0, 255) * cov[..., None]
            else:
                out = over(out, col, cov)
            mix = float(fx.get("mix", 1.0))
            return img * (1 - mix) + out * mix
        return img

    # ---- transitions
    def transition(self, cut, t, A, B, fa, fb):
        """fa, fb: callables returning the outgoing/incoming shot's frame at time t."""
        kind, tc, d = cut["type"], cut["t"], float(cut.get("dur", 0.1))
        W, H = self.W, self.H
        u = (t - (tc - d / 2)) / d
        if kind == "glitch":
            base = fb() if t >= tc else fa()
            other = fa() if t >= tc else fb()
            rng = np.random.default_rng(int(t * 1000) + 17)
            out = base.copy()
            nb = int(cut.get("bands", 9))
            ys = np.sort(rng.integers(0, H, nb * 2))
            for i in range(0, len(ys) - 1, 2):
                y0, y1 = ys[i], min(H, ys[i] + max(4, int((ys[i + 1] - ys[i]) * 0.6)))
                src = other if rng.random() < 0.45 else base
                dx = int(rng.normal(0, 0.09 * W))
                band = np.roll(src[y0:y1], dx, axis=1)
                if rng.random() < 0.5:
                    ch = rng.integers(0, 3)
                    band = band.copy()
                    band[..., ch] = np.roll(band[..., ch], int(rng.normal(0, 18 * self.scale)), axis=1)
                out[y0:y1] = band
            for _ in range(int(cut.get("blocks", 3))):
                bw, bh = int(rng.uniform(0.08, 0.35) * W), int(rng.uniform(0.04, 0.2) * H)
                bx, by = int(rng.uniform(0, W - bw)), int(rng.uniform(0, H - bh))
                src = other if rng.random() < 0.5 else base
                sx = int(np.clip(bx + rng.normal(0, 0.2 * W), 0, W - bw))
                out[by:by + bh, bx:bx + bw] = src[by:by + bh, sx:sx + bw]
            return out
        if kind == "flash":
            base = fb() if t >= tc else fa()
            col = self.PAPER if cut.get("color", "paper") == "paper" else self.INK
            a = 1 - ss(tc, tc + d, t) if t >= tc else ss(tc - float(cut.get("pre", 0.03)), tc, t)
            return base * (1 - a) + col * a
        if kind == "whip":
            base = fb() if t >= tc else fa()
            k = abs(u - 0.5) * 2
            amt = (1 - k) * float(cut.get("px", 180)) * self.scale
            n = max(3, int(amt / 6) | 1)
            if amt < 2:
                return base
            ker = np.zeros((1, n), np.float32)
            ker[0, :] = 1.0 / n
            out = cv2.filter2D(base, -1, ker)
            sh = int((0.5 - u) * float(cut.get("shift", 60)) * self.scale * (1 if cut.get("dir", 1) > 0 else -1))
            return np.roll(out, sh, axis=1)
        if kind == "zoom":
            base = fb() if t >= tc else fa()
            k = 1 - abs(u - 0.5) * 2
            s = 1 + 0.25 * k
            M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s)
            z = cv2.warpAffine(base, M, (W, H), borderMode=cv2.BORDER_REFLECT)
            acc = z.copy()
            for i in range(1, 5):
                M = cv2.getRotationMatrix2D((W / 2, H / 2), 0, s * (1 + 0.025 * i * k))
                acc += cv2.warpAffine(base, M, (W, H), borderMode=cv2.BORDER_REFLECT)
            return acc / 5
        if kind == "luma":
            a, b = fa(), fb()
            L = lum(a) / 255.0
            thr = 1.05 - 1.2 * ss(tc - d / 2, tc + d / 2, t)
            m = np.clip((L - thr) * 6, 0, 1)
            return a * (1 - m[..., None]) + b * m[..., None]
        if kind == "dissolve":
            a, b = fa(), fb()
            w = ss(tc - d / 2, tc + d / 2, t)
            return a * (1 - w) + b * w
        if kind == "fieldx":
            a, b = fa(), fb()
            w = ss(tc - d / 2, tc + d / 2, t)
            src = a if t < tc else b
            L = lum(src) / 255.0
            cov = self.mosaic.render(L, W, H, t, swap=0.6)
            field = np.empty_like(a)
            field[:] = self.INK
            field = over(field, self.PAPER, cov)
            k = 1 - abs(w - 0.5) * 2
            base = a if t < tc else b
            return base * (1 - k) + field * k
        return fb() if t >= tc else fa()

    # ---- the picture under everything
    def picture(self, t):
        s = self.shot_at(t)
        if s is None:
            img = np.empty((self.H, self.W, 3), np.float32)
            img[:] = self.INK
            return img, None
        for c in self.cuts:
            d = float(c.get("dur", 0.1))
            lo, hi = c["t"] - d / 2 - float(c.get("pre", 0)), c["t"] + d / 2
            if lo <= t < hi and c["type"] != "cut":
                A = self.shot_at(c["t"] - 1e-3)
                B = self.shot_at(c["t"] + 1e-3)
                if A is None or B is None:
                    break
                return self.transition(c, t, A, B, lambda: self.shot_frame(A, t), lambda: self.shot_frame(B, t)), s
        return self.shot_frame(s, t), s

    # ---- overlays drawn in PIL (lines, boxes, labels)
    def overlay_layer(self, t, s):
        W, H = self.W, self.H
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        drew = False
        if s is not None and s.get("track"):
            tr = s["track"]
            key = tr["tracks"]
            if key not in self.tracks:
                self.tracks[key] = TrackOverlay(self.path(key), tr.get("spec", {}), W, H, self.scale, self.mono, self.fps)
            ov = self.tracks[key]
            ov.xform = self.state.get((id(s), "xform"))
            ts = t - s["t0"]
            k = int(ts * self.fps) + int(round(float(s.get("k0", 0))))
            col = tr.get("color", self.PAPER.tolist())
            rel = tr.get("release")
            ov.draw(layer, ts, k, col, float(tr.get("alpha", 1.0)), (rel[0] - s["t0"], rel[1]) if rel else None)
            self.draw_tags(layer, ov, ts, k, tr, col)
            drew = True
        if s is not None and s.get("lock"):
            self.draw_lock(layer, s, t)
            drew = True
        if s is not None:
            for fx in s.get("fx", []):
                if fx["type"] == "flow" and (not fx.get("on") or fx["on"][0] <= t < fx["on"][1]):
                    self.draw_flow(layer, s, t, fx)
                    drew = True
        for g in self.tl.get("graphics", []):
            if g["t0"] - 0.001 <= t < g["t1"]:
                getattr(self, "g_" + g["type"])(layer, g, t)
                drew = True
        return layer if drew else None

    def draw_tags(self, layer, ov, ts, k, tr, col):
        tags = tr.get("tags")
        if not tags:
            return
        d = ImageDraw.Draw(layer)
        f = self.font("mono", 18 * self.scale)
        fr = ov.points(k)
        n = int(tags.get("count", 6))
        W, H = self.W, self.H
        if ov.spec.get("style") == "target" and ov.target:
            kk = min(max(0, k), len(ov.target) - 1)
            x, y = ov.target[kk]
            xf = getattr(ov, "xform", None)
            if xf:
                x, y = xf[0] * x + xf[1], xf[2] * y + xf[3]
            pts = [(-1, x + 92 * self.scale * 0.72, y + 92 * self.scale * 0.72)]
            ov.size.setdefault(-1, 8 * self.scale)
            ov.delay.setdefault(-1, 0.15)
        else:
            inside = [p for p in fr if 0.08 * W < p[1] < 0.9 * W and 0.12 * H < p[2] < 0.7 * H]
            pts = sorted(inside, key=lambda p: -ov.size.get(p[0], 0))[:n]
        text = tags["text"]
        sw = tags.get("switch")
        for i, x, y in pts:
            if ts < ov.delay.get(i, 0) + 0.12:
                continue
            txt = text
            if sw and ts >= sw[0]:
                txt = sw[1]
            r = ov.size[i] / 2
            a = int(220 * float(tr.get("alpha", 1.0)))
            tw = d.textlength(txt, font=f)
            pad = 3 * self.scale
            if tags.get("filled") and sw and ts >= sw[0]:
                d.rectangle([x - r, y + r + 2 * self.scale, x - r + tw + 2 * pad, y + r + 20 * self.scale],
                            fill=tuple(int(c) for c in col) + (a,))
                d.text((x - r + pad, y + r + 17 * self.scale), txt, font=f, fill=tuple(int(c) for c in self.INK) + (255,), anchor="ls")
            else:
                d.text((x - r, y + r + 16 * self.scale), txt, font=f, fill=tuple(int(c) for c in col) + (a,), anchor="ls")

    def lock_box(self, s, t):
        """The locked object's box at time t in output pixels (cx, cy, w, h), or None where it is not seen."""
        lk = s["lock"]
        W, H = self.W, self.H
        ts = t - s["t0"]
        if lk.get("keys"):
            ks = lk["keys"]
            if ts < ks[0][0] - 1e-6 or ts > ks[-1][0] + 1e-6:
                return None
            v = [keyframes([[k[0], k[j]] for k in ks], ts) if lk.get("smooth") else None for j in range(1, 5)]
            if v[0] is None:   # straight between keys: an object in flight does not ease
                for (a, *p), (b, *q) in zip(ks, ks[1:]):
                    if ts <= b:
                        u = (ts - a) / max(1e-6, b - a)
                        v = [lerp(p[j], q[j], u) for j in range(4)]
                        break
                else:
                    v = ks[-1][1:5]
            return v[0] * W, v[1] * H, v[2] * W, v[3] * H
        key = ("lockpath", lk["path"])
        if key not in self.cache:
            self.cache[key] = json.load(open(self.path(lk["path"])))
        P = self.cache[key]
        k = int(math.floor(max(0.0, ts) * self.fps + 1e-6)) + int(round(float(s.get("k0", 0))))
        if s.get("step"):
            k -= k % int(s["step"])
        boxes = P["boxes"]
        if not boxes:
            return None
        b = boxes[min(max(0, k), len(boxes) - 1)]
        if b is None:
            return None
        sx, sy = W / float(P["w"]), H / float(P["h"])
        x0, y0, x1, y1 = b[0] * sx, b[1] * sy, b[2] * sx, b[3] * sy
        xf = self.state.get((id(s), "xform"))
        if xf:
            x0, x1 = xf[0] * x0 + xf[1], xf[0] * x1 + xf[1]
            y0, y1 = xf[2] * y0 + xf[3], xf[2] * y1 + xf[3]
        return (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0

    def draw_lock(self, layer, s, t):
        """A machine-vision lock on ONE object: corner brackets that snap
        onto the object after the cut and follow it, a typed id and label above, and the object's trail. With
        `from_prev` the box slides over from where the previous shot's lock let go, so one lock runs through a chain
        of cuts (a stone that becomes a spear, an arrow, a rocket). Shot key `lock`: {path: a lock_track.py file
        (per-frame boxes in take pixels) | keys: [[ts, cx, cy, w, h], ...] (shot seconds, frame fractions), label,
        id (1), delay (0.04), snap (0.14 s), from_prev (false), trail (true), pad (1.15), min (36 px at 1440),
        coords (false), alpha (1), color}."""
        lk = s["lock"]
        W, H = self.W, self.H
        ts = t - s["t0"]
        tl = ts - float(lk.get("delay", 0.04))
        box = self.lock_box(s, t)
        if box is None or tl < 0:
            return
        cx, cy, bw, bh = box
        pad = float(lk.get("pad", 1.15))
        mn = float(lk.get("min", 36)) * self.scale
        bw, bh = max(mn, bw * pad), max(mn, bh * pad)
        snap = float(lk.get("snap", 0.14))
        u = ease_out(tl / snap) if snap > 0 else 1.0
        prev = self.state.get("lock_last")
        if lk.get("from_prev") and prev and 0 <= s["t0"] - prev[4] < 0.2 and u < 1:
            # slide in from the last lock: the same object, carried across the cut
            cx, cy = lerp(prev[0], cx, u), lerp(prev[1], cy, u)
            bw, bh = lerp(prev[2], bw, u), lerp(prev[3], bh, u)
        else:
            sc = 1 + 0.9 * (1 - u)
            bw, bh = bw * sc, bh * sc
        self.state["lock_last"] = (cx, cy, bw, bh, t)
        a = ss(0.0, 0.04, tl) * float(lk.get("alpha", 1.0))
        if 0.06 < tl < 0.1:
            a *= 0.3   # one blink as it locks
        if a <= 0.01:
            return
        d = ImageDraw.Draw(layer)
        col = tuple(int(c) for c in (lk.get("color") or self.PAPER))
        A = int(245 * a)
        w = max(1, int(round(2 * self.scale)))
        x0, y0, x1, y1 = cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2
        L = min(bw, bh) * 0.3
        for px_, py_, sx_, sy_ in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
            d.line([(px_, py_), (px_ + sx_ * L, py_)], fill=col + (A,), width=w)
            d.line([(px_, py_), (px_, py_ + sy_ * L)], fill=col + (A,), width=w)
        c = 5 * self.scale
        d.line([(cx - c, cy), (cx + c, cy)], fill=col + (int(A * 0.8),), width=w)
        d.line([(cx, cy - c), (cx, cy + c)], fill=col + (int(A * 0.8),), width=w)
        # the trail: where the object has been in this shot
        if lk.get("trail", True):
            key = (id(s), "locktrail")
            tr = self.state.get(key)
            if tr is None or tr[-1][0] > t:
                tr = []
            if not tr or t - tr[-1][0] > 0.5 / self.fps:
                tr.append((t, cx, cy))
            self.state[key] = tr[-24:]
            r = 2.2 * self.scale
            n = len(tr)
            for i, (_, px_, py_) in enumerate(tr[:-1]):
                if i % 2:
                    continue
                aa = int(A * 0.55 * (i + 1) / n)
                d.rectangle([px_ - r, py_ - r, px_ + r, py_ + r], fill=col + (aa,))
        f = self.font("mono", float(lk.get("px", 19)) * self.scale)
        txt = f"{int(lk.get('id', 1)):02d} {lk.get('label', '')}".rstrip()
        n = int(len(txt) * ss(0.02, 0.02 + len(txt) / 55.0, tl) + 0.999)
        if n:
            ty = y0 - 9 * self.scale
            lx = min(max(8 * self.scale, x0), W - d.textlength(txt, font=f) - 8 * self.scale)
            if ty < 30 * self.scale:   # no room above: the label goes under the box
                ty = y1 + 24 * self.scale
            if lk.get("plate"):   # a dark plate under the label on a bright sky
                p = 4 * self.scale
                asc = f.getmetrics()[0]
                d.rectangle([lx - p, ty - asc - p, lx + d.textlength(txt[:n], font=f) + p, ty + p * 1.2],
                            fill=tuple(int(c) for c in self.INK) + (int(255 * float(lk["plate"]) * a),))
            d.text((lx, ty), txt[:n], font=f, fill=col + (A,), anchor="ls")
        if lk.get("coords"):
            fc = self.font("mono", 15 * self.scale)
            d.text((x1 + 8 * self.scale, y1), f"{cx / W:.3f} {cy / H:.3f}", font=fc, fill=col + (int(A * 0.65),), anchor="ls")

    def draw_flow(self, layer, s, t, fx):
        W, H = self.W, self.H
        key = (id(s), "flowprev")
        cur = self.shot_frame_small(s, t)
        prev = self.state.get(key)
        self.state[key] = cur
        if prev is None or prev.shape != cur.shape:
            return
        flow = cv2.calcOpticalFlowFarneback(prev, cur, None, 0.5, 2, 13, 2, 5, 1.1, 0)
        d = ImageDraw.Draw(layer)
        step = int(fx.get("step", 36))
        sx, sy = W / cur.shape[1], H / cur.shape[0]
        col = tuple(int(c) for c in fx.get("color", self.PAPER)) + (int(200 * float(fx.get("alpha", 1.0))),)
        gain = float(fx.get("gain", 5.0))
        w = max(1, int(round(1.4 * self.scale)))
        for y in range(step // 2, cur.shape[0], max(2, int(step / sy))):
            for x in range(step // 2, cur.shape[1], max(2, int(step / sx))):
                fx_, fy_ = flow[y, x]
                m = math.hypot(fx_, fy_)
                if m < 0.35:
                    continue
                X, Y = x * sx, y * sy
                d.line([(X, Y), (X + fx_ * sx * gain, Y + fy_ * sy * gain)], fill=col, width=w)
                d.rectangle([X - w, Y - w, X + w, Y + w], fill=col)

    def shot_frame_small(self, s, t):
        tk = self.take(s["take"], ("small", self.shots.index(s)))
        k = int(max(0.0, t - s["t0"]) * self.fps) + int(round(float(s.get("k0", 0))))
        f = tk.frame(k)
        g = cv2.cvtColor(cv2.resize(f, (self.W // 4, self.H // 4), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
        return g

    # ---- graphics
    def g_label(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        a = ss(g["t0"], g["t0"] + 0.08, t) * (1 - ss(g["t1"] - 0.08, g["t1"], t))
        if g.get("blink") and int(t * 4) % 2:
            a *= 0.35
        f = self.font(g.get("role", "mono"), float(g.get("px", 18)) * self.scale)
        col = tuple(int(c) for c in (g.get("color") or self.PAPER)) + (int(235 * a),)
        txt = g["text"]
        if g.get("type_on"):
            n = int(len(txt) * ss(g["t0"], g["t0"] + float(g["type_on"]), t) + 0.999)
            txt = txt[:n]
        d.text((g["x"] * self.W, g["y"] * self.H), txt, font=f, fill=col, anchor=g.get("anchor", "ls"))

    def g_counter(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        u = ss(g.get("t_roll", g["t0"]), g.get("t_end", g["t1"]), t)   # t_roll: shown from t0, counting from t_roll
        v = lerp(float(g["from"]), float(g["to"]), u ** float(g.get("pow", 1.0)))
        txt = g.get("fmt", "{:,.0f}").format(v)
        a = ss(g["t0"], g["t0"] + 0.06, t) * (1 - ss(g["t1"] - 0.06, g["t1"], t)) * float(g.get("alpha", 1.0))
        f = self.font(g.get("role", "serif"), float(g["px"]) * self.scale)
        col = tuple(int(c) for c in (g.get("color") or self.PAPER)) + (int(255 * a),)
        d.text((g["x"] * self.W, g["y"] * self.H), txt, font=f, fill=col, anchor=g.get("anchor", "mm"))

    @staticmethod
    def year_text(v):
        y = int(round(v))
        if y < 0:
            return f"{-y:,} BC"
        return str(max(1, y))

    def g_yearclock(self, layer, g, t):
        """A year that holds, then rolls onto the next one. keys [[t, year], ...] with
        BC years negative: the number holds on each year and rolls into the next over the `roll` seconds before its
        time, easing out so it lands. {x, y, px, role (mono), label (small, above), label_px, anchor, roll (0.3)}."""
        d = ImageDraw.Draw(layer)
        a = ss(g["t0"], g["t0"] + 0.08, t) * (1 - ss(g["t1"] - 0.08, g["t1"], t)) * float(g.get("alpha", 1.0))
        if a <= 0.01:
            return
        keys = g["keys"]
        v = keys[0][1]
        for ka, kb in zip(keys, keys[1:]):
            va, tb, vb = ka[1], kb[0], kb[1]
            roll = float(kb[2]) if len(kb) > 2 else float(g.get("roll", 0.3))   # a key may roll in slower
            if t >= tb:
                v = vb
            elif t >= tb - roll:
                v = lerp(va, vb, ease_out((t - (tb - roll)) / roll))
                break
            else:
                v = va
                break
        col = tuple(int(c) for c in (g.get("color") or self.PAPER))
        x, y = g["x"] * self.W, g["y"] * self.H
        f = self.font(g.get("role", "mono"), float(g.get("px", 34)) * self.scale)
        txt = self.year_text(v)
        fl = self.font(g.get("role", "mono"), float(g.get("label_px", 16)) * self.scale)
        yl = y - float(g.get("px", 34)) * self.scale * 1.05
        if g.get("plate"):   # a dark plate so the year reads on a white sky
            p = 9 * self.scale
            wmax = max(d.textlength(self.year_text(k[1]), font=f) for k in keys)
            d.rectangle([x - p, yl - float(g.get("label_px", 16)) * self.scale - p, x + wmax + p, y + p * 0.9],
                        fill=tuple(int(c) for c in self.INK) + (int(255 * float(g["plate"]) * a),))
        d.text((x, y), txt, font=f, fill=col + (int(245 * a),), anchor=g.get("anchor", "ls"))
        if g.get("label"):
            d.text((x, yl), g["label"], font=fl, fill=col + (int(170 * a),), anchor=g.get("anchor", "ls"))

    @staticmethod
    def scale_text(m):
        """A size in metres, written the way a person reads it: 120 µm, 3.5 mm, 40 m, 2,300 km, 76,000 LIGHT YEARS."""
        def sig(v):   # three significant figures (76,104 reads 76,100)
            if v <= 0:
                return 0.0
            p = int(math.floor(math.log10(v)))
            return round(v, 2 - p)

        def num(v):
            v = sig(v)
            return f"{v:,.0f}" if v >= 100 else (f"{v:.1f}".rstrip("0").rstrip(".") if v >= 10 else f"{v:.2f}".rstrip("0").rstrip("."))
        if m < 1e-6:
            return num(m * 1e9) + " nm"
        if m < 1e-3:
            return num(m * 1e6) + " µm"
        if m < 1.0:
            return num(m * 1e3) + " mm"
        if m < 1e3:
            return num(m) + " m"
        if m < 1e13:
            return num(m / 1e3) + " km"
        return num(m / 9.4607e15) + " LIGHT YEARS"

    def g_scaleclock(self, layer, g, t):
        """The size of what is on screen, rolling on a log scale from one shot's size to the next.
        keys [[t, metres, roll?], ...]; {x, y, px, label, label_px, anchor, plate, roll (0.3)}."""
        d = ImageDraw.Draw(layer)
        a = ss(g["t0"], g["t0"] + 0.08, t) * (1 - ss(g["t1"] - 0.08, g["t1"], t)) * float(g.get("alpha", 1.0))
        if a <= 0.01:
            return
        keys = g["keys"]
        v = math.log10(keys[0][1])
        for ka, kb in zip(keys, keys[1:]):
            va, tb, vb = math.log10(ka[1]), kb[0], math.log10(kb[1])
            roll = float(kb[2]) if len(kb) > 2 else float(g.get("roll", 0.3))
            if t >= tb:
                v = vb
            elif t >= tb - roll:
                v = lerp(va, vb, ease_out((t - (tb - roll)) / roll))
                break
            else:
                v = va
                break
        col = tuple(int(c) for c in (g.get("color") or self.PAPER))
        x, y = g["x"] * self.W, g["y"] * self.H
        f = self.font(g.get("role", "mono"), float(g.get("px", 30)) * self.scale)
        fl = self.font(g.get("role", "mono"), float(g.get("label_px", 15)) * self.scale)
        txt = self.scale_text(10 ** v)
        yl = y - float(g.get("px", 30)) * self.scale * 1.05
        if g.get("plate"):
            p = 9 * self.scale
            wmax = max(d.textlength(self.scale_text(k[1]), font=f) for k in keys)
            anc = g.get("anchor", "ls")
            x0 = x - p if anc[0] == "l" else x - wmax - p
            d.rectangle([x0, yl - float(g.get("label_px", 15)) * self.scale - p, x0 + wmax + 2 * p, y + p * 0.9],
                        fill=tuple(int(c) for c in self.INK) + (int(255 * float(g["plate"]) * a),))
        d.text((x, y), txt, font=f, fill=col + (int(245 * a),), anchor=g.get("anchor", "ls"))
        if g.get("label"):
            d.text((x, yl), g["label"], font=fl, fill=col + (int(170 * a),), anchor=g.get("anchor", "ls"))

    def g_spiral(self, layer, g, t):
        """A golden spiral drawn onto a spiral in the picture, as the machine recognising it.
        {x, y: centre (frame fractions), r: outer radius (fraction of W), rot: angle of the outer end (deg),
        turns (3.2), dir: 1 counterclockwise inwards, -1 clockwise, grow (0.5 s: draws itself from the outside in),
        width (1.6), alpha, keys [[t, x, y, r, rot], ...] to follow a zoom or a turn, label: typed beside the outer end}.
        Golden spiral: the radius grows by phi every quarter turn."""
        d = ImageDraw.Draw(layer)
        a = ss(g["t0"], g["t0"] + 0.06, t) * (1 - ss(g["t1"] - 0.08, g["t1"], t)) * float(g.get("alpha", 1.0))
        if a <= 0.01:
            return
        W, H = self.W, self.H
        x, y, r, rot = float(g["x"]), float(g["y"]), float(g["r"]), float(g.get("rot", 0.0))
        if g.get("keys"):
            ks = g["keys"]
            x = keyframes([[k[0], k[1]] for k in ks], t, x)
            y = keyframes([[k[0], k[2]] for k in ks], t, y)
            r = keyframes([[k[0], k[3]] for k in ks], t, r)
            rot = keyframes([[k[0], k[4]] for k in ks], t, rot)
        cx, cy, R = x * W, y * H, r * W
        b = math.log((1 + 5 ** 0.5) / 2) / (math.pi / 2)
        turns = float(g.get("turns", 3.2))
        th_max = turns * 2 * math.pi
        u = ease_out((t - g["t0"]) / max(1e-3, float(g.get("grow", 0.5))))
        n = 360
        sgn = 1 if int(g.get("dir", 1)) >= 0 else -1
        pts = []
        for i in range(int(n * u) + 1):
            th = th_max * i / n                      # 0 at the outer end, th_max at the centre
            rr = R * math.exp(-b * th)
            ang = math.radians(rot) + sgn * th
            pts.append((cx + rr * math.cos(ang), cy - rr * math.sin(ang)))
        col = tuple(int(c) for c in (g.get("color") or self.PAPER))
        A = int(235 * a)
        w = max(1, int(round(float(g.get("width", 1.6)) * self.scale)))
        if len(pts) > 1:
            d.line(pts, fill=col + (A,), width=w, joint="curve")
        c = 6 * self.scale
        if u > 0.9:
            d.line([(cx - c, cy), (cx + c, cy)], fill=col + (int(A * 0.8),), width=w)
            d.line([(cx, cy - c), (cx, cy + c)], fill=col + (int(A * 0.8),), width=w)
        if g.get("label") and pts:
            f = self.font("mono", float(g.get("px", 18)) * self.scale)
            txt = g["label"]
            k = int(len(txt) * ss(g["t0"] + 0.1, g["t0"] + 0.1 + len(txt) / 50.0, t) + 0.999)
            ox, oy = pts[0]
            tx = min(max(8 * self.scale, ox + 10 * self.scale), W - d.textlength(txt, font=f) - 8 * self.scale)
            ty = min(max(24 * self.scale, oy - 10 * self.scale), H - 10 * self.scale)
            d.text((tx, ty), txt[:k], font=f, fill=col + (A,), anchor="ls")

    def g_hexgrid(self, layer, g, t):
        """A hexagonal lattice laid onto hexagons in the picture (honeycomb, basalt, Saturn's pole), appearing cell by
        cell from the centre. {x, y: centre, cell: cell width across flats (fraction of
        W), rot (deg), radius: how far the lattice reaches (fraction of W), grow (0.45 s), width (1.3), alpha, keys
        [[t, x, y, cell, rot], ...], label, squash: vertical scale after the rotation, for a hexagon seen at an angle
        (Saturn's pole from the side, 0.45), trace: draw each outline as a line that runs round it instead of growing
        it from its centre (a single hexagon: radius smaller than the cell)}."""
        d = ImageDraw.Draw(layer)
        a = ss(g["t0"], g["t0"] + 0.06, t) * (1 - ss(g["t1"] - 0.08, g["t1"], t)) * float(g.get("alpha", 1.0))
        if a <= 0.01:
            return
        W, H = self.W, self.H
        x, y, cell, rot = float(g["x"]), float(g["y"]), float(g["cell"]), float(g.get("rot", 0.0))
        radius = float(g.get("radius", 0.3))
        if g.get("keys"):
            ks = g["keys"]
            x = keyframes([[k[0], k[1]] for k in ks], t, x)
            y = keyframes([[k[0], k[2]] for k in ks], t, y)
            cell = keyframes([[k[0], k[3]] for k in ks], t, cell)
            rot = keyframes([[k[0], k[4]] for k in ks], t, rot)
            if len(ks[0]) > 5:                       # [t, x, y, cell, rot, radius]: the reach follows a zoom too
                radius = keyframes([[k[0], k[5]] for k in ks], t, radius)
        cx, cy = x * W, y * H
        s = cell * W / math.sqrt(3)                  # hexagon side (flat to flat = sqrt(3) * side)
        reach = radius * W
        grow = max(1e-3, float(g.get("grow", 0.45)))
        u = (t - g["t0"]) / grow
        ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        sq = float(g.get("squash", 1.0))
        trace = bool(g.get("trace"))
        col = tuple(int(c) for c in (g.get("color") or self.PAPER))
        w = max(1, int(round(float(g.get("width", 1.3)) * self.scale)))
        n = int(reach / (math.sqrt(3) * s)) + 2
        for q in range(-n, n + 1):
            for rr in range(-n, n + 1):
                # axial coordinates, pointy-top lattice
                hx = s * math.sqrt(3) * (q + rr / 2.0)
                hy = s * 1.5 * rr
                dist = math.hypot(hx, hy)
                if dist > reach:
                    continue
                appear = ease_out(u * 1.6 - dist / reach) if not trace else ease_out(min(1.0, u - dist / max(reach, 1e-6) * 0.5))
                if appear <= 0:
                    continue
                fall = 1 - ss(reach * 0.6, reach, dist) if reach > s else 1.0
                A = int(215 * a * (appear if not trace else 1.0) * fall)
                if A < 4:
                    continue
                px, py = cx + hx * ca - hy * sa, cy + (hx * sa + hy * ca) * sq
                corners = []
                size = s if trace else s * appear
                for k in range(6):
                    ang = math.radians(60 * k - 30 + rot)
                    corners.append((px + size * math.cos(ang), py + size * math.sin(ang) * sq))
                ring = corners + [corners[0]]
                if trace:
                    # the outline runs round from its first corner; the last segment is cut where the line has got to
                    m = appear * 6
                    k = int(m)
                    pts = ring[:k + 1]
                    if k < 6:
                        f = m - k
                        (x0, y0), (x1, y1) = ring[k], ring[k + 1]
                        pts = pts + [(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)]
                    if len(pts) > 1:
                        d.line(pts, fill=col + (A,), width=w, joint="curve")
                else:
                    d.line(ring, fill=col + (A,), width=w)
        if g.get("label"):
            f = self.font("mono", float(g.get("px", 18)) * self.scale)
            txt = g["label"]
            k = int(len(txt) * ss(g["t0"] + 0.1, g["t0"] + 0.1 + len(txt) / 50.0, t) + 0.999)
            A = int(235 * a)
            tx = min(cx + reach * 0.62, W - d.textlength(txt, font=f) - 8 * self.scale)
            ty = max(24 * self.scale, cy - reach * 0.62)
            d.text((tx, ty), txt[:k], font=f, fill=col + (A,), anchor="ls")

    def g_graph(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        x0, y0, x1, y1 = [v * (W if i % 2 == 0 else H) for i, v in enumerate(g["box"])]
        a = ss(g["t0"], g["t0"] + 0.1, t) * (1 - ss(g["t1"] - 0.1, g["t1"], t))
        col = tuple(int(c) for c in self.PAPER)
        w = max(1, int(round(2 * self.scale)))
        d.line([(x0, y1), (x1, y1)], fill=col + (int(150 * a),), width=w)
        d.line([(x0, y0), (x0, y1)], fill=col + (int(150 * a),), width=w)
        f = self.font("mono", 22 * self.scale)
        for k in range(1, 5):
            yy = y1 - (y1 - y0) * k / 4
            d.line([(x0, yy), (x1, yy)], fill=col + (int(40 * a),), width=1)
        for ln in g["lines"]:
            u = ss(ln["t0"], ln["t1"], t)
            if u <= 0:
                continue
            n = 90
            pts = []
            for i in range(int(n * u) + 1):
                q = i / n
                v = eval_curve(ln["curve"], q, i)
                pts.append((x0 + (x1 - x0) * q, y1 - (y1 - y0) * v))
            if len(pts) > 1:
                d.line(pts, fill=col + (int(245 * a),), width=max(2, int(round(3 * self.scale))))
                x, y = pts[-1]
                r = 6 * self.scale
                d.rectangle([x - r, y - r, x + r, y + r], outline=col + (int(245 * a),), width=w)
                d.text((x + 14 * self.scale, y - 8 * self.scale), ln["label"], font=f, fill=col + (int(235 * a),), anchor="ls")
                if ln.get("value"):
                    v = eval_curve(ln["curve"], u, int(n * u)) * float(ln.get("vmax", 100))
                    d.text((x + 14 * self.scale, y + 16 * self.scale), ln["value"].format(v=v), font=f,
                           fill=col + (int(160 * a),), anchor="ls")

    def g_wave(self, layer, g, t):
        if self.wave is None:
            return
        x, sr, at = self.wave
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = ss(g["t0"], g["t0"] + 0.08, t) * (1 - ss(g["t1"] - 0.08, g["t1"], t))
        span = float(g.get("span", 0.6))
        c = int((t - at) * sr)
        n = int(span * sr)
        seg = x[max(0, c - n): c] if c > 0 else np.zeros(1, np.float32)
        if len(seg) < n:
            seg = np.concatenate([np.zeros(n - len(seg), np.float32), seg])
        k = 360
        seg = seg[: n - n % k].reshape(k, -1)
        env = np.sqrt((seg ** 2).mean(1)) * np.sign(seg.mean(1) + 1e-9)
        amp = float(g.get("amp", 0.22)) * H * 3.2
        y = g.get("y", 0.5) * H
        pts = [(W * i / (k - 1), y - env[i] * amp * (1 if i % 2 else -1)) for i in range(k)]
        col = tuple(int(cc) for cc in self.PAPER)
        d.line(pts, fill=col + (int(235 * a),), width=max(1, int(round(2 * self.scale))))
        d.line([(0, y), (W, y)], fill=col + (int(60 * a),), width=1)
        if g.get("label"):
            f = self.font("mono", 21 * self.scale)
            d.text((56 * self.scale, y - 0.2 * H), g["label"], font=f, fill=col + (int(220 * a),), anchor="ls")

    def g_swatches(self, layer, g, t):
        key = ("sw", g["take"], g.get("at", 0.0))
        if key not in self.cache:
            f = self.take(g["take"], "swatch").frame(int(float(g.get("at", 0.0)) * self.fps))
            px = cv2.resize(f, (96, 72), interpolation=cv2.INTER_AREA).reshape(-1, 3).astype(np.float32)
            crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
            _, lab, cen = cv2.kmeans(px, int(g.get("k", 5)), None, crit, 3, cv2.KMEANS_PP_CENTERS)
            cnt = np.bincount(lab.ravel(), minlength=len(cen))
            order = np.argsort(-cnt)
            self.cache[key] = [tuple(int(v) for v in cen[i]) for i in order]
        cols = self.cache[key]
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        n = len(cols)
        x0, y0, x1, y1 = [v * (W if i % 2 == 0 else H) for i, v in enumerate(g["box"])]
        bw = (x1 - x0) / n
        f = self.font("mono", 17 * self.scale)
        for i, c in enumerate(cols):
            ti = g["t0"] + i * float(g.get("stagger", 0.06))
            u = ease_out((t - ti) / 0.18)
            if u <= 0:
                continue
            a = u * (1 - ss(g["t1"] - 0.08, g["t1"], t))
            top = y1 - (y1 - y0) * u
            d.rectangle([x0 + i * bw + 2, top, x0 + (i + 1) * bw - 2, y1], fill=c + (int(255 * a),))
            hx = "#%02X%02X%02X" % c
            ink = (27, 27, 25) if sum(c) > 380 else (245, 245, 243)
            d.text((x0 + i * bw + 10 * self.scale, y1 - 12 * self.scale), hx, font=f, fill=ink + (int(255 * a),), anchor="ls")

    def g_network(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        cx, cy = g.get("center", [0.5, 0.5])
        cx, cy = cx * W, cy * H
        a_all = ss(g["t0"], g["t0"] + 0.1, t) * (1 - ss(g["t1"] - 0.1, g["t1"], t))
        col = tuple(int(c) for c in self.PAPER)
        w = max(1, int(round(1.6 * self.scale)))
        f = self.font("mono", float(g.get("px", 21)) * self.scale)
        rx, ry = g.get("radius", [0.34, 0.3])
        rx, ry = rx * W, ry * H
        spin = float(g.get("spin", 0.05)) * (t - g["t0"])
        nodes = g["nodes"]
        for i, nd in enumerate(nodes):
            ang = nd.get("a", 2 * math.pi * i / len(nodes)) + spin
            rr = nd.get("r", 1.0)
            x, y = cx + math.cos(ang) * rx * rr, cy + math.sin(ang) * ry * rr
            ta = nd["t"]
            u = ss(ta, ta + 0.22, t)
            if u <= 0:
                continue
            ex, ey = cx + (x - cx) * u, cy + (y - cy) * u
            d.line([(cx, cy), (ex, ey)], fill=col + (int(170 * a_all),), width=w)
            # pulses travel from the centre outwards
            for p in range(2):
                q = ((t - ta) * float(g.get("pulse", 0.9)) + p * 0.5) % 1.0
                if t > ta + 0.2:
                    px_, py_ = cx + (x - cx) * q, cy + (y - cy) * q
                    r = 3.2 * self.scale
                    d.rectangle([px_ - r, py_ - r, px_ + r, py_ + r], fill=col + (int(230 * a_all),))
            if u >= 1:
                r = 7 * self.scale
                d.rectangle([x - r, y - r, x + r, y + r], outline=col + (int(240 * a_all),), width=w)
                anc = "ls" if x >= cx else "rs"
                dx = 16 * self.scale if x >= cx else -16 * self.scale
                d.text((x + dx, y + 6 * self.scale), nd["label"], font=f, fill=col + (int(230 * a_all),), anchor=anc)
        # the centre
        cu = ss(g["t0"], g["t0"] + 0.25, t)
        R = (26 + 6 * math.sin(t * 5)) * self.scale * cu
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=col + (int(240 * a_all),), width=w + 1)
        r = 5 * self.scale
        d.rectangle([cx - r, cy - r, cx + r, cy + r], fill=col + (int(255 * a_all),))
        if g.get("center_label"):
            fl = self.font("mono", 23 * self.scale)
            tw = d.textlength(g["center_label"], font=fl)
            yb = cy + R + 30 * self.scale
            d.rectangle([cx - tw / 2 - 10 * self.scale, yb - 24 * self.scale, cx + tw / 2 + 10 * self.scale, yb + 9 * self.scale],
                        fill=tuple(int(c) for c in self.INK) + (int(235 * a_all),))
            d.text((cx, cy + R + 30 * self.scale), g["center_label"], font=fl,
                   fill=col + (int(240 * a_all),), anchor="ms")

    def g_hud(self, layer, g, t):
        pass  # the hud is drawn by draw_hud every frame

    def g_ascii_logo(self, layer, g, t):
        pass  # drawn in numpy by ascii_logo, after the type

    # ---- type
    def draw_type(self, img, t):
        W, H = self.W, self.H
        for cap in self.tl.get("captions", []):
            if cap["words"][0][1] - 0.05 <= t < cap["t_out"]:
                self.draw_caption(img, cap, t)
        for b in self.tl.get("big", []):
            if b["t0"] - 0.001 <= t < b["t1"]:
                self.draw_big(img, b, t)
        return img

    def draw_caption(self, img, cap, t):
        W, H = self.W, self.H
        px = float(cap.get("size", 42)) * self.scale
        role = cap.get("role", "sans")
        axes = cap.get("axes", {"wght": 420})
        gap = px * float(cap.get("gap", 0.34))
        col = np.array(cap.get("color") or self.PAPER, np.float32)
        maxw = float(cap.get("max_w", 0.8)) * W
        words = cap["words"]
        lines, cur, cw = [], [], 0.0
        for w in words:
            ln = self.line(w[0], role, px, axes)
            ww = ln.advance
            if cur and cw + gap + ww > maxw:
                lines.append((cur, cw))
                cur, cw = [], 0.0
            cur.append((w, ln))
            cw += (gap if len(cur) > 1 else 0) + ww
        if cur:
            lines.append((cur, cw))
        lh = px * 1.3
        y0 = cap.get("y", 0.8) * H - (len(lines) - 1) * lh / 2
        out_a = 1 - ss(cap["t_out"] - 0.08, cap["t_out"], t)
        for li, (items, lw) in enumerate(lines):
            x = W / 2 - lw / 2 if cap.get("align", "c") == "c" else cap.get("x", 0.08) * W
            y = y0 + li * lh
            for (w, ln) in items:
                t_in = w[1]
                a = ss(t_in - 0.02, t_in + 0.08, t) * out_a
                flags = w[3] if len(w) > 3 else ""
                if a > 0.004:
                    dy = (1 - ss(t_in - 0.02, t_in + 0.1, t)) * 10 * self.scale
                    if "h" in flags:  # highlight box sweeps in, the word turns ink
                        u = ease_out((t - t_in) / 0.16)
                        pad = 0.18 * px
                        bx0, bx1 = x - pad, x - pad + (ln.advance + 2 * pad) * u
                        by0, by1 = int(y - px * 0.82), int(y + px * 0.3)
                        img[max(0, by0):by1, max(0, int(bx0)):int(bx1)] = (
                            img[max(0, by0):by1, max(0, int(bx0)):int(bx1)] * (1 - 0.94 * a) + self.PAPER * 0.94 * a)
                        typeset.place(img, ln, x, y + dy, self.INK, a, "ls")
                    else:
                        self.halo(img, ln, x, y + dy, a * float(cap.get("shadow", 0.75)), px)
                        typeset.place(img, ln, x, y + dy, col, a, "ls")
                x += ln.advance + gap

    def halo(self, img, ln, x, y, a, px, anchor="ls"):
        key = ("halo", id(ln))
        if key not in self.cache:
            al = Image.fromarray((ln.alpha * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(max(1.5, px * 0.09)))
            import dataclasses
            self.cache[key] = dataclasses.replace(ln, alpha=np.asarray(al, np.float32) / 255.0)
        typeset.place(img, self.cache[key], x, y + 1, (10, 10, 9), a, anchor)

    def draw_words(self, img, b, t):
        """A block of words that appear as they are spoken, the calm way: one face,
        one size, no italic; the block is laid out once, so nothing reflows; each
        word fades up out of a slight blur over `fade` s, starting `lead` s before it is said; the block leaves
        with one short fade. lines: [[[word, t_in], ...], ...], centred on (x, y) (y is the block's middle)."""
        W, H = self.W, self.H
        a_out = 1 - ss(b["t1"] - float(b.get("out", 0.2)), b["t1"], t)
        if a_out <= 0.004:
            return
        role = b.get("role", "serif")
        px = float(b["px"]) * self.scale
        axes = b.get("axes") if b.get("axes") is not None else self.axes.get(role)
        col = np.array(b.get("color") or self.PAPER, np.float32)
        lead, fade = float(b.get("lead", 0.04)), max(1e-3, float(b.get("fade", 0.3)))
        blur, rise = float(b.get("blur", 0.07)), float(b.get("rise", 0.0))
        shadow = float(b.get("shadow", 0.45))
        lines = b["lines"]
        n = len(lines)
        L = px * float(b.get("leading", 1.24))
        xc, yc = float(b.get("x", 0.5)) * W, float(b.get("y", 0.5)) * H
        hl = b.get("highlight") or {}          # words flagged "s" switch to this face at the same size
        h_role = hl.get("role", "serif")
        h_axes = hl.get("axes") if hl.get("axes") is not None else self.axes.get(h_role)
        h_px = px * float(hl.get("scale", 1.06))   # a serif's smaller x-height needs a touch more size to read the same
        for i, words in enumerate(lines):
            flagged = any(len(wd) > 2 and "s" in wd[2] for wd in words)
            text = " ".join(wd[0] for wd in words)
            key = ("wpos", text, role, round(px, 1), json.dumps(axes, sort_keys=True), flagged)
            if key not in self.cache:
                if not flagged:
                    self.cache[key] = typeset.pen_positions(text, self.fonts[role], px, axes)
                else:
                    # mixed faces: lay the words out one by one with the running face's space
                    sp = self.line("n n", role, px, axes).advance - 2 * self.line("n", role, px, axes).advance
                    xs, x = [0.0] * len(text), 0.0
                    k = 0
                    for wd in words:
                        s_ = len(wd) > 2 and "s" in wd[2]
                        xs[k] = x
                        x += self.line(wd[0], h_role if s_ else role, h_px if s_ else px, h_axes if s_ else axes).advance + sp
                        k += len(wd[0]) + 1
                    self.cache[key] = (xs, x - sp)
            xs, adv = self.cache[key]
            x0 = xc - adv / 2 if b.get("align", "c") == "c" else xc
            y = yc + (i - (n - 1) / 2) * L + float(b.get("centre", 0.3)) * px   # baseline; 0.3 em puts the x-height band on the line's middle
            k = 0
            for wd in words:
                w, tw = wd[0], wd[1]
                s_ = len(wd) > 2 and "s" in wd[2]
                w_role, w_px, w_axes = (h_role, h_px, h_axes) if s_ else (role, px, axes)
                u = (t - (tw - lead)) / fade
                if u > 0:
                    ln = self.line(w, w_role, w_px, w_axes)
                    a = ss(0.0, 1.0, u) * a_out
                    sig = blur * px * (1 - ease_out(u))
                    if sig >= 0.5:
                        import dataclasses
                        ln = dataclasses.replace(ln, alpha=cv2.GaussianBlur(ln.alpha, (0, 0), sig))
                    dy = rise * px * (1 - ease_out(u))
                    if shadow > 0:
                        self.halo(img, self.line(w, w_role, w_px, w_axes), x0 + xs[k], y + dy, a * shadow, px)
                    typeset.place(img, ln, x0 + xs[k], y + dy, col, a, "ls")
                k += len(w) + 1

    def draw_big(self, img, b, t):
        W, H = self.W, self.H
        style = b.get("style", "fill")
        if style == "words":
            self.draw_words(img, b, t)
            return
        role = b.get("role", "serif")
        px = float(b["px"]) * self.scale
        text = b.get("text", "")
        runs = b.get("runs")
        col = np.array(b.get("color") or self.PAPER, np.float32)
        a_in = ss(b["t0"] - 0.01, b["t0"] + float(b.get("fade", 0.06)), t)
        a_out = 1 - ss(b["t1"] - float(b.get("out", 0.06)), b["t1"], t)
        a = a_in * a_out * float(b.get("alpha", 1.0))
        x, y = b.get("x", 0.5) * W, b.get("y", 0.5) * H
        anchor = b.get("anchor", "mm")
        if style == "typewriter":
            chars = b.get("chars")  # [[n_chars, time], ...]
            n = len(text)
            if chars:
                n = 0
                for k_, tk_ in chars:
                    if t >= tk_:
                        n = k_
            else:
                n = int(len(text) * ss(b["t0"], b.get("t_typed", b["t1"]), t) + 0.999)
            full = self.line(text, role, px, b.get("axes"), float(b.get("tracking", 0.0)))
            shown = text[:n]
            if shown.strip():
                ln = self.line(shown, role, px, b.get("axes"), float(b.get("tracking", 0.0)))
                x0 = x - full.advance / 2 if anchor[0] == "m" else x
                self.halo(img, ln, x0, y, a * 0.7, px, "l" + anchor[1])
                typeset.place(img, ln, x0, y, col, a, "l" + anchor[1])
                cx = x0 + ln.advance + 0.06 * px
            else:
                cx = x - full.advance / 2 if anchor[0] == "m" else x
            blink = 1.0 if (t - b["t0"]) % 0.5 < 0.3 or n < len(text) else 0.0
            if b.get("cursor", True) and blink:
                h0, h1 = y - px * 0.72, y + px * 0.12
                if anchor[1] == "m":
                    h0, h1 = y - px * 0.42, y + px * 0.42
                cw = max(2, int(round(px * 0.06)))
                img[int(h0):int(h1), int(cx):int(cx) + cw] = img[int(h0):int(h1), int(cx):int(cx) + cw] * (1 - a) + col * a
            return
        ln = self.line(text, role, px, b.get("axes"), float(b.get("tracking", 0.0)), runs,
                       float(b.get("word_spacing", 0.0)))
        anim = b.get("anim", "pop")
        sc = 1.0
        if anim == "pop":
            sc = 1 + 0.06 * (1 - ease_out((t - b["t0"]) / 0.18))
        elif anim == "push":
            sc = lerp(1.0, float(b.get("push", 1.08)), (t - b["t0"]) / max(0.01, b["t1"] - b["t0"]))
        elif anim == "slide":
            x += (1 - ease_out((t - b["t0"]) / 0.25)) * float(b.get("dx", 80)) * self.scale
        if style == "ghost":
            x += float(b.get("drift", -40)) * self.scale * (t - b["t0"])
        if sc != 1.0:
            ln = scaled_line(ln, sc)
        if style == "knockout":
            # the word is a window onto another take; the rest goes to ink
            key = ("ko", id(ln), round(x), round(y))
            mask = np.zeros((H, W), np.float32)
            place_alpha(mask, ln, x, y, anchor)
            tk = self.take(b["take"], "big")
            fr = tk.frame(int((t - b["t0"] + float(b.get("take_in", 0))) * self.fps))
            fill = cv2.resize(fr, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32)
            bg = float(b.get("bg_keep", 0.0))
            base = img * bg + self.INK * (1 - bg)
            m = mask[..., None]
            img[:] = img * (1 - a) + (base * (1 - m) + fill * m) * a
            return
        if style == "echo":
            n = int(b.get("copies", 5))
            step = float(b.get("step", 0.08))
            dyp = float(b.get("dy", 0.11)) * H
            for i in range(n):
                ti = b["t0"] + i * step
                if t < ti:
                    continue
                ai = a * (0.18 + 0.82 * (i == n - 1)) * ss(ti, ti + 0.05, t)
                yy = y - (n - 1 - i) * dyp
                self.halo(img, ln, x, yy, ai * 0.6, px, anchor)
                typeset.place(img, ln, x, yy, col, ai, anchor)
            return
        if style == "highlight":
            pad = 0.22 * px
            u = ease_out((t - b["t0"]) / 0.2)
            x0 = x - ln.advance / 2 - pad if anchor[0] == "m" else x - pad
            bx1 = x0 + (ln.advance + 2 * pad) * u
            y0, y1 = int(y - px * 0.62), int(y + px * 0.46)
            if anchor[1] == "s":
                y0, y1 = int(y - px * 0.85), int(y + px * 0.28)
            img[max(0, y0):y1, max(0, int(x0)):int(bx1)] = img[max(0, y0):y1, max(0, int(x0)):int(bx1)] * (1 - a) + self.PAPER * a
            typeset.place(img, ln, x, y, self.INK, a * ss(b["t0"] + 0.06, b["t0"] + 0.14, t), anchor)
            return
        if style != "ghost":
            self.halo(img, ln, x, y, a * float(b.get("shadow", 0.7)), px, anchor)
        typeset.place(img, ln, x, y, col, a, anchor)

    # ---- hud
    def draw_hud(self, layer, t):
        h = self.tl.get("hud")
        if not h:
            return
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        mx, my = [v * self.scale for v in h.get("margin", [40, 40])]
        on = keyframes(h.get("on", [[0, 1]]), t, 1.0)
        if on <= 0.01:
            return
        ground = keyframes(h.get("paper", [[0, 0]]), t, 0.0)
        col = tuple(int(c) for c in (self.INK if ground > 0.5 else self.PAPER))
        f = self.font("mono", 20 * self.scale)
        txt = h.get("text", "BRAND")
        tw = d.textlength(txt, font=f)
        pad = 7 * self.scale
        x1, y0 = W - mx, my
        n = len(txt)
        if h.get("type_on"):
            n = int(n * ss(h["type_on"][0], h["type_on"][1], t) + 0.999)
        box_a = int(255 * on)
        d.rectangle([x1 - tw - 2 * pad, y0, x1, y0 + 30 * self.scale], fill=col + (box_a,))
        inv = tuple(int(c) for c in (self.PAPER if ground > 0.5 else self.INK))
        d.text((x1 - tw - pad, y0 + 22 * self.scale), txt[:n], font=f, fill=inv + (box_a,), anchor="ls")
        if h.get("context"):
            v = keyframes(h["context"], t, 0.0)
            ctx = f"CONTEXT {int(round(v)):03d}%"
            d.text((x1, y0 + 56 * self.scale), ctx, font=f, fill=col + (int(220 * on),), anchor="rs")
            bw = tw + 2 * pad
            yb = y0 + 66 * self.scale
            d.rectangle([x1 - bw, yb, x1, yb + 3 * self.scale], outline=col + (int(120 * on),), width=1)
            d.rectangle([x1 - bw, yb, x1 - bw + bw * v / 100.0, yb + 3 * self.scale], fill=col + (int(230 * on),))
        if h.get("tc"):
            fr = int(round(t * self.fps))
            tc = f"{fr // (self.fps * 60):02d}:{(fr // self.fps) % 60:02d}:{fr % self.fps:02d}"
            d.text((mx, y0 + 22 * self.scale), f"REC {tc}", font=f, fill=col + (int(170 * on),), anchor="ls")
            if int(t * 2) % 2 == 0:
                r = 5 * self.scale
                cxr = mx - 14 * self.scale
                d.ellipse([cxr - r, y0 + 13 * self.scale - r, cxr + r, y0 + 13 * self.scale + r], fill=col + (int(200 * on),))

    # ---- one frame
    def frame(self, t):
        img, s = self.picture(t)
        img = np.clip(img, 0, 255)
        lay = self.overlay_layer(t, s)
        if lay is not None:
            a = np.asarray(lay, np.float32)
            al = a[..., 3] / 255.0
            for cap in self.tl.get("captions", []):
                if cap["words"][0][1] - 0.05 <= t < cap["t_out"]:
                    px = float(cap.get("size", 42)) * self.scale
                    yc = cap.get("y", 0.8) * self.H
                    y0, y1 = int(max(0, yc - 1.25 * px)), int(min(self.H, yc + 0.7 * px))
                    al[y0:y1] *= 0.12
            sh = cv2.GaussianBlur(al, (0, 0), 2.0 * self.scale) * 0.55
            img *= (1 - sh[..., None])
            img = img * (1 - al[..., None]) + a[..., :3] * al[..., None]
        self.draw_type(img, t)
        for g in self.tl.get("graphics", []):
            if g["type"] == "ascii_logo" and g["t0"] <= t < g["t1"]:
                self.ascii_logo(img, g, t)
        band = min(self.H, int(round(130 * self.scale)))  # the corner tag lives in the top strip only
        hud = Image.new("RGBA", (self.W, band), (0, 0, 0, 0))
        self.draw_hud(hud, t)
        a = np.asarray(hud, np.float32)
        al = a[..., 3] / 255.0
        top = img[:band]
        h = self.tl.get("hud") or {}
        if keyframes(h.get("paper", [[0, 0]]), t, 0.0) < 0.5:  # light readouts need a dark halo on bright footage
            sh = cv2.GaussianBlur(al, (0, 0), 2.4 * self.scale) * 0.6
            top *= (1 - sh[..., None])
        img[:band] = top * (1 - al[..., None]) + a[..., :3] * al[..., None]
        return self.finish(img, t)

    def ascii_logo(self, img, g, t):
        """The wordmark resolving out of the letter field, then the real logo file."""
        W, H = self.W, self.H
        key = ("logo", g.get("w", 0.62), g.get("y", 0.5), g.get("x", 0.5))
        if key not in self.cache:
            lw = int(W * float(g.get("w", 0.62)))
            lg = self.logo.resize((lw, int(self.logo.height * lw / self.logo.width)), Image.LANCZOS)
            m = np.zeros((H, W), np.float32)
            a = np.asarray(lg.getchannel("A"), np.float32) / 255.0
            x0 = int(g.get("x", 0.5) * W - lw / 2)
            y0 = int(g.get("y", 0.5) * H - a.shape[0] / 2)
            m[y0:y0 + a.shape[0], x0:x0 + a.shape[1]] = a
            self.cache[key] = m
        m = self.cache[key]
        u = ss(g["t0"], g.get("t_resolve", g["t0"] + 0.8), t)
        rng = np.random.default_rng(int(t * 997))
        noise = cv2.resize(rng.random((H // 20, W // 20)).astype(np.float32), (W, H), interpolation=cv2.INTER_NEAREST)
        field_src = np.clip(m * (0.35 + 0.65 * u) + noise * (1 - u) * 0.55, 0, 1)
        cov = self.mosaic.render(field_src, W, H, t, swap=0.5 * (1 - u))
        col = self.INK if g.get("ground", "ink") == "paper" else self.PAPER
        clean = ss(g.get("t_resolve", g["t0"] + 0.8) - 0.15, g.get("t_resolve", g["t0"] + 0.8) + 0.15, t)
        fade = 1 - ss(g["t1"] - 0.1, g["t1"], t) if g.get("fade_out", True) else 1.0
        over(img, col, np.clip(cov * (1 - clean), 0, 1) * fade)
        over(img, col, m * clean * fade)

    def finish(self, img, t):
        f = self.tl.get("finish") or {}
        W, H = self.W, self.H
        if f.get("chroma"):
            d = int(round(float(f["chroma"]) * self.scale))
            if d:
                img[..., 0] = np.roll(img[..., 0], d, axis=1)
                img[..., 2] = np.roll(img[..., 2], -d, axis=1)
        if f.get("vignette"):
            key = ("vig",)
            if key not in self.cache:
                yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
                r = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
                self.cache[key] = (1 - float(f["vignette"]) * np.clip(r - 0.55, 0, 1) ** 1.6)[..., None]
            img *= self.cache[key]
        if f.get("scan"):
            key = ("scan",)
            if key not in self.cache:
                v = np.ones((H, 1, 1), np.float32)
                v[::3] = 1 - float(f["scan"])
                self.cache[key] = v
            img *= self.cache[key]
        if f.get("grain"):
            g = self.rng.normal(0, float(f["grain"]), (H // 2, W // 2)).astype(np.float32)
            img += cv2.resize(g, (W, H), interpolation=cv2.INTER_NEAREST)[..., None]
        return np.clip(img, 0, 255)


# ---------------------------------------------------------------- helpers for type
def eval_curve(c, q, i):
    if c == "up":
        return 0.04 + 0.9 * q ** 2.2
    if c == "flat":
        return 0.035 + 0.006 * math.sin(i * 1.7)
    if c == "lin":
        return q
    return 0.0


def scaled_line(ln, s):
    import dataclasses
    h, w = ln.alpha.shape
    a = cv2.resize(ln.alpha, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_LINEAR)
    return dataclasses.replace(ln, alpha=a, origin=(ln.origin[0] * s, ln.origin[1] * s), advance=ln.advance * s,
                               ascent=ln.ascent * s, descent=ln.descent * s)


def place_alpha(mask, ln, x, y, anchor):
    h, w = ln.alpha.shape
    ox = {"l": 0.0, "m": -ln.advance / 2, "r": -ln.advance}[anchor[0]]
    oy = {"s": 0.0, "m": ln.ascent * 0.36, "a": ln.ascent}[anchor[1]]
    x0 = int(round(x + ox - ln.origin[0]))
    y0 = int(round(y + oy - ln.origin[1]))
    H, W = mask.shape
    sx0, sy0 = max(0, -x0), max(0, -y0)
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(W, x0 + w), min(H, y0 + h)
    if fx1 > fx0 and fy1 > fy0:
        mask[fy0:fy1, fx0:fx1] = np.maximum(mask[fy0:fy1, fx0:fx1], ln.alpha[sy0:sy0 + fy1 - fy0, sx0:sx0 + fx1 - fx0])


# ---------------------------------------------------------------- audio
def build_audio(tl, base, out_wav, total):
    a = tl["audio"]
    P = lambda p: p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))  # noqa: E731
    vo_at = float(a.get("vo_at", 0.0))
    m_in = float(a.get("music_in", 0.0))
    fade = float(a.get("fade_out", 1.5))
    duck = float(a.get("duck_db", 7.0))
    mv = float(a.get("music_db", -4.0))
    tmp = out_wav + ".pre.wav"
    sfx = a.get("sfx") or []
    m_at = float(a.get("music_at", 0.0))   # film time where the track (from music_in) starts; before it, only the voice and SFX
    ms = int(round(m_at * 1000))
    fc = (f"[0:a]atrim=start={m_in:.3f}:duration={max(0.1, total - m_at):.3f},asetpts=PTS-STARTPTS,"
          f"aformat=sample_rates=48000:channel_layouts=stereo,volume={mv}dB,afade=t=in:d=0.04,"
          f"adelay={ms}|{ms},apad=whole_dur={total:.3f},afade=t=out:st={total - fade:.3f}:d={fade:.3f}[m0];"
          f"[1:a]aformat=sample_rates=48000:channel_layouts=stereo,adelay={int(vo_at * 1000)}|{int(vo_at * 1000)},"
          f"apad=whole_dur={total:.3f},volume={float(a.get('vo_db', 0.0))}dB[v];")
    # sound effects ride the music bus, so the voice ducks them too
    bus = ["[m0]"]
    for k, e in enumerate(sfx):
        at_ms = int(round(float(e["at"]) * 1000))
        dur = float(e.get("dur", 30.0))
        fo = float(e.get("fade", 0.06))
        fi = float(e.get("fade_in", 0.0))     # a sound that swells in (an ambience under a quiet ending)
        fin = f"afade=t=in:d={fi:.3f}," if fi > 0 else ""
        fc += (f"[{k + 2}:a]atrim=start={float(e.get('trim', 0.0)):.3f}:duration={dur:.3f},asetpts=PTS-STARTPTS,"
               f"aformat=sample_rates=48000:channel_layouts=stereo,volume={float(e.get('gain', -12.0))}dB,{fin}"
               f"afade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f},adelay={at_ms}|{at_ms}[s{k}];")
        bus.append(f"[s{k}]")
    if len(bus) > 1:
        fc += f"{''.join(bus)}amix=inputs={len(bus)}:duration=first:normalize=0[m];"
    else:
        fc += "[m0]anull[m];"
    fc += (f"[v]asplit=2[v1][v2];"
           f"[m][v1]sidechaincompress=threshold=0.03:ratio={max(2.0, duck):.1f}:attack=15:release=320:makeup=1[md];"
           f"[md][v2]amix=inputs=2:duration=first:normalize=0,atrim=duration={total:.3f}[mix]")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", P(a["music"]), "-i", P(a["vo"])]
    for e in sfx:
        cmd += ["-i", P(e["file"])]
    subprocess.run(cmd + ["-filter_complex", fc, "-map", "[mix]", "-ar", "48000", tmp], check=True)
    target = float(a.get("lufs", -14.0))
    tp = float(a.get("tp", -1.0))  # leave room for the AAC encode, which overshoots by about half a dB
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", tmp, "-af", f"loudnorm=I={target}:TP={tp}:LRA=11:print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    js = json.loads(r.stderr[r.stderr.rfind("{"):r.stderr.rfind("}") + 1])
    lf = (f"loudnorm=I={target}:TP={tp}:LRA=11:measured_I={js['input_i']}:measured_TP={js['input_tp']}:"
          f"measured_LRA={js['input_lra']}:measured_thresh={js['input_thresh']}:offset={js['target_offset']}:linear=true")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-af", lf, "-ar", "48000", out_wav], check=True)
    os.remove(tmp)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("timeline")
    ap.add_argument("--out", required=True)
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--times", default=None)
    ap.add_argument("--from", dest="t_from", type=float, default=None)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    ap.add_argument("--sheet", default=None, help="also save a sheet every 0.25 s")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--lean", action="store_true")
    ap.add_argument("--mix-only", action="store_true", help="build <out>_mix.wav and stop (for chunked masters)")
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(args.timeline))
    tl = json.load(open(args.timeline, encoding="utf-8"))
    if not os.path.isabs(tl["brand"]):
        tl["brand"] = os.path.normpath(os.path.join(base, tl["brand"]))
    W, H = tl["size"]
    fps = int(tl.get("fps", 30))
    if args.preview:
        W, H, fps = W // 2, H // 2, 15
    film = Film(tl, W, H, fps, base)
    total = float(tl["duration"])
    if args.mix_only:
        mix = os.path.splitext(args.out)[0] + "_mix.wav"
        build_audio(tl, base, mix, total)
        print("done:", mix)
        return

    if args.times:
        ts = [float(v) for v in args.times.split(",") if v.strip()]
        ims = [(f"{t:.2f}s", Image.fromarray(film.frame(t).astype(np.uint8))) for t in ts]
        if len(ims) == 1:
            ims[0][1].save(args.out)
        else:
            sm.sheet_image(ims, W, H, cols=min(4, len(ims)), tw=480).save(args.out)
        print("done:", args.out)
        return

    f0 = int(round((args.t_from or 0.0) * fps))
    f1 = int(round((args.t_to if args.t_to is not None else total) * fps))
    windowed = args.t_from is not None or args.t_to is not None
    audio = os.path.splitext(args.out)[0] + "_mix.wav"
    use_audio = not args.no_audio and not windowed and tl.get("audio")
    if use_audio:
        build_audio(tl, base, audio, total)
        print("  audio:", audio, flush=True)
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
    if use_audio:
        cmd += ["-i", audio, "-c:a", "aac", "-b:a", "256k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "medium", "-movflags", "+faststart"]
    if args.lean:
        cmd += ["-threads", "2", "-x264-params", "rc-lookahead=10"]
    cmd += (["-shortest"] if use_audio else []) + [args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    sheet = []
    for k in range(f0, f1):
        t = k / fps
        fr = film.frame(t).astype(np.uint8)
        if k % fps == 0:
            film.release_takes(t)
        enc.stdin.write(fr.tobytes())
        if args.sheet and k % max(1, fps // 4) == 0:
            sheet.append((f"{t:.2f}s", Image.fromarray(fr)))
        if k % (fps * 2) == 0:
            print(f"  {t:5.1f}s / {total:.1f}s", flush=True)
    enc.stdin.close()
    enc.wait()
    if args.sheet and sheet:
        sm.sheet_image(sheet, W, H, cols=10, tw=240).save(args.sheet)
    print("done:", args.out, f"{W}x{H} @ {fps}fps")


if __name__ == "__main__":
    main()
