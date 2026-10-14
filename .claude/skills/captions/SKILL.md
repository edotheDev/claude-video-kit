---
name: captions
description: Burn clean, readable captions into recorded video (talking head, vlogs, interviews, demos) in any language, including speech that switches languages mid sentence. Measured pauses, local Whisper word timings, phrase aware line breaks, per script fonts, libass shaping. Use for "add subtitles", "caption this", "burn captions", "transcribe and caption", or fixing broken caption text.
---

# Captions

Scripts are in `scripts/` (Node 20+). Install once: `cd .claude/skills/captions/scripts && npm install`.
Every step writes JSON into a work folder, so any stage can be redone alone.

```
node prep.mjs <video> work                         -> work/meta.json, audio.wav, pauses.json, runs.json
node words.mjs <video> work/words.json             -> word timings from local Whisper (English: .en models)
node cues.mjs work/words.json work                 -> work/chunks.json (caption cues)
node ass.mjs work/chunks.json work/meta.json work/captions.ass [--font "Name"]
cd work && ffmpeg -i <video> -vf "ass=captions.ass:shaping=complex" -c:v libx264 -crf 18 -preset medium \
           -pix_fmt yuv420p -c:a copy -movflags +faststart <out>-captioned.mp4
```

- `prep.mjs` measures the noise floor and sets the silence gate from it. Quiet recordings sit near -40 dB on average, so a fixed -30 dB gate finds no pauses at all. Never hard code the gate.
- `words.mjs` downloads the model once into `scripts/models/` (`--model small` is the default and the one to trust for on screen words; `base` is faster). It runs on the CPU and uploads nothing.
- `cues.mjs` scores every possible break and picks the cheapest set for the whole video: sentence ends are free, a break after "the" or "to" is expensive, a phrasal verb ("lock in") is never split, a real pause always ends a cue, and a cue is charged for reading faster than about 21 characters a second. `--max-chars 42` suits vertical video; use 60 for 16:9. `--drop-fillers` removes standalone um and uh.
- `ass.mjs` wraps to the safe width, centres the block at 64.5 percent of the height, and prints `OVERFLOWING_LINES`, which must be 0.

## When Whisper is the wrong tool

Whisper models are single language by design. Speech that switches language inside a sentence comes back as phonetic mush in one of the two scripts, and some languages (Burmese among them) were barely in its training data. For those:

1. Get an anchored transcript from a model that handles the mix (Gemini does this well), in this shape, with an anchor every 10 to 20 seconds:
   ```
   [00:00] first sentence as spoken ...
   [00:15] next sentence ...
   ```
2. `node parse-anchored.mjs transcript.md work` lays the lines out inside each anchor window by how long they take to say and snaps every boundary to a measured pause. It prints an `integrity` block: `lossless` must be `true`, or something ate text.
3. `node ass.mjs ... --font "Latin Face" --font2 "Script Face" --script2 myanmar` (or `cjk`) gives each script its own font, run by run.

ElevenLabs Scribe (through its connector) also returns word timings for many languages and is a good second opinion on timing; keep the text from whichever source you can vouch for.

## The caption law

- **Position:** the block's centre at about 64.5 percent of the height. Higher sits on the chin; lower runs into the platform's own UI.
- **Size:** about 4.2 percent of the frame height (54 px on 1280, 81 on 1920). 3.6 percent was too small to read on a phone.
- **Two lines at most,** broken on meaning. A break like "I'm / feeling stuck in one" is a failure even when it fits.
- **White, thin dark outline (about 0.28 percent of the height, at least 3 px), no box.** A box reads as an amateur burn in; an outline holds over a patterned shirt where a shadow alone does not.
- **One font per script, never one font for both.** Set the size explicitly on every run, because an ASS override persists for the rest of the line. Latin often needs to be set a touch smaller (about 0.9) to look the same size as another script.
- **No karaoke word highlighting, no coloured keywords.** Both are platform defaults and read as generated.
- **Fix names in `work/corrections.json`,** never by hand editing later. Every edit carries a reason.
- **Skip what you cannot vouch for.** A missing word is better than a wrong one.

## Traps that have already cost time

- **Use the `ass` filter with `shaping=complex`, never the `subtitles` filter or `drawtext`.** `subtitles=` silently uses simple shaping, which does not reorder complex scripts: in Burmese a vowel sign stored after its consonant must render before it, and without reordering it comes out wrong while still looking plausible. `drawtext` does no complex shaping at all.
- **Run ffmpeg from inside the work folder with a relative `.ass` name.** A Windows absolute path puts a colon into the filter graph and it will not parse.
- **Check shaping zoomed.** Crop the caption band and scale it 2x before judging; misordered marks are invisible at full frame size.
- **Write scripts with a file editor, never through a shell heredoc.** Backslashes get eaten: `/\s+/` silently became `/s+/`, which split on the letter s and deleted it from every word.
- **Keep the source resolution and frame rate.** Never upscale.
- **Transcripts stay in files.** Do not print a whole transcript into the chat; quote only the line you need.
