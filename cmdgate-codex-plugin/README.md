# Command Gate for Codex CLI

A second opinion before Codex runs a shell command. It uses Codex's native
`PreToolUse` hook and returns a user-visible `systemMessage` with the same
four-row layout as the Claude plugin. No IDE extension or CLI wrapper is required.

For the mock backend's `mkdir` smoke test, the evaluation text is:

```text
Command Gate
  Verdict    🟢 safe
  Risk       🔵 low        69%
  Dangerous  🟢 no          4%
  Root       🟢 no          5%
```

Green means safe/no, red means unsafe/yes. Risk uses green → blue → yellow →
orange → red. Missing answers show a neutral marker and `n/a`. These are Unicode
status markers, not ANSI-coloured text; appearance depends on your terminal/font.
Codex controls the surrounding warning, placement, and any collapsed details.
The hook requests this layout, rather than replacing Codex's approval dialog.

The plugin is **advisory only**. It never returns a permission decision, rewrites
a command, injects model context, or exits with Codex's blocking code. Backend
failures produce an offline warning; malformed/non-shell hook input is ignored.
It runs no model itself: evaluations come from your configured backend.

## What it checks, and when

- Every matching shell command **before execution**, not only approval prompts.
- Codex's canonical shell-hook name is `Bash`, including unified exec
  (`exec_command`); the hook reads the string in `tool_input.command`.
- Existing Codex sandbox and approval rules remain unchanged. This hook does
  **not** force an approval prompt for commands that Codex otherwise allows.
- File edits, MCP tools, unrelated events, and subsequent `write_stdin` polling
  are not scored by this hook.

## Requirements

- Codex CLI with native `PreToolUse` hooks and `systemMessage` support.
  CLI commands, marketplace installation, and a real model-backed `git status`
  hook invocation were checked against **0.169.0**. The headless `exec --json`
  run reached the backend but did not include the hook warning in its JSONL
  output; interactive TUI appearance still needs a visual check. The Node tests
  below validate the advisory payload independently.
- Node.js **18+** on `PATH`; no npm dependencies or build step for this plugin.
- A backend implementing [`POST /v1/systemone`](../docs/decision-api.md).
- Hook trust: review the hook with **`/hooks`** in Codex. New/changed untrusted
  hooks are skipped until reviewed. Organization policy can prohibit local hooks.

Official references: [hooks](https://developers.openai.com/codex/hooks/),
[plugins](https://developers.openai.com/codex/plugins/), and
[plugin packaging](https://developers.openai.com/plugins/build/plugins).

## Install and try it

All commands below are **PowerShell**, run from the **repository root**. Choose
one backend in step 1 and one loading method in step 3. Both loading methods work
with both backends. Codex and Node must already be installed. A Codex model
session uses your account and can incur usage charges.

### 1A. Demo backend: run the test server in the background

Use port **8010** to distinguish the demo from the default real-backend port.
If the mock is already running there, reuse it; do not start a second copy.

```powershell
$mockJob = Start-Job -ArgumentList (Get-Location).Path -ScriptBlock {
    param($repo)
    Set-Location $repo
    node ./vscode-extension/demo/mock-app.js --port 8010
}
$endpoint = 'http://127.0.0.1:8010/v1/systemone'

# Repeat until the startup banner appears:
Receive-Job $mockJob -Keep
# Then expect ok=True and demo=True:
Invoke-RestMethod 'http://127.0.0.1:8010/health'
```

Keep this PowerShell window open. The mock returns **made-up answers, not model
results**. If reusing an existing mock, just set `$endpoint` and check `/health`.

### 1B. Real backend: choose the IP and port

Instead of step 1A, connect to your already running decision API:

```powershell
$backendIp = Read-Host 'Real backend IP or hostname reachable from this machine'
$backendPort = Read-Host 'Real backend port'
$endpoint = "http://${backendIp}:$backendPort/v1/systemone"
$endpoint
Test-NetConnection -ComputerName $backendIp -Port ([int]$backendPort)
```

Use `https://` if required by the backend. A successful TCP check only confirms
connectivity, not API compatibility. The plugin does not start your real backend;
its startup command depends on the backend you use. A remote endpoint receives
command text over the network: only use an authorized, trusted service. Do not
run the mock for this route.

### 2. Configure the selected backend

In the **same shell that will launch Codex**:

```powershell
$env:CMDGATE_ENDPOINT = $endpoint
$env:CMDGATE_MODEL = 'local'
$env:CMDGATE_TIMEOUT_MS = '3000'
$env:CMDGATE_THRESHOLD = '0.5'
$env:CMDGATE_FORWARD_TO_IDE = 'true'
```

Change `CMDGATE_MODEL` if your real backend needs a particular identifier. This
selects the evaluation backend's model, not Codex's own chat model. These values
apply to this shell and its child processes; set them again in a new terminal.

### 3A. Install the plugin, then run Codex CLI

Install from the marketplace in this checkout:

```powershell
codex plugin marketplace add .
codex plugin add cmdgate@dontquit
codex plugin list --marketplace dontquit
codex --no-daemon --no-alt-screen --sandbox workspace-write --ask-for-approval on-request
```

Alternatively, for a published copy of the repository, use this **instead of**
the local marketplace-add command:

```powershell
codex plugin marketplace add Merrixx/hackathon-26-think-fast-DontQuit-
```

Only add one source named `dontquit`. Remote installation requires repository
access if private and a published version containing this plugin. Local edits
do not automatically update an installed cached copy. After updates, reinstall
or refresh through Codex's plugin manager, restart, and review `/hooks` again.

In the new CLI session, enter:

```text
/hooks
```

Review and trust the **Command Gate** `PreToolUse` hook. `/plugins` opens Codex's
plugin browser if you prefer its install/enable controls. Then continue to step 4.

### 3B. Use local plugin files without installing

Instead of step 3A, pass the hook explicitly through Codex's native session
configuration. This avoids depending on discovery of a project `hooks.json`
file, which did not expose the hook in `/hooks` in our interactive CLI check.
Run from the repository root:

```powershell
# Remove this checkout's old project-file registration if you used the earlier helper:
node ./cmdgate-codex-plugin/scripts/local-hooks.mjs --remove

$hookConfig = "hooks.PreToolUse=[{matcher='^Bash$',hooks=[{type='command',command='node ./cmdgate-codex-plugin/scripts/gate.mjs',timeout=5,statusMessage='Command Gate: checking shell command'}]}]"
codex --config $hookConfig --no-daemon --no-alt-screen --sandbox workspace-write --ask-for-approval on-request
```

The override applies only to this process; include it every time you launch a
local-files session. No marketplace install, `--plugin-dir`, generated settings
file, dependency install, or build step is needed. Its PowerShell/TOML syntax
was validated with Codex without making a model call; the same native override
mechanism invoked the hook in the model-backed smoke test.

Trust the project when Codex asks, then use `/hooks` to review and trust this
hook. If an installed `cmdgate` plugin or another copy of the hook is enabled,
disable/remove that copy first to avoid duplicate requests and messages. Code
changes may require renewed hook review. The legacy `local-hooks.mjs` utility
is retained for project-file setup/removal, but is not the recommended launch
route when the CLI does not discover that file.

### 4. Test the CLI and inspect the result

Paste this into **Codex's chat**, not directly into PowerShell:

```text
Run git status as one shell tool call. Do not run any other commands.
```

Expected:

- **Demo:** a hook warning/system message with Command Gate's four rows: safe,
  risk `safe`, dangerous `no`, root `no`, with backend probabilities. Codex owns
  the warning presentation; expand hook details if your version collapses them.
- **Real backend:** the same fields with actual backend answers. No verdict or
  percentage is guaranteed. Missing answers show `n/a`.
- **No forced approval:** a read-only command can execute without an approval
  prompt. The hook is advisory; it does not change that policy.
- **Backend log:** in the PowerShell window, `Receive-Job $mockJob -Keep` shows
  the command if you started the demo job there.
- **Optional IDE card:** when the VS Code add-on is running and forwarding is
  enabled, the checked command also appears as a card labelled `Codex`.
  This is optional, not a substitute for the CLI evaluation.

For a warning-path check with **no command execution and no Codex model call**,
exit the CLI and send command text to the local hook directly:

```powershell
'{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git push --force origin main"}}' | node ./cmdgate-codex-plugin/scripts/gate.mjs
```

The result is JSON containing **only `systemMessage`**. The demo should report
`UNSAFE`, risk `high`. This scores text; it **does not run the force-push**.
It validates the hook/backend, not Codex's plugin discovery or terminal rendering.

If nothing appears, open `/hooks`: confirm the source is discovered, enabled,
and trusted. Verify that native hooks are allowed by organization policy and
that the selected endpoint is reachable from the Codex process. An offline
message means the hook ran but the backend did not answer successfully.

### 5. Clean up

Exit Codex. The session-only `--config` hook goes away with the process.
If you used the earlier project-file helper, remove its handler with:

```powershell
node ./cmdgate-codex-plugin/scripts/local-hooks.mjs --remove
```

This removes only this checkout's handler; it preserves unrelated configuration.
To uninstall the marketplace version instead:

```powershell
codex plugin remove cmdgate@dontquit
```

Only if you started the demo job in step 1A:

```powershell
Stop-Job $mockJob
Remove-Job $mockJob
```

Do not stop your real backend. Close the shell or restore earlier `CMDGATE_*`
values when finished. Closing a shell can leave child processes running; verify
port 8010 before starting another demo server.

## Settings

| Environment variable | Default | Purpose |
|---|---|---|
| `CMDGATE_ENDPOINT` | `http://127.0.0.1:8009/v1/systemone` | Full backend endpoint |
| `CMDGATE_MODEL` | `local` | Evaluation backend's model identifier |
| `CMDGATE_TIMEOUT_MS` | `3000` | Backend timeout in milliseconds |
| `CMDGATE_THRESHOLD` | `0.5` | Yes/no probability threshold, inclusive; zero is valid |
| `CMDGATE_FORWARD_TO_IDE` | `true` | Set `false` to disable optional IDE forwarding |

The outer hook timeout is five seconds. Increase it in the hook configuration
if you intentionally increase the backend timeout; IDE forwarding has an
additional one-second budget. Invalid timeout/threshold values use defaults.

## IDE forwarding (optional)

The event protocol matches the [VS Code add-on](../vscode-extension/README.md):
read `~/.cmdgate/windows/*.json`, post to each discovered loopback port's `/event`
with `x-cmdgate-token`, and include the **full backend reply** as `result`.
`source` is `Codex`, `tool` is `Bash`, and the event includes command, description,
and cwd when provided by Codex. A malformed discovery file does not prevent
forwarding to other windows. IDE failures do not change Codex's permissions.

## Tests

From the repo root, without installing the plugin or calling a model:

```powershell
node --test ./cmdgate-codex-plugin/tests/gate.test.mjs
```

Tests cover the wire contract and output formatting, advisory-only JSON,
timeouts and backend failures, invalid/non-shell input, threshold zero, IDE
forwarding, the installed-plugin bootstrap from a path with spaces, and safe,
idempotent local-hook setup/removal. Runtime scripts are standalone; they do not
import the Claude/Cursor plugins or require the IDE add-on.
