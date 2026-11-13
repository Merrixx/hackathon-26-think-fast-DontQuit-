#!/usr/bin/env node
// KI-Guard: one-file safety hook for Claude Code (PreToolUse) and any other AI agent.
// Verdicts: BLOCK (never run), ASK (user must confirm), ALLOW.
// Fail-open: if anything inside the guard breaks, the action is allowed (never bricks your agent).
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');

// [level, id, regex, reason]  — checked against shell commands
const CMD_RULES = [
  // --- BLOCK: destroys system / disk / everything ---
  ['block', 'rm-root', /\brm\s+(-[a-z]*r[a-z]*f?|-[a-z]*f[a-z]*r|--recursive)[^\n;|&]*\s(\/|\/\*|~|~\/|\$HOME|\/home|\/Users|[a-z]:[\\/]?)(\s|$|;|&|\|)/i, 'Deletes the whole drive / home folder'],
  ['block', 'ps-rm-root', /\b(remove-item|ri)\b(?=[^\n;|]*-recurse)[^\n;|]*\s['"]?([a-z]:\\?|\$env:userprofile|~|\$home|c:\\users|c:\\windows)['"]?(\s|$|;|\|)/i, 'Deletes a drive, Windows or user folder'],
  ['block', 'cmd-del-root', /\b(del|erase)\s+\/[sq].*\s[a-z]:\\(\*|\s|$)|\brd\s+\/s\s+\/q\s+[a-z]:\\?(\s|$)/i, 'Deletes a whole drive'],
  ['block', 'format-disk', /\b(mkfs(\.\w+)?|format-volume|clear-disk|initialize-disk|diskpart|wipefs)\b|\bformat\s+[a-z]:(\s|$|\/)/i, 'Formats / wipes a disk'],
  ['block', 'dd-disk', /\bdd\b[^\n]*\bof=\/dev\/(sd|nvme|hd|disk|mmcblk)/i, 'Overwrites a raw disk'],
  ['block', 'fork-bomb', /:\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:/, 'Fork bomb (freezes the PC)'],
  ['block', 'shadow-delete', /\b(vssadmin\s+delete\s+shadows|wbadmin\s+delete|bcdedit\b|cipher\s+\/w)/i, 'Deletes backups / boot config (ransomware pattern)'],
  ['block', 'chmod-root', /\bchmod\s+(-R\s+)?[0-7]*777\s+\/(\s|$)|\bchown\s+-R\s+\S+\s+\/(\s|$)/i, 'Breaks permissions of the whole system'],
  ['block', 'reg-hklm-delete', /\breg\s+delete\s+hklm|\bremove-item\b[^\n]*hklm:/i, 'Deletes system registry keys'],
  ['block', 'disable-defender', /set-mppreference[^\n]*-disable\w*\s+\$?true|\bnetsh\s+advfirewall\s+set\s+\w+\s+state\s+off/i, 'Turns off antivirus / firewall'],
  ['block', 'secret-exfil', /(\.env|id_rsa|id_ed25519|\.ssh[\\/]|credentials|\.aws[\\/]|token)[^\n]*\|\s*(curl|wget|nc|iwr|invoke-webrequest)|\b(curl|wget)\b[^\n]*(-d|--data|-F|--upload-file|-T)\s*@?\S*(\.env|id_rsa|id_ed25519|credentials)/i, 'Sends secrets / keys to the internet'],
  // --- ASK: risky, but sometimes wanted ---
  ['ask', 'pipe-to-shell', /\b(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^\n]*\|\s*(sudo\s+)?(sh|bash|zsh|python\d?|node|iex|invoke-expression)\b/i, 'Runs code straight from the internet'],
  ['ask', 'rm-recursive', /\brm\s+-[a-z]*r|\bremove-item\b[^\n]*-recurse|\brd\s+\/s|\brmdir\s+\/s|\bdel\s+\/s/i, 'Deletes folders recursively'],
  ['ask', 'git-force', /\bgit\s+push\b[^\n]*(--force\b|-f\b|--force-with-lease)|\bgit\s+(reset\s+--hard|clean\s+-[a-z]*f|checkout\s+--\s|branch\s+-D|filter-branch|reflog\s+expire)/i, 'Rewrites / throws away git history or changes'],
  ['ask', 'sql-drop', /\b(drop\s+(database|table|schema)|truncate\s+table|delete\s+from\s+\w+\s*(;|$))/i, 'Deletes a database / table'],
  ['ask', 'shutdown', /\b(shutdown|reboot|restart-computer|stop-computer|poweroff|halt)\b/i, 'Shuts down / restarts the machine'],
  ['ask', 'kill-all', /\b(taskkill\s+\/f\s+\/im|killall|pkill\s+-9|stop-process\b[^\n]*-force)/i, 'Force-kills programs'],
  ['ask', 'sudo', /\bsudo\b|\brunas\b|start-process\b[^\n]*-verb\s+runas/i, 'Runs as admin'],
  ['ask', 'publish', /\b(npm\s+publish|docker\s+push|gh\s+(release\s+create|pr\s+merge|repo\s+delete)|twine\s+upload|cargo\s+publish)\b/i, 'Publishes something to the outside world'],
  ['ask', 'remote-shell', /\b(ssh|scp|rsync)\b[^\n]*\b(rm|mv|systemctl|reboot|drop|truncate)\b/i, 'Changes things on a remote server'],
  ['ask', 'env-dump', /\b(printenv|env|set|get-childitem\s+env:|gci\s+env:)\s*($|\|)/i, 'Prints all environment variables (may contain secrets)'],
  ['ask', 'encoded-cmd', /-(e|enc|encodedcommand)\s+[a-z0-9+\/=]{20,}|base64\s+(-d|--decode)[^\n]*\|\s*(sh|bash)/i, 'Hidden / encoded command'],
];

// Files that should never be written by an AI without asking
const PATH_RULES = [
  ['block', /[\\/]\.ssh[\\/]|[\\/]windows[\\/]system32[\\/]|^\/(etc|boot|usr|bin|sbin)\//i, 'System or SSH file'],
  ['ask', /(^|[\\/])\.env(\.|$)|[\\/]\.claude[\\/]settings(\.local)?\.json$|[\\/]\.aws[\\/]|[\\/]\.gitconfig$|[\\/]\.(bash|zsh)rc$|[\\/]powershell[\\/].*profile\.ps1$|credentials/i, 'Secrets, shell profile or AI settings (could turn the guard off)'],
];

// Secrets inside written content
const SECRET_RE = /-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bxox[baprs]-[A-Za-z0-9-]{10,}/;

const RANK = { allow: 0, ask: 1, block: 2 };

function checkCommand(cmd) {
  const hits = [];
  for (const [level, id, re, why] of CMD_RULES) if (re.test(cmd)) hits.push({ level, id, why });
  return hits;
}
function checkPath(p) {
  const hits = [];
  const n = String(p || '');
  for (const [level, re, why] of PATH_RULES) if (re.test(n)) hits.push({ level, id: 'path', why: `${why}: ${n}` });
  return hits;
}

// Works on Claude Code tool calls and on plain text (other AIs / CLI)
function evaluate(input) {
  let hits = [];
  if (typeof input === 'string') hits = checkCommand(input);
  else {
    const t = input.tool_name || '';
    const a = input.tool_input || {};
    if (a.command) hits.push(...checkCommand(String(a.command)));
    const p = a.file_path || a.path || a.notebook_path;
    if (p && /write|edit|notebook|create|delete|move/i.test(t)) hits.push(...checkPath(p));
    const content = [a.content, a.new_string, a.new_source].filter(Boolean).join('\n');
    if (content && SECRET_RE.test(content)) hits.push({ level: 'ask', id: 'secret-in-file', why: 'Writes a password / API key into a file' });
  }
  const level = hits.reduce((m, h) => (RANK[h.level] > RANK[m] ? h.level : m), 'allow');
  return { level, hits };
}

function log(entry) {
  if (process.env.KI_GUARD_NOLOG) return;
  try {
    const dir = path.join(os.homedir(), '.ki-guard');
    fs.mkdirSync(dir, { recursive: true });
    fs.appendFileSync(path.join(dir, 'log.jsonl'), JSON.stringify({ t: new Date().toISOString(), ...entry }) + '\n');
  } catch {}
}

// User allowlist: ~/.ki-guard/allow.json = { "patterns": ["^git status", ...] } (regex, case-insensitive)
function allowed(text) {
  try {
    const a = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.ki-guard', 'allow.json'), 'utf8'));
    return (a.patterns || []).find(p => { try { return new RegExp(p, 'i').test(text); } catch { return false; } }) || null;
  } catch { return null; }
}

// One decision for any AI. bucket: blocked | likely (probably dangerous, ask) | unsure (ask, low signal) | safe | yours (your allowlist)
function decide(input, agent) {
  const r = evaluate(input);
  const cmd = typeof input === 'string' ? input : input.tool_input && input.tool_input.command;
  const tool = typeof input === 'string' ? 'shell' : input.tool_name || 'shell';
  let p = null;
  if (cmd) {
    try {
      const m = require('./model/predict').predict(String(cmd), r.level, /^(bash|shell)$/i.test(tool) ? 'bash' : 'other');
      p = m.p;
      if (RANK[m.verdict] > RANK[r.level]) { r.level = m.verdict; r.hits.push({ level: m.verdict, id: 'model', why: `Model risk ${(m.p * 100).toFixed(1)} %` }); }
    } catch {}
  }
  const text = cmd || JSON.stringify(input.tool_input || input);
  const mine = r.level !== 'allow' && allowed(text);
  if (mine) { r.level = 'allow'; r.hits = [{ level: 'allow', id: 'allowlist', why: 'Allowed by you: ' + mine }]; }
  const ruleHit = r.hits.some(h => h.id !== 'model' && h.level !== 'allow');
  const bucket = mine ? 'yours' : r.level === 'block' ? 'blocked' : r.level === 'allow' ? 'safe' : ruleHit || (p ?? 0) >= 0.3 ? 'likely' : 'unsure';
  log({ level: r.level, bucket, agent: agent || 'claude-code', tool, text: String(text).slice(0, 300), p, hits: r.hits.map(h => h.id) });
  return r;
}

function main() {
  const args = process.argv.slice(2);
  const ai = args.indexOf('--agent'); const agent = ai >= 0 ? args.splice(ai, 2)[1] : null;
  // CLI mode for any AI / script:  node guard.js check "rm -rf /" [--agent codex]  -> exit 0 allow, 1 ask, 2 block
  if (args[0] === 'check') {
    const r = decide(args.slice(1).join(' '), agent || 'cli');
    console.log(`${r.level.toUpperCase()}${r.hits.map(h => `\n  - ${h.why} [${h.id}]`).join('')}`);
    process.exit(RANK[r.level]);
  }
  // Hook mode: JSON on stdin. Claude Code shape {tool_name, tool_input}; other AIs may send {command}.
  let raw = '';
  process.stdin.on('data', d => (raw += d));
  process.stdin.on('end', () => {
    try {
      let input = JSON.parse(raw || '{}');
      if (!input.tool_input && input.command) input = { tool_name: input.tool || 'Bash', tool_input: { command: input.command } };
      const r = decide(input, agent);
      if (r.level === 'allow') return process.exit(0);
      const reason = 'KI-Guard: ' + r.hits.filter(h => h.level === r.level).map(h => h.why).join(' · ');
      process.stdout.write(JSON.stringify({
        hookSpecificOutput: {
          hookEventName: 'PreToolUse',
          permissionDecision: r.level === 'block' ? 'deny' : 'ask',
          permissionDecisionReason: reason,
        },
      }));
      process.exit(0);
    } catch {
      process.exit(0); // fail-open
    }
  });
}

if (require.main === module) main();
module.exports = { evaluate, decide, allowed };
