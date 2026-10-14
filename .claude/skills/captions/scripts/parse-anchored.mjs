// parse-anchored.mjs <transcript.md> <work>
// Turn an ANCHORED transcript into caption lines with real times (work/chunks.json).
//
// Use it when no word-level ASR handles the speech, above all when a speaker switches languages inside a sentence.
// Ask a model that handles the mix (Gemini does) for a transcript with a [mm:ss] anchor every 10 to 20 seconds:
//   [00:00] first sentence ...
//   [00:15] next sentence ...
// Inside each anchor window the lines are laid out by how long they take to say, then every boundary is snapped to
// a real pause from prep.mjs (work/runs.json), so a caption never changes in the middle of a word.
// work/corrections.json: {"replace": [["from", "to"], ...]} (or a bare list of pairs), applied first: names come out
// phonetic from any transcription model.
import fs from 'node:fs';
import path from 'node:path';

const [, , mdPath, workArg] = process.argv;
const work = workArg || 'work';
if (!mdPath) { console.error('usage: node parse-anchored.mjs <transcript.md> <work>'); process.exit(1); }

const meta = JSON.parse(fs.readFileSync(path.join(work, 'meta.json'), 'utf8'));
const runs = JSON.parse(fs.readFileSync(path.join(work, 'runs.json'), 'utf8'));

// ---- 1. read the anchors ---------------------------------------------------
const md = fs.readFileSync(mdPath, 'utf8');
const blocks = [];
for (const raw of md.split(/\r?\n/)) {
  const m = raw.match(/^\s*\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*(.+?)\s*$/);
  if (!m) continue;
  const t = m[3] ? (+m[1] * 3600 + +m[2] * 60 + +m[3]) : (+m[1] * 60 + +m[2]);
  blocks.push({ start: t, text: m[4].trim() });
}
if (!blocks.length) { console.error('no [mm:ss] anchors found'); process.exit(1); }
blocks.forEach((b, i) => { b.end = i + 1 < blocks.length ? blocks[i + 1].start : meta.duration; });

// ---- 1b. corrections -------------------------------------------------------
// Transcription models write what they hear, so proper nouns come out phonetically.
// work/corrections.json is a list of [from, to] pairs applied before splitting.
const corrPath = path.join(work, 'corrections.json');
let applied = 0;
if (fs.existsSync(corrPath)) {
  const corr = JSON.parse(fs.readFileSync(corrPath, 'utf8'));
  const pairs = Array.isArray(corr) ? corr : (corr.replace || []);   // same file shape as cues.mjs works too
  for (const b of blocks) {
    for (const [from, to] of pairs) {
      if (b.text.includes(from)) {
        b.text = b.text.split(from).join(to);
        applied++;
      }
    }
  }
}

// ---- 2. measures -----------------------------------------------------------
const MY_COMB = /[ါ-ဿ]/;              // stacking marks, no advance
const isMyCh = c => /[က-႟]/.test(c);

// how long a line takes to say
const weight = s =>
  (s.match(/[က-ဪ]/g) || []).length * 1.0 +
  (s.match(/[A-Za-z][A-Za-z'’]*/g) || []).length * 1.9 + 0.6;

// how wide a line renders, in units of font size
const cost = s => {
  let w = 0;
  for (const ch of s) {
    if (MY_COMB.test(ch)) continue;
    else if (isMyCh(ch)) w += 0.62;
    else if (/\s/.test(ch)) w += 0.26;
    else w += 0.50;
  }
  return w;
};
const MAX_COST = Number(process.env.MAX_COST || 22);   // one comfortable line at size 54 on a 720 wide frame

// ---- 3. break at the BEST seam, not the first one that fits ----------------
function gapScore(prev, nextWord) {
  const p = prev.slice(-1);
  if (/[၊။。！？]/.test(p)) return 100;   // Burmese or CJK clause or sentence end
  if (/[.!?]/.test(p)) return 90;             // English sentence end
  if (/[)\]”"']/.test(p)) return 80;     // after a closing bracket or quote
  if (/[,;:]/.test(p)) return 70;
  const a = isMyCh(p), b = isMyCh((nextWord || ' ')[0]);
  if (a !== b) return 60;                     // script change, a natural seam
  return 10;                                  // plain space, last resort
}

function split(text) {
  const parts = text.split(/(?<=[၊။])\s*/g).map(x => x.trim()).filter(Boolean);
  const out = [];
  for (const p of parts) {
    if (cost(p) <= MAX_COST) { out.push(p); continue; }
    const words = p.split(/\s+/);
    let line = [];
    for (let i = 0; i < words.length; i++) {
      line.push(words[i]);
      if (cost(line.join(' ')) < MAX_COST) continue;
      let bestAt = line.length - 1, best = -Infinity;
      const from = Math.max(1, Math.floor(line.length * 0.5));
      for (let g = from; g < line.length; g++) {
        const sc = gapScore(line.slice(0, g).join(' '), line[g]);
        const near = 1 - Math.abs(g / line.length - 0.85);   // prefer near the end
        const total = sc + near * 10;
        if (total > best) { best = total; bestAt = g; }
      }
      out.push(line.slice(0, bestAt).join(' '));
      line = line.slice(bestAt);
    }
    if (line.length) out.push(line.join(' '));
  }
  return out.map(x => x.trim()).filter(Boolean);
}

// ---- 4. pause edges to snap to ---------------------------------------------
const edges = [];
for (const r of runs) { edges.push(r.start); edges.push(r.end); }
const snap = (t, maxDrift) => {
  let best = t, d = Infinity;
  for (const e of edges) { const dd = Math.abs(e - t); if (dd < d) { d = dd; best = e; } }
  return d <= maxDrift ? best : t;
};

// ---- 5. lay the lines inside each anchor window -----------------------------
const chunks = [];
for (const b of blocks) {
  const lines = split(b.text);
  if (!lines.length) continue;
  const w = lines.map(weight);
  const tot = w.reduce((a, c) => a + c, 0);
  const span = Math.max(0.8, b.end - b.start);
  let t = b.start;
  lines.forEach((text, i) => {
    const share = (w[i] / tot) * span;
    const last = i === lines.length - 1;
    const end = last ? b.end
      : Math.min(b.end - 0.05, Math.max(t + 0.7, snap(t + share, 0.4)));
    chunks.push({ text, anchor: b.start, start: +t.toFixed(2), end: +end.toFixed(2), dur: +(end - t).toFixed(2) });
    t = end;
  });
}

fs.writeFileSync(path.join(work, 'chunks.json'), JSON.stringify(chunks, null, 1));

// integrity: every non space character of the source must survive
const srcChars = blocks.map(b => b.text).join('').replace(/\s/g, '');
const capChars = chunks.map(c => c.text).join('').replace(/\s/g, '');
const durs = chunks.map(c => c.dur).sort((a, b) => a - b);

console.log(JSON.stringify({
  anchors: blocks.length,
  caption_lines: chunks.length,
  video_seconds: +meta.duration.toFixed(1),
  covered_to: chunks[chunks.length - 1].end,
  duration: { min: durs[0], median: durs[Math.floor(durs.length / 2)], max: durs[durs.length - 1] },
  over_6s: chunks.filter(c => c.dur > 6).length,
  corrections_applied: applied,
  integrity: {
    source_chars: srcChars.length,
    caption_chars: capChars.length,
    lossless: srcChars === capChars,
  },
}, null, 2));
