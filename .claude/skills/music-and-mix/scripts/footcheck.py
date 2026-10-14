#!/usr/bin/env python3
"""Shot by shot check of the footage's OWN audio, to decide which shots may keep their sound.

A clip's sound is only worth keeping when it is plain natural sound: no speech, no beat, no music bed. News clips and
trailers almost always carry a narrator or a score, and that reads as "another song in the background" under yours.

1. Lay the footage audio out on the film timeline (the sound of every shot where it plays) as footage_raw.wav.
2. Split it with Demucs:  demucs -n htdemucs -o stems footage_raw.wav   (gives vocals, drums, bass, other)
3. python footcheck.py footage_raw.wav stems/htdemucs/footage_raw spans.json
   spans.json: [{"key": "shot-03", "t0": 4.2, "t1": 6.1}, ...] in film seconds.

Per shot it prints the raw level, each stem relative to the raw (dB), the chroma peak of the 'other' stem (1 = flat
noise, 3+ = a clear pitch class, which means music) and its spectral flatness. Keep a shot's sound only when vocals,
drums and bass sit well under the raw level and chroma stays low. Writes footcheck.json beside spans.json.
Needs: numpy, soundfile, librosa."""
import json
import os
import sys

import librosa
import numpy as np
import soundfile as sf

if len(sys.argv) < 4:
    sys.exit(__doc__)
raw_path, stems_dir, spans_path = sys.argv[1:4]

R = {}
sr = None
for k in ('raw', 'vocals', 'drums', 'bass', 'other'):
    p = raw_path if k == 'raw' else os.path.join(stems_dir, k + '.wav')
    x, sr = sf.read(p)
    R[k] = x.mean(1) if x.ndim == 2 else x


def db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)


out = []
for s in json.load(open(spans_path, encoding='utf-8')):
    a, b = int(s['t0'] * sr) + 1000, int(s['t1'] * sr) - 1000      # skip the cut edges
    if b - a < 2000:
        continue
    d = {k: db(R[k][a:b]) for k in R}
    o = R['other'][a:b].astype(np.float32)
    C = librosa.feature.chroma_stft(y=o, sr=sr, n_fft=4096, hop_length=1024)
    cp = float(np.median(C.max(0) / (C.mean(0) + 1e-9)))
    fl = float(np.median(librosa.feature.spectral_flatness(y=o, n_fft=2048, hop_length=512)))
    row = dict(key=s['key'], raw=round(d['raw'], 1), voc=round(d['vocals'] - d['raw'], 1),
               drm=round(d['drums'] - d['raw'], 1), bas=round(d['bass'] - d['raw'], 1),
               oth=round(d['other'] - d['raw'], 1), chroma=round(cp, 2), flat=round(fl, 4))
    out.append(row)
    print(f"{row['key']:16s} raw {row['raw']:6.1f}  voc {row['voc']:6.1f} drm {row['drm']:6.1f} "
          f"bas {row['bas']:6.1f} oth {row['oth']:6.1f}  chroma {row['chroma']:5.2f} flat {row['flat']:.4f}")
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(spans_path)), 'footcheck.json'), 'w'), indent=1)
