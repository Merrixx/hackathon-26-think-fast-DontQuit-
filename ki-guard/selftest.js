// node selftest.js : the hook against a stand-in backend. It checks the wiring, not a model:
// the backend's verdict is passed through, an unreachable backend asks, and the file-path checks still block.
'use strict';
const http = require('http');
process.env.KI_GUARD_NOLOG = '1';

const fake = http.createServer((req, res) => {
  let b = ''; req.on('data', d => (b += d));
  req.on('end', () => {
    const risky = /rm -rf/.test(JSON.parse(b || '{}').command || '');
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ model: 'fake', p: risky ? 0.99 : 0.01, verdict: risky ? 'ask' : 'allow', bucket: risky ? 'likely' : 'safe', reason: 'stand-in', models: {}, rules: [] }));
  });
});

fake.listen(0, '127.0.0.1', async () => {
  process.env.KI_GUARD_BACKEND = `http://127.0.0.1:${fake.address().port}`;
  const { decide } = require('./guard');
  const bash = command => ({ tool_name: 'Bash', tool_input: { command } });
  const cases = [
    ['allow', 'backend allows', bash('ls -la')],
    ['ask', 'backend asks', bash(['rm', '-rf', 'build'].join(' '))],
    ['block', 'write into .ssh', { tool_name: 'Write', tool_input: { file_path: '/home/me/.ssh/authorized_keys', content: 'x' } }],
    ['ask', 'write .env', { tool_name: 'Write', tool_input: { file_path: 'app/.env', content: 'x' } }],
  ];
  let ok = 0;
  for (const [want, name, input] of cases) { const got = (await decide(input, 'selftest')).level; ok += got === want; console.log(got === want ? 'ok  ' : 'FAIL', want.padEnd(5), got.padEnd(5), name); }
  fake.close();
  // Same hook, backend gone: a command must go to a person, never straight through.
  const off = (await decide(bash('ls -la'), 'selftest')).level;
  ok += off === 'ask'; console.log(off === 'ask' ? 'ok  ' : 'FAIL', 'ask  ', off.padEnd(5), 'backend unreachable');
  console.log(`${ok}/${cases.length + 1} passed`); process.exit(ok === cases.length + 1 ? 0 : 1);
});
