#!/usr/bin/env python3
"""
Montage layout (brand-agnostic): real footage, full bleed, cut on the music.

Footage fills the frame in its own colour, with only a light grade and grain to hold mixed sources
together. Cuts land on the beat of a real piece of music and tighten toward the peak. A sentence
builds line by line over the pictures, each line decoding out of random letters and settling. The
film ends on the brand's loader: on the brand's ground, letters settle out of noise into the
wordmark, the drawn logo takes over, and a small line of type lands beneath it.

The score is music, not synthesis: segments of a track placed on the timeline, a drop with a room
tail, a final chord under the logo, normalised to -14 LUFS with peaks under -1 dBFS.

  python montage.py board.json --out film.mp4                        # 1080x1920, 30 fps
  python montage.py board.json --out p.mp4 --preview --sheet p.png   # half size, 15 fps, same timing
  python montage.py board.json --out stills.png --stills             # one frame per shot, plus the end
  python montage.py board.json --out f.png --still 12.2              # one frame
  python montage.py board.json --out part.mp4 --from 15 --to 20      # a window
  python montage.py board.json --out film.mp4 --remux                # new audio, picture untouched
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import motion_engine as sm  # noqa: E402
import typeset  # noqa: E402
from lettering import hash2  # noqa: E402


def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def ease(x):  # css "ease", near enough
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def color(cfg, value, default):
    if value is None:
        return np.array(default, np.float32)
    if isinstance(value, (list, tuple)):
        return np.array(value, np.float32)
    named = cfg.get("colors") or {}
    if value in named:
        return np.array(named[value], np.float32)
    g, _, k = value.partition(".")
    return np.array(sm.GROUNDS[g][k or "bg"], np.float32)


# ----------------------------------------------------------------------------- footage
class Clip:
    """One shot's frames: cropped to the frame's aspect around `center`, scaled with room for a push."""

    def __init__(self, shot, W, H, fps):
        self.shot, self.W, self.H = shot, W, H
        src = shot["src"]
        wh = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                             "-of", "csv=p=0", src], capture_output=True, text=True).stdout
        sw, sh = (int(v) for v in wh.strip().split(",")[:2])
        z = shot.get("zoom", [1.0, 1.03])
        self.z0, self.z1 = (float(z), float(z)) if not isinstance(z, list) else (float(z[0]), float(z[1]))
        zmax = max(self.z0, self.z1, 1.0)
        self.ow, self.oh = int(math.ceil(W * zmax / 2) * 2), int(math.ceil(H * zmax / 2) * 2)
        aspect = W / H
        cw, ch = (int(round(sh * aspect)), sh) if sw / sh > aspect else (sw, int(round(sw / aspect)))
        crop = float(shot.get("crop", 1.0))  # < 1 punches in on the source
        cw, ch = int(cw * crop) // 2 * 2, int(ch * crop) // 2 * 2
        cx, cy = shot.get("center", [0.5, 0.5])
        x0 = int(np.clip(cx * sw - cw / 2, 0, sw - cw))
        y0 = int(np.clip(cy * sh - ch / 2, 0, sh - ch))
        vf = f"crop={cw}:{ch}:{x0}:{y0},scale={self.ow}:{self.oh}:flags=lanczos"
        if shot.get("flip"):
            vf += ",hflip"
        rate = float(shot.get("speed", 1.0))
        pre = f"setpts={1 / rate:.6f}*PTS," if rate != 1.0 else ""
        vf = pre + vf + f",fps={fps}"
        self.proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(shot.get("in", 0.0)), "-i", src,
                                      "-t", str(float(shot["dur"]) * rate + 0.5), "-vf", vf, "-an",
                                      "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.last = None

    def next(self, ts):
        n = self.ow * self.oh * 3
        buf = self.proc.stdout.read(n)
        if len(buf) == n:
            self.last = np.frombuffer(buf, np.uint8).reshape(self.oh, self.ow, 3)
        if self.last is None:
            self.last = np.zeros((self.oh, self.ow, 3), np.uint8)
        p = min(1.0, max(0.0, ts / max(1e-3, float(self.shot["dur"]))))
        z = self.z0 + (self.z1 - self.z0) * p
        # decoded at the widest zoom; zoom z shows a window of ow/z by oh/z, drifting by `drift` frames
        cw, ch = self.ow / z, self.oh / z
        dx, dy = self.shot.get("drift", [0.0, 0.0])
        cx = self.ow / 2 + dx * self.W * (p - 0.5)
        cy = self.oh / 2 + dy * self.H * (p - 0.5)
        cx = min(max(cx, cw / 2), self.ow - cw / 2)
        cy = min(max(cy, ch / 2), self.oh - ch / 2)
        if abs(cw - self.W) < 0.5 and abs(ch - self.H) < 0.5 and abs(cx - self.ow / 2) < 0.5 and abs(cy - self.oh / 2) < 0.5:
            x0, y0 = (self.ow - self.W) // 2, (self.oh - self.H) // 2
            return self.last[y0:y0 + self.H, x0:x0 + self.W]
        box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
        return np.asarray(Image.fromarray(self.last).resize((self.W, self.H), Image.BILINEAR, box=box))

    def close(self):
        try:
            self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait(timeout=2)
        except Exception:
            pass


def grade(img, g, shot):
    """Light and even: the footage keeps its own colour; this only brings mixed sources together."""
    x = img.astype(np.float32) / 255.0
    ev = float(shot.get("exposure", 0.0))
    if ev:
        x = x * (2.0 ** ev)
    lum = x @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    sat = float(shot.get("saturation", g.get("saturation", 0.95)))
    x = lum[..., None] + (x - lum[..., None]) * sat
    con = float(shot.get("contrast", g.get("contrast", 1.03)))
    x = (x - 0.5) * con + 0.5
    x = np.clip(x, 0, 1)
    gam = float(shot.get("gamma", g.get("gamma", 1.0)))
    if gam != 1.0:
        x = np.power(x, gam)
    # greens are the loudest colour in real footage (fields, leaves); they can sit back on their own
    gs = float(g.get("green_sat", 1.0))
    if gs != 1.0:
        lum2 = x @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        green = np.clip((x[..., 1] - np.maximum(x[..., 0], x[..., 2])) * 6.0, 0, 1)[..., None]
        x = x * (1 - green) + (lum2[..., None] + (x - lum2[..., None]) * gs) * green
    # a film curve: more shape through the mids, soft toe and shoulder
    curve = float(g.get("curve", 0.0))
    if curve:
        x = x + curve * (x * x * (3 - 2 * x) - x)
    # split toning toward the brand's own two tones: shadows lean to ink, highlights to paper
    lum3 = (x @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
    if g.get("shadow_tint") is not None:
        w = np.clip(1 - lum3, 0, 1) ** 2 * float(g.get("shadow_mix", 0.2))
        x = x * (1 - w) + (np.asarray(g["shadow_tint"], np.float32) / 255.0) * w
    if g.get("highlight_tint") is not None:
        w = np.clip(lum3, 0, 1) ** 2 * float(g.get("highlight_mix", 0.15))
        x = x * (1 - w) + (np.asarray(g["highlight_tint"], np.float32) / 255.0) * w
    lift = float(g.get("lift", 0.012))
    x = lift + (1 - lift) * x
    warm = float(shot.get("warmth", g.get("warmth", 0.0)))
    if warm:
        x = x * np.array([1 + 0.04 * warm, 1.0, 1 - 0.04 * warm], np.float32)
    return np.clip(x * 255.0, 0, 255)


# ----------------------------------------------------------------------------- type that decodes
class DecodeLine:
    """A line that writes itself: each character cycles random letters, then settles, left to right-ish."""

    LOWER = "abcdefghijklmnopqrstuvwxyz"
    UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    def __init__(self, text, path, px, axes, tracking, x, y, decode, seed, shadow):
        self.text, self.x, self.y, self.decode = text, x, y, decode
        self.line = typeset.set_line(text, path, px, axes, tracking)
        xs, adv = typeset.pen_positions(text, path, px, axes, tracking)
        self.xs = xs + [adv]
        n = len(text)
        k = np.arange(n)
        order = 0.55 * (k / max(1, n - 1)) + 0.45 * hash2(k, seed)
        self.settle = order * decode
        self.seed = seed
        self.shadow = shadow
        font = typeset.pil_font(path, px * 2, axes)
        self.glyphs = {}
        for ch in set(self.LOWER + self.UPPER + text):
            if ch.strip():
                self.glyphs[ch] = self._glyph(font, ch, px)
        h, w = self.line.alpha.shape
        # the settled line, split into per-character bands so settled characters show exact final pixels
        ox, _ = self.line.origin
        self.bands = []
        for i in range(n):
            a = ox + (self.xs[i] + self.xs[i + 1]) / 2 - (self.xs[i + 1] - self.xs[i]) / 2
            left = ox + (self.xs[i - 1] + self.xs[i]) / 2 if i > 0 else 0
            right = ox + (self.xs[i] + self.xs[i + 1]) / 2 if i < n - 1 else w
            self.bands.append((int(round(left)) if i > 0 else 0, int(round(right)) if i < n - 1 else w))
        blur = float(shadow.get("blur", 14)) * px / 100.0
        self.shadow_alpha = np.asarray(Image.fromarray((self.line.alpha * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(blur)), np.float32) / 255.0

    def _glyph(self, font, ch, px):
        from PIL import ImageDraw
        pad = int(px * 0.6) + 4
        size = (int(px * 2 * 1.4) + 2 * pad, int(px * 2 * 1.6) + 2 * pad)
        im = Image.new("L", size, 0)
        ImageDraw.Draw(im).text((pad, pad + px * 2 * 1.1), ch, font=font, fill=255, anchor="ls")
        small = im.resize((size[0] // 2, size[1] // 2), Image.LANCZOS)
        a = np.asarray(small, np.float32) / 255.0
        return a, (pad / 2, (pad + px * 2 * 1.1) / 2), font.getlength(ch) / 2

    def draw(self, frame, tl, text_color, shadow_color):
        """tl: seconds since this line appeared."""
        h, w = self.line.alpha.shape
        ox, oy = self.line.origin
        x0 = int(round(self.x - ox))
        y0 = int(round(self.y - oy))
        op = float(self.shadow.get("opacity", 0.45))
        dx, dy = self.shadow.get("offset", [0, 2])
        if tl >= self.decode:
            _blend(frame, self.shadow_alpha * op, x0 + int(dx), y0 + int(dy), shadow_color)
            _blend(frame, self.line.alpha, x0, y0, text_color)
            return
        mask = np.zeros_like(self.line.alpha)
        for i, ch in enumerate(self.text):
            if ch.strip() and tl >= self.settle[i]:
                a, b = self.bands[i]
                mask[:, a:b] = self.line.alpha[:, a:b]
        sh = np.asarray(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(
            float(self.shadow.get("blur", 14)) * 0.5)), np.float32) / 255.0
        _blend(frame, sh * op, x0 + int(dx), y0 + int(dy), shadow_color)
        _blend(frame, mask, x0, y0, text_color)
        step = int(tl / 0.05)
        for i, ch in enumerate(self.text):
            if not ch.strip() or tl >= self.settle[i] or tl < self.settle[i] - self.decode * 0.9:
                continue
            pool = self.UPPER if ch.isupper() else self.LOWER if ch.isalpha() else None
            if pool is None:
                continue
            r = pool[int(hash2(i + 101 * self.seed, step) * len(pool)) % len(pool)]
            a, (gx, gy), gw = self.glyphs[r]
            cw = self.xs[i + 1] - self.xs[i]
            px_ = self.x + self.xs[i] + (cw - gw) / 2 - gx
            py_ = self.y - gy
            _blend(frame, a * (0.35 + 0.35 * float(hash2(i, step + 29))), int(round(px_)), int(round(py_)), text_color)


class InkLine:
    """A line whose words ink in one after another, the way a page's mission statement does on scroll.
    mode "ink": opacity only. mode "rise": each word also lifts `rise` px as it inks."""

    def __init__(self, text, path, px, axes, tracking, x, y, stagger, fade, rise, shadow):
        self.x, self.y, self.stagger, self.fade, self.rise = x, y, stagger, fade, rise
        self.line = typeset.set_line(text, path, px, axes, tracking)
        xs, adv = typeset.pen_positions(text, path, px, axes, tracking)
        ox, _ = self.line.origin
        h, w = self.line.alpha.shape
        # word bands: from the middle of the space before a word to the middle of the space after it
        words, start = [], None
        for i, ch in enumerate(text + " "):
            if ch != " " and start is None:
                start = i
            elif ch == " " and start is not None:
                words.append((start, i))
                start = None
        self.bands = []
        for k, (a, b) in enumerate(words):
            left = 0 if k == 0 else int(round(ox + (xs[a - 1] + xs[a]) / 2))
            right = w if k == len(words) - 1 else int(round(ox + (xs[b] + (xs[b + 1] if b + 1 < len(xs) else adv)) / 2))
            self.bands.append((left, right))
        self.shadow = shadow
        blur = float(shadow.get("blur", 14)) * px / 100.0
        self.sh = np.asarray(Image.fromarray((self.line.alpha * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(blur)), np.float32) / 255.0

    def draw(self, frame, tl, text_color, shadow_color):
        ox, oy = self.line.origin
        x0, y0 = int(round(self.x - ox)), int(round(self.y - oy))
        op = float(self.shadow.get("opacity", 0.45))
        dx, dy = self.shadow.get("offset", [0, 2])
        for k, (a, b) in enumerate(self.bands):
            t = tl - k * self.stagger
            if t <= 0:
                continue
            o = ease_out(t / self.fade)
            lift = int(round(self.rise * (1 - o)))
            _blend(frame, self.sh[:, a:b] * op * o, x0 + a + int(dx), y0 + lift + int(dy), shadow_color)
            _blend(frame, self.line.alpha[:, a:b] * o, x0 + a, y0 + lift, text_color)


class LetterAtlas:
    """A to Z (or any alphabet) cut into cell-sized tiles, the way the site's loader stamps letters."""

    def __init__(self, font_path, axes, cw, ch, letters, ss=4):
        from PIL import ImageDraw
        self.cw, self.ch, self.letters = cw, ch, letters
        n = len(letters) + 1
        sheet = Image.new("L", (cw * ss * n, ch * ss), 0)
        d = ImageDraw.Draw(sheet)
        f = typeset.pil_font(font_path, max(3, round(ch * 0.74)) * ss, axes)
        for i, letter in enumerate(letters):
            wl = f.getlength(letter)
            d.text((cw * ss * (i + 1) + (cw * ss - wl) / 2, ch * ss / 2 + ch * 0.27 * ss), letter, font=f, fill=255, anchor="ls")
        a = np.asarray(sheet.resize((cw * n, ch), Image.LANCZOS), np.float32) / 255.0
        self.tiles = np.stack([a[:, k * cw:(k + 1) * cw] for k in range(n)])

    def render(self, A, G):
        rows, cols = A.shape
        pix = self.tiles[G] * A[..., None, None]
        return pix.transpose(0, 2, 1, 3).reshape(rows * self.ch, cols * self.cw)


class LetterLine:
    """Each word settles out of a fine field of letters, then the drawn type takes over: the site's loader,
    word by word. Same bands and shadow as InkLine."""

    def __init__(self, text, path, px, axes, tracking, x, y, stagger, shadow, atlas, settle, seed):
        self.ink = InkLine(text, path, px, axes, tracking, x, y, stagger, 0.2, 0, shadow)
        self.stagger, self.S, self.seed = stagger, settle, seed
        self.at = atlas
        la = self.ink.line.alpha
        h, w = la.shape
        cw, ch = atlas.cw, atlas.ch
        self.cols, self.rows = int(math.ceil(w / cw)) + 2, int(math.ceil(h / ch)) + 2
        canvas = np.zeros((self.rows * ch, self.cols * cw), np.float32)
        canvas[ch:ch + h, cw:cw + w] = la
        self.cover = canvas.reshape(self.rows, ch, self.cols, cw).mean(axis=(1, 3))
        ox, oy = self.ink.line.origin
        self.gx0 = int(round(x - ox)) - cw
        self.gy0 = int(round(y - oy)) - ch
        # which word owns each column of cells
        colw = np.full(self.cols, -1)
        for k, (a, b) in enumerate(self.ink.bands):
            c0, c1 = (a + cw) // cw, int(math.ceil((b + cw) / cw))
            colw[max(0, c0):min(self.cols, c1)] = k
        self.word = np.broadcast_to(colw[None, :], (self.rows, self.cols))
        n = self.rows * self.cols
        idx = np.arange(n).reshape(self.rows, self.cols)
        self.idx = idx
        self.set = hash2(idx, 5 + 31 * seed)
        rr, cc = np.mgrid[0:self.rows, 0:self.cols]
        dy = (rr + 0.5) / self.rows - 0.5
        self.field = smoothstep(0.5, 0.15, np.abs(dy))
        blur = float(shadow.get("blur", 14)) * px / 100.0
        self.blur = blur

    def draw(self, frame, tl, text_color, shadow_color):
        nw = len(self.ink.bands)
        if tl >= (nw - 1) * self.stagger + self.S + 0.3:
            self.ink.draw(frame, 99.0, text_color, shadow_color)  # settled: the plain line
            return
        N = len(self.at.letters)
        A = np.zeros((self.rows, self.cols), np.float32)
        G = np.zeros((self.rows, self.cols), np.int32)
        form = self.S * 0.82
        drawn = []
        for k in range(nw):
            t = tl - k * self.stagger
            if t <= 0:
                continue
            p = float(smoothstep(0.0, self.S, t))
            m = self.word == k
            noise, samp = int(t / 0.06), int(t / 0.11)
            i = self.idx
            c = self.cover
            inside = m & (c > 0.1)
            settled = inside & (p > self.set * 0.92)
            g_s = (1 + np.floor(hash2(i, 3 + self.seed) * N)).astype(np.int32)
            g_n = (1 + np.floor(hash2(i, noise + 17 + self.seed) * N)).astype(np.int32)
            a = np.where(settled, 0.42 + 0.58 * c,
                         np.where(inside & (hash2(i, noise + self.seed) < 0.7), 0.16 + 0.42 * hash2(i, noise + 29), 0.0))
            out = m & ~inside & (hash2(i, noise + 7 + self.seed) < 0.28 * self.field * (1 - p))
            a = np.where(out, 0.08 + 0.3 * hash2(i, noise + 41), a)
            fade = 1.0 - ease((t - form) / 0.25) if t > form else 1.0
            A = np.where(m, a * fade, A)
            G = np.where(m, np.where(settled, g_s, g_n), G)
            if t > form:
                drawn.append((k, ease((t - form) / 0.2)))
        lay = self.at.render(np.clip(A, 0, 1), G)
        if lay.max() > 0.003:
            op = float(self.ink.shadow.get("opacity", 0.45)) * 0.8
            sh = np.asarray(Image.fromarray((np.clip(lay, 0, 1) * 255).astype(np.uint8)).filter(
                ImageFilter.GaussianBlur(max(1.0, self.blur * 0.35))), np.float32) / 255.0
            _blend(frame, sh * op, self.gx0, self.gy0 + 1, shadow_color)
            _blend(frame, lay, self.gx0, self.gy0, text_color)
        ln = self.ink
        ox, oy = ln.line.origin
        x0, y0 = int(round(ln.x - ox)), int(round(ln.y - oy))
        op = float(ln.shadow.get("opacity", 0.45))
        dx, dy = ln.shadow.get("offset", [0, 2])
        for k, o in drawn:
            a, b = ln.bands[k]
            _blend(frame, ln.sh[:, a:b] * op * o, x0 + a + int(dx), y0 + int(dy), shadow_color)
            _blend(frame, ln.line.alpha[:, a:b] * o, x0 + a, y0, text_color)


class LetterScatter:
    """Small clouds of letters that scatter in, flicker and scatter out in different parts of the frame,
    all through the film: never a full overlay, never behind the words.

    Patches spawn on the beat, one to three alive at a time. Each is a loose blob of grid cells whose
    density falls away from its centre, so it reads as one soft object; its cells switch on one by one
    (centre first), flicker through the alphabet like the site's loader, then switch off at random."""

    def __init__(self, W, H, atlas, spec, beat, until, avoid, seed=11):
        self.W, self.H, self.at = W, H, atlas
        self.color = None
        rng = np.random.default_rng(seed)
        s = W / 1080.0
        rmin, rmax = spec.get("radius", [0.07, 0.17])
        lmin, lmax = spec.get("life", [0.9, 1.8])
        self.appear, self.vanish = float(spec.get("appear", 0.35)), float(spec.get("vanish", 0.4))
        ybox = spec.get("y", [0.16, 0.8])
        xbox = spec.get("x", [0.1, 0.9])
        t = float(spec.get("start", beat))
        self.patches = []
        while t < until - 0.4:
            life = float(rng.uniform(lmin, lmax))
            life = min(life, until - t)
            r = float(rng.uniform(rmin, rmax)) * W
            for _ in range(60):
                cx = float(rng.uniform(*xbox)) * W
                cy = float(rng.uniform(*ybox)) * H
                clear = True
                for (a, z, x0, y0, x1, y1) in avoid:  # boxes in frame fractions, active from a to z
                    if t < z and t + life > a:
                        nx = min(max(cx, x0 * W), x1 * W)
                        ny = min(max(cy, y0 * H), y1 * H)
                        if (cx - nx) ** 2 + (cy - ny) ** 2 < (r * 1.05) ** 2:
                            clear = False
                for p in self.patches:  # keep live patches apart
                    if t < p["t1"] and t + life > p["t0"] and (cx - p["cx"]) ** 2 + (cy - p["cy"]) ** 2 < (r + p["r"]) ** 2:
                        clear = False
                if clear:
                    break
            else:
                t += beat
                continue
            self.patches.append({"t0": t, "t1": t + life, "cx": cx, "cy": cy, "r": r, "k": len(self.patches)})
            t += beat * float(rng.choice(spec.get("every", [1, 1, 2])))
        self.alpha = spec.get("alpha", [0.3, 0.85])
        self.shadow = float(spec.get("shadow", 0.35))

    # words a random letter field must never spell in a row (a paused frame or a cover still can catch one)
    BLOCK = ("SEX", "ASS", "CUM", "TIT", "FAG", "FUK", "FCK", "NIG", "KKK", "XXX", "WTF", "HOE", "DIE", "GUN",
             "FUCK", "SHIT", "PISS", "DICK", "COCK", "CUNT", "PORN", "SLUT", "RAPE", "ANAL", "BOOB", "JIZZ", "DAMN",
             "NAZI", "KILL", "DEAD", "TWAT", "WANK", "DYKE", "SPIC", "KIKE", "COON", "GOOK", "MILF", "HOMO", "ISIS",
             "WHORE", "PUSSY", "NIGGA", "CHINK", "RETARD", "HITLER")

    def _row(self, g, live):
        return "".join(self.at.letters[v - 1].upper() if on else " " for v, on in zip(g, live))

    def _clean(self, g, live, ix):
        """Re-letter the middle of any blocked word the live cells spell left to right, until none is left."""
        N = len(self.at.letters)
        for it in range(8):
            hit = False
            for r in range(g.shape[0]):
                s = self._row(g[r], live[r])
                for w in self.BLOCK:
                    j = s.find(w)
                    while j >= 0:
                        m = j + len(w) // 2
                        g[r, m] = 1 + (g[r, m] + int(hash2(ix[r, m], 41 + it) * (N - 1))) % N  # always a different letter
                        hit = True
                        j = s.find(w, j + 1)
            if not hit:
                break
        return g

    def cells(self, p, t, clean=True):
        """What patch p shows at time t: (live mask, letter grid, alpha, first column, first row), or None."""
        cw, ch = self.at.cw, self.at.ch
        N = len(self.at.letters)
        ts, life = t - p["t0"], p["t1"] - p["t0"]
        c0, c1 = int((p["cx"] - p["r"]) // cw), int((p["cx"] + p["r"]) // cw) + 1
        r0, r1 = int((p["cy"] - p["r"]) // ch), int((p["cy"] + p["r"]) // ch) + 1
        c0, r0 = max(0, c0), max(0, r0)
        c1, r1 = min(self.W // cw, c1), min(self.H // ch, r1)
        if c1 <= c0 or r1 <= r0:
            return None
        rr, cc = np.mgrid[r0:r1, c0:c1]
        ix = (rr * 4099 + cc) + 7919 * p["k"]
        dx = ((cc + 0.5) * cw - p["cx"]) / p["r"]
        dy = ((rr + 0.5) * ch - p["cy"]) / p["r"]
        d = np.sqrt(dx * dx + dy * dy)
        # a soft, slightly lumpy blob: density falls away from the centre
        lump = 0.75 + 0.5 * hash2(ix // 3, 13)
        dens = np.clip(1 - (d * lump) ** 1.6, 0, 1)
        on = hash2(ix, 5) < dens * 0.9
        # cells arrive centre first and leave at random: the patch scatters in and out
        arrive = self.appear * np.clip(d * 0.8 + 0.3 * hash2(ix, 17), 0, 1)
        leave = life - self.vanish * hash2(ix, 23)
        live = on & (ts >= arrive) & (ts < leave)
        if not live.any():
            return None
        step = int(t / 0.07)
        g = (1 + np.floor(hash2(ix, step + 3) * N)).astype(np.int32)
        keep = hash2(ix, step) < 0.55  # about half the letters hold for a beat, the rest flicker
        g = np.where(keep, (1 + np.floor(hash2(ix, 3) * N)).astype(np.int32), g)
        if clean:
            g = self._clean(g, live, ix)
        a = (self.alpha[0] + (self.alpha[1] - self.alpha[0]) * hash2(ix, 29)) * live
        return live, g, a, c0, r0

    def scan(self, fps, t_end, clean=True):
        """Every blocked word the clouds spell in a row, frame by frame: [(t, word, x, y)] in pixels."""
        found = []
        for f in range(int(round(t_end * fps))):
            t = f / fps
            for p in self.patches:
                if not (p["t0"] <= t < p["t1"]):
                    continue
                got = self.cells(p, t, clean)
                if got is None:
                    continue
                live, g, _, c0, r0 = got
                for r in range(g.shape[0]):
                    s = self._row(g[r], live[r])
                    for w in self.BLOCK:
                        j = s.find(w)
                        while j >= 0:
                            found.append((t, w, (c0 + j) * self.at.cw, (r0 + r) * self.at.ch))
                            j = s.find(w, j + 1)
        return found

    def draw(self, frame, t, color, shadow_color):
        cw, ch = self.at.cw, self.at.ch
        for p in self.patches:
            if not (p["t0"] <= t < p["t1"]):
                continue
            got = self.cells(p, t)
            if got is None:
                continue
            live, g, a, c0, r0 = got
            lay = self.at.render(a.astype(np.float32), g)
            x0, y0 = c0 * cw, r0 * ch
            if self.shadow > 0:
                sh = np.asarray(Image.fromarray((np.clip(lay, 0, 1) * 255).astype(np.uint8)).filter(
                    ImageFilter.GaussianBlur(max(1.0, cw * 0.35))), np.float32) / 255.0
                _blend(frame, sh * self.shadow, x0, y0 + 1, shadow_color)
            _blend(frame, lay, x0, y0, color)


class LetterCut:
    """A cut that writes itself in: for a few frames the incoming shot appears as the letter field,
    its own light deciding each letter's ink, then the picture takes over."""

    def __init__(self, W, H, atlas, ground, ink, seconds, floor=0.22):
        self.W, self.H, self.at, self.d = W, H, atlas, seconds
        self.cols, self.rows = W // atlas.cw, H // atlas.ch
        self.ox, self.oy = (W - self.cols * atlas.cw) // 2, (H - self.rows * atlas.ch) // 2
        self.ground, self.ink, self.floor = np.asarray(ground, np.float32), np.asarray(ink, np.float32), floor
        n = self.rows * self.cols
        self.idx = np.arange(n).reshape(self.rows, self.cols)

    def apply(self, frame, ts):
        """frame: the graded picture (float RGB). ts: seconds since the cut."""
        if ts >= self.d:
            return frame
        cw, ch = self.at.cw, self.at.ch
        region = frame[self.oy:self.oy + self.rows * ch, self.ox:self.ox + self.cols * cw]
        lum = (region @ np.array([0.2126, 0.7152, 0.0722], np.float32)) / 255.0
        cells = lum.reshape(self.rows, ch, self.cols, cw).mean(axis=(1, 3))
        a = np.clip(self.floor + 0.9 * cells ** 0.8, 0, 1) * (0.82 + 0.18 * hash2(self.idx, 7))
        step = int(ts / 0.05)
        G = (1 + np.floor(hash2(self.idx, 3 + step) * len(self.at.letters))).astype(np.int32)
        G = np.where(a < 0.26, 0, G)
        lay = self.at.render(a, G)[..., None]
        letters = np.empty_like(frame)
        letters[:] = self.ground
        sl = letters[self.oy:self.oy + self.rows * ch, self.ox:self.ox + self.cols * cw]
        sl[:] = sl * (1 - lay) + self.ink * lay
        k = ease(ts / self.d)  # the picture takes over as the letters settle
        return letters * (1 - k) + frame * k


def _blend(frame, alpha, x0, y0, col):
    H, W = frame.shape[:2]
    h, w = alpha.shape
    sx, sy = max(0, -x0), max(0, -y0)
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(W, x0 + w), min(H, y0 + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    a = alpha[sy:sy + fy1 - fy0, sx:sx + fx1 - fx0][..., None]
    frame[fy0:fy1, fx0:fx1] = frame[fy0:fy1, fx0:fx1] * (1 - a) + np.asarray(col, np.float32) * a


# ----------------------------------------------------------------------------- the loader end card
class Loader:
    """The brand's first view, as a film's last: letters settle out of noise into the wordmark."""

    def __init__(self, W, H, brand, spec):
        self.W, self.H, self.spec = W, H, spec
        cfg = brand.get("cfg") or {}
        lf = dict(cfg.get("letter_field") or {})
        self.letters = list(spec.get("alphabet", lf.get("alphabet", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")))
        font = brand["fonts"][lf.get("font", "sans")]
        axes = lf.get("axes")
        lg = spec.get("logo", {})
        img = Image.open(lg.get("src") or brand["logo"]).convert("RGBA")
        img = img.crop(img.getchannel("A").getbbox())
        w = int(round(W * float(lg.get("width", 0.6))))
        h = int(round(img.height * w / img.width))
        self.logo_a = np.asarray(img.getchannel("A").resize((w, h), Image.LANCZOS), np.float32) / 255.0
        self.lx, self.ly = (W - w) // 2, int(round(H * float(lg.get("y", 0.5)) - h / 2))
        self.lw, self.lh = w, h
        # the canvas hangs off the mark: left -8%, top -55%, 116% wide, 210% tall (Loader.css)
        cx0, cy0 = self.lx - 0.08 * w, self.ly - 0.55 * h
        cwid, chei = 1.16 * w, 2.10 * h
        dpr = float(spec.get("dpr", 2.0)) * W / 1080.0
        cell = min(10.0, max(4.5, (w / dpr) / 86.0)) * dpr   # clamp(logo.width / 86, 4.5, 10), in device px
        self.cw = max(3, int(round(cell)))
        self.ch = max(4, int(round(cell * 1.18)))
        self.cols, self.rows = int(cwid // self.cw), int(chei // self.ch)
        self.ox = int(round(cx0 + (cwid - self.cols * self.cw) / 2))
        self.oy = int(round(cy0 + (chei - self.rows * self.ch) / 2))
        n = self.cols * self.rows
        idx = np.arange(n)
        col, row = idx % self.cols, idx // self.cols
        full = np.zeros((H, W), np.float32)
        full[self.ly:self.ly + h, self.lx:self.lx + w] = self.logo_a
        # coverage of each cell by the wordmark
        cov = np.zeros(n, np.float32)
        for i in range(n):
            y0 = self.oy + row[i] * self.ch
            x0 = self.ox + col[i] * self.cw
            if 0 <= y0 < H and 0 <= x0 < W:
                cov[i] = full[y0:y0 + self.ch, x0:x0 + self.cw].mean()
        self.cover = cov
        self.settle = hash2(idx, 5)
        dx = (col + 0.5) / self.cols - 0.5
        dy = (row + 0.5) / self.rows - 0.5
        self.field = smoothstep(0.5, 0.1, np.abs(dx)) * smoothstep(0.5, 0.12, np.abs(dy))
        self.idx = idx
        self.tiles = self._atlas(font, axes)

    def _atlas(self, font_path, axes, ss=4):
        from PIL import ImageDraw
        cw, ch = self.cw * ss, self.ch * ss
        n = len(self.letters) + 1
        sheet = Image.new("L", (cw * n, ch), 0)
        d = ImageDraw.Draw(sheet)
        f = typeset.pil_font(font_path, round(self.ch * 0.74) * ss, axes)
        for i, letter in enumerate(self.letters):
            wl = f.getlength(letter)
            d.text((cw * (i + 1) + (cw - wl) / 2, ch / 2 + self.ch * 0.27 * ss), letter, font=f, fill=255, anchor="ls")
        a = np.asarray(sheet.resize((self.cw * n, self.ch), Image.LANCZOS), np.float32) / 255.0
        return np.stack([a[:, k * self.cw:(k + 1) * self.cw] for k in range(n)])

    def cells(self, t):
        """Alpha and glyph slot per cell at t seconds, exactly as the site's Loader draws them."""
        S = float(self.spec.get("settle", 1.15))
        p = float(smoothstep(0.0, S, t))
        N = len(self.letters)
        noise = int(t / 0.06)
        samp = int(t / 0.11)
        i = self.idx
        c = self.cover
        a = np.zeros(len(i), np.float32)
        g = np.zeros(len(i), np.int32)
        inside = c > 0.1
        settled = inside & (p > self.settle * 0.92)
        g_s = (1 + np.floor(hash2(i, 3) * N)).astype(np.int32)
        swap = hash2(i, samp) < 0.012
        g_s = np.where(swap, (1 + np.floor(hash2(i, samp + 9) * N)).astype(np.int32), g_s)
        g_n = (1 + np.floor(hash2(i, noise + 17) * N)).astype(np.int32)
        a_n = 0.16 + 0.42 * hash2(i, noise + 29)
        on_n = hash2(i, noise) < 0.7
        a = np.where(settled, 0.42 + 0.58 * c, np.where(inside & on_n, a_n, 0.0))
        g = np.where(settled, g_s, g_n)
        outside = ~inside & (hash2(i, noise) < 0.3 * self.field * (1 - p))
        a = np.where(outside, 0.08 + 0.3 * hash2(i, noise + 29), a)
        return a.astype(np.float32), g

    def draw(self, frame, t, ink, ground):
        S = float(self.spec.get("settle", 1.15))
        form = S * float(self.spec.get("form_at", 0.82))
        scale = float(self.spec.get("scale_from", 0.965))
        s = scale + (1 - scale) * ease_out(t / float(self.spec.get("scale_time", 1.8)))
        a, g = self.cells(t)
        canvas_op = 1.0
        if t > form:
            canvas_op = 1.0 - ease((t - form - 0.05) / float(self.spec.get("letters_out", 0.7)))
        logo_op = ease((t - form) / float(self.spec.get("logo_in", 0.55))) if t > form else 0.0
        layer = np.zeros((self.rows * self.ch, self.cols * self.cw), np.float32)
        if canvas_op > 0.002:
            A = (a * canvas_op).reshape(self.rows, self.cols)
            pix = self.tiles[g.reshape(self.rows, self.cols)] * A[..., None, None]
            layer = pix.transpose(0, 2, 1, 3).reshape(self.rows * self.ch, self.cols * self.cw)
        # the logo lives in the same layer so the whole mark scales together
        lay = np.zeros_like(layer)
        ly0, lx0 = self.ly - self.oy, self.lx - self.ox
        if logo_op > 0:
            h, w = self.logo_a.shape
            y0, x0 = max(0, ly0), max(0, lx0)
            y1, x1 = min(lay.shape[0], ly0 + h), min(lay.shape[1], lx0 + w)
            lay[y0:y1, x0:x1] = self.logo_a[y0 - ly0:y1 - ly0, x0 - lx0:x1 - lx0] * logo_op
        comb = 1 - (1 - layer) * (1 - lay)
        if abs(s - 1.0) > 1e-4:  # scale the whole mark about its centre, sub-pixel smooth
            im = Image.fromarray(np.clip(comb, 0, 1).astype(np.float32), "F")
            cx, cy = (self.lx - self.ox) + self.lw / 2, (self.ly - self.oy) + self.lh / 2
            k = 1.0 / s
            im = im.transform(im.size, Image.AFFINE, (k, 0, cx - cx * k, 0, k, cy - cy * k), resample=Image.BILINEAR)
            comb = np.asarray(im, np.float32)
        _blend(frame, comb, self.ox, self.oy, ink)


# ----------------------------------------------------------------------------- music
def load_audio(path, sr=48000):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).astype(np.float64)


def build_music(board, total, out_wav, sr=48000):
    """Segments of a track on the timeline, a room tail after the drop, normalised like the kit's scores."""
    m = board["music"]
    src = load_audio(m["src"], sr)
    mix = np.zeros((int(total * sr), 2))
    for seg in m["segments"]:
        a, z, at = float(seg["from"]), float(seg["to"]), float(seg["at"])
        piece = src[int(a * sr):int(z * sr)].copy()
        n = len(piece)
        fi, fo = float(seg.get("fade_in", 0.005)), float(seg.get("fade_out", 0.02))
        env = np.ones(n)
        if fi > 0:
            k = min(n, int(fi * sr))
            env[:k] = np.linspace(0, 1, k) ** 2
        if fo > 0:
            k = min(n, int(fo * sr))
            env[n - k:] *= np.linspace(1, 0, k) ** 2
        piece *= env[:, None] * 10 ** (float(seg.get("gain_db", 0.0)) / 20)
        i0 = int(at * sr)
        j = min(len(mix), i0 + n)
        mix[i0:j] += piece[:j - i0]
        if seg.get("tail"):  # the music leaves the room ringing when it cuts
            tl = float(seg["tail"])
            k = int(0.35 * sr)
            last = piece[max(0, n - k):]
            irl, irr = sm.reverb_ir(sr, rt60=float(seg.get("tail_rt60", 2.4)))
            wet = np.stack([sm._fftconv(last[:, 0], irl), sm._fftconv(last[:, 1], irr)], 1)
            wet = wet[:int(tl * sr) + k]
            wet *= np.linspace(1, 0, len(wet))[:, None] ** 1.5 * float(seg.get("tail_gain", 0.6))
            t0 = i0 + n - len(last)
            j = min(len(mix), t0 + len(wet))
            mix[t0:j] += wet[:j - t0]
    fade = int(float(m.get("end_fade", 0.4)) * sr)
    if fade:
        mix[-fade:] *= np.linspace(1, 0, fade)[:, None]
    sm.write_wav(out_wav, mix / (np.abs(mix).max() + 1e-9) * 0.9, sr)
    lufs = sm.measure_lufs(out_wav)
    target = float(m.get("target_lufs", -14.0))
    if lufs is not None:
        mix = mix / (np.abs(mix).max() + 1e-9) * 0.9 * 10 ** ((target - lufs) / 20)
        knee, ceil = 0.6, float(m.get("ceiling", 0.84))  # AAC adds inter-sample peaks; 0.84 keeps true peak under -1 dBFS
        a = np.abs(mix)
        over = a > knee
        mix[over] = np.sign(mix[over]) * (knee + (ceil - knee) * np.tanh((a[over] - knee) / (ceil - knee)))
        sm.write_wav(out_wav, mix, sr)
    return sm.measure_lufs(out_wav)


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Render a full-bleed montage storyboard.")
    ap.add_argument("storyboard")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--brand", default="")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--stills", action="store_true")
    ap.add_argument("--still", type=float, default=None)
    ap.add_argument("--from", dest="t_from", type=float, default=None)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--remux", action="store_true")
    ap.add_argument("--lean", action="store_true", help="low-memory encode for small machines")
    ap.add_argument("--scan-letters", action="store_true", help="list words the letter clouds would spell, then stop")
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(args.storyboard))
    board = sm.resolve_paths(json.load(open(args.storyboard, encoding="utf-8")), base)
    if board.get("music", {}).get("src") and not os.path.isabs(board["music"]["src"]):
        board["music"]["src"] = os.path.normpath(os.path.join(base, board["music"]["src"]))
    W, H = (int(v) for v in args.size.lower().split("x"))
    fps = args.fps
    if args.preview:
        W, H, fps = (W // 2) & ~1, (H // 2) & ~1, 15
    brand = sm.load_brand(sm.find_brand(args.brand, args.storyboard))
    cfg = brand.get("cfg") or {}
    scale = W / 1080.0

    # timeline
    t, shots = 0.0, []
    for s in board["shots"]:
        dur = float(s["seconds"])
        shots.append(dict(s, start=t, end=t + dur, dur=dur))
        t += dur
    end = board.get("end") or {}
    end_at = t
    total = t + float(end.get("seconds", 0.0))
    g = dict(cfg.get("footage_grade") or {}, **(board.get("grade") or {}))

    # type
    L = board.get("lines") or {}
    lines = []
    if L:
        role = L.get("font", "sans")
        path = brand["fonts"][role]
        axes = L.get("axes", (brand.get("font_axes") or {}).get(role))
        size = float(L.get("size", 96)) * scale
        lead = float(L.get("leading", 1.06)) * size
        mode = L.get("mode", "ink")
        lf = cfg.get("letter_field") or {}
        letters = list(L.get("alphabet", lf.get("alphabet", "ABCDEFGHIJKLMNOPQRSTUVWXYZ")))
        if mode == "letters":
            cw_ = max(3, int(round(float(L.get("letter_cell", 6)) * scale)))
            word_atlas = LetterAtlas(brand["fonts"][lf.get("font", "sans")], lf.get("axes"), cw_, max(4, int(round(cw_ * 1.18))), letters)
        for k, it in enumerate(L["items"]):
            y = float(L.get("y", 0.4)) * H + k * lead
            x = float(L.get("x", 0.083)) * W
            tr = float(L.get("tracking", -0.03))
            if mode == "letters":
                obj = LetterLine(it["text"], path, size, axes, tr, x, y, float(L.get("stagger", 0.1)), L.get("shadow", {}),
                                 word_atlas, float(L.get("settle", 0.45)), k + 1)
            elif mode == "decode":
                obj = DecodeLine(it["text"], path, size, axes, tr, x, y, float(L.get("decode", 0.4)), k + 1, L.get("shadow", {}))
            else:
                obj = InkLine(it["text"], path, size, axes, tr, x, y, float(L.get("stagger", 0.1)), float(L.get("fade", 0.28)),
                              float(L.get("rise", 10 if mode == "rise" else 0)) * scale, L.get("shadow", {}))
            lines.append((float(it["at"]), obj))
        until = float(L.get("until", end_at))
        text_col = color(cfg, L.get("color"), (245, 245, 243))
        shadow_col = color(cfg, (L.get("shadow") or {}).get("color"), (10, 10, 9))
    scrim = None
    sc = (L or {}).get("scrim")
    if lines and sc is not False:
        sc = sc or {}
        # where the words sit: the light measured there decides how far a bright shot dips
        top = int(float(L.get("y", 0.4)) * H - size * 1.0)
        bot = int(float(L.get("y", 0.4)) * H + lead * (len(lines) - 1) + size * 0.45)
        lft = int(float(L.get("x", 0.083)) * W - 0.04 * W)
        rgt = int(min(W, lft + 0.8 * W))
        scrim = {"box": (max(0, top), min(H, bot), max(0, lft), rgt),
                 "from": float(sc.get("from", 0.45)), "max": float(sc.get("max", 0.3))}

    # cuts that write themselves in from the letter field (a few, at the film's turns)
    lc_spec = board.get("letter_cuts") or {}
    letter_cut, lc_shots = None, set(int(v) for v in lc_spec.get("shots", []))
    if lc_shots:
        lfc = cfg.get("letter_field") or {}
        cwc = max(3, int(round(float(lc_spec.get("cell", 12)) * scale)))
        atl = LetterAtlas(brand["fonts"][lfc.get("font", "sans")], lfc.get("axes"), cwc, max(4, int(round(cwc * 1.18))),
                          list(lc_spec.get("alphabet", lfc.get("alphabet", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))))
        letter_cut = LetterCut(W, H, atl, color(cfg, lc_spec.get("ground", "ink"), (27, 27, 25)),
                               color(cfg, lc_spec.get("ink", "paper"), (245, 245, 243)), float(lc_spec.get("seconds", 0.2)),
                               float(lc_spec.get("floor", 0.22)))

    # small clouds of letters that come and go through the film, never behind the words
    scatter = None
    sc_spec = board.get("letter_scatter")
    if sc_spec:
        lfs = cfg.get("letter_field") or {}
        cws = max(3, int(round(float(sc_spec.get("cell", 11)) * scale)))
        atl_s = LetterAtlas(brand["fonts"][lfs.get("font", "sans")], lfs.get("axes"), cws, max(4, int(round(cws * 1.18))),
                            list(sc_spec.get("alphabet", lfs.get("alphabet", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"))))
        avoid = [tuple(b) for b in sc_spec.get("avoid", [])]
        if lines:  # the headline block, padded, from its first word to the drop
            yb0 = float(L.get("y", 0.4)) - float(L.get("size", 96)) * scale * 1.05 / H
            yb1 = float(L.get("y", 0.4)) + (lead * (len(lines) - 1) + size * 0.5) / H
            avoid.append((min(at for at, _ in lines) - 0.6, until, float(L.get("x", 0.083)) - 0.05, yb0, 0.9, yb1))
        scatter = LetterScatter(W, H, atl_s, sc_spec, float(sc_spec.get("beat", 0.5)),
                                float(sc_spec.get("until", end_at)), avoid, int(sc_spec.get("seed", 11)))
        sc_color = color(cfg, sc_spec.get("color"), (245, 245, 243))
        sc_shadow = color(cfg, sc_spec.get("shadow_color"), (10, 10, 9))
        print(f"  letter scatter: {len(scatter.patches)} patches")
        if args.scan_letters:  # report blocked words, raw and after the filter, then stop (no footage is read)
            for clean in (False, True):
                hits = scatter.scan(fps, float(sc_spec.get("until", end_at)), clean)
                spots = {}
                for t_, w, x_, y_ in hits:
                    spots.setdefault((w, x_, y_), []).append(t_)
                print(f"  {'filtered' if clean else 'raw'}: {len(hits)} word frames in {len(spots)} places")
                for (w, x_, y_), ts_ in sorted(spots.items(), key=lambda kv: kv[1][0]):
                    print(f"    {w:<6} x {x_:>4} y {y_:>4}  {len(ts_):>3} frames  {ts_[0]:.2f} to {ts_[-1]:.2f} s")
            return

    loader = Loader(W, H, brand, end) if end else None
    ground = np.array(sm.GROUNDS[end.get("ground", "paper")]["bg"], np.float32) if end else None
    ink = color(cfg, end.get("ink"), sm.GROUNDS[end.get("ground", "paper")]["text"]) if end else None
    end_texts = []
    for it in end.get("texts", []):
        role = it.get("font", "mono")
        ln = typeset.set_line(it["text"], brand["fonts"][role], float(it.get("size", 30)) * scale,
                              it.get("axes", (brand.get("font_axes") or {}).get(role)), float(it.get("tracking", 0.0)))
        end_texts.append(dict(it, line=ln, col=color(cfg, it.get("color"), ink)))

    rng = np.random.default_rng(board.get("seed", 3))
    gstd = float(board.get("film_grain", 2.6))
    grain = [rng.normal(0, gstd, (H, W, 1)).astype(np.float32) for _ in range(6)] if gstd > 0 else None

    # audio
    audio = os.path.splitext(args.out)[0] + "_music.wav"
    windowed = args.t_from is not None or args.t_to is not None
    still_mode = args.stills or args.still is not None
    if not still_mode and not args.no_audio and not windowed and board.get("music"):
        lufs = build_music(board, total, audio)
        print(f"  music: {audio} ({lufs} LUFS)")
        if args.remux:
            tmp = os.path.splitext(args.out)[0] + "_remux.mp4"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", args.out, "-i", audio, "-map", "0:v", "-map", "1:a",
                            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp],
                           check=True)
            os.replace(tmp, args.out)
            print("remuxed:", args.out)
            return

    nframes = int(round(total * fps))
    if args.stills:
        want = sorted({int(round((s["start"] + s["dur"] * 0.5) * fps)) for s in shots} |
                      {int(round((end_at + x) * fps)) for x in (0.4, 0.9, 1.3, 2.4)} if end else set())
    elif args.still is not None:
        want = [int(round(args.still * fps))]
    else:
        f0 = int(round((args.t_from or 0.0) * fps))
        f1 = int(round((args.t_to if args.t_to is not None else total) * fps))
        want = list(range(f0, min(nframes, f1)))
    wanted = set(want)
    last_frame = max(want) if want else 0

    enc = None
    if not still_mode:
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
        use_audio = not args.no_audio and not windowed and board.get("music")
        if use_audio:
            cmd += ["-i", audio, "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-maxrate", "24M", "-bufsize", "48M",
                "-preset", "medium", "-movflags", "+faststart"]
        if args.lean:  # an 8 GB machine: fewer encoder threads and a short lookahead, same CRF, a little slower
            cmd += ["-threads", "2", "-x264-params", "rc-lookahead=10"]
        cmd += (["-shortest"] if use_audio else []) + [args.out]
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    # one rule for which shot owns a frame, used both to plan and to draw (cuts land on whole frames)
    sf = [int(round(s["start"] * fps)) for s in shots]
    ef = int(round(end_at * fps))
    owner = lambda f: max(i for i in range(len(shots)) if sf[i] <= f)
    needed = {owner(f) for f in wanted if f < ef}
    clip, clip_k = None, None
    frames_out, sheet = [], []
    for fi in range(0, last_frame + 1):
        t = fi / fps
        draw = fi in wanted
        if fi < ef:
            k = owner(fi)
            s = shots[k]
            ts = t - s["start"]
            if clip_k != k:
                if clip is not None:
                    clip.close()
                clip, clip_k = (Clip(s, W, H, fps), k) if k in needed else (None, k)
            if clip is None:
                continue
            img = clip.next(ts)
            if not draw:
                continue
            frame = grade(img, g, s)
            if letter_cut is not None and k in lc_shots:
                frame = letter_cut.apply(frame, (fi - sf[k]) / fps)
            live = [(at, dl) for at, dl in lines if at <= t < until]
            if live and scrim is not None:  # bright pictures sit back a little behind the words, only as much as they need
                y0, y1, x0, x1 = scrim["box"]
                lum = float((frame[y0:y1, x0:x1] @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean()) / 255.0
                k = float(np.clip((lum - scrim["from"]) / (1 - scrim["from"]), 0, 1)) * scrim["max"]
                if k > 0.005:  # the whole shot dips, so there is no shape to see, only a darker take
                    frame *= (1 - k)
            if scatter is not None:  # after the dip, so the letters keep their paper white
                scatter.draw(frame, t, sc_color, sc_shadow)
            for at, dl in live:
                dl.draw(frame, t - at, text_col, shadow_col)
            label = str(s.get("id", k))
        else:
            if clip is not None:
                clip.close()
                clip = None
            if not draw:
                continue
            te = max(0.0, t - end_at)
            frame = np.empty((H, W, 3), np.float32)
            frame[:] = ground
            loader.draw(frame, te, ink, ground)
            for it in end_texts:
                at = float(it.get("at", 1.4))
                if te >= at:
                    op = min(1.0, (te - at) / max(1e-3, float(it.get("fade", 0.3))))
                    typeset.place(frame, it["line"], float(it.get("x", 0.5)) * W, float(it.get("y", 0.57)) * H,
                                  it["col"], op, it.get("anchor", "ms"))
            label = "end"
        out = frame if grain is None else frame + grain[fi % len(grain)]
        out = np.clip(out, 0, 255).astype(np.uint8)
        if enc is not None:
            enc.stdin.write(out.tobytes())
            if args.sheet and fi % max(1, fps // 2) == 0:
                sheet.append((f"{t:.1f}s", Image.fromarray(out)))
            if fi % (fps * 2) == 0:
                print(f"  {t:5.1f}s / {total:.1f}s", flush=True)
        else:
            frames_out.append((f"{t:.2f}s {label}", Image.fromarray(out)))
    if clip is not None:
        clip.close()
    if enc is not None:
        enc.stdin.close()
        enc.wait()
        if args.sheet and sheet:
            sm.sheet_image(sheet, W, H).save(args.sheet)
        print("done:", args.out, f"{W}x{H} @ {fps}fps, {total:.2f}s")
    elif args.still is not None:
        frames_out[0][1].save(args.out)
        print("done:", args.out)
    else:
        sm.sheet_image(frames_out, W, H, cols=min(8, len(frames_out)), tw=240).save(args.out)
        print("done:", args.out, len(frames_out), "frames")


if __name__ == "__main__":
    main()
