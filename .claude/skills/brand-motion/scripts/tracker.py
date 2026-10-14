#!/usr/bin/env python3
"""Real motion tracking for overlay films: feature points found and followed through a clip.

OpenCV finds good corners on the first frame and follows them with pyramidal Lucas-Kanade optical
flow, frame to frame. Lost points are dropped and new ones found, so the overlay always sits on
something that is really in the picture. Every point keeps its id for life. The result is plain JSON:

    {"fps": 30, "w": 1080, "h": 1920, "frames": [[[id, x, y], ...], ...], "target": [[x, y], ...]}

"target" is the median of the points in the strongest cluster, a stable spot for a crosshair.

  python tracker.py clip.mp4 --out clip.tracks.json [--max 90] [--quality 0.02] [--min-dist 60]
                    [--scale 0.5] [--refill 0.7] [--mask x0,y0,x1,y1]
"""
import argparse
import json

import cv2
import numpy as np


def track(path, max_pts=90, quality=0.02, min_dist=60, scale=0.5, refill=0.7, mask_box=None, blur=0):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    ok, fr = cap.read()
    if not ok:
        raise SystemExit("cannot read " + path)
    H, W = fr.shape[:2]
    sw, sh = int(W * scale), int(H * scale)

    def gray(f):
        g = cv2.cvtColor(cv2.resize(f, (sw, sh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(g, (0, 0), blur) if blur > 0 else g

    mask = None
    if mask_box:
        mask = np.zeros((sh, sw), np.uint8)
        x0, y0, x1, y1 = mask_box
        mask[int(y0 * sh):int(y1 * sh), int(x0 * sw):int(x1 * sw)] = 255
    md = max(4, int(min_dist * scale))

    def find(g, existing):
        m = mask.copy() if mask is not None else np.full((sh, sw), 255, np.uint8)
        for (x, y) in existing:
            cv2.circle(m, (int(x), int(y)), md, 0, -1)
        need = max_pts - len(existing)
        if need <= 0:
            return np.zeros((0, 2), np.float32)
        p = cv2.goodFeaturesToTrack(g, maxCorners=need, qualityLevel=quality, minDistance=md, mask=m, blockSize=7)
        return np.zeros((0, 2), np.float32) if p is None else p.reshape(-1, 2).astype(np.float32)

    prev = gray(fr)
    pts = find(prev, [])
    ids = list(range(1, len(pts) + 1))
    next_id = len(pts) + 1
    frames.append([[i, float(x / scale), float(y / scale)] for i, (x, y) in zip(ids, pts)])
    lk = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = gray(fr)
        if len(pts):
            p1, st, err = cv2.calcOpticalFlowPyrLK(prev, g, pts.reshape(-1, 1, 2), None, **lk)
            # forward-backward check: a point that does not come back to itself was not really followed
            p0r, st2, _ = cv2.calcOpticalFlowPyrLK(g, prev, p1, None, **lk)
            fb = np.linalg.norm(pts.reshape(-1, 2) - p0r.reshape(-1, 2), axis=1)
            p1 = p1.reshape(-1, 2)
            good = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < 1.5) & \
                   (p1[:, 0] > 2) & (p1[:, 0] < sw - 2) & (p1[:, 1] > 2) & (p1[:, 1] < sh - 2)
            pts = p1[good]
            ids = [i for i, k in zip(ids, good) if k]
        if len(pts) < max_pts * refill:
            new = find(g, pts)
            if len(new):
                pts = np.vstack([pts, new]) if len(pts) else new
                ids += list(range(next_id, next_id + len(new)))
                next_id += len(new)
        frames.append([[i, float(x / scale), float(y / scale)] for i, (x, y) in zip(ids, pts)])
        prev = g
    cap.release()
    # a crosshair target: the median of the densest third of the points, smoothed over time
    target = []
    for fp in frames:
        if not fp:
            target.append(target[-1] if target else [W / 2, H / 2])
            continue
        P = np.array([[x, y] for _, x, y in fp])
        c = np.median(P, axis=0)
        d = np.linalg.norm(P - c, axis=1)
        core = P[d <= np.percentile(d, 34)] if len(P) > 3 else P
        target.append([float(v) for v in np.median(core, axis=0)])
    T = np.array(target)
    k = 5
    if len(T) > k:
        pad = np.pad(T, ((k, k), (0, 0)), mode="edge")
        T = np.stack([np.convolve(pad[:, j], np.ones(2 * k + 1) / (2 * k + 1), mode="valid") for j in range(2)], 1)
    return {"fps": fps, "w": W, "h": H, "frames": frames, "target": T.tolist()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max", type=int, default=90)
    ap.add_argument("--quality", type=float, default=0.02)
    ap.add_argument("--min-dist", type=float, default=60)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--refill", type=float, default=0.7)
    ap.add_argument("--blur", type=float, default=0)
    ap.add_argument("--mask", default=None, help="x0,y0,x1,y1 in fractions: find points only inside")
    a = ap.parse_args()
    box = [float(v) for v in a.mask.split(",")] if a.mask else None
    res = track(a.clip, a.max, a.quality, a.min_dist, a.scale, a.refill, box, a.blur)
    json.dump(res, open(a.out, "w"))
    n = [len(f) for f in res["frames"]]
    print(f"{a.clip}: {len(n)} frames, points min {min(n)} mean {np.mean(n):.0f} max {max(n)}")


if __name__ == "__main__":
    main()
