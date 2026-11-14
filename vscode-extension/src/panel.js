"use strict";
// HTML for the second-opinion panel. Built to help at a glance, not to distract:
// one traffic-light status (Stop / Careful / Safe), one main number, the command,
// one risk bar and two short facts. Older checks shrink to one line each.
// Liquid-glass look: a soft backdrop glows in the status colour and shows through
// layered glass (panel, darker inner card, pills). Everything else is silver.
// No scripts run in it. Styling is Tailwind: classes below, theme and the few
// custom pieces (glass edge, glow, orb) in styles/panel.css. The built CSS is
// inlined because the webview's CSP only allows inline styles.

const fs = require("fs");
const path = require("path");
const core = require("./core");

const CSS = fs.readFileSync(path.join(__dirname, "..", "media", "panel.css"), "utf8");

// Glass layers: panel > darker inner card > pills. High contrast: no glass, solid outline.
const HC = "hc:bg-transparent hc:backdrop-filter-none hc:outline hc:outline-hc hc:before:hidden";
const GLASS = `glass-edge bg-tint backdrop-blur-[5px] backdrop-saturate-130 shadow-[0_10px_60px_rgba(15,15,15,.06)] ${HC}`;
const PILL = `glass-edge inline-flex items-center gap-1 rounded-[18px] bg-pill px-3 py-1 backdrop-blur-[20px] lift hover:scale-105 hover:bg-hov ${HC}`;
const GREY = ["offline", "na", "ready"];
// Written out in full so Tailwind finds them when it scans this file
const TONE = { stop: "tone-stop", caution: "tone-caution", go: "tone-go", offline: "tone-offline", na: "tone-na", ready: "tone-ready" };

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// What to do at the prompt, in one word and one short line.
function headline(entry) {
  if (entry.error) return { tone: "offline", word: "Offline", text: "No second opinion", sub: `The local app did not answer (${entry.error}). Start it, then check again.` };
  const v = entry.result.verdict;
  if (!v) return { tone: "na", word: "Unsure", text: "No verdict from the app", sub: "The app answered, but not the safe/unsafe question." };
  if (!v.yes) return { tone: "go", word: "Safe", text: "Looks safe to run", sub: "Nothing risky found.", main: 1 - v.p, mainLabel: "safe" };
  const r = entry.result, flags = [r.dangerous && r.dangerous.yes && "can do damage", r.root && r.root.yes && "needs root"].filter(Boolean);
  const why = flags.length ? `It ${flags.join(" and ")}.` : "Not clearly dangerous, but not clearly safe either.";
  return entry.severity === "error"
    ? { tone: "stop", word: "Stop", text: "Look closely before you approve", sub: why, main: v.p, mainLabel: "unsafe" }
    : { tone: "caution", word: "Careful", text: "Look closely before you approve", sub: why, main: v.p, mainLabel: "unsafe" };
}

const ICONS = {
  stop: '<path d="M8 8l8 8M16 8l-8 8"/>',
  caution: '<path d="M12 7.5v5.5"/><circle cx="12" cy="16.5" r=".8" fill="currentColor"/>',
  go: '<path d="M7.5 12.5l3 3 6-6.5"/>',
  offline: '<path d="M8 12h8"/>',
  na: '<path d="M10 10a2.2 2.2 0 1 1 2.9 2.1c-.5.2-.9.6-.9 1.1v.3"/><circle cx="12" cy="16.5" r=".8" fill="currentColor"/>',
  ready: '<path d="M12 4 6 6.3v4.6c0 3.8 2.6 6.6 6 7.6 3.4-1 6-3.8 6-7.6V6.3z"/>',
};

// One bar, five steps, the chosen step marked. Each step shows its number below it.
function scale(risk) {
  if (!risk) return "";
  const step = (i) => i === risk.index ? "bg-tone shadow-[0_0_8px_color-mix(in_srgb,var(--c)_55%,transparent)]"
    : i < risk.index ? "bg-tone/45" : "bg-track hc:bg-transparent hc:outline hc:outline-hc";
  const steps = core.LEVELS.map((name, i) => `<span class="grid gap-[3px]" title="${esc(name)}: ${core.pct(risk.probabilities[i])}"><i class="h-1.5 rounded-[3px] ${step(i)}"></i><small class="text-center text-[10px]/[1.2] tabular-nums ${i === risk.index ? "font-semibold text-ink" : "text-quiet"}">${core.pct(risk.probabilities[i])}</small></span>`).join("");
  return `<div class="mx-1 mt-[14px] flex items-start gap-[10px] text-[12px]" aria-label="Risk level: ${esc(risk.level)}"><span class="text-quiet">Risk level</span><span class="grid flex-1 grid-cols-5 gap-[3px]">${steps}</span><b class="min-w-[58px] text-right capitalize">${esc(risk.level)}</b></div>`;
}

function fact(label, v) {
  const value = v ? (v.yes ? "yes" : "no") : "n/a";
  return `<span class="${PILL} text-[12px] text-quiet" title="${v ? core.pct(v.p) : "no answer"}">${esc(label)}: <b class="font-semibold ${v && v.yes ? "text-tone" : "text-ink"}">${value}</b>${v ? ` <span class="tabular-nums">(${core.pct(v.p)})</span>` : ""}</span>`;
}

// One pill per backend model: its own risk, and whether it passed its threshold.
function model(m) {
  const state = m.failed ? "no answer" : m.passed ? "pass" : "fail";
  const title = m.failed ? "no answer" : `risk ${core.pct(m.p)}${m.threshold === null ? "" : `, threshold ${core.pct(m.threshold)}`}`;
  return `<span class="${PILL} text-[12px] text-quiet" title="${esc(title)}">${esc(m.name)}: <b class="font-semibold ${m.failed || m.passed ? "text-ink" : "text-tone"}">${state}</b>${m.failed ? "" : ` <span class="tabular-nums">(${core.pct(m.p)})</span>`}</span>`;
}

function models(r) {
  if (!r.models || !r.models.length) return "";
  const head = r.decision ? `${r.decision.decision.toUpperCase()}${r.decision.reason ? ` · ${r.decision.reason}` : ""}` : "";
  return `<div class="mx-1 mt-3 text-[12px] text-quiet">Models${head ? `: ${esc(head)}` : ""}</div><div class="mt-1.5 flex flex-wrap gap-1.5">${r.models.map(model).join("")}</div>`;
}

function who(entry) {
  return entry.source === "Claude Code" ? "Claude Code wants to run" : entry.source === "manual" ? "You asked about" : "Checked from your selection";
}

const badge = (tone) => `<span class="orb relative grid size-[42px] flex-none place-items-center rounded-full ${tone === "caution" ? "text-[#1d1d1f]" : "text-white"}">
  <svg class="size-6 fill-none stroke-current stroke-[2.6] [stroke-linecap:round] [stroke-linejoin:round]" viewBox="0 0 24 24" aria-hidden="true">${ICONS[tone]}</svg></span>`;

const TOP = "flex items-center gap-3";
const WORD = "text-[22px] font-bold leading-[1.1] tracking-[-.01em]";
const SUB = "mx-[2px] mt-[10px] text-quiet";
const MAIN = `${GLASS} animate-rise rounded-[50px] px-[22px] py-5`;

function slip(entry, h) {
  const about = entry.description || entry.summary;
  const r = entry.result;
  return `<article class="${MAIN}">
    <div class="${TOP}">${badge(h.tone)}<div class="min-w-0 flex-1"><div class="${WORD} ${GREY.includes(h.tone) ? "text-ink" : "text-tone"}">${esc(h.word)}</div><div class="text-[13px] font-semibold">${esc(h.text)}</div></div>
      ${h.main != null ? `<div class="text-right leading-none"><b class="text-[28px] font-bold tabular-nums">${Math.round(h.main * 100)}<small class="ml-px text-[14px] font-semibold">%</small></b><span class="mt-[3px] block text-[11px] text-quiet">${h.mainLabel}</span></div>` : ""}</div>
    <p class="${SUB}">${esc(h.sub)}</p>
    <div class="glass-edge mt-3 rounded-[26px] bg-card px-[14px] pt-[10px] pb-3 ${HC}">
      <div class="flex justify-between text-[12px] text-quiet"><span>${esc(who(entry))}</span><time>${esc(entry.at.toLocaleTimeString())}</time></div>
      <pre class="mt-1 font-mono text-[13px]/[1.45] whitespace-pre-wrap [word-break:break-word]"><code class="font-mono">${esc(entry.command)}</code></pre>
      ${about ? `<p class="mt-1.5 text-[12px] text-quiet">${esc(about)}</p>` : ""}
    </div>
    ${r ? `${scale(r.risk)}<div class="mt-3 flex flex-wrap gap-1.5">${fact("Dangerous", r.dangerous)}${fact("Needs root access", r.root)}</div>${models(r)}` : ""}
  </article>`;
}

function row(entry) {
  const h = headline(entry);
  return `<li class="${TONE[h.tone]} grid grid-cols-[8px_minmax(0,1fr)_auto_auto] items-baseline gap-[10px] rounded-[15px] px-2 py-1.5 lift hover:scale-[1.025] hover:bg-hov [&+&]:shadow-[0_-1px_0_var(--color-line)]"><span class="size-2 self-start mt-[6px] rounded-full bg-tone shadow-[0_0_6px_color-mix(in_srgb,var(--c)_60%,transparent)]"></span><code class="font-mono text-[12px] whitespace-pre-wrap [word-break:break-word]">${esc(entry.command)}</code>
    <span class="text-[12px] text-quiet">${esc(h.word)}</span><time class="text-[12px] text-quiet">${esc(entry.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</time></li>`;
}

function renderPanel(history, endpoint) {
  const [latest, ...older] = history;
  const tone = latest ? headline(latest).tone : "ready";
  const body = latest
    ? `${slip(latest, headline(latest))}${older.length ? `<section class="${GLASS} rounded-[34px] px-[18px] pt-[14px] pb-[10px]"><h2 class="${PILL} mb-1.5 text-[12px] font-semibold">Earlier checks</h2><ol class="list-none">${older.map(row).join("")}</ol></section>` : ""}`
    : `<article class="${MAIN}"><div class="${TOP}">${badge("ready")}<div class="min-w-0 flex-1"><div class="${WORD} text-ink">Ready</div>
       <div class="text-[13px] font-semibold">Waiting for a permission prompt</div></div></div>
       <p class="${SUB}">When Claude Code asks to run a shell command, the second opinion appears here.
       To try it now, run <b>Command Gate: Check command…</b> from the Command Palette.</p></article>`;
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>${CSS}</style></head><body class="overflow-x-hidden bg-base font-sans text-[13px]/[1.45] text-ink">
<main class="${TONE[tone]} stage-glow grid min-h-screen content-start gap-3 px-4 pt-[18px] pb-6 *:w-full *:max-w-[560px] hc:bg-none hc:bg-[var(--vscode-editor-background,#000)]">
${body}
<footer class="px-2 text-[11.5px] text-quiet">Advice only: your answer in the permission prompt decides. Answers come from the local app at ${esc(endpoint)}.</footer>
</main>
</body></html>`;
}

module.exports = { renderPanel, esc, headline };
