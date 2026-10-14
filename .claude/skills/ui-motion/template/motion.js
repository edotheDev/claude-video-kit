// motion.js: the small timeline toolkit every scene uses. Time is always a number of seconds passed to render(t).

// A cubic bezier easing, solved for x (the same curves as CSS cubic-bezier()).
export function bezier(x1, y1, x2, y2) {
  const cx = 3 * x1, bx = 3 * (x2 - x1) - cx, ax = 1 - cx - bx;
  const cy = 3 * y1, by = 3 * (y2 - y1) - cy, ay = 1 - cy - by;
  const sx = s => ((ax * s + bx) * s + cx) * s;
  const sy = s => ((ay * s + by) * s + cy) * s;
  const dx = s => (3 * ax * s + 2 * bx) * s + cx;
  return x => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    let s = x;
    for (let i = 0; i < 8; i++) {                     // Newton steps
      const e = sx(s) - x, d = dx(s);
      if (Math.abs(e) < 1e-6) return sy(s);
      if (Math.abs(d) < 1e-6) break;
      s -= e / d;
    }
    let lo = 0, hi = 1; s = x;                          // bisection fallback
    for (let i = 0; i < 30; i++) { const v = sx(s); if (Math.abs(v - x) < 1e-6) break; if (v < x) lo = s; else hi = s; s = (lo + hi) / 2; }
    return sy(s);
  };
}

// The named eases from the motion-craft skill.
export const E = {
  calm: bezier(0.62, 0.01, 0.18, 1),     // default reveals
  device: bezier(0.7, 0, 0.2, 1),        // moving a graphic device, rolling a word
  punch: bezier(0.2, 0.9, 0.1, 1),       // impacts and hits
  settle: bezier(0.16, 1, 0.3, 1),       // surfaces and cards landing
  linear: x => x,                        // machines, counters, terminals only
};

// Progress of a move from a to b seconds, eased: 0 before, 1 after.
export const k = (t, a, b, ease = E.calm) => ease(Math.min(1, Math.max(0, (t - a) / (b - a))));
export const mix = (a, b, u) => a + (b - a) * u;

// Set several style properties at once.
export const css = (el, props) => { for (const p in props) el.style[p] = props[p]; };

// Split an element's text into word spans, each with the time it is said: [["Every", 3.9], ["reply", 4.05], ...].
export function words(el, timed) {
  el.textContent = '';
  return timed.map(([w, at], i) => {
    const s = document.createElement('span');
    s.textContent = w + (i < timed.length - 1 ? ' ' : '');
    s.style.display = 'inline-block';
    s.style.whiteSpace = 'pre';
    el.appendChild(s);
    return { el: s, at };
  });
}

// Words fading up out of a slight blur as they are said (the calm text default).
export function sayWords(spans, t, fade = 0.28, blur = 6, rise = 8) {
  for (const { el, at } of spans) {
    const u = k(t, at - 0.04, at - 0.04 + fade, E.calm);
    css(el, { opacity: u, filter: `blur(${(1 - u) * blur}px)`, transform: `translateY(${(1 - u) * rise}px)` });
  }
}

// Tell the capture script the page is ready once fonts and images have loaded.
export async function ready(render) {
  await document.fonts.ready;
  await Promise.all([...document.images].map(i => i.complete ? null : new Promise(r => { i.onload = i.onerror = r; })));
  render(0);
  document.body.dataset.ready = '1';
}
