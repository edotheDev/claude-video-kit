"""Rank candidate tracks by how close their loudest 30 s sounds to a reference song's drop.

  python similar.py reference.mp3[@start:end] cand1.mp3 cand2.mp3 ...

Features: tempo from the kick band, spectral centroid, a 32-band log spectrum profile (timbre), the share of energy in
low / low-mid / high-mid / air bands, and onset density. Without @start:end the reference's loudest 30 s is used.
Use it to find a free track that feels like a commercial reference song you cannot license: give the reference's
drop as @start:end and rank free candidates against it.
"""
import subprocess
import sys

import numpy as np

SR = 22050


def load(p):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def feats(x):
    n, hop = 2048, 512
    w = np.hanning(n)
    fr = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(fr)[:, None]
    S = np.abs(np.fft.rfft(x[idx] * w, axis=1))
    f = np.fft.rfftfreq(n, 1 / SR)
    edges = np.geomspace(40, 11000, 33)
    prof = np.array([S[:, (f >= a) & (f < b)].mean() for a, b in zip(edges[:-1], edges[1:])])
    prof = np.log(prof + 1e-9)
    prof -= prof.mean()
    cen = float((S * f).sum() / (S.sum() + 1e-9))
    L = np.log1p(S)
    flux = np.maximum(0, np.diff(L, axis=0)).sum(1)
    low = S[:, f < 150].sum(1)
    e = low - np.convolve(low, np.ones(40) / 40, mode="same")
    e = np.maximum(e, 0)
    e = e - e.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lags = np.arange(len(ac)) * hop / SR
    ok = (lags > 60 / 170) & (lags < 60 / 85)
    bpm = 60 / lags[np.argmax(ac * ok)]
    bands = []
    for a, b in ((20, 150), (150, 800), (800, 4000), (4000, 11000)):
        bands.append(S[:, (f >= a) & (f < b)].sum())
    bands = np.array(bands) / sum(bands)
    onset = float((flux > np.percentile(flux, 90)).mean())
    return prof, cen, bpm, bands, onset


def loudest(x, secs=30):
    win = int(secs * SR)
    if len(x) <= win:
        return x, 0.0
    hop = SR
    best, bi = -1, 0
    for i in range(0, len(x) - win, hop):
        r = np.mean(x[i:i + win] ** 2)
        if r > best:
            best, bi = r, i
    return x[bi:bi + win], bi / SR


arg = sys.argv[1]
if "@" in arg:
    path, win = arg.split("@")
    a, b = (float(v) for v in win.split(":"))
    ref = load(path)
    rseg = ref[int(a * SR):int(b * SR)]
else:
    ref = load(arg)
    rseg, _ = loudest(ref)
rp, rc, rb, rbands, ron = feats(rseg)
print(f"REF: bpm {rb:.1f} centroid {rc:.0f} bands {np.round(rbands, 3)} onset {ron:.3f}")
rows = []
for p in sys.argv[2:]:
    x = load(p)
    seg, at = loudest(x)
    pr, c, b, bands, on = feats(seg)
    timbre = float(np.corrcoef(pr, rp)[0, 1])
    tempo_d = min(abs(b - rb), abs(b * 2 - rb), abs(b / 2 - rb)) / rb
    band_d = float(np.abs(bands - rbands).sum())
    score = timbre - 1.5 * tempo_d - 1.2 * band_d - abs(c - rc) / rc * 0.5
    rows.append((score, p, timbre, b, c, band_d, at, len(x) / SR))
for s, p, t, b, c, bd, at, d in sorted(rows, reverse=True):
    print(f"{s:6.3f}  {p:12s} timbre {t:.3f}  bpm {b:6.1f}  centroid {c:5.0f}  banddiff {bd:.3f}  loudest@{at:5.0f}s  dur {d:5.0f}")
