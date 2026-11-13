# Command Gate for VS Code

A second opinion on the shell commands an AI agent asks you to approve. When a
permission prompt appears, it shows what your locally running Command Gate app
says about that command:

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
| **Claude Code permission prompts** | Exactly when Claude Code asks you to approve a shell command | A `PermissionRequest` hook forwards the command. The prompt is unchanged; the second opinion pops up next to it |
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
- Node.js on `PATH`, only for the Claude Code hook.

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

Run **Command Gate: Install Claude Code hook** and pick where it goes:

- **This project only:** `.claude/settings.local.json` in an open folder, or in
  any folder you pick with *Another project folder…* (the folder you start
  Claude Code in). The file is personal and not committed.
- **This user:** `~/.claude/settings.json`, for every project.

The first install keeps a backup of the original file (`*.cmdgate-backup`).
Removing the hook deletes only the add-on's own entry.

It copies the hook script to `~/.cmdgate/cmdgate-hook.js` and adds:

```json
{
  "hooks": {
    "PermissionRequest": [
      { "matcher": "Bash|PowerShell",
        "hooks": [{ "type": "command", "command": "node \"<home>/.cmdgate/cmdgate-hook.js\"", "timeout": 5 }] }
    ]
  }
}
```

Restart running Claude Code sessions afterwards. Permission prompts only appear
in Claude Code's default mode: in auto mode or bypass mode there is no prompt,
so there is nothing to give a second opinion on. **Command Gate: Remove Claude Code hook** takes it out again.

How it works:

1. Claude Code is about to show a permission prompt and runs the hook with the
   tool call on stdin.
2. The hook finds the VS Code window whose workspace contains the command's
   working directory. Each window writes its port and a random token to
   `~/.cmdgate/windows/`.
3. The hook sends that window the command and the agent's description.
4. The window asks the app and shows the answer.

The hook prints nothing and always exits 0, so Claude Code's prompt and your
choice there are unaffected. If no window or app answers, nothing happens. The
listener binds to `127.0.0.1` only and refuses requests without the token.

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
answer shows as **n/a**; nothing is guessed.

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
- the hook end to end: it forwards to the right window and prints no decision;
- the listener refusing requests without the token.
