// node model/bench-api.js  (with `node serve.js` running): HTTP latency p50/p95 over holdout-style requests,
// plus one sample answer. Uses harmless commands only.
'use strict';
const http = require('http');
const L = require('./lib');
const PORT = +process.env.PORT || 8009;
const cmds = ['ls -la', 'git status', 'npm test', 'find . -name "*.log" -mtime +7', 'du -sh *', 'cat README.md | head'];
const body = c => JSON.stringify({ model: 'ki-guard', state: { command: c }, questions: {
  risky: { type: 'noul', instructions: 'Is this command risky?' },
  risk_level: { type: 'score', instructions: 'Risk level', criteria: ['low', 'mid', 'high'] },
  needs_root: { type: 'noul', instructions: 'Does it need root access?' },
  reversible: { type: 'noul', instructions: 'Can it be undone?' } } });
const agent = new http.Agent({ keepAlive: true });
function call(c) {
  return new Promise((ok, no) => {
    const t0 = process.hrtime.bigint();
    const req = http.request({ host: '127.0.0.1', port: PORT, path: '/v1/systemone', method: 'POST', agent, headers: { 'content-type': 'application/json' } }, res => {
      let d = ''; res.on('data', x => (d += x)); res.on('end', () => ok({ ms: Number(process.hrtime.bigint() - t0) / 1e6, json: JSON.parse(d) }));
    });
    req.on('error', no); req.end(body(c));
  });
}
(async () => {
  const lat = []; let sample;
  for (let i = 0; i < 2000; i++) { const r = await call(cmds[i % cmds.length]); lat.push(r.ms); sample ||= r.json; }
  console.log(JSON.stringify({ requests: lat.length, p50_ms: L.percentile(lat, 50), p95_ms: L.percentile(lat, 95), sample }, null, 2));
  agent.destroy();
})();
