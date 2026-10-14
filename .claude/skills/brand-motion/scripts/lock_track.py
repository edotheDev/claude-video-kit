#!/usr/bin/env python3
"""Follow ONE object through a take, for cutfilm's `lock` (a box that locks on a thrown or flying object).

Motion trackers that follow corners (tracker.py) lose a small fast object against the sky, so this one is
guided: you give the object's box on a few frames (keys, read off a contact sheet), and between the keys every
frame is refined by template matching in a window around the straight line from key to key. With a single key
it tracks forward on its own (constant velocity, template refreshed as it goes). Where the match is weak the
guided line is kept, so the box never jumps to something else.

    python lock_track.py take.mp4 --keys "0.0:0.41,0.52,0.47,0.60;0.8:0.70,0.20,0.74,0.26" --out take.lock.json
          [--sheet check.jpg] [--search 0.9] [--thr 0.45] [--smooth 2] [--hold]

keys: "seconds:x0,y0,x1,y1" in fractions of the take, separated by ";". Output: {"fps", "w", "h", "boxes":
[[x0, y0, x1, y1] | null per frame]} in take pixels; frames before the first key and after the last are null
unless --hold keeps the end boxes. --sheet draws the boxes on every third frame for a check by eye.
"""
import argparse
import json
import subprocess

import cv2
import numpy as np


def read_gray(path, width=640):
    p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=width,height,r_frame_rate", "-of", "json", path],
                                  capture_output=True, text=True).stdout)["streams"][0]
    W, H = int(p["width"]), int(p["height"])
    a, b = p["r_frame_rate"].split("/")
    fps = float(a) / float(b)
    w = min(width, W)
    h = int(round(H * w / W / 2)) * 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                          "-pix_fmt", "gray", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h, w)
    return frames, fps, W, H


def parse_keys(s):
    out = []
    for part in s.split(";"):
        part = part.strip()
        if not part:
            continue
        t, box = part.split(":")
        out.append((float(t), [float(v) for v in box.split(",")]))
    return sorted(out)


def patch(img, cx, cy, bw, bh):
    h, w = img.shape
    x0, y0 = int(round(cx - bw / 2)), int(round(cy - bh / 2))
    x1, y1 = x0 + max(4, int(round(bw))), y0 + max(4, int(round(bh)))
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        pad = max(-x0, -y0, x1 - w, y1 - h, 0) + 2
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        x0, y0, x1, y1 = x0 + pad, y0 + pad, x1 + pad, y1 + pad
    return img[y0:y1, x0:x1]


def match(img, tpl, cx, cy, radius):
    """Best match of tpl around (cx, cy) within radius px: returns (cx, cy, score)."""
    th, tw = tpl.shape
    h, w = img.shape
    x0 = int(max(0, cx - radius - tw / 2))
    y0 = int(max(0, cy - radius - th / 2))
    x1 = int(min(w, cx + radius + tw / 2))
    y1 = int(min(h, cy + radius + th / 2))
    win = img[y0:y1, x0:x1]
    if win.shape[0] < th + 1 or win.shape[1] < tw + 1 or tpl.std() < 1.0:
        return cx, cy, -1.0
    r = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
    _, mx, _, loc = cv2.minMaxLoc(r)
    return x0 + loc[0] + tw / 2, y0 + loc[1] + th / 2, float(mx)


def track(frames, fps, keys, search=0.9, thr=0.45, smooth=2, hold=False):
    n, h, w = frames.shape
    kf = [(min(n - 1, max(0, int(round(t * fps)))), b) for t, b in keys]
    # key boxes in working pixels (cx, cy, bw, bh)
    K = [(k, ((b[0] + b[2]) / 2 * w, (b[1] + b[3]) / 2 * h, (b[2] - b[0]) * w, (b[3] - b[1]) * h)) for k, b in kf]
    tpls = {k: patch(frames[k], *v) for k, v in K}
    out = [None] * n
    if len(K) == 1:
        k0, (cx, cy, bw, bh) = K[0]
        tpl = tpls[k0].astype(np.float32)
        vx = vy = 0.0
        out[k0] = (cx, cy, bw, bh)
        for k in range(k0 + 1, n):
            px, py = cx + vx, cy + vy
            mx, my, sc = match(frames[k], tpl.astype(np.uint8), px, py, max(bw, bh) * search + abs(vx) + abs(vy))
            if sc >= thr:
                vx, vy = 0.6 * vx + 0.4 * (mx - cx), 0.6 * vy + 0.4 * (my - cy)
                cx, cy = mx, my
                tpl = 0.85 * tpl + 0.15 * patch(frames[k], cx, cy, bw, bh).astype(np.float32)
            else:
                cx, cy = px, py
            out[k] = (cx, cy, bw, bh)
    else:
        for (ka, va), (kb, vb) in zip(K, K[1:]):
            for k in range(ka, kb + 1):
                u = (k - ka) / max(1, kb - ka)
                cx, cy, bw, bh = [a + (b - a) * u for a, b in zip(va, vb)]
                near = ka if u < 0.5 else kb
                tpl = tpls[near]
                sw, sh = tpl.shape[1], tpl.shape[0]
                # the template at this frame's box size
                if abs(bw - sw) > 1 or abs(bh - sh) > 1:
                    tpl = cv2.resize(tpl, (max(4, int(round(bw))), max(4, int(round(bh)))), interpolation=cv2.INTER_AREA)
                mx, my, sc = match(frames[k], tpl, cx, cy, max(bw, bh) * search * min(u, 1 - u) * 2 + 2)
                if sc >= thr and k not in (ka, kb):
                    cx, cy = mx, my
                out[k] = (cx, cy, bw, bh)
    # smooth the centres (keys stay put)
    idx = [k for k in range(n) if out[k] is not None]
    if smooth and len(idx) > 2 * smooth + 1:
        C = np.array([out[k][:2] for k in idx])
        pad = np.pad(C, ((smooth, smooth), (0, 0)), mode="edge")
        ker = np.ones(2 * smooth + 1) / (2 * smooth + 1)
        S = np.stack([np.convolve(pad[:, j], ker, mode="valid") for j in range(2)], 1)
        keyset = {k for k, _ in K}
        for i, k in enumerate(idx):
            if k not in keyset:
                out[k] = (S[i, 0], S[i, 1], out[k][2], out[k][3])
    if hold and idx:
        for k in range(0, idx[0]):
            out[k] = out[idx[0]]
        for k in range(idx[-1] + 1, n):
            out[k] = out[idx[-1]]
    return out


def track_blob(frames, fps, mode="bright", size=(0.08, 0.1), roi=None, pct=99.6, t0=0.0, t1=None, smooth=3, follow=0.25):
    """For a flame, a rocket's exhaust or a plane against open sky: each frame, the pixels past the `pct` percentile
    of brightness (or darkness) inside a window that follows the object; their weighted centre carries a box of
    `size` (fractions of the frame). roi [x0, y0, x1, y1] is where to look on the first frame; after that the window
    is `follow` of the frame around the last centre. Frames before t0 or after t1 get no box."""
    n, h, w = frames.shape
    out = [None] * n
    k0 = int(round(t0 * fps))
    k1 = n if t1 is None else min(n, int(round(t1 * fps)) + 1)
    x0, y0, x1, y1 = [int(v * s) for v, s in zip(roi or (0, 0, 1, 1), (w, h, w, h))]
    cx = cy = None
    for k in range(k0, k1):
        g = frames[k].astype(np.float32)
        if mode == "dark":
            g = 255.0 - g
        if cx is not None:
            rw, rh = int(follow * w), int(follow * h)
            x0, x1 = max(0, int(cx - rw)), min(w, int(cx + rw))
            y0, y1 = max(0, int(cy - rh)), min(h, int(cy + rh))
        win = g[y0:y1, x0:x1]
        if win.size == 0:
            continue
        thr = np.percentile(win, pct)
        m = np.clip(win - thr + 1.0, 0, None) * (win >= thr)   # >= : a saturated flame is all at the top value
        if m.sum() <= 0:
            if cx is not None:
                out[k] = (cx, cy, size[0] * w, size[1] * h)
            continue
        ys, xs = np.mgrid[y0:y1, x0:x1]
        cx, cy = float((xs * m).sum() / m.sum()), float((ys * m).sum() / m.sum())
        out[k] = (cx, cy, size[0] * w, size[1] * h)
    idx = [k for k in range(n) if out[k] is not None]
    if smooth and len(idx) > 2 * smooth + 1:
        C = np.array([out[k][:2] for k in idx])
        pad = np.pad(C, ((smooth, smooth), (0, 0)), mode="edge")
        ker = np.ones(2 * smooth + 1) / (2 * smooth + 1)
        S = np.stack([np.convolve(pad[:, j], ker, mode="valid") for j in range(2)], 1)
        for i, k in enumerate(idx):
            out[k] = (S[i, 0], S[i, 1], out[k][2], out[k][3])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("take")
    ap.add_argument("--keys", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--search", type=float, default=0.9)
    ap.add_argument("--thr", type=float, default=0.45)
    ap.add_argument("--smooth", type=int, default=2)
    ap.add_argument("--hold", action="store_true")
    a = ap.parse_args()
    frames, fps, W, H = read_gray(a.take)
    n, h, w = frames.shape
    res = track(frames, fps, parse_keys(a.keys), a.search, a.thr, a.smooth, a.hold)
    sx, sy = W / w, H / h
    boxes = [None if b is None else [round((b[0] - b[2] / 2) * sx, 1), round((b[1] - b[3] / 2) * sy, 1),
                                     round((b[0] + b[2] / 2) * sx, 1), round((b[1] + b[3] / 2) * sy, 1)] for b in res]
    json.dump({"fps": fps, "w": W, "h": H, "boxes": boxes}, open(a.out, "w"))
    if a.sheet:
        tiles = []
        for k in range(0, n, 3):
            im = cv2.cvtColor(frames[k], cv2.COLOR_GRAY2BGR)
            b = res[k]
            if b is not None:
                cv2.rectangle(im, (int(b[0] - b[2] / 2), int(b[1] - b[3] / 2)), (int(b[0] + b[2] / 2), int(b[1] + b[3] / 2)), (0, 0, 255), 2)
            cv2.putText(im, f"{k / fps:.2f}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            tiles.append(cv2.resize(im, (320, int(320 * h / w))))
        cols = 6
        rows = (len(tiles) + cols - 1) // cols
        th_, tw_ = tiles[0].shape[:2]
        sheet = np.zeros((rows * th_, cols * tw_, 3), np.uint8)
        for i, tl in enumerate(tiles):
            r, c = divmod(i, cols)
            sheet[r * th_:(r + 1) * th_, c * tw_:(c + 1) * tw_] = tl
        cv2.imwrite(a.sheet, sheet, [cv2.IMWRITE_JPEG_QUALITY, 80])
    seen = sum(1 for b in boxes if b is not None)
    print(f"{a.take}: {n} frames at {fps:.2f} fps, boxes on {seen}")


if __name__ == "__main__":
    main()
