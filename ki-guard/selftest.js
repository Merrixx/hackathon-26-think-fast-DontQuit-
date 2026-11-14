// node selftest.js : the frontend against a stand-in backend. It checks the wiring, not a model:
// the backend's verdict and per-model answers are passed through unchanged, and an unreachable backend reads as "ask".
'use strict';
const http = require('http');

const fake = http.createServer((req, res) => {
  let b = ''; req.on('data', d => (b += d));
  req.on('end', () => {
    const risky = /rm -rf/.test(JSON.parse(b || '{}').command || '');
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ model: 'all models', p: risky ? 0.99 : 0.01, verdict: risky ? 'ask' : 'allow', bucket: risky ? 'likely' : 'safe', reason: 'stand-in',
      models: { one: { p: risky ? 0.99 : 0.01, ms: 1, verdict: risky ? 'ask' : 'allow' } }, rules: [] }));
  });
});

fake.listen(0, '127.0.0.1', async () => {
  process.env.KI_GUARD_BACKEND = `http://127.0.0.1:${fake.address().port}`;
  const { checkCommand } = require('./guard');
  const cases = [['allow', 'backend allows', 'ls -la'], ['ask', 'backend asks', ['rm', '-rf', 'build'].join(' ')]];
  let ok = 0;
  for (const [want, name, command] of cases) {
    const r = await checkCommand(command); const good = r.verdict === want && r.models.one.verdict === want;
    ok += good; console.log(good ? 'ok  ' : 'FAIL', want.padEnd(5), String(r.verdict).padEnd(5), name);
  }
  fake.close();
  const off = await checkCommand('ls -la'); const good = off.verdict === 'ask' && off.offline === true;
  ok += good; console.log(good ? 'ok  ' : 'FAIL', 'ask  ', String(off.verdict).padEnd(5), 'backend unreachable');
  console.log(`${ok}/${cases.length + 1} passed`); process.exit(ok === cases.length + 1 ? 0 : 1);
});
