"use strict";
// Command Gate for VS Code: a second opinion on shell commands, answered by the
// locally running Command Gate app over the decision API.
//
// Where commands come from:
//   - Claude Code permission prompts, forwarded by the Command Gate Claude Code
//     plugin (cmdgate-plugin/) together with the app's answer: only commands an
//     agent asks you to approve, nothing that already ran
//   - the "Check command" and "Check selection" commands, which ask the app directly
// The add-on never approves, denies or blocks anything; it only shows the result.

const vscode = require("vscode");

const core = require("./core");
const { postJson } = require("./client");
const bridge = require("./bridge");
const { renderPanel } = require("./panel");

const HISTORY_MAX = 50;

let history = [];
let panel = null;
let statusItem = null;
let output = null;

function settings() {
  const c = vscode.workspace.getConfiguration("cmdgate");
  return {
    endpoint: c.get("endpoint"),
    model: c.get("model"),
    timeoutMs: c.get("timeoutMs"),
    threshold: c.get("threshold"),
    questions: c.get("questions") || core.DEFAULT_QUESTIONS,
    fields: { ...core.DEFAULT_FIELDS, ...(c.get("fields") || {}) },
    notifyOnPrompt: c.get("notifyOnPrompt"),
    showCard: c.get("showCard"),
  };
}

// `answer` is the app's reply when the Claude Code plugin already asked it;
// otherwise the add-on asks the app itself.
async function check(command, meta = {}, answer = null) {
  const s = settings();
  const started = Date.now();
  const entry = { command, ...meta, at: new Date(), endpoint: s.endpoint };
  try {
    const body = answer || await postJson(s.endpoint, core.buildRequest(command, { model: s.model, questions: s.questions }),
      s.timeoutMs);
    entry.result = core.parseAnswers(body, { threshold: s.threshold, fields: s.fields });
    entry.summary = typeof body.summary === "string" ? body.summary : "";
    entry.ms = Date.now() - started;
    entry.severity = core.severity(entry.result);
  } catch (err) {
    entry.error = err.message;
    entry.severity = "offline";
  }
  history.unshift(entry);
  history = history.slice(0, HISTORY_MAX);
  updateStatus(entry);
  refreshPanel();
  output.appendLine(`[${entry.at.toISOString()}] ${meta.source || "manual"}: ${command}`);
  output.appendLine(`    ${entry.error ? `error: ${entry.error}` : `${core.summaryLine(entry.result)} (${entry.ms} ms)`}`);
  return entry;
}

function updateStatus(entry) {
  const icons = { error: "$(error)", warning: "$(warning)", info: "$(pass)", offline: "$(debug-disconnect)" };
  const label = entry.error ? "app offline" : core.verdictText(entry.result);
  statusItem.text = `${icons[entry.severity]} Gate: ${label}`;
  statusItem.tooltip = entry.error
    ? `No answer from ${entry.endpoint}: ${entry.error}`
    : `${core.shorten(entry.command, 120)}\n${core.summaryLine(entry.result)}`;
  statusItem.backgroundColor = entry.severity === "error"
    ? new vscode.ThemeColor("statusBarItem.errorBackground")
    : entry.severity === "warning" ? new vscode.ThemeColor("statusBarItem.warningBackground") : undefined;
  statusItem.show();
}

// The styled card opens beside the editor without taking focus; the plain
// VS Code toast (which extensions cannot style) is optional on top.
function present(entry, mode) {
  if (settings().showCard) showPanel();
  else if (mode === "off") mode = "always"; // with the card off, the toast is the only way to see the result
  notify(entry, mode);
}

async function notify(entry, mode) {
  if (mode === "off") return;
  if (mode === "flagged" && !["error", "warning", "offline"].includes(entry.severity)) return;
  const what = entry.description ? `${entry.description} — ` : "";
  const text = entry.error
    ? `Command Gate: no second opinion for \`${core.shorten(entry.command, 60)}\` (${entry.error}).`
    : `Command Gate ${entry.source ? `(${entry.source}) ` : ""}— ${what}\`${core.shorten(entry.command, 60)}\`: `
      + core.summaryLine(entry.result);
  const show = entry.severity === "error" ? vscode.window.showErrorMessage
    : ["warning", "offline"].includes(entry.severity) ? vscode.window.showWarningMessage
      : vscode.window.showInformationMessage;
  if ((await show(text, "Details")) === "Details") showPanel();
}

function showPanel() {
  if (panel) {
    panel.reveal(vscode.ViewColumn.Beside, true);
  } else {
    panel = vscode.window.createWebviewPanel("cmdgate", "Second opinion", { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      { enableScripts: false });
    panel.iconPath = new vscode.ThemeIcon("shield");
    panel.onDidDispose(() => { panel = null; });
  }
  refreshPanel();
}

function refreshPanel() {
  if (panel) panel.webview.html = renderPanel(history, settings().endpoint);
}

// --- activation --------------------------------------------------------------

async function activate(context) {
  output = vscode.window.createOutputChannel("Command Gate");
  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  statusItem.command = "cmdgate.showPanel";
  statusItem.text = "$(shield) Gate";
  statusItem.tooltip = "Command Gate: second opinion on shell commands";
  statusItem.show();
  context.subscriptions.push(output, statusItem);

  context.subscriptions.push(
    vscode.commands.registerCommand("cmdgate.checkCommand", async () => {
      const command = await vscode.window.showInputBox({ prompt: "Shell command to check", placeHolder: "rm -rf build/" });
      if (command) present(await check(command, { source: "manual" }), "off");
    }),
    vscode.commands.registerCommand("cmdgate.checkSelection", async () => {
      const editor = vscode.window.activeTextEditor;
      const text = editor && editor.document.getText(editor.selection).trim();
      if (!text) {
        vscode.window.showInformationMessage("Command Gate: select a command first.");
        return;
      }
      present(await check(text, { source: "selection" }), "off");
    }),
    vscode.commands.registerCommand("cmdgate.showPanel", showPanel),
  );

  // Listener for the Claude Code plugin (and any other tool that speaks its protocol).
  try {
    const { server, token, port } = await bridge.startListener(async (event) => {
      const answer = event.result && typeof event.result === "object" ? event.result : null;
      const entry = await check(event.command, { source: event.source || "hook", description: event.description,
        cwd: event.cwd }, answer);
      present(entry, settings().notifyOnPrompt);
    });
    const folders = () => (vscode.workspace.workspaceFolders || []).map((f) => f.uri.fsPath);
    let file = bridge.writeDiscovery({ port, token, folders: folders() });
    context.subscriptions.push(
      vscode.workspace.onDidChangeWorkspaceFolders(() => {
        bridge.removeDiscovery(file);
        file = bridge.writeDiscovery({ port, token, folders: folders() });
      }),
      { dispose: () => { bridge.removeDiscovery(file); server.close(); } },
    );
    output.appendLine(`listening for Claude Code permission prompts on 127.0.0.1:${port}`);
  } catch (err) {
    output.appendLine(`could not start the listener for the Claude Code plugin: ${err.message}`);
  }
}

function deactivate() {}

module.exports = { activate, deactivate };
