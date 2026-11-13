// node model/reproduce.js  ->  every number in REPORT.md. Writes model/model.json + model/results.json.
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const L = require('./lib');
const { evaluate: rules } = require('../guard');

const DATA = path.join(__dirname, '..', 'data');
const redDir = path.join(DATA, 'RedCode/dataset/RedCode-Exec/bash2text_dataset_json');
if (!fs.existsSync(redDir)) { console.error('Run `node get-data.js` first.'); process.exit(1); }

// Split rule, fixed before any training: scenario number mod 5 == 0 -> holdout, == 4 -> eval, else train.
const splitOf = n => (n % 5 === 0 ? 'holdout' : n % 5 === 4 ? 'eval' : 'train');

const rows = [];
for (const f of fs.readdirSync(redDir).sort()) {
  for (const r of JSON.parse(fs.readFileSync(path.join(redDir, f), 'utf8'))) {
    const scen = +String(r.Index).split('_')[0];
    rows.push({ text: r.Code, label: 1, split: splitOf(scen), group: 'redcode-' + scen });
  }
}
const seen = new Set();
fs.readFileSync(path.join(DATA, 'nl2bash/data/bash/all.cm'), 'utf8').split('\n').forEach((line, i) => {
  const t = line.trim(); if (!t || seen.has(t)) return; seen.add(t);
  rows.push({ text: t, label: 0, split: splitOf(i), group: 'nl2bash' });
});
const rulesLevel = t => { try { return rules(t).level; } catch { return 'allow'; } };
for (const r of rows) { r.rule = rulesLevel(r.text); r.f = L.features(r.text, r.rule); }

const S = s => rows.filter(r => r.split === s);
const train = S('train'), evalSet = S('eval'), hold = S('holdout');
const count = s => ({ n: s.length, risky: s.filter(r => r.label).length });

// Baseline 1: base rate from train. Baseline 2: the regex rule guard, its 3 levels mapped to the risky rate seen on train.
const base = train.filter(r => r.label).length / train.length;
const ruleP = {};
for (const lv of ['allow', 'ask', 'block']) { const x = train.filter(r => r.rule === lv); ruleP[lv] = x.length ? (x.filter(r => r.label).length + 1) / (x.length + 2) : base; }

let final;
// Model: logistic regression on hashed tokens + rule verdict, trained on train only.
const w = L.train(train.map(r => r.f), train.map(r => r.label));
const model = r => L.score(w, r.f);

const engines = { 'Base rate': () => base, 'Rule guard (regex)': r => ruleP[r.rule], 'KI-Guard model (raw)': model, 'KI-Guard model (shipped)': r => final(r) };
const evaluateSet = set => Object.fromEntries(Object.entries(engines).map(([k, fn]) => [k, L.summarize(set.map(r => L.decision(fn(r), r.label)))]));

// Calibration check on eval (Platt). Shipped only if it improves eval Brier.
const pl = L.platt(evalSet.map(r => L.decision(model(r), r.label)));
const evalRaw = L.summarize(evalSet.map(r => L.decision(model(r), r.label)));
const evalPl = L.summarize(evalSet.map(r => L.decision(pl.apply(model(r)), r.label)));
const usePlatt = evalPl.brier < evalRaw.brier;
final = r => (usePlatt ? pl.apply(model(r)) : model(r));

// Kit threshold (one confidence cut, 5 % error budget), chosen on eval.
const thr = L.automationRate(evalSet.map(r => L.decision(final(r), r.label))).threshold;
// Tool thresholds, chosen on eval, because a missed attack costs more than a false alarm:
//   auto-allow if p < tAllow: largest cut that lets at most 2 % of eval's risky cases through automatically.
//   auto-block if p >= tBlock: smallest cut where at least 95 % of eval's auto-blocked cases are risky.
//   everything between -> ask a person (review queue).
const ev = evalSet.map(r => ({ p: final(r), y: r.label })).sort((a, b) => a.p - b.p);
const nRisk = ev.filter(d => d.y).length;
let tAllow = 0; { let miss = 0; for (let i = 0; i < ev.length; i++) { if (ev[i].y) miss++; if (miss > Math.floor(0.02 * nRisk)) break; if (i + 1 < ev.length && ev[i + 1].p > ev[i].p) tAllow = ev[i + 1].p; } }
let tBlock = 1; { let tp = 0, n = 0; for (let i = ev.length - 1; i >= 0; i--) { n++; tp += ev[i].y; if (tp / n >= 0.95 && (i === 0 || ev[i - 1].p < ev[i].p)) tBlock = ev[i].p; } }
function queue(set) {
  const ds = set.map(r => ({ p: final(r), y: r.label }));
  const A = ds.filter(d => d.p < tAllow), B = ds.filter(d => d.p >= tBlock), Q = ds.filter(d => d.p >= tAllow && d.p < tBlock);
  return { n: ds.length, auto_allowed: A.length, risky_in_auto_allowed: A.filter(d => d.y).length,
    auto_blocked: B.length, harmless_in_auto_blocked: B.filter(d => !d.y).length,
    to_person: Q.length, risky_in_queue: Q.filter(d => d.y).length,
    automated_share: (A.length + B.length) / ds.length, error_rate_in_automated: (A.filter(d => d.y).length + B.filter(d => !d.y).length) / Math.max(1, A.length + B.length),
    risky_caught_share: (B.filter(d => d.y).length + Q.filter(d => d.y).length) / Math.max(1, ds.filter(d => d.y).length) };
}

// Bootstrap on holdout: gain over rule guard (AUC up, Brier down), 2000 resamples, fixed seed.
function bootstrap(set, n = 2000) {
  let s = 7; const rnd = () => ((s = Math.imul(s ^ (s >>> 15), 0x2c1b3c6d) + 0x6d2b79f5 | 0) >>> 0) / 4294967296;
  const pm = set.map(final), pr = set.map(r => ruleP[r.rule]);
  const gA = [], gB = [];
  for (let b = 0; b < n; b++) {
    const ix = set.map(() => Math.floor(rnd() * set.length));
    if (!ix.some(i => set[i].label) || ix.every(i => set[i].label)) continue;
    const m = L.summarize(ix.map(i => L.decision(pm[i], set[i].label))), q = L.summarize(ix.map(i => L.decision(pr[i], set[i].label)));
    gA.push(m.auc - q.auc); gB.push(q.brier - m.brier);
  }
  const ci = v => [L.percentile(v, 2.5), L.percentile(v, 97.5)];
  return { auc_gain_vs_rules: ci(gA), brier_gain_vs_rules: ci(gB), resamples: gA.length };
}

// Weakest slice: each unseen attack scenario in the holdout.
const slices = {};
for (const g of [...new Set(hold.filter(r => r.label).map(r => r.group))]) {
  const x = hold.filter(r => r.group === g);
  slices[g] = { n: x.length, model_recall: x.filter(r => final(r) >= 0.5).length / x.length, rules_recall: x.filter(r => ruleP[r.rule] >= 0.5).length / x.length };
}

// Real-world check (optional, local only): Bash/PowerShell commands from this machine's Claude Code sessions.
// Private: only aggregate counts are printed, nothing is written to disk.
function realWorld() {
  const root = path.join(os.homedir(), '.claude', 'projects'); if (!fs.existsSync(root)) return null;
  const cmds = [];
  const walk = d => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else if (e.name.endsWith('.jsonl')) {
    for (const line of fs.readFileSync(p, 'utf8').split('\n')) { if (!line.includes('"tool_use"')) continue; try { for (const c of JSON.parse(line).message?.content || []) if (c.type === 'tool_use' && /^(Bash|PowerShell)$/.test(c.name) && c.input?.command) cmds.push(c.name + '' + c.input.command); } catch {} } } } };
  walk(root);
  const uniq = [...new Set(cmds)];
  const items = uniq.map(u => { const [tool, t] = u.split(''); const p = final({ f: L.features(t, rulesLevel(t)) }); return { tool, t, p }; });
  // Same verdict as the hook: the model blocks on its own only for Bash; PowerShell goes to a person instead.
  const v = d => (d.p >= tBlock ? (d.tool === 'Bash' ? 'block' : 'ask') : d.p >= tAllow ? 'ask' : 'allow');
  return { commands: items.length, bash: items.filter(d => d.tool === 'Bash').length, powershell: items.filter(d => d.tool !== 'Bash').length,
    model_above_block_cut: items.filter(d => d.p >= tBlock).length, auto_blocked: items.filter(d => v(d) === 'block').length,
    sent_to_person: items.filter(d => v(d) === 'ask').length, auto_allowed: items.filter(d => v(d) === 'allow').length,
    rules_flagged: items.filter(d => rulesLevel(d.t) !== 'allow').length };
}

// Style check: does the model just learn "multi-line script = risky"? Benign holdout NL2Bash lines,
// grouped into scripts of 2-32 lines with a #!/bin/bash header (RedCode scripts have 2-32 lines, p10-p90).
function styleCheck() {
  const neg = hold.filter(r => !r.label); let s = 3; const rnd = () => ((s = Math.imul(s ^ (s >>> 15), 0x2c1b3c6d) + 0x6d2b79f5 | 0) >>> 0) / 4294967296;
  const ps = []; let i = 0;
  while (i < neg.length) { const k = 2 + Math.floor(rnd() * 31); const t = '#!/bin/bash\n\n' + neg.slice(i, i + k).map(r => r.text).join('\n'); i += k; ps.push(final({ f: L.features(t, rulesLevel(t)) })); }
  return { scripts: ps.length, auto_blocked: ps.filter(p => p >= tBlock).length, to_person: ps.filter(p => p >= tAllow && p < tBlock).length, flagged_at_0_5: ps.filter(p => p >= 0.5).length };
}
// Latency, in process.
const lat = []; for (const r of hold.slice(0, 2000)) { const t0 = process.hrtime.bigint(); final({ f: L.features(r.text, rulesLevel(r.text)) }); lat.push(Number(process.hrtime.bigint() - t0) / 1e6); }

const results = {
  data: { train: count(train), eval: count(evalSet), holdout: count(hold), split_rule: 'RedCode scenario number mod 5: 0 -> holdout, 4 -> eval, else train; NL2Bash line index, same rule' },
  rule_guard_probabilities_from_train: ruleP,
  eval: evaluateSet(evalSet), holdout: evaluateSet(hold),
  calibration: { platt_on_eval: { a: pl.a, b: pl.b }, eval_brier_raw: evalRaw.brier, eval_brier_platt: evalPl.brier, eval_ece_raw: evalRaw.ece, eval_ece_platt: evalPl.ece, shipped: usePlatt ? 'platt' : 'raw' },
  kit_threshold_from_eval: thr, tool_thresholds_from_eval: { tAllow, tBlock }, queue_eval: queue(evalSet), queue_holdout: queue(hold),
  bootstrap_holdout: bootstrap(hold), weakest_slices_holdout: slices,
  style_check_benign_scripts: styleCheck(), real_world_claude_sessions: realWorld(),
  latency_ms_in_process: { p50: L.percentile(lat, 50), p95: L.percentile(lat, 95) },
  weights: Object.keys(L.sparse(w)).length,
};
fs.writeFileSync(path.join(__dirname, 'model.json'), JSON.stringify({ w: L.sparse(w), platt: usePlatt ? { a: pl.a, b: pl.b } : null, tAllow, tBlock }));
fs.writeFileSync(path.join(__dirname, 'results.json'), JSON.stringify(results, null, 2));
console.log(JSON.stringify(results, null, 2));
