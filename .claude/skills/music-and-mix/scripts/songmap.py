#!/usr/bin/env python3
"""Song structure map: per 2 s full band and kick band (40-120 Hz) loudness, and a tempo estimate from the kick envelope."""
import subprocess, sys
import numpy as np
p = sys.argv[1]
SR = 8000
raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', p, '-ac', '1', '-ar', str(SR), '-f', 'f32le', '-'], capture_output=True).stdout
x = np.frombuffer(raw, np.float32)
X = np.fft.rfft(x.reshape(-1, 400)[:, :], axis=1) if False else None
hop = 400  # 50 ms
n = len(x) // hop
fr = x[:n * hop].reshape(n, hop)
F = np.abs(np.fft.rfft(fr * np.hanning(hop), axis=1))
f = np.fft.rfftfreq(hop, 1 / SR)
low = (F[:, (f >= 40) & (f <= 120)] ** 2).sum(1); full = (F ** 2).sum(1)
L = 10 * np.log10(low + 1e-9); A = 10 * np.log10(full + 1e-9)
seg = 40  # 2 s
print('dur', round(len(x) / SR, 1))
for k in range(0, n - seg, seg):
    print(f'{k * 0.05:6.1f}  full {A[k:k + seg].mean() - A.max():6.1f}  kick {L[k:k + seg].mean() - L.max():6.1f}  kickvar {L[k:k+seg].std():4.1f}')
