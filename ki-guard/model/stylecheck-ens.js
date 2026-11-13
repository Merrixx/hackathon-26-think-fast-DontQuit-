// Shortcut test for every ensemble model: harmless holdout NL2Bash lines packed into #!/bin/bash scripts.
'use strict';
const fs = require('fs'), path = require('path'), L = require('./lib'), E = require('./ensemble');
const { evaluate: rules } = require('../guard');
const lvl = t => { try { return rules(t).level; } catch { return 'allow'; } };
const M = JSON.parse(fs.readFileSync(path.join(__dirname, 'ensemble.json'), 'utf8'));
const W = {}; for (const id in M.models) { W[id] = new Float64Array(L.DIM); for (const k in M.models[id].w) W[id][k] = M.models[id].w[k]; }
const p = (id, t) => E.cal(M.models[id].platt, L.score(W[id], E.MODELS[id].feats(t, lvl(t))));
const seen = new Set(), neg = [];
fs.readFileSync(path.join(__dirname, '../data/nl2bash/data/bash/all.cm'), 'utf8').split('\n').forEach((l, i) => { const t = l.trim(); if (!t || seen.has(t)) return; seen.add(t); if (i % 5 === 0) neg.push(t); });
let s = 3; const rnd = () => ((s = Math.imul(s ^ (s >>> 15), 0x2c1b3c6d) + 0x6d2b79f5 | 0) >>> 0) / 4294967296;
const scripts = []; for (let i = 0; i < neg.length;) { const k = 2 + Math.floor(rnd() * 31); scripts.push('#!/bin/bash\n\n' + neg.slice(i, i + k).join('\n')); i += k; }
const out = { scripts: scripts.length };
for (const id of [...Object.keys(M.models), 'auto']) {
  const th = M.thresholds[id];
  const ps = scripts.map(t => id === 'auto' ? E.auto(Object.fromEntries(Object.keys(M.models).map(m => [m, p(m, t)]))).p : p(id, t));
  out[id] = { auto_blocked: ps.filter(x => x >= th.tBlock).length, to_person: ps.filter(x => x >= th.tAllow && x < th.tBlock).length };
}
fs.writeFileSync(path.join(__dirname, 'stylecheck-ens.json'), JSON.stringify(out, null, 2)); console.log(JSON.stringify(out));
