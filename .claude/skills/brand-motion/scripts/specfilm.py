#!/usr/bin/env python3
"""specfilm: the spec-sheet launch grammar, on top of cutfilm.

Built for a launch concept that reads like a spec sheet: mono type that types and
deletes with a cursor, thin wireframe boxes that re-flow between layouts, counters in boxes, squares that
appear one by one on a crosshair, a leaderboard of segmented bars, a terminal, a measuring marker over a
calm wide shot, an info card that flips from ink to paper, and a letter field that draws a figure at both
ends. Everything is drawn in the brand's own paper and ink, in its own fonts; footage keeps its colour.

A timeline is a cutfilm timeline (shots, cuts, big, hud, finish, audio); these graphics types are added:

  type     mono type toward target strings: ops [[t, "text", cps?, dps?], ...] (deletes back to the common
           prefix, then types); cursor block | bar | under | none; "\\n" for lines; hi: tokens drawn inverse
           (paper box, ink type), dim: tokens drawn in ink-3; anchor ls | ms (centred on what is shown)
  frames   wireframe boxes: layouts [[t, [[x0, y0, x1, y1], ...]], ...] (same count in every layout; a box
           re-flows over `dur`), boxes draw in from their corner; texts: typed slots riding a box
  squares  squares that pop in one by one on a crosshair (appear [t, ...]), then `dup` [t, n, dur] into a
           row of identical squares, then `split` [t, dx]
  board    rows of mono labels and segmented bars; fill keyframes per row; `hi` keyframes the highlight row
  ibeam    a measuring marker: a line from the top of frame to a bar, a label, a line to a second bar, a label
  card     an info card that grows from its corner: typed condensed title, body copy, `flip` ink to paper,
           an optional tab label
  glyphs   a box of big changing glyphs from the brand alphabet (blocked words filtered)
  corners  four corner squares around a box (static or keyframed), optional crosshair lines and a label
  grid     a faint background grid
  lines    thin lines that grow from their first point
  field    (numpy) the brand letter field drawing a figure (a glyph or the logo) out of noise and dissolving
Any graphic may carry `cam`: [[t, scale, dx, dy], ...], a slow camera drift applied to that graphic alone,
and `flat`: true to skip the soft shadow the overlay casts on footage (use it on paper grounds).

Shots may use fx `ascii2`: the footage drawn as the letter field, ink letters on paper or paper on ink,
cell size in px, `invert_at` to swap ground and letters on a beat.

  python specfilm.py timeline.json --out film.mp4 [--preview] [--times a,b] [--from a --to b] [--lean]
"""
import json
import math
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cutfilm  # noqa: E402
import typeset  # noqa: E402
from cutfilm import Mosaic, ease_out, keyframes, lerp, lum, over, ss  # noqa: E402


def typed(ops, t, cps=40.0, dps=90.0):
    """What a typist shows at time t, following ops [[t, target, cps?, dps?], ...]. Returns (text, done)."""
    shown, done = "", True
    for i, op in enumerate(ops):
        tk, target = float(op[0]), op[1]
        if t < tk:
            break
        c = float(op[2]) if len(op) > 2 and op[2] is not None else cps
        dd = float(op[3]) if len(op) > 3 and op[3] is not None else dps
        t_end = float(ops[i + 1][0]) if i + 1 < len(ops) else float("inf")
        tt = min(t, t_end) - tk
        if c <= 0:
            shown, done = target, True
            continue
        p = 0
        while p < min(len(shown), len(target)) and shown[p] == target[p]:
            p += 1
        ndel = len(shown) - p
        tdel = ndel / dd if dd > 0 else 0.0
        if tt < tdel:
            shown = shown[:len(shown) - int(tt * dd)]
            done = False
        else:
            n = int((tt - tdel) * c + 1e-6)
            shown = target[:p + min(len(target) - p, n)]
            done = shown == target
    return shown, done


def kf_vec(kf, t, dur=None, linear=False):
    """[[t, [v...]], ...] -> list at t. With dur, each step eases over dur after its time; without, a smooth
    (or, with linear, a straight) blend between keyframes."""
    if not kf:
        return None
    if not isinstance(kf[0], list) or not isinstance(kf[0][1], list):
        return kf
    if t <= kf[0][0]:
        return list(kf[0][1])
    for (t0, v0), (t1, v1) in zip(kf, kf[1:]):
        if dur is not None:
            if t < t1:
                return list(v0) if not isinstance(v0[0], list) else [list(b) for b in v0]
            if t < t1 + dur:
                u = ease_out((t - t1) / dur)
                return _mix(v0, v1, u)
        elif t <= t1:
            u = min(1.0, max(0.0, (t - t0) / max(1e-6, t1 - t0))) if linear else ss(t0, t1, t)
            return _mix(v0, v1, u)
    return list(kf[-1][1]) if not isinstance(kf[-1][1][0], list) else [list(b) for b in kf[-1][1]]


def _mix(a, b, u):
    if isinstance(a[0], list):
        return [_mix(x, y, u) for x, y in zip(a, b)]
    return [lerp(x, y, u) for x, y in zip(a, b)]


def kf_num(v, t, default=0.0):
    if isinstance(v, list):
        return keyframes(v, t, default)
    return float(v) if v is not None else default


class SpecFilm(cutfilm.Film):
    def __init__(self, tl, W, H, fps, base):
        super().__init__(tl, W, H, fps, base)
        c = self.cfg["colors"]
        self.C = {k: tuple(int(x) for x in v) for k, v in c.items()}
        self.P = tuple(int(x) for x in self.PAPER)
        self.K = tuple(int(x) for x in self.INK)
        self.D = self.C.get("ink-3", (108, 108, 103))
        self.mosaics = {}
        self._flat = None
        lf = self.cfg.get("letter_field", {})
        self.alphabet = lf.get("alphabet", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

    # ---- helpers
    def col(self, c, default=None):
        if c is None:
            return default or self.P
        if isinstance(c, str):
            return {"paper": self.P, "ink": self.K, "dim": self.D}.get(c) or self.C.get(c) or self.P
        return tuple(int(x) for x in c)

    def pfont(self, role, px, axes=None):
        key = ("pf", role, round(px, 1), json.dumps(axes, sort_keys=True))
        if key not in self.cache:
            path = self.fonts[role] if role in self.fonts else role
            ax = axes if axes is not None else self.axes.get(role)
            self.cache[key] = typeset.pil_font(path, px, ax)
        return self.cache[key]

    def fade(self, g, t):
        fi, fo = float(g.get("fade", 0.06)), float(g.get("out", 0.06))
        a = ss(g["t0"] - 0.001, g["t0"] + fi, t) if fi > 0 else 1.0
        a *= (1 - ss(g["t1"] - fo, g["t1"], t)) if fo > 0 else 1.0
        return a * kf_num(g.get("alpha", 1.0), t, 1.0)

    def mosaic_for(self, cell):
        cell = max(6, int(cell))
        if cell not in self.mosaics:
            lf = self.cfg.get("letter_field", {})
            self.mosaics[cell] = Mosaic(self.fonts[lf.get("font", "sans")], cell, float(lf.get("cell_ratio", 1.18)),
                                        self.alphabet, lf.get("axes"), seed=11)
        return self.mosaics[cell]

    def lw(self, w=1.6):
        return max(1, int(round(w * self.scale)))

    # ---- text with tokens
    def draw_tokens(self, d, x, y, shown, full, f, col, a, hi=(), dim=(), hi_col=None, anchor="ls"):
        """One line of mono text at baseline (x, y). Tokens in hi are drawn inverse, tokens in dim in ink-3."""
        if not shown:
            return x
        kind = [0] * len(shown)
        for toks, v in ((dim, 1), (hi, 2)):
            for tok in toks:
                s = 0
                while True:
                    j = full.find(tok, s)
                    if j < 0:
                        break
                    for k in range(j, min(len(shown), j + len(tok))):
                        kind[k] = v
                    s = j + len(tok)
        asc, desc = f.getmetrics()
        i = 0
        cx = x
        A = int(255 * a)
        while i < len(shown):
            j = i
            while j < len(shown) and kind[j] == kind[i]:
                j += 1
            run = shown[i:j]
            wrun = d.textlength(run, font=f)
            if kind[i] == 2:
                pad = 0.12 * asc
                d.rectangle([cx - pad, y - asc * 0.86, cx + wrun + pad, y + desc * 0.55], fill=(hi_col or col) + (A,))
                d.text((cx, y), run, font=f, fill=self.K + (A,), anchor="ls")
            elif kind[i] == 1:
                d.text((cx, y), run, font=f, fill=self.D + (A,), anchor="ls")
            else:
                d.text((cx, y), run, font=f, fill=col + (A,), anchor="ls")
            cx += wrun
            i = j
        return cx

    def cursor(self, d, x, y, f, style, col, a, t, done, t0):
        if style in (None, "none"):
            return
        if done and (t - t0) % 0.53 >= 0.3:
            return
        asc, desc = f.getmetrics()
        cw = f.getlength("M")
        A = int(255 * a)
        if style == "block":
            d.rectangle([x + 0.08 * cw, y - asc * 0.82, x + 0.08 * cw + cw * 0.72, y + desc * 0.3], fill=col + (A,))
        elif style == "under":
            h = max(2, 0.07 * asc)
            d.rectangle([x + 0.06 * cw, y - h, x + 0.06 * cw + cw * 0.8, y], fill=col + (A,))
        else:
            w = max(2, int(round(0.06 * asc)))
            d.rectangle([x + 0.1 * cw, y - asc * 0.8, x + 0.1 * cw + w, y + desc * 0.25], fill=col + (A,))

    def text_block(self, d, g, t, x, y, a):
        """Typed mono text (possibly several lines) at (x, y) baseline; returns nothing."""
        f = self.pfont(g.get("role", "mono"), float(g.get("px", 30)) * self.scale, g.get("axes"))
        col = self.col(g.get("color"))
        ops = g.get("ops") or [[g.get("t0", 0.0), g.get("text", ""), 0]]
        shown, done = typed(ops, t, float(g.get("cps", 38)), float(g.get("dps", 90)))
        cur_target = ops[0][1]
        for op in ops:
            if t >= float(op[0]):
                cur_target = op[1]
        lines = shown.split("\n")
        full_lines = cur_target.split("\n")
        lh = float(g.get("lh", 1.28)) * f.size
        anchor = g.get("anchor", "ls")
        endx, endy = x, y
        for i, ln in enumerate(lines):
            full = full_lines[i] if i < len(full_lines) else ln
            yy = y + i * lh
            xx = x
            if anchor[0] == "m":
                # centre_cursor false centres the glyphs alone (a lone "0" should sit on the centre line)
                with_cursor = g.get("cursor") not in (None, "none") and g.get("centre_cursor", True)
                w = d.textlength(ln, font=f) + (f.getlength("M") if with_cursor else 0)
                xx = x - w / 2
            elif anchor[0] == "r":
                xx = x - d.textlength(ln, font=f)
            endx = self.draw_tokens(d, xx, yy, ln, full, f, col, a, g.get("hi", ()), g.get("dim", ()),
                                    self.col(g.get("hi_color")) if g.get("hi_color") else None)
            endy = yy
        self.cursor(d, endx, endy, f, g.get("cursor", "bar"), col, a, t, done, float(ops[-1][0]))

    # ---- graphics
    def g_type(self, layer, g, t):
        """Mono type that types and deletes toward target strings. `label` puts a small line above it and `plate` a
        dark plate behind both, sized to the widest target string so it holds still while the text changes (a
        pattern readout, matching cutfilm's scaleclock)."""
        d = ImageDraw.Draw(layer)
        a = self.fade(g, t)
        x = kf_num(g.get("x", 0.5), t) * self.W
        y = kf_num(g.get("y", 0.5), t) * self.H
        if (g.get("plate") or g.get("label")) and a > 0.01:
            px = float(g.get("px", 30)) * self.scale
            f = self.pfont(g.get("role", "mono"), px, g.get("axes"))
            fl = self.pfont(g.get("role", "mono"), float(g.get("label_px", 15)) * self.scale, g.get("axes"))
            wmax = max(d.textlength(str(op[1]), font=f) for op in (g.get("ops") or [[0, g.get("text", "")]]))
            p = 9 * self.scale
            yl = y - px * 1.05
            anc = g.get("anchor", "ls")
            x0 = x - p if anc[0] == "l" else (x - wmax - p if anc[0] == "r" else x - wmax / 2 - p)
            col = self.col(g.get("color"))
            if g.get("plate"):
                d.rectangle([x0, yl - float(g.get("label_px", 15)) * self.scale - p, x0 + wmax + 2 * p, y + p * 0.9],
                            fill=tuple(int(c) for c in self.INK) + (int(255 * float(g["plate"]) * a),))
            if g.get("label"):
                d.text((x, yl), g["label"], font=fl, fill=col + (int(170 * a),), anchor=anc)
        self.text_block(d, g, t, x, y, a)

    def g_frames(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = self.fade(g, t)
        boxes = kf_vec(g["layouts"], t, float(g.get("dur", 0.3)))
        col = self.col(g.get("color"))
        A = int(255 * a * float(g.get("line_alpha", 0.92)))
        w = self.lw(float(g.get("width", 1.6)))
        di = float(g.get("draw_in", 0.28))
        u = ease_out((t - g["t0"]) / di) if di > 0 else 1.0
        for bi, b in enumerate(boxes):
            x0, y0, x1, y1 = b[0] * W, b[1] * H, b[2] * W, b[3] * H
            if abs(x1 - x0) < 1 and abs(y1 - y0) < 1:
                continue
            ub = min(1.0, max(0.0, u * 1.25 - bi * 0.06))
            if ub <= 0:
                continue
            k1, k2 = min(1.0, ub * 2), max(0.0, ub * 2 - 1)
            d.line([(x0, y0), (x0 + (x1 - x0) * k1, y0)], fill=col + (A,), width=w)
            d.line([(x0, y0), (x0, y0 + (y1 - y0) * k1)], fill=col + (A,), width=w)
            if k2 > 0:
                d.line([(x1, y0), (x1, y0 + (y1 - y0) * k2)], fill=col + (A,), width=w)
                d.line([(x0, y1), (x0 + (x1 - x0) * k2, y1)], fill=col + (A,), width=w)
        for tx in g.get("texts", []):
            if not (tx.get("t0", g["t0"]) <= t < tx.get("t1", g["t1"])):
                continue
            b = boxes[tx["box"]]
            x = (b[0] + (b[2] - b[0]) * float(tx.get("ax", 0.06))) * W
            y = (b[1] + (b[3] - b[1]) * float(tx.get("ay", 0.8))) * H
            ta = a * ss(tx.get("t0", g["t0"]) - 0.001, tx.get("t0", g["t0"]) + 0.05, t)
            self.text_block(d, tx, t, x, y, ta)

    def g_squares(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = self.fade(g, t)
        A = int(255 * a)
        col = self.col(g.get("color"))
        size = float(g.get("size", 0.08)) * H
        gap = float(g.get("gap", 0.28)) * size
        cx, cy = float(g.get("x", 0.5)) * W, float(g.get("y", 0.5)) * H
        times = g["appear"]
        n = sum(1 for tt in times if t >= tt)
        if g.get("lines", True):
            la = int(110 * a)
            d.line([(0, cy), (W, cy)], fill=col + (la,), width=1)
            d.line([(cx, cy - 0.22 * H), (cx, cy + 0.22 * H)], fill=col + (la,), width=1)
        step = size + gap
        n_eff = sum(ease_out((t - tt) / 0.14) for tt in times if t >= tt)
        pos = [cx + (i - (n_eff - 1) / 2) * step for i in range(n)]
        dup = g.get("dup")
        items = []  # (x, alpha, pop)
        if dup and t >= dup[0]:
            N = int(dup[1])
            st = float(dup[2]) if len(dup) > 2 else 0.035
            final = [cx + (j - (N - 1) / 2) * step for j in range(N)]
            mid0 = (N - n) // 2
            for j in range(N):
                if mid0 <= j < mid0 + n:
                    items.append((final[j], 1.0, 1.0))
                else:
                    dist = (mid0 - j) if j < mid0 else (j - (mid0 + n - 1))
                    tj = float(dup[0]) + (dist - 1) * st
                    pop = ease_out((t - tj) / 0.08) if t >= tj else 0.0
                    items.append((final[j], pop, pop))
        else:
            for i, x in enumerate(pos):
                items.append((x, 1.0, ease_out((t - times[i]) / 0.09)))
        sp = g.get("split")
        if sp and t >= sp[0]:
            u = ease_out((t - sp[0]) / 0.3)
            items = [(x + (float(sp[1]) * W * u) * (1 if x > cx else -1), al, pp) for x, al, pp in items]
        for x, al, pp in items:
            if al <= 0.001:
                continue
            s2 = size * (0.55 + 0.45 * pp)
            d.rectangle([x - s2 / 2, cy - s2 / 2, x + s2 / 2, cy + s2 / 2], fill=col + (int(A * al),))
        if n < len(times) and (not dup or t < dup[0]) and n > 0:
            x = cx + (n - (n_eff - 1) / 2) * step
            d.rectangle([x - size / 2, cy - size / 2, x + size / 2, cy + size / 2], outline=col + (int(150 * a),), width=self.lw(1.4))

    def g_board(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a_all = self.fade(g, t)
        x0, y0, w, rh = g["x"] * W, g["y"] * H, g["w"] * W, g["row_h"] * H
        f = self.pfont(g.get("role", "mono"), float(g.get("px", 24)) * self.scale)
        fs = self.pfont("mono", float(g.get("px_head", 16)) * self.scale)
        cols = g["cols"]
        segs = int(g.get("segs", 30))
        bw = float(g.get("bar_w", 0.34)) * w
        seg_w = bw / segs
        if g.get("head"):
            ha = int(200 * a_all * ss(g["t0"], g["t0"] + 0.15, t))
            yh = y0 - rh * 0.95
            for key, cx in (("n", cols[0]), ("label", cols[1]), ("mid", cols[2]), ("bar", cols[3])):
                txt = g["head"].get(key)
                if txt:
                    d.text((x0 + cx * w, yh), txt, font=fs, fill=self.D + (ha,), anchor="ls")
            d.line([(x0, yh + rh * 0.3), (x0 + w, yh + rh * 0.3)], fill=self.D + (int(ha * 0.6),), width=1)
        asc = f.getmetrics()[0]
        for i, r in enumerate(g["rows"]):
            ti = g["t0"] + i * float(g.get("stagger", 0.05))
            ra = a_all * ss(ti, ti + 0.12, t)
            if ra <= 0:
                continue
            y = y0 + i * rh
            dx = (1 - ease_out((t - ti) / 0.22)) * 26 * self.scale
            A = int(255 * ra)
            d.text((x0 + cols[0] * w + dx, y), r.get("n", "%02d" % (i + 1)), font=f, fill=self.D + (A,), anchor="ls")
            d.text((x0 + cols[1] * w + dx, y), r["label"], font=f, fill=self.P + (A,), anchor="ls")
            if r.get("mid"):
                d.text((x0 + cols[2] * w + dx, y), r["mid"], font=f, fill=self.D + (A,), anchor="ls")
            v = keyframes(r.get("fill", [[0, 0.0]]), t, 0.0)
            nfill = int(round(v * segs))
            bx = x0 + cols[3] * w + dx
            sh = asc * 0.62
            if r.get("bar") is False:
                # a row with no bar at all: a note types on where the bar would be ("NOT MEASURED")
                note = r.get("note", "")
                tn = float(r.get("note_at", ti))
                n_ch = int(len(note) * ss(tn, tn + len(note) / float(r.get("note_cps", 26)), t) + 0.999)
                if n_ch > 0:
                    d.text((bx, y), note[:n_ch], font=f, fill=self.P + (A,), anchor="ls")
                continue
            for k in range(segs):
                sx0 = bx + k * seg_w
                on = k < nfill
                d.rectangle([sx0, y - sh, sx0 + seg_w * 0.7, y], fill=(self.P if on else self.D) + (int(A * (1.0 if on else 0.32)),))
            if g.get("pct", True):
                d.text((bx + bw + 16 * self.scale, y), f"{int(round(v * 100)):d}%", font=f,
                       fill=(self.P if v > 0.005 else self.D) + (A,), anchor="ls")
        hi = g.get("hi")
        if hi:
            hv = keyframes(hi, t, -1.0)
            ha = kf_num(g.get("hi_alpha", 1.0), t, 1.0) * a_all
            if hv >= 0 and ha > 0.01:
                yy = y0 + hv * rh
                p = 12 * self.scale
                d.rectangle([x0 - p, yy - rh * 0.68, x0 + w + p * 5, yy + rh * 0.3], outline=self.P + (int(235 * ha),), width=self.lw(1.5))

    def g_ibeam(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = self.fade(g, t)
        A = int(255 * a)
        col = self.col(g.get("color"))
        x = kf_num(g["x"], t) * W
        top, bot = kf_num(g["top"], t) * H, kf_num(g["bot"], t) * H
        bar = float(g.get("bar", 0.2)) * W
        w = self.lw(1.6)
        f = self.pfont("mono", float(g.get("px", 24)) * self.scale)
        asc = f.getmetrics()[0]
        u = ease_out((t - g["t0"]) / 0.35)
        d.line([(x, 0), (x, top * u)], fill=col + (A,), width=w)
        d.line([(x - bar / 2 * u, top), (x + bar / 2 * u, top)], fill=col + (A,), width=w)
        yl = top + asc * 1.55
        lt = g.get("label_top")
        if lt:
            s, _ = typed(lt["ops"], t, float(lt.get("cps", 30)))
            d.text((x, yl), s, font=f, fill=col + (A,), anchor="ms")
        ym = yl + asc * 0.9
        if bot > ym:
            d.line([(x, ym), (x, ym + (bot - ym) * u)], fill=col + (A,), width=w)
            d.line([(x - bar / 2 * u, bot), (x + bar / 2 * u, bot)], fill=col + (A,), width=w)
        lb = g.get("label_bot")
        if lb:
            s, _ = typed(lb["ops"], t, float(lb.get("cps", 30)))
            d.text((x, bot + asc * 1.55), s, font=f, fill=col + (A,), anchor="ms")

    def g_card(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = self.fade(g, t)
        A = int(255 * a)
        x0, y0, x1, y1 = [v * (W if i % 2 == 0 else H) for i, v in enumerate(kf_vec(g["box"], t) if isinstance(g["box"][0], list) else g["box"])]
        u = ease_out((t - g["t0"]) / float(g.get("grow", 0.22)))
        cx1, cy0 = x0 + (x1 - x0) * u, y1 - (y1 - y0) * u
        flipped = g.get("flip") is not None and t >= float(g["flip"])
        fill = self.P if flipped else self.K
        ink = self.K if flipped else self.P
        d.rectangle([x0, cy0, cx1, y1], fill=fill + (A,))
        if not flipped:
            d.rectangle([x0, cy0, cx1, y1], outline=self.P + (int(A * 0.5),), width=1)
        if u < 0.98:
            return
        pad = float(g.get("pad", 0.05)) * (x1 - x0)
        tf = self.pfont(g.get("title_role", "sans"), float(g.get("title_px", 60)) * self.scale, g.get("title_axes"))
        title, done = typed(g["title_ops"], t, float(g.get("cps", 26)))
        asc = tf.getmetrics()[0]
        lh = asc * float(g.get("title_lh", 1.02))
        yy = y0 + pad + asc * 0.95
        lines = title.split("\n")
        for i, ln in enumerate(lines):
            d.text((x0 + pad, yy + i * lh), ln, font=tf, fill=ink + (A,), anchor="ls")
        if g.get("cursor", True) and not (done and (t % 0.53) >= 0.3):
            last = lines[-1]
            cx = x0 + pad + d.textlength(last, font=tf) + asc * 0.06
            cy = yy + (len(lines) - 1) * lh
            d.rectangle([cx, cy - asc * 0.8, cx + max(2, asc * 0.05), cy + asc * 0.08], fill=ink + (A,))
        bt = g.get("body_t")
        if bt is not None and t >= float(bt):
            ba = int(A * ss(float(bt), float(bt) + 0.2, t))
            bf = self.pfont(g.get("body_role", "sans"), float(g.get("body_px", 17)) * self.scale, g.get("body_axes", {"wght": 430}))
            words = g["body"].split()
            maxw = (x1 - x0) - 2 * pad
            out, cur = [], ""
            for wd in words:
                trial = (cur + " " + wd).strip()
                if d.textlength(trial, font=bf) > maxw and cur:
                    out.append(cur)
                    cur = wd
                else:
                    cur = trial
            if cur:
                out.append(cur)
            bl = bf.getmetrics()[0] * 1.35
            by = y1 - pad - (len(out) - 1) * bl
            bcol = self.C.get("ink-2", (74, 74, 70)) if flipped else self.C.get("paper-3", (228, 228, 224))
            for i, ln in enumerate(out):
                d.text((x0 + pad, by + i * bl), ln, font=bf, fill=bcol + (ba,), anchor="ls")
        tab = g.get("tab")
        if tab and t >= float(tab.get("t", g["t0"])):
            tfnt = self.pfont("mono", float(tab.get("px", 15)) * self.scale)
            tw = d.textlength(tab["text"], font=tfnt)
            p = 8 * self.scale
            tx1, ty0 = x1, y0 - tfnt.getmetrics()[0] - 3 * p
            d.rectangle([tx1 - tw - 2 * p, ty0, tx1, y0 - p], fill=self.P + (A,), outline=self.K + (A,))
            d.text((tx1 - tw - p, y0 - 2.2 * p), tab["text"], font=tfnt, fill=self.K + (A,), anchor="ls")

    def g_glyphs(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = self.fade(g, t)
        A = int(255 * a)
        bb = kf_vec(g["box"], t, linear=True) if isinstance(g["box"][0], list) else g["box"]
        x0, y0, x1, y1 = [v * (W if i % 2 == 0 else H) for i, v in enumerate(bb)]
        inv = g.get("invert_at") is not None and t >= float(g["invert_at"])
        ground = g.get("ground", "paper")
        if inv:
            ground = "ink" if ground == "paper" else "paper"
        bg, fg = (self.P, self.K) if ground == "paper" else (self.K, self.P)
        d.rectangle([x0, y0, x1, y1], fill=bg + (A,))
        f = self.pfont(g.get("role", "mono"), float(g.get("px", 40)) * self.scale)
        cw = f.getlength("M")
        asc = f.getmetrics()[0]
        rows = max(1, int((y1 - y0 - 0.3 * asc) // (asc * 1.02)))
        ncol = max(1, int((x1 - x0 - 0.4 * cw) // cw))
        rng = np.random.default_rng(int(t * float(g.get("rate", 12))) + int(g.get("seed", 3)))
        chars = g.get("chars", self.alphabet)
        rx = cutfilm._blocked()
        for r in range(rows):
            row = "".join(chars[k] for k in rng.integers(0, len(chars), ncol))
            if rng.random() < 0.3:
                k = int(rng.integers(0, ncol))
                row = row[:k] + " " * min(ncol - k, int(rng.integers(1, 4))) + row[k + 3:]
            for m in rx.finditer(row):
                row = row[:m.start()] + "7" * (m.end() - m.start()) + row[m.end():]
            d.text((x0 + 0.2 * cw, y0 + 0.2 * asc + (r + 1) * asc * 1.02), row[:ncol], font=f, fill=fg + (A,), anchor="ls")

    def g_corners(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = self.fade(g, t)
        A = int(255 * a)
        col = self.col(g.get("color"))
        b = g["box"]
        b = kf_vec(b, t, linear=True) if isinstance(b[0], list) else b
        pop = ease_out((t - g["t0"]) / 0.16)
        cxf, cyf = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        sc = lerp(1.35, 1.0, pop)
        x0, y0 = (cxf + (b[0] - cxf) * sc) * W, (cyf + (b[1] - cyf) * sc) * H
        x1, y1 = (cxf + (b[2] - cxf) * sc) * W, (cyf + (b[3] - cyf) * sc) * H
        r = float(g.get("size", 5)) * self.scale
        if g.get("lines"):
            la = int(float(g.get("line_alpha", 0.35)) * 255 * a)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            d.line([(0, cy), (W, cy)], fill=col + (la,), width=1)
            d.line([(cx, 0), (cx, H)], fill=col + (la,), width=1)
        if g.get("outline"):
            d.rectangle([x0, y0, x1, y1], outline=col + (int(A * 0.85),), width=self.lw(float(g.get("width", 1.3))))
        for (x, y) in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            d.rectangle([x - r, y - r, x + r, y + r], fill=col + (A,))
        for lk in g.get("links", []):
            px, py = lk[0] * W, lk[1] * H
            d.line([((x0 + x1) / 2, (y0 + y1) / 2), (px, py)], fill=col + (int(A * 0.9),), width=self.lw(float(g.get("width", 1.3))))
            d.rectangle([px - r * 0.7, py - r * 0.7, px + r * 0.7, py + r * 0.7], fill=col + (A,))
        lab = g.get("label")
        if lab:
            f = self.pfont("mono", float(lab.get("px", 20)) * self.scale)
            s, _ = typed(lab.get("ops") or [[g["t0"], lab["text"], 0]], t, float(lab.get("cps", 30)))
            pos = lab.get("pos", "below")
            if pos == "center":
                d.text(((x0 + x1) / 2, (y0 + y1) / 2 + f.getmetrics()[0] * 0.36), s, font=f, fill=col + (A,), anchor="ms")
            else:
                d.text((x0, y1 + f.getmetrics()[0] * 1.5), s, font=f, fill=col + (A,), anchor="ls")

    def dashed(self, d, p0, p1, fill, dash=7.0, gap=5.0):
        (x0, y0), (x1, y1) = p0, p1
        L = math.hypot(x1 - x0, y1 - y0)
        if L < 1:
            return
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        s = 0.0
        dash, gap = dash * self.scale, gap * self.scale
        while s < L:
            e = min(L, s + dash)
            d.line([(x0 + ux * s, y0 + uy * s), (x0 + ux * e, y0 + uy * e)], fill=fill, width=1)
            s = e + gap

    def g_logo(self, layer, g, t):
        """The wordmark file itself, tinted to paper (or ink), at x, y; w is its width as a fraction of W."""
        W, H = self.W, self.H
        a = self.fade(g, t)
        lw = int(W * float(g.get("w", 0.12)))
        key = ("logo_rgba", lw, json.dumps(self.col(g.get("color"))))
        if key not in self.cache:
            lg = self.logo.resize((lw, max(1, int(self.logo.height * lw / self.logo.width))), Image.LANCZOS)
            al = lg.getchannel("A")
            im = Image.new("RGBA", lg.size, self.col(g.get("color")) + (0,))
            im.putalpha(al)
            self.cache[key] = im
        im = self.cache[key]
        if a < 0.999:
            im = im.copy()
            im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
        x, y = float(g.get("x", 0.5)) * W, float(g.get("y", 0.5)) * H
        anc = g.get("anchor", "mm")
        x0 = x if anc[0] == "l" else x - im.width / 2 if anc[0] == "m" else x - im.width
        y0 = y - im.height / 2 if anc[1] == "m" else y
        layer.alpha_composite(im, (int(round(x0)), int(round(y0))))

    def g_grid(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = self.fade(g, t) * float(g.get("strength", 0.09))
        col = self.col(g.get("color"))
        st = float(g.get("step", 0.05)) * W
        A = int(255 * a)
        x = (W / 2) % st
        while x < W:
            d.line([(x, 0), (x, H)], fill=col + (A,), width=1)
            x += st
        y = (H / 2) % st
        while y < H:
            d.line([(0, y), (W, y)], fill=col + (A,), width=1)
            y += st

    def g_lines(self, layer, g, t):
        d = ImageDraw.Draw(layer)
        W, H = self.W, self.H
        a = self.fade(g, t)
        col = self.col(g.get("color"))
        A = int(255 * a * float(g.get("strength", 0.9)))
        u = ease_out((t - g["t0"]) / float(g.get("grow", 0.25)))
        for ln in g["lines"]:
            x0, y0, x1, y1 = ln[0] * W, ln[1] * H, ln[2] * W, ln[3] * H
            d.line([(x0, y0), (x0 + (x1 - x0) * u, y0 + (y1 - y0) * u)], fill=col + (A,), width=self.lw(float(g.get("width", 1.4))))

    # ---- numpy graphics
    def fig_mask(self, g):
        fig = g["fig"]
        key = ("fig", json.dumps(fig, sort_keys=True), g.get("x", 0.5), g.get("y", 0.5))
        if key in self.cache:
            return self.cache[key]
        W, H = self.W, self.H
        m = np.zeros((H, W), np.float32)
        if fig.get("logo"):
            lw = int(W * float(fig.get("w", 0.6)))
            lg = self.logo.resize((lw, int(self.logo.height * lw / self.logo.width)), Image.LANCZOS)
            al = np.asarray(lg.getchannel("A"), np.float32) / 255.0
            x0 = int(float(g.get("x", 0.5)) * W - lw / 2)
            y0 = int(float(g.get("y", 0.5)) * H - al.shape[0] / 2)
            m[y0:y0 + al.shape[0], x0:x0 + al.shape[1]] = al
        else:
            im = Image.new("L", (W, H), 0)
            dd = ImageDraw.Draw(im)
            f = self.pfont(fig.get("role", "mono"), float(fig.get("px", 700)) * self.scale, fig.get("axes"))
            dd.text((float(g.get("x", 0.5)) * W, float(g.get("y", 0.5)) * H), fig["text"], font=f, fill=255, anchor="mm")
            m = np.asarray(im, np.float32) / 255.0
            ys, xs = np.nonzero(m > 0.05)
            if fig.get("ink_centre", True) and len(ys):
                # anchor "mm" centres the font's line box, not the glyph; a figure has no descender, so a big "0"
                # sat 10% of the frame high. Move the ink's own bounding box onto (x, y).
                dx = int(round(float(g.get("x", 0.5)) * W - (xs.min() + xs.max() + 1) / 2))
                dy = int(round(float(g.get("y", 0.5)) * H - (ys.min() + ys.max() + 1) / 2))
                moved = np.zeros_like(m)
                sy0, sy1 = max(0, -dy), min(H, H - dy)
                sx0, sx1 = max(0, -dx), min(W, W - dx)
                moved[sy0 + dy:sy1 + dy, sx0 + dx:sx1 + dx] = m[sy0:sy1, sx0:sx1]
                m = moved
            if fig.get("blur"):
                m = cv2.GaussianBlur(m, (0, 0), float(fig["blur"]) * self.scale)
        self.cache[key] = m
        return m

    def n_field(self, img, g, t):
        W, H = self.W, self.H
        mz = self.mosaic_for(round(float(g.get("cell", 16)) * self.scale))
        m = self.fig_mask(g)
        ra, rb = g.get("resolve", [g["t0"] + 0.1, g["t0"] + 0.7])
        u = ss(ra, rb, t)
        da, db = g.get("dissolve", [g["t1"] + 1, g["t1"] + 2])
        v = ss(da, db, t)
        a_in = ss(g["t0"], g["t0"] + float(g.get("fade", 0.25)), t)
        rng = np.random.default_rng(int(t * 997) + 5)
        k = max(8, int(round(float(g.get("grain", 26)) * self.scale)))
        noise = cv2.resize(rng.random((H // k + 1, W // k + 1)).astype(np.float32), (W, H), interpolation=cv2.INTER_NEAREST)
        amp = float(g.get("noise", 0.5))
        vig = self.cache.get(("fvig",))
        if vig is None:
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            r = np.sqrt(((xx - W / 2) / (W * 0.5)) ** 2 + ((yy - H / 2) / (H * 0.5)) ** 2)
            vig = np.clip(1.15 - r * 0.8, 0, 1)
            self.cache[("fvig",)] = vig
        fig = m * u * (1 - v)
        nz = noise * amp * vig * a_in * (1 - u * float(g.get("clear", 0.85))) * (1 - ss(db - 0.2, db, t) if v > 0 else 1)
        nz += noise * amp * vig * v * (1 - v) * 1.6
        src = np.clip(fig * float(g.get("gain", 0.95)) + nz, 0, 1)
        hole = g.get("hole")
        if hole:
            # a soft clearing where words sit over the field: {box: [x0, y0, x1, y1], feather (px), strength}
            hk = ("fhole", json.dumps(hole, sort_keys=True))
            if hk not in self.cache:
                hm = np.zeros((H, W), np.float32)
                x0, y0, x1, y1 = hole["box"]
                hm[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)] = 1.0
                hm = cv2.GaussianBlur(hm, (0, 0), float(hole.get("feather", 60)) * self.scale)
                self.cache[hk] = 1 - np.clip(hm, 0, 1) * float(hole.get("strength", 0.92))
            src = src * self.cache[hk]
        cov = mz.render(src, W, H, t, swap=0.12 + 0.5 * (1 - u) + 0.5 * v)
        col = np.array(self.col(g.get("color")), np.float32)
        over(img, col, np.clip(cov * float(g.get("alpha", 0.95)), 0, 1))

    # ---- fx: the footage drawn as the letter field
    def apply_fx(self, fx, img, s, t):
        if fx.get("type") == "blur":
            # soften a region (a legible phone screen, a label): box [x0, y0, x1, y1] in fractions, feathered edges
            W, H = self.W, self.H
            b = fx.get("box", [0, 0, 1, 1])
            soft = cv2.GaussianBlur(img, (0, 0), float(fx.get("sigma", 6)) * self.scale)
            m = np.zeros((H, W), np.float32)
            m[int(b[1] * H):int(b[3] * H), int(b[0] * W):int(b[2] * W)] = 1.0
            m = cv2.GaussianBlur(m, (0, 0), float(fx.get("feather", 30)) * self.scale)[..., None]
            return img * (1 - m) + soft * m
        if fx.get("type") != "ascii2":
            return super().apply_fx(fx, img, s, t)
        on = fx.get("on")
        if on and not (on[0] <= t < on[1]):
            return img
        W, H = self.W, self.H
        L = lum(img) / 255.0
        if fx.get("blur"):
            L = cv2.GaussianBlur(L, (0, 0), float(fx["blur"]) * self.scale)
        ground = fx.get("ground", "ink")
        if fx.get("invert_at") is not None and t >= float(fx["invert_at"]):
            ground = "paper" if ground == "ink" else "ink"
        src = (1 - L) if ground == "paper" else L
        src = np.clip((src - float(fx.get("floor", 0.08))) * float(fx.get("gain", 1.3)), 0, 1)
        mz = self.mosaic_for(round(float(fx.get("cell", 18)) * self.scale))
        cov = mz.render(src, W, H, t, swap=float(fx.get("swap", 0.25)))
        bg, fg = (self.PAPER, self.INK) if ground == "paper" else (self.INK, self.PAPER)
        out = np.empty_like(img)
        out[:] = bg
        out = over(out, fg, np.clip(cov * float(fx.get("strength", 1.0)), 0, 1))
        mix = kf_num(fx.get("mix", 1.0), t, 1.0)
        return img * (1 - mix) + out * mix

    # ---- composition
    def cam_warp(self, im, cam, t):
        if not cam:
            return im
        s = keyframes([[c[0], c[1]] for c in cam], t, 1.0)
        dx = keyframes([[c[0], c[2]] for c in cam], t, 0.0) * self.W
        dy = keyframes([[c[0], c[3]] for c in cam], t, 0.0) * self.H
        if abs(s - 1) < 1e-4 and abs(dx) < 0.05 and abs(dy) < 0.05:
            return im
        cx, cy = self.W / 2, self.H / 2
        data = (1 / s, 0, cx - (cx + dx) / s, 0, 1 / s, cy - (cy + dy) / s)
        return im.transform((self.W, self.H), Image.AFFINE, data, resample=Image.BICUBIC)

    NUMPY = ("field", "ascii_logo", "hud")

    def overlay_layer(self, t, s):
        W, H = self.W, self.H
        g_all = self.tl.get("graphics", [])
        self.tl["graphics"] = []
        try:
            base = super().overlay_layer(t, s)
        finally:
            self.tl["graphics"] = g_all
        layer = base if base is not None else Image.new("RGBA", (W, H), (0, 0, 0, 0))
        drew = base is not None
        flat = None
        for g in g_all:
            if g["type"] in self.NUMPY or not (g["t0"] - 0.001 <= t < g["t1"]):
                continue
            fn = getattr(self, "g_" + g["type"])
            if g.get("cam") or g.get("flat"):
                tmp = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                fn(tmp, g, t)
                tmp = self.cam_warp(tmp, g.get("cam"), t)
                if g.get("flat"):
                    flat = tmp if flat is None else Image.alpha_composite(flat, tmp)
                else:
                    layer.alpha_composite(tmp)
                    drew = True
            else:
                fn(layer, g, t)
                drew = True
        self._flat = flat
        return layer if drew else None

    def frame(self, t):
        img, s = self.picture(t)
        img = np.clip(img, 0, 255)
        for g in self.tl.get("graphics", []):
            if g["type"] == "field" and g["t0"] <= t < g["t1"] and not g.get("top"):
                self.n_field(img, g, t)
        lay = self.overlay_layer(t, s)
        if lay is not None:
            a = np.asarray(lay, np.float32)
            al = a[..., 3] / 255.0
            sh = cv2.GaussianBlur(al, (0, 0), 2.0 * self.scale) * float(self.tl.get("overlay_shadow", 0.55))
            img *= (1 - sh[..., None])
            img = img * (1 - al[..., None]) + a[..., :3] * al[..., None]
        if self._flat is not None:
            a = np.asarray(self._flat, np.float32)
            al = a[..., 3] / 255.0
            img = img * (1 - al[..., None]) + a[..., :3] * al[..., None]
        self.draw_type(img, t)
        for g in self.tl.get("graphics", []):
            if g["type"] == "ascii_logo" and g["t0"] <= t < g["t1"]:
                self.ascii_logo(img, g, t)
            if g["type"] == "field" and g.get("top") and g["t0"] <= t < g["t1"]:
                self.n_field(img, g, t)
        band = min(self.H, int(round(130 * self.scale)))
        hud = Image.new("RGBA", (self.W, band), (0, 0, 0, 0))
        self.draw_hud(hud, t)
        a = np.asarray(hud, np.float32)
        al = a[..., 3] / 255.0
        top = img[:band]
        h = self.tl.get("hud") or {}
        if keyframes(h.get("paper", [[0, 0]]), t, 0.0) < 0.5:
            sh = cv2.GaussianBlur(al, (0, 0), 2.4 * self.scale) * 0.6
            top *= (1 - sh[..., None])
        img[:band] = top * (1 - al[..., None]) + a[..., :3] * al[..., None]
        return self.finish(img, t)


if __name__ == "__main__":
    cutfilm.Film = SpecFilm
    cutfilm.main()
