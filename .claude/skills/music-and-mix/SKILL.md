---
name: music-and-mix
description: Music, voice and the final mix for edits and films. Picking a free track by its structure, proving it has no vocals under a voiceover, measuring the beat grid to cut on, generating or recording a voice, sound effects restraint, ducking, loudness and a clean ending. Use for "find music", "cut to the beat", "the music is too loud", "voiceover", "sound effects", "mix", or "the ending cuts off".
---

# Music and mix

A model cannot listen. Every claim about sound here is measured: tempo, onsets, loudness, the tail, whether a track has vocals.

## Picking the track

- **The film picks the music.** Melodic electronic fits a lot of fast cuts, but choose what fits this film's turn and say why in the report. Never reuse a track across films in a series.
- **A commercial song is a reference, never the track.** Use it for its rhythm and its drop, then find a licensed track that feels like it: `scripts/similar.py reference.mp3@48.5:77 cand1.mp3 cand2.mp3 ...` ranks candidates by timbre, tempo, band balance and brightness against the reference's drop.
- **Free sources:** Mixkit music (`scripts/mk_list.py tag/edm tag/house ...` lists a tag page; files are at `https://assets.mixkit.co/music/<id>/<id>.mp3`). Check each item's licence on its page (the JSON-LD must say the free stock music licence). Other free libraries work the same way; record the licence in `sources.json`.
- **Read the structure before choosing:** `analyse.py` (tempo and an energy map), `bars.py track.mp3` (per bar levels on the kick grid: exact bars of builds, drops and endings), `songmap.py` (2 s loudness map), `specimg.py` (spectrogram strips), `tonal.py` (key and how melodic a section is).

## Instrumental under a voice (hard)

A sung or spoken vocal in the bed fights the narration. Never assume a track is instrumental from its title or from memory.

1. Split the exact span the film uses with Demucs (its own venv, it pulls in torch): `demucs -n htdemucs --two-stems vocals track.mp3`.
2. Measure the vocal stem against the song, in 0.1 s windows: count the windows within 20 dB of the song and 12 dB of the music, and the longest run. Synth leads bleed into the vocal stem, so short blips prove nothing; seconds of continuous vocal do.
3. Run Whisper on any flagged spot of the vocal stem. If it hears words, skip the track.
4. Write the result in `qa.txt`.

## The beat grid

- `tempo.py song t0 t1` gives tempo and beat phase from spectral flux; `kickgrid.py song t0 t1` and `kicks.py song` give kick onsets, so cuts land on the hit and not near it.
- Fit period and phase to the onsets, then snap cut points to the grid in the builder.
- Enter the song so its drop lands on the story's turn (`music_in` and `music_at` in a cutfilm timeline). If the song changes after the picture is cut, re-pick its start so the drop still lands.

## Voice

- **A real voice stays real.** Cut at the pauses, fade every join (10 to 20 ms), one static gain. A denoise, EQ, compressor and limiter chain on a real voice is what people hear first and hate. A transparent peak limiter that touches under 2 percent of the speech is the most it gets.
- **Generated voice (ElevenLabs, through its connector):** one take, chosen by measurement, not a batch of options. Long reads go one paragraph per call, joined, because long single takes loop phrases near their tails; listen to (or transcribe) every paragraph's end for repeats. Write numbers and years as words in the TTS text. For English, expression tags before phrases (tone and stress) make delivery less flat; for scripts in other languages, tags often hurt, so test one paragraph first.
- **Speed a slow voice with the pitch kept:** `ffmpeg -i vo.wav -af "rubberband=tempo=1.1:pitchq=quality" vo-fast.wav` (or `atempo=1.1`), before word timings are made.
- **A voice the person did not record** is labelled as AI voiced where the platform asks.

## Sound effects

- **Default: none.** A song carries a music driven edit; effects on every move pile up into a second rhythm that fights it.
- **Where they belong:** a soft click as a photo lands in a collage; a few airy transitions in a music only showreel, one gesture each on the big scene changes, never on details. Hype edits may hit harder, on the real transients only.
- **Never** stock cinematic hits, whooshes on every cut, risers into every section, tonal effects that clash with the song, or anything that rings.
- **Place an effect by its peak, not its start.** Many impacts peak 0.5 to 0.9 s in. Two peaks landing together can push the mix past its true peak ceiling.
- `sfx_list.py whoosh click ...` lists Mixkit effects by category. ElevenLabs sound effects work for one off sounds: short concrete prompts ("single soft UI tap, short clean click, dry"); measure each take before choosing.

## Footage sound

Muted by default. A clip keeps its own sound only when it is plain natural sound: no narrator, no beat, no music bed. News and trailers almost always carry one. `scripts/footcheck.py` measures it shot by shot from Demucs stems. Film dialogue used on purpose is stripped to its vocal stem, gated in the pauses, with room tone about 26 dB under.

## The mix

- Voice compressed lightly and peak limited, then ONE fixed gain for the whole mix. Never a dynamic loudnorm over the finished mix: it pumps the first seconds.
- Music ducked under the voice with a sidechain. **Pad the voice sidechain to the full film length** (`apad=whole_dur=`), or the ducked music stops dead when the voice ends.
- Music about 9 to 11 LU under the voice after the duck. A second language voice often needs the music lower than the first.
- Master at -14 LUFS integrated, true peak at or under -1 dBTP (leave room: AAC overshoots by about half a dB). cutfilm's `audio` block does the duck and a two pass loudnorm.
- Every sound edge gets a short fade. Scan the first 10 s for clicks.

## Endings

- The voice lands, the picture holds, the music plays on about 3 s and fades on a raised cosine, ideally on a bar line.
- Do not stop the groove dead. At the last cut, loop back into a bar with the same chords (a 12 ms crossfade on the beat) and fade under the end card.
- QA the tail: the RMS of the last 4 s in 50 ms steps must never drop to silence before the last frame (`video-qa/scripts/checks.py`).

## Deliver

The master, and a voice only version (voice and any clicks, no song) for platforms where the song is added in app, with the song's start time noted.

## Words in the chat

Never write song lyrics anywhere: not in chat, storyboards, captions or JSON. Work from the audio: name moments by time and role ("the drop at 48.5 s"). A lyrics video used as a reference gives its audio only.
