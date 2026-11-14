"use strict";
// Optional card feed for a patched chat webview (opt-in, setting cmdgate.chatCardPort).
// A script injected into a chat webview can ask "what did Command Gate say about this
// command?" and draw the answer next to the Allow/Deny prompt itself.
//
// GET /explain?cmd=<command>&lang=<code>  ->  200 JSON breakdown in that language.
// GET /card?cmd=<command>  ->  200 JSON card, or 204 when no check finished within the wait.
// Only answers webview origins (vscode-webview://), so a website open in a browser
// cannot read the commands. Binds to 127.0.0.1 only. Never approves or denies anything.

const http = require("http");
const { headline } = require("./panel");
const { explain } = require("./explain");

const WAIT_MS = 8000;
const FRESH_MS = 5 * 60 * 1000;

const same = (a, b) => String(a).trim() === String(b).trim();

// The small, flat shape the chat card needs. No HTML: the injected script sets textContent.
function cardData(entry) {
  const h = headline(entry);
  const r = entry.result || {};
  const fact = (label, v) => ({ label, value: v ? (v.yes ? "yes" : "no") : "n/a", yes: !!(v && v.yes) });
  return {
    tone: h.tone,
    word: h.word,
    text: h.text,
    sub: h.sub || "",
    main: h.main != null ? Math.round(h.main * 100) : null,
    mainLabel: h.mainLabel || "",
    level: r.risk ? r.risk.level : null,
    levelIndex: r.risk ? r.risk.index : null,
    facts: entry.error ? [] : [fact("Dangerous", r.dangerous), fact("Needs root", r.root)],
    ms: entry.ms != null ? entry.ms : null,
  };
}

function startCardServer({ port, getHistory, waitMs = WAIT_MS, host = "127.0.0.1" }) {
  const waiters = new Set();

  const find = (cmd) => getHistory().find((e) => same(e.command, cmd) && Date.now() - new Date(e.at).getTime() < FRESH_MS);

  const server = http.createServer((req, res) => {
    const origin = req.headers.origin || "";
    if (!origin.startsWith("vscode-webview://")) {
      res.writeHead(403).end();
      return;
    }
    const cors = { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Private-Network": "true", Vary: "Origin" };
    if (req.method === "OPTIONS") {
      res.writeHead(204, { ...cors, "Access-Control-Allow-Methods": "GET" }).end();
      return;
    }
    const url = new URL(req.url, "http://127.0.0.1");
    const cmd = url.searchParams.get("cmd");
    // GET /explain?cmd=&lang= -> plain-language breakdown (offline dictionary, no model call)
    if (req.method === "GET" && url.pathname === "/explain" && cmd) {
      res.writeHead(200, { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" })
        .end(JSON.stringify(explain(cmd, url.searchParams.get("lang") || "en")));
      return;
    }
    if (req.method !== "GET" || url.pathname !== "/card" || !cmd) {
      res.writeHead(404, cors).end();
      return;
    }
    const send = (entry) => {
      if (res.writableEnded) return;
      if (!entry) res.writeHead(204, cors).end();
      else res.writeHead(200, { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" }).end(JSON.stringify(cardData(entry)));
    };
    // A finished check answers at once; otherwise wait for the hook's check to land.
    const hit = find(cmd);
    if (hit) return send(hit);
    const waiter = { cmd, send };
    waiters.add(waiter);
    const timer = setTimeout(() => { waiters.delete(waiter); send(null); }, waitMs);
    req.on("close", () => { clearTimeout(timer); waiters.delete(waiter); });
  });

  // Call after every finished check.
  const push = (entry) => {
    for (const w of [...waiters]) if (same(w.cmd, entry.command)) { waiters.delete(w); w.send(entry); }
  };

  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, host, () => resolve({ server, push, port: server.address().port }));
  });
}

module.exports = { startCardServer, cardData };
