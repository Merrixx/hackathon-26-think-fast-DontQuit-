// The wire format with the Command Gate app and how its answers read on screen.
// Kept in sync with cmdgate-claude-plugin/hooks/gate.ts (Claude Code plugin).

export const LEVELS = ['safe', 'low', 'moderate', 'high', 'critical']

export const QUESTIONS = {
  dangerous: {
    type: 'noul',
    instructions: 'Could this shell command cause serious damage, data loss or a security problem if it runs as written?',
  },
  requires_root: {
    type: 'noul',
    instructions: 'Does this shell command need root or administrator privileges to run?',
  },
  risk_level: {
    type: 'score',
    instructions: 'How much damage could this shell command do if it runs as written?',
    criteria: [
      'safe: read-only or routine, no plausible path to data loss, privilege change or disruption',
      'low: a local change that is quick to undo',
      'moderate: repairable without backups, with some effort',
      'high: needs backups to recover, exposes secrets to others, runs unverified code, or takes the machine down',
      'critical: destroys a disk, filesystem, system directory or home folder, or gives away root-level control',
    ],
  },
  unsafe: {
    type: 'noul',
    instructions: 'Should a person approve this shell command before an AI coding agent runs it?',
  },
}

export function buildRequest(command, model) {
  return { model, state: { command }, questions: QUESTIONS }
}

function clamp01(x) {
  const n = Number(x)
  return x !== null && x !== undefined && Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : null
}

function readYesNo(answer, threshold) {
  if (!answer || typeof answer !== 'object') return null
  const p = clamp01(answer.noul) ?? clamp01(answer.probabilities?.['true'])
  return p === null ? null : { p, yes: p >= threshold }
}

function readLevel(answer) {
  if (!answer || typeof answer !== 'object' || !answer.probabilities) return null
  const raw = LEVELS.map((_, i) => Math.max(0, Number(answer.probabilities?.[String(i)]) || 0))
  const total = raw.reduce((a, b) => a + b, 0)
  if (total <= 0) return null
  const probabilities = raw.map(v => v / total)
  const index = probabilities.indexOf(Math.max(...probabilities))
  return { index, level: LEVELS[index] ?? 'unknown', p: probabilities[index] ?? 0, probabilities }
}

// Missing or malformed answers become null and read as "n/a", never guessed.
// Each model's own risk and whether it passed its threshold, when the backend sends them.
function readModels(body) {
  const models = body?.models
  if (!models || typeof models !== 'object') return []
  return Object.entries(models).map(([name, m]) => {
    const p = clamp01(m?.p)
    const failed = m?.failed === true || p === null
    return { name, p, threshold: clamp01(m?.threshold), passed: !failed && m?.passed === true, failed }
  })
}

function readDecision(body) {
  return typeof body?.decision === 'string'
    ? { decision: body.decision, reason: typeof body.reason === 'string' ? body.reason : '' }
    : null
}

// When the backend sends its own decision, the verdict is that decision, not this plugin's threshold.
export function parseAnswers(body, threshold) {
  const answers = (body?.answers ?? {})
  const decision = readDecision(body)
  let verdict = readYesNo(answers['unsafe'], threshold)
  if (decision) {
    verdict = { p: clamp01(body.mean) ?? verdict?.p ?? 0, yes: decision.decision === 'ask' || decision.decision === 'deny' }
  }
  return {
    dangerous: readYesNo(answers['dangerous'], threshold),
    root: readYesNo(answers['requires_root'], threshold),
    risk: readLevel(answers['risk_level']),
    verdict,
    models: readModels(body),
    decision,
  }
}

export const pct = (p) => `${Math.round(p * 100)}%`

// ANSI for the Cursor CLI approval prompt. Safe/green, unsafe/red; risk steps
// follow the same green→red ramp as the VS Code card. Disabled when NO_COLOR is set.
const ANSI = {
  reset: '\x1b[0m',
  bold: '\x1b[1m',
  dim: '\x1b[2m',
  green: '\x1b[32m',
  cyan: '\x1b[36m',
  yellow: '\x1b[33m',
  orange: '\x1b[38;5;208m',
  red: '\x1b[31m',
  gray: '\x1b[90m',
}

const RISK_COLOR = [ANSI.green, ANSI.cyan, ANSI.yellow, ANSI.orange, ANSI.red]

function useColor(color) {
  // true → always paint (user CLI prompt; Cursor often sets NO_COLOR on hooks).
  // false → plain text (agent_message).
  if (color === true) return true
  if (color === false) return false
  return !process.env.NO_COLOR && process.env.FORCE_COLOR !== '0'
}

function paint(enabled, style, text) {
  if (!enabled || !style) return text
  return `${style}${text}${ANSI.reset}`
}

function row(label, value, percent, style, colored) {
  const left = label.padEnd(11)
  const mid = value.padEnd(10)
  const right = percent ? percent.padStart(4) : ''
  if (!colored) return `  ${left}${mid}${right}`.trimEnd()
  return `  ${paint(true, ANSI.dim, left)}${paint(true, style, mid)}${paint(true, style, right)}`.trimEnd()
}

function verdictParts(o) {
  if (!o.verdict) return { value: 'n/a', percent: '', style: ANSI.gray }
  if (o.verdict.yes) return { value: 'UNSAFE', percent: pct(o.verdict.p), style: ANSI.bold + ANSI.red }
  return { value: 'safe', percent: pct(o.verdict.p), style: ANSI.bold + ANSI.green }
}

function riskParts(o) {
  if (!o.risk) return { value: 'n/a', percent: '', style: ANSI.gray }
  const style = RISK_COLOR[o.risk.index] ?? ANSI.gray
  return { value: o.risk.level, percent: pct(o.risk.p), style }
}

function yesNoParts(v) {
  if (!v) return { value: 'n/a', percent: '', style: ANSI.gray }
  // yes on dangerous/root is bad (red); no is good (green). % shares the colour.
  return v.yes
    ? { value: 'yes', percent: pct(v.p), style: ANSI.red }
    : { value: 'no', percent: pct(v.p), style: ANSI.green }
}

// Multi-line block for the Cursor CLI / IDE prompt. Pass { color: false } for agents.
export function noticeLine(o, { color = true } = {}) {
  const colored = useColor(color)
  const title = paint(colored, ANSI.bold, 'Command Gate')
  const v = verdictParts(o)
  const r = riskParts(o)
  const d = yesNoParts(o.dangerous)
  const root = yesNoParts(o.root)
  return [
    title,
    row('Verdict', v.value, v.percent, v.style, colored),
    row('Risk', r.value, r.percent, r.style, colored),
    row('Dangerous', d.value, d.percent, d.style, colored),
    row('Root', root.value, root.percent, root.style, colored),
    ...modelRows(o, colored),
  ].join('\n')
}

// One row per model of the backend: its own risk and whether it passed its threshold.
function modelRows(o, colored) {
  const models = o.models ?? []
  if (!models.length) return []
  const text = o.decision ? `${o.decision.decision.toUpperCase()}${o.decision.reason ? ` · ${o.decision.reason}` : ''}` : ''
  const head = colored ? `  ${paint(true, ANSI.dim, 'Models'.padEnd(11))}${text}`.trimEnd() : `  ${'Models'.padEnd(11)}${text}`.trimEnd()
  return [head, ...models.map(m => m.failed || m.p === null
    ? row(m.name.slice(0, 10), 'no answer', '', ANSI.gray, colored)
    : row(m.name.slice(0, 10), m.passed ? 'pass' : 'FAIL', pct(m.p), m.passed ? ANSI.green : ANSI.red, colored))]
}

export function offlineLine(endpoint, reason, { color = true } = {}) {
  const colored = useColor(color)
  const title = paint(colored, ANSI.bold, 'Command Gate')
  const detail = paint(colored, ANSI.yellow, 'no second opinion')
  return [
    title,
    `  ${detail}`,
    paint(colored, ANSI.dim, `  ${endpoint}`),
    paint(colored, ANSI.dim, `  ${reason}`),
  ].join('\n')
}
