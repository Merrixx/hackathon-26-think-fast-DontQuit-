#!/usr/bin/env node
// Command Gate for Cursor: before a shell command runs, ask the local Command
// Gate app about it and attach the answer as user/agent messages. Advisory
// only — never returns deny. Failures fail open so the agent is not blocked.

import { readdir, readFile, access } from 'node:fs/promises'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { buildRequest, noticeLine, offlineLine, parseAnswers } from './lib/gate.mjs'

function env(name, fallback) {
  const v = process.env[name]
  return v === undefined || v === '' ? fallback : v
}

function options() {
  return {
    endpoint: env('CMDGATE_ENDPOINT', 'http://127.0.0.1:8009/v1/systemone'),
    model: env('CMDGATE_MODEL', 'local'),
    timeoutMs: Math.max(1, Number(env('CMDGATE_TIMEOUT_MS', '3000')) || 3000),
    threshold: Number(env('CMDGATE_THRESHOLD', '0.5')) || 0.5,
    forwardToIde: env('CMDGATE_FORWARD_TO_IDE', 'true') !== 'false',
  }
}

async function readStdin() {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(chunk)
  return Buffer.concat(chunks).toString('utf8')
}

async function askApp(o, command) {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), o.timeoutMs)
  try {
    const res = await fetch(o.endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(buildRequest(command, o.model)),
      signal: ctrl.signal,
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return await res.json()
  } finally {
    clearTimeout(timer)
  }
}

// IDE add-ons (such as the VS Code one) each write ~/.cmdgate/windows/<id>.json
// with a port and token; send them the command and the app's answer.
async function forwardToIdes(event) {
  const dir = join(homedir(), '.cmdgate', 'windows')
  try {
    await access(dir)
  } catch {
    return
  }
  const names = (await readdir(dir)).filter(n => n.endsWith('.json'))
  await Promise.allSettled(names.map(async name => {
    let win
    try {
      win = JSON.parse(await readFile(join(dir, name), 'utf8'))
    } catch {
      return
    }
    if (!win.port || !win.token) return
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 1000)
    try {
      await fetch(`http://127.0.0.1:${win.port}/event`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-cmdgate-token': win.token },
        body: JSON.stringify(event),
        signal: ctrl.signal,
      })
    } finally {
      clearTimeout(timer)
    }
  }))
}

function respond(payload) {
  process.stdout.write(JSON.stringify(payload))
}

async function main() {
  const o = options()
  let input
  try {
    input = JSON.parse(await readStdin())
  } catch {
    // Invalid hook input: fail open with a valid schema so Cursor does not block.
    respond({ permission: 'allow' })
    return
  }

  const command = input.command
  if (typeof command !== 'string' || !command.trim()) {
    respond({ permission: 'allow' })
    return
  }

  let body = null
  let forUser
  let forAgent
  try {
    body = await askApp(o, command)
    const opinion = parseAnswers(body, o.threshold)
    forUser = noticeLine(opinion, { color: true })
    forAgent = noticeLine(opinion, { color: false })
  } catch (err) {
    const reason = err?.name === 'AbortError'
      ? `no answer within ${o.timeoutMs} ms`
      : (err instanceof Error ? err.message : String(err))
    forUser = offlineLine(o.endpoint, reason, { color: true })
    forAgent = offlineLine(o.endpoint, reason, { color: false })
  }

  if (o.forwardToIde) {
    await forwardToIdes({
      source: 'Cursor',
      tool: 'Shell',
      command,
      description: typeof input.description === 'string' ? input.description : '',
      result: body,
    }).catch(() => undefined)
  }

  // Never deny. Return ask so Cursor shows an approval prompt with the Gate
  // block (user_message). Plain allow hides the message in the CLI. Colours are
  // for the user prompt only; the agent gets a plain-text copy.
  respond({
    permission: 'ask',
    user_message: forUser,
    agent_message: forAgent,
  })
}

main().catch(() => {
  respond({ permission: 'allow' })
  process.exit(0)
})
