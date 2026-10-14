// ass.mjs <chunks.json> <meta.json> <out.ass> [--font "Name"] [--font2 "Name" --script2 myanmar|cjk]
//         [--size 0.042] [--y 0.645] [--latin-scale 0.9] [--max-lines 2]
// Build an ASS subtitle file: wrapped to the safe width, placed below the middle of the frame (clear of the face
// and of the platform's own UI at the bottom), white with a thin dark outline and no box.
// A second script (Burmese, Chinese, Japanese, Korean) gets its own font per run, so each script uses a face made
// for it. Render with:  ffmpeg -i in.mp4 -vf "ass=captions.ass:shaping=complex" ...   (see SKILL.md)
import fs from 'node:fs';

const args = process.argv.slice(2);
const flag = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : def; };
const FONT = flag('--font', 'Arial');
const FONT2 = flag('--font2', null);
const SCRIPT2 = flag('--script2', FONT2 ? 'myanmar' : 'none');
const SIZE = Number(flag('--size', 0.042));          // of frame height: 54 px on 1280, 81 on 1920
const POS_Y = Number(flag('--y', 0.645));             // centre of the block, of frame height: on the chest, not the chin
const LATIN_SCALE = Number(flag('--latin-scale', FONT2 ? 0.9 : 1.0));
const MAX_LINES = Number(flag('--max-lines', 2));
const [chunksPath, metaPath, outPath] = args;
if (!chunksPath || !metaPath || !outPath) {
  console.error('usage: node ass.mjs <chunks.json> <meta.json> <out.ass> [--font "Name"] [--font2 "Name" --script2 myanmar|cjk]');
  process.exit(1);
}
const chunks = JSON.parse(fs.readFileSync(chunksPath, 'utf8'));
const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
const W = meta.width, H = meta.height;

const FS = Math.round(H * SIZE);
const FS_LATIN = Math.round(FS * LATIN_SCALE);
const OUTLINE = Math.max(3, Math.round(H * 0.0028));
const CENTRE_Y = Math.round(H * POS_Y);
const SIDE = Math.round(W * 0.085);
const MAX_W = W - SIDE * 2;

// ---- scripts: what counts as the second script, how wide it sets, where a line may break ----------------
const SCRIPTS = {
  // Burmese marks stack on the base glyph and add no advance; counting them wraps short lines for nothing.
  // Burmese does not space every word, so a line may also break before a syllable-initial consonant.
  myanmar: {
    is: ch => /[က-႟ꧠ-꧿ꩠ-ꩿ]/.test(ch),
    zero: ch => /[ါ-ဿ]/.test(ch),
    width: 0.62,
    breakBefore: (prev, ch) => /[က-ဪ]/.test(ch) && prev !== '္' && !/[ျ-ှ]/.test(prev) && !/\s/.test(prev),
  },
  // Chinese, Japanese, Korean: full width, and a line may break between any two characters.
  cjk: {
    is: ch => /[　-ヿ㐀-鿿가-힯＀-￯]/.test(ch),
    zero: () => false,
    width: 1.0,
    breakBefore: (prev, ch) => !/[、。，！？]/.test(ch),
  },
  none: { is: () => false, zero: () => false, width: 0, breakBefore: () => false },
};
const S2 = SCRIPTS[SCRIPT2] || SCRIPTS.none;

const widthOf = text => {
  let w = 0;
  for (const ch of text) {
    if (S2.zero(ch)) continue;
    else if (S2.is(ch)) w += S2.width * FS;
    else if (/\s/.test(ch)) w += 0.26 * FS_LATIN;
    else w += 0.52 * FS_LATIN;
  }
  return w;
};

function candidates(text) {
  const c = [];
  const chars = [...text];
  for (let i = 1; i < chars.length; i++) {
    const prev = chars[i - 1], ch = chars[i];
    if (/\s/.test(prev) && !/\s/.test(ch)) { c.push(i); continue; }
    if (S2.is(ch) && S2.breakBefore(prev, ch)) c.push(i);
  }
  return c;
}

// Two balanced lines when one does not fit; more only rather than overflow the frame.
function wrap(text) {
  if (widthOf(text) <= MAX_W) return [text];
  const chars = [...text];
  const cut = (a, b) => chars.slice(a, b).join('').trim();
  const cands = candidates(text);
  let best = null;
  for (const i of cands) {
    const a = cut(0, i), b = cut(i);
    if (!a || !b) continue;
    const wa = widthOf(a), wb = widthOf(b);
    if (wa > MAX_W || wb > MAX_W) continue;
    const score = Math.abs(wa - wb);
    if (!best || score < best.score) best = { lines: [a, b], score };
  }
  if (best) return best.lines;
  const lines = [];
  let start = 0;
  while (start < chars.length && lines.length < MAX_LINES + 1) {
    let end = -1;
    for (const i of cands) {
      if (i <= start) continue;
      if (widthOf(cut(start, i)) > MAX_W) break;
      end = i;
    }
    if (end < 0) break;
    lines.push(cut(start, end));
    start = end;
  }
  lines.push(cut(start));
  return lines.filter(Boolean);
}

const esc = s => s.replace(/\\/g, '\\\\').replace(/\{/g, '\\{').replace(/\}/g, '\\}');

// Runs of one script, each with its font and size set explicitly (an ASS override persists to the end of the line).
function styled(line) {
  if (!FONT2) return esc(line);
  const out = [];
  let cur = null;
  for (const ch of line) {
    const neutral = /[\s.,!?;:'"()\[\]‘’“”၊။]/.test(ch);
    const script = neutral ? (cur ? cur.script : 'latin') : (S2.is(ch) ? 's2' : 'latin');
    if (!cur || cur.script !== script) { cur = { script, text: '' }; out.push(cur); }
    cur.text += ch;
  }
  return out.map(r => r.script === 's2'
    ? `{\\fn${FONT2}\\fs${FS}}${esc(r.text)}`
    : `{\\fn${FONT}\\fs${FS_LATIN}}${esc(r.text)}`).join('');
}

const ts = sec => {
  const cs = Math.max(0, Math.round(sec * 100));
  const h = Math.floor(cs / 360000), m = Math.floor(cs / 6000) % 60, s = Math.floor(cs / 100) % 60, c = cs % 100;
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(c).padStart(2, '0')}`;
};

const head = `[Script Info]
ScriptType: v4.00+
PlayResX: ${W}
PlayResY: ${H}
WrapStyle: 2
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,${FONT},${FS_LATIN},&H00FFFFFF,&H00FFFFFF,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,${OUTLINE},0,5,${SIDE},${SIDE},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;

let overflow = 0, tooMany = 0;
const events = chunks.map(c => {
  const lines = wrap(c.text.trim());
  if (lines.length > MAX_LINES) tooMany++;
  for (const l of lines) if (widthOf(l) > MAX_W) overflow++;
  return `Dialogue: 0,${ts(c.start)},${ts(c.end)},Cap,,0,0,0,,{\\pos(${Math.round(W / 2)},${CENTRE_Y})}${lines.map(styled).join('\\N')}`;
}).join('\n');

fs.writeFileSync(outPath, head + events + '\n');
console.log(JSON.stringify({
  out: outPath, cues: chunks.length, font: FONT, font2: FONT2, script2: SCRIPT2,
  font_size: FS, latin_size: FS_LATIN, outline: OUTLINE, centre_y: CENTRE_Y, max_line_width_px: MAX_W,
  cues_over_max_lines: tooMany,      // should be 0: shorten the cue (cues.mjs --max-chars) instead
  OVERFLOWING_LINES: overflow,       // must be 0
}, null, 2));
