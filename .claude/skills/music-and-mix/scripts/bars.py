"""Per-bar energy of a track on its kick grid: finds the tempo and first downbeat from the kick band, then prints each
bar's low, mid, high and full level, so builds, drops and breaks show as a table.

  python bars.py track.mp3 [bpm]
"""
import subprocess
import sys

import numpy as np

SR = 22050
p = sys.argv[1]
x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", p, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                                 capture_output=True).stdout, np.float32).astype(np.float64)
X = np.fft.rfft(x)
f = np.fft.rfftfreq(len(x), 1 / SR)


def band(lo, hi):
    Y = X.copy()
    Y[(f < lo) | (f > hi)] = 0
    return np.fft.irfft(Y, len(x))


lo, mid, hi = band(20, 120), band(120, 2000), band(4000, 11000)
# kick onsets: rises of the low-band envelope
env = np.sqrt(np.convolve(lo ** 2, np.ones(220) / 220, mode="same"))[::22]      # 1 ms
d = np.maximum(np.diff(env, prepend=env[0]), 0)
thr = 0.3 * d.max()
peaks = []
i = 0
while i < len(d):
    if d[i] > thr and d[i] == d[max(0, i - 60):i + 60].max():
        peaks.append(i / 1000.0)
        i += 250
    else:
        i += 1
peaks = np.array(peaks)
bpm = float(sys.argv[2]) if len(sys.argv) > 2 else 60.0 / np.median(np.diff(peaks))
beat = 60.0 / bpm
bar = 4 * beat
# phase: the offset that puts most kicks on beats
ph = np.linspace(0, beat, 200, endpoint=False)
score = [np.sum(np.cos(2 * np.pi * (peaks - p0) / beat)) for p0 in ph]
p0 = ph[int(np.argmax(score))]
# the downbeat: of the 4 beat phases, the one where bars start (strongest low energy on beat 1 of 4-bar cycles)
print(f"{p}: {len(x) / SR:.1f} s, {bpm:.2f} BPM, beat {beat:.4f}, bar {bar:.4f}, beat phase {p0:.3f}")


def db(v):
    return 20 * np.log10(np.sqrt(np.mean(v ** 2)) + 1e-9)


k = 0
t0 = p0
while t0 + bar <= len(x) / SR + 0.01:
    a, b = int(t0 * SR), int(min(len(x), (t0 + bar) * SR))
    print(f"bar {k:3d} {t0:7.2f}  low {db(lo[a:b]):6.1f}  mid {db(mid[a:b]):6.1f}  high {db(hi[a:b]):6.1f}  full {db(x[a:b]):6.1f}")
    t0 += bar
    k += 1
