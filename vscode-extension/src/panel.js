"use strict";
// HTML for the second-opinion panel. Built to help at a glance, not to distract:
// one traffic-light status (Stop / Careful / Safe), one main number, the command,
// one risk bar and two short facts. Older checks shrink to one line each.
// Calm glass cards that follow the VS Code theme (dark, light, high contrast).
// Colour is used only for the status. No scripts run in it.

const core = require("./core");

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

// One bar, five steps, the chosen step marked. Exact numbers sit in the tooltip.
function scale(risk) {
  if (!risk) return "";
  const steps = core.LEVELS.map((name, i) => `<i class="s${i}${i === risk.index ? " on" : ""}${i <= risk.index ? " lit" : ""}" title="${esc(name)}: ${core.pct(risk.probabilities[i])}"></i>`).join("");
  return `<div class="scale" aria-label="Risk level: ${esc(risk.level)}"><span class="lbl">Risk level</span><span class="steps">${steps}</span><b>${esc(risk.level)}</b></div>`;
}

function fact(label, v) {
  const value = v ? (v.yes ? "yes" : "no") : "n/a";
  return `<span class="fact ${v && v.yes ? "yes" : ""}" title="${v ? core.pct(v.p) : "no answer"}">${esc(label)}: <b>${value}</b></span>`;
}

function who(entry) {
  return entry.source === "Claude Code" ? "Claude Code wants to run" : entry.source === "manual" ? "You asked about" : "Checked from your selection";
}

const status = (h, small) => `<span class="dot${small ? " sm" : ""}"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[h.tone]}</svg></span>`;

function slip(entry) {
  const h = headline(entry);
  const about = entry.description || entry.summary;
  const r = entry.result;
  return `<article class="card glass t-${h.tone}">
    <div class="top">${status(h)}<div class="say"><div class="word">${esc(h.word)}</div><div class="text">${esc(h.text)}</div></div>
      ${h.main != null ? `<div class="num"><b>${Math.round(h.main * 100)}<small>%</small></b><span>${h.mainLabel}</span></div>` : ""}</div>
    <p class="sub">${esc(h.sub)}</p>
    <div class="meta"><span>${esc(who(entry))}</span><time>${esc(entry.at.toLocaleTimeString())}</time></div>
    <pre class="command"><code>${esc(entry.command)}</code></pre>
    ${about ? `<p class="about">${esc(about)}</p>` : ""}
    ${r ? `${scale(r.risk)}<div class="facts">${fact("Dangerous", r.dangerous)}${fact("Needs root access", r.root)}</div>` : ""}
  </article>`;
}

function row(entry) {
  const h = headline(entry);
  return `<li class="row t-${h.tone}"><span class="pin"></span><code>${esc(core.shorten(entry.command, 70))}</code>
    <span class="lvl">${esc(h.word)}</span><time>${esc(entry.at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }))}</time></li>`;
}

function renderPanel(history, endpoint) {
  const [latest, ...older] = history;
  const body = latest
    ? `${slip(latest)}${older.length ? `<section class="earlier glass"><h2>Earlier checks</h2><ol>${older.slice(0, 6).map(row).join("")}</ol></section>` : ""}`
    : `<article class="card glass t-ready"><div class="top">${status({ tone: "ready" })}<div class="say"><div class="word">Ready</div>
       <div class="text">Waiting for a permission prompt</div></div></div>
       <p class="sub">When Claude Code asks to run a shell command, the second opinion appears here.
       To try it now, run <b>Command Gate: Check command…</b> from the Command Palette.</p></article>`;
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
  :root {
    --ink: var(--vscode-foreground, #e8e8ea);
    --quiet: var(--vscode-descriptionForeground, rgba(255,255,255,.6));
    --bg: var(--vscode-editor-background, #1f1f1f);
    --tint: rgba(255,255,255,.04); --deep: rgba(0,0,0,.24); --line: rgba(255,255,255,.08);
    --rim: linear-gradient(180deg,rgba(181,181,181,.38),rgba(48,48,48,.28) 30%,rgba(16,16,16,.28) 65%,rgba(172,172,172,.28));
    --red: #f2555a; --amber: #f0b429; --green: #3ecf8e; --grey: #8b8b90;
  }
  body.vscode-light {
    --tint: rgba(0,0,0,.025); --deep: rgba(0,0,0,.045); --line: rgba(0,0,0,.08);
    --rim: linear-gradient(180deg,#fff,rgba(48,48,48,.14) 35%,rgba(16,16,16,.12) 65%,#fff);
    --red: #d92d3a; --amber: #b7791f; --green: #1f9d63; --grey: #75757a;
  }
  * { box-sizing: border-box; }
  html, body { overflow-x: hidden; }
  body { margin: 0; padding: 16px 18px 24px; color: var(--ink); background: var(--bg); font: 13px/1.5 var(--vscode-font-family); }
  .card, .earlier, footer { max-width: 560px; }

  .t-stop { --c: var(--red); } .t-caution { --c: var(--amber); } .t-go { --c: var(--green); }
  .t-offline, .t-na, .t-ready { --c: var(--grey); }

  .glass { position: relative; isolation: isolate; background: var(--tint); border-radius: 22px;
           backdrop-filter: blur(5px) saturate(1.3); -webkit-backdrop-filter: blur(5px) saturate(1.3); }
  .glass::before { content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1px; background: var(--rim); pointer-events: none;
           -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; }

  /* Main card: the status colour shows once, as a soft wash and the round badge */
  .card { padding: 16px 18px 16px; animation: rise .35s ease-out both;
          background: linear-gradient(180deg, color-mix(in srgb, var(--c) 13%, transparent), var(--tint) 46%); }
  .top { display: flex; align-items: center; gap: 12px; }
  .dot { width: 40px; height: 40px; border-radius: 50%; flex: none; display: grid; place-items: center; background: var(--c); color: #fff; }
  .t-caution .dot { color: #1d1d1f; }
  .dot svg { width: 24px; height: 24px; fill: none; stroke: currentColor; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
  .dot.sm { width: 10px; height: 10px; } .dot.sm svg { display: none; }
  .say { flex: 1; min-width: 0; }
  .word { font-size: 22px; font-weight: 700; line-height: 1.1; color: var(--c); letter-spacing: -.01em; }
  .t-offline .word, .t-na .word, .t-ready .word { color: var(--ink); }
  .text { font-size: 13px; font-weight: 600; }
  .num { text-align: right; line-height: 1; }
  .num b { font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .num small { font-size: 14px; font-weight: 600; margin-left: 1px; }
  .num span { display: block; font-size: 11px; color: var(--quiet); margin-top: 3px; }
  .sub { margin: 10px 0 0; color: var(--quiet); }

  .meta { display: flex; justify-content: space-between; margin-top: 12px; color: var(--quiet); font-size: 12px; }
  .command { margin: 4px 0 0; padding: 10px 13px; border-radius: 14px; background: var(--deep);
             white-space: pre-wrap; word-break: break-word; font: 13px/1.45 var(--vscode-editor-font-family); }
  .about { margin: 6px 2px 0; color: var(--quiet); font-size: 12px; }

  /* Risk: one segmented bar */
  .scale { display: flex; align-items: center; gap: 10px; margin-top: 14px; font-size: 12px; }
  .scale .lbl { color: var(--quiet); }
  .steps { flex: 1; display: grid; grid-template-columns: repeat(5, 1fr); gap: 3px; }
  .steps i { height: 6px; border-radius: 3px; background: var(--line); }
  .steps i.lit { background: color-mix(in srgb, var(--c) 45%, transparent); }
  .steps i.on { background: var(--c); }
  .scale b { min-width: 58px; text-align: right; text-transform: capitalize; }

  .facts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
  .fact { padding: 3px 10px; border-radius: 999px; background: var(--deep); font-size: 12px; color: var(--quiet); }
  .fact b { color: var(--ink); font-weight: 600; }
  .fact.yes b { color: var(--c); }

  /* Earlier checks: one quiet line each */
  .earlier { margin-top: 12px; padding: 10px 14px 8px; }
  h2 { margin: 0 0 4px; font-size: 12px; font-weight: 600; color: var(--quiet); }
  .earlier ol { list-style: none; margin: 0; padding: 0; }
  .row { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto auto; gap: 10px; align-items: center; padding: 6px 4px; }
  .row + .row { border-top: 1px solid var(--line); }
  .pin { width: 8px; height: 8px; border-radius: 50%; background: var(--c); }
  .row code { font-family: var(--vscode-editor-font-family); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .lvl, .row time { color: var(--quiet); font-size: 12px; }

  footer { margin-top: 12px; font-size: 11.5px; color: var(--quiet); padding: 0 4px; }
  @keyframes rise { from { opacity: 0; transform: translateY(6px); } }

  /* High contrast: no blur or wash, solid theme borders */
  body.vscode-high-contrast .glass { backdrop-filter: none; background: transparent; outline: 1px solid var(--vscode-contrastBorder); }
  body.vscode-high-contrast .glass::before { display: none; }
  body.vscode-high-contrast .command, body.vscode-high-contrast .fact { background: transparent; outline: 1px solid var(--vscode-contrastBorder); }
  body.vscode-high-contrast .steps i:not(.lit) { background: transparent; outline: 1px solid var(--vscode-contrastBorder); }
</style></head><body>
${body}
<footer>Advice only: your answer in the permission prompt decides. Answers come from the local app at ${esc(endpoint)}.</footer>
</body></html>`;
}

module.exports = { renderPanel, esc };
