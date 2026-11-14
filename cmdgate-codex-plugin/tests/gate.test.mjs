import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { buildRequest, noticeBlock, parseAnswers, QUESTIONS } from '../scripts/lib/gate.mjs'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const SCRIPT = join(ROOT, 'scripts', 'gate.mjs')
const SETUP = join(ROOT, 'scripts', 'local-hooks.mjs')
const INPUT = {
  session_id: 'test-session', turn_id: 'test-turn', transcript_path: null,
  cwd: '/workspace', model: 'test', permission_mode: 'default',
  hook_event_name: 'PreToolUse', tool_name: 'Bash', tool_use_id: 'test-call',
  tool_input: { command: 'git status', description: 'Inspect working tree' },
}
const ANSWER = {
  answers: {
    dangerous: { noul: 0.91 }, requires_root: { noul: 0.12 }, unsafe: { noul: 0.95 },
    risk_level: { probabilities: { 0: 0.02, 1: 0.03, 2: 0.05, 3: 0.2, 4: 0.7 } },
  },
}

function run(argv, { stdin = '', env = {}, cwd = ROOT, shell = false } = {}) {
  return new Promise((resolve, reject) => {
    const child = shell
      ? spawn(argv, { shell: true, cwd, env: { ...process.env, ...env }, stdio: ['pipe', 'pipe', 'pipe'] })
      : spawn(process.execPath, argv, { cwd, env: { ...process.env, ...env }, stdio: ['pipe', 'pipe', 'pipe'] })
    let out = ''
    let err = ''
    const timer = setTimeout(() => { child.kill(); reject(new Error('test subprocess timed out')) }, 10000)
    child.stdout.on('data', chunk => { out += chunk })
    child.stderr.on('data', chunk => { err += chunk })
    child.on('error', error => { clearTimeout(timer); reject(error) })
    child.on('close', code => { clearTimeout(timer); resolve({ code, out, err }) })
    child.stdin.end(stdin)
  })
}

function hook(input = INPUT, env = {}) {
  return run([SCRIPT], {
    stdin: JSON.stringify(input),
    env: { CMDGATE_FORWARD_TO_IDE: 'false', CMDGATE_TIMEOUT_MS: '3000', CMDGATE_THRESHOLD: '0.5', ...env },
  })
}

async function listen(handler) {
  const server = createServer(handler)
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const port = server.address().port
  return {
    port, url: `http://127.0.0.1:${port}/v1/systemone`,
    close: () => new Promise((resolve, reject) => {
      server.close(error => error ? reject(error) : resolve())
      server.closeAllConnections()
    }),
  }
}

function jsonReply(response, body = ANSWER) {
  response.writeHead(200, { 'content-type': 'application/json' })
  response.end(JSON.stringify(body))
}

function advisory(result) {
  assert.equal(result.code, 0, result.err)
  assert.equal(result.err, '')
  const payload = JSON.parse(result.out)
  assert.deepEqual(Object.keys(payload), ['systemMessage'])
  assert.equal(typeof payload.systemMessage, 'string')
  assert.doesNotMatch(payload.systemMessage, /\x1b\[/)
  return payload.systemMessage
}

test('request carries the unchanged four-question decision API contract', () => {
  const request = buildRequest('git status', 'local')
  assert.deepEqual(request.state, { command: 'git status' })
  assert.deepEqual(request.questions, QUESTIONS)
  assert.deepEqual(Object.keys(request.questions), ['dangerous', 'requires_root', 'risk_level', 'unsafe'])
})

test('PreToolUse emits only an advisory and sends the exact command and model', async () => {
  let received
  const app = await listen((request, response) => {
    let body = ''
    request.on('data', chunk => { body += chunk })
    request.on('end', () => { received = JSON.parse(body); jsonReply(response) })
  })
  try {
    const message = advisory(await hook(INPUT, { CMDGATE_ENDPOINT: app.url, CMDGATE_MODEL: 'my-backend-model' }))
    assert.equal(message, [
      'Command Gate', '  Verdict    🔴 UNSAFE', '  Risk       🔴 critical   70%',
      '  Dangerous  🔴 yes        91%', '  Root       🟢 no         12%',
    ].join('\n'))
    assert.deepEqual(received, buildRequest('git status', 'my-backend-model'))
  } finally { await app.close() }
})

test('safe and missing answers retain the same marker and probability semantics', () => {
  const opinion = parseAnswers({ answers: {
    unsafe: { noul: 0.04 }, dangerous: { probabilities: { true: 0.04 } },
    requires_root: { noul: 0.05 }, risk_level: { probabilities: { 1: 1 } },
  } }, 0.5)
  assert.match(noticeBlock(opinion), /Verdict\s+🟢 safe/)
  assert.match(noticeBlock(opinion), /Risk\s+🔵 low\s+100%/)
  assert.match(noticeBlock(opinion), /Dangerous\s+🟢 no\s+4%/)
  assert.match(noticeBlock(parseAnswers({ answers: {} }, 0.5)), /Verdict\s+⚪ n\/a/)
  assert.equal(parseAnswers({ answers: { unsafe: { noul: 0 } } }, 0).verdict.yes, true)
})

test('malformed yes/no probabilities are unknown, not fabricated safe answers', () => {
  const opinion = parseAnswers({ answers: {
    dangerous: { noul: '' }, requires_root: { noul: false }, unsafe: { noul: [] },
  } }, 0.5)
  assert.equal(opinion.dangerous, null)
  assert.equal(opinion.root, null)
  assert.equal(opinion.verdict, null)
  assert.doesNotMatch(noticeBlock(opinion), /🟢|🔴/)
})

test('backend HTTP errors and invalid JSON are warnings, not permission decisions', async () => {
  for (const mode of ['http', 'json']) {
    const app = await listen((_request, response) => {
      response.writeHead(mode === 'http' ? 503 : 200)
      response.end('not JSON')
    })
    try {
      const message = advisory(await hook(INPUT, { CMDGATE_ENDPOINT: app.url }))
      assert.match(message, /no second opinion/)
      assert.ok(message.includes(app.url))
      if (mode === 'http') assert.match(message, /HTTP 503/)
    } finally { await app.close() }
  }
})

test('slow backend times out without blocking the tool', async () => {
  const app = await listen(() => {})
  try {
    const message = advisory(await hook(INPUT, { CMDGATE_ENDPOINT: app.url, CMDGATE_TIMEOUT_MS: '50' }))
    assert.match(message, /no answer within 50 ms/)
  } finally { await app.close() }
})

test('empty, malformed, non-shell, and wrong-event inputs are ignored', async () => {
  const ignored = [null, {}, { ...INPUT, tool_name: 'apply_patch' },
    { ...INPUT, hook_event_name: 'PostToolUse' },
    { ...INPUT, tool_input: { command: '  ' } }, { ...INPUT, tool_input: { command: ['git', 'status'] } }]
  for (const input of ignored) {
    const result = await hook(input, { CMDGATE_ENDPOINT: 'http://127.0.0.1:1/unreachable' })
    assert.equal(result.code, 0)
    assert.deepEqual(JSON.parse(result.out), {})
  }
  const invalid = await run([SCRIPT], { stdin: '{bad json' })
  assert.equal(invalid.code, 0)
  assert.deepEqual(JSON.parse(invalid.out), {})
})

test('threshold zero is respected by the actual hook', async () => {
  const app = await listen((_request, response) => jsonReply(response, { answers: { unsafe: { noul: 0 } } }))
  try {
    const message = advisory(await hook(INPUT, { CMDGATE_ENDPOINT: app.url, CMDGATE_THRESHOLD: '0' }))
    assert.match(message, /Verdict\s+🔴 UNSAFE/)
  } finally { await app.close() }
})

test('IDE forwarding includes Codex source, cwd, token, and full backend reply', async () => {
  const home = await mkdtemp(join(tmpdir(), 'cmdgate-codex-home-'))
  const events = []
  const app = await listen((request, response) => {
    if (request.url !== '/event') { jsonReply(response); return }
    let body = ''
    request.on('data', chunk => { body += chunk })
    request.on('end', () => {
      events.push({ token: request.headers['x-cmdgate-token'], body: JSON.parse(body) })
      response.writeHead(202); response.end('{}')
    })
  })
  try {
    const directory = join(home, '.cmdgate', 'windows')
    await mkdir(directory, { recursive: true })
    await writeFile(join(directory, 'broken.json'), '{invalid')
    await writeFile(join(directory, 'window.json'), JSON.stringify({ port: app.port, token: 'test-token' }))
    advisory(await hook(INPUT, {
      CMDGATE_ENDPOINT: app.url, CMDGATE_FORWARD_TO_IDE: 'true', HOME: home, USERPROFILE: home,
    }))
    assert.equal(events.length, 1)
    assert.equal(events[0].token, 'test-token')
    assert.deepEqual(events[0].body, {
      source: 'Codex', tool: 'Bash', command: 'git status', description: 'Inspect working tree',
      cwd: '/workspace', result: ANSWER,
    })
  } finally { await app.close(); await rm(home, { recursive: true, force: true }) }
})

test('packaged hook runs from PLUGIN_ROOT even outside the repository', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'cmdgate plugin space '))
  const plugin = join(directory, 'installed plugin')
  const app = await listen((_request, response) => jsonReply(response))
  try {
    await cp(ROOT, plugin, { recursive: true })
    const manifest = JSON.parse(await readFile(join(plugin, '.codex-plugin', 'plugin.json'), 'utf8'))
    const config = JSON.parse(await readFile(join(plugin, manifest.hooks), 'utf8'))
    const group = config.hooks.PreToolUse[0]
    assert.equal(group.matcher, '^Bash$')
    const result = await run(group.hooks[0].command, {
      shell: true, cwd: directory, stdin: JSON.stringify(INPUT),
      env: { PLUGIN_ROOT: plugin, CMDGATE_ENDPOINT: app.url, CMDGATE_FORWARD_TO_IDE: 'false' },
    })
    assert.match(advisory(result), /Command Gate/)
  } finally { await app.close(); await rm(directory, { recursive: true, force: true }) }
})

test('local setup is idempotent, uses an absolute script path, and preserves unrelated hooks', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'cmdgate codex setup '))
  const file = join(directory, '.codex', 'hooks.json')
  const original = { description: 'Keep this metadata', hooks: {
    SessionStart: [{ hooks: [{ type: 'command', command: 'echo existing' }] }],
    PreToolUse: [{ matcher: 'other-tool', hooks: [{ type: 'command', command: 'echo existing' }] }],
  } }
  try {
    await mkdir(join(directory, '.codex'))
    await writeFile(file, JSON.stringify(original))
    assert.equal((await run([SETUP], { cwd: directory })).code, 0)
    const first = await readFile(file, 'utf8')
    assert.equal((await run([SETUP], { cwd: directory })).code, 0)
    assert.equal(await readFile(file, 'utf8'), first)
    const configured = JSON.parse(first)
    const added = configured.hooks.PreToolUse[1].hooks[0]
    const result = await run(added.command, { shell: true, cwd: directory, stdin: '{}' })
    assert.equal(result.code, 0, result.err)
    assert.deepEqual(JSON.parse(result.out), {})
    assert.equal((await run([SETUP, '--remove'], { cwd: directory })).code, 0)
    assert.deepEqual(JSON.parse(await readFile(file, 'utf8')), original)
  } finally { await rm(directory, { recursive: true, force: true }) }
})

test('local setup refuses malformed existing configuration without overwriting it', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'cmdgate-codex-invalid-'))
  const file = join(directory, '.codex', 'hooks.json')
  try {
    await mkdir(join(directory, '.codex'))
    await writeFile(file, '{bad json')
    const result = await run([SETUP], { cwd: directory })
    assert.equal(result.code, 1)
    assert.equal(await readFile(file, 'utf8'), '{bad json')
  } finally { await rm(directory, { recursive: true, force: true }) }
})
