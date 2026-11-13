# REPORT: KI-Guard

## Scorecard

| | |
|---|---|
| Problem, in one line | An AI coding agent is about to run a shell command: allow it, ask a person, or block it? |
| Who makes these calls today, how often, and what it costs them | The developer clicks "allow" on every agent command. On this machine Claude Code ran 263 distinct Bash/PowerShell commands (`model/reproduce.js`, `real_world_claude_sessions`). The cost of one wrong "allow": deleted data, leaked secrets. |
| Why it should run locally | The command text holds paths, hosts and secrets; sending it to a hosted API leaks exactly what the guard protects. The hook also blocks every tool call, so it must answer in under a millisecond. |
| Data: source, licence, size, link | Risky: [RedCode-Exec bash](https://github.com/AI-secure/RedCode) (CC BY 4.0 data, MIT code, commit 664e479), 600 scripts in 20 scenarios. Harmless: [NL2Bash](https://github.com/TellinaTool/nl2bash) `data/bash/all.cm` (MIT, commit d6b9f5b), 12,607 distinct one-liners. Not redistributed; `node get-data.js` downloads both. |
| Test set: how it was held out | By attack scenario: scenario number mod 5 = 0 goes to holdout (scenarios 5, 15, 20, 25 never seen in training), = 4 to eval, else train. NL2Bash by line index, same rule. Holdout n = 2263 (120 risky). |
| Metric, and why it fits the problem | Brier, ECE and AUC (kit `metrics.py` formulas), plus the queue: share auto-decided and the error there. A missed attack costs more than a false alarm, so thresholds are asymmetric. |
| Simple baseline on the test set | Regex rule guard (`guard.js`): AUC 0.516, Brier 0.1003, recall 0.00. Base rate: Brier 0.1005. |
| Your model on the test set | Shipped "Auto" = weighted vote of 3 models: AUC 1.000, Brier 0.0019, ECE 0.0092, recall 1.00, false alarm 0.00 % (at 0.5). Single models below. |
| Bootstrap 95 % range on the gain over the baseline | AUC +0.479 to +0.487, Brier −0.077 to −0.110 (2000 resamples, seed 7). |
| Weakest slice | Unseen scenario 15: recall 0.967 (rules 0.00). Scenarios 5, 20, 25: 1.00 (rules 0.00). |
| Big-model reference on the test set | Not measured (no local LLM installed, no hosted calls by design). |
| Threshold: share decided automatically, and the error rate there | Auto: cut 0.298 chosen on eval → holdout 100 % automated, 0 of 120 attacks allowed, 1 of 2143 harmless blocked. Model C alone (allow < 0.032, block ≥ 0.578): 90.1 % automated, 0 attacks allowed, 2 harmless blocked, 224 to a person. The regex rules still send their hits to a person in every mode. |
| Calibration: before → after | Platt on eval: Brier 0.0546 → 0.0301, ECE 0.0276 → 0.0106 (eval). Shipped. |
| Model: name, size, licence | 3 own logistic regressions on hashed features: A words, B character 3–5-grams, C words + rule verdict; each Platt-calibrated on eval. Vote weights from eval Brier: B 3, C 2, A 1 (best gets most say; the fastest, A, gets least). `ensemble.json` 2.6 MB, MIT. |
| What you trained, tuned or built yourself | All of it: rule guard, features, training, calibration, thresholds, Claude Code hook, decision API. Plain Node, no dependencies. |
| Hardware and latency (p50 / p95 per decision) | AMD Ryzen 7 5800H laptop, Node 26 (`node model/compare.js`). Auto in process 0.021 / 0.055 ms. Full guard 3 ways: library 0.089 / 0.186 ms, local HTTP API 0.345 / 0.653 ms, hook as new process 114 / 129 ms (Node start-up dominates). |
| Cost per million decisions: yours vs a hosted model | Yours: 1 M × 0.345 ms (HTTP p50) ≈ 6 min of one laptop core, ≈ 0 extra cost. Hosted: tokens per call × list price × 1 M; not measured here, and every call would ship the command text out. |
| Runs with Wi-Fi off? | yes (no network code in `guard.js`, `serve.js`, `model/`); video: to record |
| Command that reproduces the test-set numbers | `npm run reproduce` (single model, results.json) and `npm run compare` (3 models + Auto, compare.json) |
| Demo video | to record |
| Known limits | See below. |

## Is it a good problem?

- [x] Volume is high: every agent tool call (263 distinct commands on one dev machine).
- [x] Latency is tight: the agent waits on the hook for every command.
- [x] Threshold, not a verdict: wrong allow = damage, wrong block = annoyance → two cuts and a review queue.
- [x] Labelled data: public risky (RedCode) vs harmless (NL2Bash) sets.
- [x] Inputs are only the command text known before it runs.
- [x] Split by attack scenario, so the holdout attacks are new kinds.
- [x] Baseline measured on the same holdout (regex rules, base rate).

## Results (holdout, `npm run reproduce` → `model/results.json`)

| Engine | AUC | Brier | ECE | Recall | False alarm |
|---|---|---|---|---|---|
| Base rate | 0.500 | 0.1005 | 0.005 | 0.00 | 0.00 % |
| Rule guard (regex) | 0.516 | 0.1003 | 0.005 | 0.00 | 0.00 % |
| KI-Guard (raw) | 0.9996 | 0.0324 | 0.013 | 0.60 | 0.00 % |
| **KI-Guard (shipped)** | **0.9996** | **0.0067** | 0.015 | **0.99** | 0.19 % |

Extra checks:

- **Style shortcut?** 123 harmless holdout lines packed into 2–32-line `#!/bin/bash` scripts (same shape as RedCode): 0 blocked, 0 sent to a person.
- **Real agent commands** (this PC's Claude Code logs, read locally, only counts kept): 263 commands → 0 auto-blocked, 40 to a person, 223 allowed. The rules alone flagged 19.

## Models compared (holdout, `npm run compare` → `model/compare.json`)

| Engine | AUC | Brier | ECE | Recall | False alarm | avg ms |
|---|---|---|---|---|---|---|
| Base rate | 0.500 | 0.1005 | 0.005 | 0.00 | 0.00 % | 0.0001 |
| Regex rules | 0.516 | 0.1003 | 0.005 | 0.00 | 0.00 % | 0.0026 |
| A: words | 0.9991 | 0.0090 | 0.019 | 0.99 | 0.14 % | 0.0062 |
| B: characters | 1.0000 | 0.0001 | 0.000 | 1.00 | 0.00 % | 0.0159 |
| C: words + rules | 0.9996 | 0.0067 | 0.015 | 0.99 | 0.19 % | 0.0062 |
| **Auto (weighted vote)** | **1.0000** | **0.0019** | 0.009 | **1.00** | **0.00 %** | 0.0258 |

Team: all 3 agree on 2258 of 2263 holdout commands (all right). On the 5 splits, B was right 5×, A 1×, C 0×, the weighted vote 5×. Style check (`model/stylecheck-ens.js`): 0 of 123 harmless #!/bin/bash scripts flagged by any model.

## Outputs

`POST /v1/systemone` (127.0.0.1:8009) answers per command: risky (probability), risk level 0/1/2 (low/mid/high, from the two cuts), needs root (rule-based), reversible (rule-based). The Claude Code hook (`guard.js`) uses the stricter of rules and model.

## Recommendation

Ship rules + model as the hook: rules catch the obvious, the model catches the scenario-shaped attacks the rules miss, the middle band goes to a person. A big hosted model is only worth it for the review queue, and only if the user accepts sending command text out.

## Limits and next steps

- Two data sources: part of the score may be source style. A near-perfect score (B: Brier 0.0001) is a warning sign, not a win. Example: `curl … | sh` gets 0.0 % from B (rules still ask). Next: real agent commands with labels.
- Trained on Bash only. PowerShell is never auto-blocked, only sent to a person.
- Auto has no model-only "ask" band (both eval cuts meet at 0.298); the person queue comes from the rules. Model C mode keeps a wide ask band if you want more human review.
- Needs root and reversible are regex traits, not evaluated (no labels exist).
- No big-model reference; Wi-Fi-off video not recorded yet.

## Credits

RedCode, AI-secure, CC BY 4.0. NL2Bash, Lin et al., MIT. Neither is redistributed in this repo.
