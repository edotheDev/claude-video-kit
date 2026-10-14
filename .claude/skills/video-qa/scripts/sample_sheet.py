#!/usr/bin/env python3
"""Contact sheet from a local file or an HTTP URL, one frame per time (ffmpeg seeks, 4 at a time).
  python sample_sheet.py SRC OUT.jpg t0 t1 step [cols] [cellw]"""
import subprocess, sys, concurrent.futures as cf
import numpy as np
from PIL import Image, ImageDraw
src, out, t0, t1, step = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
cols = int(sys.argv[6]) if len(sys.argv) > 6 else 6
cw = int(sys.argv[7]) if len(sys.argv) > 7 else 320
times = []
t = t0
while t < t1: times.append(round(t, 2)); t += step
def grab(t):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.2f}', '-i', src, '-frames:v', '1', '-vf', f'scale={cw}:-2', '-f', 'image2pipe', '-vcodec', 'png', '-'],
                       capture_output=True, timeout=120)
    try:
        import io
        return Image.open(io.BytesIO(r.stdout)).convert('RGB')
    except Exception:
        return None
with cf.ThreadPoolExecutor(4) as ex: ims = list(ex.map(grab, times))
ok = [im for im in ims if im is not None]
ch = ok[0].height if ok else 180
rows = (len(times) + cols - 1) // cols
sheet = Image.new('RGB', (cols * cw, rows * ch), (20, 20, 20))
d = ImageDraw.Draw(sheet)
for i, (t, im) in enumerate(zip(times, ims)):
    x, y = (i % cols) * cw, (i // cols) * ch
    if im is not None: sheet.paste(im.resize((cw, ch)), (x, y))
    d.rectangle([x, y, x + 58, y + 13], fill=(0, 0, 0)); d.text((x + 2, y + 1), f'{t:.1f}', fill=(255, 220, 0))
sheet.save(out, quality=85); print('wrote', out, len(ok), '/', len(times))
