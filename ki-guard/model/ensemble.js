// 3 models + "auto" (soft vote). Shared by training (compare.js) and runtime (predict.js).
'use strict';
const L = require('./lib');

function fnv(s) { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); } return (h >>> 0) % L.DIM; }
// Character 3-5-grams: catches spelling tricks (r''m, base64 pieces, odd spacing) that word tokens miss.
function charFeatures(text) {
  const t = ' ' + String(text).toLowerCase().replace(/\s+/g, ' ').slice(0, 4000) + ' ';
  const set = new Set([fnv('bias')]);
  for (let n = 3; n <= 5; n++) for (let i = 0; i + n <= t.length; i++) set.add(fnv('c' + n + ':' + t.slice(i, i + n)));
  return [...set];
}
const MODELS = {
  words: { name: 'Model A: words', feats: (t, lvl) => L.features(t, null) },
  chars: { name: 'Model B: characters', feats: (t, lvl) => charFeatures(t) },
  rules: { name: 'Model C: words + rules', feats: (t, lvl) => L.features(t, lvl) },
};
const logit = p => Math.log(Math.max(1e-9, p) / Math.max(1e-9, 1 - p));
const cal = (pl, p) => (pl ? L.sigmoid(pl.a * logit(p) + pl.b) : p);
// Auto = weighted soft vote. Weights from eval quality: best Brier gets the most say (3), worst the least (1).
// The best model here is also the slowest, so the fastest model ends up with the least say.
function auto(ps, weights) { let s = 0, n = 0; for (const id in ps) { const w = weights ? weights[id] || 0 : 1; s += w * ps[id]; n += w; }
  return { p: s / n, votes: Object.values(ps).filter(p => p >= 0.5).length }; }

// Thresholds from eval: allow below tAllow (<= 2 % of risky slip through), block from tBlock (>= 95 % precision).
function thresholds(ds) {
  const ev = [...ds].sort((a, b) => a.p - b.p), nR = ev.filter(d => d.y).length;
  let tAllow = 0; { let miss = 0; for (let i = 0; i < ev.length; i++) { if (ev[i].y) miss++; if (miss > Math.floor(0.02 * nR)) break; if (i + 1 < ev.length && ev[i + 1].p > ev[i].p) tAllow = ev[i + 1].p; } }
  let tBlock = 1; { let tp = 0, n = 0; for (let i = ev.length - 1; i >= 0; i--) { n++; tp += ev[i].y; if (tp / n >= 0.95 && (i === 0 || ev[i - 1].p < ev[i].p)) tBlock = ev[i].p; } }
  return { tAllow: Math.min(tAllow, tBlock), tBlock };
}
module.exports = { MODELS, charFeatures, cal, auto, thresholds };
