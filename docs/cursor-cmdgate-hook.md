# Command Gate for Cursor

A Cursor project hook that asks the local Command Gate decision API
(`POST /v1/systemone`), then surfaces a second opinion on agent shell commands
via `beforeShellExecution`.

Wire format matches the VS Code add-on and the Claude Code plugin
(`feature/claude-plugin` → `cmdgate-plugin/`): four questions
(`dangerous`, `requires_root`, `risk_level`, `unsafe`), same notice line shape.
See [vscode-extension/src/core.js](../vscode-extension/src/core.js) for the
shared question IDs and [docs/decision-api.md](decision-api.md) for the endpoint.

## Requirements

- Cursor with project hooks enabled (Hooks settings / Hooks output channel).
- `python3` on `PATH` (stdlib only; no uv/node needed for the hook).
- Command Gate app (or any engine) on
  `http://127.0.0.1:8009/v1/systemone` (or set `CMDGATE_ENDPOINT`).
  Demo without a model: `node vscode-extension/demo/mock-app.js`.

## Install / enable

1. Open this repository in Cursor (project hooks load from `.cursor/hooks.json`).
2. Confirm Hooks are on for the workspace; Cursor reloads `hooks.json` on save.
3. Start the decision API, then run any agent shell command.

Files:

| Path | Role |
|---|---|
| [`.cursor/hooks.json`](../.cursor/hooks.json) | Registers `beforeShellExecution` |
| [`.cursor/hooks/cmdgate.py`](../.cursor/hooks/cmdgate.py) | Calls the app; prints Cursor hook JSON |

No separate install step. Does not change Claude Code settings
(`~/.claude/settings.json`) or the VS Code add-on.

## Settings (environment)

| Variable | Default | Purpose |
|---|---|---|
| `CMDGATE_ENDPOINT` | `http://127.0.0.1:8009/v1/systemone` | Decision API URL |
| `CMDGATE_MODEL` | `local` | Sent as `model` |
| `CMDGATE_TIMEOUT_MS` | `3000` | HTTP timeout |
| `CMDGATE_THRESHOLD` | `0.5` | Yes/no cut |
| `CMDGATE_FORWARD_TO_IDE` | `true` | Also POST to `~/.cmdgate/windows` (VS Code add-on) |
| `CMDGATE_MODE` | `ask_on_flag` | See below |

### `CMDGATE_MODE`

| Value | Behavior |
|---|---|
| `ask_on_flag` (default) | `ask` when unsafe / dangerous / high+ / root; else `allow`. Line always goes to the agent. |
| `advise` | Always `allow`; only `agent_message` (closest to Claude: never changes the decision). |
| `always_ask` | Always `ask` with the gate line as `user_message`. |

Fail-open: if the app is down or slow, the hook returns `allow` and an offline line
(unless `always_ask`).

## Vs Claude Code plugin

The Claude plugin lives on branch `feature/claude-plugin` (`cmdgate-plugin/`).

| | Claude (`cmdgate-plugin`) | Cursor (this hook) |
|---|---|---|
| Event | `tool.check` on Bash/PowerShell | `beforeShellExecution` |
| When it runs | Only when Claude already decided `ask` | Every agent shell command |
| Decision | Never changed | Default `ask_on_flag`; set `advise` to never deny/ask |
| UI | Notice under Claude's permission dialog | `user_message` / `agent_message` on the Cursor permission card |
| API body | Identical four-question request | Identical |
| IDE forward | `~/.cmdgate/windows` | Same protocol (`source: "Cursor"`) |

## Smoke test

With the mock app running:

```bash
printf '%s' '{"command":"sudo rm -rf /var"}' | .cursor/hooks/cmdgate.py
```

Expect JSON with `permission` and a `Command Gate: …` line in `agent_message`
(and `user_message` when asking).
