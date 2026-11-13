# Product requirements · Local yes/no decision tagging with Kev and Adaption

**Think Fast: Local Decision Models (proximata Hackathon '26) · Code freeze Saturday 14 November 2026, 15:00 Vienna time**

## Product decision

Build an offline tool for an analyst who makes the same yes/no judgment call on
many records. For each record it estimates the probability that the answer is
yes, automatically tags the cases it is confident about, and sends the rest to
a review queue. The headline result is the share of a batch tagged
automatically at a measured error rate, within an error budget of **1% wrong
automatic tags per tag**.

The decision model is our own: **Kev** (open decision model, Apache-2.0),
fine-tuned on our task. Its training data is the dataset's real training split,
**improved and expanded with Adaption (adaptionlabs.ai)** into growing amounts
of synthetic data. Classic models (lookup, logistic regression, gradient-boosted
trees) are the baselines Kev has to beat.

**Dataset:** not chosen yet. The code is dataset-agnostic: a dataset is
added by placing its files in `data/` and filling in `dataset.toml` (see
[data/README.md](data/README.md)). Sections below that depend on the dataset
say what has to be filled in once it is known.

**Starting state.** No dataset, model, benchmark or result exists yet. The
pipeline (`tagger/`) and its tests are in place. Every number in `REPORT.md`
must come from a command in this repo. Development targets are not promised
results.

## User and first milestone

To be filled in once the dataset is chosen: who makes this call today, how
often, how long a case takes, and what a wrong yes or a wrong no costs. Label
any workload or savings figure that is not measured as an assumption, and
validate it with someone who does the work if possible.

First milestone: with the dataset in place, run `tagger.compare` to get the
classic baselines on the eval split, then train the first Kev checkpoint on the
real seed (no synthetic rows yet) and score it on the same eval rows.

## Data contract

| Split | Source | Permitted purpose |
|---|---|---|
| Train | Real records, earliest period | Training, Adaption seed, category mappings, lookup rates |
| Train + synthetic | Train seed after Adaption Improve / Expand | Kev training only |
| Eval | Real records, later period | Model comparison, calibration check, cutoff selection |
| Holdout | Real records, latest period | One final score after the design is frozen |

- **Real data:** use a real, licensed dataset. Pin its release, record checksums
  and row counts in `dataset.toml`, and record source, licence and attribution.
  Keep raw data out of Git (`data/raw/`, `data/holdout/` are gitignored) and
  provide a download command or link.
- **Splits:** split by time if the data has dates, otherwise by a grouping
  (customer, site) that prevents leakage.
- **Synthetic data:** only ever in training. Eval and holdout stay 100% real.
- **Holdout:** stays out of the development workspace until the model and
  cutoffs are frozen. Inference gets an allowlisted projection (ID + features).
  Labels stay with the scorer, keyed by ID. Never refit preprocessing,
  calibration or cutoffs on the holdout.
- **Inputs:** only fields known at decision time. Exclude IDs, split markers,
  outcome fields and labels from features. Handle missing values and unseen
  categories without dropping the record. Account for every split row.

## Data improvement with Adaption

Adaption is used to clean the real training data and expand it into larger
synthetic training sets, in iterations of increasing size.

1. **Seed.** `tagger.seed seed --rows 50000` samples real training rows,
   stratified by label and the configured `stratify` column. It writes a CSV to
   upload to Adaption and the same rows as Kev training JSONL. Each CSV row
   carries `source=real` and its ID.
2. **Improve.** Upload the seed to Adaption and run *Improve* to fix gaps and
   inconsistencies. Keep the label column and every input column, and mark
   changed or generated rows in `source`.
3. **Expand.** Run *Expand* to grow the set: about 100k, then 200k rows, and
   larger if time allows. Export each version as CSV into `data/adaption/`.
4. **Convert.** `tagger.seed jsonl --input data/adaption/<export>.csv --out data/kev/<name>.jsonl`
   turns each export into Kev training JSONL and reports real vs generated
   counts and the positive rate.
5. **Check.** Compare each export's positive rate and value ranges with the
   real seed. Generated labels may not follow the real relationship between
   inputs and outcome, so the eval split decides whether a version helps.
6. **Terms.** Confirm that Adaption's terms allow training on its output.
   Record which rows are generated and how.

## Models

| Candidate | Implementation | Purpose |
|---|---|---|
| Constant rate | Training positive rate for every row | Floor any model must beat |
| Lookup | Training positive rate per value of one configured column | Value of a simple rule |
| Logistic regression | One-hot categories plus scaled numeric inputs | Linear classic baseline |
| Boosted trees | scikit-learn histogram gradient boosting, fixed settings | Strongest classic baseline |
| Kev (real only) | Kev LoRA fine-tune on the real seed at size N | Control for each synthetic iteration |
| Kev (real + synthetic) | Kev LoRA fine-tune on the Adaption export at size N | Measures what synthetic data adds |

**Kev training** (from the [Kev README](https://github.com/jaredpalmer/kev)):
start from a released checkpoint, not the bare base model.

```bash
uv run python -m kev.train --data data/kev/<train>.jsonl --base Qwen/Qwen3.5-0.8B-Base \
    --init_from jaredpalmer/kev-0.8b --epochs 2 --lr 2e-5 --batch 1 --accum 8 \
    --dtype bf16 --checkpointing 1 --device cuda --out runs/<name>
```

- **Hardware:** training needs an NVIDIA GPU with CUDA. Use a rented cloud GPU
  (24 GB or more), or Adaption if it can fine-tune a Kev / Qwen3.5 base and
  lets you download the result. Confirm that at the Adaption workshop before
  relying on it.
- **Checkpoints:** download them into `runs/` or `models/` (gitignored) and
  serve them locally with `kev.serve --run <folder> --port 8009`.
- **Sizes:** start with Kev-0.8B. It runs on a 4 GB GPU or CPU. Larger Kev
  sizes only if time and hardware allow.

**Iterations.** For each data size N (50k, 100k, 200k, …), train one Kev on N
real rows and one on N rows from the Adaption export, then score both on the
same fixed real eval sample with `tagger.compare --sample 10000 --endpoint ... --label <name>`.
Every run is appended to `results/compare_history.jsonl`. Plan for two or three
iterations before code freeze, not open-ended scaling.

**Selection.** The candidate with the best eval result on the agreed metric
ships, Kev or classic. If a classic model beats Kev, report that. Record the
model, its training data, the data release and the cutoffs together in
`frozen/policy.json`.

## Operating policy

The model returns p, the probability of yes.

- **p ≥ upper cutoff:** tag yes automatically.
- **p ≤ lower cutoff:** tag no automatically.
- **Anything in between:** send to review.

Cutoffs are chosen on eval to maximize automatic coverage, with each tag's
wrong-tag share within the **1% budget**. Settings live in `dataset.toml` under `[policy]`.

**Support rule** (fixed before any holdout scoring): a tag is enabled only if
at least `min_support` eval rows get it and the 95% Wilson upper bound of its
eval error is within the budget. If no cutoff qualifies, that tag is disabled
and reported as such. If neither qualifies, everything goes to review. A tag may
be disabled on one side only.

The starter kit's `automation_rate` picks its threshold on the rows it scores,
so it is reported separately and labelled in-sample.

## Integration and workflow

1. **API:** the shipped engine answers `POST /v1/systemone` on port 8009, as in
   [docs/decision-api.md](docs/decision-api.md).
   - Kev: `kev.serve`.
   - Classic models: `tagger.serve`.
   - Smoke test: `uv run python -m thinkfast.run_eval --suite data/kev/eval_real_10k_suite.jsonl --limit 200`.
2. **Batch replay:** `tagger.batch replay` scores a batch from the allowlisted
   input columns only, so labels are never loaded.
   - Exports: ID, probability, proposed tag and disposition.
   - The queue shows inputs, score and the cutoffs that deferred each case, with
     the highest p first.
   - Reviewers can record yes, no or pending. Reviews survive reopening and stay
     separate from predictions and outcomes.
3. **Reveal and score:** `tagger.score` joins outcomes to predictions only
   after they are saved. It writes `score.json` and `wrong_tags.csv`.
4. **Holdout:** `tagger.holdout` is the one documented command for the final
   score.

## Evaluation and acceptance

| Check | Acceptance evidence |
|---|---|
| Model comparison | Same eval rows for every candidate. ROC AUC, PR AUC, Brier score and a calibration table (count, mean predicted, observed rate per bin). Selected candidate named, with its measured gain or loss. |
| Synthetic data | For each size N: Kev real-only vs Kev real + synthetic, on the same real eval sample. Gain or loss reported, including no gain. |
| Frozen policy | Automatic yes and no tags, reviews and wrong tags, each per tag. Coverage = automatic / all. Selective error = wrong automatic / automatic. N/A for zero denominators. |
| Final score | Design frozen before the holdout is opened. `tagger.holdout` scores every holdout row once. Counts reconcile, inference never sees labels, and a rerun reproduces the predictions hash. Any breach of the 1% budget is reported as observed. |
| Failure analysis | Inspect eval mistakes and unfamiliar categories. Report holdout failure patterns without retuning. |
| Workflow | Wi-Fi-off replay shows at least one real record, real model probabilities and a working review disposition. |
| Local performance | Hardware, batch size, timing scope, median and p95 latency, throughput. Cost per million from throughput plus documented power, electricity and hardware assumptions, measured inputs kept apart from estimates. |

## Delivery order

1. **Dataset in.** Choose the dataset, fill `dataset.toml`, verify files with
   `tagger.download --verify`, and run the classic baselines with `tagger.compare`.
2. **Adaption seed.** Generate the seed and real eval sample with
   `tagger.seed`, upload the seed, and run Improve and Expand.
3. **Kev iterations.** Train real-only and real + synthetic checkpoints per
   size, and score each on the same eval sample.
4. **Freeze.** Pick the shipped engine and run `tagger.freeze`, adding
   `--endpoint` for Kev. Pin dependencies, checkpoint, cutoffs and data release.
5. **Holdout.** Run `tagger.holdout` once and generate the scorecard from the
   saved outputs.
6. **Demo and submission.** Put `REPORT.md` at the repo root, starting with
   the scorecard from `docs/REPORT_TEMPLATE.md`. Include setup, data source and
   licence, the Adaption steps, commands, failure analysis and measured
   results. Record a three-minute backup video in live-demo order.

## Judging fit

The jury scores problem value (20), evidence (30), local performance (20),
workflow (20) and original contribution (10). Evidence is the first
tie-breaker.

- **Make it yours:** the Kev fine-tune with Adaption-expanded data, with
  measured gain per iteration.
- **Prize:** the Adaption pipeline is also eligible for Best use of Adaption.
- **Time short?** Put held-out evidence and the working review queue first.

**Pitch, five minutes:**
- **About 45 s:** the user and their workload.
- **2 min:** the offline batch and the review queue, with Wi-Fi off.
- **90 s:** held-out coverage and errors versus the baseline.
- **45 s:** what Kev and the synthetic data added, and the limits.

Keep the strongest claim to one reproducible sentence: "Of N records, our
model tagged A automatically, made E mistakes among those tags, and deferred R."

## Source materials

- [CASE.md](CASE.md) and [README.md](README.md): brief, rubric, runner and submission gates.
- [docs/decision-api.md](docs/decision-api.md): the API contract.
- [Kev](https://github.com/jaredpalmer/kev): models, training and serving.
- [Adaption](https://www.adaptionlabs.ai/): data Improve / Expand. Check its terms and export options.
