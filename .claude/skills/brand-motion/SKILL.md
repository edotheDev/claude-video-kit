---
name: brand-motion
description: Render finished films in code, locally, with Python and ffmpeg: voice led fast cuts on real footage (cutfilm), spec sheet launch films (specfilm), music cut montages (montage), particle films (motion_engine), full bleed footage films (fullbleed) and letter field films (fieldfilm). Real fonts and logo files from a brand.json, word timed text, beat grids, tracking overlays, a ducked and loudness normalised mix. Use it to build any motion film or edit, cover stills and motion tests.
---

# Brand motion

Local engines turn a timeline or storyboard JSON into a finished MP4: picture, type, logo and sound. Type is set in real font files and logos come from real logo files, so a film never contains AI drawn lettering or an invented logo. Everything brand specific lives in `brand.json`.

The working pattern for an edit: a small **builder** script per film (start from `examples/cut-film/build.py`) owns the decisions (takes, cut points on the beat, word blocks from the voice's timings) and writes the timeline; an engine renders it. A revision is a small diff to the builder.

## The engines (`scripts/`)

- **`cutfilm.py`**: the voice led fast cut, the main editing engine. Every shot plays a TAKE that `conform_takes` has already cut, cropped, speed changed and conformed, so the render only reads forward. Word timed text, big type, transitions, footage effects, graphics, tracking locks, a corner HUD, grade and finish, and the mix.
- **`specfilm.py`**: extends cutfilm with the spec sheet grammar (a calm voice over a product spec sheet): mono type that types and deletes, boxes that re-flow, counters, a leaderboard, info cards, a letter field. Same timeline and CLI.
- **`montage.py`**: full bleed footage in its own colour, cut on a real track's beat grid, a sentence building line by line, an end card where letters settle into the wordmark. The score is the track itself.
- **`motion_engine.py`**: particle films. Grain behaves like sand on a vibrating plate: every cut starts as noise and resolves into a pattern, a picture or the logo. Also holds the brand loader and a synthesised score.
- **`fullbleed.py`** + **`footage.py`**: footage films with focus pulls, floating lines, callouts, and an ending where the picture turns to grain.
- **`fieldfilm.py`**: letter field films. The brand's letter field draws one figure per shot (a giant letter, the logo, an empty page of dots), settling from static or refining on the beat. With **`tracker.py`** + **`overlay.py`**: real footage with a machine vision overlay that follows real motion (Lucas Kanade with a forward backward check). **`dotflow.py`**: thousands of dots on a curl noise flow that land on a figure's cells.
- **Tools:** `typeset.py` sets type with HarfBuzz (kerning, tracking, variable axes; PIL alone ignores GPOS kerning, which is the only kerning most modern fonts have). `lettering.py` draws a picture as a grid of letters. `lock_track.py` tracks one object through a shot from a few keyed boxes. `fetch_segments.py` pulls only the seconds a film needs from direct file links.

## Setup

- ffmpeg and ffprobe on PATH; the repo's Python with `requirements.txt` (see SETUP.md at the repo root).
- A `brand.json` (below). Without real fonts the engines fall back to a system font: enough to test the machinery, never enough to ship.

## brand.json

Lookup order: `--brand path` (or a timeline's `"brand"` key), the `MOTION_BRAND` environment variable, beside the storyboard, one level up, the scripts folder, then the nearest `brand/brand.json` walking up from the storyboard (so the repo's `brand/brand.json` is found from any project). Paths inside are relative to the file. Every key is optional. Start from `brand/brand.example.json`.

- `fonts`: a path per role: `serif`, `serif_italic`, `sans`, `sans_small`, `mono`. A missing role falls back to a related one, then to a system font. Storyboards name roles, never files.
- `font_axes`: variable font axes per role, `{"sans": {"wght": 500}}` or a named instance. Ship the foundry's unmodified variable font.
- `logo`, `logomark`: PNGs with transparency, rasterised from the brand's own SVG, never redrawn.
- `grounds`: named palettes, each `bg` (the plate), `ink`, `hi`, `text`. cutfilm needs grounds named `ink` and `paper`.
- `default_ground`, `colors` (named colours a storyboard can use), `overlay` (type colours over footage), `grade_shadow`, `grade`, `render` (engine tuning), `letter_field` (`alphabet`, `font`, `axes`, `cell`, `cell_ratio`, `glyph`, `dot_below`, `dot_radius`, `weights`).

A brand needs at least one dark and one light ground and one accent used sparingly. Name grounds after the brand's own colours so scenes read like the brand.

## cutfilm timeline

All times in film seconds.

- **Top level:** `size [W, H]`, `fps`, `duration`, `brand`, `seed`, `shots`, `cuts`, `captions`, `big`, `graphics`, `hud`, `finish`, `audio`.
- **Takes** (`conform_takes(takes, out_dir, W, H, fps)` in the builder): `{src, in, dur, speed, cx, cy, m, box, size, dar, deint, hflip, reverse}`. A `.take` sidecar re-conforms a take only when its settings change. `box: "auto"` crops a film scan inside its gate (or pass `[x0, y0, x1, y1]` when a dark shot fools it). Non square pixels (DVD and PAL archive transfers) are squared from the stream's sample aspect ratio; `dar` forces it for untagged files; `deint` deinterlaces. Before mirroring a shot with `hflip`, check it for letters and numbers that would read backwards.
- **Shots:** `{t0, t1, take | gen, z: [z0, z1], pan, fx, grade: {con, sat, warm, lift}, dip, step, loop, freeze, track, lock, inset}`. `gen` is a plain ground (`ink`, `paper`, `field`). `dip` darkens a bright or busy shot under words. A shot's `k0` counts render frames, so a half size 15 fps preview reads twice as far into a take: a black preview frame can still be a correct master.
- **Text, the calm way (`big` with `style: "words"`):** `lines [[[word, t], ...], ...]` in one face and one size, centred on `x`/`y`, laid out once so nothing reflows; each word fades up out of a slight `blur` over `fade` s from `lead` s before it is said; the block leaves with a short `out`. Keys: `role`, `axes`, `px`, `leading`, `shadow`, `rise`, `color`, `align`. A word entry `[word, t, "s"]` switches that word to the `highlight` face at the same size (`{role, axes, scale}`). This is the default for spoken lines.
- **Other big type styles:** `fill`, `knockout` (footage inside the letters), `typewriter` (with a cursor, `chars` keyed), `ghost`, `echo`, `highlight`, and `runs` for mixed faces on one baseline. Use them sparingly; most films need only `words`.
- **Captions** (word by word with per word highlight boxes): `{words: [[text, t_in, t_out, flags]], t_out, y, size, max_w, color}`. For talking head captions use the `captions` skill instead.
- **Cuts:** `{t, type, dur}` with `glitch`, `flash`, `whip`, `zoom`, `luma`, `fieldx`, `dissolve`. Hard cuts need no entry. Keep transitions rare and meaningful.
- **Shot fx**, applied in order: `pointcloud`, `edges` (keys `mix` and `dim`), `trail`, `slitscan`, `rgb`, `smear`, `flow`, `dither`, `ascii` (a letter mosaic); specfilm adds `ascii2` (footage drawn as the letter field) and `blur` (a feathered box, for a stray label or a private detail).
- **Graphics:** `label`, `counter` (`t_roll`: shown from `t0`, counting from `t_roll` to `t_end`), `graph`, `wave` (the voice's waveform), `swatches`, `network`, `ascii_logo`, `hud_label`, `yearclock` (a year that holds and rolls to the next), `scaleclock` (the size of what is on screen on a log scale: 120 µm, 3.5 mm, 2,300 km), `spiral` and `hexgrid` (a golden spiral or a hexagonal lattice laid onto the real shape in the picture). Key them on exact frame sheets.
- **One object through a chain of cuts:** a shot's `lock` `{path | keys, label, id, delay, snap, from_prev, trail, pad, min, coords, px, color}` draws corner brackets that snap on after the cut and follow the object, with a typed id and label; `from_prev` slides the box over from where the previous shot's lock let go. Paths come from `lock_track.py --keys "s:x0,y0,x1,y1;..."` (template matching between keys; `--sheet` draws the result for a check by eye).
- **Vertical from archive:** a 9:16 crop of 4:3 SD film is a fourfold upscale. A shot's `inset` `{y, x, w, field, swap, holes, feather}` plays the take sharp in a window and continues the picture above and below as the letter field. HD footage stays full bleed.
- **hud:** `{text, context: [[t, value]], margin}`, a corner tag with a meter. **finish:** `{grain, scan, vignette, chroma}`.
- **audio:** `{vo, vo_at, vo_db, music, music_in, music_at, music_db, duck_db, fade_out, lufs, tp, sfx: [{file, at, gain, trim, dur, fade, fade_in}]}`. Voice plus a music and effects bus, a sidechain duck (the voice is padded to the film length, so the music never stops dead), and a two pass loudnorm. `music_at` starts the track at a film time, so its drop can land on a word while the film opens on the voice alone.
- **specfilm graphics:** `type` (`ops [[t, text, cps?, dps?]]`, block or bar cursor, `hi` and `dim` tokens, `label`, `plate`), `frames`, `squares`, `board` (a leaderboard of segmented bars), `ibeam`, `card`, `glyphs`, `corners`, `grid`, `lines`, `logo`, `field` (the letter field drawing a glyph or the logo, with a `hole` where words sit). Any graphic takes `cam` (a slow drift) and `flat` (no shadow, for paper grounds).

**Re-timing to a new voice take:** map every old time through word i to word i, then snap the cuts back to the beat.

## Commands

Run with the repo's Python from `scripts/` or with full paths.

- A still sheet of chosen moments, in seconds: `python cutfilm.py timeline.json --times 1.2,4.5,8.0 --out stills.png`
- A half size, 15 fps preview with a contact sheet: `python cutfilm.py timeline.json --preview --out preview.mp4 --sheet sheet.png`
- The master: `python cutfilm.py timeline.json --out master.mp4`. On a low memory machine render in chunks split at shot starts (`--from 0 --to 20 --out part1.mp4`, ...; a windowed render has no sound), join them with the concat demuxer and a stream copy, build the mix with `--mix-only --out master.mp4` (writes `master_mix.wav`), and mux it on. One heavy render at a time; `--lean` encodes with fewer threads.
- Particle film: `python motion_engine.py board.json --out stills.png --stills --preview`, then `--preview --sheet p.png`, then the master. `--still scene@2.2` for a cover, `--only a,b` for some scenes, `--remux` for a new score on a finished picture, `--size 1080x1350` for 4:5.
- Footage, montage and field films: the same flags (`fullbleed.py`, `montage.py`, `fieldfilm.py`); montage and fieldfilm also take `--still T`, `--from/--to`, `--lean` and `--scan-letters`, and fieldfilm `--times a,b,c`.

## Storyboards for the other engines

- **Particle scenes** (`motion_engine.py`): top level `title`, `notes`, `pulse` (seconds a beat), `particles_per_px`, `band`, `tail`, `seed`, `score`. Each scene: `id`, `pulses` or `seconds`, `ground`, `pattern`, `settle`, `pull`, `spread`, `shimmer`, `reset`, `dissolve_last`, `still`, `texts` `{text, font, size, at, x, y, anchor, gap, color}`, `logo` `{width, color, y, x, at, src}`, `dot`. Score keys per scene: `chord`, `bass`, `melody` `[[note, seconds, velocity?]]`, `tone`, `gain`, `bass_vel`, `grain_gain`.
- **Patterns:** waves `{"kind":"waves","count":3,"k":5,"drift":0.15}`, a Chladni rectangle `{"kind":"rect","n":1,"m":3}`, a grid `{"kind":"grid","n":2.2,"m":2.2,"level":0.15}`, a rosette `{"kind":"polar","k":20,"n":18,"stagger":true,"hollow":0.3,"squash":1.3}`, a pinwheel `{"kind":"spiral","n":2,"b":1.6,"k":4}`, a cell lattice `{"kind":"voiceprint","k":10,"n":6,"mix":0.45}`, tightening stripes `{"kind":"chirp","k":12,"g":1.1}`, or grain gathering into a picture `{"kind":"image","src":"...png","mask_src":"...png","size":0.5,"cy":-0.12,"reach":300}`. All take `spin`, `rot`, `zoom`, `breathe`. A filled shape needs `spread` about 2e-5; a logo whose parts must stay separate about 6e-6.
- **Footage scenes** (`fullbleed.py`): `clip`, `in`, `center`, `focus`, `warmth`, `time`, `callout` `{to, label_at, label, tone, at, label_anchor}`; grain scenes turn the previous shot's light into grain (`grain_fade`, `calm`).
- **Montage** (`montage.py`): `fps`, `seed`, `film_grain`, `grade` `{saturation, contrast, lift, gamma, warmth}` (keep it light); `shots` `{id, seconds, src, in, center, crop, zoom [z0, z1], drift, speed, flip, exposure, saturation, contrast, gamma, warmth}`; `lines` `{mode: ink | rise | decode | letters, x, y, size, leading, tracking, stagger, fade, shadow, scrim, items: [{text, at}], until}` (ink, words inking in by opacity only, tested cleanest); `end` (the letters settling into the wordmark); `music` `{src, target_lufs, ceiling, end_fade, segments: [{from, to, at, fade_in, fade_out, gain_db, tail, tail_gain, tail_rt60}]}`; `letter_scatter`, `letter_cuts`.
- **Field films** (`fieldfilm.py`): `size`, `fps`, `seed`, `film_grain`, `field`, `static`, `page`, `bloom`, `shots` `{seconds, ground, mode: resolve | refine | hold | footage | empty, cell, settle, order, figure {kind: letter | logo | none, char, font, axes, height, width, x, y, rot, spin, drift, zoom, *_keys}, steps, logo}`, `lines`, `music`, `dots`.

**A random letter field can spell words.** The blocked word filter catches profanity, slurs and look alike spellings; run `--scan-letters` before a master (filtered must be 0).

## The synthesised score

motion_engine's `resonance` score is written against the timeline: hiss resolves into each scene's chord on every cut, a piano note lands on each entrance in `melody`, a grain layer follows how agitated the particles are, one reverb holds it, mastered to -14 LUFS. Give a film one key. For brand films, real composed music cut to its beat usually beats the synth score; see `music-and-mix`.

## Fitting a brand

1. Read the brand's guidelines and voice first: palette, type roles, logo files, motion principles.
2. Fill `brand.json` from them.
3. Derive a direction from the brand's own graphic language. Never rebuild another brand's signature device; from a reference film take the rhythm (cut grid, build, drop, where words and logo land), not its key image.
4. Never invent a visual claim. If a pattern stands for something, the brand must actually claim it.
5. Keep licensed fonts out of version control.

## QA

- Video and audio durations match; -14 LUFS within 1, true peak at or under -1 dBTP; nothing loud under 70 Hz (`video-qa/scripts/checks.py`).
- Type legible at phone size, inside the safe area, clear of platform UI. One face per position, one size per screen.
- No generated text, no redrawn logo. Every shape resolves; nothing stays hazy.
- Every downloaded source decodes clean.
- Read the contact sheet end to end before calling it done.

## Specs

- 9:16 is 1080x1920, 4:5 is 1080x1350, 1:1 is 1080x1080, 4:3 is 1440x1080; 30 fps; H.264 CRF 18, AAC 192k. Sizes are forced even.
- Previews are half size at 15 fps with identical timing. Particle physics runs at a fixed 90 steps a second, so a preview and a master match.
- A 4:5 render of a particle board is a centre crop of the 9:16 plate; anything near the top or bottom edge gets cropped, so a portrait board usually needs its own 4:5 variant.
- Name masters `NN-slug-RATIO.mp4`. Never overwrite a file someone has seen; bump to `-v2`.

## Directions

- **Voiced fast cut** (cutfilm): a voice leads; takes cut to its words and the music's beats; calm word timed text; real footage full frame.
- **Slow story** (cutfilm `words`): archive and modern footage under one calm voice or real recorded voices; words centred, fading in as said.
- **Spec sheet** (specfilm): a calm voice over typed mono, re-flowing boxes, a leaderboard, the letter field.
- **Music montage** (montage): no voice; real footage in colour cut on a track's beat; a sentence inks in; the wordmark settles at the end.
- **Letter field** (fieldfilm): the brand's letters draw one figure per shot; real footage with a tracking overlay converging into it.
- **Particle film** (motion_engine): hard cuts on a pulse, grain resolving into patterns and the logo.
- **Full bleed footage** (fullbleed): focus pulls, corner lines, callouts, a grain ending.

Mixing directions inside one film reads as indecision. Vary them across pieces instead.
