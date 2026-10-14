#!/usr/bin/env python3
"""Footage and a machine-vision overlay for fieldfilm.py: the "tracking" look.

FootageReader  plays a conformed clip (already cropped to the frame size and rate) frame by frame,
               sequentially through one ffmpeg pipe, reopening at a seek only when a frame is asked
               for out of order (stills).
TrackOverlay   draws what tracker.py measured: a box on every tracked point that locks on after the
               cut (the tracker starts from zero at every cut), ids, links to a neighbour, a crosshair
               on a single subject, and a small HUD (feed, category, timecode, OBJECTS, CONTEXT).
Converge       the release: tracked boxes break into small squares that fly into the cells of a
               figure; the time each square lands is when that cell's character lights up.

Everything is drawn in one colour (the ground's text colour) with a soft dark shadow so it reads on
bright footage too. Type is the brand's mono.
"""
import json
import math
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def smooth(a, b, x):
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


class FootageReader:
    def __init__(self, path, W, H, fps):
        self.path, self.W, self.H, self.fps = path, W, H, fps
        self.proc, self.k, self.last = None, -10, None

    def _open(self, k):
        self.close()
        t = k / self.fps
        self.proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", self.path, "-vf",
                                      f"scale={self.W}:{self.H},fps={self.fps}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.k = k - 1

    def frame(self, k):
        if self.proc is None or k != self.k + 1:
            if k == self.k and self.last is not None:
                return self.last
            self._open(k)
        n = self.W * self.H * 3
        buf = self.proc.stdout.read(n)
        if len(buf) == n:
            self.last = np.frombuffer(buf, np.uint8).reshape(self.H, self.W, 3)
        elif self.last is None:
            self.last = np.zeros((self.H, self.W, 3), np.uint8)
        self.k = k
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


class TrackOverlay:
    def __init__(self, tracks_path, spec, W, H, scale, font_path, fps):
        self.T = json.load(open(tracks_path))
        self.spec, self.W, self.H, self.s, self.fps = spec, W, H, scale, fps
        sx, sy = W / float(self.T["w"]), H / float(self.T["h"])
        self.frames = [[(i, x * sx, y * sy) for i, x, y in f] for f in self.T["frames"]]
        self.target = [(x * sx, y * sy) for x, y in self.T.get("target", [])]
        self.font = ImageFont.truetype(font_path, max(8, int(round(17 * scale))))
        self.font_hud = ImageFont.truetype(font_path, max(8, int(round(22 * scale))))
        # when each id was first seen, and its partner (nearest point at that moment)
        self.first, self.partner = {}, {}
        for k, fr in enumerate(self.frames):
            P = {i: (x, y) for i, x, y in fr}
            for i, (x, y) in P.items():
                if i not in self.first:
                    self.first[i] = k
                    best, bd = None, 1e18
                    for j, (u, v) in P.items():
                        if j != i:
                            d = (u - x) ** 2 + (v - y) ** 2
                            if d < bd:
                                best, bd = j, d
                    if best is not None and bd < (340 * scale) ** 2:
                        self.partner[i] = best
        rng = np.random.default_rng(int(spec.get("feed", 1)) * 101)
        ids = sorted(self.first)
        self.size = {i: float(rng.uniform(22, 58)) * scale for i in ids}
        self.kind = {i: ("ticks" if rng.random() < 0.22 else "box") for i in ids}
        self.coord = {i: rng.random() < 0.28 for i in ids}
        order = rng.permutation(len(ids))
        # at the cut nothing is known: boxes lock on one after another over the first half second
        self.delay = {i: 0.02 + 0.55 * (r / max(1, len(ids) - 1)) ** 0.8 for i, r in zip(ids, order)}

    def points(self, k):
        """Points for frame k. `xform` (ax, bx, ay, by), when set, maps them through a zoom/pan crop."""
        k = min(max(0, k), len(self.frames) - 1)
        xf = getattr(self, "xform", None)
        if xf:
            ax, bx, ay, by = xf
            return [(i, ax * x + bx, ay * y + by) for i, x, y in self.frames[k]]
        return self.frames[k]

    def draw(self, layer, t, k, color, alpha=1.0, release=None, hud=None):
        """Draw onto an RGBA PIL layer. t: shot time; k: frame index in the clip. release: (t0, out)
        boxes fade and shrink after t0 over `out` seconds (they break into squares)."""
        d = ImageDraw.Draw(layer)
        col = tuple(int(c) for c in color)
        fr = self.points(k)
        P = {i: (x, y) for i, x, y in fr}
        vis = {}
        for i, (x, y) in P.items():
            tl = t - self.first[i] / self.fps - (self.delay[i] if self.first[i] == 0 else 0.03)
            if tl <= 0:
                continue
            a = smooth(0.0, 0.08, tl)
            grow = 1.0 + 0.6 * (1 - smooth(0.0, 0.1, tl))
            if release is not None and t > release[0]:
                u = smooth(release[0], release[0] + release[1], t)
                a *= 1 - u
                grow *= 1 - 0.7 * u
            if a > 0.01:
                vis[i] = (x, y, a * alpha, grow)
        # links under the boxes
        for i, (x, y, a, _) in vis.items():
            j = self.partner.get(i)
            if j in vis:
                u, v, b, _ = vis[j]
                d.line([(x, y), (u, v)], fill=col + (int(110 * min(a, b)),), width=max(1, int(round(1.5 * self.s))))
        for i, (x, y, a, grow) in vis.items():
            r = self.size[i] * grow / 2
            A = int(235 * a)
            w = max(1, int(round(2 * self.s)))
            if self.kind[i] == "box":
                d.rectangle([x - r, y - r, x + r, y + r], outline=col + (A,), width=w)
            else:
                L = r * 0.45
                for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                    cx, cy = x + sx * r, y + sy * r
                    d.line([(cx, cy), (cx - sx * L, cy)], fill=col + (A,), width=w)
                    d.line([(cx, cy), (cx, cy - sy * L)], fill=col + (A,), width=w)
            lab = f"{i:03d}"
            d.text((x - r, y - r - 4 * self.s), lab, font=self.font, fill=col + (int(A * 0.9),), anchor="lb")
            if self.coord[i]:
                d.text((x + r + 5 * self.s, y + r), f"{x / self.W:.3f} {y / self.H:.3f}", font=self.font,
                       fill=col + (int(A * 0.7),), anchor="ls")
        if self.spec.get("style") == "target" and self.target:
            kk = min(max(0, k), len(self.target) - 1)
            x, y = self.target[kk]
            xf = getattr(self, "xform", None)
            if xf:
                x, y = xf[0] * x + xf[1], xf[2] * y + xf[3]
            ta = smooth(0.05, 0.25, t) * alpha
            if release is not None and t > release[0]:
                ta *= 1 - smooth(release[0], release[0] + release[1], t)
            if ta > 0.01:
                A = int(245 * ta)
                R = 92 * self.s * (1 + 0.5 * (1 - smooth(0.05, 0.3, t)))
                w = max(1, int(round(2 * self.s)))
                d.ellipse([x - R, y - R, x + R, y + R], outline=col + (A,), width=w)
                g = R * 0.35
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    d.line([(x + dx * (R - 14 * self.s), y + dy * (R - 14 * self.s)), (x + dx * (R + 26 * self.s), y + dy * (R + 26 * self.s))],
                           fill=col + (A,), width=w)
                    d.line([(x + dx * 6 * self.s, y + dy * 6 * self.s), (x + dx * g, y + dy * g)], fill=col + (int(A * 0.6),), width=w)
                d.text((x + R + 34 * self.s, y - 6 * self.s), "TARGET 01", font=self.font, fill=col + (A,), anchor="ls")
                d.text((x + R + 34 * self.s, y + 18 * self.s), f"CONF 0.{97 - (k % 5)}", font=self.font, fill=col + (int(A * 0.7),), anchor="ls")
        return len(vis)

    def hud(self, layer, t_film, t_shot, objects, context, color, alpha, spec):
        d = ImageDraw.Draw(layer)
        col = tuple(int(c) for c in color)
        A = int(235 * alpha)
        if A <= 2:
            return
        mx, my = [v * self.s for v in spec.get("margin", [56, 150])]
        f = self.font_hud
        lh = 30 * self.s
        feed = int(self.spec.get("feed", 1))
        d.text((mx, my), f"FEED {feed:02d}", font=f, fill=col + (A,), anchor="ls")
        d.text((mx, my + lh), str(self.spec.get("label", "")), font=f, fill=col + (int(A * 0.62),), anchor="ls")
        fr = int(round(t_film * self.fps))
        tc = f"00:00:{fr // self.fps:02d}:{fr % self.fps:02d}"
        d.text((self.W - mx, my), tc, font=f, fill=col + (A,), anchor="rs")
        blink = (int(t_film * 2.5) % 2) == 0
        sq = 9 * self.s
        tw = d.textlength("TRACKING", font=f)
        d.text((self.W - mx, my + lh), "TRACKING", font=f, fill=col + (int(A * 0.62),), anchor="rs")
        if blink:
            x0 = self.W - mx - tw - 16 * self.s
            d.rectangle([x0 - sq, my + lh - sq * 1.6, x0, my + lh - sq * 0.6], fill=col + (int(A * 0.8),))
        by = spec.get("bottom", 0.745) * self.H
        d.text((mx, by), f"OBJECTS {objects:03d}", font=f, fill=col + (A,), anchor="ls")
        d.text((mx, by + lh), f"CONTEXT {int(round(context * 100))}%", font=f, fill=col + (int(A * 0.62),), anchor="ls")


class Converge:
    """Tracked boxes break into squares that fly into a figure's cells; returns each cell's landing time."""

    def __init__(self, origins, cells, t0, W, H, scale, seed=5, spread=0.5, fly=(0.9, 1.7)):
        rng = np.random.default_rng(seed)
        O = np.asarray(origins, float)
        C = np.asarray(cells, float)
        M, K = len(C), len(O)
        # left-to-right order on both sides, so paths rarely cross
        oc = np.argsort(C[:, 0] + 0.15 * C[:, 1])
        oo = np.argsort(O[:, 0] + 0.15 * O[:, 1])
        src = np.empty(M, int)
        src[oc] = oo[np.minimum((np.arange(M) * K) // max(1, M), K - 1)]
        self.P0 = O[src] + rng.normal(0, 5 * scale, (M, 2))
        self.P1 = C
        mid = (self.P0 + self.P1) / 2
        v = self.P1 - self.P0
        n = np.stack([-v[:, 1], v[:, 0]], 1) / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-6)
        self.Pc = mid + n * rng.normal(0, 1, (M, 1)) * np.linalg.norm(v, axis=1, keepdims=True) * 0.28
        # squares leave their box a little after it lets go, the farthest boxes first
        dist = np.linalg.norm(v, axis=1)
        dn = dist / (dist.max() + 1e-6)
        self.start = t0 + spread * (0.25 * rng.random(M) + 0.75 * (1 - dn) * rng.random(M))
        self.dur = fly[0] + (fly[1] - fly[0]) * (0.35 * rng.random(M) + 0.65 * dn)
        self.arrive = self.start + self.dur
        self.scale, self.W, self.H = scale, W, H

    def draw(self, layer, t, color):
        d = ImageDraw.Draw(layer)
        col = tuple(int(c) for c in color)
        live = (t >= self.start) & (t < self.arrive + 0.08)
        if not live.any():
            return
        u = np.clip((t - self.start[live]) / self.dur[live], 0, 1)
        e = u * u * (3 - 2 * u)
        P0, P1, Pc = self.P0[live], self.P1[live], self.Pc[live]
        q = ((1 - e) ** 2)[:, None] * P0 + (2 * (1 - e) * e)[:, None] * Pc + (e ** 2)[:, None] * P1
        size = (13 - 9 * e) * self.scale
        a = np.clip(1.4 - e, 0.35, 1.0)
        w = max(1, int(round(1.5 * self.scale)))
        for (x, y), s, al in zip(q, size, a):
            r = s / 2
            d.rectangle([x - r, y - r, x + r, y + r], outline=col + (int(240 * al),), width=w)

    def fraction(self, t):
        return float((self.arrive <= t).mean())


def shadowed(layer, strength=0.55, blur=2.2):
    """A dark soft halo under an RGBA overlay so thin white lines read on bright footage."""
    a = layer.getchannel("A")
    sh = a.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(sh, np.float32) / 255.0 * strength
