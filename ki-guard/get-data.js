// Downloads the two public datasets at pinned commits into data/ (not committed).
// RedCode (AI-secure/RedCode): dataset CC BY 4.0, code MIT.  NL2Bash (TellinaTool/nl2bash): data/bash MIT, code GPL-3.0.
'use strict';
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const SOURCES = [
  { name: 'RedCode', url: 'https://github.com/AI-secure/RedCode.git', commit: '664e4793dc7309f98b8271baed5bbc04d6ef4c39' },
  { name: 'nl2bash', url: 'https://github.com/TellinaTool/nl2bash.git', commit: 'd6b9f5bdff45621d190134e31ab63b7bf7002190' },
];
const dir = path.join(__dirname, 'data');
fs.mkdirSync(dir, { recursive: true });
for (const s of SOURCES) {
  const dest = path.join(dir, s.name);
  if (!fs.existsSync(dest)) execFileSync('git', ['clone', '-q', s.url, dest], { stdio: 'inherit' });
  execFileSync('git', ['-C', dest, 'checkout', '-q', s.commit], { stdio: 'inherit' });
  console.log(`${s.name} @ ${s.commit.slice(0, 10)}`);
}
