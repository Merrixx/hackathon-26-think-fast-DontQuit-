"use strict";
// HTML for the second-opinion panel. The newest check is a full "slip": the
// command, one plain verdict, the five-step risk scale and the two yes/no facts.
// Older checks shrink to one line each. Only VS Code theme colours are used, so
// it follows light, dark and high-contrast themes. No scripts run in it.

const core = require("./core");

// Colour per risk level, from the theme's chart palette.
const LEVEL_COLOURS = [
  "var(--vscode-charts-green)",
  "var(--vscode-charts-blue)",
  "var(--vscode-charts-yellow)",
  "var(--vscode-charts-orange)",
  "var(--vscode-charts-red)",
];

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// The headline, from the reader's point of view: what to do at the prompt.
function headline(entry) {
  if (entry.error) return { tone: "offline", text: "No second opinion", sub: `The local app at ${entry.endpoint} did not answer: ${entry.error}. Start it, then check again.` };
  const v = entry.result.verdict;
  if (!v) return { tone: "na", text: "No verdict from the app", sub: "The app answered, but not the safe/unsafe question." };
  return v.yes
    ? { tone: entry.severity === "error" ? "stop" : "caution", text: "Look closely before you approve", sub: `The model rates this unsafe (${core.pct(v.p)}).` }
    : { tone: "go", text: "Looks safe to run", sub: `The model rates this safe (${core.pct(1 - v.p)}).` };
}

function scale(risk) {
  if (!risk) return `<p class="muted">No risk level from the app.</p>`;
  const steps = core.LEVELS.map((name, i) => {
    const p = risk.probabilities[i];
    const on = i === risk.index;
    return `<li class="step${on ? " on" : ""}" style="--c:${LEVEL_COLOURS[i]};--p:${Math.round(p * 100)}%">
      <span class="track"><span class="fill"></span></span>
      <span class="name">${esc(name)}</span><span class="prob">${core.pct(p)}</span></li>`;
  }).join("");
  return `<figure class="scale" aria-label="Risk level: ${esc(risk.level)}, ${core.pct(risk.p)}">
    <figcaption>Risk level <b style="color:${LEVEL_COLOURS[risk.index]}">${esc(risk.level)}</b></figcaption>
    <ol>${steps}</ol></figure>`;
}

function fact(label, v) {
  const value = v ? (v.yes ? "Yes" : "No") : "n/a";
  const tone = !v ? "na" : v.yes ? "yes" : "no";
  return `<div class="fact ${tone}"><dt>${esc(label)}</dt><dd>${value}${v ? ` <small>${core.pct(v.p)}</small>` : ""}</dd></div>`;
}

function who(entry) {
  const source = entry.source === "Claude Code" ? "Claude Code wants to run" : entry.source === "manual" ? "You asked about" : "Checked from your selection";
  return source;
}

function slip(entry) {
  const h = headline(entry);
  const about = entry.description || entry.summary;
  const r = entry.result;
  return `<article class="slip ${h.tone}">
    <header><span>${esc(who(entry))}</span><time>${esc(entry.at.toLocaleTimeString())}</time></header>
    <pre class="command"><code>${esc(entry.command)}</code></pre>
    ${about ? `<p class="about">${esc(about)}</p>` : ""}
    <h1>${esc(h.text)}</h1>
    <p class="sub">${esc(h.sub)}</p>
    ${r ? `${scale(r.risk)}<dl class="facts">${fact("Dangerous", r.dangerous)}${fact("Needs root access", r.root)}</dl>` : ""}
  </article>`;
}

function row(entry) {
  const h = headline(entry);
  const level = entry.result && entry.result.risk ? entry.result.risk.level : "";
  return `<li class="row ${h.tone}"><span class="dot"></span><code>${esc(core.shorten(entry.command, 70))}</code>
    <span class="lvl">${esc(level)}</span><time>${esc(entry.at.toLocaleTimeString())}</time></li>`;
}

function renderPanel(history, endpoint) {
  const [latest, ...older] = history;
  const body = latest
    ? `${slip(latest)}${older.length ? `<section class="earlier"><h2>Earlier checks</h2><ol>${older.map(row).join("")}</ol></section>` : ""}`
    : `<div class="empty"><h1>Waiting for a permission prompt</h1>
       <p>When Claude Code asks to run a shell command, the second opinion appears here.
       To try it now, run <b>Command Gate: Check command…</b> from the Command Palette.</p></div>`;
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
  :root {
    --go: var(--vscode-testing-iconPassed, var(--vscode-charts-green));
    --caution: var(--vscode-editorWarning-foreground);
    --stop: var(--vscode-errorForeground);
    --quiet: var(--vscode-descriptionForeground);
    --line: var(--vscode-widget-border, var(--vscode-panel-border));
    --hc: var(--vscode-contrastBorder, transparent);
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 20px 22px 28px; color: var(--vscode-foreground); background: var(--vscode-editor-background);
         font: 13px/1.5 var(--vscode-font-family); }
  .slip { --tone: var(--quiet); max-width: 62ch; }
  .slip.go { --tone: var(--go); } .slip.caution { --tone: var(--caution); } .slip.stop { --tone: var(--stop); }
  header { display: flex; justify-content: space-between; color: var(--quiet); font-size: 12px; }
  .command { margin: 8px 0 0; padding: 12px 14px; border-left: 3px solid var(--tone); outline: 1px solid var(--hc);
             background: var(--vscode-textCodeBlock-background); white-space: pre-wrap; word-break: break-word;
             font: 14px/1.45 var(--vscode-editor-font-family); }
  .about { margin: 6px 0 0; color: var(--quiet); }
  h1 { margin: 22px 0 2px; font-size: 22px; line-height: 1.2; font-weight: 650; letter-spacing: -0.01em; color: var(--tone); }
  .sub { margin: 0; color: var(--quiet); }
  .scale { margin: 22px 0 0; }
  figcaption { font-size: 12px; color: var(--quiet); margin-bottom: 8px; }
  figcaption b { font-weight: 600; }
  .scale ol { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px; }
  .step { display: grid; grid-template-rows: auto auto auto; font-size: 11.5px; color: var(--quiet); }
  .track { height: 8px; background: color-mix(in srgb, var(--c) 18%, transparent); outline: 1px solid var(--hc); position: relative; overflow: hidden; }
  .step:first-child .track { border-radius: 4px 0 0 4px; } .step:last-child .track { border-radius: 0 4px 4px 0; }
  .fill { position: absolute; inset: 0 auto 0 0; width: var(--p); background: var(--c); opacity: .55; }
  .step.on .track { height: 14px; margin-top: -3px; } .step.on .fill { opacity: 1; }
  .step.on .name { color: var(--vscode-foreground); font-weight: 600; }
  .name { margin-top: 6px; } .prob { font-variant-numeric: tabular-nums; }
  .step.on .fill { animation: grow .5s ease-out; }
  @keyframes grow { from { width: 0; } }
  @media (prefers-reduced-motion: reduce) { .step.on .fill { animation: none; } }
  .facts { display: flex; gap: 28px; margin: 20px 0 0; }
  .fact dt { font-size: 12px; color: var(--quiet); } .fact dd { margin: 0; font-size: 15px; font-weight: 600; }
  .fact small { font-weight: 400; color: var(--quiet); font-variant-numeric: tabular-nums; }
  .fact.yes dd { color: var(--stop); } .fact.na dd { color: var(--quiet); }
  .earlier { margin-top: 34px; max-width: 62ch; border-top: 1px solid var(--line); padding-top: 12px; }
  h2 { font-size: 12px; font-weight: 600; color: var(--quiet); margin: 0 0 6px; }
  .earlier ol { list-style: none; margin: 0; padding: 0; }
  .row { display: grid; grid-template-columns: 10px 1fr auto auto; gap: 10px; align-items: baseline; padding: 4px 0; }
  .row code { font-family: var(--vscode-editor-font-family); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .lvl, .row time { color: var(--quiet); font-size: 11.5px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--quiet); outline: 1px solid var(--hc); }
  .row.go .dot { background: var(--go); } .row.caution .dot { background: var(--caution); } .row.stop .dot { background: var(--stop); }
  .muted { color: var(--quiet); }
  .empty { max-width: 52ch; } .empty h1 { color: var(--vscode-foreground); }
  footer { margin-top: 28px; font-size: 11.5px; color: var(--quiet); max-width: 62ch; }
</style></head><body>
${body}
<footer>Advice only: your answer in the permission prompt decides. Answers come from the local app at ${esc(endpoint)}.</footer>
</body></html>`;
}

module.exports = { renderPanel, esc };
