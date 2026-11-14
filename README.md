# Think Fast: Local Decision Models

Our entry for the proximata track at **proximata Hackathon '26** (13–14 November 2026, Home of Innovation, Vienna), built on the track's starter kit. The brief and judging rules are in [CASE.md](CASE.md); our plan is in [PRD.md](PRD.md).

## Try it

The command gate reads one shell command an AI coding agent wants to run and decides whether a person should approve it first. It runs on your machine, and after the first download it works with the network off.

```bash
uv sync --extra ml
git clone https://github.com/jaredpalmer/kev.git ../kev && git -C ../kev checkout 2074cc4 && (cd ../kev && uv sync --extra serve)
uv run --extra ml hf auth login                                  # the model repo is private: you need a token with access to it
uv run --extra ml python -m cmdgate.fetch_data --only cli-command-risk
uv run --extra ml python -m cmdgate.fetch_models                 # Kev adapter, n-gram models, Kev's base model (about 1.8 GB)
uv run --extra ml python -m cmdhook.setup                        # writes cmdhook/.env and prints the Claude Code hook entry
```

In `cmdhook/.env` set `CMDHOOK_MODELS=rules,ngram-plus,kev` and `CMDHOOK_KEV_RUN=runs/cmdrisk-08b-cap16-e1/checkpoint`, then:

```bash
uv run --extra ml python -m cmdhook.start                        # the backend, port 8019; it launches Kev too
node cmdhook/hook.js check "git push --force origin main"        # the decision and every model's risk for one command
cd ki-guard && npm start                                         # the dashboard, http://127.0.0.1:8049/
```

- **Claude Code hook:** paste the entry `cmdhook.setup` printed into `.claude/settings.json` ([cmdhook/README.md](cmdhook/README.md)).
- **Plugins:** [Claude Code](cmdgate-claude-plugin/README.md), [Cursor](cmdgate-cursor-plugin/README.md) and [Codex](cmdgate-codex-plugin/README.md) show the backend's decision and every model's result under the permission prompt.
- **Model repo access:** the weights are in a private Hugging Face repo under a personal account, which cannot be shared with other accounts. Until it is made public or moved to an organisation, only its owner can run the fetch.

## Our project: local yes/no tagging with Kev and Adaption

The tool is offline and answers one yes/no question per record. It tags the records it is confident about automatically and sends the rest to a review queue, keeping wrong automatic tags within **1%** per tag.

- **Model:** our own fine-tune of [Kev](https://github.com/jaredpalmer/kev), an open decision model.
- **Training data:** the dataset's real training split, improved and expanded with [Adaption](https://www.adaptionlabs.ai/) into growing synthetic training sets (50k → 100k → 200k rows).
- **Baselines:** classic models (lookup, logistic regression, gradient-boosted trees).
- **Evaluation:** always on real data only.

**Status:** pipeline and tests are in place, but there is **no dataset yet** and therefore no results. A dataset is added by filling in [`dataset.toml`](dataset.toml); see [data/README.md](data/README.md).

### Setup

Needs [uv](https://docs.astral.sh/uv/). The `tagger` pipeline uses pandas and scikit-learn (pinned in `uv.lock`):

```bash
uv sync
uv run python -m unittest discover -s tests -t .
```

Kev is a separate project with its own setup (Python 3.12+, an NVIDIA GPU for training). See its README and [PRD.md](PRD.md#models).

### Workflow

```bash
# 1. dataset in: fill dataset.toml, put files in data/raw/, then
uv run python -m tagger.download --verify
uv run python -m tagger.compare                          # classic baselines on eval

# 2. Adaption seed and the real eval sample Kev is scored on
uv run python -m tagger.seed seed --rows 50000           # upload data/kev/seed_real_50k.csv to Adaption
uv run python -m tagger.seed eval --rows 10000           # stays local
uv run python -m tagger.seed jsonl --input data/adaption/<export>.csv --out data/kev/<name>.jsonl

# 3. Kev: train on a GPU with kev.train, serve locally with kev.serve on port 8009, then
uv run python -m tagger.compare --sample 10000 --endpoint http://127.0.0.1:8009/v1/systemone --label <name>

# 4. freeze the shipped engine and cutoffs (classic by default; --endpoint for Kev)
uv run python -m tagger.freeze

# 5. review tool
uv run python -m tagger.batch replay --batch demo --input data/raw/eval.csv
uv run python -m tagger.batch queue  --batch demo
uv run python -m tagger.batch review --batch demo --id <id> --tag yes --note "why"
uv run python -m tagger.score --batch demo --key data/raw/eval.csv

# 6. final score, once, after freeze
uv run python -m tagger.holdout
```

`uv run python -m tagger.serve` serves a frozen classic model on the decision API (port 8009). Kev serves the same API itself.

| Path | What it is |
|---|---|
| [PRD.md](PRD.md) | Product requirements: data contract, the Adaption → Kev loop, policy, acceptance |
| [dataset.toml](dataset.toml) | The dataset definition, the only file to edit for a new dataset |
| [data/README.md](data/README.md) | How to add a dataset; where files go |
| `tagger/` | The pipeline: `config`, `data`, `download`, `models`, `compare`, `policy`, `engine`, `freeze`, `batch`, `score`, `holdout`, `serve`, `seed` |
| `frozen/policy.json` | The frozen design (created by `tagger.freeze`, tracked) |
| `results/`, `models/`, `runs/` | Generated outputs, model artifacts and Kev checkpoints (gitignored) |

## Dataset and baselines: the command gate

The problem we locked in: should a person approve this shell command before an AI coding agent runs it? `cmdgate/` holds the data pipeline, the question, two baselines and the evaluation for it. The scorecard and the numbers so far are in [REPORT.md](REPORT.md).

| Path | What it is |
|---|---|
| `cmdgate/questions.py` | The yes/no question and the five-level risk scale, in the decision API's format |
| `cmdgate/fetch_data.py` | Downloads eight datasets at pinned revisions into `data/raw/` (not committed), with each one's licence |
| `cmdgate/dataset.py` | Loads CLI Command Risk as one row per distinct command, with its template family |
| `cmdgate/build_suite.py` | Writes the splits as suites for the evaluation runner |
| `cmdgate/rules.py`, `cmdgate/ngram.py` | The two baselines: a regex denylist, and character n-grams into a logistic regression |
| `cmdgate/encoder.py` | A fine-tuned text encoder (ModernBERT-base by default) with a five-level head, temperature-scaled on validation |
| `cmdgate/kev_data.py` | Writes the train, calibration and development partitions a Kev fine-tune reads into `data/kev/cmdrisk/` |
| `cmdgate/scorers.py` | The models by name (`prior`, `rules`, `ngram`, `encoder`, any decision API endpoint such as Kev, and `vote`, the mean of its members). The harness and the engine both load them from here |
| `cmdgate/engine.py` | The model backend: serves the models behind `POST /v1/systemone` (decision API) and `POST /v1/check` (verdict for the KI-Guard hook and dashboard, using each model's cut-offs from the harness) |
| [cmdhook/](cmdhook/README.md) | The backend every frontend calls, and the Claude Code hook. `python -m cmdhook.start` loads the models and thresholds set in `cmdhook/.env` and serves `/v1/hook` (the hook), `/v1/systemone` (the plugins and the VS Code add-on, with every model's own result) and `/v1/check` (the dashboard) on port 8019 |
| `ki-guard/` | The dashboard. A frontend only: it holds no model and makes no decision, it shows what the cmdhook backend answers. See [ki-guard/README.md](ki-guard/README.md) |
| `cmdgate/evaluate.py` | The eval harness: scores a model in process (`--scorer`) or any decision API engine (`--endpoint`) on a split, with ranges from resampling whole template families. Writes `results/<name>_<split>.json` |
| [cmdgate-claude-plugin/](cmdgate-claude-plugin/README.md) | Claude Code plugin: a second opinion from the local app as a line under every shell-command permission prompt, in any terminal or IDE. Install: `/plugin install cmdgate --marketplace Merrixx/hackathon-26-think-fast-DontQuit-` |
| [cmdgate-cursor-plugin/](cmdgate-cursor-plugin/README.md) | Cursor plugin: the same second opinion via a `beforeShellExecution` hook (advisory only; never denies). Marketplace: [`.cursor-plugin/marketplace.json`](.cursor-plugin/marketplace.json) |
| [cmdgate-codex-plugin/](cmdgate-codex-plugin/README.md) | Codex CLI plugin: the same styled evaluation via a native `PreToolUse` advisory, without changing permissions. Local-file setup and marketplace installation: [walkthrough](cmdgate-codex-plugin/README.md#install-and-try-it). Marketplace: [`.agents/plugins/marketplace.json`](.agents/plugins/marketplace.json) |
| [vscode-extension/](vscode-extension/README.md) | Optional VS Code / Cursor add-on: shows the plugin's answers as a styled card. Demo with fake data: [DEMO.md](vscode-extension/DEMO.md) |

Downloading the datasets and training the encoder need extra libraries (Hugging Face Hub, PyArrow, PyTorch, Transformers), kept in the optional `ml` group:

```bash
uv sync --extra ml
uv run --extra ml python -m cmdgate.fetch_data
uv run --extra ml python -m cmdgate.build_suite
uv run --extra ml python -m cmdgate.ngram
uv run --extra ml python -m cmdgate.evaluate --scorer ngram --split validation
uv run --extra ml python -m cmdgate.encoder                   # fine-tunes the encoder into models/encoder/ (about 30 min on Apple Silicon)
uv run --extra ml python -m cmdgate.evaluate --scorer encoder --split validation
uv run --extra ml python -m cmdgate.evaluate --scorer vote --members rules,ngram,encoder --split validation
uv run --extra ml python -m cmdgate.engine --scorer ngram     # then score it with thinkfast.run_eval

# any engine behind the decision API (Kev, the bridge) gets the same metrics:
uv run --extra ml python -m cmdgate.evaluate --endpoint http://127.0.0.1:8011/v1/systemone --name kev --split validation
```

### Models: fetch instead of training

The trained weights are not in git. They sit in one private Hugging Face model repo, pinned by revision in `cmdgate/models.json`; log in once with `uv run --extra ml hf auth login` (a token with access to that repo).

```bash
uv run --extra ml python -m cmdgate.fetch_models          # Kev adapter, both n-gram models, and Kev's base model into the local cache (about 1.8 GB)
uv run --extra ml python -m cmdgate.fetch_models --all    # also the fine-tuned encoders (about 5.8 GB more)
uv run --extra ml python -m cmdgate.policy --check        # the downloaded weights are the ones the cut-offs were measured on
git clone https://github.com/jaredpalmer/kev.git ../kev && git -C ../kev checkout 2074cc4 && (cd ../kev && uv sync --extra serve)
```

Each model's allow and ask cut-offs are frozen in `cmdgate/policy.json`, so nothing has to be measured after a fetch. After retraining a model, run `cmdgate.evaluate` for it and then `uv run --extra ml python -m cmdgate.policy` to freeze the new cut-off; `--publish <account>/<repo>` on `cmdgate.fetch_models` uploads new weights and repins the revision.

Start everything with the hook backend, which loads the models set in `cmdhook/.env`, launches Kev and serves the hook, the plugins and the dashboard on port 8019 (see [Try it](#try-it) and [cmdhook/README.md](cmdhook/README.md)):

```bash
uv run --extra ml python -m cmdhook.start
cd ki-guard && npm start                        # dashboard on http://127.0.0.1:8049/
```

The models by name, for `CMDHOOK_MODELS`: `rules`, `ngram`, `ngram-plus`, `encoder`, `kev`, and the fine-tuned encoders `enc-mbl-cap8`, `enc-qwenc-cap8`, `enc-qwenc-cap8-s1`, `enc-qwenc-cap8-s2`, `enc-qwen-cap8` (2 to 3 GB of memory each). [KI-Guard](ki-guard/README.md) is the dashboard: a frontend only, it shows what the backend answers.

`cmdgate.engine` is the evaluation backend: it serves one or more models behind the decision API on port 8009 for `thinkfast.run_eval` and `cmdgate.evaluate --endpoint`, with the cut-offs frozen in `cmdgate/policy.json`. The hook, the plugins and the dashboard do not use it.

### Fine-tuning Kev on a cloud GPU

Kev trains on an NVIDIA GPU, so the fine-tune runs on [Modal](https://modal.com) with the app Kev ships (`skills/kev-finetune/scripts/kev_modal.py`), and the weights come back to this machine to be served offline. Clone [Kev](https://github.com/jaredpalmer/kev) next to this repo, sign in to Modal once with `uvx modal setup`, then from this repo's root:

```bash
uv run --extra ml python -m cmdgate.kev_data                  # data/kev/cmdrisk/{train,calibration,development}.jsonl
KEV_MODAL=../kev/skills/kev-finetune/scripts/kev_modal.py
uvx modal run $KEV_MODAL::validate --data data/kev/cmdrisk --init-from jaredpalmer/kev-0.8b      # CPU only: do the records fit?
uv run --extra ml python -m cmdgate.kev_variants --cap 16                                        # at most 16 commands per family: data/kev/cmdrisk-cap16/
uvx modal run --detach $KEV_MODAL::train --data data/kev/cmdrisk-cap16 --name cmdrisk-08b-cap16-e1 --init-from jaredpalmer/kev-0.8b \
    --epochs 1 --lr 5e-5 --replay 500 --no-baseline --regression 0 --gpu L40S --timeout 5400
uvx modal run $KEV_MODAL::pull --name cmdrisk-08b-cap16-e1 --checkpoint                          # weights into runs/cmdrisk-08b-cap16-e1/
(cd ../kev && uv run --extra serve python -m kev.serve --run "$OLDPWD/runs/cmdrisk-08b-cap16-e1/checkpoint" --port 8011)
uv run --extra ml python -m cmdgate.evaluate --endpoint http://127.0.0.1:8011/v1/systemone --name kev --split validation
```

`train` uploads the three partitions to a Modal volume, fine-tunes from the released checkpoint, fits the temperature on the calibration partition (train families held out whole) and scores the validation split next to the untouched checkpoint. Run names are immutable: a new attempt needs a new `--name`. `uvx modal run $KEV_MODAL::teardown --everything --yes` removes the volumes afterwards. Keep the launching machine online until the run ends: Modal cancels the training call if the client disconnects, even with `--detach`.

The capped file is the recipe we ship; every run, the hard set (`cmdgate/hardset.py`), the other-source tests (`cmdgate/crosseval.py`), the encoder sweep (`cmdgate/modal_encoder.py`), the Adaption comparison (`cmdgate/adaption_data.py`) and plain model averages (`cmdgate/blend.py`) are written up in [docs/training-log.md](docs/training-log.md), with one line per scored run in `docs/cmdgate-experiments.jsonl`.

To add Kev: serve it on another port, evaluate it with `--endpoint ... --name kev`, then start the backend with `--endpoint kev=http://127.0.0.1:8011/v1/systemone` and re-evaluate the vote with `--member-endpoint kev=...`.

Tune on the validation split only. The test split is scored once, at the end. `cmdgate` is not yet connected to the `tagger` pipeline or `dataset.toml`.

---

## Starter kit

The rest of this README is the track's starter kit documentation. The starter problem, its generators and the runner are unchanged.

You choose the problem. This kit is optional help: a starter problem with a test suite, a scorer and a baseline engine, for teams who'd rather spend the time building.

## What's here

| Path | What it is |
|---|---|
| [CASE.md](CASE.md) | The case brief: challenge, steps, rules, judging |
| [docs/REPORT_TEMPLATE.md](docs/REPORT_TEMPLATE.md) | Copy it to `REPORT.md` in your repo: the scorecard the jury reads during your demo |
| [docs/decision-api.md](docs/decision-api.md) | The one endpoint your engine must serve |
| [docs/further-resources.md](docs/further-resources.md) | Optional extras: a free deep-learning course on calibration, LoRA, quantization and distillation, and what carries over to a decision engine |
| [docs/participant-guide.html](docs/participant-guide.html) | One-page visual guide: the challenge, how you're judged, the starter problem and its baseline numbers. Download it and open it in a browser |
| `thinkfast/suite.py` | Starter problem: generates the dev suite (five data-work task families), or unlimited training and test data with other seeds |
| `thinkfast/run_eval.py` | Runs a suite against your engine: accuracy, Brier score, calibration error, automation rate at a 5% error budget, latency. Works on your own problem too |
| `thinkfast/bridge.py` | Baseline engine on top of any local OpenAI-compatible server that returns `top_logprobs` |
| `thinkfast/messy.py` | Starter problem: generates a messy product catalogue to build your tool on, with an answer key |
| `thinkfast/metrics.py` | The formulas the runner uses |

The starter kit (`thinkfast/`) is plain Python 3.11+ with no dependencies. Its starter-problem data is synthetic: fictional brands and products, no people. Our `tagger/` pipeline adds pandas and scikit-learn.

## Pick a problem

Three ways in. Whichever you take, hold out a test set before you start tuning.

- **Your own problem.** Judgment calls someone makes often: routing, policy checks, deduplication, triage. [docs/REPORT_TEMPLATE.md](docs/REPORT_TEMPLATE.md) has a checklist for whether it's a good one.
- **A ready-made task from [Dataset Factory](https://earino.github.io/dataset-factory-site/).** Public data turned into yes/no prediction tasks, with time-ordered splits and baselines to beat. Check each task's licence, and link the data instead of committing it.
- **Our starter problem.** Five data-work task families and a messy product catalogue, generated by this kit. Quick start below.

## Quick start (starter problem)

```bash
uv run python -m thinkfast.suite          # writes data/dev.jsonl (1,000 requests, 1,400 questions)
uv run python -m thinkfast.messy          # writes data/catalogue_messy.csv and data/catalogue_key.json
```

Start an engine on port 8009 (pick one):

```bash
# A: Kev, an open decision model that already serves this API (fastest on a GPU or Apple Silicon; CPU works, slower)
git clone https://github.com/jaredpalmer/kev.git && cd kev
uv sync --extra serve
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009

# B: the baseline bridge over any local model server that returns top_logprobs
#    (start your model server first, then point the bridge at it)
uv run python -m thinkfast.bridge --upstream http://127.0.0.1:8080 --model <model-name>
```

On a machine with an NVIDIA GPU, [Goethe](https://github.com/meshapplied/goethe) (Apache-2.0, by mesh applied) serves open models behind an OpenAI-compatible API. It uses HTTPS with its own certificate and API keys, so point the bridge at it with `--ca-file <goethe folder>/nginx/certs/server.crt` and set `UPSTREAM_API_KEY`. Whether it returns `top_logprobs` isn't documented either way — try it, and if not, run the model through a server that does (e.g. vLLM, llama.cpp) for the bridge, and keep Goethe for the larger teacher model you distil from.

Then score it:

```bash
uv run python -m thinkfast.run_eval --suite data/dev.jsonl --limit 200   # quick check
uv run python -m thinkfast.run_eval --suite data/dev.jsonl               # full dev suite
```

The runner prints a table per task family and writes `results/report.json` plus every raw answer to `results/answers.jsonl`.

The headline number is the **automation rate**: the share of decisions your engine can take on its own while keeping errors at or below 5%. It rewards accuracy and honest confidence together.

Train on other seeds (`--seed 7`, `--seed 8`, …) and keep one seed you never train or tune on as your test set, for example `uv run python -m thinkfast.suite --seed 777 --out data/test.jsonl`.

## Score your own problem with the runner

If your engine serves the decision API, the runner scores any problem written as JSON lines in the suite's format: one request per line, with its labels.

```json
{"id": "ticket-0001", "family": "support_routing",
 "request": {"state": {"message": "I was charged twice for invoice 4821. Please refund one."},
             "questions": {"team": {"type": "choice", "instructions": "Which team should handle this ticket?",
                                    "criteria": {"billing": "Charges and invoices", "shipping": "Deliveries", "other": "Anything else"}},
                           "refund": {"type": "noul", "instructions": "Does the customer ask for money back?"}}},
 "labels": {"team": "billing", "refund": true}}
```

Labels are the option name for `choice`, `true` or `false` for `noul`, and the level index (0, 1, 2, …) for `score`. The full question format is in [docs/decision-api.md](docs/decision-api.md).

## How you're judged

The track jury scores every team out of 100 during its demo slot: a problem worth solving (20), evidence it works on held-out data (30), local, fast and cheap (20), put to work (20), and what you made yours (10). A few gates come first, such as a filled-in `REPORT.md` scorecard. There's no hidden test suite: you bring the evidence. Details in [CASE.md](CASE.md#judging-criteria).

## Status and known limits

- The tests cover the metrics, the suite generator, the runner and the bridge's wire format against fake servers, plus our `tagger` pipeline (config checks, cutoff policy, freeze → batch → review → score → holdout → API on a throwaway fixture): `uv run python -m unittest discover -s tests -t .`
- The bridge has been run against Qwen3.5-0.8B (Q8_0) on llama.cpp's `llama-server` (build b12314): 200 dev requests, no errors. It needs a server that returns `top_logprobs` for chat completions; it reports an error if yours doesn't. **Turn thinking off for reasoning models** (`llama-server --reasoning off`): otherwise the reply starts with the thinking marker instead of an answer letter and every question becomes a uniform guess.
- Dev-suite labels are generated by rules. The ticket family's urgency labels follow rules the question doesn't state, so even good engines score near chance on urgency. Leave it out of your numbers, or write the rules into the question.
- Kev, SemIf, LocalJev and Goethe are independent open-source projects. Check their READMEs for hardware needs and setup. This kit is not affiliated with them or with any decision-model vendor.

## Licence

MIT, see [LICENSE](LICENSE).
