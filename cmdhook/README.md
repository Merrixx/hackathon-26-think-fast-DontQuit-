# cmdhook: the command gate as a Claude Code hook

Everything needed to put the command gate in front of Claude Code's shell commands: a backend that loads the models you pick, and a thin hook that asks it before every command.

```
Claude Code ──PreToolUse──▶ hook.js ──POST /v1/hook──▶ backend (cmdhook.start)
                              ▲                          scores the command with every configured model,
                              └── allow / ask / deny ◀── compares each score to its threshold
```

The hook holds no model and no threshold. The backend keeps the models loaded, so a check costs one local HTTP call.

## Setup

From the repo root:

```bash
uv run --extra ml python -m cmdhook.setup
```

`uv` installs the Python dependencies on that run. The command then

- creates `cmdhook/.env` from [.env.example](.env.example) if there is none,
- checks that Node and the libraries and files of every selected model are there,
- loads each model and scores one command with it,
- lists the other models found on disk, and
- prints the hook entry for Claude Code's settings.

Weights are trained, not downloaded. For a missing model the output names the command that makes it. The exit code is 0 only when every selected model is ready. `--no-load` checks the files without loading the models.

## Start

```bash
uv run --extra ml python -m cmdhook.start
```

The backend listens on `http://127.0.0.1:8019` and prints each model with its threshold. If `kev` is selected, the command launches the Kev server first and stops it again on exit.

## Install the hook in Claude Code

Merge the entry that `cmdhook.setup` prints into `.claude/settings.json` (one project) or `~/.claude/settings.json` (all projects):

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "node \"<path to this folder>/hook.js\"" }] }
    ]
  }
}
```

The hook needs Node and nothing else.

## Who calls the backend

Every frontend in this repo calls this one backend on port 8019; none of them holds a model or decides anything.

| Frontend | Endpoint | What it gets |
|---|---|---|
| `cmdhook/hook.js` (the Claude Code hook) | `POST /v1/hook` | The decision, the reason and every model's risk, threshold and pass or fail |
| The Claude Code, Cursor and Codex plugins, the VS Code add-on | `POST /v1/systemone` | Answers to their questions (approval, risk level, dangerous), plus the same decision and per-model results |
| The KI-Guard dashboard | `POST /v1/check`, `GET /v1/models` | The same decision and thresholds in the fields the dashboard draws; `"model": "<name>"` asks for one model's own decision |

- The approval question is answered with the mean risk over the models; the decision itself follows `CMDHOOK_COMBINE`.
- Risk level and "dangerous" (P(high or critical)) come from the models that have risk levels (`rules`, `ngram`, `ngram-plus`, the encoders). Kev answers yes or no only.
- "Needs root" gets no answer: no model here knows, so the plugins show `n/a`.
- Every hook decision is appended to `CMDHOOK_LOG` (default `~/.ki-guard/log.jsonl`), which the dashboard counts.

## Settings

Settings are `CMDHOOK_*` variables in `cmdhook/.env`. A variable in the environment wins over the file, so one start can override it:

```bash
CMDHOOK_MODELS=ngram,encoder CMDHOOK_THRESHOLD=0.2 uv run --extra ml python -m cmdhook.start
```

Restart the backend after a change.

| Variable | Default | Meaning |
|---|---|---|
| `CMDHOOK_MODELS` | `rules,ngram,encoder` | The models to load, comma-separated (see below) |
| `CMDHOOK_ENDPOINTS` | none | Engines already running behind the decision API, as `name=url`, comma-separated |
| `CMDHOOK_THRESHOLD` | `0.5` | A model passes a command when its risk is at or below this |
| `CMDHOOK_THRESHOLD_<MODEL>` | none | A threshold for one model, such as `CMDHOOK_THRESHOLD_NGRAM` or `CMDHOOK_THRESHOLD_ENC_MBL_CAP8` |
| `CMDHOOK_COMBINE` | `all` | `all`: every model must pass. `any`: one is enough. `mean`: the mean risk against `CMDHOOK_THRESHOLD` |
| `CMDHOOK_ON_PASS` | `allow` | `allow`: the command runs without a permission prompt. `defer`: Claude Code's own rules decide |
| `CMDHOOK_ON_FAIL` | `ask` | `ask`: a permission prompt with the scores. `deny`: blocked, and Claude sees the scores |
| `CMDHOOK_HOST`, `CMDHOOK_PORT` | `127.0.0.1`, `8019` | Where the backend listens and the hook connects |
| `CMDHOOK_ON_ERROR` | `ask` | Backend not reachable or too slow: `ask`, `defer` or `deny` |
| `CMDHOOK_TIMEOUT_MS` | `5000` | How long the hook waits for the backend |
| `CMDHOOK_QUIET` | `0` | `1`: do not show the scores for commands that pass |
| `CMDHOOK_KEV_RUN` | none | The Kev checkpoint to serve, such as `runs/<name>/checkpoint` |
| `CMDHOOK_KEV_DIR`, `CMDHOOK_KEV_PORT` | `../kev`, `8011` | The Kev checkout that serves it, and its port |
| `CMDHOOK_KEV_START_SECONDS` | `300` | How long the start command waits for Kev |

The score is the risk: the model's probability, from 0 to 1, that a person should approve the command first. A lower threshold is stricter. With `CMDHOOK_COMBINE=mean` the per-model thresholds are shown but not used.

These thresholds are yours. They are not the cut-offs the eval harness measures; those still decide `POST /v1/check`, which this backend also serves.

### Models

| Name | What it is | Needs |
|---|---|---|
| `prior`, `rules` | The level shares, and the regex denylist, fitted on the train split at start | The CLI Command Risk dataset (`python -m cmdgate.fetch_data`) |
| `ngram` | Character n-grams | `models/ngram.joblib` (`python -m cmdgate.ngram`) |
| `ngram-plus` | N-grams with wider training data | `models/ngram_plus.joblib` (`python -m cmdgate.crosseval --train-plus`) |
| `encoder` | The fine-tuned encoder | `models/encoder/` (`python -m cmdgate.encoder`) |
| `enc-<name>` | Any other fine-tuned encoder | `models/enc-<name>/` |
| `kev` | A fine-tuned Kev checkpoint | `CMDHOOK_KEV_RUN` and a Kev checkout in `CMDHOOK_KEV_DIR` |

An encoder trained on a prompt-wrapped command is not served.

## What you see

For a command that passes, the scores appear as a message in Claude Code. For one that does not, they are the reason in the permission prompt (`ask`) or the reason Claude gets (`deny`).

Try a command without Claude Code:

```bash
node cmdhook/hook.js check "git push --force origin main"
```

It prints the decision and one line per model, with exit code 0 for allow or defer, 1 for ask and 2 for deny.

## Backend API

| Route | Answer |
|---|---|
| `POST /v1/hook {"command": "..."}` | `decision`, `passed`, `combine`, `mean`, `reason`, `summary`, the denylist `rules` the command trips, and `models`: for each one `p`, `threshold`, `passed`, `ms`, `failed` |
| `GET /v1/config` | The models with their thresholds, and the combine and outcome settings |
| everything `cmdgate.engine` serves | `/v1/check`, `/v1/systemone`, `/v1/models`, `/health`: KI-Guard and the VS Code add-on can point at this backend |

A model that fails to answer, such as an endpoint that is down, does not pass.

## Files

| File | What it is |
|---|---|
| `config.py` | Reads `.env` and the environment into the settings |
| `models.py` | The models by name: what each needs on disk and how it is loaded |
| `server.py` | The decision from the scores, and the HTTP routes |
| `setup.py`, `start.py` | The two commands |
| `hook.js` | The Claude Code hook and the `check` command |

## Test

```bash
uv run --extra ml python -m unittest tests.test_cmdhook
```

The tests cover the settings, the registry's file checks, the decision for each combine mode, and `hook.js` against a backend with stand-in models. They do not measure a model; `python -m cmdgate.evaluate` does.
