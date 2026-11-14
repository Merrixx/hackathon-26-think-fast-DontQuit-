# Command Gate for VS Code

An optional, richer display for [Command Gate](../cmdgate-claude-plugin/README.md): the
second opinion on the shell commands an AI agent asks you to approve. The
Claude Code plugin already shows a one-line second opinion under every
permission prompt, in any terminal or IDE. With this add-on installed, VS Code
also opens a styled card for each prompt:

| Field | Question ID it reads | Shown as |
|---|---|---|
| Verdict | `unsafe` (yes/no) | SAFE / UNSAFE |
| Risk level | `risk_level` (5-level score) | safe · low · moderate · high · critical, with a bar per level |
| Dangerous | `dangerous` (yes/no) | yes / no with probability |
| Root access | `requires_root` (yes/no) | yes / no with probability |

Each check also shows a one-line summary: the agent's own description of the
command when Claude Code provides one (e.g. "Force-push main"), or a `summary`
field if your app returns one.

The add-on **only advises**. It never approves, denies or blocks a command,
and it runs no model itself: every answer comes from the app.

## Where it checks commands

Commands that already ran in a terminal are not checked: only what an agent
asks permission for, or what you check by hand.

| Source | When | How |
|---|---|---|
| **Claude Code permission prompts** | Exactly when Claude Code asks you to approve a shell command | The [Command Gate Claude Code plugin](../cmdgate-claude-plugin/README.md) asks the app and forwards the command and its answer here. The prompt is unchanged |
| **Cursor Agent shell commands** | When Cursor Agent is about to run a shell command | The [Command Gate Cursor plugin](../cmdgate-cursor-plugin/README.md) asks the app, attaches a message, and forwards here |
| **By hand** | Any time | *Command Gate: Check command…*, or select text → right-click → *Check selection* |

Results open as a **second-opinion card** beside the editor, without taking
focus. It shows the command, a plain verdict, the five-step risk scale, and
whether it is dangerous or needs root; earlier checks are listed below it. The
status bar item reopens the card, and the *Command Gate* output channel logs
every check. VS Code doesn't let extensions style its notification toasts, so
the plain toast is off by default (`cmdgate.notifyOnPrompt`).

## Requirements

- VS Code 1.93 or newer.
- The Command Gate app running locally and serving the decision API
  (`POST /v1/systemone`, see [../docs/decision-api.md](../docs/decision-api.md)). The default endpoint is
  `http://127.0.0.1:8009/v1/systemone`.
- For Claude Code prompts: the [Command Gate Claude Code plugin](../cmdgate-claude-plugin/README.md).
- For Cursor Agent shell commands: the [Command Gate Cursor plugin](../cmdgate-cursor-plugin/README.md).

No npm packages are needed; the add-on uses only Node and VS Code built-ins.

## Install and try it

These are **PowerShell** commands, run from the **repository root**. Choose a
backend in step 1, then an installed or local add-on in step 2. That gives all
four combinations: demo/installed, demo/local, real/installed, and real/local.
The add-on is an editor extension, not an agent CLI: `code` below launches VS
Code. For the actual agent CLI commands, see the
[Claude plugin walkthrough](../cmdgate-claude-plugin/README.md#install-and-try-it)
or [Cursor plugin walkthrough](../cmdgate-cursor-plugin/README.md#install-and-try-it).
The older end-to-end demo is also in [DEMO.md](DEMO.md).

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

Use `https://` in `$endpoint` if your backend requires TLS. TCP success checks
connectivity, not the API contract. The backend must implement
[the decision API](../docs/decision-api.md); its startup command depends on the
backend you run. Do not start the mock for this route. A non-loopback endpoint
receives command text over the network: only use an authorized, trusted service.

### 2A. Install the add-on, then launch the editor

If you already have the `vsce` packaging tool installed:

```powershell
Push-Location ./vscode-extension
vsce package --out cmdgate-vscode.vsix
Pop-Location
# Run these only if packaging succeeded:
code --install-extension ./vscode-extension/cmdgate-vscode.vsix
code --new-window .
```

For Cursor's editor, use `cursor --install-extension` and `cursor --new-window`
instead of `code`. See the
[packaging documentation](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)
if `vsce` is not installed; the local-file route does not need it. Do not commit
the generated `.vsix`. Alternatively, use **Extensions → … → Install from VSIX…**.
Reload an already open editor after installation, then continue with step 3.

### 2B. Launch with local add-on files, without installation

Instead of step 2A, run:

```powershell
$extensionDir = (Resolve-Path ./vscode-extension).Path
code --new-window --disable-extensions --extensionDevelopmentPath $extensionDir .
```

This opens an Extension Development Host with the add-on loaded from the
checkout; other installed extensions are disabled for isolation. No package
installation or build is needed. Continue with step 3 **in the development
window**. Restart that window after source edits.

The checked-in **Run Command Gate add-on** F5 launch is an alternative: it opens
`~/cmdgate-demo` rather than the repo, so create that folder first if using F5.
Do not load an installed and development copy in the same window.

### 3. Configure the selected backend in the editor

Print the exact setting for the endpoint chosen in step 1:

```powershell
@{ 'cmdgate.endpoint' = $endpoint } | ConvertTo-Json
```

In the editor window being tested, open the Command Palette and run
**Preferences: Open Workspace Settings (JSON)**. Merge the printed property
into the existing object without overwriting other settings. For the demo,
it is:

```json
{
  "cmdgate.endpoint": "http://127.0.0.1:8010/v1/systemone"
}
```

For the real route, use the IP and port you selected, not the demo value.
Set `cmdgate.model` too if the backend needs a particular model identifier.
There is no add-on endpoint CLI flag: **the add-on reads editor settings, not
`CMDGATE_ENDPOINT` environment variables**. Avoid committing local backend
addresses in workspace settings; restore the previous value after testing.

### 4. Test it and inspect the result

1. Open the Command Palette and run **Command Gate: Check command…**.
2. Enter `git status`. A second-opinion card should open beside the editor.
   With the demo backend it should be safe, risk `safe`, dangerous `no`, root
   `no`. `Receive-Job $mockJob -Keep` should show the request in the server log.
3. Run **Check command…** again and enter `git push --force origin main`.
   With the demo it should show an unsafe verdict, risk `high`, dangerous `yes`,
   root `no`. Both manual checks **only score text; they do not run commands**.
   Do not paste the force-push into a terminal.
4. With the real backend, the card should reflect the actual API reply instead
   of fixed demo expectations. Missing answers show `n/a`; an unreachable server
   produces an offline/no-second-opinion card, not a fabricated verdict.
5. To test agent forwarding, open a terminal in this editor window and follow
   the installed or local CLI route in the
   [Claude README](../cmdgate-claude-plugin/README.md#install-and-try-it) or
   [Cursor README](../cmdgate-cursor-plugin/README.md#install-and-try-it).
   Configure **that plugin's endpoint separately** to the same backend and
   enable IDE forwarding. Its checked shell command should appear in both the
   agent's permission prompt and this add-on's card. The add-on does not score
   forwarded events a second time.

Use **Command Gate: Show second opinions** to reopen the card, or inspect
**Output → Command Gate** if it is not visible. No IDE card is expected when
only the CLI plugin is loaded and this add-on is not running.

### 5. Clean up

Close the development window if using local files and restore any editor
settings changed for the test. Only if you started the demo job in step 1A:

```powershell
Stop-Job $mockJob
Remove-Job $mockJob
```

Do not stop the real backend as part of this test.

## Claude Code / Cursor permission prompts

Install the [Command Gate Claude Code plugin](../cmdgate-claude-plugin/README.md) and/or
the [Command Gate Cursor plugin](../cmdgate-cursor-plugin/README.md). They do the
work at the prompt: ask the app, show a second-opinion line (Claude) or hook
message (Cursor), and forward the command and the app's answer to this add-on,
which shows the card. The add-on doesn't ask the app a second time.

How the plugin finds VS Code: each VS Code window listens on `127.0.0.1` on a
random port and writes that port and a random token to
`~/.cmdgate/windows/<id>.json`. The plugin posts to every window listed there;
requests without the token are refused. Any other IDE add-on can receive the
same events by doing the same (see the plugin README's protocol section).

Permission prompts only appear in Claude Code's default mode: in auto mode or
bypass mode there is no prompt, so there is nothing to give a second opinion on.

## Settings

| Setting | Default | Purpose |
|---|---|---|
| `cmdgate.endpoint` | `http://127.0.0.1:8009/v1/systemone` | Where the app listens |
| `cmdgate.model` | `local` | Sent as `model`, e.g. to pick one of the app's four models |
| `cmdgate.timeoutMs` | `3000` | Wait this long before showing "no answer" |
| `cmdgate.threshold` | `0.5` | Probability at which a yes/no field shows "yes" |
| `cmdgate.questions` | four questions | Question IDs and wording sent with each command; match them to what the app was trained on |
| `cmdgate.fields` | see above | Which question ID fills which field |
| `cmdgate.showCard` | `true` | Open the second-opinion card beside the editor, without taking focus |
| `cmdgate.notifyOnPrompt` | `off` | Also show VS Code's plain toast for prompts: `always`, `flagged` or `off` |

## What the app must answer

The add-on sends one request per command:

```json
{ "model": "local", "state": { "command": "…" },
  "questions": { "dangerous": {"type": "noul", …}, "requires_root": {"type": "noul", …},
                 "risk_level": {"type": "score", "criteria": [5 levels]}, "unsafe": {"type": "noul", …} } }
```

It reads `answers.<id>.noul` for yes/no questions and
`answers.risk_level.probabilities` (`"0"`–`"4"`) for the level. A missing
answer shows as **n/a**; nothing is guessed. The Claude Code plugin sends the
same request.

**Known gap in the current baseline engine** (`cmdgate/engine.py`): it gives
every yes/no question the same "risky" probability. That makes *dangerous*,
*root access* and the verdict identical; for example, `curl … | sh` shows
"root yes". The app has to answer `requires_root` separately (CLI Command
Risk has a `requires_root` label to train on) for that field to mean anything.

## Tests

Run against the local files, starting at the repo root (no add-on installation needed):

```sh
cd vscode-extension
node --test test/
```

The tests cover:

- the request and answer format;
- the n/a handling;
- the HTTP client against a fake app;
- HTML escaping in the panel;
- the listener taking an event in the plugin's format, answer included;
- the listener refusing requests without the token.
