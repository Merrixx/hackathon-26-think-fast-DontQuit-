# KI-Guard

The dashboard in front of the command gate. KI-Guard is a frontend only: it holds no model and makes no decision. Every verdict, every model's risk and every threshold comes from the hook backend in this repo ([cmdhook](../cmdhook/README.md)), and the counts come from the decision log that backend writes.

The Claude Code hook is `cmdhook/hook.js`, set up by `python -m cmdhook.setup`. KI-Guard does not hook into anything.

## Start

In the repo root, start the hook backend (port 8019); it loads the models and thresholds set in `cmdhook/.env`:

```bash
uv run --extra ml python -m cmdhook.start
```

Then, in this folder, start the dashboard (port 8049):

```bash
npm start
```

Open http://127.0.0.1:8049/. `KI_GUARD_BACKEND` points KI-Guard at another backend address, `PORT` moves the dashboard.

`node guard.js check "<command>"` prints the backend's verdict and every model's risk for one command (exit 0 allow, 1 ask, 2 block).

**Backend not running:** the dashboard says so and shows "ask" for a tried command.

## Dashboard + VS Code

- Browser: `npm start` → http://127.0.0.1:8049/
- VS Code: copy `vscode-extension/` to `~/.vscode/extensions/dontquit.ki-guard-0.1.0/`, reload the window. Click the shield in the status bar or run **KI-Guard: Open dashboard**.

What you can see and do:

- Blocked / likely risky / unsure / safe / allowed-by-you counts, per day and per AI.
- **Try a command:** the backend's decision, every backend model's risk with its threshold, root needed, and whether it can be undone. The last two are fixed patterns in `traits.js`, shown as hints; they decide nothing.
- **Decision mode:** Auto (the backend's own decision over all its models) or one model's own decision. This changes what the tester shows, not what the hook does. Saved in `~/.ki-guard/settings.json`.
- **Models in the backend:** which models are served and the threshold each has in `cmdhook/.env`.
- **Eval harness:** every model scored by the repo's Python harness on the same CLI Command Risk rows: accuracy, share automated at 5 % errors, severe commands caught at 1 % false alarms, AUC, Brier and calibration error, with 95 % ranges on hover. It reads `../results/<name>_<split>.json`, written by `python -m cmdgate.evaluate` in the repo root (set `KI_GUARD_RESULTS` to read another folder). Nothing is shown until that command has run.
- **Allowlist:** the list is still stored in `~/.ki-guard/allow.json`, but nothing applies it any more: the backend decides. Allow rules belong in Claude Code's own permission settings.

## Test

`npm test` runs the frontend against a stand-in backend: the backend's verdict and per-model answers are passed through unchanged, and an unreachable backend reads as "ask". It tests the wiring, not a model.
