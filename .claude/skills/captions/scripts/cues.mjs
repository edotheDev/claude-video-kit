// cues.mjs <words.json> <work> [--max-chars 42] [--max-dur 4.2] [--drop-fillers] [--duration S]
// Word timings -> caption cues (work/chunks.json), broken where a reader expects a break.
//
// Greedy filling breaks phrases in the wrong places ("the next" / "chapter", "trying to get a" / "ticket").
// So every legal break is scored, and dynamic programming picks the cheapest set of breaks for the whole video:
// sentence ends are free, a break after "the" or "to" is expensive, a phrasal verb is never split, a cue never
// straddles two sentences when it can help it, and a real pause always ends a cue.
//
// work/corrections.json (optional), applied before segmenting:
//   {"replace": [["Gemeni", "Gemini"]],                      per word, punctuation kept
//    "edits":   [{"from": 10, "to": 13, "text": null, "why": "false start"},
//                {"from": 40, "to": 40, "text": "lock in", "why": "ASR heard log in"}]}   index ranges into words
// Every edit carries a reason. Proper nouns come out phonetic from any ASR: fix names here, never by hand later.
import fs from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const flag = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : def; };
const bool = name => { const i = args.indexOf(name); if (i >= 0) { args.splice(i, 1); return true; } return false; };
const MAX_CHARS = Number(flag('--max-chars', 42));     // about two lines on a vertical frame
const MAX_DUR = Number(flag('--max-dur', 4.2));
const MIN_DUR = Number(flag('--min-dur', 1.1));
const GAP_BREAK = Number(flag('--gap', 0.62));          // a pause this long always ends a cue
const LEAD = Number(flag('--lead', 0.06));              // show a cue a touch early; late reads worse than early
const TAIL = Number(flag('--tail', 0.28));              // hold after the last word
const DROP_FILLERS = bool('--drop-fillers');
const DURATION = flag('--duration', null);
const [wordsPath, work = 'work'] = args;
if (!wordsPath) { console.error('usage: node cues.mjs <words.json> <work> [--max-chars 42] [--drop-fillers]'); process.exit(1); }
fs.mkdirSync(work, { recursive: true });

const src = JSON.parse(fs.readFileSync(wordsPath, 'utf8'));
let out = (Array.isArray(src) ? src : src.words).map(w => ({ ...w }));

// ---- corrections ----------------------------------------------------------------
const corrPath = path.join(work, 'corrections.json');
let applied = 0;
if (fs.existsSync(corrPath)) {
  const corr = JSON.parse(fs.readFileSync(corrPath, 'utf8'));
  for (const e of [...(corr.edits || [])].sort((a, b) => b.from - a.from)) {   // back to front keeps indices valid
    const span = out.slice(e.from, e.to + 1);
    if (!span.length) { console.error('bad edit range', e); process.exit(1); }
    if (e.text === null) { out.splice(e.from, span.length); applied++; continue; }
    const start = span[0].start, end = span[span.length - 1].end;
    const parts = e.text.split(/\s+/), step = (end - start) / parts.length;
    out.splice(e.from, span.length, ...parts.map((p, i) => ({ word: p, start: +(start + step * i).toFixed(3), end: +(start + step * (i + 1)).toFixed(3) })));
    applied++;
  }
  for (const [from, to] of corr.replace || []) {
    for (const w of out) {
      const m = w.word.match(/^(.*?)([.,!?;:]*)$/);
      if (m[1] === from) { w.word = to + m[2]; applied++; }
    }
  }
}

// ---- fillers (standalone only; "like" and "so" carry meaning too often to drop blind) ----
const FILLERS = new Set(['um', 'uh', 'er', 'erm', 'hmm', 'mm']);
const bare = s => s.replace(/[^\p{L}\p{N}']/gu, '').toLowerCase();
let dropped = 0;
if (DROP_FILLERS) {
  const keep = [];
  for (const w of out) { if (FILLERS.has(bare(w.word))) { dropped++; continue; } keep.push(w); }
  out = keep;
}

// ---- break costs (English function words; other languages fall back to punctuation and pauses) ----
const DETERMINER = new Set(['a', 'an', 'the', 'my', 'your', 'his', 'her', 'its', 'our', 'their', 'this', 'that', 'these', 'those'])
const FUNCTION = new Set(['to', 'of', 'in', 'on', 'for', 'with', 'and', 'but', 'or', 'so', 'as', 'at', 'from', 'by', 'is', 'was',
  'are', 'were', 'be', 'been', 'am', 'i', 'it', 'not', 'just', 'like', 'very', 'really', 'about', 'into', 'than', 'then',
  'if', 'when', 'while', 'because', 'can', 'will', 'would', 'should', 'could'])
const PARTICLE = new Set(['in', 'on', 'up', 'out', 'off', 'down', 'over', 'back', 'away'])
const N = out.length;
if (!N) { console.error('no words'); process.exit(1); }
const total = DURATION ? Number(DURATION) : out[N - 1].end + TAIL + 0.5;

function breakCost(j) {
  if (j >= N - 1) return 0;
  const w = out[j].word, b = bare(w);
  const particle = PARTICLE.has(bare(out[j + 1].word)) && !/[.!?,;:]$/.test(w) ? 55 : 0;
  if (/[.!?。！？]$/.test(w)) return particle;
  if (/[,;:，、]$/.test(w)) return 4 + particle;
  if (DETERMINER.has(b)) return 60 + particle;
  if (FUNCTION.has(b)) return 38 + particle;
  return 15 + particle;
}
const textOf = (i, j) => out.slice(i, j + 1).map(w => w.word).join(' ');
const onScreen = (i, j) => {
  const start = Math.max(0, out[i].start - LEAD);
  const ceiling = j + 1 < N ? out[j + 1].start - LEAD - 0.02 : total;
  return Math.min(out[j].end + TAIL, ceiling) - start;
};
const prefixSent = [0];
for (let k = 0; k < N; k++) prefixSent.push(prefixSent[k] + (/[.!?。！？]$/.test(out[k].word) ? 1 : 0));

const best = new Array(N + 1).fill(Infinity), next = new Array(N + 1).fill(-1);
best[N] = 0;
for (let i = N - 1; i >= 0; i--) {
  for (let j = i; j < N; j++) {
    if (j > i && out[j].start - out[j - 1].end >= GAP_BREAK) break;
    const len = textOf(i, j).length;
    if (len > MAX_CHARS && j > i) break;
    if (out[j].end - out[i].start > MAX_DUR && j > i) break;
    if (best[j + 1] === Infinity) continue;
    const scr = onScreen(i, j);
    const fill = 0.018 * Math.max(0, MAX_CHARS - len) ** 2;
    const tooShort = scr < 0.95 ? (0.95 - scr) * 260 : 0;
    const rate = len / Math.max(scr, 0.01);
    const tooFast = rate > 21 ? (rate - 21) * 12 : 0;                       // characters a second a viewer can read
    const straddle = (prefixSent[j] - prefixSent[i]) * 110;
    const orphan = j - i + 1 <= 2 && i > 0 && !/[.!?]$/.test(out[i - 1].word) ? 40 : 0;
    const cost = fill + tooShort + tooFast + straddle + orphan + breakCost(j) + best[j + 1];
    if (cost < best[i]) { best[i] = cost; next[i] = j; }
  }
}

const chunks = [];
for (let i = 0; i < N;) {
  const j = next[i];
  if (j < 0) { console.error('segmentation failed at word', i); process.exit(1); }
  chunks.push({ text: textOf(i, j).replace(/\s+/g, ' ').trim(), start: Math.max(0, out[i].start - LEAD), end: out[j].end + TAIL, words: j - i + 1 });
  i = j + 1;
}
// A cue may hold through the silence after it, but never past the next cue's start.
for (let i = 0; i < chunks.length; i++) {
  const ceiling = i < chunks.length - 1 ? chunks[i + 1].start - 0.02 : total;
  const c = chunks[i];
  c.end = Math.min(c.end, ceiling);
  if (c.end - c.start < MIN_DUR) c.end = Math.min(ceiling, c.start + MIN_DUR);
  if (c.end <= c.start) c.end = c.start + 0.3;
  c.start = +c.start.toFixed(2); c.end = +c.end.toFixed(2); c.dur = +(c.end - c.start).toFixed(2);
}
fs.writeFileSync(path.join(work, 'chunks.json'), JSON.stringify(chunks, null, 1));
fs.writeFileSync(path.join(work, 'corrected-words.json'), JSON.stringify(out, null, 1));

const durs = chunks.map(c => c.dur).sort((a, b) => a - b);
const capChars = chunks.map(c => c.text).join('').replace(/\s/g, '');
const srcChars = out.map(w => w.word).join('').replace(/\s/g, '');
console.log(JSON.stringify({
  cues: chunks.length, corrections_applied: applied, fillers_dropped: dropped,
  duration: { min: durs[0], median: durs[Math.floor(durs.length / 2)], max: durs[durs.length - 1] },
  over_max_chars: chunks.filter(c => c.text.length > MAX_CHARS).length,
  integrity: { lossless: capChars === srcChars },
}, null, 2));
