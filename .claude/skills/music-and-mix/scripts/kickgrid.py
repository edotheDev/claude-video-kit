#!/usr/bin/env python3
"""Kick onsets (40-120 Hz, 5 ms hops) in a window of a song, and the beat period: python kickgrid.py song t0 t1"""
import subprocess, sys
import numpy as np
p, a, b = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
SR = 16000
raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(a), '-t', str(b - a), '-i', p, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'], capture_output=True).stdout
x = np.frombuffer(raw, np.float32).astype(np.float64)
X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / SR); X[(f < 40) | (f > 120)] = 0; y = np.fft.irfft(X, len(x))
hop = 80; n = len(y) // hop
e = np.sqrt((y[:n * hop].reshape(n, hop) ** 2).mean(1)); e = np.convolve(e, np.ones(5) / 5, 'same'); e = e / e.max()
d = e - np.concatenate([np.zeros(6), e[:-6]])
on = [k for k in range(2, n - 2) if d[k] > 0.08 and d[k] == d[max(0, k - 20):k + 20].max()]
ts = [a + k * hop / SR for k in on]
print('onsets', ' '.join(f'{t:.3f}' for t in ts[:60]))
if len(ts) > 4:
    iv = np.diff(ts); med = np.median(iv); print('median interval', round(med, 4), 'bpm', round(60 / med, 2))
