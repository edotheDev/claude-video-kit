#!/usr/bin/env python3
"""YouTube storyboard contact sheets (preview a long video without downloading it).
  python yt_storyboard.py <video id> [sb0|sb1] [t_from t_to]  -> ./footage/sb/<id>.jpg (set SB_OUT to change),
  tiles labelled with their source time (s)
Reads the storyboard format from `yt-dlp -j`, fetches its fragment images (i.ytimg.com) and lays the tiles out 10 across."""
import json, os, subprocess, sys, urllib.request, io
from PIL import Image, ImageDraw
OUT = os.environ.get('SB_OUT', os.path.join('footage', 'sb')); os.makedirs(OUT, exist_ok=True)
vid = sys.argv[1]; fmt = sys.argv[2] if len(sys.argv) > 2 else 'sb0'
t_from = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
t_to = float(sys.argv[4]) if len(sys.argv) > 4 else 1e9
jp = os.path.join(OUT, vid + '.json')
if not os.path.exists(jp):
    j = subprocess.run(['yt-dlp', '-j', '--no-warnings', 'https://www.youtube.com/watch?v=' + vid], capture_output=True, text=True).stdout
    open(jp, 'w', encoding='utf-8').write(j)
info = json.load(open(jp, encoding='utf-8'))
f = [x for x in info['formats'] if x['format_id'] == fmt][0]
rows, cols, tw, th = f['rows'], f['columns'], f['width'], f['height']
dur = info['duration']; n_frag = len(f['fragments'])
per = cols * rows
tiles = []
t = 0.0
for k, fr in enumerate(f['fragments']):
    fd = fr.get('duration') or dur / n_frag
    step = fd / per
    t0 = sum((x.get('duration') or dur / n_frag) for x in f['fragments'][:k])
    if t0 + fd < t_from or t0 > t_to: continue
    url = fr.get('url') or (f.get('fragment_base_url', '') + fr['path'])
    im = Image.open(io.BytesIO(urllib.request.urlopen(url, timeout=30).read())).convert('RGB')
    for i in range(per):
        ts = t0 + i * step
        if ts > dur or ts < t_from or ts > t_to: continue
        r, c = divmod(i, cols)
        tile = im.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th))
        if tile.size[0] < tw: continue
        tiles.append((ts, tile))
MAXT = int(os.environ.get('SB_MAX', '120'))
if len(tiles) > MAXT:
    k = len(tiles) / MAXT
    tiles = [tiles[int(i * k)] for i in range(MAXT)]
W10 = 10
sheet = Image.new('RGB', (W10 * tw, ((len(tiles) + W10 - 1) // W10) * (th + 12)), (0, 0, 0))
d = ImageDraw.Draw(sheet)
for i, (ts, tile) in enumerate(tiles):
    x, y = (i % W10) * tw, (i // W10) * (th + 12)
    sheet.paste(tile, (x, y + 12)); d.text((x + 2, y), f'{int(ts // 60)}:{ts % 60:04.1f} ({ts:.0f})', fill=(255, 220, 0))
name = os.path.join(OUT, f'{vid}_{int(t_from)}.jpg' if t_from else f'{vid}.jpg')
sheet.save(name, quality=85)
print(name, len(tiles), 'tiles', f'{dur}s', info.get('title', '')[:80], info.get('channel', ''), f"{info.get('width')}x{info.get('height')}")
