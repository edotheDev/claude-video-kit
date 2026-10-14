#!/usr/bin/env python3
"""A builder for a voice-led cut film: the pattern every cutfilm.py film follows.

A film is a small Python file like this one. It owns the decisions (which takes, where the cuts land, how the
words are grouped) and writes a timeline JSON that cutfilm.py renders. Copy this folder into your project and edit it.

  python build.py              # conform the takes and write timeline.json
  python ../../scripts/cutfilm.py timeline.json --times 0.5,2.5,4.5,6.5 --out stills.png    # a still sheet first
  python ../../scripts/cutfilm.py timeline.json --preview --out preview.mp4 --sheet sheet.png
  python ../../scripts/cutfilm.py timeline.json --out master.mp4

Inputs it looks for beside itself:
  words.json   word timings of the voiceover: [{"word": "Every", "start": 0.42, "end": 0.61}, ...]
               (make it with the captions skill: node words.mjs vo.wav words.json). Without it, a demo line is used.
  vo.wav       the voiceover, and music.mp3 a track to cut to. Both optional; with both, the mix is built too.
  footage/     source clips. Run with --test-media first to generate three abstract test clips with ffmpeg.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
from cutfilm import conform_takes  # noqa: E402

SIZE, FPS = (1440, 1080), 30          # 4:3. Use (1080, 1920) for 9:16.
BPM, PHASE = 120.0, 0.0               # the track's beat grid: measure it (music-and-mix: tempo.py / kicks.py)

# Every take: a source, an in point, a duration, a speed and a crop centre. conform_takes cuts them once,
# so the render only reads frames forward. Re-running skips takes whose settings did not change.
TAKES = {
    "a": {"src": "footage/test-a.mp4", "in": 0.0, "dur": 4.0, "speed": 1.0, "cx": 0.5, "cy": 0.5},
    "b": {"src": "footage/test-b.mp4", "in": 0.0, "dur": 4.0, "speed": 1.0, "cx": 0.5, "cy": 0.5},
    "c": {"src": "footage/test-c.mp4", "in": 0.0, "dur": 4.0, "speed": 1.0, "cx": 0.5, "cy": 0.5},
}

DEMO = ("Every film starts with one sentence. Then the cuts follow the voice, "
        "and the voice follows the music.")


def test_media():
    """Three abstract clips, so the pipeline can be tried before any real footage exists."""
    os.makedirs(os.path.join(HERE, "footage"), exist_ok=True)
    srcs = {"a": "gradients=s=1920x1080:d=6:speed=0.03:c0=0x2a2a2e:c1=0xb8a88a",
            "b": "gradients=s=1920x1080:d=6:speed=0.02:type=radial:c0=0x1a1c1e:c1=0x8d8778",
            "c": "life=s=1920x1080:mold=10:r=30:ratio=0.1:death_color=#202024:life_color=#d8d4cc"}
    for k, src in srcs.items():
        out = os.path.join(HERE, "footage", f"test-{k}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", src, "-t", "6", "-r", "30",
                        "-pix_fmt", "yuv420p", out], check=True)
        print("made", out)


def load_words():
    p = os.path.join(HERE, "words.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    out, t = [], 0.4                                   # a stand-in voice: 0.28 s a word, a pause after each sentence
    for w in DEMO.split():
        out.append({"word": w, "start": round(t, 2), "end": round(t + 0.24, 2)})
        t += 0.28 + (0.45 if w.endswith(".") else 0.0)
    return out


def group_lines(words, max_chars=26, gap=0.35):
    """Words into blocks of one or two lines. A block ends at a sentence end or a real pause, or at a comma once it is
    a line long, never mid phrase. Two lines are balanced, so no word is left alone on the second line."""
    blocks, cur = [], []
    for i, w in enumerate(words):
        if cur and len(" ".join(x["word"] for x in cur + [w])) > 2 * max_chars:
            blocks.append(cur)
            cur = []
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text = " ".join(x["word"] for x in cur)
        if (nxt is None or w["word"][-1] in ".!?" or nxt["start"] - w["end"] > gap
                or (w["word"][-1] in ",;:" and len(text) >= max_chars)):
            blocks.append(cur)
            cur = []
    out = []
    for blk in blocks:
        if len(" ".join(x["word"] for x in blk)) <= max_chars or len(blk) < 2:
            out.append([blk])
            continue
        k = min(range(1, len(blk)), key=lambda j: abs(len(" ".join(x["word"] for x in blk[:j]))
                                                      - len(" ".join(x["word"] for x in blk[j:]))))
        out.append([blk[:k], blk[k:]])
    return out


def snap(t):
    """The nearest beat."""
    beat = 60.0 / BPM
    return round(PHASE + round((t - PHASE) / beat) * beat, 3)


def build():
    words = load_words()
    blocks = group_lines(words)
    duration = round(words[-1]["end"] + 2.0, 2)        # the voice lands, then the picture holds and the music fades

    # Text: one face, one size, words fading in as they are said, centred, nothing moving around.
    big = []
    for k, lines in enumerate(blocks):
        t0 = lines[0][0]["start"] - 0.08
        nxt = blocks[k + 1][0][0]["start"] if k + 1 < len(blocks) else duration
        t1 = min(nxt - 0.02, lines[-1][-1]["end"] + 1.2)
        big.append({"style": "words", "role": "sans", "px": 58, "x": 0.5, "y": 0.5, "leading": 1.25,
                    "fade": 0.26, "blur": 0.07, "lead": 0.06, "out": 0.14, "shadow": 0.5,
                    "lines": [[[w["word"], w["start"]] for w in ln] for ln in lines],
                    "t0": round(t0, 3), "t1": round(t1, 3)})

    # Picture: a cut at the start of each block, snapped to the beat; takes rotate; each shot pushes in slowly.
    cut_times = sorted({0.0} | {snap(b["t0"]) for b in big if b["t0"] > 0.5})
    names = list(TAKES)
    shots = []
    for i, t0 in enumerate(cut_times):
        t1 = cut_times[i + 1] if i + 1 < len(cut_times) else duration
        shots.append({"t0": t0, "t1": t1, "take": f"takes/{names[i % len(names)]}.mp4",
                      "z": [1.0, 1.06], "dip": 0.3})          # dip darkens the shot under the words

    tl = {
        "title": "example cut film", "size": list(SIZE), "fps": FPS, "duration": duration, "seed": 4,
        "brand": os.path.relpath(os.path.join(HERE, "..", "..", "..", "..", "..", "brand", "brand.example.json"), HERE),
        "shots": shots, "cuts": [], "captions": [], "big": big, "graphics": [],
        "finish": {"grain": 3.0, "scan": 0.0, "vignette": 0.25, "chroma": 0},
    }
    vo, music = os.path.join(HERE, "vo.wav"), os.path.join(HERE, "music.mp3")
    if os.path.exists(vo) and os.path.exists(music):
        tl["audio"] = {"vo": "vo.wav", "vo_at": 0.0, "music": "music.mp3", "music_in": 0.0, "music_db": -11.0,
                       "duck_db": 5.0, "fade_out": 2.0, "lufs": -14.0, "tp": -1.5}

    takes = {k: dict(v, src=os.path.join(HERE, v["src"])) for k, v in TAKES.items()}
    conform_takes(takes, os.path.join(HERE, "takes"), SIZE[0], SIZE[1], FPS)
    json.dump(tl, open(os.path.join(HERE, "timeline.json"), "w", encoding="utf-8"), indent=1)
    print(f"timeline.json: {len(shots)} shots, {len(big)} text blocks, {duration} s")


if __name__ == "__main__":
    if "--test-media" in sys.argv:
        test_media()
    build()
