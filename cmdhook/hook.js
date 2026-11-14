#!/usr/bin/env node
"use strict";
// Claude Code PreToolUse hook for shell commands. It holds no model and no threshold: it sends the command to the
// hook backend (`python -m cmdhook.start`, POST /v1/hook) and passes the backend's decision on to Claude Code.
//
//   hook mode   Claude Code pipes the tool call as JSON on stdin; the answer is JSON on stdout.
//   check mode  node hook.js check "<command>"   prints the scores per model; exit 0 allow/defer, 1 ask, 2 deny.
//
// Settings come from the .env file next to this script; a variable in the environment wins.
// Node built-ins only, so there is nothing to install.

const fs = require("fs");
const http = require("http");
const path = require("path");

function settings() {
  const values = {};
  try {
    for (const line of fs.readFileSync(path.join(__dirname, ".env"), "utf8").split(/\r?\n/)) {
      const match = /^\s*(?:export\s+)?(CMDHOOK_\w+)\s*=\s*(.*?)\s*$/.exec(line);
      if (match) values[match[1]] = match[2].replace(/^(["'])(.*)\1$/, "$2");
    }
  } catch {
    // no .env file: the defaults and the environment apply
  }
  const get = (key, fallback) => process.env[key] || values[key] || fallback;
  const host = get("CMDHOOK_HOST", "127.0.0.1");
  const onError = get("CMDHOOK_ON_ERROR", "ask");
  return {
    host: host === "0.0.0.0" ? "127.0.0.1" : host,
    port: Number(get("CMDHOOK_PORT", "8019")),
    timeoutMs: Number(get("CMDHOOK_TIMEOUT_MS", "5000")),
    onError: ["ask", "defer", "deny"].includes(onError) ? onError : "ask",
    quiet: ["1", "true", "yes"].includes(get("CMDHOOK_QUIET", "0").toLowerCase()),
  };
}

function ask(cfg, body) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(body));
    const req = http.request({
      host: cfg.host, port: cfg.port, path: "/v1/hook", method: "POST", timeout: cfg.timeoutMs,
      headers: { "content-type": "application/json", "content-length": data.length },
    }, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => {
        try {
          const answer = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (res.statusCode === 200) resolve(answer);
          else reject(new Error(answer.error || `HTTP ${res.statusCode}`));
        } catch {
          reject(new Error(`invalid answer, HTTP ${res.statusCode}`));
        }
      });
    });
    req.on("timeout", () => req.destroy(new Error(`no answer within ${cfg.timeoutMs} ms`)));
    req.on("error", reject);
    req.end(data);
  });
}

// The backend's answer, or the CMDHOOK_ON_ERROR decision when it cannot be reached.
async function decide(cfg, command, cwd) {
  try {
    return await ask(cfg, { command, cwd });
  } catch (err) {
    const reason = `backend at http://${cfg.host}:${cfg.port} did not answer (${err.message})`;
    return { decision: cfg.onError, reason, summary: `cmdhook: ${cfg.onError.toUpperCase()} - ${reason}`, offline: true };
  }
}

// What Claude Code reads on stdout. `defer` leaves the decision to Claude Code's own permission rules.
function claudeOutput(answer, quiet) {
  const out = {};
  if (answer.decision !== "defer") {
    out.hookSpecificOutput = {
      hookEventName: "PreToolUse",
      permissionDecision: answer.decision,
      permissionDecisionReason: answer.summary,
    };
  }
  // An ask or a deny already shows the reason; for the rest the scores go to the user as a message.
  // A backend that did not answer is always reported, also with CMDHOOK_QUIET.
  const shown = !quiet || answer.offline;
  if (shown && (answer.decision === "allow" || answer.decision === "defer")) out.systemMessage = answer.summary;
  return out;
}

function readStdin() {
  return new Promise((resolve) => {
    const chunks = [];
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", () => resolve(""));
  });
}

async function main() {
  const cfg = settings();
  const args = process.argv.slice(2);
  if (args[0] === "check") {
    const answer = await decide(cfg, args.slice(1).join(" "), process.cwd());
    console.log(answer.summary);
    process.exit({ allow: 0, defer: 0, ask: 1, deny: 2 }[answer.decision]);
  }
  let input;
  try {
    input = JSON.parse(await readStdin());
  } catch {
    return; // malformed hook input: stay out of the way
  }
  const command = input && input.tool_input && input.tool_input.command;
  if (typeof command !== "string" || !command.trim()) return; // not a shell command
  const answer = await decide(cfg, command, input.cwd || "");
  process.stdout.write(JSON.stringify(claudeOutput(answer, cfg.quiet)));
}

if (require.main === module) main().catch(() => {}).finally(() => process.exit(process.exitCode || 0));
module.exports = { settings, claudeOutput };
