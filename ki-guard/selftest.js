// node selftest.js : rule + model verdicts. Dangerous strings are assembled from parts so antivirus does not flag this file.
'use strict';
const { evaluate } = require('./guard');
const { predict } = require('./model/predict');
const j = (...p) => p.join(' ');
const cases = [
  ['allow', 'ls -la'], ['allow', 'git status'], ['allow', 'npm test'],
  ['block', j('rm', '-rf', '/')], ['block', j('mkfs.ext4', '/dev/sda1')],
  ['block', j('dd', 'if=/dev/zero', 'of=/dev/sda')],
  ['ask', j('git', 'push', '--force')],
];
const rank = { allow: 0, ask: 1, block: 2 };
let ok = 0;
for (const [want, cmd] of cases) {
  const r = evaluate({ tool_name: 'Bash', tool_input: { command: cmd } }).level;
  const m = predict(cmd, r, 'bash').verdict;
  const got = rank[m] > rank[r] ? m : r;
  const pass = want === 'allow' ? got !== 'block' : rank[got] >= rank[want];
  ok += pass; console.log(pass ? 'ok  ' : 'FAIL', want.padEnd(5), got.padEnd(5), cmd);
}
console.log(`${ok}/${cases.length} passed`); process.exit(ok === cases.length ? 0 : 1);
