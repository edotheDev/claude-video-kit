import sys, subprocess, numpy as np
from PIL import Image, ImageDraw
SR = 22050
def load(p, t0, t1):
    raw = subprocess.run(["ffmpeg","-v","error","-ss",str(t0),"-t",str(t1-t0),"-i",p,"-ac","1","-ar",str(SR),"-f","f32le","-"],capture_output=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)
def spec(x, n=4096, hop=256):
    w = np.hanning(n)
    fr = 1 + (len(x)-n)//hop
    idx = np.arange(n)[None,:] + hop*np.arange(fr)[:,None]
    return np.abs(np.fft.rfft(x[idx]*w, axis=1)), hop
out, t0, t1, pxs = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
files = sys.argv[5:]
H = 170
rows = []
for f in files:
    x = load(f, t0, t1)
    S, hop = spec(x)
    freqs = np.fft.rfftfreq(4096, 1/SR)
    logf = np.geomspace(30, 11000, H)
    # interpolate each frame onto log freq
    Si = np.stack([np.interp(logf, freqs, s) for s in S])  # frames x H
    D = 20*np.log10(Si + 1e-6)
    D = np.clip((D - (D.max()-70)) / 70, 0, 1)
    W = int((t1-t0)*pxs)
    # resample time
    ti = np.linspace(0, len(D)-1, W).astype(int)
    img = (255*(1-D[ti].T[::-1])).astype(np.uint8)
    im = Image.fromarray(img).convert("RGB")
    d = ImageDraw.Draw(im)
    for s in range(int(t0), int(t1)+1):
        x_ = int((s-t0)*pxs)
        col = (255,0,0) if s % 10 == 0 else (255,180,180)
        d.line([(x_,0),(x_, 6 if s%10 else 14)], fill=col)
    d.text((4, 2), f, fill=(255,0,0))
    rows.append(im)
sheet = Image.new("RGB", (max(r.width for r in rows), sum(r.height+4 for r in rows)), "white")
y = 0
for r in rows:
    sheet.paste(r, (0, y)); y += r.height+4
sheet.save(out)
