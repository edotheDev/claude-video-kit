---
name: story-reel
description: Make a narrated story reel, a short film where a voiceover tells a true story (a founder, a company, a scandal, a piece of history) over real footage, real headlines and photos, with words on screen that follow the voice and a cut paced by the music. From script and voice to the finished master, plus versions in other languages. Use for "story video", "explainer reel", "documentary style short", "narrated reel".
---

# Story reel

A voice tells one true story in 60 to 90 seconds. The picture is proof: the real people, the real places, the real headlines. The words on screen follow the voice. The cut follows the music. Nothing looks like a template.

Read `film-taste` first. This skill adds the story specific rules and the order of work.

## The order of work

1. **Script and voice first.** Nothing is cut before the voice is final, because the voice is the clock.
   - About 200 words. A cold open on the most surprising true fact. Plain spoken sentences. Every fact verified against a source you can link; a claim is said as a claim ("he says ... and the company denies that").
   - **A new spine every time.** If the last three reels went hook, "that's X", chronology, quote, closing question, use something else: a cold open at the ending then rewind, a countdown of attempts, rounds, then versus now, a myth against the receipt, second person ("imagine you start a company with your friend"), or a walkthrough of one real artifact where the viewer is asked to notice things.
   - Record the voice, or generate it (see `music-and-mix`). One take, chosen by measurement.
2. **Word timings.** `captions/scripts/words.mjs vo.wav vo/words.json`, checked word by word against the script. Snap the first word after each silence to the measured pause.
3. **A brief.** Copy `brief-template.md` into the project as `brief.md` and fill in only the inputs. Keep every rule. Name the devices of the last film you liked and ask for fresh ones; reusing every device makes every video look the same.
4. **Sourcing.** `footage-sourcing`. Every shot specific to this story.
5. **Music.** `music-and-mix`. Measure the grid; enter the song so its drop lands on the story's turn.
6. **Build.** With `motion-designer`, from `brand-motion/examples/cut-film/build.py`: cutfilm.py already renders words fading in as said, big type, counters, take conforming, the beat grid and the mix. Story specific pieces (a page with a highlight sweep, a photo collage) are small per film functions in the builder.
7. **One quick internal test** (the first 15 s with every device in it), then straight to the master. Review the preview sheet while the master renders, and send fixes in one batch.
8. **QA and deliver** (below).

## Style

**Text.** One face, centred, and its position never jumps; faces are handled by framing the footage, never by moving words.
- Running words: one weight, about 4 to 5 percent of the frame height, one soft shadow. Each word rises a few pixels out of an invisible baseline with a short blur fade as it is spoken; a finished line leaves with a 4 frame fade and a small drift. One or two lines at a time.
- One key word per line may get a highlight bar that wipes in behind it as it is said, the word flipping colour where the bar has passed. The same bar marks key phrases on headlines, so the film has one visual language.
- Big words on a handful of beats: one word or a stack of two or three, 160 to 240 px on a 1080 high frame, entering with a short blur and a 1.06 to 1.00 scale, a stack built one word per beat.
- Numbers count up to their true value in 8 to 12 frames, with a small unit word under them. A wrong number can be struck through before the real one counts.
- Banned: glow, tracking or weight animation, rotation, bounce, slides across the frame, scrambles, glitches, flashes, shakes, position jumps.

**Headlines and web pages are never static.** Capture the real page, push in slowly, keep only the headline sharp, and sweep the highlight bar across the key phrase as the voice says it.

**Photos are the exception.** Real video by default. A photo only where a group of pictures tells the beat better than video: laid flat on a plain grey ground, different sizes, landing one per beat, no borders, shadows or tilt. One such collage in a film is usually enough.

**Pace follows the music.** Short bursts of 3 to 6 shots at 0.2 to 0.35 s only where the song hits or the script lists or builds; about one cut a second between; holds of 0.8 to 2 s where the music thins or a headline is read. About 1.25 to 1.45 cuts a second overall, never uniform. Hard cuts, match cuts where the footage offers them, one dropout to black with one centred line before the twist.

**Faces never under type.** Frame the footage so faces sit above, below or beside the text band. Check with `video-qa/scripts/face_check.py`.

**Each source moment once.** Count by camera setup. A longer voice (or a longer second language version) means new clips, never re cuts. List source, in point and setup for every shot before rendering.

**Sound.** One instrumental song, continuous from one start point. The drop lands on the turn; the song can stop dead under a single film line and go silent on the dropout. Footage sound muted unless it is plain natural sound. Sound effects: only a soft click as a photo lands. At most two meme sounds or meme moves (a snap zoom, a freeze), only where earned. The voice lands, the picture holds about 3 s, the song fades on a raised cosine and the picture eases to black.

**A meme moment** (a famous film line, a reaction) may sit in a gap opened in the voice at a silence. Use the film's dialogue only: strip its score with a vocal stem split.

## Optional: an AI insert inside real footage

Some creators place themselves inside the real footage for a second or two (sitting in a real interview, reacting beside the real person). If the person asks for it:

- 1 to 3 inserts, 1 to 2 s each, only where they blend in naturally. One insert that matches beats two that look pasted on.
- Build on the real frame: send the image model a crop of the empty space only, then paste back only the new person's region; every real person stays original pixels.
- Match black level, saturation, sharpness, grain and compression softness to the neighbouring shots. The generated person must not be cleaner than the plate.
- Animate from the composited plate with a locked camera, 3 s, silent, at least 1080p. Check the first result's resolution before making more.
- Generate only what ships, inside a budget agreed up front. Turn on the platform's AI label.
- Never alter a copyrighted film clip to get past a model's refusal.

## Another language version

1. Rewrite each approved sentence for its meaning, the way a storyteller in that language would say the beat, not the English sentence shape. Drop closing lines that sound wrong in that language. Show the script to a native speaker and wait for their OK before any voice is made.
2. Voice it one paragraph per call and join; listen to every paragraph's end for loops. Write numbers as words in the TTS text; keep digits on screen.
3. Re-time the approved edit line by line to the new voice. New clips fill longer lines; never repeat shots.
4. Set the text in a face made for the script, shaped with HarfBuzz or libass (`shaping=complex`), and check every unique string zoomed at 2x.
5. The music often needs to sit lower under a second language voice; measure and adjust.

## Deliver

To `projects/<slug>/out/`: the master, a voice only version with the song's start time noted, covers in the film's ratio and in 3:4 (title clear of faces), `credits.md` (footage, music, AI inserts named as such, "cropped, retimed and colour graded, with graphics added"), and `qa.txt`.

A fix after delivery re-renders only the changed span (keyframe to keyframe) and splices it in with a stream copy. A mix only fix remuxes onto the same picture.
