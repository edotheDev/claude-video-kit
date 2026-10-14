# Video kit: house rules

This repo is a set of agents and skills for editing video and making motion films in code. Read this first in every session.

## Who does what

- **Captions, cleanup cuts, screen recorded demos:** the `video-editor` agent with the `captions` skill.
- **Deciding what a film is (idea, beats, flow doc) and reviewing cuts:** the `motion-director` agent with `motion-craft` and `film-taste`.
- **Building any film or edit beyond captions:** the `motion-designer` agent with `brand-motion`, plus `story-reel`, `footage-sourcing`, `music-and-mix` and `video-qa` as the job needs.
- A quick question about a tool or a single command needs no agent. Any real edit goes to an agent with a self contained brief, and you review its stills and master before reporting.

## The baseline

Neutral and human, never template: real footage, real fonts, real logo files, words that appear as they are said, cuts on the music's beat, an instrumental bed under a voice, measured loudness, a soft landing at the end. The full list is in `.claude/skills/film-taste/SKILL.md`; load it before planning, building or reviewing anything.

## Where things go

- One folder per film: `projects/<slug>/` with `brief.md`, `footage/`, `vo/`, `music/`, `build/`, `out/`, `sources.json`. It is ignored by git.
- Brand settings: `brand/brand.json` (fonts and logos in `brand/fonts/` and `brand/logo/`, also ignored).
- Python: the repo's `.venv` (`.venv/Scripts/python` on Windows, `.venv/bin/python` on macOS and Linux). Node scripts: `.claude/skills/captions/scripts/` after `npm install`.
- Check the machine with `python tools/check_setup.py`.

## Rules

- **Evidence over claims.** Measure loudness, durations, the tail, faces under type, duplicates. Read contact sheets. Never say a video "looks good" or a track "has no vocals" without checking.
- **Provenance.** Every shot and track goes into `sources.json` with its source page and licence. Never state a source or a fact from memory.
- **Facts on screen are verified** against a source you can link. Claims are said as claims.
- **Ask before spending money.** Apify runs, voice generation, image and video generation: agree a budget, generate only what ships, report the spend.
- **Privacy.** Blur other people's names, messages, emails, ids, keys and costs in screen recordings. Never print secrets or tokens. Keys live in `.env` or the connector settings, never in the repo.
- **Never post or schedule anything.** Delivery ends at files in `projects/<slug>/out/`.
- **Revisions change only what was named.** Bump to `-v2`; never overwrite a file someone has seen.
- **Words in the chat.** Never write song lyrics anywhere, and never paste whole transcripts or speeches into the chat: they go to files, and only the line the film uses is quoted. Work with songs by time ("the drop at 48.5 s").
- **One heavy render at a time** on a machine with 8 GB of RAM or less; render masters in chunks.
- **Write down rejections.** When a person rejects something, note what and why in the project's `brief.md`, and update the relevant skill if the lesson is general.
