# KI-Guard

One file and no dependencies. It checks every action an AI wants to run **before** it runs.

| Verdict | Meaning | Examples |
|---|---|---|
| 🛑 BLOCK | never runs | delete a whole drive, format a disk, fork bomb, delete backups, turn off antivirus, send keys/.env to the internet, write into `.ssh`/`system32` |
| ⚠️ ASK | you have to confirm | `curl … \| bash`, `rm -rf folder`, `git push --force`, `reset --hard`, `DROP TABLE`, shutdown, `sudo`, `npm publish`, encoded commands, writing `.env` or the AI's own settings, API keys in code |
| ✅ ALLOW | runs normally | everything else |

**Fail-open:** if the guard itself breaks, the action is allowed, so your AI never gets stuck.
Every hit is logged in `~/.ki-guard/log.jsonl`.

## Claude Code
Add this to `~/.claude/settings.json` (all projects) or `.claude/settings.json` (one project):
```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit",
        "hooks": [{ "type": "command", "command": "node C:/path/to/KI-Guard/guard.js" }] }
    ]
  }
}
```

## Any other AI or script
```
node guard.js check "<command>"
```
Exit code `0` means allow, `1` means ask, `2` means block. Use it in front of any agent's tool runner.

## Test
`node test.js` runs 40 cases: dangerous commands must be caught and normal work must pass.
The test cases are stored as base64 so antivirus does not flag the file.

## Any AI, not only Claude

| AI / tool | How to hook it |
|---|---|
| Claude Code | `PreToolUse` hook (snippet above). |
| Any agent with a pre-command hook (JSON on stdin) | `node guard.js --agent <name>`. Accepts `{tool_name, tool_input}` or `{command}`. |
| Any script / wrapper | `node guard.js check "<command>" --agent <name>` → exit 0 allow, 1 ask, 2 block. |
| Anything over HTTP | `npm start`, then `POST 127.0.0.1:8009/api/check {"command": "..."}`. |

Every decision lands in `~/.ki-guard/log.jsonl` with the agent name.

## Dashboard + VS Code

- Browser: `npm start` → http://127.0.0.1:8009/
- VS Code: copy `vscode-extension/` to `~/.vscode/extensions/dontquit.ki-guard-0.1.0/`, reload the window. Click the shield in the status bar or run **KI-Guard: Open dashboard**.

What you can see and do:

- Blocked / likely risky / unsure / safe / allowed-by-you counts, per day and per AI.
- **Try a command:** the verdict, each model's risk, the vote, root needed, and whether it can be undone.
- **Decision mode:** Auto (weighted vote of 3), or one model alone. Saved in `~/.ki-guard/settings.json`.
- **Models compared:** calibration curve against the ideal line, quality bars and downsides.
- **Response time** 3 ways (library, HTTP, hook).
- **Team:** each model's say in the vote and who is right when they disagree.
- **Allowlist:** click "Allow" on any decision, or type a regex. Saved in `~/.ki-guard/allow.json`.
