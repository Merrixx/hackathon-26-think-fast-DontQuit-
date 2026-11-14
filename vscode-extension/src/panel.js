"use strict";
// HTML for the second-opinion panel. The newest check is a full "slip": one big
// verdict word, the command, the five-step risk scale and the two yes/no facts.
// Older checks shrink to one line each. Monochrome liquid glass that follows the
// VS Code theme (dark, light, high contrast). No scripts run in it.

const core = require("./core");

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// The headline, from the reader's point of view: what to do at the prompt.
function headline(entry) {
  if (entry.error) return { tone: "offline", word: "Offline", text: "No second opinion", sub: `The local app at ${entry.endpoint} did not answer: ${entry.error}. Start it, then check again.` };
  const v = entry.result.verdict;
  if (!v) return { tone: "na", word: "Unsure", text: "No verdict from the app", sub: "The app answered, but not the safe/unsafe question." };
  return v.yes
    ? entry.severity === "error"
      ? { tone: "stop", word: "Stop", text: "Look closely before you approve", sub: `The model rates this unsafe (${core.pct(v.p)}).` }
      : { tone: "caution", word: "Careful", text: "Look closely before you approve", sub: `The model rates this unsafe (${core.pct(v.p)}).` }
    : { tone: "go", word: "Safe", text: "Looks safe to run", sub: `The model rates this safe (${core.pct(1 - v.p)}).` };
}

const ICONS = {
  stop: '<path d="M7 7l10 10M17 7 7 17"/>',
  caution: '<path d="M12 7v6"/><circle cx="12" cy="17" r=".9" fill="currentColor"/>',
  go: '<path d="M6.5 12.5l3.5 3.5 7.5-8"/>',
  offline: '<path d="M5 12h14"/>',
  na: '<path d="M9.5 9.5a2.6 2.6 0 1 1 3.4 2.5c-.6.2-.9.7-.9 1.3v.4"/><circle cx="12" cy="17" r=".9" fill="currentColor"/>',
};
const SHIELD = '<path d="M12 2.5 4 5.5v6c0 5 3.4 8.6 8 10 4.6-1.4 8-5 8-10v-6z"/>';

function scale(risk) {
  if (!risk) return `<p class="muted">No risk level from the app.</p>`;
  const cols = core.LEVELS.map((name, i) => {
    const p = risk.probabilities[i];
    const on = i === risk.index;
    return `<li class="col${on ? " on" : ""}" style="--h:${Math.max(4, Math.round(p * 100))}%;--k:${22 + i * 16}%;--d:${i * 70}ms">
      <span class="pv">${core.pct(p)}</span><span class="bar"><span class="fill"></span></span><span class="name">${esc(name)}</span></li>`;
  }).join("");
  return `<figure class="scale glass" aria-label="Risk level: ${esc(risk.level)}, ${core.pct(risk.p)}">
    <figcaption><span class="pill">Risk level</span> <b>${esc(risk.level)}</b> <span class="muted">${core.pct(risk.p)}</span></figcaption>
    <ol>${cols}</ol></figure>`;
}

function fact(label, v) {
  const value = v ? (v.yes ? "Yes" : "No") : "n/a";
  const tone = !v ? "na" : v.yes ? "yes" : "no";
  const C = 2 * Math.PI * 17;
  const ring = `<svg class="ring" viewBox="0 0 44 44" aria-hidden="true"><circle cx="22" cy="22" r="17" class="rt"/>${v
    ? `<circle cx="22" cy="22" r="17" class="rf" style="--len:${(v.p * C).toFixed(1)}" stroke-dasharray="${(v.p * C).toFixed(1)} ${C.toFixed(1)}" transform="rotate(-90 22 22)"/>` : ""}</svg>`;
  return `<div class="fact glass ${tone}">${ring}<dl><dt>${esc(label)}</dt><dd>${value}${v ? ` <small>${core.pct(v.p)}</small>` : ""}</dd></dl></div>`;
}

function who(entry) {
  return entry.source === "Claude Code" ? "Claude Code wants to run" : entry.source === "manual" ? "You asked about" : "Checked from your selection";
}

function slip(entry) {
  const h = headline(entry);
  const about = entry.description || entry.summary;
  const r = entry.result;
  return `<article class="slip ${h.tone}">
    <div class="hero"><div class="ghost" aria-hidden="true">${esc(h.word)}</div>
      <div class="word"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${ICONS[h.tone]}</svg>${esc(h.word)}</div></div>
    <section class="card glass">
      <header><span class="pill">${esc(who(entry))}</span><time>${esc(entry.at.toLocaleTimeString())}</time></header>
      <pre class="command"><code>${esc(entry.command)}</code></pre>
      ${about ? `<p class="about">${esc(about)}</p>` : ""}
      <h1>${esc(h.text)}</h1>
      <p class="sub">${esc(h.sub)}</p>
    </section>
    ${r ? `${scale(r.risk)}<div class="facts">${fact("Dangerous", r.dangerous)}${fact("Needs root access", r.root)}</div>` : ""}
  </article>`;
}

function row(entry) {
  const h = headline(entry);
  const risk = entry.result && entry.result.risk;
  const meter = core.LEVELS.map((_, i) => `<i class="${risk && i <= risk.index ? "on" : ""}"></i>`).join("");
  return `<li class="row ${h.tone}"><span class="meter" title="${esc(risk ? risk.level : "n/a")}">${meter}</span><code>${esc(core.shorten(entry.command, 70))}</code>
    <span class="lvl">${esc(risk ? risk.level : h.word.toLowerCase())}</span><time>${esc(entry.at.toLocaleTimeString())}</time></li>`;
}

function renderPanel(history, endpoint) {
  const [latest, ...older] = history;
  const body = latest
    ? `${slip(latest)}${older.length ? `<section class="earlier glass"><h2 class="pill">Earlier checks</h2><ol>${older.map(row).join("")}</ol></section>` : ""}`
    : `<div class="empty"><div class="hero"><div class="ghost" aria-hidden="true">Ready</div>
       <div class="word"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true">${SHIELD}</svg>Ready</div></div>
       <section class="card glass"><h1>Waiting for a permission prompt</h1>
       <p class="sub">When Claude Code asks to run a shell command, the second opinion appears here.
       To try it now, run <b>Command Gate: Check command…</b> from the Command Palette.</p></section></div>`;
  return `<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline';">
<style>
  /* Liquid glass recipe: blur 5 + saturate 1.3, light tint, 1 px rim light-dark-light, nested layers rounder outside */
  :root {
    --ink: var(--vscode-foreground, #e8e8ea);
    --quiet: var(--vscode-descriptionForeground, rgba(255,255,255,.55));
    --bg: var(--vscode-editor-background, #1f1f1f);
    --tint: rgba(255,255,255,.045); --deep: rgba(0,0,0,.22); --pillbg: rgba(255,255,255,.07);
    --rim: linear-gradient(180deg,rgba(181,181,181,.41) 0%,rgba(181,181,181,.25) 20%,rgba(48,48,48,.31) 30%,rgba(16,16,16,.31) 65%,rgba(48,48,48,.31) 75%,rgba(172,172,172,.31) 100%);
    --rim-in: linear-gradient(180deg,rgba(181,181,181,.25),rgba(4,4,4,.25) 50%,rgba(177,177,177,.25));
    --shadow: 0 10px 60px rgba(0,0,0,.25);
    --cond: "Bahnschrift SemiBold Condensed", "Bahnschrift Condensed", "Arial Narrow", "Roboto Condensed", Impact, sans-serif;
  }
  body.vscode-light {
    --tint: rgba(0,0,0,.035); --deep: rgba(21,21,21,.05); --pillbg: rgba(21,21,21,.07);
    --rim: linear-gradient(180deg,#fff 0%,rgba(181,181,181,.45) 20%,rgba(48,48,48,.2) 32%,rgba(16,16,16,.16) 65%,rgba(120,120,120,.3) 78%,#fff 100%);
    --rim-in: linear-gradient(180deg,rgba(255,255,255,.9),rgba(4,4,4,.12) 50%,rgba(255,255,255,.9));
    --shadow: 0 10px 60px rgba(15,15,15,.06);
  }
  * { box-sizing: border-box; }
  html, body { overflow-x: hidden; }
  body { margin: 0; padding: 18px 22px 28px; color: var(--ink); background: var(--bg); font: 13px/1.5 var(--vscode-font-family); }
  .slip, .empty, .earlier, footer { max-width: 640px; }
  .muted { color: var(--quiet); }

  .glass { position: relative; isolation: isolate; background: var(--tint); border-radius: 26px; box-shadow: var(--shadow);
           backdrop-filter: blur(5px) saturate(1.3); -webkit-backdrop-filter: blur(5px) saturate(1.3); }
  .glass::before { content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1px; background: var(--rim); pointer-events: none;
           -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; }
  .pill { display: inline-block; position: relative; padding: 3px 11px; border-radius: 18px; background: var(--pillbg); font-size: 11.5px; font-weight: 600; color: var(--ink); }
  .pill::before { content: ""; position: absolute; inset: 0; border-radius: inherit; padding: 1px; background: var(--rim-in); pointer-events: none;
           -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0); -webkit-mask-composite: xor; mask-composite: exclude; }

  /* Big verdict word, with a blurred dot copy behind the glass */
  .hero { position: relative; height: clamp(86px, 22vw, 150px); margin: 4px 0 -34px; z-index: 0; }
  .ghost { position: absolute; left: -10px; top: -14px; white-space: nowrap; font: 600 clamp(120px, 34vw, 240px)/1 var(--cond); font-stretch: condensed;
           text-transform: uppercase; letter-spacing: -.01em; color: transparent; opacity: .55;
           background-image: radial-gradient(circle, var(--ink) 1.1px, transparent 1.5px); background-size: 5px 5px;
           -webkit-background-clip: text; background-clip: text; filter: blur(1.3px); pointer-events: none; }
  .word { position: relative; display: flex; align-items: center; gap: 10px; font: 600 clamp(56px, 15vw, 104px)/1 var(--cond); font-stretch: condensed;
          text-transform: uppercase; letter-spacing: -.005em; animation: rise .6s cubic-bezier(.2,.8,.2,1) both; }
  .ico { width: .62em; height: .62em; flex: none; fill: none; stroke: currentColor; stroke-width: 2.6; stroke-linecap: round; stroke-linejoin: round;
         border-radius: 50%; padding: .08em; }
  .slip.stop .ico { background: var(--ink); color: var(--bg); }
  .slip.caution .word { color: color-mix(in srgb, var(--ink) 72%, transparent); }
  .slip.offline .word, .slip.na .word { color: color-mix(in srgb, var(--ink) 45%, transparent); }
  .slip.caution .ico { color: var(--ink); outline: 2px solid var(--ink); outline-offset: -2px; }
  .slip.go .word { font-weight: 400; }

  .card { padding: 16px 20px 18px; z-index: 1; animation: rise .6s cubic-bezier(.2,.8,.2,1) .08s both; }
  header { display: flex; justify-content: space-between; align-items: center; color: var(--quiet); font-size: 12px; }
  .command { margin: 10px 0 0; padding: 12px 15px; border-radius: 15px; background: var(--deep); position: relative;
             white-space: pre-wrap; word-break: break-word; font: 14px/1.45 var(--vscode-editor-font-family); }
  .slip.stop .command { box-shadow: inset 3px 0 0 var(--ink); }
  .about { margin: 8px 2px 0; color: var(--quiet); }
  h1 { margin: 14px 2px 2px; font-size: 19px; line-height: 1.25; font-weight: 650; letter-spacing: -0.01em; }
  .sub { margin: 0 2px; color: var(--quiet); }

  /* Risk scale: five columns, the chosen level solid */
  .scale { margin: 12px 0 0; padding: 14px 20px 14px; animation: rise .6s cubic-bezier(.2,.8,.2,1) .16s both; }
  figcaption { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 13px; }
  figcaption b { font: 600 22px/1 var(--cond); font-stretch: condensed; text-transform: uppercase; letter-spacing: .02em; }
  .scale ol { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
  .col { display: grid; grid-template-rows: 18px 74px auto; justify-items: center; font-size: 11.5px; color: var(--quiet); }
  .pv { font-variant-numeric: tabular-nums; align-self: end; }
  .bar { position: relative; width: 100%; max-width: 54px; height: 100%; border-radius: 12px; background: var(--deep); overflow: hidden; }
  .fill { position: absolute; inset: auto 0 0 0; height: var(--h); border-radius: 12px;
          background: color-mix(in srgb, var(--ink) var(--k), transparent); transform-origin: bottom; animation: grow .9s cubic-bezier(.2,.8,.2,1) var(--d) both; }
  .col.on { color: var(--ink); }
  .col.on .pv { font-weight: 700; font-size: 13px; }
  .col.on .fill { background: linear-gradient(180deg, var(--ink), color-mix(in srgb, var(--ink) 70%, transparent)); }
  .col.on .bar { outline: 1px solid color-mix(in srgb, var(--ink) 35%, transparent); outline-offset: 2px; }
  .name { margin-top: 6px; } .col.on .name { font-weight: 600; }

  /* Two facts as ring gauges */
  .facts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 12px 0 0; }
  .fact { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-radius: 22px; animation: rise .6s cubic-bezier(.2,.8,.2,1) .24s both; }
  .ring { width: 44px; height: 44px; flex: none; }
  .rt { fill: none; stroke: var(--deep); stroke-width: 5; }
  .rf { fill: none; stroke: var(--ink); stroke-width: 5; stroke-linecap: round; animation: arc 1s cubic-bezier(.2,.8,.2,1) .3s both; }
  .fact.no .rf { stroke: color-mix(in srgb, var(--ink) 45%, transparent); }
  .fact dl { margin: 0; } .fact dt { font-size: 12px; color: var(--quiet); }
  .fact dd { margin: 0; font: 600 24px/1.1 var(--cond); font-stretch: condensed; text-transform: uppercase; }
  .fact small { font: 400 12px var(--vscode-font-family); color: var(--quiet); font-variant-numeric: tabular-nums; text-transform: none; }
  .fact.na dd { color: var(--quiet); }

  /* Earlier checks */
  .earlier { margin-top: 18px; padding: 14px 18px 10px; animation: rise .6s cubic-bezier(.2,.8,.2,1) .3s both; }
  h2.pill { margin: 0 0 8px; }
  .earlier ol { list-style: none; margin: 0; padding: 0; }
  .row { display: grid; grid-template-columns: auto 1fr auto auto; gap: 12px; align-items: center; padding: 7px 8px; border-radius: 15px;
         transition: background-color .083s ease-in-out, transform .12s; }
  .row:hover { background: var(--pillbg); }
  .row + .row { border-top: 1px solid color-mix(in srgb, var(--ink) 7%, transparent); }
  .row code { font-family: var(--vscode-editor-font-family); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row .lvl, .row time { color: var(--quiet); font-size: 11.5px; }
  .meter { display: inline-flex; gap: 2px; align-items: flex-end; height: 12px; }
  .meter i { width: 3px; border-radius: 2px; background: color-mix(in srgb, var(--ink) 16%, transparent); }
  .meter i:nth-child(1) { height: 4px; } .meter i:nth-child(2) { height: 6px; } .meter i:nth-child(3) { height: 8px; } .meter i:nth-child(4) { height: 10px; } .meter i:nth-child(5) { height: 12px; }
  .meter i.on { background: var(--ink); }

  .empty .card h1 { margin-top: 2px; }
  footer { margin-top: 18px; font-size: 11.5px; color: var(--quiet); padding: 0 4px; }

  @keyframes rise { from { opacity: 0; transform: translateY(14px); } }
  @keyframes grow { from { transform: scaleY(0); } }
  @keyframes arc { from { stroke-dasharray: 0 200; } }

  /* High contrast: no blur, solid theme borders */
  body.vscode-high-contrast { --deep: color-mix(in srgb, var(--ink) 16%, transparent); --pillbg: transparent; }
  body.vscode-high-contrast .command, body.vscode-high-contrast .bar { outline: 1px solid var(--vscode-contrastBorder); }
  body.vscode-high-contrast .glass, body.vscode-high-contrast .pill { backdrop-filter: none; background: transparent; box-shadow: none;
           outline: 1px solid var(--vscode-contrastBorder); }
  body.vscode-high-contrast .glass::before, body.vscode-high-contrast .pill::before, body.vscode-high-contrast .ghost { display: none; }
  @media (max-width: 420px) { .facts { grid-template-columns: 1fr; } .scale ol { gap: 4px; } }
</style></head><body>
${body}
<footer>Advice only: your answer in the permission prompt decides. Answers come from the local app at ${esc(endpoint)}.</footer>
</body></html>`;
}

module.exports = { renderPanel, esc };
