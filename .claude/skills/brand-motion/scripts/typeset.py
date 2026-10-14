#!/usr/bin/env python3
"""Shaped type for the motion kit.

PIL's basic layout ignores GPOS kerning, which is the only kerning most modern fonts carry, and it
has no tracking. Here HarfBuzz (uharfbuzz) places the glyphs, with kerning, tracking and variable
axes, and PIL draws them at 2x before a clean downsample. Without uharfbuzz it falls back to PIL
advances, so a machine without it still renders, just unkerned.

    line = set_line("ordinary days.", "fonts/InstrumentSans.ttf", 96, axes={"wght": 500}, tracking=-0.03)
    line.alpha      # float32 [0, 1] coverage, tight to the ink plus a margin
    line.origin     # (x, y) of the pen origin on the baseline, inside line.alpha
    line.advance    # width of the set line, for centring
"""
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import uharfbuzz as hb
except ImportError:  # the kit still runs, without kerning
    hb = None

AXIS_NAMES = {"wght": "Weight", "wdth": "Width", "opsz": "Optical size", "slnt": "Slant", "ital": "Italic"}
_HB = {}


def pil_font(path, px, axes=None):
    f = ImageFont.truetype(path, max(4, int(round(px))))
    if not axes:
        return f
    try:
        if isinstance(axes, str):
            f.set_variation_by_name(axes)
            return f
        info = f.get_variation_axes()
        names = [(a["name"].decode() if isinstance(a["name"], bytes) else a["name"]).lower() for a in info]
        vals = [a["default"] for a in info]
        tags = _axis_tags(path)  # fvar order, the same order FreeType reports; names differ in case between fonts
        for tag, v in axes.items():
            if tags and tag in tags and len(tags) == len(vals):
                vals[tags.index(tag)] = v
                continue
            n = AXIS_NAMES.get(tag, tag).lower()
            if n in names:
                vals[names.index(n)] = v
        f.set_variation_by_axes(vals)
    except (OSError, AttributeError):
        pass
    return f


_TAGS = {}


def _axis_tags(path):
    if path not in _TAGS:
        try:
            from fontTools.ttLib import TTFont
            t = TTFont(path, lazy=True)
            _TAGS[path] = [ax.axisTag for ax in t["fvar"].axes] if "fvar" in t else []
        except Exception:
            _TAGS[path] = []
    return _TAGS[path]


def _hb_font(path, axes):
    key = (path, repr(axes))
    if key not in _HB:
        face = hb.Face(hb.Blob.from_file_path(path))
        font = hb.Font(face)
        if isinstance(axes, dict) and axes:
            font.set_variations({k: float(v) for k, v in axes.items()})
        _HB[key] = (font, face.upem)
    return _HB[key]


def pen_positions(text, path, px, axes=None, tracking=0.0, features=None, word_spacing=0.0):
    """Pen x of every character (px), and the set width. Tracking is in em and goes between glyphs.
    word_spacing (em) widens every space; condensed faces ship tight spaces that close up at caption size."""
    if hb is None:
        f = pil_font(path, px, axes)
        xs, x = [], 0.0
        for ch in text:
            xs.append(x)
            x += f.getlength(ch) + tracking * px + (word_spacing * px if ch == " " else 0.0)
        return xs, max(0.0, x - tracking * px)
    font, upem = _hb_font(path, axes)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    feats = {"kern": True, "liga": False, "clig": False, "dlig": False}
    feats.update(features or {})
    hb.shape(font, buf, feats)
    k = px / upem
    xs = [None] * len(text)
    x = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        if info.cluster < len(xs) and xs[info.cluster] is None:
            xs[info.cluster] = x + pos.x_offset * k
        x += pos.x_advance * k + tracking * px
        if word_spacing and info.cluster < len(text) and text[info.cluster] == " ":
            x += word_spacing * px
    last = 0.0
    for i, v in enumerate(xs):  # characters folded into a cluster sit at its pen
        if v is None:
            xs[i] = last
        last = xs[i]
    return xs, max(0.0, x - tracking * px)


@dataclass
class Line:
    alpha: np.ndarray   # (h, w) float32 coverage
    origin: tuple       # pen origin on the baseline, in alpha's pixels
    advance: float      # set width in px
    ascent: float
    descent: float


def set_line(text, path, px, axes=None, tracking=0.0, ss=2, features=None, word_spacing=0.0):
    """Set one line. Returns a Line whose alpha is tight to the ink with a small margin."""
    f = pil_font(path, px * ss, axes)
    xs, adv = pen_positions(text, path, px * ss, axes, tracking, features, word_spacing)
    asc, desc = f.getmetrics()
    pad = int(math.ceil(px * ss * 0.25)) + 4
    W = int(math.ceil(adv)) + 2 * pad
    H = asc + desc + 2 * pad
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    base = pad + asc
    for ch, x in zip(text, xs):
        if ch.strip():
            d.text((pad + x, base), ch, font=f, fill=255, anchor="ls")
    w1, h1 = max(1, W // ss), max(1, H // ss)
    small = img.resize((w1, h1), Image.LANCZOS)
    a = np.asarray(small, np.float32) / 255.0
    return Line(a, (pad / ss, base / ss), adv / ss, asc / ss, desc / ss)


def set_runs(runs, px, tracking=0.0, word_spacing=0.0, ss=2):
    """Set one line from runs of different faces on one baseline: a roman line with an italic word, a sans
    phrase into a serif one. runs: [(text, path, axes, scale)], scale multiplies px for that run.
    Kerning across a run boundary is not applied; keep the space inside the run before the switch."""
    lines = [set_line(t, p, px * s, a, tracking, ss, None, word_spacing) for t, p, a, s in runs]
    xs, x = [], 0.0
    for i, ln in enumerate(lines):
        xs.append(x)
        x += ln.advance + (tracking * px if i < len(lines) - 1 else 0.0)
    top = max(ln.origin[1] for ln in lines)
    bot = max(ln.alpha.shape[0] - ln.origin[1] for ln in lines)
    ox = lines[0].origin[0]
    W = int(math.ceil(max(ox + xi - ln.origin[0] + ln.alpha.shape[1] for xi, ln in zip(xs, lines)))) + 1
    H = int(math.ceil(top + bot)) + 1
    a = np.zeros((H, W), np.float32)
    for xi, ln in zip(xs, lines):
        x0, y0 = int(round(ox + xi - ln.origin[0])), int(round(top - ln.origin[1]))
        h, w = ln.alpha.shape
        a[y0:y0 + h, x0:x0 + w] = np.maximum(a[y0:y0 + h, x0:x0 + w], ln.alpha)
    return Line(a, (ox, top), x, max(ln.ascent for ln in lines), max(ln.descent for ln in lines))


def place(frame, line, x, y, color, opacity=1.0, anchor="ms"):
    """Composite a Line onto an RGB float frame. anchor: l/m/r horizontally, s (baseline) or m (x-height middle)."""
    h, w = line.alpha.shape
    ox = {"l": 0.0, "m": -line.advance / 2, "r": -line.advance}[anchor[0]]
    oy = {"s": 0.0, "m": line.ascent * 0.36, "a": line.ascent}[anchor[1]]
    x0 = int(round(x + ox - line.origin[0]))
    y0 = int(round(y + oy - line.origin[1]))
    H, W = frame.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(W, x0 + w), min(H, y0 + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    a = line.alpha[sy0:sy0 + fy1 - fy0, sx0:sx0 + fx1 - fx0][..., None] * opacity
    region = frame[fy0:fy1, fx0:fx1]
    frame[fy0:fy1, fx0:fx1] = region * (1 - a) + np.asarray(color, np.float32) * a
