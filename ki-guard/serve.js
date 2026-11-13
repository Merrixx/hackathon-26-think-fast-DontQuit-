// Decision API (starter-kit contract): POST /v1/systemone on 127.0.0.1:8009. No network calls.
// Every question is answered "is this command risky?" as noul = probability of yes.
'use strict';
const http = require('http');
const { evaluate: rules } = require('./guard');
const { predict, load } = require('./model/predict');
const { traits } = require('./model/traits');

const PORT = +process.env.PORT || 8009;
load();

function textOf(state) {
  if (typeof state === 'string') return state;
  if (state && typeof state === 'object') return String(state.command ?? state.code ?? JSON.stringify(state));
  return JSON.stringify(state ?? '');
}

http.createServer((req, res) => {
  const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
  if (req.method === 'GET' && req.url === '/health') return send(200, { ok: true });
  const S = require('./stats');
  if (req.method === 'GET' && (req.url === '/' || req.url === '/dashboard')) { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(require('fs').readFileSync(require('path').join(__dirname, 'dashboard.html'))); }
  if (req.method === 'GET' && req.url === '/api/stats') return send(200, S.stats());
  if (req.method === 'POST' && req.url === '/api/allow') {
    let b = ''; req.on('data', d => (b += d));
    return req.on('end', () => { try { const p = JSON.parse(b).patterns; if (!Array.isArray(p)) throw 0; S.writeAllow(p.map(String).slice(0, 200)); send(200, { ok: true }); } catch { send(400, { error: 'patterns[]' }); } });
  }
  const readJson = fn => { let b = ''; req.on('data', d => { b += d; if (b.length > 1e5) req.destroy(); }); req.on('end', () => { try { fn(JSON.parse(b || '{}')); } catch { send(400, { error: 'bad request' }); } }); };
  const SETTINGS = require('path').join(require('os').homedir(), '.ki-guard', 'settings.json');
  if (req.method === 'GET' && req.url === '/api/settings') return send(200, { mode: require('./model/predict').mode() });
  if (req.method === 'POST' && req.url === '/api/settings') return readJson(q => {
    if (!['auto', 'words', 'chars', 'rules'].includes(q.mode)) return send(400, { error: 'mode' });
    require('fs').mkdirSync(require('path').dirname(SETTINGS), { recursive: true }); require('fs').writeFileSync(SETTINGS, JSON.stringify({ mode: q.mode })); send(200, { ok: true });
  });
  // Mini tester: type a command, see rules + every model + the vote. Not logged.
  if (req.method === 'POST' && req.url === '/api/check') return readJson(q => {
    const text = String(q.command || '').slice(0, 5000), r = rules(text), t0 = process.hrtime.bigint();
    const m = predict(text, r.level, q.shell || 'bash'); const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    const RANK = { allow: 0, ask: 1, block: 2 }, mine = require('./guard').allowed(text);
    const level = mine ? 'allow' : RANK[m.verdict] > RANK[r.level] ? m.verdict : r.level;
    send(200, { level, allowlisted: mine, rules: { level: r.level, hits: r.hits }, model: m, traits: traits(text), ms });
  });
  if (req.method !== 'POST' || req.url !== '/v1/systemone') return send(404, { error: 'POST /v1/systemone' });
  let body = '';
  req.on('data', d => { body += d; if (body.length > 1e6) req.destroy(); });
  req.on('end', () => {
    try {
      const q = JSON.parse(body);
      const text = textOf(q.state);
      const shell = (q.state && q.state.shell) || 'bash';
      const r = predict(text, rules(text).level, shell);
      const tr = traits(text);
      const answers = {};
      // Question routing by id/instructions: risk level (score), root, reversible; anything else = "is it risky?".
      for (const [id, qq] of Object.entries(q.questions || {})) {
        const key = (id + ' ' + (qq.instructions || '')).toLowerCase();
        if (qq.type === 'score' || /level/.test(key)) {
          // 3 levels from the eval thresholds: 0 low (auto-allow), 1 mid (ask a person), 2 high (auto-block).
          const lv = r.p >= r.tBlock ? 2 : r.p >= r.tAllow ? 1 : 0;
          answers[id] = { type: 'score', score: lv, probabilities: { 0: lv === 0 ? 1 : 0, 1: lv === 1 ? 1 : 0, 2: lv === 2 ? 1 : 0 }, levels: ['low', 'mid', 'high'] };
        } else if (/root|admin|sudo/.test(key)) answers[id] = { type: 'noul', noul: tr.needs_root ? 1 : 0, source: 'rules' };
        else if (/revers|undo/.test(key)) answers[id] = { type: 'noul', noul: tr.reversible ? 1 : 0, source: 'rules' };
        else answers[id] = { type: 'noul', noul: r.p, verdict: r.verdict };
      }
      send(200, { model: q.model || 'ki-guard', answers });
    } catch (e) { send(400, { error: 'bad request' }); }
  });
}).listen(PORT, '127.0.0.1', () => console.log(`KI-Guard decision API on http://127.0.0.1:${PORT}/v1/systemone`));
