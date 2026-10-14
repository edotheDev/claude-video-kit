#!/usr/bin/env python3
"""Dot flow for the motion kit: thousands of round dots moving like dust in light.

The dots drift on a smooth curl-noise flow, at different depths (near dots are bigger, brighter and
faster). A film gives them a list of gathers. In each gather a fresh scatter of dots flows toward a
target (points sampled inside a shape, loosely), and the next gather blows the last one off the frame
on its hit and starts again from nothing. At the turn nothing is blown away: the dots are re-aimed at
exact cell centres, and the time each one lands is recorded so a letter field can light that cell up
as it arrives. Everything is simulated once, deterministically, at a fixed step, and stored per frame,
so a still and a master draw identical dots.

Used by fieldfilm.py through a board's "dots" spec.
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def smooth(a, b, x):
    with np.errstate(invalid="ignore"):
        t = np.clip((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3 - 2 * t)


class Curl:
    """A divergence-free flow: the curl of a sum of travelling sine waves. Smooth, cheap, analytic."""

    def __init__(self, rng, W, H, octaves=6, scale=1.0):
        s = max(W, H)
        self.k = []
        for i in range(octaves):
            ang = rng.uniform(0, 2 * math.pi)
            freq = (1.4 + 1.9 * i) * 2 * math.pi / s / scale
            self.k.append((math.cos(ang) * freq, math.sin(ang) * freq, rng.uniform(0.2, 0.7) * (1 if i % 2 else -1),
                           rng.uniform(0, 2 * math.pi), 1.0 / (1 + 0.8 * i)))

    def __call__(self, p, t):
        vx = np.zeros(len(p))
        vy = np.zeros(len(p))
        for kx, ky, w, ph, amp in self.k:
            c = np.cos(kx * p[:, 0] + ky * p[:, 1] + w * t + ph) * amp
            vx += c * ky   # d(psi)/dy
            vy -= c * kx   # -d(psi)/dx
        n = np.sqrt(sum((kx * kx + ky * ky) for kx, ky, *_ in self.k) / len(self.k))
        return np.stack([vx, vy], 1) / n


class DotFlow:
    def __init__(self, spec, W, H, fps, soft_targets, cells_xy, scale):
        self.spec, self.W, self.H, self.fps, self.scale = spec, W, H, fps, scale
        rng = np.random.default_rng(int(spec.get("seed", 5)))
        self.N = N = int(spec.get("count", 3000))
        self.Na = Na = int(spec.get("ambient", 700))
        self.gathers = [float(g) for g in spec["gathers"]]      # start times; the last one runs into the turn
        self.dirs = [np.array(d, float) for d in spec.get("sweep_dirs", [[1, 0]] * len(self.gathers))]
        self.turn = float(spec["turn"])
        self.until = float(spec["until"])
        self.jitter = float(spec.get("jitter", 22)) * scale
        self.flow_amp = float(spec.get("flow", 150)) * scale
        self.rate = float(spec.get("rate", 0.6))
        self.turn_rate = float(spec.get("turn_rate", 1.1))
        self.land = float(spec.get("land_by", 2.6))               # every assigned dot lands within this after the turn
        self.curl = Curl(rng, W, H, 6, float(spec.get("flow_scale", 1.0)))
        self.soft = np.asarray(soft_targets, float)
        self.cells = np.asarray(cells_xy, float)
        self.depth = [rng.random(N) ** 1.3, rng.random(N) ** 1.3]
        self.depth_a = rng.random(Na) ** 1.6
        self.rng = rng
        self._simulate()

    # ------------------------------------------------------------------ simulation
    def _scatter(self, n):
        m = 0.04
        return np.stack([self.rng.uniform(-m, 1 + m, n) * self.W, self.rng.uniform(-m, 1 + m, n) * self.H], 1)

    def _targets(self, n):
        idx = self.rng.integers(0, len(self.soft), n)
        return self.soft[idx] + self.rng.normal(0, self.jitter, (n, 2))

    def _simulate(self):
        W, H, N, Na = self.W, self.H, self.N, self.Na
        dt = 1.0 / 120.0
        steps = int(math.ceil(self.until / dt)) + 1
        pos = [self._scatter(N), self._scatter(N)]
        vel = [np.zeros((N, 2)), np.zeros((N, 2))]
        tgt = [self._targets(N), self._targets(N)]
        spawn = [0.0, -1e9]
        blown = [-1e9, -1e9]
        live = [True, False]
        posa = self._scatter(Na)
        vela = np.zeros((Na, 2))
        # the turn: which dot goes to which cell, and when it lands
        self.assign = None
        self.arrive = np.full(len(self.cells), np.inf)
        land_t = np.full(N, np.inf)
        fade_t = np.full(N, np.inf)          # unassigned dots at the turn fade from this time
        gi = 0
        frames = {}
        next_frame = 0
        nf = int(round(self.until * self.fps))
        speed_rng = np.random.default_rng(int(self.spec.get("seed", 5)) + 99)
        turn_done = False
        a_set = 0
        for k in range(steps):
            t = k * dt
            # a new gather: blow the last one away on its hit, scatter a fresh one
            while gi + 1 < len(self.gathers) and t >= self.gathers[gi + 1]:
                gi += 1
                b = (gi - 1) % 2
                a_set = gi % 2
                d = self.dirs[gi] / (np.linalg.norm(self.dirs[gi]) + 1e-9)
                perp = np.array([-d[1], d[0]])
                sp = speed_rng.uniform(900, 2300, N) * self.scale * (0.6 + 0.7 * self.depth[b])
                vel[b] = vel[b] * 0.3 + d[None, :] * sp[:, None] + perp[None, :] * speed_rng.normal(0, 260, N)[:, None] * self.scale
                blown[b] = t
                live[b] = False
                pos[a_set] = self._scatter(N)
                vel[a_set] = np.zeros((N, 2))
                tgt[a_set] = self._targets(N)
                spawn[a_set] = t
                live[a_set] = True
                vela += d[None, :] * speed_rng.uniform(300, 900, Na)[:, None] * self.scale
            if not turn_done and t >= self.turn:
                turn_done = True
                self._assign(pos[a_set])
                tgt[a_set] = tgt[a_set].copy()
                tgt[a_set][self.assign_p] = self.cells[self.assign_c]
                un = np.ones(N, bool)
                un[self.assign_p] = False
                fade_t[un] = self.turn + 0.25 + speed_rng.uniform(0, 1.1, un.sum())
                self.unassigned = un
            # the gathering set
            for s in (0, 1):
                if live[s]:
                    age = t - spawn[s]
                    ramp = float(smooth(0.0, 0.5, age))
                    to = tgt[s] - pos[s]
                    dist = np.sqrt((to ** 2).sum(1)) + 1e-6
                    flow = self.curl(pos[s], t) * self.flow_amp * np.clip(dist / (140 * self.scale), 0.1, 1.0)[:, None]
                    rate = self.rate
                    if turn_done and s == a_set:
                        boost = 1 + 3.5 * float(smooth(self.turn + 1.2, self.turn + self.land, t))
                        rate = self.turn_rate * boost
                        flow = flow * (1 - 0.8 * float(smooth(self.turn, self.turn + 1.6, t)))
                    desired = to * rate + flow * (0.55 + 0.9 * self.depth[s][:, None])
                    vel[s] += (desired - vel[s]) * (1 - math.exp(-dt * 5.0))
                    pos[s] += vel[s] * dt * ramp
                    if turn_done and s == a_set:
                        near = (dist < 1.2 * self.scale) & np.isinf(land_t)
                        late = (t > self.turn + self.land) & np.isinf(land_t)
                        hit = near | late
                        hit[self.unassigned] = False
                        if hit.any():
                            land_t[hit] = t
                            pos[s][hit] = tgt[s][hit]
                            cells = self.p2c[hit]
                            self.arrive[cells] = np.minimum(self.arrive[cells], t)
                else:
                    vel[s] *= math.exp(-dt * 1.1)
                    vel[s] += self.curl(pos[s], t) * 700 * self.scale * dt
                    pos[s] += vel[s] * dt
            # ambient dust
            vela *= math.exp(-dt * 1.6)
            posa += (self.curl(posa, t * 0.7) * 26 * self.scale * (0.4 + self.depth_a[:, None]) + vela) * dt
            posa[:, 0] = np.mod(posa[:, 0] + 0.05 * W, 1.1 * W) - 0.05 * W
            posa[:, 1] = np.mod(posa[:, 1] + 0.05 * H, 1.1 * H) - 0.05 * H
            # record frames
            while next_frame <= nf and next_frame / self.fps <= t + 1e-9:
                ft = next_frame / self.fps
                rec = []
                for s in (0, 1):
                    if live[s]:
                        age = ft - spawn[s]
                        al = np.full(N, float(smooth(0.0, 0.18, age + 0.18 * (spawn[s] <= 0))))
                        if turn_done and s == a_set:
                            al = al * np.where(np.isfinite(land_t), 1 - smooth(land_t, land_t + 0.14, ft), 1.0)
                            al = al * np.where(np.isfinite(fade_t), 1 - smooth(fade_t, fade_t + 0.6, ft), 1.0)
                    elif blown[s] > -1e8:
                        al = np.full(N, float(1 - smooth(0.0, 0.5, ft - blown[s])))
                    else:
                        continue
                    if al.max() <= 0.003:
                        continue
                    rec.append((pos[s].astype(np.float32).copy(), vel[s].astype(np.float32).copy(), self.depth[s], al.astype(np.float32),
                                not live[s]))
                ala = np.full(Na, 1.0) * (1 - float(smooth(self.turn + 0.2, self.turn + 1.6, ft)))
                if ala.max() > 0.003:
                    rec.append((posa.astype(np.float32).copy(), vela.astype(np.float32).copy(), self.depth_a, (ala * 0.75).astype(np.float32),
                                False))
                frames[next_frame] = rec
                next_frame += 1
        self.frames = frames
        # any cell never reached lands at the end of the landing window
        self.arrive[np.isinf(self.arrive)] = self.turn + self.land

    def _assign(self, p):
        """Each cell takes its nearest free dot (cells in random order), so paths rarely cross."""
        M = len(self.cells)
        order = self.rng.permutation(M)
        free = np.ones(len(p), bool)
        ap, ac = [], []
        for c in order:
            d = ((p - self.cells[c]) ** 2).sum(1)
            d[~free] = np.inf
            j = int(np.argmin(d))
            if not np.isfinite(d[j]):
                break
            free[j] = False
            ap.append(j)
            ac.append(c)
        self.assign_p = np.array(ap, int)
        self.assign_c = np.array(ac, int)
        self.p2c = np.zeros(len(p), int)
        self.p2c[self.assign_p] = self.assign_c

    # ------------------------------------------------------------------ drawing
    def render(self, t):
        """Coverage layer (H, W) float32 of the dots at film time t."""
        f = int(round(t * self.fps))
        rec = self.frames.get(f)
        if not rec:
            return None
        ss = 2
        W, H = self.W, self.H
        rmin, rmax = float(self.spec.get("r_min", 1.2)) * self.scale, float(self.spec.get("r_max", 3.8)) * self.scale
        shutter = float(self.spec.get("shutter", 1 / 45))
        near_z = float(self.spec.get("near", 0.86))
        sharp = Image.new("L", (W * ss, H * ss), 0)
        soft = Image.new("L", (W * ss, H * ss), 0)
        ds, dn = ImageDraw.Draw(sharp), ImageDraw.Draw(soft)
        rows = []
        for p, v, z, al, blown in rec:
            r = rmin + (rmax - rmin) * z ** 1.4
            b = (0.5 + 0.5 * z) * al
            rows.append((z, p, v, r, b, np.full(len(z), blown)))
        Z = np.concatenate([a[0] for a in rows])
        P = np.concatenate([a[1] for a in rows])
        V = np.concatenate([a[2] for a in rows])
        R = np.concatenate([a[3] for a in rows])
        B = np.concatenate([a[4] for a in rows])
        BL = np.concatenate([a[5] for a in rows])
        keep = (B > 0.01) & (P[:, 0] > -40) & (P[:, 0] < W + 40) & (P[:, 1] > -40) & (P[:, 1] < H + 40)
        order = np.argsort(Z[keep])
        Z, P, V, R, B, BL = Z[keep][order], P[keep][order], V[keep][order], R[keep][order], B[keep][order], BL[keep][order]
        for z, (x, y), (vx, vy), r, br, blown in zip(Z, P, V, R, B, BL):
            near = z > near_z
            d = dn if near else ds
            if near:  # the closest dots sit in front of the focus: bigger, softer, dimmer
                r, br = r * 1.9, br * 0.55
            x, y, r = x * ss, y * ss, r * ss
            fill = int(max(0, min(255, br * 255)))
            if blown and (vx * vx + vy * vy) > (240 * self.scale) ** 2:  # the gust: the shutter smears them
                x2, y2 = x - vx * shutter * ss, y - vy * shutter * ss
                d.line([(x, y), (x2, y2)], fill=fill, width=max(1, int(round(2 * r))))
                d.ellipse([x2 - r, y2 - r, x2 + r, y2 + r], fill=fill)
            d.ellipse([x - r, y - r, x + r, y + r], fill=fill)
        soft = soft.filter(ImageFilter.GaussianBlur(2.6 * ss * self.scale))
        a = np.asarray(sharp.resize((W, H), Image.BOX), np.float32) / 255.0
        b = np.asarray(soft.resize((W, H), Image.BOX), np.float32) / 255.0
        return 1 - (1 - a) * (1 - b)
