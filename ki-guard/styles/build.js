// Builds the Tailwind CSS for dashboard.html and writes it into the page's <style> block.
// The block is emptied first, so Tailwind only scans the markup, never its own output.
'use strict';
const fs = require('fs'), path = require('path'), os = require('os'), { execSync } = require('child_process');
const page = path.join(__dirname, '..', 'dashboard.html');
const A = '/* tw:start */', Z = '/* tw:end */';
const html = fs.readFileSync(page, 'utf8'), i = html.indexOf(A), j = html.indexOf(Z);
if (i < 0 || j < i) throw new Error('style markers missing in dashboard.html');
const put = css => fs.writeFileSync(page, html.slice(0, i + A.length) + css + html.slice(j));
put('');
const out = path.join(os.tmpdir(), `ki-guard-dashboard-${process.pid}.css`);
try {
  execSync(`npx tailwindcss -i "${path.join(__dirname, 'dashboard.css')}" -o "${out}" --minify`, { cwd: path.join(__dirname, '..'), stdio: 'inherit' });
  put(fs.readFileSync(out, 'utf8').trim());
} catch (e) { put(html.slice(i + A.length, j)); throw e; }
finally { fs.rmSync(out, { force: true }); }
console.log('dashboard.html: CSS updated');
