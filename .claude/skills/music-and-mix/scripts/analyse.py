import sys, subprocess, numpy as np
SR = 22050
HOP = 256
def load(p):
    raw = subprocess.run(["ffmpeg","-v","error","-i",p,"-ac","1","-ar",str(SR),"-f","f32le","-"],capture_output=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)
def stft_mag(x, n=1024, hop=HOP):
    w = np.hanning(n)
    frames = 1 + (len(x) - n) // hop
    idx = np.arange(n)[None,:] + hop*np.arange(frames)[:,None]
    return np.abs(np.fft.rfft(x[idx]*w, axis=1))
def analyse(p):
    x = load(p); dur = len(x)/SR
    S = stft_mag(x)
    freqs = np.fft.rfftfreq(1024, 1/SR)
    L = np.log1p(100*S)
    flux = np.maximum(0, np.diff(L, axis=0)).sum(1)
    flux = np.concatenate([[0], flux])
    fr = SR/HOP
    env = flux - np.convolve(flux, np.ones(int(fr*0.5))/int(fr*0.5), mode="same")
    env = np.maximum(env, 0)
    # tempo by autocorrelation over the whole track
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e)-1:]
    lags = np.arange(len(ac))/fr
    bpm_range = (lags > 60/180) & (lags < 60/60)
    best = np.argmax(ac * bpm_range)
    bpm = 60/lags[best]
    conf = ac[best]/ac[0]
    # rms & low band per 2 s
    win = int(2*fr)
    rms = np.sqrt((S**2).sum(1))
    low = np.sqrt((S[:, freqs < 150]**2).sum(1))
    hi = np.sqrt((S[:, freqs > 3000]**2).sum(1))
    def blocks(v): 
        n = len(v)//win
        return v[:n*win].reshape(n, win).mean(1)
    R, Lo, Hi = blocks(rms), blocks(low), blocks(hi)
    bars = " .:-=+*#%@"
    def spark(v):
        v = v/ (v.max()+1e-9)
        return "".join(bars[min(9,int(a*9.99))] for a in v)
    cent = (S*freqs).sum(1)/(S.sum(1)+1e-9)
    first = np.argmax(rms > 0.05*rms.max())/fr
    print(f"== {p}  dur {dur:6.1f}s  bpm {bpm:6.2f} (beat {60/bpm:.3f}s, conf {conf:.2f})  first-sound {first:.2f}s  centroid {np.median(cent):5.0f}Hz")
    print("   rms  |" + spark(R) + "|")
    print("   low  |" + spark(Lo) + "|")
    print("   high |" + spark(Hi) + "|")
for p in sys.argv[1:]:
    analyse(p)
