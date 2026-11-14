import { expect, mock, test } from 'claude-code/testing'

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
function engine(on: any, opts: { decision?: string; reply?: () => unknown } = {}) {
  const seen = { notices: [] as unknown[], requests: [] as any[] }
  mock.clock(on) // the timeout timer; it never fires unless a test advances it
  on('tool.check', () => ({ decision: opts.decision ?? 'ask' }))
  on('http.fetch', (_: unknown, e: any) => {
    seen.requests.push(e)
    return { value: opts.reply ? opts.reply() : { status: 200, ok: true, headers: {}, text: JSON.stringify(ANSWER) } }
  })
  on('ui.notice', (_: unknown, e: unknown) => {
    seen.notices.push(e)
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

test('a command that runs without asking is not checked', async ($, on) => {
  const seen = engine(on, { decision: 'allow' })
  const verdict = await $.tool.check(ASK)

  expect(verdict.decision).toBe('allow')
  expect(seen.requests.length).toBe(0)
  expect(seen.notices.length).toBe(0)
})

test('an app that is down says so and leaves the decision alone', async ($, on) => {
  const seen = engine(on, { reply: () => ({ status: 500, ok: false, headers: {}, text: 'boom' }) })
  const verdict = await $.tool.check(ASK)

  expect(verdict.decision).toBe('ask')
  expect(JSON.stringify(seen.notices)).toContain('Command Gate: no second opinion')
})

test('missing answers read as n/a, never guessed', async ($, on) => {
  const seen = engine(on, { reply: () => ({ status: 200, ok: true, headers: {}, text: '{"answers":{}}' }) })
  await $.tool.check(ASK)

  expect(JSON.stringify(seen.notices)).toContain('no verdict · risk n/a · dangerous n/a · root n/a')
})

test('a slow app times out and the dialog still opens as decided', async ($, on) => {
  const seen = { notices: [] as unknown[] }
  const clock = mock.clock(on)
  on('tool.check', () => ({ decision: 'ask' }))
  on('http.fetch', () => new Promise(() => {})) // never answers
  on('ui.notice', (_: unknown, e: unknown) => { seen.notices.push(e); return { value: undefined } })
  on('env.get', () => ({ value: undefined }))

  const pending = $.tool.check({ ...ASK, tool: 'PowerShell', input: { command: 'Remove-Item -Recurse C:/Windows' } })
  await clock.advance(3000)
  const verdict = await pending

  expect(verdict.decision).toBe('ask')
  expect(JSON.stringify(seen.notices)).toContain('no answer within 3000 ms')
})

test('the answer is forwarded to IDE add-ons that registered a window', async ($, on) => {
  const posts: any[] = []
  mock.clock(on)
  on('tool.check', () => ({ decision: 'ask' }))
  on('ui.notice', () => ({ value: undefined }))
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
