# <TITLE>: story reel brief (<DATE>)

> Template. Copy to `projects/<slug>/brief.md`. Replace the inputs, the device ideas and the meme moment. Keep every rule. Delete this note.

You are building a narrated story reel end to end, with no checkpoints unless something blocks you. Read this whole brief and the `story-reel` and `film-taste` skills first. Report back in under 15 lines when the master is done.

## Inputs

- Voice: `vo/<file>` (<length> s, final). Word timings: `vo/words.json` (make them with `captions/scripts/words.mjs`, check them against the script).
- Script (the running words on screen follow it exactly):
  1. <line>
  2. <line>
- Verified facts and their sources (every number and claim on screen comes from here):
  - <fact> (<source link>)
- Format: <4:3 1440x1080 | 9:16 1080x1920>, 30 fps, about the voice length plus any inserted gaps. -14 LUFS, true peak under -1 dBTP.
- Font: <one face, from brand/brand.json>. Never commit the font files.

## The spine and the devices

- Spine: <which structure this film uses, and why it differs from the last one>.
- Devices from the last film that worked (do not copy them, invent fresh ones in the same spirit): <list>.
- Meme moment (optional, at most two): <where and what>.

## Rules (from the skills, repeated so nothing is missed)

- **Text:** centred, position never jumps; words rise and fade in as spoken; one highlight per line at most; big words and counts on a handful of beats; no glow, bounce, slides, glitches, flashes or shakes.
- **Headlines:** real page captures, slow push in, highlight sweeping the key phrase as it is said.
- **Photos:** only where a group of pictures tells the beat better than video; flat collage on grey, one per beat.
- **Pace:** follows the music; bursts only where the song hits; about 1.25 to 1.45 cuts a second overall, never uniform.
- **Faces:** never under type. Reframe the footage; check with `face_check.py`.
- **No duplicate footage:** every camera setup once. List source, in point and setup for every shot before rendering.
- **Footage:** real, specific to the story, no stock look, no watermarks, handles or burned captions.
- **Sound:** one instrumental song from one start point (vocal check done); footage muted unless plain natural sound; clicks only on photo landings; a 3 s ending with the song fading.

## Process

1. Check nothing else heavy is rendering. One render at a time on a low memory machine.
2. A 15 s internal test with every device in it. Check it against this brief yourself. Do not stop to wait.
3. Build the full timeline, render, QA (faces, duplicates, text overlap, the last word holding, loudness, the tail, decode), fix, done.

## Deliverables

In `out/`: `<slug>-<ratio>.mp4`, `<slug>-voice-only.mp4` (no song), covers (film ratio and 3:4 at 1080x1440, title off faces), `credits.md`, `qa.txt`.

## Report (under 15 lines)

Paths, length, shot count and cuts per second, the song and its start time, footage sources, anything spent, the QA lines, and what a person must check before posting.
