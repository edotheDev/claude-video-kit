// prep.mjs <video> [workdir]
// Probe the video, extract 16 kHz mono audio, and map the pauses and the speech runs between them.
// The pause map is the evidence for cut points and caption boundaries, so it is measured here,
// never guessed later.
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const src = process.argv[2];
const work = process.argv[3] || 'work';
if (!src) { console.error('usage: node prep.mjs <video> [workdir]'); process.exit(1); }
fs.mkdirSync(work, { recursive: true });

const sh = (bin, args) => execFileSync(bin, args, { encoding: 'utf8', maxBuffer: 1 << 28, stdio: ['ignore', 'pipe', 'pipe'] });
// ffmpeg reports filter results on stderr and still succeeds, so execFileSync never shows them. spawnSync does.
const err = (bin, args) => String(spawnSync(bin, args, { encoding: 'utf8', maxBuffer: 1 << 28 }).stderr || '');

// ---- 1. probe ----------------------------------------------------------------
const probe = JSON.parse(sh('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', src]));
const v = probe.streams.find(s => s.codec_type === 'video');
const a = probe.streams.find(s => s.codec_type === 'audio');
if (!a) { console.error('no audio stream in ' + src); process.exit(1); }
const [fn, fd] = ((v && v.r_frame_rate) || '30/1').split('/').map(Number);
const meta = {
  src: path.resolve(src),
  duration: Number(probe.format.duration),
  width: v ? v.width : 0, height: v ? v.height : 0,
  fps: fd ? fn / fd : fn,
  vcodec: v && v.codec_name, acodec: a.codec_name,
  vertical: v ? v.height > v.width : false,
};
fs.writeFileSync(path.join(work, 'meta.json'), JSON.stringify(meta, null, 2));

// ---- 2. audio ----------------------------------------------------------------
const wav = path.join(work, 'audio.wav');
sh('ffmpeg', ['-y', '-v', 'error', '-i', src, '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', wav]);

// ---- 3. levels, so the silence gate is measured, not assumed --------------------
// Quiet recordings sit near -40 dB on average, so a fixed -30 dB gate finds no pauses at all.
const vol = err('ffmpeg', ['-hide_banner', '-i', wav, '-af', 'volumedetect', '-f', 'null', '-']);
const mean = Number((vol.match(/mean_volume:\s*(-?[\d.]+)/) || [])[1]);
const max = Number((vol.match(/max_volume:\s*(-?[\d.]+)/) || [])[1]);
const noise = Math.round(Math.min(-25, Math.max(-50, (Number.isFinite(mean) ? mean : -40) + 4)));

// ---- 4. pauses and speech runs -------------------------------------------------
const sd = err('ffmpeg', ['-hide_banner', '-i', wav, '-af', `silencedetect=noise=${noise}dB:d=0.30`, '-f', 'null', '-']);
const pauses = [];
let open = null;
for (const line of sd.split(/\r?\n/)) {
  const s = line.match(/silence_start:\s*(-?[\d.]+)/);
  const e = line.match(/silence_end:\s*([\d.]+)/);
  if (s) open = Math.max(0, Number(s[1]));
  if (e && open !== null) { pauses.push({ start: open, end: Number(e[1]), dur: +(Number(e[1]) - open).toFixed(3) }); open = null; }
}
if (open !== null) pauses.push({ start: open, end: meta.duration, dur: +(meta.duration - open).toFixed(3) });

const runs = [];
let t = 0;
for (const p of pauses) {
  if (p.start - t > 0.05) runs.push({ start: +t.toFixed(3), end: +p.start.toFixed(3) });
  t = p.end;
}
if (meta.duration - t > 0.05) runs.push({ start: +t.toFixed(3), end: +meta.duration.toFixed(3) });

fs.writeFileSync(path.join(work, 'pauses.json'), JSON.stringify({ noise_gate_db: noise, mean_volume: mean, max_volume: max, count: pauses.length, pauses }, null, 2));
fs.writeFileSync(path.join(work, 'runs.json'), JSON.stringify(runs, null, 1));

console.log(JSON.stringify({
  ...meta, mean_volume: mean, max_volume: max, noise_gate_db: noise,
  pauses: pauses.length, speech_runs: runs.length,
  longest_pause: pauses.length ? Math.max(...pauses.map(p => p.dur)) : 0, work,
}, null, 2));
