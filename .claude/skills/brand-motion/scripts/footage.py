#!/usr/bin/env python3
"""Footage helpers for the motion kit: stream a clip's frames cropped to the frame, and grade them.

Kept separate from the engines so any layout can decode footage the same way. Needs ffmpeg and ffprobe.
"""
import subprocess

import numpy as np


def ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


class Clip:
    """Streams one scene's frames from a video, cropped to the plate's aspect around a chosen center."""

    def __init__(self, path, t_in, dur, fps, center, out_w, out_h):
        wh = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
        sw, sh = (int(v) for v in wh.strip().split(",")[:2])
        aspect = out_w / out_h
        cw, ch = (int(round(sh * aspect)), sh) if sw / sh > aspect else (sw, int(round(sw / aspect)))
        cx = int(np.clip(center[0] * sw - cw / 2, 0, sw - cw))
        cy = int(np.clip(center[1] * sh - ch / 2, 0, sh - ch))
        self.w, self.h = out_w, out_h
        vf = f"crop={cw}:{ch}:{cx}:{cy},scale={out_w}:{out_h}:flags=lanczos,fps={fps}"
        self.proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", str(t_in), "-i", path, "-t", str(dur + 0.5),
                                      "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.last = None

    def next(self):
        n = self.w * self.h * 3
        buf = self.proc.stdout.read(n)
        if len(buf) == n:
            self.last = np.frombuffer(buf, np.uint8).reshape(self.h, self.w, 3)
        return self.last

    def close(self):
        try:
            self.proc.stdout.close()
            self.proc.kill()
        except Exception:
            pass


def grade(img, warmth=1.0, shadow=(24, 24, 24)):
    """Soft and slightly quiet: saturation pulled back, shadows tinted toward the brand's own dark.

    `shadow` is the color the darkest areas lean into (brand.json "grade_shadow", default the
    default ground's background). `warmth` scales the final warm or cool cast.
    """
    sh_rgb = np.array(shadow, np.float32)
    x = img.astype(np.float32) / 255.0
    lum = x @ np.array([0.299, 0.587, 0.114], np.float32)
    x = lum[..., None] + (x - lum[..., None]) * 0.86     # ease the saturation
    x = x * 0.93 + 0.035                                  # lift the black point a little
    sh = ((1 - lum) ** 2 * 0.18)[..., None]
    x = x * (1 - sh) + (sh_rgb / 255.0) * sh              # shadows lean to the brand's dark
    warm = np.array([1.02, 1.0, 0.95], np.float32) ** warmth
    return np.clip(x * warm * 255.0, 0, 255)
