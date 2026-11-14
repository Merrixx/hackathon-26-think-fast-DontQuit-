# Think Fast: Local Decision Models

Our entry for the proximata track at **proximata Hackathon '26** (13–14 November 2026, Home of Innovation, Vienna), built on the track's starter kit. The brief and judging rules are in [CASE.md](CASE.md); our plan is in [PRD.md](PRD.md).

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
| `cmdgate/engine.py` | Serves any scorer behind `POST /v1/systemone` |
| `cmdgate/evaluate.py` | Scores a scorer on a split, with ranges from resampling whole template families |
| [vscode-extension/](vscode-extension/README.md) | VS Code add-on: a second opinion on the commands an agent asks permission for, answered by the local app. Demo with fake data: [DEMO.md](vscode-extension/DEMO.md) |
| [docs/cursor-cmdgate-hook.md](docs/cursor-cmdgate-hook.md) | Cursor project hook (`.cursor/hooks/`): same decision API / notice line for agent shell commands |

Downloading and reading the datasets needs two extra libraries, kept in the optional `ml` group:

```bash
uv sync --extra ml
uv run --extra ml python -m cmdgate.fetch_data
uv run --extra ml python -m cmdgate.build_suite
uv run --extra ml python -m cmdgate.ngram
uv run --extra ml python -m cmdgate.evaluate --scorer ngram --split validation
uv run --extra ml python -m cmdgate.engine --scorer ngram     # then score it with thinkfast.run_eval
```

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
