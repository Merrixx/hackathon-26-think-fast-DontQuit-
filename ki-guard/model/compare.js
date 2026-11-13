// node model/compare.js -> model/compare.json: engines side by side on the holdout (quality, calibration curve
// vs the ideal line, latency avg/p50/p95/best) and the shipped guard at 3 speeds: in process, HTTP API, hook process.
'use strict';
const fs = require('fs'), path = require('path'), http = require('http'), { spawn, execFileSync } = require('child_process');
const L = require('./lib');
const { evaluate: rules } = require('../guard');
const ROOT = path.join(__dirname, '..'), DATA = path.join(ROOT, 'data');
const redDir = path.join(DATA, 'RedCode/dataset/RedCode-Exec/bash2text_dataset_json');
if (!fs.existsSync(redDir)) { console.error('Run `node get-data.js` first.'); process.exit(1); }
const splitOf = n => (n % 5 === 0 ? 'holdout' : n % 5 === 4 ? 'eval' : 'train');
const rows = [];
for (const f of fs.readdirSync(redDir).sort()) for (const r of JSON.parse(fs.readFileSync(path.join(redDir, f), 'utf8'))) rows.push({ text: r.Code, label: 1, split: splitOf(+String(r.Index).split('_')[0]) });
const seen = new Set();
fs.readFileSync(path.join(DATA, 'nl2bash/data/bash/all.cm'), 'utf8').split('\n').forEach((l, i) => { const t = l.trim(); if (!t || seen.has(t)) return; seen.add(t); rows.push({ text: t, label: 0, split: splitOf(i) }); });
const lvl = t => { try { return rules(t).level; } catch { return 'allow'; } };
for (const r of rows) r.rule = lvl(r.text);
const S = s => rows.filter(r => r.split === s), train = S('train'), ev = S('eval'), hold = S('holdout');

const base = train.filter(r => r.label).length / train.length;
const ruleP = {}; for (const v of ['allow', 'ask', 'block']) { const x = train.filter(r => r.rule === v); ruleP[v] = x.length ? (x.filter(r => r.label).length + 1) / (x.length + 2) : base; }
const E = require('./ensemble');
const W = {}, PL = {}, T = {};
const sc = (id, t) => L.score(W[id], E.MODELS[id].feats(t, lvl(t)));
for (const id in E.MODELS) {
  W[id] = L.train(train.map(r => E.MODELS[id].feats(r.text, r.rule)), train.map(r => r.label));
  PL[id] = L.platt(ev.map(r => L.decision(sc(id, r.text), r.label)));
  T[id] = E.thresholds(ev.map(r => ({ p: E.cal(PL[id], sc(id, r.text)), y: r.label })));
}
const evalBrier = {}; for (const id in E.MODELS) evalBrier[id] = L.summarize(ev.map(r => L.decision(E.cal(PL[id], sc(id, r.text)), r.label))).brier;
const order = Object.keys(evalBrier).sort((a, b) => evalBrier[b] - evalBrier[a]); // worst first
const WEIGHTS = Object.fromEntries(order.map((id, i) => [id, i + 1]));
const psOf = t => { const ps = {}; for (const id in E.MODELS) ps[id] = E.cal(PL[id], sc(id, t)); return ps; };
const autoP = t => E.auto(psOf(t), WEIGHTS).p;
T.auto = E.thresholds(ev.map(r => ({ p: autoP(r.text), y: r.label })));
fs.writeFileSync(path.join(__dirname, 'ensemble.json'), JSON.stringify({ models: Object.fromEntries(Object.keys(E.MODELS).map(id => [id, { w: L.sparse(W[id]), platt: { a: PL[id].a, b: PL[id].b } }])), thresholds: T, weights: WEIGHTS, eval_brier: evalBrier }));
// Team view on holdout: how often the 3 agree, and who is right when they don't.
const team = { weights: WEIGHTS, eval_brier: evalBrier, all_agree: 0, all_agree_correct: 0, split: 0, split_auto_correct: 0, right_when_split: Object.fromEntries(Object.keys(E.MODELS).map(id => [id, 0])) };
for (const r of hold) { const ps = psOf(r.text); const v = Object.values(ps).map(p => p >= 0.5 ? 1 : 0); const a = E.auto(ps, WEIGHTS).p >= 0.5 ? 1 : 0;
  if (v.every(x => x === v[0])) { team.all_agree++; team.all_agree_correct += v[0] === r.label; } else { team.split++; team.split_auto_correct += a === r.label; for (const id in ps) team.right_when_split[id] += (ps[id] >= 0.5 ? 1 : 0) === r.label; } }

const engines = [
  { id: 'base', name: 'Base rate', fn: t => base, cons: ['Says the same for every command', 'Catches nothing'] },
  { id: 'regex', name: 'Regex rules', fn: t => ruleP[lvl(t)], cons: ['Only knows patterns someone wrote', 'Missed every unseen attack scenario', 'Easy to dodge with other spelling'] },
  { id: 'words', name: 'Model A: words', fn: t => E.cal(PL.words, sc('words', t)), cons: ['Blind to spelling tricks inside words', 'Ignores the rule verdict', 'Bash only'] },
  { id: 'chars', name: 'Model B: characters', fn: t => E.cal(PL.chars, sc('chars', t)), cons: ['Bigger model file', 'Slower than word models', 'Bash only'] },
  { id: 'rules', name: 'Model C: words + rules', fn: t => E.cal(PL.rules, sc('rules', t)), cons: ['Inherits rule blind spots', 'Bash only'] },
  { id: 'auto', name: 'Auto (vote of 3)', fn: autoP, cons: ['Runs 3 models: ~3x the work', 'Bash only, PowerShell only asks', 'Two data sources: style shortcut not ruled out'] },
];
const stat = v => { const o = [...v].sort((a, b) => a - b); return { avg: v.reduce((a, b) => a + b, 0) / v.length, p50: L.percentile(v, 50), p95: L.percentile(v, 95), best: o[0] }; };
function curve(ds) { const b = Array.from({ length: 10 }, () => ({ n: 0, p: 0, y: 0 }));
  for (const d of ds) { const k = Math.min(9, Math.floor(d.p * 10)); b[k].n++; b[k].p += d.p; b[k].y += d.label; }
  return b.filter(x => x.n).map(x => ({ predicted: x.p / x.n, actual: x.y / x.n, n: x.n })); }

const out = { team, holdout_n: hold.length, holdout_risky: hold.filter(r => r.label).length, engines: [] };
for (const e of engines) {
  for (let i = 0; i < 300; i++) e.fn(hold[i].text); // warm-up
  const lat = [], ds = [];
  for (const r of hold) { const t0 = process.hrtime.bigint(); const p = e.fn(r.text); lat.push(Number(process.hrtime.bigint() - t0) / 1e6); ds.push(L.decision(p, r.label)); }
  const s = L.summarize(ds); const th = T[e.id];
  const q = th && (() => { const A = ds.filter(d => d.p < th.tAllow), Bk = ds.filter(d => d.p >= th.tBlock), Q = ds.length - A.length - Bk.length;
    return { auto_allowed: A.length, attacks_let_through: A.filter(d => d.label).length, auto_blocked: Bk.length, harmless_blocked: Bk.filter(d => !d.label).length, to_person: Q, automated_share: (A.length + Bk.length) / ds.length }; })();
  out.engines.push({ id: e.id, name: e.name, auc: s.auc, brier: s.brier, ece: s.ece, recall: s.recall, false_alarm_rate: s.false_alarm_rate, accuracy: s.accuracy,
    latency_ms: stat(lat), queue: q || null, thresholds: th || null, calibration_curve: curve(ds), cons: e.cons });
}

// 3 speeds of the real shipped guard.
const { predict } = require('./predict');
const cmds = hold.slice(0, 500).map(r => r.text);
const inproc = cmds.map(t => { const t0 = process.hrtime.bigint(); predict(t, lvl(t)); return Number(process.hrtime.bigint() - t0) / 1e6; });
const PORT = 8000 + Math.floor(Math.random() * 900) + 30;
const srv = spawn(process.execPath, [path.join(ROOT, 'serve.js')], { env: { ...process.env, PORT: String(PORT) }, stdio: 'ignore' });
const agent = new http.Agent({ keepAlive: true });
const call = t => new Promise((ok, no) => { const t0 = process.hrtime.bigint();
  const q = http.request({ host: '127.0.0.1', port: PORT, path: '/v1/systemone', method: 'POST', agent, headers: { 'content-type': 'application/json' } }, res => { res.resume(); res.on('end', () => ok(Number(process.hrtime.bigint() - t0) / 1e6)); });
  q.on('error', no); q.end(JSON.stringify({ state: { command: t }, questions: { risky: { type: 'noul' } } })); });
(async () => {
  for (let i = 0; i < 50; i++) { try { await call('ls'); break; } catch { await new Promise(r => setTimeout(r, 100)); } }
  const httpLat = []; for (const t of cmds) httpLat.push(await call(t));
  agent.destroy(); srv.kill();
  const hook = []; for (let i = 0; i < 20; i++) { const t0 = process.hrtime.bigint();
    try { execFileSync(process.execPath, [path.join(ROOT, 'guard.js'), 'check', cmds[i]], { env: { ...process.env, KI_GUARD_NOLOG: '1' }, stdio: 'ignore' }); } catch {}
    hook.push(Number(process.hrtime.bigint() - t0) / 1e6); }
  out.speeds = [
    { id: 'inproc', name: 'In process (library)', n: inproc.length, ...stat(inproc) },
    { id: 'http', name: 'Local HTTP API', n: httpLat.length, ...stat(httpLat) },
    { id: 'hook', name: 'Hook (new node process per call)', n: hook.length, ...stat(hook) },
  ];
  fs.writeFileSync(path.join(__dirname, 'compare.json'), JSON.stringify(out, null, 2));
  for (const e of out.engines) console.log(e.name.padEnd(22), 'AUC', e.auc.toFixed(4), 'Brier', e.brier.toFixed(4), 'recall', e.recall.toFixed(3), 'avg ms', e.latency_ms.avg.toFixed(4));
  for (const s of out.speeds) console.log(s.name.padEnd(34), 'avg', s.avg.toFixed(3), 'p50', s.p50.toFixed(3), 'p95', s.p95.toFixed(3), 'best', s.best.toFixed(3));
})();
