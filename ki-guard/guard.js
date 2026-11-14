// KI-Guard's link to the backend. KI-Guard is a frontend: it holds no model and makes no decision.
// Every verdict, every model's risk and every threshold comes from the hook backend
// (`python -m cmdhook.start`, KI_GUARD_BACKEND). The Claude Code hook is cmdhook/hook.js, not this file.
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');

const BACKEND = process.env.KI_GUARD_BACKEND || 'http://127.0.0.1:8019';
const TIMEOUT_MS = +process.env.KI_GUARD_TIMEOUT_MS || 5000;
const DIR = path.join(os.homedir(), '.ki-guard');

// One JSON call to the backend. Rejects on any error, timeout or non-200 answer.
function backend(method, route, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(BACKEND + route, { method, timeout: TIMEOUT_MS, headers: data ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } : {} }, res => {
      let raw = ''; res.on('data', d => (raw += d));
      res.on('end', () => { try { const j = JSON.parse(raw); res.statusCode === 200 ? resolve(j) : reject(new Error(j.error || 'backend error ' + res.statusCode)); } catch (e) { reject(e); } });
    });
    req.on('timeout', () => req.destroy(new Error('backend timed out')));
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// Model shown in the dashboard: ~/.ki-guard/settings.json { "mode": "<model name>" }. Missing or "auto" = the backend's own decision over all its models.
function mode() { try { const m = JSON.parse(fs.readFileSync(path.join(DIR, 'settings.json'), 'utf8')).mode; return typeof m === 'string' && m && m !== 'auto' ? m : null; } catch { return null; } }

// The backend's answer for one command, passed through: { verdict, bucket, p, reason, model, models, rules, policy }.
async function checkCommand(command) {
  const body = { command: String(command).slice(0, 5000) };
  const m = mode(); if (m) body.model = m;
  try { return await backend('POST', '/v1/check', body); }
  catch (e) {
    return { offline: true, verdict: 'ask', bucket: 'unsure', p: null, model: null, models: {}, rules: [], reason: `Hook backend not reachable at ${BACKEND} (${e.message})` };
  }
}

// CLI: node guard.js check "<command>" prints the backend's verdict. Exit 0 allow, 1 ask, 2 block.
if (require.main === module) {
  const args = process.argv.slice(2);
  if (args[0] !== 'check') { console.error('usage: node guard.js check "<command>"   (the Claude Code hook is cmdhook/hook.js)'); process.exit(64); }
  checkCommand(args.slice(1).join(' ')).then(r => {
    console.log(`${String(r.verdict).toUpperCase()}\n  - ${r.reason}`);
    for (const [name, m] of Object.entries(r.models || {})) console.log(`  ${name.padEnd(18)} risk ${(m.p * 100).toFixed(1).padStart(5)} %  ${m.verdict || ''}`);
    process.exit({ allow: 0, ask: 1, block: 2 }[r.verdict] ?? 1);
  });
}
module.exports = { checkCommand, backend, mode, BACKEND };
