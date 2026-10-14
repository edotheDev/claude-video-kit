# Setup

About 15 minutes. Commands are for Windows (PowerShell) with macOS in brackets where it differs.

## 1. Claude Code

Install Claude Code and sign in: https://docs.claude.com/en/docs/claude-code

## 2. The repo

```
git clone https://github.com/edotheDev/claude-video-kit.git
cd claude-video-kit
```

## 3. ffmpeg

```
winget install Gyan.FFmpeg          # macOS: brew install ffmpeg
```

Use a full build: captions need libass, and the voice speed step likes the rubberband filter.

## 4. Python 3.11 or 3.12

```
py -3.12 -m venv .venv              # macOS: python3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     # macOS: .venv/bin/python -m pip install -r requirements.txt
```

## 5. Node 20 or newer, for captions

```
winget install OpenJS.NodeJS.LTS    # macOS: brew install node
cd .claude/skills/captions/scripts
npm install
cd ../../../..
```

The first caption run downloads a Whisper model (about 250 MB) into `.claude/skills/captions/scripts/models/`.

## 6. Optional tools

- **yt-dlp**, to fetch sections of online video: `winget install yt-dlp` (macOS: `brew install yt-dlp`).
- **Demucs**, to check a song for vocals and split stems. It pulls in torch, so give it its own environment:
  `py -3.12 -m venv .venv-demucs` then `.venv-demucs\Scripts\python -m pip install demucs`.
- **Fonts and logo**: see `brand/README.md`.

## 7. Check

```
.venv\Scripts\python tools\check_setup.py
```

Everything marked `MISSING` must be fixed; `optional` lines are only needed for the tools they name.

## 8. Connectors (optional, as you need them)

The kit works offline for editing, captions and rendering. Some jobs go faster with connected services. Add them in claude.ai under Settings, Connectors (they then appear in Claude Code when you sign in with the same account), or with `claude mcp add`:

- **Apify**: scraping and downloading footage when direct downloads are blocked (official uploads, reels, stock portfolios). Paid per run: set a spending cap.
- **ElevenLabs**: generated voiceover, sound effects, and transcription with word timings for many languages.
- **An image and video generation service** (for example Higgsfield): the occasional generated insert, against a budget.
- **Claude in Chrome**: page captures and downloads that need a real browser. Use a separate browser profile for automation.

Keys stay in the connector settings or a local `.env`, never in the repo (`.env` and `.mcp.json` are ignored by git).

## 9. Try it

Open the folder in Claude Code (`claude`) and ask, for example:

- "Caption `C:\path\to\clip.mp4`."
- "Run the brand-motion cut film example and show me the stills."
- "Read the film-taste skill, then review this cut: `C:\path\to\draft.mp4`."
