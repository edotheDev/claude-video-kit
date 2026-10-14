#!/usr/bin/env python3
"""
Particle motion engine (brand-agnostic).

Renders a storyboard JSON into an MP4: particles behave like sand on a vibrating plate, so every
cut starts as noise and resolves into a pattern. Type is set in the brand's real fonts and the logo
comes from the brand's own file, so a film never contains AI-drawn text. A score is synthesized
against the timeline and muxed in.

Fonts, palette grounds, and the logo come from a brand.json (see the kit README). With no
brand.json it falls back to neutral grounds and a system font, so it always runs.

  python motion_engine.py board.json --out film.mp4                     # 1080x1920, 30 fps
  python motion_engine.py board.json --out film_4x5.mp4 --size 1080x1350
  python motion_engine.py board.json --out preview.mp4 --preview        # half size, 15 fps, same timing
  python motion_engine.py board.json --out film.mp4 --sheet sheet.png   # plus a contact sheet (2 frames/s)
  python motion_engine.py board.json --out stills.png --stills          # one still per scene, no video
  python motion_engine.py board.json --out cover.png --still end@2.5    # one frame (e.g. the cover image)
  python motion_engine.py board.json --out test.mp4 --only look,end     # render a subset of scenes
  python motion_engine.py board.json --out film.mp4 --remux             # new score into an existing render
  python motion_engine.py board.json --out film.mp4 --brand brand.json  # or set MOTION_BRAND
"""
import argparse
import json
import math
import os
import subprocess
import sys
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

DEFAULT_BRAND_FILE = os.environ.get("MOTION_BRAND", "")

# Neutral grounds. A brand.json adds or replaces entries by name.
# bg = the plate, ink = the sand, hi = the densest sand, text = caption color
GROUNDS = {
    "dark":  {"bg": (24, 24, 24),    "ink": (200, 200, 198), "hi": (250, 250, 250), "text": (235, 235, 233)},
    "light": {"bg": (235, 235, 233), "ink": (44, 44, 44),    "hi": (24, 24, 24),    "text": (24, 24, 24)},
}

# Only used when a brand.json leaves a font role out, so the engine still runs on any machine.
FALLBACK_FONTS = [
    r"C:\Windows\Fonts\georgia.ttf", r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\segoeui.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
FONT_ALTS = {"serif": ("serif_italic", "sans"), "serif_italic": ("serif", "sans"),
             "sans": ("sans_small", "serif"), "sans_small": ("sans", "serif"),
             "mono": ("sans_small", "sans")}
# brand.json "font_axes" sets a variable font's axes per role, e.g. {"sans": {"wght": 500}}.
AXIS_NAMES = {"wght": "Weight", "wdth": "Width", "opsz": "Optical size", "slnt": "Slant", "ital": "Italic"}


def resolve_paths(board, base):
    """Make file references in a storyboard relative to the storyboard itself, so boards stay portable."""
    keys = ("src", "mask_src", "clip", "image", "bg_image")

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in keys and isinstance(v, str) and not os.path.isabs(v):
                    cand = os.path.normpath(os.path.join(base, v))
                    if os.path.exists(cand):
                        node[k] = cand
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(board)
    return board


def find_brand(cli, storyboard=None):
    """A brand.json from the flag, the MOTION_BRAND env var, beside the storyboard, or in the skill folder."""
    here = os.path.dirname(os.path.abspath(__file__))
    board_dir = os.path.dirname(os.path.abspath(storyboard)) if storyboard else None
    board_up = os.path.dirname(board_dir) if board_dir else None  # a brand folder holding storyboards/
    for p in (cli, DEFAULT_BRAND_FILE, board_dir, board_up, here, os.path.dirname(here)):
        if not p:
            continue
        cand = os.path.join(p, "brand.json") if os.path.isdir(p) else p
        if os.path.exists(cand):
            return cand
    # a repo-level brand/brand.json, found by walking up from the storyboard (projects/<slug>/storyboards/...)
    d = board_dir
    for _ in range(6):
        if not d:
            break
        cand = os.path.join(d, "brand", "brand.json")
        if os.path.exists(cand):
            return cand
        d = os.path.dirname(d) if os.path.dirname(d) != d else None
    return ""


def load_brand(path=None):
    """Read a brand.json. Every key is optional; its grounds are folded into GROUNDS."""
    path = path if path is not None else DEFAULT_BRAND_FILE
    cfg, base = {}, os.getcwd()
    if path:
        if os.path.isdir(path):
            path = os.path.join(path, "brand.json")
        if not os.path.exists(path):
            raise SystemExit("brand config not found: " + path)
        cfg = json.load(open(path, encoding="utf-8"))
        base = os.path.dirname(os.path.abspath(path))

    def resolve(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))

    for name, g in (cfg.get("grounds") or {}).items():
        GROUNDS[name] = {k: tuple(v) for k, v in g.items()}
    fonts = {k: resolve(v) for k, v in (cfg.get("fonts") or {}).items()}

    def pick(role):
        for cand in (fonts.get(role),) + tuple(fonts.get(a) for a in FONT_ALTS[role]):
            if cand and os.path.exists(cand):
                return cand
        for cand in FALLBACK_FONTS:
            if os.path.exists(cand):
                return cand
        raise SystemExit("no usable font: set \"fonts\" in brand.json")

    def existing(p):
        """A logo path from brand.json, or None (with a note) when the file is not there yet."""
        if not p:
            return None
        p = resolve(p)
        if not os.path.exists(p):
            print("note: brand.json names a logo that does not exist yet:", p, file=sys.stderr)
            return None
        return p

    default_ground = cfg.get("default_ground", "dark")
    if default_ground not in GROUNDS:
        raise SystemExit("default_ground \"%s\" is not in grounds" % default_ground)
    return {
        "name": cfg.get("name", "brand"),
        "fonts": {role: pick(role) for role in FONT_ALTS},
        "font_axes": cfg.get("font_axes") or {},
        "cfg": cfg,
        "base": base,
        "logo": existing(cfg.get("logo")),
        "logomark": existing(cfg.get("logomark")),
        "default_ground": default_ground,
        "overlay": cfg.get("overlay") or {},
        "render": cfg.get("render") or {},
        "grade_shadow": tuple(cfg.get("grade_shadow", GROUNDS[default_ground]["bg"])),
    }


def set_axes(font, axes):
    """Set a variable font's axes, {"wght": 500} or a named instance like "Medium". Static fonts pass through."""
    if not axes:
        return font
    try:
        if isinstance(axes, str):
            font.set_variation_by_name(axes)
            return font
        info = font.get_variation_axes()
        names = [a["name"].decode() if isinstance(a["name"], bytes) else a["name"] for a in info]
        vals = [a["default"] for a in info]
        for tag, v in axes.items():
            n = AXIS_NAMES.get(tag, tag)
            if n in names:
                vals[names.index(n)] = v
        font.set_variation_by_axes(vals)
    except (OSError, AttributeError):
        pass  # not a variable font, or FreeType without variation support
    return font


NOTES = {"D4": 293.66, "F4": 349.23, "G4": 392.00, "A4": 440.00, "C5": 523.25,
         "D5": 587.33, "F5": 698.46, "G5": 783.99, "A5": 880.00, "C6": 1046.50, "D6": 1174.66}

# Simulation constants are per step; the plate always runs STEPS_PER_SEC steps per second,
# so a 15 fps preview moves exactly like the 30 fps master.
# Plate units: the short side of the frame spans PLATE units (1 unit = 960 px on a 1080-wide frame),
# so a 4:5 or 1:1 render is a center crop of the 9:16 plate.
STEPS_PER_SEC = 90
G2_REF = 8.0        # median squared gradient of a pattern that resolves on the film's ~0.4 s clock
SIG_BASE = 0.0081   # resting agitation
SIG_CUT = 0.043     # extra agitation at a cut, decaying as the pattern resolves
ETA = 0.00127       # pull toward the still (nodal) lines
DISSOLVE_J = 0.075  # field-independent shake at the end of a dissolve
PLATE = 1.125
REF_UNIT_PX = 960.0

RENDER = {  # how grains become pixels; a board can override any key under "render"
    "speck_blur": 0.6, "body_blur": 2.4, "glow_blur": 10.0,   # px at 1080 wide
    "speck_k": 1.5, "body_k": 0.9, "glow_k": 0.55,            # opacity per grain density
    "hi_from": 1.8, "hi_span": 2.5,                           # body density where sand turns "hi"
    "grain": 3.2,                                             # film grain (std, 0-255)
}


# ----------------------------------------------------------------------------- fields
def _distance_cells(inside):
    """Euclidean distance (in grid cells) from every cell to the nearest inside cell."""
    h, w = inside.shape
    edge = inside & ~(np.roll(inside, 1, 0) & np.roll(inside, -1, 0) & np.roll(inside, 1, 1) & np.roll(inside, -1, 1))
    by, bx = np.nonzero(edge)
    if len(by) == 0:
        return np.full(inside.shape, float(max(h, w)), np.float32)
    if len(by) > 3000:
        keep = np.linspace(0, len(by) - 1, 3000).astype(int)
        by, bx = by[keep], bx[keep]
    yy, xx = np.mgrid[0:h, 0:w]
    yy, xx = yy.ravel().astype(np.float32), xx.ravel().astype(np.float32)
    d = np.empty(h * w, np.float32)
    for i in range(0, h * w, 4096):
        dy = yy[i:i + 4096, None] - by[None, :]
        dx = xx[i:i + 4096, None] - bx[None, :]
        d[i:i + 4096] = np.sqrt((dy * dy + dx * dx).min(axis=1))
    d = d.reshape(h, w)
    d[inside] = 0
    return d


def _blur_float(a, sigma):
    h, w = a.shape
    yy = np.fft.fftfreq(h)[:, None]
    xx = np.fft.rfftfreq(w)[None, :]
    k = np.exp(-2 * (np.pi * sigma) ** 2 * (yy ** 2 + xx ** 2))
    pad = np.pad(a, ((0, 0), (0, 0)), mode="edge")
    return np.fft.irfft2(np.fft.rfft2(pad) * k, s=(h, w))


def _raster_image(spec):
    """Place an image on the canonical 9:16 plate (1080x1920 px, 960 px per unit). Returns (mask, rgb) arrays."""
    img = Image.open(spec["src"]).convert("RGBA")
    if spec.get("crop"):
        img = img.crop(tuple(spec["crop"]))
    if spec.get("trim"):  # crop to the drawn area, so it lines up with a logo item of the same width
        img = img.crop(img.getchannel("A").getbbox())
    if spec.get("mask_src"):
        m = Image.open(spec["mask_src"]).convert("L").resize(img.size, Image.LANCZOS)
    elif spec.get("mask", "alpha") == "alpha":
        m = img.getchannel("A")
    else:  # luminance
        m = img.convert("L")
    w = max(2, int(spec.get("size", 0.6) * 1080))
    h = max(2, int(img.height * w / img.width))
    cx = 540 + spec.get("cx", 0.0) * 960
    cy = 960 + spec.get("cy", 0.0) * 960
    x0, y0 = int(cx - w / 2), int(cy - h / 2)
    mask = Image.new("L", (1080, 1920), 0)
    mask.paste(m.resize((w, h), Image.LANCZOS), (x0, y0))
    rgb = Image.new("RGB", (1080, 1920), (0, 0, 0))
    rgb.paste(img.convert("RGB").resize((w, h), Image.LANCZOS), (x0, y0))
    return np.asarray(mask, np.float32) / 255.0, np.asarray(rgb, np.float32)


def prepare_pattern(spec):
    spec = dict(spec)
    if spec["kind"] == "image":
        mask, rgb = _raster_image(spec)
        spec["_mask_full"], spec["_rgb_full"] = mask, rgb
        small = Image.fromarray((mask * 255).astype(np.uint8)).resize((270, 480), Image.BILINEAR)
        sharp = np.asarray(small.filter(ImageFilter.GaussianBlur(1.2)), np.float32) / 255.0
        dist = _distance_cells(sharp > 0.5)
        # still inside the picture; outside, every point slopes toward the nearest edge of it
        out = np.minimum(1.0, dist / spec.get("reach", 300.0)) ** 0.6
        spec["_fgrid"] = _blur_float((1.0 - sharp) * out, 3.0).astype(np.float32)
    if spec["kind"] == "waves":
        rng = np.random.default_rng(spec.get("seed", 1))
        count = spec.get("count", 5)
        spec["_ang"] = rng.uniform(0, np.pi, count).astype(np.float32)
        spec["_ph"] = rng.uniform(0, 2 * np.pi, count).astype(np.float32)
        spec["_dir"] = np.where(np.arange(count) % 2 == 0, 1.0, -1.0).astype(np.float32)
    gx, gy = np.meshgrid(np.linspace(-0.5625, 0.5625, 160, dtype=np.float32),
                         np.linspace(-1, 1, 280, dtype=np.float32))
    spec["_norm"] = 1.0
    rms = float(np.sqrt(np.mean(field(spec, gx, gy, 0.0) ** 2))) + 1e-6
    spec["_norm"] = 0.5 / rms
    # shallow patterns (big spirals) pull sand in slowly; boost their pull so every shot resolves on time
    h = 1.5e-3
    f = field(spec, gx, gy, 0.0)
    g2 = ((field(spec, gx + h, gy, 0.0) - f) / h) ** 2 + ((field(spec, gx, gy + h, 0.0) - f) / h) ** 2
    spec["_eta"] = float(np.clip(G2_REF / (np.median(g2) + 1e-6), 1.0, 6.0))
    return spec


def field(spec, x, y, t):
    """Plate amplitude at (x, y) at scene time t, normalized per pattern. Sand collects where this is ~0."""
    return spec.get("_norm", 1.0) * _field(spec, x, y, t)


def _sample_grid(g, x, y):
    gh, gw = g.shape
    gx = (x + 0.5625) * (gw / 1.125) - 0.5
    gy = (y + 1.0) * (gh / 2.0) - 0.5
    x0 = np.floor(gx).astype(np.int32)
    y0 = np.floor(gy).astype(np.int32)
    fx, fy = gx - x0, gy - y0
    x0c, x1c = np.clip(x0, 0, gw - 1), np.clip(x0 + 1, 0, gw - 1)
    y0c, y1c = np.clip(y0, 0, gh - 1), np.clip(y0 + 1, 0, gh - 1)
    return (g[y0c, x0c] * (1 - fx) * (1 - fy) + g[y0c, x1c] * fx * (1 - fy)
            + g[y1c, x0c] * (1 - fx) * fy + g[y1c, x1c] * fx * fy)


def _field(spec, x, y, t):
    if spec["kind"] == "image":  # placed by size/cx/cy when prepared; no spin
        return _sample_grid(spec["_fgrid"], x, y)
    rot = spec.get("rot", 0.0) + spec.get("spin", 0.0) * t
    c, s = math.cos(rot), math.sin(rot)
    z = spec.get("zoom", 1.0) * (1.0 + spec.get("breathe", 0.03) * math.sin(0.8 * t))
    x = x - spec.get("cx", 0.0)
    y = y - spec.get("cy", 0.0)
    u = (c * x - s * y) * z
    v = (s * x + c * y) * z * spec.get("squash", 1.0)
    kind = spec["kind"]
    if kind == "rect":  # classic square-plate Chladni figure
        n, m = spec["n"], spec["m"]
        return 0.5 * (np.cos(n * np.pi * u) * np.cos(m * np.pi * v) - np.cos(m * np.pi * u) * np.cos(n * np.pi * v))
    if kind == "chirp":  # stripes that tighten along one axis: pace, the gaps closing
        return np.sin(spec["k"] * np.exp(spec.get("g", 1.6) * u))
    if kind == "grid":  # a grid of rounded squares, like the film's "Listen" shot
        n, m = spec["n"], spec.get("m", spec["n"])
        return (np.cos(n * np.pi * u) * np.cos(m * np.pi * v)) ** 2 - spec.get("level", 0.3)
    r = np.sqrt(u * u + v * v) + 1e-4
    th = np.arctan2(v, u)
    if kind == "polar":  # rings and spokes; stagger = brick web; hollow = calm center disk with a center dot
        k, n = spec["k"], spec["n"]
        r0 = spec.get("hollow", 0.0)
        rr = r - r0 if r0 else r
        radial = np.sin(k * rr + spec.get("phase", 0.0))
        ang = n * th
        if spec.get("stagger"):
            ang = ang + (np.pi / 2) * (np.floor((k * rr + spec.get("phase", 0.0)) / np.pi) % 2)
        f = -radial * np.sin(ang) if r0 else radial * np.sin(ang)
        if r0:
            f = np.where(r < r0, np.cos(0.5 * np.pi * r / r0), f)  # calm center: sand leaves it for the ring
        return f
    if kind == "spiral":  # rotating arms (n=1 is the film's S, n=2 its pinwheel)
        return np.sin(spec["n"] * th + spec["b"] * np.log(r)) * (0.55 + 0.45 * np.cos(spec.get("k", 0.0) * r))
    if kind == "voiceprint":  # radially symmetric lattice of cells, like the voice print tiles
        mix = spec.get("mix", 0.4)
        return (np.cos(spec["k"] * r) * np.cos(spec["n"] * th) + mix * np.cos(2 * spec["k"] * r)) / (1 + mix)
    if kind == "waves":  # organic meanders from a few crossing plane waves
        f = 0.0
        k = spec["k"]
        drift = spec.get("drift", 0.2) * t
        for a, p, d in zip(spec["_ang"], spec["_ph"], spec["_dir"]):
            f = f + np.cos(k * (u * math.cos(a) + v * math.sin(a)) + p + d * drift)
        return f / len(spec["_ang"])
    raise ValueError("unknown pattern kind: " + kind)


# ----------------------------------------------------------------------------- particles
class Sand:
    def __init__(self, n, ax, ay, free=0.035, seed=7):
        self.rng = np.random.default_rng(seed)
        self.ax, self.ay, self.n = ax, ay, n
        self.P = np.empty((n, 2), np.float32)
        loose = self.rng.random(n) < free  # a few grains never settle: the film's loose specks
        self.floor = np.where(loose, 0.55, 0.015).astype(np.float32)
        self.scatter()

    def scatter(self):
        self.P[:, 0] = self.rng.uniform(-self.ax, self.ax, self.n)
        self.P[:, 1] = self.rng.uniform(-self.ay, self.ay, self.n)

    def step(self, spec, t, sigma, eta, band, jitter, spread=0.0):
        x, y = self.P[:, 0], self.P[:, 1]
        if spread:  # a gentle pressure between grains: gathered grain spreads evenly through a shape
            gw, gh = 135, 240
            ix = np.clip(((x + self.ax) / (2 * self.ax) * gw).astype(np.int32), 0, gw - 1)
            iy = np.clip(((y + self.ay) / (2 * self.ay) * gh).astype(np.int32), 0, gh - 1)
            rho = np.bincount(iy * gw + ix, minlength=gw * gh).reshape(gh, gw).astype(np.float32)
            rho = _blur_float(rho, 2.5)
            rho /= rho.mean() + 1e-6
            gy_, gx_ = np.gradient(rho)
            self.P[:, 0] -= spread * gx_[iy, ix] * (gw / (2 * self.ax))
            self.P[:, 1] -= spread * gy_[iy, ix] * (gh / (2 * self.ay))
            x, y = self.P[:, 0], self.P[:, 1]
        f = field(spec, x, y, t)
        h = 1.5e-3
        fx = (field(spec, x + h, y, t) - f) / h
        fy = (field(spec, x, y + h, t) - f) / h
        g2 = fx * fx + fy * fy
        # grains bounce where the plate moves (sigma*|f|); band*|grad f| sets how wide the settled lines are
        std = sigma * (np.abs(f) + self.floor) + band * np.sqrt(g2) + jitter
        kick = self.rng.standard_normal(self.P.shape, dtype=np.float32) * std[:, None]
        pull = eta * f / (1.0 + eta * g2)  # implicit step: stable however steep the pattern
        self.P[:, 0] += kick[:, 0] - pull * fx
        self.P[:, 1] += kick[:, 1] - pull * fy
        for d, lim in ((0, self.ax), (1, self.ay)):  # reflect at the plate edges
            col = self.P[:, d]
            col[:] = np.where(col > lim, 2 * lim - col, col)
            col[:] = np.where(col < -lim, -2 * lim - col, col)
            np.clip(col, -lim, lim, out=col)


def agitation(s, ts, band_px):
    """sigma, eta, band, jitter for scene s at scene time ts (all per step)."""
    tau = s.get("settle", 0.12 if s["dur"] <= 1.01 else 0.22)  # short shots resolve faster
    sigma = SIG_BASE + SIG_CUT * math.exp(-ts / tau)
    eta_nom = ETA * s.get("pull", 1.0) * s.get("pattern", {}).get("_eta", 1.0)
    eta = eta_nom * (1.0 - math.exp(-ts / 0.15))
    if s.get("calm"):  # a continuation, not a cut: no shake, full pull from the first frame
        sigma, eta = SIG_BASE, eta_nom
    band = (s.get("band", band_px) / REF_UNIT_PX) * math.sqrt(2 * eta_nom)
    jitter = s.get("shimmer", 0.0)  # a little life inside still shapes, so gathered grain fills evenly
    dis = s.get("dissolve_last", 0.0)
    if dis and ts > s["dur"] - dis:  # the plate lets go: back to noise before the cut to black
        k = (ts - (s["dur"] - dis)) / dis
        sigma += SIG_CUT * 1.2 * k * k
        eta *= (1.0 - k) ** 2
        jitter += DISSOLVE_J * k * k
    return sigma, eta, band, jitter


# ----------------------------------------------------------------------------- drawing
class Renderer:
    def __init__(self, W, H, brand, render=None, seed=3):
        self.W, self.H = W, H
        self.unit = min(W, H) / PLATE           # px per plate unit
        self.ax, self.ay = W / 2 / self.unit, H / 2 / self.unit
        self.scale = min(W, H) / 1080.0
        brand = brand if isinstance(brand, dict) else load_brand(brand)
        self.brand = brand
        self.cfg = dict(RENDER, **(brand.get("render") or {}), **(render or {}))
        self.fonts = dict(brand["fonts"])
        self.logo_path = brand.get("logo")
        rng = np.random.default_rng(seed)
        self.grain = [rng.normal(0, self.cfg["grain"], (H, W, 1)).astype(np.float32) for _ in range(6)]

    def _blur(self, d, gain, radius):
        img = Image.fromarray(np.clip(d * gain, 0, 255).astype(np.uint8))
        if radius > 0:
            img = img.filter(ImageFilter.GaussianBlur(radius * self.scale))
        return np.asarray(img, np.float32) / gain

    def colormap(self, spec, amount=0.9):
        """The image's colors and mask cropped to this frame (a 4:5 frame is a center crop of the 9:16 plate)."""
        u = 960.0
        x0, x1 = int(round((0.5625 - self.ax) * u)), int(round((0.5625 + self.ax) * u))
        y0, y1 = int(round((1.0 - self.ay) * u)), int(round((1.0 + self.ay) * u))
        m = Image.fromarray((spec["_mask_full"] * 255).astype(np.uint8)).crop((x0, y0, x1, y1)).resize((self.W, self.H), Image.BILINEAR)
        rgb = Image.fromarray(spec["_rgb_full"].astype(np.uint8)).crop((x0, y0, x1, y1)).resize((self.W, self.H), Image.BILINEAR)
        m = np.asarray(m.filter(ImageFilter.GaussianBlur(1.5 * self.scale)), np.float32)[..., None] / 255.0 * amount
        return m, np.asarray(rgb, np.float32)

    def sand_frame(self, P, ground, over=None, cmap=None, bg_img=None):
        W, H = self.W, self.H
        c = dict(self.cfg, **(over or {}))  # a scene can override render keys
        ix = ((P[:, 0] + self.ax) * self.unit).astype(np.int32)
        iy = ((P[:, 1] + self.ay) * self.unit).astype(np.int32)
        ok = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
        d = np.bincount(iy[ok] * W + ix[ok], minlength=W * H).reshape(H, W).astype(np.float32)
        speck = self._blur(d, 85.0, c["speck_blur"])   # each grain a visible speck
        body = self._blur(d, 28.0, c["body_blur"])     # the rope of sand
        glow = self._blur(d, 28.0, c["glow_blur"])     # soft haze around it
        alpha = 1.0 - np.exp(-(c["speck_k"] * speck + c["body_k"] * body + c["glow_k"] * glow))
        hi = np.clip((body - c["hi_from"]) / c["hi_span"], 0, 1)
        g = GROUNDS[ground]
        bg = np.array(g["bg"], np.float32)
        ink = np.array(g["ink"], np.float32)
        hic = np.array(g["hi"], np.float32)
        col = ink[None, None, :] * (1 - hi[..., None]) + hic[None, None, :] * hi[..., None]
        if cmap is not None:  # grains inside the picture wear its colors
            m, rgb = cmap
            col = col * (1 - m) + rgb * m
        base = bg[None, None, :] if bg_img is None else bg_img
        return base * (1 - alpha[..., None]) + col * alpha[..., None]

    def add_grain(self, frame, i):
        return frame + self.grain[i % len(self.grain)]

    # --- type, logo, dot
    def font(self, name, size):
        f = ImageFont.truetype(self.fonts[name], max(8, int(round(size * self.scale))))
        return set_axes(f, (self.brand.get("font_axes") or {}).get(name))

    def text_patches(self, pieces, color, halo_color):
        """pieces: list of (text, font, x_px, y_px, anchor). Returns (x0, y0, [rgba arrays by blur level])."""
        layer = Image.new("RGBA", (self.W, self.H), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for text, font, x, y, anchor, *rest in pieces:
            d.text((x, y), text, font=font, fill=tuple(color) + (255,), anchor=anchor,
                   spacing=rest[0] if rest else 4, align="left" if anchor[0] == "l" else ("right" if anchor[0] == "r" else "center"))
        bbox = layer.getbbox()
        if not bbox:
            return None
        pad = int(28 * self.scale)
        x0, y0 = max(0, bbox[0] - pad), max(0, bbox[1] - pad)
        x1, y1 = min(self.W, bbox[2] + pad), min(self.H, bbox[3] + pad)
        crop = layer.crop((x0, y0, x1, y1))
        # a faint halo in the ground color so small type reads over moving sand
        a = crop.getchannel("A").filter(ImageFilter.GaussianBlur(10 * self.scale))
        halo = Image.new("RGBA", crop.size, tuple(halo_color) + (0,))
        halo.putalpha(a.point(lambda v: int(v * 0.55)))
        levels = []
        for r in (7, 4.5, 2.5, 1.2, 0):  # blur-in = gaining clarity
            t = crop if r == 0 else crop.filter(ImageFilter.GaussianBlur(r * self.scale))
            h = halo.copy()
            h.alpha_composite(t)
            levels.append(np.asarray(h, np.float32))
        return (x0, y0, levels)

    def composite(self, frame, patch, level, opacity=1.0):
        x0, y0, levels = patch
        arr = levels[max(0, min(level, len(levels) - 1))]
        h, w = arr.shape[:2]
        a = arr[..., 3:4] / 255.0 * opacity
        region = frame[y0:y0 + h, x0:x0 + w]
        frame[y0:y0 + h, x0:x0 + w] = region * (1 - a) + arr[..., :3] * a

    def logo_patch(self, width_rel, color, y_rel=0.5, src=None, x_rel=None):
        path = src or self.logo_path
        if not path:
            raise SystemExit("this storyboard places a logo: set \"logo\" in brand.json, or give the item a \"src\"")
        logo = Image.open(path).convert("RGBA")
        logo = logo.crop(logo.getchannel("A").getbbox())
        w = int(self.W * width_rel)
        h = int(logo.height * w / logo.width)
        a = logo.getchannel("A").resize((w, h), Image.LANCZOS)
        mark = Image.new("RGBA", (w, h), tuple(color) + (255,))
        mark.putalpha(a)
        pad = int(30 * self.scale)
        canvas = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        canvas.alpha_composite(mark, (pad, pad))
        x0 = (self.W - canvas.width) // 2 if x_rel is None else int(self.W * x_rel) - pad  # x_rel = left edge
        y0 = int(self.H * y_rel) - canvas.height // 2
        levels = [np.asarray(canvas.filter(ImageFilter.GaussianBlur(r * self.scale)) if r else canvas, np.float32)
                  for r in (12, 8, 5, 3, 1.5, 0)]
        return (x0, y0, levels)

    def dot_patch(self, color, radius=4.0, x_rel=0.5, y_rel=0.5):
        """The bright still point at the center of the end card."""
        R = int(48 * self.scale)
        yy, xx = np.mgrid[-R:R + 1, -R:R + 1].astype(np.float32)
        rr = xx * xx + yy * yy
        core = np.exp(-rr / (2 * (radius * self.scale) ** 2))
        halo = 0.28 * np.exp(-rr / (2 * (16 * self.scale) ** 2))
        rgba = np.zeros((2 * R + 1, 2 * R + 1, 4), np.float32)
        rgba[..., :3] = color
        rgba[..., 3] = np.clip(core + halo, 0, 1) * 255
        return (int(self.W * x_rel) - R, int(self.H * y_rel) - R, [rgba])


# ----------------------------------------------------------------------------- audio
# The launch film's score, measured: a lub-dub heartbeat (~50 Hz, second hit 0.16 s later) about once
# a second, a warm drone in the 100-400 Hz band that dips with each beat, two plate tones alternating
# every half beat (D minor pentatonic: F5/D5, G5/F5, C6/F5), and a struck-plate ping (~Bb5 with
# inharmonic partials at 2.6x, 4.2x, 6.8x) between beats. No voice. Integrated loudness about -14 LUFS.
PENTA = ["D4", "F4", "G4", "A4", "C5", "D5", "F5", "G5", "A5", "C6", "D6"]


def _band_noise(rng, n, sr, center, width):
    spec = np.fft.rfft(rng.standard_normal(n))
    fr = np.fft.rfftfreq(n, 1 / sr)
    spec *= np.exp(-0.5 * ((fr - center) / width) ** 2)
    x = np.fft.irfft(spec, n)
    return x / (np.abs(x).max() + 1e-9)


def _add(buf, i, seg):
    if i >= len(buf):
        return
    seg = seg[: len(buf) - i]
    buf[i:i + len(seg)] += seg


def synth_score(scenes, total, sr=48000, pulse=1.0, seed=5):
    rng = np.random.default_rng(seed)
    n = int(total * sr)
    t = np.arange(n) / sr
    L = np.zeros(n)
    R = np.zeros(n)
    end_pic = max(s["end"] for s in scenes)
    cuts = [s["start"] for s in scenes]
    beats = np.arange(0.0, end_pic - 0.05, pulse)

    # heartbeat duck for the drone: a quick dip on each hit, slow recovery
    duck = np.ones(n)
    m = int(0.5 * sr)
    k = np.arange(m) / sr
    for b in beats:
        for off, depth in ((0.0, 0.5), (0.16, 0.3)):
            i = int((b + off) * sr)
            if i < n:
                j = min(n, i + m)
                shape = 1 - depth * np.minimum(1, k / 0.01) * np.exp(-k / 0.14)
                duck[i:j] = np.minimum(duck[i:j], shape[: j - i])

    # warm drone (D3, F3, A3, D4), slightly detuned right channel
    env = np.clip(t / 2.5, 0, 1) * np.clip((total - t) / 2.0, 0, 1) * (0.8 + 0.2 * np.sin(2 * np.pi * 0.07 * t))
    for f, a in ((146.83, 0.017), (174.61, 0.01), (220.0, 0.013), (293.66, 0.006)):
        L += a * (np.sin(2 * np.pi * f * t) + 0.2 * np.sin(2 * np.pi * 2 * f * t)) * env * duck
        R += a * (np.sin(2 * np.pi * f * 1.002 * t + 0.7) + 0.2 * np.sin(2 * np.pi * 2 * f * 1.002 * t)) * env * duck

    # heartbeat: lub-dub sub hits, stronger on cuts
    k = np.arange(int(0.9 * sr)) / sr
    thump = np.sin(2 * np.pi * (50 * k + 16 * 0.04 * (1 - np.exp(-k / 0.04)))) * np.exp(-k / 0.3) * np.minimum(1, k / 0.004)
    for b in beats:
        g = 0.6 if any(abs(b - c) < 1e-6 for c in cuts) else 0.42
        for off, gg in ((0.0, g), (0.16, g * 0.6)):
            i = int((b + off) * sr)
            _add(L, i, thump * gg)
            _add(R, i, thump * gg)

    # struck-plate ping between beats; the fundamental rings on, the upper partials tick
    k = np.arange(int(2.2 * sr)) / sr
    ping = np.zeros(len(k))
    for ratio, amp, dec in ((1.0, 1.0, 1.8), (2.58, 0.45, 0.45), (4.2, 0.3, 0.25), (6.8, 0.2, 0.12), (10.6, 0.08, 0.06)):
        ping += amp * np.sin(2 * np.pi * 932.33 * ratio * k) * np.exp(-k / dec)
    ping *= np.minimum(1, k / 0.002)
    for b in beats[1:]:
        if b + 0.5 * pulse < end_pic - 0.1:
            i = int((b + 0.5 * pulse) * sr)
            _add(L, i, ping * 0.055)
            _add(R, i, ping * 0.048)

    # a faint bed of sand hiss under everything, like the grain on the picture
    bed = _band_noise(rng, n, sr, 7000, 2500) * 0.02 * np.clip(t / 0.5, 0, 1) * np.clip((end_pic - t) / 0.05, 0, 1)
    L += bed
    R += np.roll(bed, 997)

    burst_len = int(0.9 * sr)
    sand = _band_noise(rng, burst_len, sr, 5200, 2200)
    kb = np.arange(burst_len) / sr
    sand *= np.minimum(1, kb / 0.03) * np.exp(-kb / 0.28)

    for s in scenes:
        i0, i1 = int(s["start"] * sr), int(min(total, s["end"]) * sr)
        tones = s.get("tones")
        if not tones and s.get("tone") in PENTA:
            tones = [s["tone"], PENTA[max(0, PENTA.index(s["tone"]) - 1)]]
        if tones:  # two plate tones alternating every half beat, legato, with a gentle vibrato
            m = i1 - i0
            tt = np.arange(m) / sr
            idx = np.floor(tt / (0.5 * pulse)).astype(int) % len(tones)
            freq = np.array([NOTES[x] for x in tones])[idx]
            freq = np.convolve(freq, np.ones(480) / 480, mode="same")  # 10 ms glide, no clicks
            freq[:240] = freq[240]
            freq[-240:] = freq[-241]
            freq = freq * (1 + 0.0025 * np.sin(2 * np.pi * 4.8 * tt))
            ph = 2 * np.pi * np.cumsum(freq) / sr  # integrate frequency: the vibrato stays gentle
            a = np.minimum(1, tt / 0.09) * np.minimum(1, (tt[-1] - tt + 1e-3) / 0.12)
            tone = (np.sin(ph) * 0.12 + np.sin(2 * ph) * 0.02) * a
            L[i0:i1] += tone
            R[i0:i1] += tone * 0.92
        seg = sand[: max(0, min(burst_len, n - i0))] * 0.2  # sand shaken loose on the cut
        L[i0:i0 + len(seg)] += seg
        R[i0:i0 + len(seg)] += seg * 0.85
        dis = s.get("dissolve_last", 0.0)
        if dis:  # the sand letting go, cut dead with the picture
            j0 = int((s["end"] - dis) * sr)
            m = i1 - j0
            hiss = _band_noise(rng, m, sr, 3600, 2200) * (np.arange(m) / m) ** 2 * 0.14
            L[j0:i1] += hiss
            R[j0:i1] += hiss * 0.9

    mix = np.stack([L, R], axis=1)
    spec = np.fft.rfft(mix, axis=0)  # high-pass at ~30 Hz: keep the heartbeat, lose the rumble
    fr = np.fft.rfftfreq(n, 1 / sr)
    spec *= (1 / np.sqrt(1 + (30.0 / np.maximum(fr, 1e-3)) ** 8))[:, None]
    mix = np.fft.irfft(spec, n, axis=0)
    mix = np.tanh(mix * 1.1)
    mix *= 0.89 / (np.abs(mix).max() + 1e-9)
    return mix, sr



# ----------------------------------------------------------------------------- score: resonance (default)
# Written for the picture rather than copied from the film. On every cut the sound starts as sand hiss
# and resolves into a chord as the pattern forms (noise into harmony, on the same clock as the sand);
# a felt piano lands on the cut and on each super, logo, or dot as it appears; a grain layer follows
# the sand's agitation frame by frame; one long room holds it together. On the end card the chord
# falls back into hiss, and the cut to black leaves only the room ringing out.
_PC = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
       "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def hz(note):
    name, octave = note[:-1], int(note[-1])
    return 440.0 * 2 ** ((12 * (octave + 1) + _PC[name] - 69) / 12)


def _fftconv(x, k):
    n = len(x) + len(k) - 1
    nf = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, nf) * np.fft.rfft(k, nf), nf)[:n]


def felt_piano(rng, f0, dur, sr, vel=0.5):
    """A soft, felted piano note: stretched partials, two detuned strings, two-stage decay, a low thump."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    out = np.zeros(n)
    bright = 0.2 + 0.5 * vel
    tau0 = 3.2 * (261.6 / f0) ** 0.3
    for k in range(1, 16):
        fk = k * f0 * math.sqrt(1 + 0.00035 * k * k)
        if fk > 9000:
            break
        amp = math.exp(-(k - 1) * (1.15 - bright)) / math.sqrt(k)
        tau = tau0 / (1 + 0.3 * (k - 1))
        env = 0.6 * np.exp(-t / tau) + 0.4 * np.exp(-t / (0.16 * tau))
        for cents in (-0.4, 0.4):
            out += 0.5 * amp * env * np.sin(2 * np.pi * fk * 2 ** (cents / 1200) * t + rng.uniform(0, 2 * np.pi))
    out *= np.minimum(1, t / 0.006)
    m = int(0.03 * sr)
    out[:m] += _band_noise(rng, m, sr, 380, 260) * np.exp(-np.arange(m) / sr / 0.007) * 0.12
    return out * vel / 2.2


def resonance_pad(rng, freqs, dur, sr, tau, dissolve=0.0):
    """Hiss that organizes into a chord: each partial's phase wanders widely before the pattern forms
    (a smeared noise band) and settles into a steady tone as it forms."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    form = 1 - np.exp(-t / tau)
    if dissolve:
        k = np.clip((t - (dur - dissolve)) / dissolve, 0, 1)
        form = form * (1 - k) ** 2
    smear = 38.0 * (1 - form) + 1.2
    L = np.zeros(n)
    R = np.zeros(n)
    for f0 in freqs:
        for h, ha in ((1, 1.0), (2, 0.4), (3, 0.18), (4, 0.08), (5, 0.04)):
            fh = f0 * h * 1.0006 ** h
            if fh > 7000:
                break
            low = fh < 220
            for buf, cents in ((L, -3.5), (R, 3.5)):
                wander = np.cumsum(rng.standard_normal(n)) / math.sqrt(sr)
                ph = 2 * np.pi * fh * 2 ** (cents / 1200) * t + (1.2 if low else smear) * wander + rng.uniform(0, 2 * np.pi)
                lfo = 1 + 0.15 * np.sin(2 * np.pi * rng.uniform(0.06, 0.2) * t + rng.uniform(0, 2 * np.pi))
                buf += ha * lfo * (form ** 1.5 if low else 1.0) * np.sin(ph)
    g = 1 / math.sqrt(len(freqs))
    bloom = 0.7 + 0.3 * form - 0.22 * np.clip((t - 0.6) / max(0.1, dur - 0.6), 0, 1)
    hiss = _band_noise(rng, n, sr, 2800, 2600) * (1 - form) ** 1.2 * 1.8
    edge = np.minimum(1, t / 0.004) * np.minimum(1, (t[-1] - t + 1e-4) / 0.03)
    return (L * g * bloom + hiss) * edge, (R * g * bloom + np.roll(hiss, 311)) * edge


def reverb_ir(sr, rt60=2.6, seed=11):
    rng = np.random.default_rng(seed)
    n = int(rt60 * 1.1 * sr)
    t = np.arange(n) / sr
    fr = np.fft.rfftfreq(n, 1 / sr)
    chans = []
    for _ in range(2):
        spec = np.fft.rfft(rng.standard_normal(n))
        ir = np.zeros(n)
        for lo, hi, rt in ((0, 500, rt60 * 0.6), (500, 4000, rt60 * 0.85), (4000, sr / 2, rt60 * 0.4)):
            band = np.fft.irfft(spec * ((fr >= lo) & (fr < hi)), n)
            ir += band * np.exp(-6.91 * t / rt)
        pre = int(0.02 * sr)
        ir = np.concatenate([np.zeros(pre), ir * np.minimum(1, t / 0.015)])[:n]
        chans.append(ir / math.sqrt(np.sum(ir ** 2)))
    return chans


def synth_resonance(scenes, total, sr=48000, pulse=1.0, seed=9, band_px=9.0):
    rng = np.random.default_rng(seed)
    n = int(total * sr)
    pad = [np.zeros(n), np.zeros(n)]
    pno = [np.zeros(n), np.zeros(n)]
    grains = [np.zeros(n), np.zeros(n)]

    def put(bufs, i, sig_l, sig_r):
        if i >= n:
            return
        m = min(len(sig_l), n - i)
        bufs[0][i:i + m] += sig_l[:m]
        bufs[1][i:i + m] += sig_r[:m]

    for s in scenes:
        i0 = int(s["start"] * sr)
        chord = [hz(x) for x in s.get("chord", [])]
        if not chord:  # no harmony given: an open fifth under the scene's plate tone
            f = hz(s["tone"]) if s.get("tone") else hz("D4")
            chord = [f / 4, f * 3 / 8, f / 2, f * 9 / 16]
        tau = s.get("settle", 0.12 if s["dur"] <= 1.01 else 0.22)
        pl, pr = resonance_pad(rng, chord, s["dur"], sr, tau * 1.6, s.get("dissolve_last", 0.0))
        g = float(s.get("gain", 1.0))  # a scene can sit back (a drop) or push (a build)
        put(pad, i0, pl * g, pr * g)
        notes = [(s["bass"], 0.0, float(s.get("bass_vel", 0.3)))] if s.get("bass") else []
        # melody items are [note, seconds] or [note, seconds, velocity]
        notes += [(m[0], float(m[1]), float(m[2]) if len(m) > 2 else 0.46) for m in s.get("melody", [])]
        for nm, at, vel in notes:
            if at >= s["dur"]:
                continue
            f0 = hz(nm)
            while f0 < 70:  # keep the bass where phones and headphones both carry it cleanly
                f0 *= 2
            # dampers come down at the next cut, like the picture: bass stops clean, melody fades quickly
            hold = s["dur"] - at + (0.12 if f0 < 180 else 0.5)
            note = felt_piano(rng, f0, hold + 0.3, sr, vel)
            k = np.arange(len(note)) / sr
            note *= np.clip(1 - (k - hold) / 0.3, 0, 1) ** 2
            pan = 0.5 if f0 < 180 else min(0.8, max(0.2, 0.5 + math.log2(f0 / 262) * 0.18))
            put(pno, i0 + int(at * sr), note * math.sqrt(1 - pan), note * math.sqrt(pan))

    # grains: the sand heard, one short tick per bouncing grain, dense on the cut, sparse once it settles
    fps = STEPS_PER_SEC
    impl, impr = np.zeros(n), np.zeros(n)
    for s in scenes:
        for i in range(int(s["dur"] * fps)):
            ts = i / fps
            sigma, _, _, jitter = agitation(s, ts, band_px)
            inten = max(0.0, (sigma - SIG_BASE) / SIG_CUT) + jitter / DISSOLVE_J
            cnt = rng.poisson((20 + 1300 * inten) * float(s.get("grain_gain", 1.0)) / fps)
            if cnt:
                pos = np.clip(int((s["start"] + ts) * sr) + rng.integers(0, sr // fps, cnt), 0, n - 1)
                amp = rng.lognormal(0, 0.5, cnt) * rng.choice([-1.0, 1.0], cnt)
                pan = rng.random(cnt)
                np.add.at(impl, pos, amp * np.sqrt(1 - pan))
                np.add.at(impr, pos, amp * np.sqrt(pan))
    klen = int(0.004 * sr)
    kern = _band_noise(rng, klen, sr, 5600, 2400) * np.hanning(klen)
    grains = [_fftconv(impl, kern)[:n], _fftconv(impr, kern)[:n]]

    irl, irr = reverb_ir(sr)

    def rms(x):
        return math.sqrt(np.mean(x[0] ** 2 + x[1] ** 2) / 2) + 1e-9

    # stems to fixed levels, then each gets its share of the room
    pad = [x * (0.07 / rms(pad)) for x in pad]
    pno = [x * (0.11 / rms(pno)) for x in pno]
    grains = [x * (0.022 / rms(grains)) for x in grains]
    dry_l = pad[0] * 0.85 + pno[0] * 0.8 + grains[0] * 0.7
    dry_r = pad[1] * 0.85 + pno[1] * 0.8 + grains[1] * 0.7
    send_l = pad[0] * 0.5 + pno[0] * 0.6 + grains[0] * 0.25
    send_r = pad[1] * 0.5 + pno[1] * 0.6 + grains[1] * 0.25
    fr_s = np.fft.rfftfreq(n, 1 / sr)
    hp = 1 / np.sqrt(1 + (160.0 / np.maximum(fr_s, 1e-3)) ** 4)  # keep the lows out of the room
    send_l = np.fft.irfft(np.fft.rfft(send_l) * hp, n)
    send_r = np.fft.irfft(np.fft.rfft(send_r) * hp, n)
    L = dry_l + _fftconv(send_l, irl)[:n] * 0.9
    R = dry_r + _fftconv(send_r, irr)[:n] * 0.9
    mix = np.stack([L, R], axis=1)
    fade = int(0.25 * sr)
    mix[-fade:] *= np.linspace(1, 0, fade)[:, None]
    spec = np.fft.rfft(mix, axis=0)  # gentle air roll-off and rumble cut
    fr = np.fft.rfftfreq(n, 1 / sr)
    shape = 1 / np.sqrt(1 + (fr / 11000.0) ** 4) / np.sqrt(1 + (35.0 / np.maximum(fr, 1e-3)) ** 6)
    mix = np.fft.irfft(spec * shape[:, None], n, axis=0)
    mix *= 0.6 / (np.abs(mix).max() + 1e-9)
    mix = np.tanh(mix)
    mix *= 0.89 / (np.abs(mix).max() + 1e-9)
    return mix, sr


def measure_lufs(path):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    vals = [line.split("I:")[1].split("LUFS")[0] for line in out.splitlines() if line.strip().startswith("I:")]
    return float(vals[-1]) if vals else None


def write_score(path, scenes, total, pulse, target=-14.0, style="resonance", band_px=9.0):
    """Synthesize, write, and trim to about -14 LUFS integrated with peaks under -1 dBFS.
    style "resonance" (default) is written for the picture; "film" is the launch film's measured score."""
    if style == "film":
        mix, sr = synth_score(scenes, total, pulse=pulse)
    else:
        mix, sr = synth_resonance(scenes, total, pulse=pulse, band_px=band_px)
    write_wav(path, mix, sr)
    lufs = measure_lufs(path)
    if lufs is not None:
        mix = mix * 10 ** ((target - lufs) / 20)
        knee, ceil = 0.6, 0.89  # a soft knee on the peaks only; everything under the knee is untouched
        a = np.abs(mix)
        over = a > knee
        mix[over] = np.sign(mix[over]) * (knee + (ceil - knee) * np.tanh((a[over] - knee) / (ceil - knee)))
        write_wav(path, mix, sr)
    return measure_lufs(path)


def write_wav(path, mix, sr):
    data = (np.clip(mix, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data.tobytes())


# ----------------------------------------------------------------------------- storyboard
def build_timeline(board, only=None):
    pulse = float(board.get("pulse", 1.0))
    scenes, t = [], 0.0
    for s in board["scenes"]:
        if only and s.get("id") not in only:
            continue
        dur = float(s.get("seconds", s.get("pulses", 2) * pulse))
        s = dict(s, start=t, end=t + dur, dur=dur)
        s["pattern"] = prepare_pattern(s["pattern"])
        scenes.append(s)
        t += dur
    if not scenes:
        raise SystemExit("no scenes selected")
    return scenes, t, pulse


def layout_text(r, item):
    """A storyboard text item -> [(delay, pieces)]. Supports split|lines, word reveals, and ( parentheticals )."""
    size = item.get("size", 50)
    fnt = r.font(item.get("font", "sans"), size)
    cx = item.get("x", 0.5) * r.W
    cy = item.get("y", 0.5) * r.H
    text = item["text"]
    if "|" in text:  # split around the center point, like the film's "shape your · voice print"
        left, right = (p.strip() for p in text.split("|", 1))
        gap = item.get("gap", 34) * r.scale
        return [(0.0, [(left, fnt, cx - gap, cy, "rm"), (right, fnt, cx + gap, cy, "lm")])]
    if item.get("paren_reveal") and text.startswith("( ") and text.endswith(" )"):
        inner = text[2:-2]
        left = cx - fnt.getlength(text) / 2
        return [
            (0.0, [("( ", fnt, left, cy, "lm"), (" )", fnt, left + fnt.getlength("( " + inner), cy, "lm")]),
            (item.get("inner_delay", 0.45), [(inner, fnt, left + fnt.getlength("( "), cy, "lm")]),
        ]
    if item.get("reveal") == "words":  # word by word, like the film's "How / words / arrive"
        words = text.split(" ")
        gap_t = item.get("word_gap", 0.3)
        if item.get("spread"):
            step = item.get("spread_step", 0.27)
            xs = [r.W * (item.get("x", 0.5) + (i - (len(words) - 1) / 2) * step) for i in range(len(words))]
            return [(i * gap_t, [(w, fnt, xs[i], cy, "mm")]) for i, w in enumerate(words)]
        x = cx - fnt.getlength(text) / 2
        out = []
        for i, w in enumerate(words):
            out.append((i * gap_t, [(w, fnt, x, cy, "lm")]))
            x += fnt.getlength(w + " ")
        return out
    anchor = item.get("anchor", "la" if "\n" in text else "mm")
    return [(0.0, [(text, fnt, cx, cy, anchor, int(item.get("leading", 0.2) * size * r.scale))])]


def prepare_items(r, scenes):
    prev = None
    for s in scenes:
        if s["pattern"]["kind"] == "image" and s.get("ink_image"):
            s["_cmap"] = r.colormap(s["pattern"], float(s["ink_image"]))
        if prev is not None and not s.get("reset", True) and prev.get("_cmap") is not None:
            s["_prev_cmap"] = prev["_cmap"]
        prev = s
        g = GROUNDS[s["ground"]]
        s["_items"] = []
        for item in s.get("texts", []):
            for delay, pieces in layout_text(r, item):
                patch = r.text_patches(pieces, item.get("color", g["text"]), g["bg"])
                if patch:
                    s["_items"].append({"patch": patch, "at": item.get("at", 0.1) + delay, "until": item.get("until")})
        if s.get("logo"):
            lg = s["logo"]
            s["_items"].append({"patch": r.logo_patch(lg.get("width", 0.3), lg.get("color", g["text"]), lg.get("y", 0.5)),
                                "at": lg.get("at", 0.15), "until": lg.get("until")})
        if s.get("dot"):
            dt = s["dot"]
            s["_items"].append({"patch": r.dot_patch(dt.get("color", g["hi"]), dt.get("radius", 4.0)),
                                "at": dt.get("at", 0.3), "until": dt.get("until"), "fade": 0.4})


def advance(sand, s, ts, steps, band_px):
    sigma, eta, band, jitter = agitation(s, ts, band_px)
    spread = s.get("spread", 0.0)
    for _ in range(steps):
        sand.step(s["pattern"], ts, sigma, eta, band, jitter, spread)


def draw(r, sand, s, ts, fi):
    cmap = None
    if s.get("_cmap") is not None or s.get("_prev_cmap") is not None:
        parts = []
        if s.get("_cmap") is not None:
            parts.append((s["_cmap"], 1 - math.exp(-ts / s.get("color_in", 0.6))))
        if s.get("_prev_cmap") is not None and ts < 0.5:
            parts.append((s["_prev_cmap"], 1 - ts / 0.5))
        m = sum(c[0] * w for c, w in parts)
        rgb = sum(c[1] * c[0] * w for c, w in parts) / np.maximum(m, 1e-4)
        cmap = (np.clip(m, 0, 1), rgb)
    frame = r.sand_frame(sand.P, s["ground"], s.get("render"), cmap)
    dis = s.get("dissolve_last", 0.0)
    rem = s["dur"] - ts
    for it in s["_items"]:
        if ts < it["at"] or (it["until"] is not None and ts > it["until"]):
            continue
        level = int((ts - it["at"]) * 15)  # one blur level per 1/15 s: gaining clarity
        opacity = min(1.0, (ts - it["at"]) / it["fade"]) if it.get("fade") else 1.0
        if dis:  # losing it again as the plate lets go
            level = min(level, int(rem * 8))
            opacity *= min(1.0, max(0.0, (rem - 0.04) / 0.35))
        r.composite(frame, it["patch"], level, opacity)
    return r.add_grain(frame, fi)


def sheet_image(frames, W, H, cols=8, tw=240):
    th = int(tw * H / W)
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 6), rows * (th + 22)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(frames):
        x, y = (i % cols) * (tw + 6), (i // cols) * (th + 22)
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y))
        d.text((x + 3, y + th + 4), label, fill=(0, 0, 0))
    return sheet


def to_image(frame):
    return Image.fromarray(np.clip(frame, 0, 255).astype(np.uint8))


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Render a particle motion storyboard.")
    ap.add_argument("storyboard")
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", default="1080x1920")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--brand", default="", help="path to brand.json (or a folder holding one)")
    ap.add_argument("--preview", action="store_true", help="half size, 15 fps, same timing")
    ap.add_argument("--sheet", default=None, help="also write a contact sheet (two frames per second)")
    ap.add_argument("--stills", action="store_true", help="write one still per scene (at 80%% of it) instead of a video")
    ap.add_argument("--still", default=None, help="write one frame, SCENE_ID@SECONDS, instead of a video")
    ap.add_argument("--only", default=None, help="comma-separated scene ids to render")
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--remux", action="store_true",
                    help="only re-synthesize the score and swap it into an existing --out video")
    args = ap.parse_args()

    board = resolve_paths(json.load(open(args.storyboard, encoding="utf-8")),
                          os.path.dirname(os.path.abspath(args.storyboard)))
    W, H = (int(v) for v in args.size.lower().split("x"))
    fps = args.fps
    if args.preview:
        W, H, fps = (W // 2) & ~1, (H // 2) & ~1, 15  # even sizes for H.264
    steps = max(1, round(STEPS_PER_SEC / fps))
    only = set(args.only.split(",")) if args.only else None
    if args.still:
        sid, _, sec = args.still.partition("@")
        only = {sid}
    scenes, total, pulse = build_timeline(board, only)
    r = Renderer(W, H, load_brand(find_brand(args.brand, args.storyboard)), board.get("render"))
    sand = Sand(int(board.get("particles_per_px", 0.2) * W * H), r.ax, r.ay,
                free=board.get("free_grains", 0.035), seed=board.get("seed", 7))
    band_px = board.get("band", 9.0)
    prepare_items(r, scenes)

    if args.still or args.stills:  # stills: simulate each scene from its cut, keep one frame
        shots = []
        for s in scenes:
            at = float(sec) if args.still else s.get("still", 0.8) * s["dur"]
            sand.scatter()
            nf = int(round(at * fps))
            for fi in range(nf + 1):
                advance(sand, s, fi / fps, steps, band_px)
            shots.append((f"{s['id']} @ {at:.2f}s", to_image(draw(r, sand, s, nf / fps, nf))))
        if args.still:
            shots[0][1].save(args.out)
        else:
            sheet_image(shots, W, H, cols=min(5, len(shots)), tw=360).save(args.out)
        print("done:", args.out)
        return

    tail = float(board.get("tail", 0.0))
    audio_path = os.path.splitext(args.out)[0] + "_score.wav"
    if not args.no_audio:
        lufs = write_score(audio_path, scenes, total + tail, pulse, style=board.get("score", "resonance"),
                           band_px=band_px)
        print(f"  score: {audio_path} ({lufs} LUFS)")
    if args.remux:  # new score into an existing render, picture untouched
        tmp = os.path.splitext(args.out)[0] + "_remux.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", args.out, "-i", audio_path, "-map", "0:v", "-map", "1:a",
                        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", tmp],
                       check=True)
        os.replace(tmp, args.out)
        print("remuxed:", args.out)
        return

    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-"]
    if not args.no_audio:
        cmd += ["-i", audio_path, "-c:a", "aac", "-b:a", "192k"]
    # grain barely compresses: cap the bitrate (platforms re-encode to a few Mbps anyway)
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-maxrate", "24M", "-bufsize", "48M", "-preset", "medium",
            "-movflags", "+faststart", "-shortest", args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    nframes = int(round(total * fps))
    sheet_frames = []
    si = 0
    for fi in range(nframes):
        t = fi / fps
        while si < len(scenes) - 1 and t >= scenes[si]["end"] - 1e-9:
            si += 1
            if scenes[si].get("reset", True):
                sand.scatter()  # every cut starts as noise
        s = scenes[si]
        ts = t - s["start"]
        advance(sand, s, ts, steps, band_px)
        out = np.clip(draw(r, sand, s, ts, fi), 0, 255).astype(np.uint8)
        enc.stdin.write(out.tobytes())
        if args.sheet and fi % max(1, fps // 2) == 0:
            sheet_frames.append((f"{t:.1f}s", Image.fromarray(out)))
        if fi % (fps * 2) == 0:
            print(f"  {t:5.1f}s / {total:.1f}s", flush=True)
    black = np.zeros((H, W, 3), np.uint8).tobytes()  # hard cut to black, as the film ends
    for _ in range(int(round(tail * fps))):
        enc.stdin.write(black)
    enc.stdin.close()
    enc.wait()

    if args.sheet and sheet_frames:
        sheet_image(sheet_frames, W, H).save(args.sheet)
    print("done:", args.out, f"{W}x{H} @ {fps}fps, {total:.1f}s + {tail:.1f}s black")


if __name__ == "__main__":
    main()
