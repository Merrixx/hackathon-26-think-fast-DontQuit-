// The wire format with the Command Gate app and how its answers read on screen.
// No engine calls here, so it is easy to test on its own.

export const LEVELS = ['safe', 'low', 'moderate', 'high', 'critical'] as const

// Question IDs the app answers. The VS Code add-on sends the same four.
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
} as const

export type YesNo = { p: number; yes: boolean } | null
export type Risk = { index: number; level: string; p: number; probabilities: number[] } | null
// What one model of the backend said, and the backend's own decision, when it sends them.
export type ModelRow = { name: string; p: number | null; threshold: number | null; passed: boolean; failed: boolean }
export type Decision = { decision: string; reason: string } | null
export type Opinion = { dangerous: YesNo; root: YesNo; risk: Risk; verdict: YesNo; models: ModelRow[]; decision: Decision }

export function buildRequest(command: string, model: string) {
  return { model, state: { command }, questions: QUESTIONS }
}

function clamp01(x: unknown): number | null {
  const n = Number(x)
  return x !== null && x !== undefined && Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : null
}

type Answer = { noul?: unknown; probabilities?: Record<string, unknown> } | undefined

function readYesNo(answer: Answer, threshold: number): YesNo {
  if (!answer || typeof answer !== 'object') return null
  const p = clamp01(answer.noul) ?? clamp01(answer.probabilities?.['true'])
  return p === null ? null : { p, yes: p >= threshold }
}

function readLevel(answer: Answer): Risk {
  if (!answer || typeof answer !== 'object' || !answer.probabilities) return null
  const raw = LEVELS.map((_, i) => Math.max(0, Number(answer.probabilities?.[String(i)]) || 0))
  const total = raw.reduce((a, b) => a + b, 0)
  if (total <= 0) return null
  const probabilities = raw.map(v => v / total)
  const index = probabilities.indexOf(Math.max(...probabilities))
  return { index, level: LEVELS[index] ?? 'unknown', p: probabilities[index] ?? 0, probabilities }
}

type ModelAnswer = { p?: unknown; threshold?: unknown; passed?: unknown; failed?: unknown } | null | undefined

// Each model's own risk and whether it passed its threshold. Only the backend knows the thresholds.
function readModels(body: unknown): ModelRow[] {
  const models = (body as { models?: unknown } | null)?.models
  if (!models || typeof models !== 'object') return []
  return Object.entries(models as Record<string, ModelAnswer>).map(([name, m]) => {
    const p = clamp01(m?.p)
    const failed = m?.failed === true || p === null
    return { name, p, threshold: clamp01(m?.threshold), passed: !failed && m?.passed === true, failed }
  })
}

function readDecision(body: unknown): Decision {
  const b = body as { decision?: unknown; reason?: unknown } | null
  return typeof b?.decision === 'string' ? { decision: b.decision, reason: typeof b.reason === 'string' ? b.reason : '' } : null
}

// Missing or malformed answers become null and read as "n/a", never guessed.
// When the backend sends its own decision, the verdict is that decision, not this plugin's threshold.
export function parseAnswers(body: unknown, threshold: number): Opinion {
  const answers = ((body as { answers?: Record<string, Answer> } | null)?.answers ?? {}) as Record<string, Answer>
  const decision = readDecision(body)
  let verdict = readYesNo(answers['unsafe'], threshold)
  if (decision) {
    const mean = clamp01((body as { mean?: unknown }).mean)
    verdict = { p: mean ?? verdict?.p ?? 0, yes: decision.decision === 'ask' || decision.decision === 'deny' }
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

const pct = (p: number) => `${Math.round(p * 100)}%`
const yesNo = (v: YesNo) => (v ? `${v.yes ? 'yes' : 'no'} ${pct(v.p)}` : 'n/a')

// The one line shown under the permission dialog.
export function noticeLine(o: Opinion): string {
  const verdict = !o.verdict ? 'no verdict' : o.verdict.yes ? 'UNSAFE' : 'safe'
  const risk = o.risk ? `${o.risk.level} ${pct(o.risk.p)}` : 'n/a'
  const models = o.models.length
    ? ` · models ${o.models.map(m => `${m.name} ${m.failed || m.p === null ? 'n/a' : pct(m.p)}${m.passed ? '' : '!'}`).join(', ')}`
    : ''
  return `Command Gate: ${verdict} · risk ${risk} · dangerous ${yesNo(o.dangerous)} · root ${yesNo(o.root)}${models}`
}

// Transcript logs are plain text, so use line breaks and coloured Unicode
// markers rather than ANSI escapes, which the host may strip or reject.
const RISK_MARKERS = ['🟢', '🔵', '🟡', '🟠', '🔴'] as const

function row(label: string, marker: string, value: string, p?: number): string {
  const percent = p === undefined ? '' : pct(p).padStart(4)
  return `  ${label.padEnd(11)}${marker} ${value.padEnd(10)}${percent}`.trimEnd()
}

function yesNoRow(label: string, value: YesNo): string {
  return value
    ? row(label, value.yes ? '🔴' : '🟢', value.yes ? 'yes' : 'no', value.p)
    : row(label, '⚪', 'n/a')
}

export function transcriptBlock(o: Opinion): string {
  return [
    'Command Gate',
    o.verdict
      ? row('Verdict', o.verdict.yes ? '🔴' : '🟢', o.verdict.yes ? 'UNSAFE' : 'safe')
      : row('Verdict', '⚪', 'n/a'),
    o.risk
      ? row('Risk', RISK_MARKERS[o.risk.index] ?? '⚪', o.risk.level, o.risk.p)
      : row('Risk', '⚪', 'n/a'),
    yesNoRow('Dangerous', o.dangerous),
    yesNoRow('Root', o.root),
    ...modelRows(o),
  ].join('\n')
}

// One row per model of the backend: its own risk and whether it passed its threshold.
function modelRows(o: Opinion): string[] {
  if (!o.models.length) return []
  const head = o.decision ? `  Models     ${o.decision.decision.toUpperCase()}${o.decision.reason ? ` · ${o.decision.reason}` : ''}` : '  Models'
  return [head, ...o.models.map(m => m.failed || m.p === null
    ? row(m.name.slice(0, 10), '⚪', 'no answer')
    : row(m.name.slice(0, 10), m.passed ? '🟢' : '🔴', m.passed ? 'pass' : 'FAIL', m.p))]
}

export function offlineLine(endpoint: string, reason: string): string {
  return `Command Gate: no second opinion from ${endpoint} (${reason})`
}

export function offlineBlock(endpoint: string, reason: string): string {
  return ['Command Gate', '  ⚠ no second opinion', `  ${endpoint}`, `  ${reason}`].join('\n')
}
