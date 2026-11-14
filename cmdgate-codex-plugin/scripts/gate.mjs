#!/usr/bin/env node
// Codex PreToolUse hook. Only systemMessage is returned: never a permission
// decision, command rewrite, blocking exit code, or model-visible context.
import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { homedir } from 'node:os'
import { buildRequest, noticeBlock, offlineBlock, parseAnswers } from './lib/gate.mjs'

function env(name, fallback) {
  const value = process.env[name]
  return value === undefined || value === '' ? fallback : value
}

function options() {
  const timeout = Number(env('CMDGATE_TIMEOUT_MS', '3000'))
  const threshold = Number(env('CMDGATE_THRESHOLD', '0.5'))
  return {
    endpoint: env('CMDGATE_ENDPOINT', 'http://127.0.0.1:8019/v1/systemone'),
    model: env('CMDGATE_MODEL', 'local'),
    timeoutMs: Number.isFinite(timeout) && timeout > 0 ? Math.max(1, Math.floor(timeout)) : 3000,
    threshold: Number.isFinite(threshold) && threshold >= 0 && threshold <= 1 ? threshold : 0.5,
    forwardToIde: env('CMDGATE_FORWARD_TO_IDE', 'true') !== 'false',
  }
}

async function readStdin() {
  const chunks = []
  for await (const chunk of process.stdin) chunks.push(chunk)
  return JSON.parse(Buffer.concat(chunks).toString('utf8'))
}

async function askApp(o, command) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), o.timeoutMs)
  try {
    const response = await fetch(o.endpoint, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(buildRequest(command, o.model)),
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    return await response.json()
  } finally {
    clearTimeout(timer)
  }
}

async function forwardToIdes(event) {
  const directory = join(homedir(), '.cmdgate', 'windows')
  let entries
  try {
    entries = await readdir(directory, { withFileTypes: true })
  } catch {
    return
  }
  await Promise.allSettled(entries.filter(entry => entry.isFile() && entry.name.endsWith('.json')).map(async entry => {
    const window = JSON.parse(await readFile(join(directory, entry.name), 'utf8'))
    if (!Number.isInteger(window.port) || window.port < 1 || window.port > 65535 ||
        typeof window.token !== 'string' || !window.token) return
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), 1000)
    try {
      await fetch(`http://127.0.0.1:${window.port}/event`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', 'x-cmdgate-token': window.token },
        body: JSON.stringify(event),
        signal: controller.signal,
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
  let input
  try {
    input = await readStdin()
  } catch {
    respond({})
    return
  }
  const command = input?.tool_input?.command
  if (input?.hook_event_name !== 'PreToolUse' || input.tool_name !== 'Bash' ||
      typeof command !== 'string' || !command.trim()) {
    respond({})
    return
  }

  const o = options()
  let body = null
  let message
  try {
    body = await askApp(o, command)
    message = noticeBlock(parseAnswers(body, o.threshold))
  } catch (error) {
    const reason = error?.name === 'AbortError'
      ? `no answer within ${o.timeoutMs} ms`
      : error instanceof Error ? error.message : String(error)
    message = offlineBlock(o.endpoint, reason)
  }

  if (o.forwardToIde) {
    await forwardToIdes({
      source: 'Codex', tool: input.tool_name, command,
      description: typeof input.tool_input.description === 'string' ? input.tool_input.description : '',
      cwd: typeof input.cwd === 'string' ? input.cwd : '',
      result: body,
    }).catch(() => undefined)
  }
  respond({ systemMessage: message })
}

main().catch(() => {
  // Invalid inputs and unexpected failures must not change Codex permissions.
  respond({})
})
