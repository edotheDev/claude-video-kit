#!/usr/bin/env python3
"""Tempo and beat phase from spectral flux: python tempo.py song t0 t1 -> bpm, beat times (the strongest phase)."""
import subprocess, sys
import numpy as np
p, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
SR = 22050; hop = 110  # 5 ms
raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(a), '-t', str(b - a), '-i', p, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'], capture_output=True).stdout
x = np.frombuffer(raw, np.float32).astype(np.float64)
win = 1024; n = (len(x) - win) // hop
F = np.abs(np.array([np.fft.rfft(x[k * hop:k * hop + win] * np.hanning(win)) for k in range(n)]))
L = np.log1p(F * 10)
flux = np.maximum(0, np.diff(L, axis=0)).sum(1); flux = np.concatenate([[0], flux])
flux -= np.convolve(flux, np.ones(40) / 40, 'same'); flux = np.maximum(flux, 0)
ac = np.correlate(flux, flux, 'full')[n - 1:]
lags = np.arange(len(ac)) * hop / SR
sel = (lags > 0.3) & (lags < 1.0)
best = lags[sel][np.argmax(ac[sel])]
# refine to 0.1 ms
cand = np.arange(best - 0.01, best + 0.01, 0.0001)
def score(per):
    k = (np.arange(0, b - a - per, per) / (hop / SR)).astype(int)
    return max(flux[np.clip(k + ph, 0, n - 1)].sum() for ph in range(0, int(per / (hop / SR)), 1))
per = max(cand[::10], key=score)
steps = int(per / (hop / SR))
phs = [flux[np.clip((np.arange(0, b - a - per, per) / (hop / SR)).astype(int) + ph, 0, n - 1)].sum() for ph in range(steps)]
ph = int(np.argmax(phs))
t0 = a + ph * hop / SR
print('period', round(per, 4), 'bpm', round(60 / per, 2), 'first beat', round(t0, 3))
print('beats', ' '.join(f'{t0 + k * per:.3f}' for k in range(int((b - a) / per))))
