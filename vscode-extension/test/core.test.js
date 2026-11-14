"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");

const core = require("../src/core");
const { postJson } = require("../src/client");
const bridge = require("../src/bridge");
const { renderPanel } = require("../src/panel");

const ANSWER = {
  answers: {
    dangerous: { type: "noul", noul: 0.91 },
    requires_root: { type: "noul", noul: 0.12 },
    risk_level: { type: "score", probabilities: { 0: 0.02, 1: 0.03, 2: 0.05, 3: 0.2, 4: 0.7 } },
    unsafe: { type: "noul", noul: 0.95 },
  },
};

test("request carries the command as state and all four questions", () => {
  const body = core.buildRequest("rm -rf /");
  assert.deepStrictEqual(body.state, { command: "rm -rf /" });
  assert.deepStrictEqual(Object.keys(body.questions), ["dangerous", "requires_root", "risk_level", "unsafe"]);
  assert.throws(() => core.buildRequest("  "));
});

test("answers become the four fields", () => {
  const r = core.parseAnswers(ANSWER);
  assert.strictEqual(r.dangerous.yes, true);
  assert.strictEqual(r.root.yes, false);
  assert.strictEqual(r.risk.level, "critical");
  assert.strictEqual(core.verdictText(r), "UNSAFE");
  assert.strictEqual(core.severity(r), "error");
  assert.match(core.summaryLine(r), /UNSAFE · risk critical \(70%\) · dangerous yes \(91%\) · root no \(12%\)/);
});

test("missing or malformed answers are n/a, not guessed", () => {
  const r = core.parseAnswers({ answers: { dangerous: { noul: "x" }, risk_level: { probabilities: {} } } });
  assert.deepStrictEqual(r, { dangerous: null, root: null, risk: null, verdict: null });
  assert.strictEqual(core.verdictText(r), "N/A");
  assert.strictEqual(core.severity(r), "info");
});

test("a safe command reads as safe", () => {
  const r = core.parseAnswers({ answers: {
    dangerous: { noul: 0.02 }, requires_root: { noul: 0.01 }, unsafe: { noul: 0.03 },
    risk_level: { probabilities: { 0: 0.9, 1: 0.1 } } } });
  assert.strictEqual(core.verdictText(r), "SAFE");
  assert.strictEqual(core.severity(r), "info");
});

test("client posts to the app and reads its reply; errors are reported", async () => {
  let seen;
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      seen = JSON.parse(Buffer.concat(chunks));
      if (seen.state.command === "fail") { res.writeHead(500); res.end("boom"); return; }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(ANSWER));
    });
  });
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  const url = `http://127.0.0.1:${server.address().port}/v1/systemone`;
  try {
    const body = await postJson(url, core.buildRequest("sudo rm -rf /var"));
    assert.strictEqual(seen.state.command, "sudo rm -rf /var");
    assert.strictEqual(core.parseAnswers(body).risk.level, "critical");
    await assert.rejects(postJson(url, core.buildRequest("fail")), /HTTP 500/);
    await assert.rejects(postJson("http://127.0.0.1:9/v1/systemone", core.buildRequest("ls"), 500));
  } finally {
    server.close();
  }
});

test("panel escapes the command and shows all four fields", () => {
  const entry = { command: "echo '<script>x</script>'", source: "Claude Code", description: "Print a tag",
    at: new Date(), ms: 3, result: core.parseAnswers(ANSWER), severity: "error" };
  const older = { ...entry, command: "ls -la", at: new Date(0) };
  const html = renderPanel([entry, older], "http://127.0.0.1:8009/v1/systemone");
  assert.ok(!html.includes("<script>x"));
  for (const text of ["Look closely before you approve", "Risk level", "critical", "Dangerous", "Needs root access",
    "Claude Code wants to run", "Print a tag", "Earlier checks"]) assert.ok(html.includes(text), text);
});

test("panel explains an app that did not answer, and an empty history", () => {
  const html = renderPanel([{ command: "ls", source: "manual", at: new Date(), error: "connect ECONNREFUSED",
    severity: "offline", endpoint: "http://127.0.0.1:8009/v1/systemone" }], "x");
  assert.ok(html.includes("No second opinion") && html.includes("ECONNREFUSED"));
  assert.ok(renderPanel([], "x").includes("Waiting for a permission prompt"));
});

for (const source of ["Claude Code", "Cursor", "Codex"]) {
test(`listener takes a ${source} plugin event with its answer`, async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cmdgate-"));
  const events = [];
  const { server, token, port } = await bridge.startListener((e) => events.push(e));
  const file = bridge.writeDiscovery({ port, token, folders: [], home });
  try {
    // What cmdgate-claude-plugin/hooks/register.ts sends: it reads the discovery file for port and token.
    const win = JSON.parse(fs.readFileSync(file, "utf8"));
    const status = await new Promise((resolve) => {
      const req = http.request({ host: "127.0.0.1", port: win.port, path: "/event", method: "POST",
        headers: { "content-type": "application/json", "x-cmdgate-token": win.token } },
      (res) => { res.resume(); resolve(res.statusCode); });
      req.end(JSON.stringify({ source, tool: "Bash", command: "git push --force origin main",
        description: "Force-push main", result: ANSWER }));
    });
    assert.strictEqual(status, 202);
    assert.strictEqual(events.length, 1);
    assert.strictEqual(events[0].source, source);
    assert.strictEqual(events[0].description, "Force-push main");
    assert.strictEqual(core.parseAnswers(events[0].result).risk.level, "critical");
  } finally {
    bridge.removeDiscovery(file);
    server.close();
    fs.rmSync(home, { recursive: true, force: true });
  }
});
}

test("listener refuses requests without the window token", async () => {
  const { server, port } = await bridge.startListener(() => assert.fail("must not be called"));
  try {
    const status = await new Promise((resolve) => {
      const req = http.request({ host: "127.0.0.1", port, path: "/event", method: "POST",
        headers: { "content-type": "application/json" } }, (res) => { res.resume(); resolve(res.statusCode); });
      req.end(JSON.stringify({ command: "ls" }));
    });
    assert.strictEqual(status, 403);
  } finally {
    server.close();
  }
});
