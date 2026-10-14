---
name: ui-motion
description: Make product UI motion (SaaS launch films, feature reels, app demos, animated interface moments) as HTML scenes driven by time and captured frame by frame in headless Chrome, then cut with music and sound like any film. Use for "UI animation", "product demo animation", "SaaS motion", "animate the interface", "feature video", or when a screen recording would look too raw.
---

# UI motion

The interface is rebuilt cleanly in HTML and CSS (or WebGL for a background), every element's state is a pure function of time, and `scripts/capture.mjs` renders it frame by frame. Nothing runs on a wall clock, so a still at 3.2 s and frame 96 of the master are the same picture, every time.

Read `film-taste` and `motion-craft` first. UI motion is where the template look is most common, so the tells there are the checklist here.

## Setup

```
cd .claude/skills/ui-motion/scripts && npm install
```

Needs Google Chrome installed (or `CHROME_PATH` pointing at Chrome or Chromium) and ffmpeg.

## The loop

1. **Copy the template.** `template/` holds `scene.html` (layout and styles), `scene.js` (`render(t)`, the whole animation) and `motion.js` (the named eases, `k()` for eased progress, word by word text). Copy it to `projects/<slug>/scene/`.
2. **Real UI, rebuilt clean.** Recreate the product's actual screens and labels, not an invented dashboard. Use the brand's real fonts (copy them into `scene/fonts/` and declare them with `@font-face`) and its real icons and logo files.
3. **Write `render(t)`.** Every element's opacity, position, scale and text for second `t`. No CSS transitions, no `setTimeout`, no `requestAnimationFrame` loops. Set `window.DURATION`.
4. **Stills first:** `node capture.mjs stills <scene-dir> <out-dir> 0.5,1.2,3.0,5.5`. Read them like a contact sheet: composition, safe areas, the midpoint of each move.
5. **Video:** `node capture.mjs video <scene-dir> picture.mp4 --fps 30` (`--fps 60` for very smooth UI, `--from/--to` to render in chunks and join them with the concat demuxer).
6. **Sound and mix:** voice, music and a few UI sounds, mixed with the `music-and-mix` rules, muxed onto the picture with ffmpeg.
7. **QA:** `video-qa` checks plus a smoothness check (below).

## Taste for UI motion

- **One continuous camera.** The camera moves on eased curves and only stops at a turn on the beat. A stop start camera reads as lag.
- **Precise arrival, then stillness.** Cards and panels land with Settle, presses hit with Punch and recover in a frame or two. No springs, no elastic.
- **Something always moves a little,** but one thing leads at a time. A frame where nothing at all moves for more than about a second reads as a frozen render; a frame where everything moves reads as a template.
- **Backgrounds:** a flat ground, or a slow field of colour that actually moves. Never the purple to blue AI gradient, never blurred blobs, never glassmorphism as decoration. Glass belongs on real controls only, if at all.
- **Type:** one family, few weights, sizes on a scale. Words on screen appear as they are said, the same calm way as in every other film.
- **Real states, real copy.** Labels, counts and messages a real product would show. Sample names and data are obviously sample, never real customers.
- **Vertical frames:** keep key content inside the middle 3:4 box (y 240 to 1680 on 1080x1920) so a 3:4 crop for a feed still works.
- **Sound design matters as much as the picture:** a calm instrumental bed, soft taps on presses and arrivals only, no whoosh on every move.

## Smoothness check

Stills cannot show a jerk. Measure motion between frames: for every frame pair, estimate shift and zoom (ORB features or phase correlation with OpenCV) and flag any sudden change of speed inside a move, or a move that ends at speed and then holds. Fix the curve in `render(t)`, not in the edit.

## Traps

- **Fonts load late.** The page sets `<body data-ready="1">` only after `document.fonts.ready`; the capture waits for it. A frame captured before that uses a fallback face.
- **GPU contention.** WebGL backgrounds render on the GPU by default; with another render running, add `--no-gpu` (software rendering, slower but steady).
- **Two animation frames after `render(t)`** before each screenshot, so layout and paint have settled. `capture.mjs` does this.
- **Subpixel shimmer.** Round large translations to whole pixels when an element is meant to be still, or it can shimmer at rest.
