#!/usr/bin/env node
// KI-Guard: the hook and CLI in front of the command gate. It holds no model of its own.
// Every shell command goes to the Python backend (`python -m cmdgate.engine`, POST /v1/check), which answers with
// the probability from the models the eval harness scores, and a verdict from the cut-offs measured for them.
// What stays here: file-path and secret checks for write tools, your allowlist, and the decision log.
// Backend not reachable: the command is not waved through, a person is asked.
'use strict';
const fs = require('fs');
const path = require('path');
const os = require('os');
const http = require('http');

const BACKEND = process.env.KI_GUARD_BACKEND || 'http://127.0.0.1:8009';
const TIMEOUT_MS = +process.env.KI_GUARD_TIMEOUT_MS || 5000;
const DIR = path.join(os.homedir(), '.ki-guard');

// Files that should never be written by an AI without asking
const PATH_RULES = [
  ['block', /[\\/]\.ssh[\\/]|[\\/]windows[\\/]system32[\\/]|^\/(etc|boot|usr|bin|sbin)\//i, 'System or SSH file'],
  ['ask', /(^|[\\/])\.env(\.|$)|[\\/]\.claude[\\/]settings(\.local)?\.json$|[\\/]\.aws[\\/]|[\\/]\.gitconfig$|[\\/]\.(bash|zsh)rc$|[\\/]powershell[\\/].*profile\.ps1$|credentials/i, 'Secrets, shell profile or AI settings (could turn the guard off)'],
];

// Secrets inside written content
const SECRET_RE = /-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bxox[baprs]-[A-Za-z0-9-]{10,}/;

const RANK = { allow: 0, ask: 1, block: 2 };

function checkPath(p) {
  const hits = [];
  const n = String(p || '');
  for (const [level, re, why] of PATH_RULES) if (re.test(n)) hits.push({ level, id: 'path', why: `${why}: ${n}` });
  return hits;
}

// File-path and secret checks for write tools. Commands are not judged here.
function evaluate(input) {
  const hits = [];
  if (typeof input !== 'string') {
    const t = input.tool_name || '';
    const a = input.tool_input || {};
    const p = a.file_path || a.path || a.notebook_path;
    if (p && /write|edit|notebook|create|delete|move/i.test(t)) hits.push(...checkPath(p));
    const content = [a.content, a.new_string, a.new_source].filter(Boolean).join('\n');
    if (content && SECRET_RE.test(content)) hits.push({ level: 'ask', id: 'secret-in-file', why: 'Writes a password / API key into a file' });
  }
  const level = hits.reduce((m, h) => (RANK[h.level] > RANK[m] ? h.level : m), 'allow');
  return { level, hits };
}

// One JSON call to the backend. Rejects on any error, timeout or non-200 answer.
function backend(method, route, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const req = http.request(BACKEND + route, { method, timeout: TIMEOUT_MS, headers: data ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(data) } : {} }, res => {
      let raw = ''; res.on('data', d => (raw += d));
      res.on('end', () => { try { const j = JSON.parse(raw); res.statusCode === 200 ? resolve(j) : reject(new Error(j.error || 'backend error ' + res.statusCode)); } catch (e) { reject(e); } });
    });
    req.on('timeout', () => req.destroy(new Error('backend timed out')));
    req.on('error', reject);
    if (data) req.write(data);
    req.end();
  });
}

// Model chosen in the dashboard: ~/.ki-guard/settings.json { "mode": "<model name>" }. Missing or "auto" = the backend's default.
function mode() { try { const m = JSON.parse(fs.readFileSync(path.join(DIR, 'settings.json'), 'utf8')).mode; return typeof m === 'string' && m && m !== 'auto' ? m : null; } catch { return null; } }

// The backend's verdict for one command: { verdict, bucket, p, reason, model, models, rules, policy }.
// all = also return every model's own probability (the dashboard's tester).
async function checkCommand(command, all) {
  const body = { command: String(command).slice(0, 5000) };
  const m = mode(); if (m) body.model = m;
  if (all) body.all = true;
  try { return await backend('POST', '/v1/check', body); }
  catch (e) {
    if (m && /unknown model/.test(e.message)) { delete body.model; try { return await backend('POST', '/v1/check', body); } catch {} }
    return { offline: true, verdict: 'ask', bucket: 'unsure', p: null, model: null, models: {}, rules: [], reason: `Model backend not reachable at ${BACKEND} (${e.message})` };
  }
}

function log(entry) {
  if (process.env.KI_GUARD_NOLOG) return;
  try {
    fs.mkdirSync(DIR, { recursive: true });
    fs.appendFileSync(path.join(DIR, 'log.jsonl'), JSON.stringify({ t: new Date().toISOString(), ...entry }) + '\n');
  } catch {}
}

// User allowlist: ~/.ki-guard/allow.json = { "patterns": ["^git status", ...] } (regex, case-insensitive)
function allowed(text) {
  try {
    const a = JSON.parse(fs.readFileSync(path.join(DIR, 'allow.json'), 'utf8'));
    return (a.patterns || []).find(p => { try { return new RegExp(p, 'i').test(text); } catch { return false; } }) || null;
  } catch { return null; }
}

// One decision for any AI. bucket: blocked | likely (probably risky, ask) | unsure (ask) | safe | yours (your allowlist)
async function decide(input, agent) {
  const r = evaluate(input);
  const cmd = typeof input === 'string' ? input : input.tool_input && input.tool_input.command;
  const tool = typeof input === 'string' ? 'shell' : input.tool_name || 'shell';
  let p = null, model = null, bucket = r.level === 'block' ? 'blocked' : r.level === 'ask' ? 'likely' : 'safe';
  if (cmd) {
    const c = await checkCommand(cmd);
    p = c.p; model = c.model;
    if (RANK[c.verdict] > RANK[r.level]) { r.level = c.verdict; bucket = c.bucket; r.hits.push({ level: c.verdict, id: c.offline ? 'backend-offline' : 'model:' + c.model, why: c.reason }); }
  }
  const text = cmd || JSON.stringify(input.tool_input || input);
  const mine = r.level !== 'allow' && allowed(text);
  if (mine) { r.level = 'allow'; bucket = 'yours'; r.hits = [{ level: 'allow', id: 'allowlist', why: 'Allowed by you: ' + mine }]; }
  log({ level: r.level, bucket, agent: agent || 'claude-code', tool, text: String(text).slice(0, 300), p, model, hits: r.hits.map(h => h.id) });
  return r;
}

async function main() {
  const args = process.argv.slice(2);
  const ai = args.indexOf('--agent'); const agent = ai >= 0 ? args.splice(ai, 2)[1] : null;
  // CLI mode for any AI / script:  node guard.js check "rm -rf /" [--agent codex]  -> exit 0 allow, 1 ask, 2 block
  if (args[0] === 'check') {
    const r = await decide(args.slice(1).join(' '), agent || 'cli');
    console.log(`${r.level.toUpperCase()}${r.hits.map(h => `\n  - ${h.why} [${h.id}]`).join('')}`);
    process.exit(RANK[r.level]);
  }
  // Hook mode: JSON on stdin. Claude Code shape {tool_name, tool_input}; other AIs may send {command}.
  let raw = '';
  process.stdin.on('data', d => (raw += d));
  process.stdin.on('end', async () => {
    try {
      let input = JSON.parse(raw || '{}');
      if (!input.tool_input && input.command) input = { tool_name: input.tool || 'Bash', tool_input: { command: input.command } };
      const r = await decide(input, agent);
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
      process.exit(0); // malformed hook input: do not brick the agent
    }
  });
}

if (require.main === module) main();
module.exports = { evaluate, decide, allowed, checkCommand, backend, mode, BACKEND };
