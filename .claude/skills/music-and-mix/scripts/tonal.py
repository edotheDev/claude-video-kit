"""How melodic is a section? Chroma clarity (1 - normalised chroma entropy) and harmonic peakiness in 200 to 4000 Hz,
plus the major/minor lean of the average chroma (Krumhansl profiles). Compared with the reference's drop."""
import subprocess
import sys

import numpy as np

SR = 22050
MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MIN = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NAMES = "C C# D D# E F F# G G# A A# B".split()


def load(p, a, b):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(a), "-t", str(b - a), "-i", p, "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def tonal(x):
    n, hop = 8192, 2048
    w = np.hanning(n)
    fr = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(fr)[:, None]
    S = np.abs(np.fft.rfft(x[idx] * w, axis=1))
    f = np.fft.rfftfreq(n, 1 / SR)
    band = (f > 200) & (f < 4000)
    fb = f[band]
    midi = 69 + 12 * np.log2(fb / 440.0)
    pc = np.mod(np.round(midi), 12).astype(int)
    C = np.zeros((fr, 12))
    for k in range(12):
        C[:, k] = (S[:, band][:, pc == k] ** 2).sum(1)
    C = C / (C.sum(1, keepdims=True) + 1e-12)
    ent = -(C * np.log(C + 1e-12)).sum(1) / np.log(12)
    clarity = float(1 - ent.mean())
    mean = C.mean(0)
    best = None
    for r in range(12):
        for prof, kind in ((MAJ, "maj"), (MIN, "min")):
            c = np.corrcoef(np.roll(prof, r), mean)[0, 1]
            if best is None or c > best[0]:
                best = (c, NAMES[r] + " " + kind)
    # harmonic peakiness: mean ratio of spectral peak to median in the band
    B = S[:, band]
    peak = float(np.mean(np.percentile(B, 99, axis=1) / (np.median(B, axis=1) + 1e-9)))
    return clarity, best[1], round(best[0], 2), peak


tracks = [a.split("@") for a in sys.argv[1:]]   # file@start@end
for p, a, b in tracks:
    c, key, kc, pk = tonal(load(p, float(a), float(b)))
    print(f"{p:16s} {a:>5s}-{b:<5s} chroma clarity {c:.3f}  key {key:8s} ({kc})  peakiness {pk:6.1f}")
