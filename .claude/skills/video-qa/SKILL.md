---
name: video-qa
description: Check a cut before anyone sees it. Contact sheets to read a whole film or a batch of clips in one image, frame accurate crops, and measured checks (decode, durations, loudness, true peak, the tail, flashes, faces under type, duplicate shots, motion smoothness). Use before delivering any video, when reviewing footage, and whenever a claim about a video would otherwise be made by eye or from memory.
---

# Video QA

A model cannot watch at full speed or listen. So it reads sheets and measures, and it never says "looks good" about something it did not check. Scripts are in `scripts/` and run with the repo's Python.

## Sheets: see a whole film at once

- `csheet.py video t0 t1 step out.jpg [cols] [tile_w]`: a contact sheet, tiles labelled with their time. One frame every 2 to 2.5 s reads a whole master; 4 to 8 a second reads a single cut.
- `framesheet.py video out.jpg x0 y0 x1 y1 cols t1,t2,...`: exact frames, cropped to a region. Use it zoomed on headlines, caption bands, logos and the edges of the frame.
- `sample_sheet.py src out.jpg t0 t1 step [cols] [cellw]`: the same from a local file or a URL, four seeks at a time.
- **Many clips at once** (picking footage, auditing a batch): download them and tile 6 frames per clip (at 8, 22, 38, 55, 72 and 90 percent of its length). It costs a fraction of stepping through a web player one screenshot at a time, and shows each clip's whole arc.

Read every sheet end to end. Almost every defect worth catching is visible in one: a stock looking shot, a face under the words, a cropped word, a logo landing on text, letterbox bars, a near empty frame, the same setup twice.

## Measured checks

`scripts/checks.py master.mp4 [--lufs -14] [--tp -1] [--write out/qa.txt]` runs:

- **decode:** the file decodes with no errors.
- **durations:** video and audio end together.
- **loudness:** integrated loudness within 1 LU of the target, true peak at or under the ceiling.
- **low end:** how far the content under 70 Hz sits below the mix.
- **tail:** no drop to digital silence while the picture still runs (the mix stopped dead).
- **flashes:** at most 3 full frame light jumps in any second.

`scripts/face_check.py video --box x0,y0,x1,y1 [--box ...]`: every frame where an eye, nose or mouth sits inside a text area. Give one box per text position. The fix is reframing the footage, not moving the words.

## Checks done by reading

- **Duplicates.** List every shot as source, in point and camera setup. No setup appears twice unless it is a scripted callback, and the report says so.
- **Stock look.** Posed people, generic office b roll, lit studio portraits: flag them by time.
- **Text.** One face per position, one size per screen, no orphan words, nothing in the platform's UI zones, every number matching its source.
- **Smoothness.** Stop start camera moves and sudden speed changes inside a shot show only in playback or in numbers: compare frame to frame motion (an ORB or phase correlation estimate of zoom and shift per frame) and flag jumps. Keep camera moves continuous; let velocity reach zero only on a beat.
- **Privacy.** In screen recordings: names, faces, messages, emails, ids, keys, costs. One sheet with every blur visible ships with the master.
- **Third party marks.** Watermarks, handles, burned captions, laurels and logos in the FINAL frames.

## The report

`qa.txt` holds the `checks.py` table, the face check line with its real frame count (a pass that checked 0 frames is not a pass), the duplicate list, and anything a person must check before posting. Report numbers, not adjectives.
