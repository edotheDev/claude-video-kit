#!/usr/bin/env python3
"""Local contact sheet: python csheet.py video t0 t1 step out.jpg [cols] [tile_w]  (tiles labelled with source time)"""
import subprocess, sys
import numpy as np
from PIL import Image, ImageDraw
p, a, b, step, out = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4]), sys.argv[5]
cols = int(sys.argv[6]) if len(sys.argv) > 6 else 10
tw = int(sys.argv[7]) if len(sys.argv) > 7 else 240
r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'csv=p=0', p], capture_output=True, text=True).stdout.strip().split(',')
w, h = int(r[0]), int(r[1]); th = int(round(tw * h / w))
raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(a), '-i', p, '-t', str(b - a), '-vf', f'fps=1/{step},scale={tw}:{th}', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
n = len(raw) // (tw * th * 3)
fr = np.frombuffer(raw[:n * tw * th * 3], np.uint8).reshape(n, th, tw, 3)
rows = (n + cols - 1) // cols
sheet = Image.new('RGB', (cols * tw, rows * (th + 12)), (0, 0, 0)); d = ImageDraw.Draw(sheet)
for i in range(n):
    x, y = (i % cols) * tw, (i // cols) * (th + 12)
    sheet.paste(Image.fromarray(fr[i]), (x, y + 12)); d.text((x + 2, y), f'{a + i * step:.1f}', fill=(255, 220, 0))
sheet.save(out, quality=85); print(out, n, 'tiles', f'{w}x{h}')
