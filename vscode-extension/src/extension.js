"use strict";
// Command Gate for VS Code: a second opinion on shell commands, answered by the
// locally running Command Gate app over the decision API.
//
// Where commands come from:
//   - Claude Code permission prompts, via the PermissionRequest hook (hook/cmdgate-hook.js):
//     only commands an agent asks you to approve, nothing that already ran
//   - the "Check command" and "Check selection" commands
// The add-on never approves, denies or blocks anything; it only shows the result.

const vscode = require("vscode");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFile } = require("child_process");

const core = require("./core");
const { postJson } = require("./client");
const bridge = require("./bridge");
const { renderPanel } = require("./panel");
const chatcard = require("./chatcard");

const HISTORY_MAX = 50;
const HOOK_MARK = "cmdgate-hook.js";

let history = [];
let panel = null;
let statusItem = null;
let output = null;
let cardFeed = null;

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

async function check(command, meta = {}) {
  const s = settings();
  const started = Date.now();
  const entry = { command, ...meta, at: new Date(), endpoint: s.endpoint };
  try {
    const body = await postJson(s.endpoint, core.buildRequest(command, { model: s.model, questions: s.questions }),
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
  if (cardFeed) cardFeed.push(entry);
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
  else if (mode === "off" && !cardFeed) mode = "always"; // with no card anywhere, the toast is the only way to see the result
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

// --- Claude Code hook install / remove ---------------------------------------

function projectTarget(folder) {
  return { label: `This project only (${path.basename(folder)})`,
    detail: `${path.join(folder, ".claude", "settings.local.json")} (personal, not committed)`,
    file: path.join(folder, ".claude", "settings.local.json") };
}

// Open workspace folders first, then "another project folder…", then the user-wide file.
async function pickHookTarget(placeHolder) {
  const targets = (vscode.workspace.workspaceFolders || []).map((f) => projectTarget(f.uri.fsPath));
  targets.push({ label: "Another project folder…", detail: "Pick the folder you start Claude Code in", browse: true });
  targets.push({ label: "This user (all projects)", detail: path.join(os.homedir(), ".claude", "settings.json"),
    file: path.join(os.homedir(), ".claude", "settings.json") });
  const picked = await vscode.window.showQuickPick(targets, { placeHolder });
  if (!picked || !picked.browse) return picked;
  const folders = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false,
    openLabel: "Use this project folder" });
  return folders && folders[0] ? projectTarget(folders[0].fsPath) : undefined;
}

function readSettingsFile(file) {
  if (!fs.existsSync(file)) return {};
  const text = fs.readFileSync(file, "utf8");
  if (!text.trim()) return {};
  return JSON.parse(text); // a parse error stops the edit: never overwrite a file we can't read
}

function withoutOurHook(groups) {
  return (groups || []).map((g) => ({ ...g, hooks: (g.hooks || []).filter((h) => !String(h.command || "").includes(HOOK_MARK)) }))
    .filter((g) => g.hooks.length);
}

function nodeAvailable() {
  return new Promise((resolve) => execFile("node", ["--version"], (err) => resolve(!err)));
}

async function installHook(context) {
  const target = await pickHookTarget("Where should the Claude Code hook go?");
  if (!target) return;
  if (!(await nodeAvailable())) {
    vscode.window.showWarningMessage("Command Gate: `node` is not on PATH. The hook needs Node.js to run; install it first.");
    return;
  }
  // Copy the hook to a stable place so add-on updates don't break the path in settings.
  const hookDir = path.join(os.homedir(), ".cmdgate");
  fs.mkdirSync(hookDir, { recursive: true });
  const hookPath = path.join(hookDir, HOOK_MARK);
  fs.copyFileSync(path.join(context.extensionPath, "hook", HOOK_MARK), hookPath);

  let settingsJson;
  try {
    settingsJson = readSettingsFile(target.file);
  } catch (err) {
    vscode.window.showErrorMessage(`Command Gate: ${target.file} is not valid JSON (${err.message}); fix it first.`);
    return;
  }
  settingsJson.hooks = settingsJson.hooks || {};
  const groups = withoutOurHook(settingsJson.hooks.PermissionRequest);
  groups.push({ matcher: "Bash|PowerShell", hooks: [{ type: "command", command: `node "${hookPath}"`, timeout: 5 }] });
  settingsJson.hooks.PermissionRequest = groups;
  fs.mkdirSync(path.dirname(target.file), { recursive: true });
  // Keep the first backup: it is the file as it was before the add-on ever touched it.
  const backup = `${target.file}.cmdgate-backup`;
  if (fs.existsSync(target.file) && !fs.existsSync(backup)) fs.copyFileSync(target.file, backup);
  fs.writeFileSync(target.file, `${JSON.stringify(settingsJson, null, 2)}\n`);
  const open = await vscode.window.showInformationMessage(
    `Command Gate: hook added to ${target.file}. Claude Code permission prompts for shell commands now show a second opinion here. Restart running Claude Code sessions to pick it up.`,
    "Open settings file");
  if (open) vscode.window.showTextDocument(vscode.Uri.file(target.file));
}

async function removeHook() {
  const target = await pickHookTarget("Remove the hook from where?");
  if (!target) return;
  if (!fs.existsSync(target.file)) {
    vscode.window.showInformationMessage(`Command Gate: ${target.file} does not exist, so there is no hook to remove.`);
    return;
  }
  let settingsJson;
  try {
    settingsJson = readSettingsFile(target.file);
  } catch (err) {
    vscode.window.showErrorMessage(`Command Gate: ${target.file} is not valid JSON (${err.message}).`);
    return;
  }
  if (!settingsJson.hooks || !settingsJson.hooks.PermissionRequest) {
    vscode.window.showInformationMessage(`Command Gate: no hook found in ${target.file}.`);
    return;
  }
  const groups = withoutOurHook(settingsJson.hooks.PermissionRequest);
  if (groups.length) settingsJson.hooks.PermissionRequest = groups;
  else delete settingsJson.hooks.PermissionRequest;
  if (!Object.keys(settingsJson.hooks).length) delete settingsJson.hooks;
  // Only our hook entry is removed; everything else in the file stays as it is.
  fs.writeFileSync(target.file, `${JSON.stringify(settingsJson, null, 2)}\n`);
  vscode.window.showInformationMessage(`Command Gate: hook removed from ${target.file}.`);
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
    vscode.commands.registerCommand("cmdgate.installClaudeHook", () => installHook(context)),
    vscode.commands.registerCommand("cmdgate.removeClaudeHook", removeHook),
  );

  // Listener for the Claude Code hook.
  try {
    const { server, token, port } = await bridge.startListener(async (event) => {
      const entry = await check(event.command, { source: event.source || "hook", description: event.description,
        cwd: event.cwd });
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
    output.appendLine(`could not start the hook listener: ${err.message}`);
  }

  // Opt-in feed for a chat webview that draws the card inside the chat (see src/chatcard.js).
  const cardPort = vscode.workspace.getConfiguration("cmdgate").get("chatCardPort");
  if (cardPort > 0) {
    try {
      cardFeed = await chatcard.startCardServer({ port: cardPort, getHistory: () => history });
      context.subscriptions.push({ dispose: () => cardFeed.server.close() });
      output.appendLine(`chat card feed on 127.0.0.1:${cardFeed.port}`);
    } catch (err) {
      output.appendLine(`could not start the chat card feed: ${err.message}`);
    }
  }
}

function deactivate() {}

module.exports = { activate, deactivate };
