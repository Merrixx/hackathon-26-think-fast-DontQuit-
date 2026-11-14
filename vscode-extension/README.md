# Command Gate for VS Code

An optional, richer display for [Command Gate](../cmdgate-plugin/README.md): the
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
| **Claude Code permission prompts** | Exactly when Claude Code asks you to approve a shell command | The [Command Gate Claude Code plugin](../cmdgate-plugin/README.md) asks the app and forwards the command and its answer here. The prompt is unchanged |
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
- For Claude Code prompts: the [Command Gate Claude Code plugin](../cmdgate-plugin/README.md).

No npm packages are needed; the add-on uses only Node and VS Code built-ins.

## Try it

For a full walkthrough with a fake backend (no model needed), including a
Claude Code permission prompt, see [DEMO.md](DEMO.md). Short version:

1. Start the app on port 8009. Without the real backend, use the fake one from
   the repo root: `node vscode-extension/demo/mock-app.js` (made-up answers, demo only).
2. Create `~/cmdgate-demo` once, open the repo in VS Code and press **F5**
   (*Run Command Gate add-on*). A second VS Code window starts with the add-on loaded.
3. In that window, run **Command Gate: Check command…** and enter `sudo rm -rf /var/lib/postgresql`.

To use it in your normal VS Code, copy or symlink this folder into
`~/.vscode/extensions/cmdgate-vscode`, or package it with
[`vsce package`](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)
and install the `.vsix`.

## Claude Code permission prompts

Install the [Command Gate Claude Code plugin](../cmdgate-plugin/README.md). It
does the work at the prompt: it asks the app, shows a line under Claude Code's
permission dialog, and forwards the command, the agent's description and the
app's answer to this add-on, which shows the card. The add-on doesn't ask the
app a second time.

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

```bash
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
