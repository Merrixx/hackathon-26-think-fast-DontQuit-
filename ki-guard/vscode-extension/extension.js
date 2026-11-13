// KI-Guard for VS Code: starts the local server (127.0.0.1 only) and shows the dashboard in a panel.
'use strict';
const vscode = require('vscode'), http = require('http'), path = require('path'), fs = require('fs'), { spawn } = require('child_process');
let server = null, bar = null;

const cfg = () => vscode.workspace.getConfiguration('kiGuard');
function root() {
  const p = cfg().get('path');
  if (p && fs.existsSync(path.join(p, 'serve.js'))) return p;
  const guess = [path.join(__dirname, '..'), path.join(require('os').homedir(), 'OneDrive', 'Desktop', '01-Projekte', 'KI-Guard')];
  return guess.find(g => fs.existsSync(path.join(g, 'serve.js')));
}
const get = (port, u) => new Promise(ok => { const r = http.get({ host: '127.0.0.1', port, path: u, timeout: 800 }, res => { let d = ''; res.on('data', x => (d += x)); res.on('end', () => ok(res.statusCode === 200 ? d : null)); }); r.on('error', () => ok(null)); r.on('timeout', () => { r.destroy(); ok(null); }); });

async function ensure() {
  const port = cfg().get('port');
  if (await get(port, '/api/stats')) return port;
  const dir = root(); if (!dir) { vscode.window.showErrorMessage('KI-Guard: set "kiGuard.path" to the folder with serve.js'); return null; }
  server = spawn('node', ['serve.js'], { cwd: dir, env: { ...process.env, PORT: String(port) }, stdio: 'ignore', windowsHide: true });
  for (let i = 0; i < 30; i++) { if (await get(port, '/api/stats')) return port; await new Promise(r => setTimeout(r, 200)); }
  vscode.window.showErrorMessage('KI-Guard: server did not start (is Node installed?)'); return null;
}

async function open() {
  const port = await ensure(); if (!port) return;
  const panel = vscode.window.createWebviewPanel('kiGuard', 'KI-Guard', vscode.ViewColumn.One, { enableScripts: true, retainContextWhenHidden: true });
  const url = `http://127.0.0.1:${port}/dashboard`;
  panel.webview.html = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; frame-src http://127.0.0.1:${port}; style-src 'unsafe-inline'">
    <style>html,body,iframe{margin:0;padding:0;border:0;width:100%;height:100vh;background:#0c0c0e}</style></head><body><iframe src="${url}"></iframe></body></html>`;
}

async function refreshBar() {
  const s = await get(cfg().get('port'), '/api/stats'); if (!s) { bar.text = '$(shield) KI-Guard'; return; }
  const b = JSON.parse(s).buckets;
  bar.text = `$(shield) ${b.blocked} blocked · ${b.likely + b.unsure} asked`;
  bar.tooltip = `KI-Guard\nBlocked ${b.blocked}\nLikely risky ${b.likely}\nUnsure ${b.unsure}\nSafe ${b.safe}\nAllowed by you ${b.yours}`;
}

function activate(ctx) {
  ctx.subscriptions.push(vscode.commands.registerCommand('kiGuard.open', open));
  bar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  bar.command = 'kiGuard.open'; bar.text = '$(shield) KI-Guard'; bar.show(); ctx.subscriptions.push(bar);
  // Refresh the counter when the guard log changes (event-based, no polling loop).
  const log = path.join(require('os').homedir(), '.ki-guard', 'log.jsonl');
  try { fs.mkdirSync(path.dirname(log), { recursive: true }); fs.watch(path.dirname(log), () => refreshBar()); } catch {}
  ensure().then(refreshBar);
}
function deactivate() { if (server) server.kill(); }
module.exports = { activate, deactivate };
