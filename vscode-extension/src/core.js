"use strict";
// Pure logic, no VS Code imports: build the decision API request, read the
// answers, and turn them into the four things the add-on shows.

const LEVELS = ["safe", "low", "moderate", "high", "critical"];

// The questions sent with every command. IDs and wording can be overridden in
// the `cmdgate.questions` setting so they match what the local app answers.
const DEFAULT_QUESTIONS = {
  dangerous: {
    type: "noul",
    instructions: "Could this shell command cause serious damage, data loss or a security problem if it runs as written?",
  },
  requires_root: {
    type: "noul",
    instructions: "Does this shell command need root or administrator privileges to run?",
  },
  risk_level: {
    type: "score",
    instructions: "How much damage could this shell command do if it runs as written?",
    criteria: [
      "safe: read-only or routine, no plausible path to data loss, privilege change or disruption",
      "low: a local change that is quick to undo",
      "moderate: repairable without backups, with some effort",
      "high: needs backups to recover, exposes secrets to others, runs unverified code, or takes the machine down",
      "critical: destroys a disk, filesystem, system directory or home folder, or gives away root-level control",
    ],
  },
  unsafe: {
    type: "noul",
    instructions: "Should a person approve this shell command before an AI coding agent runs it?",
  },
};

// Which question answers which field on screen.
const DEFAULT_FIELDS = { dangerous: "dangerous", root: "requires_root", riskLevel: "risk_level", verdict: "unsafe" };

function buildRequest(command, { model = "local", questions = DEFAULT_QUESTIONS } = {}) {
  if (typeof command !== "string" || !command.trim()) throw new Error("command must be a non-empty string");
  return { model, state: { command }, questions };
}

function clamp01(x) {
  const n = Number(x);
  return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : null;
}

function readYesNo(answer, threshold) {
  if (!answer || typeof answer !== "object") return null;
  let p = clamp01(answer.noul);
  if (p === null && answer.probabilities) p = clamp01(answer.probabilities.true);
  return p === null ? null : { p, yes: p >= threshold };
}

function readLevel(answer, levels = LEVELS) {
  if (!answer || typeof answer !== "object" || !answer.probabilities) return null;
  const raw = levels.map((_, i) => Math.max(0, Number(answer.probabilities[String(i)]) || 0));
  const total = raw.reduce((a, b) => a + b, 0);
  if (total <= 0) return null;
  const probabilities = raw.map((v) => v / total);
  const index = probabilities.indexOf(Math.max(...probabilities));
  return { index, level: levels[index], p: probabilities[index], probabilities };
}

// `body` is the app's JSON reply. Missing or malformed answers become null and
// are shown as "n/a", never guessed.
function parseAnswers(body, { threshold = 0.5, fields = DEFAULT_FIELDS, levels = LEVELS } = {}) {
  const answers = (body && body.answers) || {};
  return {
    dangerous: readYesNo(answers[fields.dangerous], threshold),
    root: readYesNo(answers[fields.root], threshold),
    risk: readLevel(answers[fields.riskLevel], levels),
    verdict: readYesNo(answers[fields.verdict], threshold),
  };
}

function pct(p) {
  return `${Math.round(p * 100)}%`;
}

function yesNoText(v) {
  return v ? `${v.yes ? "yes" : "no"} (${pct(v.p)})` : "n/a";
}

function verdictText(result) {
  if (!result.verdict) return "N/A";
  return result.verdict.yes ? "UNSAFE" : "SAFE";
}

// One line for notifications and the status bar tooltip.
function summaryLine(result) {
  const risk = result.risk ? `${result.risk.level} (${pct(result.risk.p)})` : "n/a";
  return `${verdictText(result)} · risk ${risk} · dangerous ${yesNoText(result.dangerous)} · root ${yesNoText(result.root)}`;
}

// "error" for unsafe/dangerous/high+, "warning" for other flags, "info" otherwise.
function severity(result) {
  const high = result.risk && result.risk.index >= LEVELS.indexOf("high");
  if ((result.verdict && result.verdict.yes && (high || (result.dangerous && result.dangerous.yes)))) return "error";
  if ((result.verdict && result.verdict.yes) || (result.dangerous && result.dangerous.yes) || high ||
      (result.root && result.root.yes)) return "warning";
  return "info";
}

function shorten(text, max = 80) {
  const oneLine = String(text).replace(/\s+/g, " ").trim();
  return oneLine.length > max ? `${oneLine.slice(0, max - 1)}…` : oneLine;
}

module.exports = {
  LEVELS, DEFAULT_QUESTIONS, DEFAULT_FIELDS,
  buildRequest, parseAnswers, summaryLine, severity, verdictText, yesNoText, shorten, pct,
};
