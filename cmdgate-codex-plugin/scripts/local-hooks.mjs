#!/usr/bin/env node
// Configure this checkout as a project-local Codex hook, without installation.
// Existing unrelated hooks and metadata are preserved. Never edits user config.
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const script = fileURLToPath(new URL('./gate.mjs', import.meta.url)).replaceAll('\\', '/')
const command = `node "${script}"`

function isObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

async function main() {
  const args = process.argv.slice(2)
  if (args.length > 1 || (args.length === 1 && args[0] !== '--remove')) {
    throw new Error('Usage: node scripts/local-hooks.mjs [--remove] (run from the target project root)')
  }
  const remove = args[0] === '--remove'
  const file = join(process.cwd(), '.codex', 'hooks.json')
  let config
  try {
    config = JSON.parse(await readFile(file, 'utf8'))
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    if (remove) {
      console.log('No project hooks file to update.')
      return
    }
    config = { hooks: {} }
  }
  if (!isObject(config) || (config.hooks !== undefined && !isObject(config.hooks))) {
    throw new Error('Expected an object with an object-valued hooks property; existing file was not changed.')
  }
  const hooks = config.hooks ?? {}
  const groups = hooks.PreToolUse ?? []
  if (!Array.isArray(groups) || groups.some(group => !isObject(group) || !Array.isArray(group.hooks))) {
    throw new Error('Expected PreToolUse to contain matcher groups with hooks arrays; existing file was not changed.')
  }
  const remaining = groups.flatMap(group => {
    const handlers = group.hooks.filter(handler => handler?.command !== command)
    return handlers.length === group.hooks.length ? [group] : handlers.length ? [{ ...group, hooks: handlers }] : []
  })
  if (!remove) {
    remaining.push({
      matcher: '^Bash$',
      hooks: [{ type: 'command', command, timeout: 5, statusMessage: 'Command Gate: checking shell command' }],
    })
  }
  const nextHooks = { ...hooks }
  if (remaining.length) nextHooks.PreToolUse = remaining
  else delete nextHooks.PreToolUse
  const next = { ...config, hooks: nextHooks }
  if (JSON.stringify(next) !== JSON.stringify(config)) {
    await mkdir(dirname(file), { recursive: true })
    await writeFile(file, JSON.stringify(next, null, 2) + '\n', 'utf8')
  }
  console.log(remove
    ? 'Removed this checkout\'s Command Gate hook from .codex/hooks.json.'
    : 'Configured .codex/hooks.json. Start Codex and review/trust the hook with /hooks.')
}

main().catch(error => {
  console.error(error.message)
  process.exitCode = 1
})
