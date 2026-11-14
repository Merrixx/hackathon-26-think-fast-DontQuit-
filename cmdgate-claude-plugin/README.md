# Command Gate for Claude Code

A second opinion in Claude Code's CLI when a shell command needs approval.
When Claude Code is about to ask you to approve a command (Bash or PowerShell),
the plugin asks your locally running Command Gate app about that command and
prints the answer as an aligned block before the permission prompt. For the
mock backend's `mkdir` smoke test, it looks like:

```text
Command Gate
  Verdict    🟢 safe
  Risk       🔵 low        69%
  Dangerous  🟢 no          4%
  Root       🟢 no          5%
```

Each field has its own row. Coloured Unicode markers distinguish safe/no
(green), unsafe/yes (red), and increasing risk (green → blue → yellow → orange
→ red). Missing answers use a neutral white marker and `n/a`. This is plain
text with status markers, not ANSI-coloured text; emoji appearance depends on
your terminal/font. Percentages retain the backend's yes probabilities for
Dangerous/Root and its selected-level probability for Risk. Each row is sent
as a separate transcript-log entry because the CLI log API is single-line;
Claude may prefix each row with `cmdgate:`. This avoids stray replacement
characters from embedded line endings.

It also attaches a compact one-line answer as a notice under the permission
dialog. Some CLI permission dialogs do not display that notice, so the transcript
block keeps the evaluation visible independently. Neither output is sent to the
model. Backend errors use a separate warning block with the endpoint and reason.

It works wherever Claude Code runs: any terminal, inside any IDE's terminal,
the VS Code extension and the desktop app. Claude Code draws the output itself,
so no IDE add-on is needed.

The plugin **only advises**. It never approves, denies or changes a permission
decision, and it runs no model itself: every answer comes from the app. If the
app is down or slow, the line says so and the prompt opens as usual.

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

- **Only commands you are about to be asked about.** Commands that Claude Code
  runs without asking (allowed by your rules, or read-only) are not sent to the app.
- **Only in modes that ask a person.** In auto mode or bypass mode there is no
  permission prompt, so there is nothing to give a second opinion on. Start
  Claude Code in default mode (`claude --permission-mode default`) to see it.

## Requirements

- Claude Code with function-hook plugins. This plugin was built and tested on
  Claude Code 2.1.325. The plugin API is early access and can change between releases.
- The Command Gate app (or any engine) serving the decision API locally:
  `POST /v1/systemone`, see [../docs/decision-api.md](../docs/decision-api.md).
  Default endpoint: `http://127.0.0.1:8019/v1/systemone`.
  Without the real backend, the fake one from the repo root works for a demo:
  `node vscode-extension/demo/mock-app.js` (made-up answers).

## Install and try it

These are **PowerShell** commands, run from the **repository root**. Choose a
backend in step 1, then an installed or local plugin in step 3. That gives all
four combinations: demo/installed, demo/local, real/installed, and real/local.
Node.js is needed for the mock server; Claude Code must already be installed
and authenticated. Starting an agent session uses your normal Claude account.

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

Create a temporary settings file, avoiding PowerShell/native JSON quoting
issues. This does not overwrite your normal Claude settings:

```powershell
$sessionSettings = New-TemporaryFile
@{
    pluginConfigs = @{
        cmdgate = @{
            options = @{
                endpoint = $endpoint
                model = 'local'
                timeoutMs = 3000
                threshold = 0.5
                forwardToIde = $true
            }
        }
    }
} | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $sessionSettings.FullName
```

Change `model` if your real backend requires a particular model identifier.
Keep the temporary file until you exit the CLI.

### 3A. Install the plugin, then run the CLI

```powershell
claude plugin marketplace add Merrixx/hackathon-26-think-fast-DontQuit-
claude plugin install cmdgate@dontquit
# Configure the installed plugin's marketplace-qualified identity.
@{ endpoint = $endpoint } | ConvertTo-Json -Compress | claude plugin configure cmdgate@dontquit --values-stdin
claude --permission-mode manual
```

Add the marketplace only once. If `cmdgate@dontquit` is already installed but
disabled, run `claude plugin enable cmdgate@dontquit` before starting the CLI.
You need GitHub repository access if the repository is private. Installation
uses the marketplace's published version, not uncommitted local edits. The
repository version you install must include `cmdgate-claude-plugin/` and its
updated marketplace path.

`manual` is the human-approval mode advertised by the current CLI. Older builds
may name it `default`; check `claude --help` and use that name if needed. Do not
use auto/bypass modes or `--print` for this interactive prompt test.

### 3B. Run the CLI with local plugin files, without installation

Instead of step 3A, run:

```powershell
claude --plugin-dir ./cmdgate-claude-plugin --settings $sessionSettings.FullName --permission-mode manual
```

No plugin installation, dependency installation, build, or project hook file is
needed. Restart the CLI after source edits. If you already installed the
marketplace copy, disable it first to isolate this test:

```powershell
claude plugin disable cmdgate@dontquit
```

Skip that command if it is not installed. Re-enable it after local testing if
needed. This route reads the files currently in your checkout.

### 4. Test the CLI and inspect the result

Paste this into the **agent chat**, not directly into PowerShell:

```text
Run mkdir cmdgate-smoke-test as one shell tool call. Do not run any other commands.
```

Use a fresh folder name if needed. Before the permission prompt, you should
see a transcript block headed `Command Gate`, with four labelled rows and
coloured status markers. A notice beneath the dialog
may also show the same answer, depending on the CLI dialog. You can reject the
command: it need not execute to test the plugin.

- **Demo:** the line should say `safe`, with risk `low`, dangerous `no`, and
  root `no`, each with a probability. These are the mock's keyword-based answers.
  In the PowerShell window, `Receive-Job $mockJob -Keep` should show the command
  logged by the mock server.
- **Real backend:** the same four fields should reflect the actual API reply.
  No particular verdict or percentage is guaranteed. Missing answers show `n/a`.
- **Optional IDE add-on:** with forwarding enabled, the same command should
  also appear as a second-opinion card in the editor.

For a warning-path test, ask the agent to propose `git push --force origin main`
as a shell tool call. **Reject that permission prompt; never approve or run the
force-push for this test.** The demo should label it `UNSAFE`, risk `high`.

If a command is already allowed by your permission rules, there is no human
prompt and this plugin intentionally makes no request. A permission prompt is
required for this test. If you see `no second opinion from <endpoint>`, the
plugin ran but could not obtain an answer: check the selected IP/port, server
logs, firewall, and timeout. The permission decision remains unchanged.

### A permission prompt appears, but there is no Command Gate output

A normal Claude permission prompt alone does **not** prove the plugin loaded.
In another terminal, check the installed plugins:

```powershell
claude plugin list
```

For the installed route, `cmdgate@dontquit` must be installed and enabled. For
the local route, it need not appear in that list, but **every new session** must
be started with `--plugin-dir`; starting plain `claude` does not retain the
previous session's local plugin load. After the folder rename, use
`./cmdgate-claude-plugin`, not the old `./cmdgate-plugin` path.

Exit the test session and launch the local route with a debug log, using the
endpoint/settings variables from steps 1–2 in the original PowerShell window:

```powershell
$debugLog = Join-Path ([System.IO.Path]::GetTempPath()) 'cmdgate-claude.debug.log'
claude --plugin-dir ./cmdgate-claude-plugin --settings $sessionSettings.FullName --permission-mode manual --debug-file $debugLog
```

Repeat the `mkdir` chat prompt, then exit the CLI and inspect relevant lines:

```powershell
Select-String -Path $debugLog -Pattern 'cmdgate|register.ts|tool.check|ui.log|ui.notice'
# Demo only: did the hook reach the backend?
Receive-Job $mockJob -Keep
```

- **No request and no notice:** investigate plugin loading, disabled hooks,
  unsupported function-hook builds, or a hook being skipped. The debug log
  should identify load/registration failures or refused hook API calls.
- **Request logged but no dialog notice:** check for the evaluation in the
  transcript above the prompt. This plugin emits both a transcript block and a
  dialog notice; the latter is not rendered by every permission dialog. If both
  are missing, inspect `ui.log`, `ui.notice`, or hook errors rather than changing
  the backend address, and confirm the session loaded the updated local files.
- **Offline notice:** the plugin loaded and rendered; investigate connectivity
  or the API response instead.

Debug logs may contain private session content. Share only relevant diagnostic
lines after checking them; do not commit the log. The transcript block uses
line breaks and coloured Unicode markers through Claude's text-log API; the
dialog notice remains one line. Both present the same verdict, risk, dangerous,
and root evaluations.

### 5. Clean up

Exit the agent before removing its temporary settings file:

```powershell
Remove-Item $sessionSettings.FullName
# Only if you started the demo job in step 1A:
Stop-Job $mockJob
Remove-Job $mockJob
```

Do not stop the real backend as part of this test. See **Tests** below for the
local automated checks, which do not require installing the plugin.

## Settings

Set them in `/config` (each is a row there) or in settings under
`pluginConfigs.cmdgate.options`:

| Setting | Default | Purpose |
|---|---|---|
| `endpoint` | `http://127.0.0.1:8019/v1/systemone` | Where the app listens |
| `model` | `local` | Sent as `model`, to pick one of the app's models |
| `timeoutMs` | `3000` | How long to wait before saying there is no answer |
| `threshold` | `0.5` | Probability at which a yes/no answer reads as yes |
| `forwardToIde` | `true` | Also send each result to IDE add-ons on this machine |

## What the app must answer

One request per command, the command as `state`, four questions:

```json
{ "model": "local", "state": { "command": "…" },
  "questions": { "dangerous": {"type": "noul", …}, "requires_root": {"type": "noul", …},
                 "risk_level": {"type": "score", "criteria": [5 levels]}, "unsafe": {"type": "noul", …} } }
```

The plugin reads `answers.<id>.noul` for the yes/no questions and
`answers.risk_level.probabilities` (`"0"`–`"4"`, safe to critical) for the
level. A missing answer reads as `n/a`; nothing is guessed. The question
wording is in [hooks/gate.ts](hooks/gate.ts).

## IDE add-ons (optional)

With `forwardToIde` on, each result also goes to IDE add-ons that want a richer
display, such as the [VS Code add-on](../vscode-extension/README.md). The
protocol is small enough for any IDE to implement:

1. The add-on listens on `127.0.0.1` on any port and writes
   `~/.cmdgate/windows/<id>.json` containing `{"port": <port>, "token": "<random>"}`.
   It deletes the file when it closes.
2. For every checked prompt, the plugin sends `POST http://127.0.0.1:<port>/event`
   with the header `x-cmdgate-token: <token>` and this body:
   ```json
   { "source": "Claude Code", "tool": "Bash", "command": "…",
     "description": "the agent's description of the command",
     "result": { "answers": { … } } }
   ```
   `result` is the app's reply as it came, or `null` when the app did not answer.
3. The add-on answers at once (any 2xx) and shows the result. It has one
   second; the prompt never waits on it.

Nothing leaves the machine: the plugin talks only to the configured endpoint
and to `127.0.0.1`.

## Tests

Run from the repo root against the local files (no plugin installation needed):

```sh
claude plugin validate cmdgate-claude-plugin
claude plugin test cmdgate-claude-plugin
```

The tests run the hook in Claude Code's own test engine. They cover:

- transcript output even when the dialog notice is not displayed;
- the notice under the dialog for a command about to be asked;
- that commands which run without asking are not checked;
- that the decision is unchanged when the app fails, is slow or answers partly;
- the PowerShell tool;
- forwarding to an IDE window.
