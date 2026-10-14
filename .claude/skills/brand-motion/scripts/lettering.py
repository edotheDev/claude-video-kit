#!/usr/bin/env python3
"""A letter field for the motion kit: a picture drawn on a grid of letters.

Some brands draw with letters rather than grain. A shape sits on a grid of cells, light decides how
much ink each cell gets, and the palest cells fall back to a dot. brand.json "letter_field" sets the
alphabet, the font role and the cell, so the same code draws any brand's field:

    "letter_field": {"alphabet": "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "font": "sans", "axes": {"wght": 500},
                     "cell": 16, "cell_ratio": 1.18, "glyph": 0.74, "dot_below": 0.26, "dot_radius": 0.075}

cell is in px at 1080 wide. The hash is stable, so a cell keeps its letter from frame to frame
and from film to film.
"""
import numpy as np
from PIL import Image, ImageDraw

import typeset

DEFAULT = {"alphabet": "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "font": "sans", "axes": None, "cell": 16,
           "cell_ratio": 1.18, "glyph": 0.74, "dot_below": 0.26, "dot_radius": 0.075, "life": 0.18}


def hash2(a, b):
    """Stable pseudo random number in [0, 1) for a cell and a step (Math.imul arithmetic, as on the site)."""
    a = np.asarray(a, dtype=np.uint32)
    b = np.asarray(b, dtype=np.uint32)
    with np.errstate(over="ignore"):
        h = (a ^ ((b + np.uint32(0x3C6EF372)) * np.uint32(0x9E3779B1))) * np.uint32(0x85EBCA6B)
        h = h ^ (h >> np.uint32(13))
        h = h * np.uint32(0xC2B2AE35)
        h = h ^ (h >> np.uint32(16))
    return h.astype(np.float64) / 4294967296.0


class Field:
    """The grid, the glyph tiles, and drawing cells onto a frame."""

    def __init__(self, W, H, font_path, spec=None):
        self.spec = dict(DEFAULT, **(spec or {}))
        s = W / 1080.0
        self.cw = max(3, int(round(self.spec["cell"] * s)))
        self.ch = max(4, int(round(self.cw * self.spec["cell_ratio"])))
        self.cols, self.rows = W // self.cw, H // self.ch
        self.ox, self.oy = (W - self.cols * self.cw) // 2, (H - self.rows * self.ch) // 2
        self.n = self.cols * self.rows
        self.letters = list(self.spec["alphabet"])
        self.tiles = self._atlas(font_path)
        idx = np.arange(self.n)
        self.cx = self.ox + (idx % self.cols + 0.5) * self.cw   # cell centres, px
        self.cy = self.oy + (idx // self.cols + 0.5) * self.ch
        self.home = (np.floor(hash2(idx, 3) * len(self.letters)) + 1).astype(np.int32)  # each cell's own letter

    def _atlas(self, font_path, ss=4):
        cw, ch = self.cw * ss, self.ch * ss
        n = len(self.letters) + 1
        sheet = Image.new("L", (cw * n, ch), 0)
        d = ImageDraw.Draw(sheet)
        r = max(1.0, self.ch * self.spec["dot_radius"]) * ss  # slot 0 is a dot, drawn, not typeset
        d.ellipse([cw / 2 - r, ch / 2 - r, cw / 2 + r, ch / 2 + r], fill=255)
        f = typeset.pil_font(font_path, self.ch * self.spec["glyph"] * ss, self.spec.get("axes"))
        for i, letter in enumerate(self.letters):
            w = f.getlength(letter)
            d.text((cw * (i + 1) + (cw - w) / 2, ch / 2 + self.ch * 0.27 * ss), letter, font=f, fill=255, anchor="ls")
        small = sheet.resize((self.cw * n, self.ch), Image.LANCZOS)
        a = np.asarray(small, np.float32) / 255.0
        return np.stack([a[:, k * self.cw:(k + 1) * self.cw] for k in range(n)])  # (n, ch, cw)

    def cell_mean(self, img):
        """Block-average a frame-sized float image into cells."""
        g = img[self.oy:self.oy + self.rows * self.ch, self.ox:self.ox + self.cols * self.cw]
        return g.reshape(self.rows, self.ch, self.cols, self.cw).mean(axis=(1, 3)).ravel()

    def glyphs(self, alpha, t, moving=None):
        """Slot per cell: its own letter, now and then resampled; a dot when pale; flicker while moving."""
        idx = np.arange(self.n)
        step = int(t / 0.11)
        g = self.home.copy()
        swap = hash2(idx, step) < 0.012
        g[swap] = (np.floor(hash2(idx[swap], step + 9) * len(self.letters)) + 1).astype(np.int32)
        if moving is not None:
            fl = int(t / 0.06)
            m = moving & (hash2(idx, fl) < 0.7)
            g[m] = (np.floor(hash2(idx[m], fl + 17) * len(self.letters)) + 1).astype(np.int32)
        g[alpha < self.spec["dot_below"]] = 0
        return g

    def draw(self, frame, alpha, slots, color, opacity=1.0):
        """Stamp every cell's slot at its alpha onto an RGB float frame (in place)."""
        A = np.clip(alpha, 0, 1).reshape(self.rows, self.cols) * opacity
        if A.max() <= 0.003:
            return frame
        pix = self.tiles[slots.reshape(self.rows, self.cols)] * A[..., None, None]
        img = pix.transpose(0, 2, 1, 3).reshape(self.rows * self.ch, self.cols * self.cw)[..., None]
        y1, x1 = self.oy + self.rows * self.ch, self.ox + self.cols * self.cw
        region = frame[self.oy:y1, self.ox:x1]
        frame[self.oy:y1, self.ox:x1] = region * (1 - img) + np.asarray(color, np.float32) * img
        return frame

    def from_light(self, lum, inside, t=0.0, floor=0.3, gain=0.85, gamma=0.7):
        """Cells from a picture: ink comes from the light, with a floor so the shape reads as letters."""
        a = np.clip((floor + gain * np.power(np.clip(lum, 0, 1), gamma)), 0, 1) * inside
        a = a * (1 - self.spec["life"] + self.spec["life"] * hash2(np.arange(self.n), 7))
        dust = (inside < 0.5) & (hash2(np.arange(self.n), 23) < 0.004)
        a = np.where(dust, 0.25 + 0.3 * hash2(np.arange(self.n), 31), a)
        return a
