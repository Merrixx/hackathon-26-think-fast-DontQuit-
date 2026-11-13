#!/usr/bin/env node
"use strict";
// Claude Code PermissionRequest hook: forward the command behind a permission
// prompt to the Command Gate add-on in VS Code, which shows a second opinion.
//
// It never makes the permission decision: it prints nothing and exits 0, so
// Claude Code shows its normal prompt. Any failure (no VS Code window, slow
// network) is swallowed for the same reason.

const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const TIMEOUT_MS = 1500;
const DIR = path.join(os.homedir(), ".cmdgate", "windows");

function readStdin() {
  return new Promise((resolve) => {
    const chunks = [];
    process.stdin.on("data", (c) => chunks.push(c));
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", () => resolve(""));
  });
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === "EPERM";
  }
}

function norm(p) {
  const r = path.resolve(p);
  return process.platform === "win32" ? r.toLowerCase() : r;
}

function inside(child, parent) {
  const rel = path.relative(norm(parent), norm(child));
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
}

// The live window whose workspace folder best contains cwd; else the most recent one.
function pickWindow(cwd) {
  let windows = [];
  try {
    windows = fs.readdirSync(DIR).filter((f) => f.endsWith(".json")).map((f) => {
      try {
        return JSON.parse(fs.readFileSync(path.join(DIR, f), "utf8"));
      } catch {
        return null;
      }
    }).filter((w) => w && w.port && w.token && alive(w.pid));
  } catch {
    return null;
  }
  let best = null;
  let bestLen = -1;
  for (const w of windows) {
    for (const folder of w.folders || []) {
      if (cwd && inside(cwd, folder) && folder.length > bestLen) {
        best = w;
        bestLen = folder.length;
      }
    }
  }
  return best || windows.sort((a, b) => (b.updated || 0) - (a.updated || 0))[0] || null;
}

function send(win, event) {
  return new Promise((resolve) => {
    const data = Buffer.from(JSON.stringify(event));
    const req = http.request({
      host: "127.0.0.1", port: win.port, path: "/event", method: "POST", timeout: TIMEOUT_MS,
      headers: { "content-type": "application/json", "content-length": data.length, "x-cmdgate-token": win.token },
    }, (res) => {
      res.resume();
      res.on("end", resolve);
    });
    req.on("timeout", () => req.destroy());
    req.on("error", resolve);
    req.end(data);
  });
}

async function main() {
  let input;
  try {
    input = JSON.parse(await readStdin());
  } catch {
    return;
  }
  const command = input && input.tool_input && input.tool_input.command;
  if (typeof command !== "string" || !command.trim()) return;
  const win = pickWindow(input.cwd);
  if (!win) return;
  await send(win, {
    source: "Claude Code",
    command,
    description: typeof input.tool_input.description === "string" ? input.tool_input.description : "",
    cwd: input.cwd || "",
    tool: input.tool_name || "",
    event: input.hook_event_name || "",
  });
}

main().catch(() => {}).finally(() => process.exit(0));
