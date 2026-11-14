# REPORT: Command gate

A local model that reads one shell command an AI coding agent wants to run and returns a calibrated probability that it is risky. Safe commands run on their own; uncertain and risky ones go to a person.

> Work in progress. Rows marked "not measured" have no number yet. The test split has not been scored by anything: every number below is from the validation split and says so.

## Scorecard

| | |
|---|---|
| Problem, in one line | Should a person approve this shell command before a coding agent runs it? |
| Who makes these calls today, how often, and what it costs them | Developers answering agent permission prompts, and hosted reviewer models. Users accept 93% of permission prompts ([Anthropic](https://www.anthropic.com/engineering/claude-code-auto-mode)); a hosted reviewer approves 99.1% of escalated actions ([OpenAI](https://alignment.openai.com/auto-review/)). Volume per team and cost per decision: not measured |
| Why it should run locally | The command and its context contain source code, paths and credentials, and the check sits inline before every command, so it needs millisecond latency and no per-call fee |
| Data: source, licence, size, link | [CLI Command Risk](https://huggingface.co/datasets/yannelli/cli-command-risk) v1.5 by Ryan Yannelli, CC BY 4.0. 136,497 rows; after keeping one row per distinct command: 21,908 train / 3,541 validation / 2,846 test. Labels were written and audited by language models, not people |
| Test set: how it was held out (by time, by customer, by site…) | By template family: every family of near-identical commands is in exactly one split (the publisher's split). The data has no dates. A second, independent source as a cross-check: not chosen yet |
| Metric, and why it fits the problem | Automation rate at a 5% error budget (how many prompts disappear), and recall on high and critical commands at a 1% false-alarm rate on safe ones (what a guard must not miss) |
| Simple baseline on the test set | Not measured. On validation: rules 77.3% accuracy, 0% automated; character n-grams 90.6% accuracy, 77.4% automated |
| Your model on the test set | Not measured |
| Optional: bootstrap 95% range on the gain over the baseline | Not measured |
| Optional: weakest slice (rarest answers, a segment): baseline vs yours | Baseline on validation: network disruption 6.6% accuracy (61 commands), resource exhaustion 54.3% (138). Ours: not measured |
| Big-model reference on the test set (optional) | Not measured |
| Threshold: share decided automatically, and the error rate there | Not measured |
| Calibration: Brier score or calibration error, before → after | Not measured |
| Model: name, size, licence | Not chosen |
| What you trained, tuned or built yourself | So far: the rules baseline, the n-gram baseline and the evaluation harness |
| Hardware and latency (p50 / p95 per decision) | Apple M2, 16 GB. N-gram baseline through the decision API: p50 0.7 ms, p95 1.0 ms. Model: not measured |
| Cost per million decisions: yours vs a hosted model | Not measured |
| Runs with Wi-Fi off? | Not shown yet |
| Command that reproduces the test-set numbers | Not available yet. Validation: see [Results](#results) |
| Demo video | Not recorded |
| Known limits | Labels judge the command alone, with no knowledge of the repository or what the user asked for; labels are model-written; no real agent traffic in the data |
| Optional: what you'd ship, and when you'd still pick the big model | Not decided |

## Is it a good problem?

- [x] Volume is high: a coding agent proposes a command every few steps. Calls per day for a team: not measured.
- [x] Latency is tight: the agent and the developer both wait on the answer before each command.
- [x] You need a threshold, not a verdict: a wrong "safe" can destroy data, a wrong "risky" costs one prompt, so the cut-off has to be chosen, not fixed by a prompt.
- [x] You have labelled data, or a way to make it: 28,296 distinct labelled commands, model-labelled.
- [x] Inputs are only what is known at decision time: the command string alone. The dataset's explanation, safer alternative and id fields name the label and are never model inputs.
- [ ] The test set is split by time, or by customer or site, if your data allows it: no dates exist; it is split by template family instead.
- [ ] A simple baseline is measured on the same test set: measured on validation only so far.

## The problem

Not written yet.

## Data

Fetch everything with `uv run --extra ml python -m cmdgate.fetch_data`; nothing is committed. Each source is pinned to a revision in `cmdgate/fetch_data.py`.

| Source | Licence | What it is | Used for |
|---|---|---|---|
| [CLI Command Risk](https://huggingface.co/datasets/yannelli/cli-command-risk) v1.5 | CC BY 4.0 | 28,296 distinct commands in 1,258 template families, five risk levels, model-written and model-audited labels | Train, validation, primary test |
| [ShellRisk-Bench](https://huggingface.co/datasets/kontext-security/ShellRisk-Bench) | Mixed; includes GPL-3.0 (GTFOBins) and unlicensed rows | 20,966 commands; "risky" means the command comes from an attack-technique catalogue | Cross-source test only. Not used yet |
| [SWE-chat auto mode](https://huggingface.co/datasets/alex-remedios/swe-chat-auto-mode) | ODC-BY | 250 real Claude Code sessions, every action expected to be allowed | False-alarm test on real traffic. Not used yet |
| [Coding-agent tool-call safety](https://huggingface.co/datasets/agentapprove/coding-agent-tool-call-safety) | CC BY 4.0 | 2,643 tool calls with policy labels | Cross-source test. Not used yet |
| [InfraSet](https://huggingface.co/datasets/infraset/infraset) | Apache-2.0 | 59,349 commands from a real sysadmin agent, no risk labels | Realistic harmless commands. Not used yet |
| [shell-safety v1.1](https://huggingface.co/datasets/tomngdev/shell-safety-v1.1) | MIT | 34,007 commands labelled allow / ask / deny, synthetic | Cross-source test or extra training rows. Not used yet |
| [Permission command corpus](https://huggingface.co/datasets/cowWhySo/permission-command-corpus) | Apache-2.0 | 321 commands with obfuscation labels | Obfuscation test. Not used yet |
| [Coding-agent security benchmark](https://huggingface.co/datasets/rogue-security/coding-agent-security-benchmark) | CC BY-NC 4.0 (non-commercial) | 332 agent messages | Small test only. Not used yet |

`cmdgate/dataset.py` keeps one row per distinct command of CLI Command Risk (the source repeats each command for five shells), drops a command whose rows disagree on the level, and drops validation and test commands that also appear in train. A command counts as risky when its level is moderate, high or critical; safe and low commands may run on their own.

## Method

Not written yet.

## Results

Validation split only, 3,541 commands in 128 template families. Ranges resample whole families.

| | Accuracy | Brier | Calibration error | Automated at 5% errors | Recall on high and critical at 1% false alarms |
|---|---|---|---|---|---|
| Always the training level shares | 0.587 | 0.497 | 0.079 | 0.0% | 0.000 |
| Rules (regex denylist) | 0.773 | 0.368 | 0.105 | 0.0% | 0.000 |
| Character n-grams + logistic regression | 0.906 [0.835, 0.952] | 0.155 | 0.025 | 77.4% [40.2%, 100%] | 0.764 [0.595, 0.954] |
| Hashreg (hashed word+char + logistic) | 0.894 [0.832, 0.943] | 0.171 | 0.026 | 76.0% [28.8%, 97.1%] | 0.707 [0.521, 0.925] |
| Kev-0.8B fine-tune (`runs/cmdrisk-0.8b`) | 0.909 | 0.172 | 0.085 | 0.0% | 0.000 |

Kev matches n-gram accuracy but does **not** automate at a 5% error budget (calibration / confidence ranking is weak on this split). AUROC on P(risky) is 0.941. Full report: `results/kev08_validation/report.json`.

<!-- kev08_calibration -->

### Kev-0.8B post-hoc calibration (validation)

Raw Kev probabilities are almost all 0 or 1, so confidence ranking cannot automate anything at a 5% error budget (**0.0%** automated). Temperature scaling and isotonic regression were fit on this same validation split (not the test set; optimistic vs a held-out calib slice).

| Method | Accuracy | Brier | ECE | Automated at 5% errors | Confidence threshold |
|---|---|---|---|---|---|
| Raw (as served) | 0.909 | 0.172 | 0.085 | 0.0% | 1.0000 |
| Temperature (T=5.064) | 0.909 | 0.144 | 0.023 | 0.0% | 1.0000 |
| Isotonic regression | 0.913 | 0.136 | 0.000 | 53.4% | 0.9514 |

**Winner:** isotonic — **53.4%** automated at 5% errors (threshold confidence ≥ **0.9514**), up from **0.0%** raw. Machine-readable: `results/kev08_calibration.json`.


```bash
uv run --extra ml python -m cmdgate.fetch_data
uv run --extra ml python -m cmdgate.ngram
uv run --extra ml python -m cmdgate.evaluate --scorer prior --split validation
uv run --extra ml python -m cmdgate.evaluate --scorer rules --split validation
uv run --extra ml python -m cmdgate.evaluate --scorer ngram --split validation
uv run --extra ml python -m cmdgate.hashreg
uv run --extra ml python -m cmdgate.evaluate --scorer hashreg --split validation
# after serve: uv run python -m cmdgate.score_api --endpoint http://127.0.0.1:8010/v1/systemone --suite data/cmdrisk_validation.jsonl --out results/kev08_validation
```

## Recommendation

Not written yet.

## Limits and next steps

Not written yet.
