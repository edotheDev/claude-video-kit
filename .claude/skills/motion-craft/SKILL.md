---
name: motion-craft
description: Motion design craft for film work (product demos, teasers, launch films, reels, title cards, kinetic type). Timing and spacing, easing, the film grammar of a trailer editor, and the tells that make motion look generated. Load it before directing or critiquing any motion, and before writing a single scene. Triggers on "motion graphic", "demo video", "teaser", "storyboard", "animate", "After Effects", "easing", "timing", "beat", "cut". Not for web or CSS animation.
---

# Motion craft

You are directing motion that should read as craft, not as software defaults. The output is direction (beats, boards, words, cuts) for whoever executes it: a human motion designer, an editor, or the engines in `brand-motion`.

The five files in `references/` are the textbook canon, adapted from two MIT licensed projects (see `references/SOURCES.md`). This file is the house layer on top: where this kit agrees, where it breaks the textbook on purpose, and the film grammar the textbook does not cover.

## Timing and spacing

**Timing** is when things happen. **Spacing** is how values move between those moments (easing). They are independent decisions. Linear easing and uniform timing are the absence of a decision.

1. Ease out for entrances, ease in for exits, a custom curve for mood. Linear only for machines, countdowns and terminals.
2. Nothing moves at the same time unless that is the point. Stagger builds hierarchy: tight 20 to 50 ms, standard 50 to 100 ms, loose 100 to 200 ms.
3. Duration is weight. Fast is light, slow is heavy. If everything takes 300 ms, nothing has weight.
4. Rhythm is phrasing, not a metronome. Beats at 0, 180, 340 and 550 ms read as a spoken sentence; 0, 200, 400 and 600 read as software. If you cannot say the timing aloud naturally, it has no rhythm.
5. Check the midpoint of every transition. A transition that passes through a broken frame is a broken transition.
6. Entry direction is meaning: from below is rising, from above is authority, from the left is arrival, scale from centre is materialising, a plain fade has no physical metaphor. Exits mirror entries, or it reads as teleporting.
7. Anchor everything to the composition's centre of gravity. Elements flying in from random directions read as clip art.
8. One thing moving well beats five things moving adequately. If a motion can be removed and the film still works, remove it.

## The house default: calm, no bounce

The textbook teaches overshoot and settle everywhere ("no follow through looks like PowerPoint"). This kit's default is quieter, because visible springs are one of the fastest tells of template motion:

**A precise, fast arrival, a dead stop, a held beat of stillness, then one element that moves late.**

- Follow through exists but is micro: 1 or 2 frames, a pixel of settle at most. No visible spring, no elastic, no back out.
- Anticipation is a one frame hold or a breath of stillness before the hit, never a cartoon wind up.
- The energy the textbook gets from overshoot comes here from **timing contrast**: something very fast landing next to something very still.
- If a cut feels flat, the fix is more stillness and a later punchline, never more bounce.

Named eases to use as tokens in every board (cubic beziers):

- **Calm** `cubic-bezier(0.62, 0.01, 0.18, 1)`: default reveals.
- **Device** `cubic-bezier(0.7, 0, 0.2, 1)`: moving a graphic device, rolling a word.
- **Punch** `cubic-bezier(0.2, 0.9, 0.1, 1)`: impacts and hits.
- **Settle** `cubic-bezier(0.16, 1, 0.3, 1)`: surfaces and cards landing.

**Hype edits flip the default.** A hard, music driven edit gets impact frames on every real hit (flashes, negatives, slice cuts, stutter holds, zoom punches) and texture (grain, dust). There the impacts are the style, and a calm pass that removes them is wrong. Keep the silences dead still and stay under 3 full frame flashes a second.

## Film grammar

You are making a film, not an animated layout. Think like a trailer editor.

- **One idea per film.** If the logline needs an "and", cut something.
- **Withhold.** Tension before product, question before answer. The first seconds earn attention with a grievance, a curiosity gap or a held breath, never with a logo or the product's name.
- **The cut is the design.** Build on a beat grid. Real sounds in the picture (a stamp, a key, a shutter) are the percussion. A cut on the beat feels inevitable; off the beat it feels cheap. Design the muted version first; it must work with the sound off.
- **Stillness is a beat.** A fully held frame after an impact is the most expensive feeling move there is. Silence in the mix is the same move for the ear.
- **Escalate.** Acts get shorter, bigger or faster. Every demo sags in the middle unless something changes gear at 40 to 60 percent: pace, scale, world or altitude. Plan the gear change before anything else.
- **Human timing.** A typed line has a hesitation. Two identical actions never take identical time; the second is 20 percent slower or faster, an echo and not a copy.
- **End on a poster.** The last frame is one someone would screenshot.

## The demo video tells

Beyond `references/defaults-to-avoid.md`, these are the tells of generated product films:

- The template arc played flat: logo, feature tour, benefit list, logo. A structure is not a story.
- Typewriter text at a constant rate, or the same fade in on every line.
- Gradient blob backgrounds, particle trails, lens flares, glow hits, light streaks as punctuation.
- A whoosh on every transition. If every cut whooshes, no cut lands.
- UI mockups floating and rotating in 3D space.
- A fake cursor gliding in perfect curves and clicking things.
- Sixty seconds of centred symmetry.
- "Uplifting corporate tech" music with a riser into every section.
- Feature words on screen ("AI powered", "seamless", "all in one") instead of one real thing happening.

If a beat could sit in any launch video with the logo swapped, it is not a beat for this film. Replace it with something only this subject can show: the real product doing the real thing, a real artifact, a held frame, one late punchline.

## When to load which reference

- An animation principle debate: `references/12-principles.md` (its follow through sections are overridden by the calm default above).
- Choosing or critiquing easing: `references/easing-taxonomy.md`.
- Duration, stagger, pacing: `references/timing-and-spacing.md`.
- Framing, entries, negative space over time: `references/composition-for-motion.md`.
- Output feels robotic or default: `references/defaults-to-avoid.md`.

## The critique loop

When reviewing a board, an animatic or a cut:

1. Say what each element is doing and why.
2. Name what reads as a default or a tell. Be direct.
3. Give a specific alternative with values: frames, pixels, ease names.

"The easing feels off" is useless. "The stamp settles with 6 frames of spring; kill the spring, hold 8 frames still, then bring the second line in late on Calm" is the job.

Voice: direct, designer to designer. Name the problem, give the fix. No hedging.
