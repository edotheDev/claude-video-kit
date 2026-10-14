// words.mjs <audio-or-video> <out.json> [--model small|base|tiny] [--lang en|xx]
// Word timings from local Whisper (Transformers.js, runs on the CPU, no API key, no upload).
//   -> {"text": "...", "words": [{"word": "Every", "start": 0.42, "end": 0.61}, ...]}
// English uses the .en models, which are the most accurate here. Any other language uses the multilingual model;
// check its words by ear, and for code-switched speech (two languages inside one sentence) use a transcript
// from a model that handles it instead (see SKILL.md, "When Whisper is the wrong tool").
// The first run downloads the model (base.en about 80 MB, small.en about 250 MB) into ./models.
// Starts are good to about 0.1 s. The first word after a long silence can read early: snap it to the pause map.
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { pipeline, env } from '@huggingface/transformers';

const args = process.argv.slice(2);
const flag = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : def; };
const size = flag('--model', 'small');
const lang = flag('--lang', 'en');
const [src, out] = args;
if (!src || !out) { console.error('usage: node words.mjs <audio-or-video> <out.json> [--model small|base|tiny] [--lang en]'); process.exit(1); }

const HERE = path.dirname(fileURLToPath(import.meta.url));
env.cacheDir = process.env.WHISPER_MODELS || path.join(HERE, 'models');
if (env.backends?.onnx) env.backends.onnx.logLevel = 'error';   // the attention exports print harmless shape warnings

// 16 kHz mono float32 straight from ffmpeg
const raw = execFileSync('ffmpeg', ['-v', 'error', '-i', src, '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'], { maxBuffer: 1 << 30 });
const audio = new Float32Array(raw.buffer, raw.byteOffset, Math.floor(raw.length / 4));

// The "output_attentions" revision exports the cross attentions that word timestamps need.
const model = lang === 'en' ? `Xenova/whisper-${size}.en` : `Xenova/whisper-${size}`;
const asr = await pipeline('automatic-speech-recognition', model, { revision: 'output_attentions', dtype: 'q8' });
const opts = { return_timestamps: 'word', chunk_length_s: 30, stride_length_s: 5 };
if (lang !== 'en') { opts.language = lang; opts.task = 'transcribe'; }
const res = await asr(audio, opts);

const words = (res.chunks || []).map(c => ({
  word: c.text.trim(),
  start: +Number(c.timestamp[0]).toFixed(3),
  end: +Number(c.timestamp[1] ?? c.timestamp[0] + 0.3).toFixed(3),
})).filter(w => w.word);
fs.writeFileSync(out, JSON.stringify({ model, text: res.text.trim(), words }, null, 1));
console.log(JSON.stringify({ model, words: words.length, seconds: +(audio.length / 16000).toFixed(1), out }));
