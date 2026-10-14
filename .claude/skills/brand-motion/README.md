# Brand motion: the workflow

`SKILL.md` is the reference (engines, keys, commands). This is the loop that produces films with it, start to finish.

## 1. Copy first, never the visuals

Write the film's words before opening an engine. For a no voice film: 5 to 10 shots, one idea and at most about seven words each. For a voiced film: the script, then the voice. Read the words as a sequence; they are the film. Check every line against what the brand can actually claim. Write the audience and the platform in the brief so the next person knows what it was for.

## 2. Pick a direction from the subject's own language

Do not start from another brand's film. Look at the brand's graphic language, or the story's real material, and ask what it implies in motion. The directions are listed at the end of `SKILL.md`. One direction per film.

## 3. Write the builder or the storyboard

A voiced edit: copy `examples/cut-film/` and edit `build.py`. A no voice film: write a storyboard JSON (examples in `storyboards/`). Give a synthesised score one key and time its notes to the frames where type or a logo appears.

Try the examples with no setup beyond Python and ffmpeg:

```
cd examples/cut-film
python build.py --test-media
python ../../scripts/cutfilm.py timeline.json --times 1.6,3.5,5.0,6.8 --out stills.png

cd ../../scripts
python motion_engine.py ../storyboards/example-particle-film.json --out preview.mp4 --preview --sheet sheet.png
```

They run on the example brand and a system font. That proves the machinery; it is never enough to ship.

## 4. Look at stills before rendering anything long

A still sheet takes seconds. Fix composition here: faces under words, words near the edges, a shot that looks stock.

## 5. Preview with a contact sheet

Half size, 15 fps, identical timing. **Read the contact sheet end to end.** Almost every defect worth catching is visible in that one image.

## 6. Masters

Render the ratios that were asked for. A 4:5 cut of a portrait film usually needs its own layout, with text pulled inward.

## 7. QA by measurement

`video-qa/scripts/checks.py` for decode, durations, loudness, true peak, the tail and flashes; `face_check.py` for faces under type; then the master's contact sheet again.

## 8. Deliver, then capture the feedback

Report paths, durations, the QA table, sources and anything spent. When feedback comes back, write down what was rejected and why next to the project. The useful rules all come from rejections. When feedback says "the rest is fine", change only the flagged part and save it as `-v2`.

## Gotchas

- **H.264 rejects odd dimensions.** Round custom sizes to even numbers.
- **Grain that fills a solid shape needs pressure** (`spread` about 2e-5) or it looks hollow; a logo whose parts must stay separate needs far less (about 6e-6) and a narrower `band`.
- **Image targets use an exact distance field.** Blurred masks leave false minima that trap grain in the holes of a shape.
- **End cards keep the film's own layout grammar.** Switching to a centred lockup at the end is where films tend to look wrong.
- **Never leave grain behind a real logo.** Fade it out, then place the logo crisp.
- **Stills and loops:** `--still scene@seconds` makes a cover; a short MP4 converts to a light GIF if you drop loose grains and hold one frame.
- **Footage keeps its colour.** A light grade and grain unify mixed sources; the brand palette is for type, grounds and the end card.

## What is not in the kit

Fonts (licensing follows the foundry's terms; keep them out of git), logo files, footage and photography, and the claims the copy is allowed to make. Those come from each brand.
