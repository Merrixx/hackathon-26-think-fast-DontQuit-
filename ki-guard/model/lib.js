// Features, logistic regression and the starter kit's metrics (ported 1:1 from thinkfast/metrics.py).
'use strict';

const DIM = 1 << 18;

function fnv(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return (h >>> 0) % DIM;
}

// Tokens + token bigrams + rule-guard verdict, hashed. The model file stores numbers only.
function features(text, guardLevel) {
  const toks = String(text).toLowerCase().match(/[a-z0-9_.\/:~$-]+|[|>&;@]/g) || [];
  const set = new Set();
  for (let i = 0; i < toks.length; i++) {
    set.add(fnv('u:' + toks[i]));
    if (i) set.add(fnv('b:' + toks[i - 1] + ' ' + toks[i]));
  }
  if (guardLevel) set.add(fnv('guard:' + guardLevel));
  if (require('./traits').ROOT.test(String(text))) set.add(fnv('trait:root'));
  set.add(fnv('bias'));
  return [...set];
}

const sigmoid = z => 1 / (1 + Math.exp(-z));
function score(w, f) { let z = 0; for (const i of f) z += w[i] || 0; return sigmoid(z); }

// Sparse L2 logistic regression with AdaGrad, fixed seed shuffle.
function train(X, y, { epochs = 8, lr = 0.3, l2 = 1e-4, seed = 1 } = {}) {
  const w = new Float64Array(DIM), g2 = new Float64Array(DIM).fill(1e-8);
  const idx = X.map((_, i) => i);
  let s = seed;
  const rnd = () => ((s = Math.imul(s ^ (s >>> 15), 0x2c1b3c6d) + 0x6d2b79f5 | 0) >>> 0) / 4294967296;
  for (let e = 0; e < epochs; e++) {
    for (let i = idx.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [idx[i], idx[j]] = [idx[j], idx[i]]; }
    for (const k of idx) {
      const g = score(w, X[k]) - y[k];
      for (const i of X[k]) { const gi = g + l2 * w[i]; g2[i] += gi * gi; w[i] -= lr * gi / Math.sqrt(g2[i]); }
    }
  }
  return w;
}

function sparse(w) { const o = {}; w.forEach((v, i) => { if (Math.abs(v) > 1e-6) o[i] = +v.toFixed(5); }); return o; }

// ---- metrics (same formulas as thinkfast/metrics.py, binary "noul" case) ----
function decision(p, label) {
  const pred = p >= 0.5 ? 1 : 0;
  return { p, label, conf: Math.max(p, 1 - p), correct: pred === label };
}
const brier = ds => ds.reduce((a, d) => a + ((d.p - d.label) ** 2) * 2, 0) / ds.length; // multi-class form = 2x binary
function ece(ds, bins = 10) {
  const b = {}; for (const d of ds) (b[Math.min(Math.floor(d.conf * bins), bins - 1)] ||= []).push(d);
  let gap = 0;
  for (const k in b) { const x = b[k]; const acc = x.filter(d => d.correct).length / x.length; const c = x.reduce((a, d) => a + d.conf, 0) / x.length; gap += Math.abs(acc - c) * x.length / ds.length; }
  return gap;
}
function automationRate(ds, budget = 0.05) {
  const r = [...ds].sort((a, b) => b.conf - a.conf);
  let best = 0, thr = 1, err = 0, i = 0;
  while (i < r.length) {
    let j = i; while (j < r.length && r[j].conf === r[i].conf) { err += !r[j].correct; j++; }
    if (err <= budget * j) { best = j; thr = r[i].conf; }
    i = j;
  }
  return { rate: best / r.length, threshold: thr };
}
function auc(ds) {
  const r = [...ds].sort((a, b) => a.p - b.p); let rank = 0, sumPos = 0, nPos = 0, i = 0;
  while (i < r.length) { let j = i; while (j < r.length && r[j].p === r[i].p) j++; const avg = (i + j + 1) / 2; for (let k = i; k < j; k++) if (r[k].label) { sumPos += avg; nPos++; } i = j; }
  const nNeg = r.length - nPos; return (sumPos - nPos * (nPos + 1) / 2) / (nPos * nNeg);
}
function summarize(ds) {
  const a = automationRate(ds);
  const tp = ds.filter(d => d.label && d.p >= 0.5).length, pos = ds.filter(d => d.label).length;
  const fp = ds.filter(d => !d.label && d.p >= 0.5).length, neg = ds.length - pos;
  return { n: ds.length, accuracy: ds.filter(d => d.correct).length / ds.length, auc: auc(ds), brier: brier(ds), ece: ece(ds),
    automation_rate_at_5pct: a.rate, automation_threshold: a.threshold, recall: tp / pos, false_alarm_rate: fp / neg };
}
function percentile(v, q) { const o = [...v].sort((a, b) => a - b); return o[Math.max(1, Math.min(o.length, Math.ceil(q / 100 * o.length))) - 1]; }

// Platt scaling: p' = sigmoid(a*logit(p)+b), fitted by gradient descent.
function platt(ds) {
  let a = 1, b = 0; const lg = p => Math.log(Math.max(1e-9, p) / Math.max(1e-9, 1 - p));
  for (let it = 0; it < 3000; it++) {
    let ga = 0, gb = 0;
    for (const d of ds) { const q = sigmoid(a * lg(d.p) + b); ga += (q - d.label) * lg(d.p); gb += q - d.label; }
    a -= 0.5 * ga / ds.length; b -= 0.5 * gb / ds.length;
  }
  return { a, b, apply: p => sigmoid(a * lg(p) + b) };
}

module.exports = { DIM, features, score, train, sparse, decision, summarize, automationRate, percentile, platt, sigmoid };
