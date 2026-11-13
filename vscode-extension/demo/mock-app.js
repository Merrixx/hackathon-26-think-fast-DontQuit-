#!/usr/bin/env node
"use strict";
// DEMO ONLY: a fake Command Gate app for showing the VS Code add-on while the
// real backend can't run. It answers the decision API with made-up
// probabilities from a few keyword rules. These are NOT model results and must
// never be reported as such; every reply says so in its `summary`.
//
//   node vscode-extension/demo/mock-app.js            # http://127.0.0.1:8009/v1/systemone
//   node vscode-extension/demo/mock-app.js --port 8010

const http = require("http");

const portArg = process.argv.indexOf("--port");
const PORT = portArg > 0 ? Number(process.argv[portArg + 1]) : 8009;

// [pattern, risk level 0-4, dangerous, needs root]
const RULES = [
  [/\brm\s+-[a-z]*r[a-z]*f?\s+(\/|~\/?|\$HOME\/?)(\s|$)/i, 4, true, false],
  [/\b(mkfs|dd\s+.*of=\/dev|wipefs|shred)\b/i, 4, true, true],
  [/:\(\)\s*\{\s*:\|:&\s*\};:/, 4, true, false],
  [/\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z)?sh\b/i, 3, true, false],
  [/\bgit\s+push\b.*(--force|-f)\b/i, 3, true, false],
  [/\bgit\s+reset\s+--hard\b/i, 3, true, false],
  [/\b(drop\s+(table|database)|truncate\s+table)\b/i, 3, true, false],
  [/(\.ssh\/|id_rsa|\.aws\/credentials|\/etc\/shadow)/i, 3, true, false],
  [/\bchmod\s+(-R\s+)?777\b/i, 3, true, false],
  [/\b(sudo|doas|su)\b/i, 2, false, true],
  [/\b(systemctl|service)\s+(stop|restart|disable)\b/i, 2, false, true],
  [/\brm\s+-[a-z]*r/i, 2, false, false],
  [/\b(npm|pip|apt|brew|choco|winget)\s+(install|i|uninstall|remove)\b/i, 1, false, false],
  [/\b(mv|cp|touch|mkdir|git\s+commit|git\s+checkout)\b/i, 1, false, false],
];

function fakeScores(command) {
  let level = 0;
  let dangerous = false;
  let root = /\bsudo\b/i.test(command);
  for (const [pattern, lvl, dang, needsRoot] of RULES) {
    if (pattern.test(command)) {
      level = Math.max(level, lvl);
      dangerous = dangerous || dang;
      root = root || needsRoot;
    }
  }
  // Peaked distribution around the chosen level, so the panel's bars look plausible.
  const weights = [0, 1, 2, 3, 4].map((i) => Math.exp(-1.6 * Math.abs(i - level)));
  const total = weights.reduce((a, b) => a + b, 0);
  const levels = weights.map((w) => w / total);
  return {
    levels,
    dangerous: dangerous ? 0.86 : level >= 2 ? 0.35 : 0.04,
    root: root ? 0.9 : 0.05,
    unsafe: levels.slice(2).reduce((a, b) => a + b, 0),
  };
}

function answer(body) {
  const command = typeof body.state === "string" ? body.state : body.state && body.state.command;
  if (typeof command !== "string" || !command.trim()) throw new Error('state must be {"command": "..."}');
  const s = fakeScores(command);
  const answers = {};
  for (const [id, q] of Object.entries(body.questions || {})) {
    if (q.type === "score") answers[id] = { type: "score", probabilities: Object.fromEntries(s.levels.map((p, i) => [String(i), p])) };
    else if (id.includes("root")) answers[id] = { type: "noul", noul: s.root };
    else if (id.includes("danger")) answers[id] = { type: "noul", noul: s.dangerous };
    else answers[id] = { type: "noul", noul: s.unsafe };
  }
  return { model: "DEMO mock-app", summary: "DEMO: fake answers from mock-app.js, not a model result", answers };
}

http.createServer((req, res) => {
  const send = (status, payload) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(payload));
  };
  if (req.method === "GET" && req.url === "/health") return send(200, { ok: true, demo: true });
  if (req.method !== "POST" || req.url !== "/v1/systemone") return send(404, { error: "not found" });
  const chunks = [];
  req.on("data", (c) => chunks.push(c));
  req.on("end", () => {
    try {
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
      const reply = answer(body);
      console.log(`${new Date().toLocaleTimeString()}  ${body.state && body.state.command}`);
      send(200, reply);
    } catch (err) {
      send(422, { error: err.message });
    }
  });
}).listen(PORT, "127.0.0.1", () => {
  console.log(`DEMO mock app (fake data) on http://127.0.0.1:${PORT}/v1/systemone — Ctrl+C to stop`);
});
