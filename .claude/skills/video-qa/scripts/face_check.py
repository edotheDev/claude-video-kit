#!/usr/bin/env python3
"""Faces under type: find every frame where a face sits under on-screen words.

  python face_check.py video.mp4 --box 0.1,0.42,0.9,0.58 [--box ...] [--every 2] [--from S --to S] [--sheet worst.jpg]

--box is a text area as x0,y0,x1,y1, in frame fractions (0..1) or pixels. Give one per text position the film uses.
Faces come from OpenCV's YuNet detector (box plus eyes, nose and mouth corners). A frame fails when any eye, nose or
mouth landmark falls inside a text box. The fix is to reframe the footage (move the crop), never to move the words.
The detector model (about 230 KB) downloads once into ~/.cache/claude-video-kit/.
Needs: numpy, opencv-python."""
import argparse
import os
import subprocess
import urllib.request

import cv2
import numpy as np

MODEL_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")


def model_path():
    d = os.path.join(os.path.expanduser("~"), ".cache", "claude-video-kit")
    p = os.path.join(d, "face_detection_yunet_2023mar.onnx")
    if not os.path.exists(p):
        os.makedirs(d, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, p)
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--box", action="append", required=True)
    ap.add_argument("--every", type=int, default=2, help="check every Nth frame")
    ap.add_argument("--from", dest="t0", type=float, default=0.0)
    ap.add_argument("--to", dest="t1", type=float, default=None)
    ap.add_argument("--score", type=float, default=0.6)
    ap.add_argument("--sheet", default=None, help="save the worst frames with faces and boxes drawn")
    a = ap.parse_args()

    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate", "-of", "csv=p=0", a.video], capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise SystemExit("cannot read a video stream from " + a.video)
    w, h, rate = r.stdout.strip().split(",")[:3]
    W, H = int(w), int(h)
    num, den = rate.split("/")
    fps = float(num) / float(den or 1)
    boxes = []
    for b in a.box:
        v = [float(x) for x in b.split(",")]
        if max(v) <= 1.0:
            v = [v[0] * W, v[1] * H, v[2] * W, v[3] * H]
        boxes.append(v)

    det = cv2.FaceDetectorYN.create(model_path(), "", (W, H), a.score, 0.3, 5000)
    det.setInputSize((W, H))
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{a.t0:.3f}", "-i", a.video]
    if a.t1 is not None:
        cmd += ["-t", f"{a.t1 - a.t0:.3f}"]
    cmd += ["-vf", f"select='not(mod(n\\,{a.every}))'", "-vsync", "0", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = W * H * 3
    k, checked, bad, worst = 0, 0, [], []
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        t = a.t0 + k * a.every / fps
        k += 1
        checked += 1
        _, faces = det.detect(img)
        if faces is None:
            continue
        hits = 0
        for f in faces:
            pts = f[4:14].reshape(5, 2)
            for (x, y) in pts:
                if any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in boxes):
                    hits += 1
        if hits:
            bad.append(round(t, 2))
            if a.sheet and len(worst) < 8:
                im = img.copy()
                for b in boxes:
                    cv2.rectangle(im, (int(b[0]), int(b[1])), (int(b[2]), int(b[3])), (0, 200, 255), 2)
                for f in faces:
                    x, y, fw, fh = f[:4].astype(int)
                    cv2.rectangle(im, (x, y), (x + fw, y + fh), (0, 0, 255), 2)
                cv2.putText(im, f"{t:.2f}s", (12, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                worst.append(cv2.resize(im, (W // 3, H // 3)))
    p.wait()
    print(f"frames checked: {checked} (every {a.every}), frames with a face under type: {len(bad)}")
    if bad:
        spans, s0, prev = [], bad[0], bad[0]
        step = a.every / fps + 1e-3
        for t in bad[1:]:
            if t - prev > step * 1.5:
                spans.append((s0, prev))
                s0 = t
            prev = t
        spans.append((s0, prev))
        print("spans:", ", ".join(f"{x:.2f}-{y:.2f}" for x, y in spans))
    if a.sheet and worst:
        rows = [np.hstack(worst[i:i + 4] + [np.zeros_like(worst[0])] * (4 - len(worst[i:i + 4])))
                for i in range(0, len(worst), 4)]
        cv2.imwrite(a.sheet, np.vstack(rows))
        print("sheet:", a.sheet)
    if checked == 0:
        print("FAIL: no frames were read; check the path and the time window")
        raise SystemExit(1)
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
