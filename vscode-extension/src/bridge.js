"use strict";
// Local listener the Claude Code hook forwards permission prompts to.
//
// Each VS Code window listens on 127.0.0.1 on a random port and writes a small
// discovery file (port, token, workspace folders) to ~/.cmdgate/windows/. The
// hook reads those files, picks the window whose folder contains the command's
// cwd, and POSTs the event with the window's token. Nothing listens beyond
// localhost, and requests without the token are refused.

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const MAX_BODY = 64 * 1024;

function discoveryDir(home = os.homedir()) {
  return path.join(home, ".cmdgate", "windows");
}

function startListener(onEvent) {
  const token = crypto.randomBytes(24).toString("hex");
  const server = http.createServer((req, res) => {
    const reply = (status, payload) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    };
    if (req.method !== "POST" || req.url !== "/event") return reply(404, { error: "not found" });
    if (req.headers["x-cmdgate-token"] !== token) return reply(403, { error: "bad token" });
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) req.destroy();
      else chunks.push(c);
    });
    req.on("end", () => {
      let event;
      try {
        event = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } catch {
        return reply(400, { error: "invalid JSON" });
      }
      if (typeof event.command !== "string" || !event.command.trim()) return reply(400, { error: "no command" });
      reply(202, { ok: true }); // answer the hook at once; the check runs in the window
      onEvent(event);
    });
  });
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve({ server, token, port: server.address().port }));
  });
}

function writeDiscovery({ port, token, folders, home }) {
  const dir = discoveryDir(home);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${process.pid}-${port}.json`);
  fs.writeFileSync(file, JSON.stringify({ port, token, folders, pid: process.pid, updated: Date.now() }),
    { mode: 0o600 });
  return file;
}

function removeDiscovery(file) {
  try {
    fs.unlinkSync(file);
  } catch {
    // already gone
  }
}

module.exports = { startListener, writeDiscovery, removeDiscovery, discoveryDir };
