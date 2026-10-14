// capture.mjs: render a UI motion scene (an HTML page with a deterministic window.render(t)) to stills or video.
//
//   node capture.mjs stills <scene-dir> <out-dir> 0.5,1.2,3.0      one PNG per time
//   node capture.mjs video  <scene-dir> <out.mp4> [--from 0] [--to 6] [--fps 30]
//   options: --size 1080x1920  --page scene.html  --crf 16  --no-gpu
//
// The page is served from <scene-dir> on a local port and loaded in headless Chrome. For every frame the script
// calls window.render(t) and takes a screenshot, so nothing runs on a wall clock and every render is identical.
// The page sets <body data-ready="1"> once its fonts and images are loaded.
// Chrome: set CHROME_PATH, or a standard install is found on Windows, macOS and Linux.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import puppeteer from 'puppeteer-core';

const args = process.argv.slice(2);
const flag = (name, def) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : def; };
const bool = name => { const i = args.indexOf(name); if (i >= 0) { args.splice(i, 1); return true; } return false; };
const [W, H] = flag('--size', '1080x1920').split('x').map(Number);
const PAGE = flag('--page', 'scene.html');
const FPS = Number(flag('--fps', 30));
const FROM = Number(flag('--from', 0));
const TO = flag('--to', null);
const CRF = flag('--crf', '16');
const GPU = !bool('--no-gpu');
const [mode, sceneDir, out, list] = args;
if (!mode || !sceneDir || !out) {
  console.error('usage: node capture.mjs stills <scene-dir> <out-dir> t1,t2,...  |  node capture.mjs video <scene-dir> <out.mp4> [--to S]');
  process.exit(1);
}

const CHROMES = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
].filter(Boolean);
const chrome = CHROMES.find(p => fs.existsSync(p));
if (!chrome) { console.error('Chrome not found: set CHROME_PATH'); process.exit(1); }

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json',
  '.svg': 'image/svg+xml', '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf' };
const root = path.resolve(sceneDir);
const server = http.createServer((req, res) => {
  const file = path.join(root, decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (!file.startsWith(root) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) { res.writeHead(404); return res.end(); }
  res.writeHead(200, { 'content-type': TYPES[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
  fs.createReadStream(file).pipe(res);
});
await new Promise(r => server.listen(0, '127.0.0.1', r));

const browser = await puppeteer.launch({
  executablePath: chrome, headless: true,
  args: ['--hide-scrollbars', '--force-color-profile=srgb', '--disable-lcd-text', '--font-render-hinting=none',
    ...(GPU ? ['--enable-gpu', '--ignore-gpu-blocklist', '--enable-webgl'] : ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])],
});
let failed = false;
try {
  const page = await browser.newPage();
  page.on('console', m => console.log('[page]', m.text()));
  page.on('pageerror', e => { console.log('[pageerror]', e.message); failed = true; });
  await page.setViewport({ width: W, height: H, deviceScaleFactor: 1 });
  await page.goto(`http://127.0.0.1:${server.address().port}/${PAGE}`, { waitUntil: 'networkidle0' });
  await page.waitForSelector('body[data-ready="1"]', { timeout: 30000 });
  const duration = TO !== null ? Number(TO) : await page.evaluate(() => window.DURATION || 6);
  const cdp = await page.createCDPSession();
  const shot = async () => Buffer.from((await cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true })).data, 'base64');
  const at = async t => {
    await page.evaluate(t => window.render(t), t);
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
  };

  if (mode === 'stills') {
    fs.mkdirSync(out, { recursive: true });
    for (const s of (list || '0').split(',')) {
      const t = Number(s);
      await at(t);
      const f = path.join(out, `still-${t.toFixed(2).padStart(6, '0')}.png`);
      fs.writeFileSync(f, await shot());
      console.log('wrote', f);
    }
  } else if (mode === 'video') {
    const f0 = Math.round(FROM * FPS), f1 = Math.round(duration * FPS);
    const ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-c:v', 'png', '-framerate', String(FPS), '-i', '-',
      '-c:v', 'libx264', '-preset', 'slow', '-crf', CRF, '-pix_fmt', 'yuv420p', '-r', String(FPS), '-movflags', '+faststart', out],
      { stdio: ['pipe', 'inherit', 'inherit'] });
    const started = Date.now();
    for (let f = f0; f < f1; f++) {
      await at(f / FPS);
      if (!ff.stdin.write(await shot())) await new Promise(r => ff.stdin.once('drain', r));
      if ((f - f0) % (FPS * 2) === 0) console.log(`  ${(f / FPS).toFixed(1)} s / ${duration} s`);
    }
    ff.stdin.end();
    await new Promise(r => ff.on('close', r));
    console.log(`wrote ${out}: ${f1 - f0} frames in ${((Date.now() - started) / 1000).toFixed(0)} s`);
  } else {
    console.error('mode must be stills or video'); failed = true;
  }
} finally {
  await browser.close();
  server.close();
}
process.exit(failed ? 1 : 0);
