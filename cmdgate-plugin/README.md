# Command Gate for Claude Code

A second opinion right under Claude Code's permission prompt. When Claude Code
is about to ask you to approve a shell command (Bash or PowerShell), the plugin
asks your locally running Command Gate app about that command and shows the
answer as a line under the dialog:

```
Command Gate: UNSAFE · risk critical 80% · dangerous yes 86% · root no 5%
```

It works wherever Claude Code runs: any terminal, inside any IDE's terminal,
the VS Code extension and the desktop app. Claude Code draws the line itself,
so no IDE add-on is needed.

The plugin **only advises**. It never approves, denies or changes a permission
decision, and it runs no model itself: every answer comes from the app. If the
app is down or slow, the line says so and the prompt opens as usual.

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
  Default endpoint: `http://127.0.0.1:8009/v1/systemone`.
  Without the real backend, the fake one from the repo root works for a demo:
  `node vscode-extension/demo/mock-app.js` (made-up answers).

## Install

From this repository's marketplace, at the prompt of a Claude Code session:

```
/plugin install cmdgate --marketplace Merrixx/hackathon-26-think-fast-DontQuit-
```

Answer `y` to add the marketplace, then pick a scope (user scope: every project).
The repository is private, so this works only for people who can read it on
GitHub and have a git credential for github.com on their machine.

To run it from a checkout for one session only, without installing:

```
claude --plugin-dir cmdgate-plugin --permission-mode default
```

## Settings

Set them in `/config` (each is a row there) or in settings under
`pluginConfigs.cmdgate.options`:

| Setting | Default | Purpose |
|---|---|---|
| `endpoint` | `http://127.0.0.1:8009/v1/systemone` | Where the app listens |
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

```
claude plugin validate cmdgate-plugin
claude plugin test cmdgate-plugin
```

The tests run the hook in Claude Code's own test engine. They cover:

- the line under the dialog for a command about to be asked;
- that commands which run without asking are not checked;
- that the decision is unchanged when the app fails, is slow or answers partly;
- the PowerShell tool;
- forwarding to an IDE window.
