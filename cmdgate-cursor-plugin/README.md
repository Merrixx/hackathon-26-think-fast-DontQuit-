# Command Gate for Cursor

A second opinion when Cursor Agent is about to run a shell command. The plugin
asks your locally running Command Gate app about that command and attaches a
stacked, colour-coded block on the approval prompt (safe/green, unsafe/red):

```
Command Gate
  Verdict    UNSAFE     95%
  Risk       critical   70%
  Dangerous  yes        91%
  Root       no         12%
```

Colours on the approval prompt: **safe / no** in green, **UNSAFE / yes** in red,
risk steps from green → cyan → yellow → orange → red. Percents use the same
colour as their value. The agent gets a plain-text copy (no ANSI).

It uses Cursor's `beforeShellExecution` hook, so it works in Agent Chat wherever
Cursor runs the agent. Optional: the [VS Code / Cursor add-on](../vscode-extension/README.md)
shows the same answer as a styled card.

The plugin **only advises**. It never returns `deny`. It returns `ask` with the
Gate line in `user_message` / `agent_message` so Cursor draws it on the shell
approval prompt (plain `allow` runs the hook but shows nothing in the CLI).
It runs no model itself: every answer comes from the app. If the app is down or
slow, the prompt still opens with an offline line.

## The backend and what each model said

Point the plugin at the hook backend of this repo, `uv run --extra ml python -m cmdhook.start` (default `http://127.0.0.1:8019/v1/systemone`). It answers the four questions from the models set in `cmdhook/.env` and adds its own decision and every model's result, which the plugin shows as extra rows:

```text
Command Gate
  Verdict    🔴 UNSAFE
  Risk       🟠 high       57%
  Dangerous  🔴 yes        72%
  Root       ⚪ n/a
  Models     ASK · 3 of 3 models above their threshold: rules, ngram-plus, kev
  rules      🔴 FAIL       69%
  ngram-plus 🔴 FAIL       86%
  kev        🔴 FAIL       94%
```

- **Verdict** is the backend's decision when it sends one (`ask` or `deny` reads as UNSAFE), not this plugin's own threshold.
- **Risk** and **Dangerous** come from the backend models that have risk levels; Dangerous is P(high or critical). With only yes/no models such as Kev they read `n/a`.
- **Root** reads `n/a`: no model in the backend answers it.
- **Models** has one row per backend model: its own risk and whether it passed its threshold. A backend that sends no `models` field gets no extra rows.

## What it checks, and when

- **Every shell command** the Cursor agent is about to run (`beforeShellExecution`).
  Cursor hooks do not expose whether a human would already be asked, so unlike
  the [Claude Code plugin](../cmdgate-claude-plugin/README.md) this cannot limit itself
  to “prompt about to open” only.
- Permission is always `ask` (never `deny`), with the Gate line on the prompt.

## Requirements

- Cursor with Agent hooks (plugin hooks / `hooks.json`). See
  [Cursor Hooks](https://cursor.com/docs/hooks).
- Node.js 18+ on `PATH` (the hook script is plain Node, no install step).
- The Command Gate app (or any engine) serving the decision API locally:
  `POST /v1/systemone`, see [../docs/decision-api.md](../docs/decision-api.md).
  Default endpoint: `http://127.0.0.1:8019/v1/systemone`.
  Without the real backend, the fake one from the repo root works for a demo:
  `node vscode-extension/demo/mock-app.js` (made-up answers).

## Install and try it

These are **PowerShell** commands, run from the **repository root**. Choose a
backend in step 1, then an installed or local plugin in step 3. That gives all
four combinations: demo/installed, demo/local, real/installed, and real/local.
Node.js and the **Cursor Agent CLI** must already be installed. `agent` is the
agent CLI; `cursor .` only opens the editor and is not a substitute. If your
installation calls the agent executable `cursor-agent`, substitute that name.
Starting an agent session uses your normal Cursor account.

```powershell
agent --help
agent login
```

Log in only if needed. On a platform without a native Agent CLI, run it in a
supported environment, with Node and the checkout available there. In WSL, use
the corresponding shell syntax rather than pasting these PowerShell commands
into Bash; the backend IP must be reachable from that environment.

### 1A. Demo backend: start the test server in the background

Use port **8010** to keep the demo separate from the default real-backend port.
Do not start a second mock server if one is already listening there.

```powershell
$mockJob = Start-Job -ArgumentList (Get-Location).Path -ScriptBlock {
    param($repo)
    Set-Location $repo
    node ./vscode-extension/demo/mock-app.js --port 8010
}
$endpoint = 'http://127.0.0.1:8010/v1/systemone'

# Read the startup banner; repeat this if the job is still starting.
Receive-Job $mockJob -Keep
# Once the banner appears, this should return ok=True and demo=True.
Invoke-RestMethod 'http://127.0.0.1:8010/health'
```

Leave this PowerShell window open: it owns the background job. The mock returns
**made-up answers**, not model results. Continue with step 2 in this window.

### 1B. Real backend: select its IP and port

Instead of step 1A, connect to your already running real decision API:

```powershell
$backendIp = Read-Host 'Real backend IP or hostname (as reachable from this machine)'
$backendPort = Read-Host 'Real backend port'
$endpoint = "http://${backendIp}:$backendPort/v1/systemone"
$endpoint
Test-NetConnection -ComputerName $backendIp -Port ([int]$backendPort)
```

Use `https://` in `$endpoint` if your backend requires TLS. TCP success only
checks connectivity, not the API contract; the smoke test below checks the
request/answer path. Do not start the mock for this route. The backend must
implement [the decision API](../docs/decision-api.md); its startup command
depends on the backend you run. A non-loopback endpoint receives command text
over the network: only point it at an authorized, trusted service.

### 2. Pass the selected endpoint to the plugin

Set these in the **same shell that will launch `agent`**:

```powershell
$env:CMDGATE_ENDPOINT = $endpoint
$env:CMDGATE_MODEL = 'local'
$env:CMDGATE_TIMEOUT_MS = '3000'
$env:CMDGATE_THRESHOLD = '0.5'
$env:CMDGATE_FORWARD_TO_IDE = 'true'
```

Change `CMDGATE_MODEL` if your real backend requires a particular model
identifier. These environment variables configure the hook, not Cursor's own
chat model. If an installed plugin has an explicit `CMDGATE_ENDPOINT` variable
set in **Plugins → Configure**, keep it aligned with `$endpoint` as well.

### 3A. Install the plugin, then run the CLI

Start the interactive agent:

```powershell
agent
```

At its chat prompt, open the plugin manager:

```text
/plugin
```

In the manager, add this repository as a marketplace and install/enable
`cmdgate` from `dontquit`:

```text
https://github.com/Merrixx/hackathon-26-think-fast-DontQuit-
```

The marketplace catalog is [`.cursor-plugin/marketplace.json`](../.cursor-plugin/marketplace.json).
You need repository access if it is private; installation uses the published
repository version, not uncommitted local edits. `/plugin` is the documented
CLI entry point; follow its marketplace/install controls rather than assuming
Claude's `claude plugin install` syntax works in Cursor. If your CLI version
cannot add this marketplace, use Cursor's **Customize → Plugins** install flow
with the same catalog, then restart the agent. Marketplace availability may
depend on your Cursor plan or organization policy.

After installing, exit and relaunch from the shell configured in step 2:

```powershell
agent
```

### 3B. Run the CLI with local plugin files, without installation

Instead of step 3A, run:

```powershell
agent --plugin-dir ./cmdgate-cursor-plugin
```

This is a direct CLI plugin load: no marketplace installation, dependency
installation, build, or `.cursor/hooks.json` file is needed. Restart the CLI
after changing plugin configuration. If you already installed `cmdgate`,
disable that copy through `/plugin` before this test. Also remove/disable any
project hook pointing at the same script to avoid duplicate requests. Restore
those only after you finish testing the local plugin.

See the official [CLI parameters](https://cursor.com/docs/cli/reference/parameters)
and [slash commands](https://cursor.com/docs/cli/reference/slash-commands) for
`--plugin-dir` and `/plugin`. Do not use `--force`, `--yolo`, Run Everything,
or `--print` when testing interactive approval prompts.

### 4. Test the CLI and inspect the result

Paste this into the **agent chat**, not directly into PowerShell:

```text
Run git status as one shell tool call. Do not run any other commands.
```

The shell approval prompt should include a **Command Gate** block with
**Verdict**, **Risk**, **Dangerous**, and **Root** fields. You can reject the
command: it need not execute to test the plugin.

- **Demo:** `git status` should be `SAFE`, risk `safe`, dangerous `no`, root `no`,
  each with a probability. Values use green for safe/no and red for unsafe/yes
  where the CLI renders ANSI colours. In PowerShell, `Receive-Job $mockJob -Keep`
  should show the command logged by the mock server.
- **Real backend:** those fields reflect the actual API reply. No particular
  verdict or percentage is guaranteed. Missing answers show `n/a`.
- **Optional IDE add-on:** with forwarding enabled, the same command should
  also appear as a second-opinion card in the editor.

To test a warning **without any chance of executing the supplied command**,
exit the CLI and send text straight to the local hook script:

```powershell
'{"command":"git push --force origin main"}' | node ./cmdgate-cursor-plugin/scripts/gate.mjs
```

This prints JSON with `permission: "ask"`, `user_message`, and `agent_message`.
The demo's message should say `UNSAFE`, risk `high`. It only scores text; it
**does not execute the force-push**. This direct test checks the local script
and selected backend, not installed-plugin discovery or Cursor's prompt UI.

If you see an offline/no-answer message, the hook ran but could not obtain an
answer: check the selected IP/port, server logs, firewall, and timeout. A valid
command still gets `ask`, not `deny`. If no message or backend request appears,
check that the plugin is enabled and restart the CLI.

### 5. Clean up

Exit the agent. Only if you started the demo job in step 1A:

```powershell
Stop-Job $mockJob
Remove-Job $mockJob
```

The `CMDGATE_*` values above last for this shell and its child processes. Close
it when finished, or restore any previous values before using another backend.
Do not stop the real backend as part of this test. See **Tests** below for local
automated checks that do not require installing the plugin.

## Settings

Configure via environment variables (or plugin variables of the same names in
Plugins → Configure when installed as a Cursor plugin):

| Setting | Default | Purpose |
|---|---|---|
| `CMDGATE_ENDPOINT` | `http://127.0.0.1:8019/v1/systemone` | Where the app listens |
| `CMDGATE_MODEL` | `local` | Sent as `model`, to pick one of the app's models |
| `CMDGATE_TIMEOUT_MS` | `3000` | How long to wait before saying there is no answer |
| `CMDGATE_THRESHOLD` | `0.5` | Probability at which a yes/no answer reads as yes |
| `CMDGATE_FORWARD_TO_IDE` | `true` | Also send each result to IDE add-ons on this machine |

## What the app must answer

One request per command, the command as `state`, four questions — same contract
as the Claude Code plugin:

```json
{ "model": "local", "state": { "command": "…" },
  "questions": { "dangerous": {"type": "noul", …}, "requires_root": {"type": "noul", …},
                 "risk_level": {"type": "score", "criteria": [5 levels]}, "unsafe": {"type": "noul", …} } }
```

The plugin reads `answers.<id>.noul` for the yes/no questions and
`answers.risk_level.probabilities` (`"0"`–`"4"`, safe to critical) for the
level. A missing answer reads as `n/a`; nothing is guessed. The question
wording is in [scripts/lib/gate.mjs](scripts/lib/gate.mjs).

## IDE add-ons (optional)

With `CMDGATE_FORWARD_TO_IDE` on, each result also goes to IDE add-ons that want
a richer display, such as the [VS Code add-on](../vscode-extension/README.md).
The protocol matches the Claude Code plugin (`source` is `"Cursor"`):

1. The add-on listens on `127.0.0.1` on any port and writes
   `~/.cmdgate/windows/<id>.json` containing `{"port": <port>, "token": "<random>"}`.
   It deletes the file when it closes.
2. For every checked command, the plugin sends `POST http://127.0.0.1:<port>/event`
   with the header `x-cmdgate-token: <token>` and this body:
   ```json
   { "source": "Cursor", "tool": "Shell", "command": "…",
     "description": "",
     "result": { "answers": { … } } }
   ```
   `result` is the app's reply as it came, or `null` when the app did not answer.
3. The add-on answers at once (any 2xx) and shows the result. It has one
   second; the shell prompt never waits on it.

Nothing leaves the machine: the plugin talks only to the configured endpoint
and to `127.0.0.1`.

## Tests

Run from the repo root against the local files (no plugin installation needed):

```sh
node --test cmdgate-cursor-plugin/tests/gate.test.mjs
```

The tests cover:

- the notice line for a scored command;
- that valid commands still get `ask` (never `deny`) when the app fails, is slow or answers partly;
- forwarding to an IDE window.
