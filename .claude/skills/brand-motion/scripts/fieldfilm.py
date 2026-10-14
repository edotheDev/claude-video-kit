#!/usr/bin/env python3
"""
Field films (brand-agnostic): every shot is the brand's letter field drawing one figure.

The field is a grid of letters over a ground, the palest cells falling back to a dot (brand.json
"letter_field"). A shot names a figure: a giant letter set in one of the brand's fonts, the brand's
logo, or none (an empty page of dots). A shot either

  resolve  starts from static (every cell a random letter, flickering) and settles into its figure,
           cells locking in random order the way the brand's loader resolves, or
  refine   carries the letters it inherits and sharpens them: on each step the grid moves to a finer
           cell, every new cell starts from the coarse picture it sits in and settles to its own
           coverage, so the figure gets sharper instead of starting again, or
  hold     shows the drawn figure only (an end card).

The drawn logo can take over from its letters. Lines of type are set with HarfBuzz and clear the
field around themselves. Music is placed as segments of a real track (montage.build_music).

  python fieldfilm.py board.json --out film.mp4                     # 1080x1920, 30 fps
  python fieldfilm.py board.json --out film.mp4 --size 1080x1350    # 4:5 (give it its own board)
  python fieldfilm.py board.json --out p.mp4 --preview --sheet p.png
  python fieldfilm.py board.json --out stills.png --stills
  python fieldfilm.py board.json --out f.png --still 12.2
  python fieldfilm.py board.json --out part.mp4 --from 8 --to 14
  python fieldfilm.py board.json --out film.mp4 --remux             # new music, picture untouched
  python fieldfilm.py board.json --scan-letters                     # blocked words the field would spell
"""
import argparse
import json
import math
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import motion_engine as sm  # noqa: E402
import montage  # noqa: E402
import typeset  # noqa: E402
from lettering import hash2  # noqa: E402

BLOCK = montage.LetterScatter.BLOCK
CODES = ("1488", "88", "666", "69", "420")  # number codes a mixed field must not show either
LEET = {"A": "4@", "B": "8", "E": "3", "G": "9", "I": "1!", "O": "0", "S": "5$", "T": "7+", "Z": "2"}


def _blocked_regex():
    """One pattern for every blocked word in plain and look-alike spellings; * stands for any letter
    after the first. The lookahead finds overlapping hits."""
    import re
    alts = []
    for w in BLOCK:
        parts = []
        for i, c in enumerate(w):
            cls = re.escape(c) + re.escape(LEET.get(c, ""))
            parts.append("[" + cls + ("*" if i else "") + "]")
        alts.append("".join(parts))
    alts += [re.escape(c) for c in CODES]
    return re.compile("(?=(" + "|".join(sorted(alts, key=len, reverse=True)) + "))")


BLOCKED = _blocked_regex()
LF_DEFAULT = {"alphabet": "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "font": "sans", "axes": None, "cell": 16,
              "cell_ratio": 1.18, "glyph": 0.74, "dot_below": 0.26, "dot_radius": 0.075, "life": 0.18}


def ease_out(x):
    x = np.clip(x, 0.0, 1.0)
    return 1 - (1 - x) ** 3


def ease(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


# ----------------------------------------------------------------------------- the grid
class Grid:
    """One cell size: the cells covering the frame, each cell's own letter, and the glyph tiles."""

    def __init__(self, W, H, cell_px, lf, font_path, scale):
        self.scale = scale
        self.cw = max(2, int(round(cell_px)))
        self.ch = max(3, int(round(self.cw * float(lf["cell_ratio"]))))
        self.cols = -(-W // self.cw) + 1
        self.rows = -(-H // self.ch) + 1
        self.ox = (W - self.cols * self.cw) // 2  # the grid overhangs the frame a little on every side
        self.oy = (H - self.rows * self.ch) // 2
        self.n = self.rows * self.cols
        self.letters = list(dict.fromkeys(lf["alphabet"]))
        self.N = len(self.letters)
        # letters, digits and symbols can be weighted so the field still reads as letters
        wt = lf.get("weights") or {}
        counts = []
        for c in self.letters:
            w = float(wt.get("letters", 1.0) if c.isalpha() else wt.get("digits", 1.0) if c.isdigit() else wt.get("symbols", 1.0))
            counts.append(max(1, int(round(w * 20))))
        self.pick = np.repeat(np.arange(1, self.N + 1, dtype=np.int32), counts)
        self.dot_below = float(lf["dot_below"])
        idx = np.arange(self.n)
        self.idx = idx
        self.cx = self.ox + (idx % self.cols + 0.5) * self.cw
        self.cy = self.oy + (idx // self.cols + 0.5) * self.ch
        life = float(lf["life"])
        self.life = (1 - life + life * hash2(idx, 7)).astype(np.float32)
        self.readable = self.cw >= 11 * scale  # smaller letters are texture, too small to read a word in
        self.home = self.rand(hash2(idx, 3))
        if self.readable:
            self.home = self.clean(self.home.copy(), np.ones(self.n, bool), 0)
        self.tiles = self._atlas(font_path, lf)

    def _atlas(self, font_path, lf, ss=4):
        cw, ch = self.cw * ss, self.ch * ss
        n = self.N + 1
        # small cells keep their ink: a letter covers a quarter of its cell, so as the grid gets finer the
        # glyphs grow heavier and fill toward the cell, and a fine field converges on the solid figure
        c1080 = self.cw / self.scale
        fine = lf.get("fine") or {}
        fill = float(np.interp(c1080, fine.get("fill_at", [3, 4, 6, 10, 12]), fine.get("fill", [0.8, 0.62, 0.36, 0.08, 0.0])))
        glyph = float(np.interp(c1080, [4, 12], [fine.get("glyph", 0.95), float(lf["glyph"])]))
        axes = dict(lf.get("axes") or {})
        if "wght" in axes:
            axes["wght"] = float(np.interp(c1080, [4, 12], [fine.get("wght", 800), float(axes["wght"])]))
        sheet = Image.new("L", (cw * n, ch), 0)
        d = ImageDraw.Draw(sheet)
        r = max(0.8, self.ch * float(lf["dot_radius"])) * ss  # slot 0 is a dot, drawn, not typeset
        d.ellipse([cw / 2 - r, ch / 2 - r, cw / 2 + r, ch / 2 + r], fill=255)
        f = typeset.pil_font(font_path, self.ch * glyph * ss, axes or lf.get("axes"))
        for i, letter in enumerate(self.letters):
            w = f.getlength(letter)
            d.text((cw * (i + 1) + (cw - w) / 2, ch / 2 + self.ch * glyph * 0.365 * ss), letter, font=f, fill=255, anchor="ls")
        small = sheet.resize((self.cw * n, self.ch), Image.LANCZOS)
        a = np.asarray(small, np.float32) / 255.0
        tiles = np.stack([a[:, k * self.cw:(k + 1) * self.cw] for k in range(n)])
        tiles[1:] = tiles[1:] * (1 - fill) + fill
        self.page_scale = float(np.interp(c1080, [6, 12], [0.0, 1.0]))  # a fine dot grid only shimmers on a phone
        return tiles

    def canvas_size(self):
        return self.cols * self.cw, self.rows * self.ch

    def block_mean(self, mask):
        """Coverage per cell. On a coarse grid a hairline covers a sliver of every cell it crosses, so
        coarse cells also count the ink they touch at all (block max), or thin strokes drop out."""
        blocks = mask.reshape(self.rows, self.ch, self.cols, self.cw)
        mean = blocks.mean(axis=(1, 3))
        w = float(np.interp(self.cw / self.scale, [6, 16, 24], [0.0, 0.55, 0.45]))
        if w <= 0:
            return mean.ravel()
        return ((1 - w) * mean + w * blocks.max(axis=(1, 3))).ravel()

    def lookup(self, x, y):
        """Index of the cell holding frame point(s) x, y."""
        c = np.clip(np.floor((x - self.ox) / self.cw).astype(np.int64), 0, self.cols - 1)
        r = np.clip(np.floor((y - self.oy) / self.ch).astype(np.int64), 0, self.rows - 1)
        return r * self.cols + c

    def rand(self, h):
        """A random slot (weighted by kind) for hash values h in [0, 1)."""
        h = np.asarray(h)
        return self.pick[np.minimum((h * len(self.pick)).astype(np.int64), len(self.pick) - 1)]

    def clean(self, g, live, salt):
        """Re-letter the middle of any blocked word the live cells spell left to right, including
        look-alike spellings (5EX, A$$, D1E, F*CK) and number codes."""
        G = g.reshape(self.rows, self.cols)
        Lv = live.reshape(self.rows, self.cols)
        ix = self.idx.reshape(self.rows, self.cols)
        for it in range(8):
            hit = False
            for r in range(self.rows):
                row = "".join(self.letters[v - 1] if (on and v > 0) else " " for v, on in zip(G[r], Lv[r]))
                if not row.strip():
                    continue
                for mt in BLOCKED.finditer(row):
                    j, w = mt.start(1), len(mt.group(1))
                    m = j + w // 2
                    old = self.letters[G[r, m] - 1]
                    for k in range(6):  # a different character, not a copy of the same one
                        G[r, m] = int(self.rand(hash2(ix[r, m], 41 + it + salt + 13 * k)))
                        if self.letters[G[r, m] - 1] != old:
                            break
                    hit = True
            if not hit:
                break
        return G.ravel()

    def render(self, A, G):
        """Canvas (rows*ch, cols*cw) of stamped glyph coverage."""
        pix = self.tiles[G.reshape(self.rows, self.cols)] * A.reshape(self.rows, self.cols)[..., None, None]
        return pix.transpose(0, 2, 1, 3).reshape(self.rows * self.ch, self.cols * self.cw)


# ----------------------------------------------------------------------------- figures
def keyed(keys, t, move, default):
    """A value set by [[at, value], ...] keys: each key eases in over `move` seconds from the one before."""
    if not keys:
        return default
    v = float(keys[0][1])
    for i, (at, val) in enumerate(keys):
        if t < float(at):
            break
        if i == 0:
            v = float(val)
            continue
        prev = float(keys[i - 1][1])
        u = float(ease_out((t - float(at)) / max(1e-3, move)))
        v = prev + (float(val) - prev) * u
    return v


class Figure:
    """What a shot draws: a giant letter, the logo, or nothing. Coverage is rendered per grid."""

    def __init__(self, spec, brand, W, H, scale):
        self.spec, self.W, self.H, self.scale = spec or {}, W, H, scale
        self.kind = self.spec.get("kind", "none")
        self.levels = []
        if self.kind == "letter":
            role = self.spec.get("font", "sans")
            axes = self.spec.get("axes", (brand.get("font_axes") or {}).get(role))
            f = typeset.pil_font(brand["fonts"][role], 2200, axes)
            ch = self.spec["char"]
            x0, y0, x1, y1 = f.getbbox(ch, anchor="ls")
            pad = 24
            im = Image.new("L", (x1 - x0 + 2 * pad, y1 - y0 + 2 * pad), 0)
            ImageDraw.Draw(im).text((pad - x0, pad - y0), ch, font=f, fill=255, anchor="ls")
            self._pyramid(im)
            self.ref = float(y1 - y0)  # ink height: "height" is a fraction of the frame height
        elif self.kind == "logo":
            img = Image.open(self.spec.get("src") or brand["logo"]).convert("RGBA")
            a = img.getchannel("A")
            a = a.crop(a.getbbox())
            self._pyramid(a)
            self.ref = float(a.width)  # "width" is a fraction of the frame width
            self.logo_src = a

    def _pyramid(self, im):
        self.levels = []
        k = 1.0
        while True:
            arr = np.asarray(im, np.float32) / 255.0
            self.levels.append((k, im, arr.shape))
            if min(im.size) < 64:
                break
            im = im.resize((max(1, im.width // 2), max(1, im.height // 2)), Image.LANCZOS)
            k *= 0.5

    def params(self, t):
        """(x, y, scale from source px to frame px, rotation in degrees) at shot time t."""
        sp = self.spec
        move = float(sp.get("move", 0.45))
        x = keyed(sp.get("x_keys"), t, move, float(sp.get("x", 0.5))) * self.W
        y = keyed(sp.get("y_keys"), t, move, float(sp.get("y", 0.5))) * self.H
        dx, dy = sp.get("drift", [0.0, 0.0])
        x += float(dx) * self.W * t
        y += float(dy) * self.H * t
        rot = float(sp.get("rot", 0.0)) + float(sp.get("spin", 0.0)) * t
        z0, z1 = sp.get("zoom", [1.0, 1.0])
        dur = float(sp.get("dur", 1.0))
        z = float(z0) + (float(z1) - float(z0)) * min(1.0, max(0.0, t / max(1e-3, dur)))
        if self.kind == "letter":
            size = keyed(sp.get("height_keys"), t, move, float(sp.get("height", 1.0))) * self.H
        else:
            size = keyed(sp.get("width_keys"), t, move, float(sp.get("width", 0.6))) * self.W
        return x, y, size / self.ref * z, rot

    def mask(self, cw_, ch_, ox, oy, t):
        """Coverage on a canvas of cw_ x ch_ px whose top left sits at frame (ox, oy)."""
        if self.kind == "none" or not self.levels:
            return np.zeros((ch_, cw_), np.float32)
        x, y, k, rot = self.params(t)
        lvl = self.levels[0]
        for cand in self.levels:  # the smallest source that is still at least as big as the result
            if k / cand[0] <= 1.0 + 1e-6:
                lvl = cand
        kl, im, _ = lvl
        k = k / kl
        th = math.radians(rot)
        c, s = math.cos(th), math.sin(th)
        sx, sy = im.width / 2.0, im.height / 2.0
        a, b = c / k, s / k
        d_, e = -s / k, c / k
        cc = sx + (c * (ox - x) + s * (oy - y)) / k
        ff = sy + (-s * (ox - x) + c * (oy - y)) / k
        out = im.transform((cw_, ch_), Image.AFFINE, (a, b, cc, d_, e, ff), resample=Image.BILINEAR)
        return np.asarray(out, np.float32) / 255.0

    def drawn(self, t):
        """The drawn figure (logo) at frame size, for the takeover: (alpha, x0, y0)."""
        x, y, k, rot = self.params(t)
        w = max(1, int(round(self.logo_src.width * k)))
        h = max(1, int(round(self.logo_src.height * k)))
        a = np.asarray(self.logo_src.resize((w, h), Image.LANCZOS), np.float32) / 255.0
        return a, int(round(x - w / 2)), int(round(y - h / 2))


# ----------------------------------------------------------------------------- the film
class Film:
    def __init__(self, board, brand, W, H, fps=30):
        self.board, self.brand, self.W, self.H, self.fps = board, brand, W, H, fps
        cfg = brand.get("cfg") or {}
        self.cfg = cfg
        self.scale = W / 1080.0
        lf = dict(LF_DEFAULT, **(cfg.get("letter_field") or {}))
        lf.update(board.get("field") or {})
        self.lf = lf
        self.font = brand["fonts"][lf.get("font", "sans")]
        self.grids = {}
        st = board.get("static") or {}
        self.noise_rate = float(st.get("rate", 0.06))
        self.noise_on = float(st.get("on", 0.7))
        self.noise_in = st.get("inside", [0.26, 0.66])
        self.noise_out = st.get("outside", [0.12, 0.5])
        self.swap = float(st.get("swap", 0.012))
        self.page = float((board.get("page") or {}).get("dots", 0.16))
        t = 0.0
        self.shots = []
        for i, s in enumerate(board["shots"]):
            dur = float(s["seconds"])
            s = dict(s, start=t, end=t + dur, dur=dur, k=i)
            fs = dict(s.get("figure") or {})
            fs.setdefault("dur", dur)
            s["fig"] = Figure(fs, brand, W, H, self.scale)
            self.shots.append(s)
            t += dur
        self.total = t
        self.lines = []
        for it in board.get("lines", []):
            role = it.get("font", "serif")
            px = float(it.get("size", 64)) * self.scale
            fax = brand.get("font_axes") or {}
            if it.get("runs"):  # mixed faces on one line: [{text, font, axes, scale}]
                runs = [(r["text"], brand["fonts"][r.get("font", role)],
                         r.get("axes", fax.get(r.get("font", role))), float(r.get("scale", 1.0))) for r in it["runs"]]
                ln = typeset.set_runs(runs, px, float(it.get("tracking", 0.0)), float(it.get("word_spacing", 0.0)))
            else:
                ln = typeset.set_line(it["text"], brand["fonts"][role], px, it.get("axes", fax.get(role)),
                                      float(it.get("tracking", 0.0)), word_spacing=float(it.get("word_spacing", 0.0)))
            self.lines.append(dict(it, line=ln, px=px))
        self._memo, self._keep = {}, {}
        self._clear_masks = {}
        self.dots = None
        self.grade = dict(board.get("grade") or {})
        self._build_footage()
        if board.get("dots"):
            self._build_dots(board["dots"])
        for it in self.lines:  # a soft shadow under words that sit on footage
            sh = it.get("shadow")
            if sh:
                ln = it["line"]
                blur = float(sh.get("blur", 20)) * it["px"] / 100.0
                a = Image.fromarray((ln.alpha * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))
                import dataclasses
                it["shadow_line"] = dataclasses.replace(ln, alpha=np.asarray(a, np.float32) / 255.0)

    def _build_footage(self):
        """Shots with a clip play it under everything; shots with tracks draw the tracking overlay.
        An arrive shot with tracks releases its boxes into the figure's cells (Converge)."""
        import overlay
        base = self.board.get("_base", ".")
        mono = self.brand["fonts"].get("mono") or self.font
        for s in self.shots:
            if s.get("clip"):
                clip = s["clip"] if os.path.isabs(s["clip"]) else os.path.join(base, s["clip"])
                s["reader"] = overlay.FootageReader(clip, self.W, self.H, self.fps)
            if s.get("tracks"):
                tp = s["tracks"] if os.path.isabs(s["tracks"]) else os.path.join(base, s["tracks"])
                s["ov"] = overlay.TrackOverlay(tp, dict(s.get("overlay") or {}), self.W, self.H, self.scale, mono, self.fps)
        for s in self.shots:
            if s.get("mode") == "arrive" and s.get("ov") is not None:
                grid = self.grid(s.get("cell", self.lf["cell"]))
                cw_, ch_ = grid.canvas_size()
                cov = grid.block_mean(s["fig"].mask(cw_, ch_, grid.ox, grid.oy, 0.0))
                inside = np.where(cov > 0.1)[0]
                rel = float((s.get("overlay") or {}).get("release", 0.2))
                k = int(round(rel * self.fps))
                origins = [(x, y) for _, x, y in s["ov"].points(k)]
                cells = np.stack([grid.cx[inside], grid.cy[inside]], 1)
                cv = overlay.Converge(origins, cells, s["start"] + rel, self.W, self.H, self.scale, seed=int(s.get("seed", 5)))
                s["converge"] = cv
                arrive = np.full(grid.n, np.inf)
                arrive[inside] = cv.arrive
                self.arrive, self.arrive_grid, self.arrive_k = arrive, grid, s["k"]

    def _build_dots(self, spec):
        """Dots that gather toward the arrive shot's figure, and land on its cells at the turn."""
        import dotflow
        ka = next(i for i, s in enumerate(self.shots) if s.get("mode") == "arrive")
        s = self.shots[ka]
        grid = self.grid(s.get("cell", self.lf["cell"]))
        cw_, ch_ = grid.canvas_size()
        m = s["fig"].mask(cw_, ch_, grid.ox, grid.oy, 0.0)
        cov = grid.block_mean(m)
        inside = np.where(cov > 0.1)[0]
        ys, xs = np.where(m > 0.5)
        pick = np.random.default_rng(7).choice(len(xs), min(len(xs), 40000), replace=False)
        soft = np.stack([xs[pick] + grid.ox, ys[pick] + grid.oy], 1).astype(float)
        cells = np.stack([grid.cx[inside], grid.cy[inside]], 1)
        spec = dict(spec, turn=s["start"], until=min(self.total, s["end"]))
        self.dots = dotflow.DotFlow(spec, self.W, self.H, self.fps, soft, cells, self.scale)
        arrive = np.full(grid.n, np.inf)
        arrive[inside] = self.dots.arrive
        self.arrive = arrive
        self.arrive_grid = grid
        self.arrive_k = ka

    def draw_overlay(self, frame, s, ts, t):
        import overlay
        ov = s["ov"]
        col = np.array(sm.GROUNDS["ink"].get("text", (245, 245, 243)), np.float32)
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        spec = s.get("overlay") or {}
        cv = s.get("converge")
        rel = (float(spec.get("release", 0.2)), 0.35) if cv is not None else None
        n = ov.draw(layer, ts, int(round(ts * self.fps)), col, 1.0, rel)
        ctx = 0.0
        if cv is not None:
            cv.draw(layer, t, col)
            ctx = cv.fraction(t)
            n = n + int(((t >= cv.start) & (t < cv.arrive)).sum())
        hud_a = 1.0
        ho = spec.get("hud_out")
        if ho:
            hud_a = 1 - smoothstep(float(ho[0]), float(ho[1]), ts)
        ov.hud(layer, t, ts, n, ctx, col, float(hud_a) * smoothstep(0.0, 0.06, ts + 0.06), self.board.get("hud") or {})
        keep = None
        for it in self.lines:  # the overlay keeps clear of the words, like the field does
            o = self.line_opacity(it, t)
            if o > 0 and float(it.get("y", 0)) < 0.9:
                m = 1 - float(spec.get("clear", 0.9)) * o * self.clear_mask(it)
                keep = m if keep is None else keep * m
        if keep is not None:
            al = np.asarray(layer.getchannel("A"), np.float32) * keep
            layer.putalpha(Image.fromarray(np.clip(al, 0, 255).astype(np.uint8)))
        sh = overlay.shadowed(layer, 0.5, 2.0 * self.scale)[..., None]
        frame = frame * (1 - sh) + np.array([10, 10, 9], np.float32) * sh
        rgba = np.asarray(layer, np.float32) / 255.0
        a = rgba[..., 3:4]
        return frame * (1 - a) + rgba[..., :3] * 255.0 * a

    def clear_mask(self, it):
        """A soft rounded hole around a line, at frame size, for layers that are not a letter grid."""
        key = id(it)
        if key not in self._clear_masks:
            x0, y0, x1, y1 = self.line_box(it)
            px, py = it["px"] * float(it.get("pad_x", 0.7)), it["px"] * float(it.get("pad_y", 0.55))
            fe = it["px"] * float(it.get("feather", 1.1))
            yy, xx = np.mgrid[0:self.H, 0:self.W].astype(np.float32)
            dx = np.maximum(np.maximum(x0 - px - xx, xx - (x1 + px)), 0.0)
            dy = np.maximum(np.maximum(y0 - py - yy, yy - (y1 + py)), 0.0)
            self._clear_masks[key] = (1 - smoothstep(0.0, fe, np.sqrt(dx * dx + dy * dy))).astype(np.float32)
        return self._clear_masks[key]

    # --- helpers
    def grid(self, cell):
        key = int(round(float(cell) * self.scale))
        if key not in self.grids:
            self.grids[key] = Grid(self.W, self.H, key, self.lf, self.font, self.scale)
        return self.grids[key]

    def ground(self, s):
        g = sm.GROUNDS[s.get("ground", "ink")]
        return np.array(g["bg"], np.float32), np.array(g.get("text", g["ink"]), np.float32)

    def coverage(self, s, grid, t):
        cw_, ch_ = grid.canvas_size()
        return grid.block_mean(s["fig"].mask(cw_, ch_, grid.ox, grid.oy, t))

    def target(self, grid, cov):
        inside = cov > 0.1
        a = np.where(inside, (0.42 + 0.58 * cov) * grid.life, self.page * grid.page_scale).astype(np.float32)
        return a, inside

    def noise(self, grid, t, salt, inside, spec=None):
        spec = spec or {}
        step = int(t / self.noise_rate)
        i = grid.idx + 7919 * salt
        g = grid.rand(hash2(i, step + 17))
        on = hash2(i, step) < float(spec.get("on", self.noise_on))
        r = hash2(i, step + 29)
        lo_i, hi_i = spec.get("inside", self.noise_in)
        lo_o, hi_o = spec.get("outside", self.noise_out)
        a = np.where(inside, lo_i + (hi_i - lo_i) * r, lo_o + (hi_o - lo_o) * r) * on
        return a.astype(np.float32), g

    def blur(self, lay, sigma):
        """A soft Gaussian of the light layer, computed at a quarter size (a glow has no detail to keep)."""
        h, w = lay.shape
        k = 4
        im = Image.fromarray((np.clip(lay, 0, 1) * 255).astype(np.uint8)).resize((max(1, w // k), max(1, h // k)), Image.BOX)
        im = im.filter(ImageFilter.GaussianBlur(max(0.5, sigma / k)))
        return np.asarray(im.resize((w, h), Image.BILINEAR), np.float32) / 255.0

    def breathe(self, grid, t, salt):
        """A settled cell keeps its own letter, now and then trading it for another (the site's loader does)."""
        samp = int(t / 0.11)
        i = grid.idx + 7919 * salt
        swap = hash2(i, samp) < self.swap
        return np.where(swap, grid.rand(hash2(i, samp + 9)), grid.home)

    # --- the state of every cell: pure functions of time, so any frame renders on its own
    def cells(self, k, t, keep=False):
        """(grid, alpha, glyph, drawn-figure opacity) for shot k at shot time t. keep: a step boundary,
        cached for the whole render because every frame of the next step starts from it."""
        key = (k, round(t, 5))
        if key in self._keep:
            return self._keep[key]
        if key in self._memo:
            return self._memo[key]
        s = self.shots[k]
        mode = s.get("mode", "resolve")
        salt = int(s.get("seed", k + 1))
        if mode in ("hold", "empty", "footage"):
            grid = self.grid(s.get("cell", self.lf["cell"]))
            out = (grid, np.zeros(grid.n, np.float32), np.zeros(grid.n, np.int32), 1.0 if mode == "hold" else 0.0)
        elif mode == "arrive":
            # each cell of the figure lights up when its dot lands; the empty page fades in behind
            grid = self.arrive_grid
            cov = self.coverage(s, grid, t)
            a1, inside = self.target(grid, cov)
            tg = s["start"] + t
            arr = np.where(np.isfinite(self.arrive), self.arrive, s["start"] + float(s.get("land_by", 2.6)))
            with np.errstate(invalid="ignore"):
                on = ease((tg - arr) / 0.16)
            pr = s.get("page_in", [1.2, 2.4])
            page = float(smoothstep(float(pr[0]), float(pr[1]), t))
            a = np.where(inside, a1 * on, a1 * page).astype(np.float32)
            fresh = inside & (tg >= arr) & (tg < arr + 0.12)
            flick = grid.rand(hash2(grid.idx + 77, int(t / self.noise_rate)))
            g = np.where(fresh, flick, self.breathe(grid, t, salt))
            out = (grid, a, g, 0.0)
        elif mode == "resolve":
            grid = self.grid(s.get("cell", self.lf["cell"]))
            cov = self.coverage(s, grid, t)
            a1, inside = self.target(grid, cov)
            S = float(s.get("settle", 0.5))
            p = float(smoothstep(0.0, S, t))
            order = hash2(grid.idx + 104729 * salt, 5)
            if s.get("order") == "converge":
                # the static gathers: the cells farthest from the figure settle first, the figure's own last,
                # so the noise closes in on the figure from the edges before it resolves
                fx, fy, _, _ = s["fig"].params(t)
                d = np.hypot((grid.cx - fx) / self.W, (grid.cy - fy) / self.H * 0.8)
                d = d / (d.max() + 1e-6)
                order = np.where(inside, 0.72 + 0.28 * order, 0.7 * (1 - d) + 0.12 * order)
            settled = p > order * 0.92
            an, gn = self.noise(grid, t, salt, inside, s.get("static"))
            a = np.where(settled, a1, an)
            g = np.where(settled, self.breathe(grid, t, salt), gn)
            out = (grid, a, g, 0.0)
        else:  # refine
            steps = s["steps"]
            j = max(i for i, st in enumerate(steps) if float(st["at"]) <= t + 1e-9) if t >= float(steps[0]["at"]) else 0
            st = steps[j]
            at = float(st["at"])
            grid = self.grid(st["cell"])
            # what the field looked like just before this step: the previous step, or the shot before
            if j > 0:
                pg, pa, pgl, _ = self.cells(k, at - 1e-4, keep=True)
            else:
                pk = k - 1
                pg, pa, pgl, _ = self.cells(pk, self.shots[pk]["dur"] - 1e-4, keep=True)
            a0 = pa[pg.lookup(grid.cx, grid.cy)]
            cov = self.coverage(s, grid, t)
            a1, inside = self.target(grid, cov)
            spread, dur = float(s.get("spread", 0.16)), float(s.get("refine", 0.26))
            u = np.clip((t - at - spread * hash2(grid.idx + 31 * j, 11)) / dur, 0.0, 1.0)
            a = a0 + (a1 - a0) * ease(u)
            moving = (u > 0) & (u < 1)
            flick = grid.rand(hash2(grid.idx + 7 * j, int(t / self.noise_rate) + 5))
            g = np.where(moving, flick, self.breathe(grid, t, salt))
            out = (grid, a.astype(np.float32), g, 0.0)
        # the drawn logo takes over from its letters
        lg = s.get("logo")
        grid, a, g, op = out
        if lg and mode != "hold":
            la = float(lg.get("at", 1e9))
            if t >= la:
                a = a * float(1 - ease((t - la) / float(lg.get("letters_out", 0.45))))
                op = float(ease((t - la) / float(lg.get("in", 0.3))))
            out = (grid, a, g, op)
        (self._keep if keep else self._memo)[key] = out
        return out

    def line_opacity(self, it, t):
        return self.line_state(it, t)[0]

    def line_state(self, it, t):
        """(opacity, dx, dy): a line fades in; at `to` it cuts off, or with "sweep" it is blown away,
        fading while it drifts in the sweep's direction."""
        a, z = float(it["from"]), float(it["to"])
        sw = it.get("sweep")
        end = z + (float(sw.get("dur", 0.3)) if sw else 0.0)
        if t < a or t >= end:
            return 0.0, 0.0, 0.0
        o = float(ease_out((t - a) / max(1e-3, float(it.get("fade", 0.2)))))
        fo = float(it.get("fade_out", 0.0))
        if fo > 0 and z - fo < t < z:
            o *= float(1 - ease((t - (z - fo)) / fo))
        if sw and t >= z:
            u = (t - z) / float(sw.get("dur", 0.3))
            d = np.array(sw.get("dir", [1, 0]), float)
            d = d / (np.linalg.norm(d) + 1e-9)
            dist = float(sw.get("dist", 90)) * self.scale * float(ease_out(u)) ** 0.8
            return o * float(1 - ease(u)), float(d[0] * dist), float(d[1] * dist)
        return o, 0.0, 0.0

    def line_box(self, it):
        ln = it["line"]
        x = float(it.get("x", 0.5)) * self.W
        y = float(it.get("y", 0.6)) * self.H
        anchor = it.get("anchor", "ms")
        ox = {"l": 0.0, "m": -ln.advance / 2, "r": -ln.advance}[anchor[0]]
        x0, x1 = x + ox, x + ox + ln.advance
        y0, y1 = y - ln.ascent * 0.78, y + ln.descent * 0.6
        return x0, y0, x1, y1

    def frame(self, t):
        k = max(i for i, s in enumerate(self.shots) if s["start"] <= t + 1e-9) if t >= 0 else 0
        k = min(k, len(self.shots) - 1)
        s = self.shots[k]
        ts = t - s["start"]
        bg, ink = self.ground(s)
        frame = np.empty((self.H, self.W, 3), np.float32)
        frame[:] = bg
        if s.get("reader") is not None:
            img = montage.grade(s["reader"].frame(int(round(ts * self.fps))), self.grade, s)
            fo = s.get("footage_out")
            f = float(smoothstep(float(fo[0]), float(fo[1]), ts)) if fo else 0.0
            if f < 1.0:
                frame = img * (1 - f) + frame * f
            dp = self.board.get("dip")
            if dp:  # a bright or busy shot sits back under the words, only as much as it needs (the whole shot, never a box)
                texts = [it for it in self.lines if self.line_opacity(it, t) > 0 and float(it.get("y", 0)) < 0.9]
                if texts:
                    x0 = int(max(0, min(self.line_box(it)[0] for it in texts) - 40))
                    x1 = int(min(self.W, max(self.line_box(it)[2] for it in texts) + 40))
                    y0 = int(max(0, min(self.line_box(it)[1] for it in texts) - 30))
                    y1 = int(min(self.H, max(self.line_box(it)[3] for it in texts) + 30))
                    reg = frame[y0:y1, x0:x1]
                    lum = float((reg @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean()) / 255.0
                    busy = float(np.std(reg @ np.array([0.2126, 0.7152, 0.0722], np.float32))) / 255.0
                    o = max(self.line_opacity(it, t) for it in texts)
                    k_ = float(np.clip((lum - float(dp.get("from", 0.3))) / 0.7 + busy * float(dp.get("busy", 1.2)), 0, 1))
                    frame = frame * (1 - k_ * float(dp.get("max", 0.42)) * o)
        grid, a, g, logo_op = self.cells(k, ts)
        live = [it for it in self.lines if self.line_opacity(it, t) > 0]
        a = a.copy()
        for it in live:  # the field clears around the words
            clear = float(it.get("clear", 0.8))
            if clear <= 0:
                continue
            o = self.line_opacity(it, t) if t < float(it["to"]) else 0.0  # a line being blown away clears nothing
            x0, y0, x1, y1 = self.line_box(it)
            px, py = it["px"] * float(it.get("pad_x", 0.7)), it["px"] * float(it.get("pad_y", 0.55))
            # a soft rounded clearing, not a box: distance to the padded text box, feathered
            fe = it["px"] * float(it.get("feather", 1.1))
            dx = np.maximum(np.maximum(x0 - px - grid.cx, grid.cx - (x1 + px)), 0.0)
            dy = np.maximum(np.maximum(y0 - py - grid.cy, grid.cy - (y1 + py)), 0.0)
            dist = np.sqrt(dx * dx + dy * dy)
            w = 1 - smoothstep(0.0, fe, dist)
            a *= (1 - clear * o * w).astype(np.float32)
        g = np.where(a < grid.dot_below, 0, g)
        if grid.readable and a.max() > 0:
            g = grid.clean(g.copy(), a >= grid.dot_below, int(t * 1000) % 997)
        lay = None
        if a.max() > 0.003:
            lay = grid.render(np.clip(a, 0, 1), g)
            sy0, sx0 = -grid.oy, -grid.ox
            lay = np.ascontiguousarray(lay[sy0:sy0 + self.H, sx0:sx0 + self.W])
        if logo_op > 0:
            la, lx, ly = s["fig"].drawn(ts)
            if lay is None:
                lay = np.zeros((self.H, self.W), np.float32)
            lg = np.zeros_like(lay)
            montage._blend(lg[..., None], la * logo_op, lx, ly, (1.0,))
            lay = 1 - (1 - lay) * (1 - lg)
        if self.dots is not None:
            dl = self.dots.render(t)
            if dl is not None:
                for it in live:  # the dots keep clear of the words too
                    clear = float(it.get("clear", 0.8))
                    if clear > 0 and t < float(it["to"]):
                        dl = dl * (1 - clear * self.line_opacity(it, t) * self.clear_mask(it))
                lay = dl if lay is None else 1 - (1 - lay) * (1 - dl)
        if lay is not None:
            bl = (self.board.get("bloom") or {}).get(s.get("ground", "ink"))
            if bl:  # light on a dark ground spreads a little, the way a lit screen does
                lay = lay + float(bl.get("strength", 0.4)) * self.blur(lay, float(bl.get("radius", 14)) * self.scale)
                lay = np.clip(lay, 0, 1)
            frame = frame * (1 - lay[..., None]) + ink * lay[..., None]
        if s.get("ov") is not None:
            frame = self.draw_overlay(frame, s, ts, t)
        for it in live:
            col = montage.color(self.cfg, it.get("color"), ink) if it.get("color") else ink
            o, dx, dy = self.line_state(it, t)
            if it.get("shadow_line") is not None:
                sh = it.get("shadow") or {}
                ox_, oy_ = sh.get("offset", [0, 2])
                typeset.place(frame, it["shadow_line"], float(it.get("x", 0.5)) * self.W + dx + ox_,
                              float(it.get("y", 0.6)) * self.H + dy + oy_, (10, 10, 9),
                              o * float(sh.get("opacity", 0.6)), it.get("anchor", "ms"))
            typeset.place(frame, it["line"], float(it.get("x", 0.5)) * self.W + dx, float(it.get("y", 0.6)) * self.H + dy,
                          col, o, it.get("anchor", "ms"))
        return frame, s.get("id", str(k))

    def scan(self, fps, clean=False):
        """Blocked words any readable grid spells in a row, frame by frame: raw, or after the per-frame
        filter exactly as frame() applies it (the words clearing is ignored, so this over-reports)."""
        found = []
        for f in range(int(round(self.total * fps))):
            t = f / fps
            k = max(i for i, s in enumerate(self.shots) if s["start"] <= t + 1e-9)
            grid, a, g, _ = self.cells(k, t - self.shots[k]["start"])
            self._memo.clear()
            if not grid.readable:
                continue
            g = np.where(a < grid.dot_below, 0, g)
            if clean:
                g = grid.clean(g.copy(), a >= grid.dot_below, int(t * 1000) % 997)
            g = g.reshape(grid.rows, grid.cols)
            for r in range(grid.rows):
                row = "".join(grid.letters[v - 1] if v > 0 else " " for v in g[r])
                for mt in BLOCKED.finditer(row):
                    found.append((round(t, 2), mt.group(1), r))
        return found


def main():
    ap = argparse.ArgumentParser(description="Render a letter-field film storyboard.")
    ap.add_argument("storyboard")
    ap.add_argument("--out", default=None)
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--fps", type=int, default=None)
    ap.add_argument("--brand", default="")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--stills", action="store_true")
    ap.add_argument("--still", type=float, default=None)
    ap.add_argument("--times", default=None, help="comma separated seconds: a sheet of just those frames")
    ap.add_argument("--from", dest="t_from", type=float, default=None)
    ap.add_argument("--to", dest="t_to", type=float, default=None)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--remux", action="store_true")
    ap.add_argument("--lean", action="store_true", help="low-memory encode for small machines")
    ap.add_argument("--scan-letters", action="store_true")
    args = ap.parse_args()

    base = os.path.dirname(os.path.abspath(args.storyboard))
    board = sm.resolve_paths(json.load(open(args.storyboard, encoding="utf-8")), base)
    if board.get("music", {}).get("src") and not os.path.isabs(board["music"]["src"]):
        board["music"]["src"] = os.path.normpath(os.path.join(base, board["music"]["src"]))
    W, H = (int(v) for v in (board.get("size") or args.size).lower().split("x"))
    fps = args.fps or int(board.get("fps", 30))
    if args.preview:
        W, H, fps = (W // 2) & ~1, (H // 2) & ~1, 15
    brand = sm.load_brand(sm.find_brand(args.brand, args.storyboard))
    board["_base"] = base
    film = Film(board, brand, W, H, fps)
    total = film.total

    if args.scan_letters:
        for clean in (False, True):
            hits = film.scan(fps, clean)
            print(f"  {'filtered' if clean else 'raw'}: {len(hits)} blocked-word rows")
            for h in hits[:12]:
                print("   ", h)
        return

    rng = np.random.default_rng(int(board.get("seed", 3)))
    gstd = float(board.get("film_grain", 2.0))
    grain = [rng.normal(0, gstd, (H, W, 1)).astype(np.float32) for _ in range(6)] if gstd > 0 else None

    audio = os.path.splitext(args.out)[0] + "_music.wav"
    windowed = args.t_from is not None or args.t_to is not None
    if args.times:
        args.stills = True
        board["stills"] = [float(v) for v in args.times.split(",") if v.strip()]
    still_mode = args.stills or args.still is not None
    if not still_mode and not args.no_audio and not windowed and board.get("music"):
        lufs = montage.build_music(board, total, audio)
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
        per_shot = set() if args.times else {int(round((s["start"] + s["dur"] * f_) * fps)) for s in film.shots
                                             for f_ in (0.12, 0.6)}
        want = sorted(per_shot | {int(round(float(x) * fps)) for x in board.get("stills", [])})
    elif args.still is not None:
        want = [int(round(args.still * fps))]
    else:
        f0 = int(round((args.t_from or 0.0) * fps))
        f1 = int(round((args.t_to if args.t_to is not None else total) * fps))
        want = list(range(f0, min(nframes, f1)))

    enc = None
    if not still_mode:
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
        use_audio = not args.no_audio and not windowed and board.get("music")
        if use_audio:
            cmd += ["-i", audio, "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-maxrate", "24M", "-bufsize", "48M",
                "-preset", "medium", "-movflags", "+faststart"]
        if args.lean:
            cmd += ["-threads", "2", "-x264-params", "rc-lookahead=10"]
        cmd += (["-shortest"] if use_audio else []) + [args.out]
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    frames_out, sheet = [], []
    for fi in want:
        t = fi / fps
        frame, label = film.frame(t)
        film._memo.clear()
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
        sm.sheet_image(frames_out, W, H, cols=min(8, len(frames_out)), tw=270).save(args.out)
        print("done:", args.out, len(frames_out), "frames")


if __name__ == "__main__":
    main()
