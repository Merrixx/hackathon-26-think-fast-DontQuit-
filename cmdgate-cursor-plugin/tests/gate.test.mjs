import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { mkdir, writeFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { buildRequest, noticeLine, offlineLine, parseAnswers, QUESTIONS } from '../scripts/lib/gate.mjs'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(ROOT, 'scripts', 'gate.mjs')

const ANSWER = {
  answers: {
    dangerous: { type: 'noul', noul: 0.91 },
    requires_root: { type: 'noul', noul: 0.12 },
    risk_level: { type: 'score', probabilities: { 0: 0.02, 1: 0.03, 2: 0.05, 3: 0.2, 4: 0.7 } },
    unsafe: { type: 'noul', noul: 0.95 },
  },
}

function runHook(stdinObj, env = {}) {
  return new Promise((resolve, reject) => {
    const childEnv = { ...process.env, ...env }
    const child = spawn(process.execPath, [SCRIPT], {
      env: childEnv,
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let out = ''
    let err = ''
    child.stdout.on('data', c => { out += c })
    child.stderr.on('data', c => { err += c })
    child.on('error', reject)
    child.on('close', code => resolve({ code, out, err }))
    child.stdin.end(JSON.stringify(stdinObj))
  })
}

function listen(handler) {
  return new Promise(resolve => {
    const server = createServer(handler)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      resolve({
        port,
        url: `http://127.0.0.1:${port}/v1/systemone`,
        close: () => new Promise((r, j) => server.close(e => e ? j(e) : r())),
      })
    })
  })
}

describe('gate parsing', () => {
  it('builds the four questions for a command', () => {
    const sent = buildRequest('rm -rf ~/', 'local')
    assert.deepEqual(sent.state, { command: 'rm -rf ~/' })
    assert.deepEqual(Object.keys(sent.questions), Object.keys(QUESTIONS))
  })

  it('formats a stacked notice from a full answer', () => {
    const plain = noticeLine(parseAnswers(ANSWER, 0.5), { color: false })
    assert.equal(plain, [
      'Command Gate',
      '  Verdict    UNSAFE     95%',
      '  Risk       critical   70%',
      '  Dangerous  yes        91%',
      '  Root       no         12%',
    ].join('\n'))
  })

  it('colours safe green and unsafe red even when NO_COLOR is set', () => {
    const colored = noticeLine(parseAnswers(ANSWER, 0.5), { color: true })
    assert.match(colored, /\x1b\[31mUNSAFE/)
    assert.match(colored, /\x1b\[31myes/)
    assert.match(colored, /\x1b\[32mno/)
    assert.match(colored, /\x1b\[31mcritical/)
  })

  it('missing answers read as n/a, never guessed', () => {
    const plain = noticeLine(parseAnswers({ answers: {} }, 0.5), { color: false })
    assert.match(plain, /Verdict\s+n\/a/)
    assert.match(plain, /Risk\s+n\/a/)
    assert.match(plain, /Dangerous\s+n\/a/)
    assert.match(plain, /Root\s+n\/a/)
  })

  it('offline line names the endpoint and reason', () => {
    const plain = offlineLine('http://127.0.0.1:9/v1/systemone', 'HTTP 500', { color: false })
    assert.match(plain, /no second opinion/)
    assert.match(plain, /127\.0\.0\.1:9/)
    assert.match(plain, /HTTP 500/)
  })
})

describe('beforeShellExecution hook', () => {
  it('a command gets the second opinion as ask (never deny)', async () => {
    const app = await listen((_req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(ANSWER))
    })
    try {
      const { code, out } = await runHook(
        { command: 'rm -rf ~/', hook_event_name: 'beforeShellExecution' },
        { CMDGATE_ENDPOINT: app.url, CMDGATE_FORWARD_TO_IDE: 'false' },
      )
      assert.equal(code, 0)
      const payload = JSON.parse(out)
      assert.equal(payload.permission, 'ask')
      assert.match(payload.user_message, /Command Gate/)
      assert.match(payload.user_message, /\x1b\[31mUNSAFE/)
      assert.match(payload.agent_message, /Verdict\s+UNSAFE/)
      assert.doesNotMatch(payload.agent_message, /\x1b\[/)
    } finally {
      await app.close()
    }
  })

  it('an app that is down says so and still asks (never denies)', async () => {
    const app = await listen((_req, res) => {
      res.writeHead(500, { 'content-type': 'text/plain' })
      res.end('boom')
    })
    try {
      const { code, out } = await runHook(
        { command: 'rm -rf ~/' },
        { CMDGATE_ENDPOINT: app.url, CMDGATE_FORWARD_TO_IDE: 'false' },
      )
      assert.equal(code, 0)
      const payload = JSON.parse(out)
      assert.equal(payload.permission, 'ask')
      assert.match(payload.user_message, /Command Gate/)
      assert.match(payload.user_message, /no second opinion/)
    } finally {
      await app.close()
    }
  })

  it('a slow app times out and still asks (never denies)', async () => {
    const app = await listen(() => { /* never answers */ })
    try {
      const { code, out } = await runHook(
        { command: 'Remove-Item -Recurse C:/Windows' },
        { CMDGATE_ENDPOINT: app.url, CMDGATE_TIMEOUT_MS: '200', CMDGATE_FORWARD_TO_IDE: 'false' },
      )
      assert.equal(code, 0)
      const payload = JSON.parse(out)
      assert.equal(payload.permission, 'ask')
      assert.match(payload.user_message, /no answer within 200 ms/)
    } finally {
      await app.close()
    }
  })

  it('the answer is forwarded to IDE add-ons that registered a window', async () => {
    const events = []
    const ide = await listen((req, res) => {
      if (req.url === '/event') {
        let body = ''
        req.on('data', c => { body += c })
        req.on('end', () => {
          events.push({ token: req.headers['x-cmdgate-token'], body: JSON.parse(body) })
          res.writeHead(200)
          res.end('ok')
        })
        return
      }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(ANSWER))
    })

    const home = join(tmpdir(), `cmdgate-cursor-test-${process.pid}`)
    const winDir = join(home, '.cmdgate', 'windows')
    await mkdir(winDir, { recursive: true })
    await writeFile(join(winDir, 'w1.json'), JSON.stringify({ port: ide.port, token: 'tok' }))

    try {
      const { code, out } = await runHook(
        { command: 'rm -rf ~/' },
        {
          CMDGATE_ENDPOINT: ide.url,
          CMDGATE_FORWARD_TO_IDE: 'true',
          HOME: home,
          USERPROFILE: home,
        },
      )
      assert.equal(code, 0)
      assert.equal(JSON.parse(out).permission, 'ask')
      // Brief wait for the fire-and-forget IDE POST to finish.
      for (let i = 0; i < 20 && events.length === 0; i++) await new Promise(r => setTimeout(r, 25))
      assert.equal(events.length, 1)
      assert.equal(events[0].token, 'tok')
      assert.equal(events[0].body.command, 'rm -rf ~/')
      assert.equal(events[0].body.source, 'Cursor')
      assert.deepEqual(events[0].body.result, ANSWER)
    } finally {
      await ide.close()
      await rm(home, { recursive: true, force: true })
    }
  })

  it('empty command is allowed without a Gate message', async () => {
    const { code, out } = await runHook(
      { command: '   ' },
      { CMDGATE_FORWARD_TO_IDE: 'false', CMDGATE_ENDPOINT: 'http://127.0.0.1:1/nope' },
    )
    assert.equal(code, 0)
    const payload = JSON.parse(out)
    assert.equal(payload.permission, 'allow')
    assert.equal(payload.user_message, undefined)
  })
})
