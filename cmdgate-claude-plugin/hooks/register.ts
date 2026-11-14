// Command Gate for Claude Code: when Claude Code is about to ask you to approve
// a shell command, ask the local Command Gate app about it and show the answer
// in the transcript and under the permission dialog. It never changes the decision: the
// verdict beneath is returned as it came, whatever the app says or if it fails.

import type { EngineInterface, Register } from 'claude-code'
import { buildRequest, noticeLine, offlineBlock, offlineLine, parseAnswers, transcriptBlock } from './gate.ts'

type Options = { endpoint: string; model: string; timeoutMs: number; threshold: number; forwardToIde: boolean }

class Timeout extends Error {}

async function withTimeout<T>($: EngineInterface, ms: number, work: Promise<T>): Promise<T> {
  const timer = $.clock.sleep(ms).then(() => {
    throw new Timeout(`no answer within ${ms} ms`)
  })
  return Promise.race([work, timer])
}

async function askApp($: EngineInterface, o: Options, command: string) {
  const res = await withTimeout($, o.timeoutMs, $.http.fetch(o.endpoint, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(buildRequest(command, o.model)),
  }))
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return JSON.parse(res.text) as unknown
}

// IDE add-ons (such as the VS Code one) each write ~/.cmdgate/windows/<id>.json
// with a port and token; send them the command and the app's answer.
async function forwardToIdes($: EngineInterface, event: Record<string, unknown>) {
  const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME'))
  if (!home) return
  const dir = `${home}/.cmdgate/windows`
  if (!(await $.fs.exists(dir))) return
  const entries = await $.fs.list(dir)
  await Promise.allSettled(entries.filter(f => f.name.endsWith('.json')).map(async f => {
    const win = JSON.parse(await $.fs.read(`${dir}/${f.name}`)) as { port?: number; token?: string }
    if (!win.port || !win.token) return
    await withTimeout($, 1000, $.http.fetch(`http://127.0.0.1:${win.port}/event`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', 'x-cmdgate-token': win.token },
      body: JSON.stringify(event),
    }))
  }))
}

export const register: Register = (on, options) => {
  const o = options as unknown as Options

  on('tool.check', { tool: ['Bash', 'PowerShell'] }, async ($, e, next) => {
    const verdict = await next(e)
    const input = (e.input ?? {}) as { command?: unknown; description?: unknown }
    const command = input.command
    // Only commands a person is about to be asked about: nothing that runs unasked.
    if (verdict.decision !== 'ask' || !e.tool_use_id || e.approver !== 'person' || typeof command !== 'string') {
      return verdict
    }
    let body: unknown = null
    let line: string
    let block: string
    try {
      body = await askApp($, o, command)
      const opinion = parseAnswers(body, o.threshold)
      line = noticeLine(opinion)
      block = transcriptBlock(opinion)
    } catch (err) {
      const reason = err instanceof Error ? err.message : String(err)
      line = offlineLine(o.endpoint, reason)
      block = offlineBlock(o.endpoint, reason)
    }
    // Some permission dialogs do not paint ui.notice; keep the evaluation
    // visible in the transcript as well, without sending it to the model.
    // ui.log accepts one line per call. Embedded line endings can be drawn
    // as replacement characters by the CLI's system-notice renderer.
    for (const row of block.split(/\r\n?|\n/)) $.ui.log(row)
    $.ui.notice(e.tool_use_id, line)
    if (o.forwardToIde) {
      await forwardToIdes($, { source: 'Claude Code', tool: e.tool, command,
        description: typeof input.description === 'string' ? input.description : '', result: body }).catch(() => undefined)
    }
    return verdict
  }).catch(($, e, next) => next(e)) // a failure here must never change the decision
}
