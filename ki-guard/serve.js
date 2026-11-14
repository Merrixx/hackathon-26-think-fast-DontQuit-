// KI-Guard frontend server: the dashboard. It holds no model and makes no decision.
// Every verdict, score and threshold comes from the hook backend (`python -m cmdhook.start`, KI_GUARD_BACKEND);
// the counts come from the decision log that backend writes, the harness card from the repo's results/ folder.
'use strict';
const http = require('http'), fs = require('fs'), path = require('path'), os = require('os');
const G = require('./guard'), S = require('./stats');
const { traits } = require('./traits');

const PORT = +process.env.PORT || 8049;
const SETTINGS = path.join(os.homedir(), '.ki-guard', 'settings.json');

http.createServer((req, res) => {
  const send = (code, obj) => { res.writeHead(code, { 'content-type': 'application/json' }); res.end(JSON.stringify(obj)); };
  const readJson = fn => { let b = ''; req.on('data', d => { b += d; if (b.length > 1e5) req.destroy(); }); req.on('end', async () => { try { await fn(JSON.parse(b || '{}')); } catch { send(400, { error: 'bad request' }); } }); };
  const models = () => G.backend('GET', '/v1/models').catch(e => ({ offline: true, error: e.message, backend: G.BACKEND, data: [], default: null }));
  if (req.method === 'GET' && req.url === '/health') return send(200, { ok: true });
  if (req.method === 'GET' && (req.url === '/' || req.url === '/dashboard')) { res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' }); return res.end(fs.readFileSync(path.join(__dirname, 'dashboard.html'))); }
  if (req.method === 'GET' && req.url === '/api/stats') return models().then(m => send(200, { ...S.stats(), models: m, mode: G.mode() || 'auto', backend: G.BACKEND }));
  if (req.method === 'POST' && req.url === '/api/allow') return readJson(q => { if (!Array.isArray(q.patterns)) throw 0; S.writeAllow(q.patterns.map(String).slice(0, 200)); send(200, { ok: true }); });
  // Which model decides: "auto" = the backend's default, or one of the models the backend serves.
  if (req.method === 'POST' && req.url === '/api/settings') return readJson(async q => {
    const m = await models();
    if (q.mode !== 'auto' && !m.data.some(x => x.name === q.mode)) return send(400, { error: 'mode' });
    fs.mkdirSync(path.dirname(SETTINGS), { recursive: true }); fs.writeFileSync(SETTINGS, JSON.stringify({ mode: q.mode })); send(200, { ok: true });
  });
  // Tester: type a command, see the backend's verdict and every model's probability. Not logged.
  if (req.method === 'POST' && req.url === '/api/check') return readJson(async q => {
    const text = String(q.command || '').slice(0, 5000), t0 = process.hrtime.bigint();
    const c = await G.checkCommand(text); const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    send(200, { ...c, level: c.verdict, allowlisted: null, traits: traits(text), ms });
  });
  send(404, { error: 'not found' });
}).listen(PORT, '127.0.0.1', () => console.log(`KI-Guard dashboard on http://127.0.0.1:${PORT}/ (model backend: ${G.BACKEND})`));
