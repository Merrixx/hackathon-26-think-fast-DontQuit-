import { expect, mock, test } from 'claude-code/testing'
import { LEVELS, parseAnswers, transcriptBlock } from '../hooks/gate.ts'

const ANSWER = {
  answers: {
    dangerous: { type: 'noul', noul: 0.91 },
    requires_root: { type: 'noul', noul: 0.12 },
    risk_level: { type: 'score', probabilities: { 0: 0.02, 1: 0.03, 2: 0.05, 3: 0.2, 4: 0.7 } },
    unsafe: { type: 'noul', noul: 0.95 },
  },
}

const ASK = { tool: 'Bash', input: { command: 'rm -rf ~/' }, tool_use_id: 'toolu_1', approver: 'person' } as const

// Hooks registered in a test sit beneath the plugin and stand for the engine.
function engine(on: any, opts: { decision?: string; reply?: () => unknown; hideNotice?: boolean } = {}) {
  const seen = { notices: [] as unknown[], logs: [] as any[], requests: [] as any[] }
  mock.clock(on) // the timeout timer; it never fires unless a test advances it
  on('tool.check', () => ({ decision: opts.decision ?? 'ask' }))
  on('http.fetch', (_: unknown, e: any) => {
    seen.requests.push(e)
    return { value: opts.reply ? opts.reply() : { status: 200, ok: true, headers: {}, text: JSON.stringify(ANSWER) } }
  })
  on('ui.log', (_: unknown, e: any) => {
    seen.logs.push(e)
    return { value: undefined }
  })
  on('ui.notice', (_: unknown, e: unknown) => {
    if (!opts.hideNotice) seen.notices.push(e)
    return { value: undefined }
  })
  on('env.get', () => ({ value: undefined }))
  return seen
}

test('a command about to be asked gets the second opinion under its dialog', async ($, on) => {
  const seen = engine(on)
  const verdict = await $.tool.check(ASK)

  expect(verdict.decision).toBe('ask') // the decision is untouched
  expect(seen.requests.length).toBe(1)
  const sent = JSON.parse(seen.requests[0].init.body)
  expect(sent.state).toEqual({ command: 'rm -rf ~/' })
  expect(Object.keys(sent.questions)).toEqual(['dangerous', 'requires_root', 'risk_level', 'unsafe'])
  expect(JSON.stringify(seen.notices)).toContain(
    'Command Gate: UNSAFE · risk critical 70% · dangerous yes 91% · root no 12%')
})

test('the evaluation is logged to the transcript even if the dialog paints no notice', async ($, on) => {
  const seen = engine(on, { hideNotice: true })
  const verdict = await $.tool.check(ASK)

  expect(verdict.decision).toBe('ask')
  expect(seen.requests.length).toBe(1)
  expect(seen.notices.length).toBe(0)
  expect(seen.logs).toEqual([
    'Command Gate',
    '  Verdict    🔴 UNSAFE',
    '  Risk       🔴 critical   70%',
    '  Dangerous  🔴 yes        91%',
    '  Root       🟢 no         12%',
  ].map(text => ({ text, to: 'transcript' })))
  for (const log of seen.logs) {
    expect(log.text.includes('\n')).toBe(false)
    expect(log.text.includes('\r')).toBe(false)
    expect(log.text.includes('�')).toBe(false)
  }
})

test('a command that runs without asking is not checked', async ($, on) => {
  const seen = engine(on, { decision: 'allow' })
  const verdict = await $.tool.check(ASK)

  expect(verdict.decision).toBe('allow')
  expect(seen.requests.length).toBe(0)
  expect(seen.notices.length).toBe(0)
  expect(seen.logs.length).toBe(0)
})

test('an app that is down says so and leaves the decision alone', async ($, on) => {
  const seen = engine(on, { reply: () => ({ status: 500, ok: false, headers: {}, text: 'boom' }) })
  const verdict = await $.tool.check(ASK)

  expect(verdict.decision).toBe('ask')
  expect(JSON.stringify(seen.notices)).toContain('Command Gate: no second opinion')
  expect(seen.logs[0].to).toBe('transcript')
  expect(seen.logs.map(log => log.text)).toEqual([
    'Command Gate',
    '  ⚠ no second opinion',
    '  http://127.0.0.1:8019/v1/systemone',
    '  HTTP 500',
  ])
})

test('missing answers read as n/a, never guessed', async ($, on) => {
  const seen = engine(on, { reply: () => ({ status: 200, ok: true, headers: {}, text: '{"answers":{}}' }) })
  await $.tool.check(ASK)

  expect(JSON.stringify(seen.notices)).toContain('no verdict · risk n/a · dangerous n/a · root n/a')
})

test('a slow app times out and the dialog still opens as decided', async ($, on) => {
  const seen = { notices: [] as unknown[], logs: [] as any[] }
  const clock = mock.clock(on)
  on('tool.check', () => ({ decision: 'ask' }))
  on('http.fetch', () => new Promise(() => {})) // never answers
  on('ui.log', (_: unknown, e: any) => { seen.logs.push(e); return { value: undefined } })
  on('ui.notice', (_: unknown, e: unknown) => { seen.notices.push(e); return { value: undefined } })
  on('env.get', () => ({ value: undefined }))

  const pending = $.tool.check({ ...ASK, tool: 'PowerShell', input: { command: 'Remove-Item -Recurse C:/Windows' } })
  await clock.advance(3000)
  const verdict = await pending

  expect(verdict.decision).toBe('ask')
  expect(JSON.stringify(seen.notices)).toContain('no answer within 3000 ms')
  expect(seen.logs.map(log => log.text).join(' ')).toContain('no answer within 3000 ms')
})

test('a safe evaluation uses green markers and preserves the backend yes probabilities', () => {
  const opinion = parseAnswers({ answers: {
    dangerous: { noul: 0.04 },
    requires_root: { noul: 0.05 },
    unsafe: { noul: 0.05 },
    risk_level: { probabilities: { 1: 1 } },
  } }, 0.5)
  const block = transcriptBlock(opinion)

  expect(block.split('\n')).toEqual([
    'Command Gate',
    '  Verdict    🟢 safe',
    '  Risk       🔵 low       100%',
    '  Dangerous  🟢 no          4%',
    '  Root       🟢 no          5%',
  ])
  expect(block.includes('\x1b')).toBe(false)
})

test('each risk level uses its own severity marker', () => {
  const markers = ['🟢', '🔵', '🟡', '🟠', '🔴']
  for (const [index, level] of LEVELS.entries()) {
    const opinion = parseAnswers({ answers: {
      risk_level: { probabilities: { [index]: 1 } },
    } }, 0.5)
    expect(transcriptBlock(opinion)).toContain(`Risk       ${markers[index]} ${level}`)
  }
})

test('missing values have neutral markers rather than safe styling', () => {
  const block = transcriptBlock(parseAnswers({ answers: {} }, 0.5))
  expect(block).toBe([
    'Command Gate',
    '  Verdict    ⚪ n/a',
    '  Risk       ⚪ n/a',
    '  Dangerous  ⚪ n/a',
    '  Root       ⚪ n/a',
  ].join('\n'))
})

test('the answer is forwarded to IDE add-ons that registered a window', async ($, on) => {
  const posts: any[] = []
  mock.clock(on)
  on('tool.check', () => ({ decision: 'ask' }))
  on('ui.notice', () => ({ value: undefined }))
  on('ui.log', () => ({ value: undefined }))
  on('env.get', (_: unknown, e: any) => ({ value: e.name === 'HOME' ? '/home/dev' : undefined }))
  on('fs.exists', () => ({ value: true }))
  on('fs.list', () => ({ value: [{ name: 'w1.json', kind: 'file', size: 10, mtimeMs: 0, isLink: false }] }))
  on('fs.read', () => ({ value: JSON.stringify({ port: 4321, token: 'tok' }) }))
  on('http.fetch', (_: unknown, e: any) => {
    posts.push(e)
    return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify(ANSWER) } }
  })

  await $.tool.check(ASK)

  const ide = posts.find(p => String(p.url).includes(':4321/event'))
  expect(ide.init.headers['x-cmdgate-token']).toBe('tok')
  const event = JSON.parse(ide.init.body)
  expect(event.command).toBe('rm -rf ~/')
  expect(event.result).toEqual(ANSWER)
})

test('shows one row per backend model and takes the verdict from the backend decision', () => {
  const body = {
  answers: { unsafe: { type: 'noul', noul: 0.61 } },
  decision: 'ask', reason: '1 of 2 models above their threshold: kev', mean: 0.61,
  models: { ngram: { p: 0.12, threshold: 0.5, passed: true, failed: false }, kev: { p: 0.91, threshold: 0.33, passed: false, failed: false } },
}
  const opinion = parseAnswers(body, 0.99)
  expect(opinion.verdict?.yes).toBe(true)
  const text = transcriptBlock(opinion)
  expect(text).toContain('Models     ASK · 1 of 2 models above their threshold: kev')
  expect(text).toMatch(/ngram\s+🟢 pass\s+12%/)
  expect(text).toMatch(/kev\s+🔴 FAIL\s+91%/)
  expect(transcriptBlock(parseAnswers({ answers: body.answers }, 0.5)).split('\n').length).toBe(5)
})
