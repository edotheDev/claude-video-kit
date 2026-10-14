// scene.js: one short UI moment, written as a pure function of time.
// render(t) sets every element's state for second t. No CSS transitions, no setTimeout, no wall clock:
// the capture script calls render() once per frame, so every render is identical.
import { E, k, mix, css, words, sayWords, ready } from './motion.js';

window.DURATION = 6.0;

const $ = s => document.querySelector(s);
const pill = $('#pill'), n1 = $('#roll .n1'), n2 = $('#roll .n2');
const card = $('#card'), msg = $('#msg'), draft = $('#draft'), actions = $('#actions');
const send = $('#send'), s1 = $('#send .s1'), s2 = $('#send .s2');
const l1 = $('#draft .l1'), l2 = $('#draft .l2');
const caption = words($('#caption'), [['Every', 3.95], ['reply', 4.12], ['starts', 4.3], ['as', 4.5], ['a', 4.58], ['draft.', 4.66]]);

function render(t) {
  // 1. The count arrives first, quietly.
  let u = k(t, 0.0, 0.7, E.settle);
  css(pill, { opacity: u, transform: `translateX(-50%) translateY(${mix(18, 0, u)}px)` });

  // 2. The card lands: a precise arrival, then stillness.
  u = k(t, 0.3, 1.05, E.settle);
  css(card, { opacity: u, transform: `translateY(${mix(44, 0, u)}px)` });

  // 3. The message, then the draft fading up out of a slight blur.
  u = k(t, 1.05, 1.45, E.settle);
  css(msg, { opacity: u, transform: `scale(${mix(0.97, 1, u)})`, transformOrigin: '0 100%' });
  u = k(t, 1.75, 2.35, E.calm);
  css(draft, { opacity: u, filter: `blur(${(1 - u) * 8}px)`, transform: `translateY(${mix(14, 0, u)}px)` });
  css(actions, { opacity: k(t, 2.2, 2.6, E.calm) });

  // 4. The press: one fast hit, a 1 frame dip, then it settles. No spring.
  const down = k(t, 2.9, 2.98, E.punch), up = k(t, 3.0, 3.25, E.settle);
  css(send, { transform: `scale(${1 - 0.045 * down + 0.045 * up})` });
  u = k(t, 3.05, 3.3, E.calm);
  css(s1, { opacity: 1 - u }); css(s2, { opacity: u });
  css(l1, { opacity: 1 - u }); css(l2, { opacity: u });
  css(draft, { borderColor: u > 0.5 ? '#141414' : '' });

  // 5. The late punchline: the count rolls down while everything else holds still.
  u = k(t, 3.35, 3.7, E.device);
  css(n1, { transform: `translateY(${-110 * u}%)`, opacity: 1 - u });
  css(n2, { transform: `translateY(${110 * (1 - u)}%)`, opacity: u });

  // 6. The line, word by word as it would be said. Then a dead hold to the end.
  sayWords(caption, t);
}

window.render = render;
ready(render);
