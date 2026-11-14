"use strict";
// HTML for the second-opinion panel. Built to help at a glance, not to distract:
// one traffic-light status (Stop / Careful / Safe), one main number, the command,
// one risk bar and two short facts. Older checks shrink to one line each.
// Liquid-glass look: a soft backdrop glows in the status colour and shows through
// layered glass (panel, darker inner card, pills). Everything else is silver.
// No scripts run in it.

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
  const steps = core.LEVELS.map((name, i) => `<i class="${i === risk.index ? "on " : ""}${i <= risk.index ? "lit" : ""}" title="${esc(name)}: ${core.pct(risk.probabilities[i])}"></i>`).join("");
  return `<div class="scale" aria-label="Risk level: ${esc(risk.level)}"><span class="lbl">Risk level</span><span class="steps">${steps}</span><b>${esc(risk.level)}</b></div>`;
}

function fact(label, v) {
  const value = v ? (v.yes ? "yes" : "no") : "n/a";
  return `<span class="pill fact${v && v.yes ? " yes" : ""}" title="${v ? core.pct(v.p) : "no answer"}">${esc(label)}: <b>${value}</b></span>`;
}

function who(entry) {
  return entry.source === "Claude Code" ? "Claude Code wants to run" : entry.source === "manual" ? "You asked about" : "Checked from your selection";
}

const badge = (tone) => `<span class="dot"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[tone]}</svg></span>`;

function slip(entry, h) {
  const about = entry.description || entry.summary;
  const r = entry.result;
  return `<article class="glass main">
    <div class="top">${badge(h.tone)}<div class="say"><div class="word">${esc(h.word)}</div><div class="text">${esc(h.text)}</div></div>
      ${h.main != null ? `<div class="num"><b>${Math.round(h.main * 100)}<small>%</small></b><span>${h.mainLabel}</span></div>` : ""}</div>
    <p class="sub">${esc(h.sub)}</p>
    <div class="card">
      <div class="meta"><span>${esc(who(entry))}</span><time>${esc(entry.at.toLocaleTimeString())}</time></div>
      <pre class="command"><code>${esc(entry.command)}</code></pre>
      ${about ? `<p class="about">${esc(about)}</p>` : ""}
    </div>
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
  const tone = latest ? headline(latest).tone : "ready";
  const body = latest
    ? `${slip(latest, headline(latest))}${older.length ? `<section class="glass earlier"><h2 class="pill">Earlier checks</h2><ol>${older.slice(0, 6).map(row).join("")}</ol></section>` : ""}`
    : `<article class="glass main"><div class="top">${badge("ready")}<div class="say"><div class="word">Ready</div>
       <div class="text">Waiting for a permission prompt</div></div></div>
       <p class="sub">When Claude Code asks to run a shell command, the second opinion appears here.
       To try it now, run <b>Command Gate: Check command…</b> from the Command Palette.</p></article>`;
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
  :root {
    --ink: #f2f2f2; --quiet: rgba(255,255,255,.62); --base: #0c0c0e;
    --tint: rgba(0,0,0,.07); --card: rgba(21,21,21,.22); --pill: rgba(21,21,21,.22); --hov: rgba(255,255,255,.10);
    --track: rgba(255,255,255,.10); --line: rgba(255,255,255,.07); --silver: rgba(210,210,215,.10);
    --edge: linear-gradient(180deg,rgba(181,181,181,.41) 0%,rgba(181,181,181,.25) 20%,rgba(48,48,48,.31) 30%,rgba(16,16,16,.31) 65%,rgba(48,48,48,.31) 75%,rgba(172,172,172,.31) 100%);
    --red: #f2555a; --amber: #f0b429; --green: #3ecf8e; --grey: #9a9aa0;
  }
  body.vscode-light {
    --ink: #1d1d1f; --quiet: rgba(0,0,0,.58); --base: #e9e9ec;
    --tint: rgba(255,255,255,.30); --card: rgba(255,255,255,.42); --pill: rgba(255,255,255,.48); --hov: rgba(255,255,255,.75);
    --track: rgba(0,0,0,.09); --line: rgba(0,0,0,.07); --silver: rgba(120,120,130,.16);
    --edge: linear-gradient(180deg,#fff 0%,rgba(255,255,255,.7) 20%,rgba(48,48,48,.14) 35%,rgba(16,16,16,.12) 65%,rgba(255,255,255,.7) 80%,#fff 100%);
    --red: #d92d3a; --amber: #c27c0e; --green: #1f9d63; --grey: #7a7a80;
  }
  .t-stop { --c: var(--red); } .t-caution { --c: var(--amber); } .t-go { --c: var(--green); }
  .t-offline, .t-na, .t-ready { --c: var(--grey); }

  * { box-sizing: border-box; }
  html, body { overflow-x: hidden; }
  body { margin: 0; color: var(--ink); background: var(--base); font: 13px/1.45 var(--vscode-font-family, "Segoe UI Variable", "Segoe UI", system-ui, sans-serif); }
  /* The backdrop the glass shows: the status colour glows softly, the rest is silver */
  .stage { min-height: 100vh; padding: 18px 16px 24px; display: grid; gap: 12px; align-content: start;
    background:
      radial-gradient(120% 60% at 12% 0%, color-mix(in srgb, var(--c) 42%, transparent), transparent 62%),
      radial-gradient(70% 45% at 95% 30%, var(--silver), transparent 70%),
      radial-gradient(90% 50% at 30% 100%, color-mix(in srgb, var(--c) 14%, transparent), transparent 70%),
      var(--base); }
  .stage > * { width: 100%; max-width: 560px; }

  /* Glass layers: panel > darker inner card > pills, each with the 1 px glass edge */
  .glass, .card, .pill { position: relative; isolation: isolate; }
  .glass::before, .card::before, .pill::before { content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1px; background: var(--edge); pointer-events: none;
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; }
  .glass { background: var(--tint); backdrop-filter: blur(5px) saturate(1.3); -webkit-backdrop-filter: blur(5px) saturate(1.3);
    border-radius: 50px; box-shadow: 0 10px 60px rgba(15,15,15,.06); }
  .card { background: var(--card); border-radius: 26px; padding: 10px 14px 12px; margin-top: 12px; }
  .pill { display: inline-flex; align-items: center; gap: 4px; background: var(--pill); backdrop-filter: blur(20px); -webkit-backdrop-filter: blur(20px);
    border-radius: 18px; padding: 4px 12px; transition: background-color .083s ease-in-out, transform .12s; }
  .pill:hover { background: var(--hov); transform: scale(1.05); }

  .main { padding: 20px 22px 20px; animation: rise .35s ease-out both; }
  .top { display: flex; align-items: center; gap: 12px; }
  .dot { position: relative; width: 42px; height: 42px; border-radius: 50%; flex: none; display: grid; place-items: center; color: #fff;
    background: radial-gradient(circle at 35% 28%, color-mix(in srgb, #fff 35%, var(--c)), var(--c) 62%);
    box-shadow: inset 0 1px 1px rgba(255,255,255,.45), inset 0 -2px 4px rgba(0,0,0,.18), 0 4px 18px color-mix(in srgb, var(--c) 40%, transparent); }
  .t-caution .dot { color: #1d1d1f; }
  .dot svg { width: 24px; height: 24px; fill: none; stroke: currentColor; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round; }
  .say { flex: 1; min-width: 0; }
  .word { font-size: 22px; font-weight: 700; line-height: 1.1; color: var(--c); letter-spacing: -.01em; }
  .t-offline .word, .t-na .word, .t-ready .word { color: var(--ink); }
  .text { font-size: 13px; font-weight: 600; }
  .num { text-align: right; line-height: 1; }
  .num b { font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .num small { font-size: 14px; font-weight: 600; margin-left: 1px; }
  .num span { display: block; font-size: 11px; color: var(--quiet); margin-top: 3px; }
  .sub { margin: 10px 2px 0; color: var(--quiet); }

  .meta { display: flex; justify-content: space-between; color: var(--quiet); font-size: 12px; }
  .command { margin: 4px 0 0; white-space: pre-wrap; word-break: break-word; font: 13px/1.45 var(--vscode-editor-font-family, Consolas, monospace); }
  .about { margin: 6px 0 0; color: var(--quiet); font-size: 12px; }

  .scale { display: flex; align-items: center; gap: 10px; margin: 14px 4px 0; font-size: 12px; }
  .scale .lbl { color: var(--quiet); }
  .steps { flex: 1; display: grid; grid-template-columns: repeat(5, 1fr); gap: 3px; }
  .steps i { height: 6px; border-radius: 3px; background: var(--track); }
  .steps i.lit { background: color-mix(in srgb, var(--c) 45%, transparent); }
  .steps i.on { background: var(--c); box-shadow: 0 0 8px color-mix(in srgb, var(--c) 55%, transparent); }
  .scale b { min-width: 58px; text-align: right; text-transform: capitalize; }

  .facts { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
  .fact { font-size: 12px; color: var(--quiet); }
  .fact b { color: var(--ink); font-weight: 600; }
  .fact.yes b { color: var(--c); }

  .earlier { padding: 14px 18px 10px; border-radius: 34px; }
  h2 { margin: 0 0 6px; font-size: 12px; font-weight: 600; }
  .earlier ol { list-style: none; margin: 0; padding: 0; }
  .row { display: grid; grid-template-columns: 8px minmax(0, 1fr) auto auto; gap: 10px; align-items: center; padding: 6px 8px; border-radius: 15px;
    transition: background-color .083s ease-in-out, transform .12s; }
  .row:hover { background: var(--hov); transform: scale(1.025); }
  .row + .row { box-shadow: 0 -1px 0 var(--line); }
  .pin { width: 8px; height: 8px; border-radius: 50%; background: var(--c); box-shadow: 0 0 6px color-mix(in srgb, var(--c) 60%, transparent); }
  .row code { font-family: var(--vscode-editor-font-family, Consolas, monospace); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .lvl, .row time { color: var(--quiet); font-size: 12px; }

  footer { font-size: 11.5px; color: var(--quiet); padding: 0 8px; }
  @keyframes rise { from { opacity: 0; transform: translateY(6px); } }

  /* High contrast: theme colours, no glass, solid borders */
  body.vscode-high-contrast { --ink: var(--vscode-foreground, #fff); --quiet: var(--vscode-foreground, #fff); }
  body.vscode-high-contrast .stage { background: var(--vscode-editor-background, #000); }
  body.vscode-high-contrast .glass, body.vscode-high-contrast .card, body.vscode-high-contrast .pill {
    backdrop-filter: none; background: transparent; outline: 1px solid var(--vscode-contrastBorder, #6fc3df); }
  body.vscode-high-contrast .glass::before, body.vscode-high-contrast .card::before, body.vscode-high-contrast .pill::before { display: none; }
  body.vscode-high-contrast .steps i:not(.lit) { background: transparent; outline: 1px solid var(--vscode-contrastBorder, #6fc3df); }
</style></head><body>
<main class="stage t-${tone}">
${body}
<footer>Advice only: your answer in the permission prompt decides. Answers come from the local app at ${esc(endpoint)}.</footer>
</main>
</body></html>`;
}

module.exports = { renderPanel, esc };
