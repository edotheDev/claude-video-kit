# Brand

The engines read one `brand.json` for fonts, logo files and colours. Set it up once per brand (or per creator).

1. Copy `brand.example.json` to `brand.json`.
2. Put font files in `brand/fonts/` and logo PNGs (transparent, rasterised from the real SVG) in `brand/logo/`. Both folders are ignored by git, because font licences follow the foundry's terms.
3. Fill in the paths, the grounds (one dark, one light, one accent used sparingly) and the variable font axes.
4. Run `python tools/check_setup.py` to confirm every file is found.

## Fonts, if there is no brand yet

Pick one sans, one serif and one mono, and use few weights. Free, open licensed (OFL) faces from Google Fonts (the files are at github.com/google/fonts) that read as designed rather than default:

- Sans: Instrument Sans, Hanken Grotesk, Schibsted Grotesk, Geist
- Serif: Newsreader, Source Serif 4, EB Garamond, Literata
- Mono: JetBrains Mono, IBM Plex Mono, Fragment Mono, Geist Mono

Inter and Space Grotesk are fine faces, but they are so common in generated design that they read as the safe default. Ship the foundry's unmodified variable font and pick weights with `font_axes`.

For captions, the font must be installed on the system (libass looks fonts up by name), and a second script (Burmese, Thai, Chinese, Japanese, Korean) needs its own face, for example Noto Sans Myanmar or Noto Sans JP.
