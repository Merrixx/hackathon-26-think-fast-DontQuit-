# KI-Guard

The hook and dashboard in front of the command gate. KI-Guard holds no model of its own: every shell command an AI wants to run goes to the Python backend in this repo (`cmdgate.engine`), and the verdict comes from the models the eval harness scores (regex rules, character n-grams, the fine-tuned encoder, and Kev once it is served).

| Verdict | Meaning | Where it comes from |
|---|---|---|
| ✅ ALLOW | runs normally | The chosen model puts the risk at or below its allow cut-off |
| ⚠️ ASK | you have to confirm | The risk is above the allow cut-off, the model has not been evaluated, or the backend is not running |
| 🛑 BLOCK | never runs | Only for write tools: a file in `.ssh`, `system32` or a system folder |

A model may allow a command on its own only inside the cut-off the eval harness measured for it on the validation split (`results/<model>_validation.json`). A model with no such result asks every time.

KI-Guard still does three things itself: file-path and secret checks for write tools (`Write`, `Edit`, …), your allowlist, and the decision log in `~/.ki-guard/log.jsonl`.

**Backend not running:** commands are not waved through; you are asked. Malformed hook input is allowed, so a broken hook call never bricks your agent.

## Start

In the repo root, start the model backend (port 8009):

```bash
uv run --extra ml python -m cmdgate.engine
```

It serves every model found on disk. Add Kev, or any engine that speaks the decision API, with `--endpoint kev=http://127.0.0.1:8011/v1/systemone`. Then, in this folder, start the dashboard (port 8049):

```bash
npm start
```

Open http://127.0.0.1:8049/. `KI_GUARD_BACKEND` points KI-Guard at another backend address, `PORT` moves the dashboard.

## Claude Code

Add this to `~/.claude/settings.json` (all projects) or `.claude/settings.json` (one project):

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "node <path to this folder>/guard.js" }] }
    ]
  }
}
```

## Any other AI or script

| AI / tool | How to hook it |
|---|---|
| Any agent with a pre-command hook (JSON on stdin) | `node guard.js --agent <name>`. Accepts `{tool_name, tool_input}` or `{command}`. |
| Any script / wrapper | `node guard.js check "<command>" --agent <name>` → exit 0 allow, 1 ask, 2 block. |
| Anything over HTTP | `POST 127.0.0.1:8009/v1/check {"command": "..."}` on the backend. |

Every decision lands in `~/.ki-guard/log.jsonl` with the agent name and the model that decided.

## Dashboard + VS Code

- Browser: `npm start` → http://127.0.0.1:8049/
- VS Code: copy `vscode-extension/` to `~/.vscode/extensions/dontquit.ki-guard-0.1.0/`, reload the window. Click the shield in the status bar or run **KI-Guard: Open dashboard**.

What you can see and do:

- Blocked / likely risky / unsure / safe / allowed-by-you counts, per day and per AI.
- **Try a command:** the verdict, every backend model's risk with its cut-offs, which model decided, root needed, and whether it can be undone. The last two are fixed patterns in `traits.js`, not models.
- **Decision mode:** Auto (the backend's default, the vote when more than one model is served) or one model alone. Saved in `~/.ki-guard/settings.json`.
- **Models in the backend:** which models are served, and whether each has cut-offs from the eval harness.
- **Eval harness:** every model scored by the repo's Python harness on the same CLI Command Risk rows: accuracy, share automated at 5 % errors, severe commands caught at 1 % false alarms, AUC, Brier and calibration error, with 95 % ranges on hover. It reads `../results/<name>_<split>.json`, written by `python -m cmdgate.evaluate` in the repo root (set `KI_GUARD_RESULTS` to read another folder). Nothing is shown until that command has run.
- **Allowlist:** click "Allow" on any decision, or type a regex. Saved in `~/.ki-guard/allow.json`.

## Test

`npm test` runs the hook against a stand-in backend: the backend's verdict is passed through, an unreachable backend asks, and the file-path checks still block. It tests the wiring, not a model. Model quality is measured by `python -m cmdgate.evaluate` in the repo root.
