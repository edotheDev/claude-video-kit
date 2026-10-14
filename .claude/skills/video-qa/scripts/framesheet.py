#!/usr/bin/env python3
"""Frame-accurate sheet of a region of a rendered video: framesheet.py video out.jpg x0 y0 x1 y1 cols t1,t2,..."""
import subprocess, sys
import numpy as np
from PIL import Image, ImageDraw
v, out = sys.argv[1], sys.argv[2]
x0, y0, x1, y1, cols = map(int, sys.argv[3:8])
ts = [float(x) for x in sys.argv[8].split(',')]
r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', v], capture_output=True, text=True).stdout.strip().split(',')
w, h = int(r[0]), int(r[1])
tiles = []
for t in ts:
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', f'{t:.3f}', '-i', v, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    if len(raw) < w * h * 3: print("no frame at", t, len(raw)); continue
    a = np.frombuffer(raw[:w * h * 3], np.uint8).reshape(h, w, 3)[y0:y1, x0:x1]
    sc = 480 / (x1 - x0)
    im = Image.fromarray(a).resize((480, int((y1 - y0) * sc))); d = ImageDraw.Draw(im); d.text((3, 3), f'{t:.2f}', fill=(255, 255, 0)); tiles.append(im)
th = tiles[0].height
s = Image.new('RGB', (480 * cols, th * ((len(tiles) + cols - 1) // cols)))
for i, t in enumerate(tiles): s.paste(t, ((i % cols) * 480, (i // cols) * th))
s.save(out, quality=90); print('wrote', out)
