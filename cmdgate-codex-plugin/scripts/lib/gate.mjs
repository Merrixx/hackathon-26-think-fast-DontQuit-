// Standalone plugin copy of the Command Gate wire contract and CLI formatting.
// Question wording and probability semantics match the Claude and Cursor plugins.
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

function clamp01(value) {
  if ((typeof value !== 'number' && typeof value !== 'string') ||
      (typeof value === 'string' && !value.trim())) return null
  const number = Number(value)
  return Number.isFinite(number) ? Math.min(1, Math.max(0, number)) : null
}

function readYesNo(answer, threshold) {
  if (!answer || typeof answer !== 'object') return null
  const p = clamp01(answer.noul) ?? clamp01(answer.probabilities?.true)
  return p === null ? null : { p, yes: p >= threshold }
}

function readLevel(answer) {
  if (!answer || typeof answer !== 'object' || !answer.probabilities) return null
  const raw = LEVELS.map((_, index) => Math.max(0, Number(answer.probabilities[String(index)]) || 0))
  const total = raw.reduce((sum, value) => sum + value, 0)
  if (!Number.isFinite(total) || total <= 0) return null
  const probabilities = raw.map(value => value / total)
  const index = probabilities.indexOf(Math.max(...probabilities))
  return { index, level: LEVELS[index], p: probabilities[index] }
}

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
  const answers = body?.answers ?? {}
  const decision = readDecision(body)
  let verdict = readYesNo(answers.unsafe, threshold)
  if (decision) {
    verdict = { p: clamp01(body.mean) ?? verdict?.p ?? 0, yes: decision.decision === 'ask' || decision.decision === 'deny' }
  }
  return {
    dangerous: readYesNo(answers.dangerous, threshold),
    root: readYesNo(answers.requires_root, threshold),
    risk: readLevel(answers.risk_level),
    verdict,
    models: readModels(body),
    decision,
  }
}

const MARKERS = ['🟢', '🔵', '🟡', '🟠', '🔴']

function row(label, marker, value, probability) {
  const percent = probability === undefined ? '' : `${Math.round(probability * 100)}%`.padStart(4)
  return `  ${label.padEnd(11)}${marker} ${value.padEnd(10)}${percent}`.trimEnd()
}

function yesNoRow(label, answer) {
  return answer
    ? row(label, answer.yes ? '🔴' : '🟢', answer.yes ? 'yes' : 'no', answer.p)
    : row(label, '⚪', 'n/a')
}

export function noticeBlock(opinion) {
  return [
    'Command Gate',
    opinion.verdict
      ? row('Verdict', opinion.verdict.yes ? '🔴' : '🟢', opinion.verdict.yes ? 'UNSAFE' : 'safe')
      : row('Verdict', '⚪', 'n/a'),
    opinion.risk
      ? row('Risk', MARKERS[opinion.risk.index] ?? '⚪', opinion.risk.level, opinion.risk.p)
      : row('Risk', '⚪', 'n/a'),
    yesNoRow('Dangerous', opinion.dangerous),
    yesNoRow('Root', opinion.root),
    ...modelRows(opinion),
  ].join('\n')
}

// One row per model of the backend: its own risk and whether it passed its threshold.
function modelRows(opinion) {
  const models = opinion.models ?? []
  if (!models.length) return []
  const d = opinion.decision
  const head = d ? `  Models     ${d.decision.toUpperCase()}${d.reason ? ` · ${d.reason}` : ''}` : '  Models'
  return [head, ...models.map(m => m.failed || m.p === null
    ? row(m.name.slice(0, 10), '⚪', 'no answer')
    : row(m.name.slice(0, 10), m.passed ? '🟢' : '🔴', m.passed ? 'pass' : 'FAIL', m.p))]
}

export function offlineBlock(endpoint, reason) {
  return ['Command Gate', '  ⚠ no second opinion', `  ${endpoint}`, `  ${reason}`].join('\n')
}
