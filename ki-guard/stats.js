// Reads the decision log the hook backend writes (~/.ki-guard/log.jsonl) for the dashboard. Local files only.
'use strict';
const fs = require('fs'), path = require('path'), os = require('os');
const DIR = path.join(os.homedir(), '.ki-guard');
const old = e => e.bucket || (e.level === 'block' ? 'blocked' : e.level === 'allow' ? 'safe' : 'likely');
// Scores written by the Python eval harness (`python -m cmdgate.evaluate`) into the repo's results/ folder.
const RESULTS = process.env.KI_GUARD_RESULTS || path.join(__dirname, '..', 'results');
function harness() {
  let files = [];
  try { files = fs.readdirSync(RESULTS).filter(f => f.endsWith('.json')).sort(); } catch {}
  const runs = [];
  for (const f of files) {
    try {
      const r = JSON.parse(fs.readFileSync(path.join(RESULTS, f), 'utf8'));
      if (typeof r.scorer !== 'string' || typeof r.split !== 'string' || !r.metrics || typeof r.metrics.accuracy !== 'number') continue;
      runs.push({ scorer: r.scorer, split: r.split, source: r.source || 'in-process', commands: r.commands ?? null, families: r.families ?? null,
        failed: r.failed ?? 0, ms_per_command: r.ms_per_command ?? null, metrics: r.metrics, ranges: r.ranges || {}, scored_at: fs.statSync(path.join(RESULTS, f)).mtime.toISOString() });
    } catch {}
  }
  return runs;
}
function stats() {
  let lines = [];
  try { lines = fs.readFileSync(path.join(DIR, 'log.jsonl'), 'utf8').split('\n').filter(Boolean); } catch {}
  const ev = lines.map(l => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean)
    .map(e => ({ t: e.t, bucket: old(e), agent: e.agent || 'claude-code', tool: e.tool || '', text: e.text || (e.input && (e.input.command || e.input.file_path)) || '', p: e.p ?? null, hits: e.hits || [] }));
  const buckets = { blocked: 0, likely: 0, unsure: 0, safe: 0, yours: 0 };
  const agents = {}, days = {}, rules = {};
  for (const e of ev) {
    buckets[e.bucket]++;
    (agents[e.agent] ||= { blocked: 0, likely: 0, unsure: 0, safe: 0, yours: 0 })[e.bucket]++;
    const d = (e.t || '').slice(0, 10); (days[d] ||= { blocked: 0, likely: 0, unsure: 0, safe: 0, yours: 0 })[e.bucket]++;
    for (const h of e.hits) rules[h] = (rules[h] || 0) + 1;
  }
  return { harness: harness(), total: ev.length, buckets, agents, days, rules, recent: ev.slice(-60).reverse() };
}
module.exports = { stats, harness };
