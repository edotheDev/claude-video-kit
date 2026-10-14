import sys, subprocess, numpy as np
SR = 48000
p = sys.argv[1]
raw = subprocess.run(["ffmpeg","-v","error","-i",p,"-ac","1","-ar",str(SR),"-f","f32le","-"],capture_output=True).stdout
x = np.frombuffer(raw, np.float32).astype(np.float64)
# low band (kick) envelope via 2nd-order-ish smoothing of lowpassed signal
def lowpass(x, fc):
    # simple FFT brickwall
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1/SR)
    X[f > fc] = 0
    return np.fft.irfft(X, len(x))
lo = lowpass(x, 120)
hop = 48  # 1 ms
env = np.sqrt(np.convolve(lo**2, np.ones(480)/480, mode="same"))[::hop]   # 10 ms rms, sampled per ms
d = np.diff(env, prepend=env[0])
d = np.maximum(d, 0)
thr = float(sys.argv[2]) if len(sys.argv) > 2 else 0.25
peaks = []
m = d.max()
i = 0
while i < len(d):
    if d[i] > thr*m and d[i] == d[max(0,i-60):i+60].max():
        # onset time: walk back to where env starts rising (10% of rise)
        peaks.append(i/1000.0); i += 300
    else:
        i += 1
peaks = np.array(peaks)
print("n kicks", len(peaks))
print(" ".join(f"{t:.3f}" for t in peaks))
dd = np.diff(peaks)
print("intervals median", np.median(dd))
