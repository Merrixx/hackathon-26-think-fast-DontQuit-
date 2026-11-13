// Turns a command into { p, verdict }. Uses model/ensemble.json (3 models + auto vote) when present,
// else model/model.json. Mode from ~/.ki-guard/settings.json { "mode": "auto" | "words" | "chars" | "rules" }.
'use strict';
const fs = require('fs'), path = require('path'), os = require('os');
const L = require('./lib'), E = require('./ensemble');

let M = null, ENS = null;
const vec = w => { const v = new Float64Array(L.DIM); for (const k in w) v[k] = w[k]; return v; };
function load() {
  if (M || ENS) return M || ENS;
  const ep = path.join(__dirname, 'ensemble.json');
  if (fs.existsSync(ep)) {
    const e = JSON.parse(fs.readFileSync(ep, 'utf8'));
    ENS = { models: Object.fromEntries(Object.entries(e.models).map(([id, m]) => [id, { w: vec(m.w), platt: m.platt }])), thresholds: e.thresholds, weights: e.weights };
    return ENS;
  }
  const m = JSON.parse(fs.readFileSync(path.join(__dirname, 'model.json'), 'utf8'));
  M = { w: vec(m.w), platt: m.platt, tAllow: m.tAllow, tBlock: m.tBlock };
  return M;
}
function mode() { try { const m = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.ki-guard', 'settings.json'), 'utf8')).mode; return ['auto', 'words', 'chars', 'rules'].includes(m) ? m : 'auto'; } catch { return 'auto'; } }

// ruleLevel: verdict of the regex rules. shell: 'bash' (trained) or other (out of domain: never auto-block).
function predict(text, ruleLevel = 'allow', shell = 'bash') {
  load();
  let p, t, md = 'single', models = null, votes = null;
  if (ENS) {
    md = mode();
    models = {}; for (const id in ENS.models) models[id] = E.cal(ENS.models[id].platt, L.score(ENS.models[id].w, E.MODELS[id].feats(text, ruleLevel)));
    if (md === 'auto') { const a = E.auto(models, ENS.weights); p = a.p; votes = a.votes; } else p = models[md];
    t = ENS.thresholds[md];
  } else {
    p = L.score(M.w, L.features(text, ruleLevel)); p = E.cal(M.platt, p); t = M;
  }
  let verdict = p >= t.tBlock ? 'block' : p >= t.tAllow ? 'ask' : 'allow';
  if (verdict === 'block' && shell !== 'bash') verdict = 'ask';
  return { p, verdict, tAllow: t.tAllow, tBlock: t.tBlock, mode: md, models, votes, weights: ENS && ENS.weights };
}
module.exports = { predict, load, mode };
