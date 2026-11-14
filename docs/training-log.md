# Training log: command gate

Every experiment on the command gate's yes/no `risky` decision, in the order it ran. The machine-readable record is [cmdgate-experiments.jsonl](cmdgate-experiments.jsonl): one line per scored run, appended by `cmdgate.lab.log_run`. This page explains what each group of runs tried and what came out. Every number here is copied from that file or from a results file named next to it.

## Rules for every run

- **Tuning signal:** out-of-fold scores on the train split, with whole template families held out (`cmdgate.lab.family_folds`). Logged as split `train-oof`.
- **Validation** is a benchmark, not a tuning set. Every look at it is a line in the log with split `validation`.
- **Test** is not read by any experiment. It is scored once, after the design is frozen.
- **Hard set:** commands we wrote ourselves to be ambiguous or adversarial (see [Hard set](#hard-set)). Our own labels, never trained on, logged as split `hard`.
- **Kev training data** follows [cmdrisk-train-contract.md](cmdrisk-train-contract.md): one decision API request per line, state `{"command": ...}`, the `risky` question with its exact wording, a boolean `label`.
- **Runs locally:** a model counts only if it can be served on the Apple M2 with 16 GB, offline.
- **Contract check:** `uv run --extra ml python -m cmdgate.kev_check data/kev/<folder> ...` verifies every record against the contract and that no validation or test command is in train or calibration. All seven folders used for Kev runs pass (`cmdrisk`, `cmdrisk-cap8`, `-cap16`, `-cap40`, `cmdrisk-adaption-plus`, `-full`, `-balanced`), and the files each run uploaded to Modal are byte-identical to them (SHA-256 of train, calibration and development for all ten runs).

## Outcome

**Best model: `cmdrisk-08b-cap16-e1`**, Kev-0.8B fine-tuned for one pass on at most 16 commands per template family. Served on the Apple M2 (16 GB) through MLX and scored through the harness (`results/kev_validation.json`):

| | Starting vote | Kev cap-16, served locally |
|---|---|---|
| Validation accuracy | 0.918 | 0.940 (family bootstrap 0.886–0.979) |
| Automated at 5% error | 0.898 | 0.973 |
| AUROC | 0.966 | 0.964 |
| Severe recall at 1% false alarms | 0.893 | 0.969 |
| Calibration error | 0.122 | 0.031 |
| Hard set, agreed cases | not scored (n-gram 0.714) | 0.826 |
| Real Claude Code commands flagged | not scored (n-gram 0.355) | 0.079 (cloud scoring) |
| Latency per command | 3.8 ms | 131 ms |

- The local server and the cloud scoring agree: on the hard set the mean difference in P(risky) is 0.003 and one decision of 400 differs.
- **0.99 was not reached.** Every model family stalls near 0.94 on unseen families and 0.82–0.85 on the hard set. The errors are whole concepts absent from training, which no training setting fixed.
- Averaging Kev with the four Qwen classifiers adds half a point of validation accuracy (0.946) at about 4 GB more weights; not worth it for a hook.
- Neither the Adaption export nor a balanced sample of it improved on this model (section 5).
- A Kev-9B teacher reaches 0.947 on the hard set's agreed cases and flags 3.8% of real commands, but is too large to ship, and distilling it into the 0.8B did not carry that over (section 6).
- **Kev-4B does fit the laptop and generalises far better:** 0.919 on the hard set and 3.7% of real commands flagged, at about 0.8 s per command and 9.4 GB. Averaged with the 0.8B it keeps validation accuracy at 0.933 with 0.908 on the hard set (section 7). For a hook where a wrong allow is costly and a second of delay is acceptable, that pair is the better choice; the 0.8B alone is the fast one.
- 0.940 is a selected number: about ten Kev variants were compared on validation. The test split has not been read.

## Where we started

Validation split, 3,541 commands in 128 families, from `results/<name>_validation.json`:

| Model | Accuracy | Automated at 5% error | AUROC | Severe recall at 1% false alarms | Calibration error |
|---|---|---|---|---|---|
| Character n-grams | 0.906 | 0.774 | 0.952 | 0.764 | 0.025 |
| Encoder (ModernBERT-base) | 0.913 | 0.891 | 0.959 | 0.790 | 0.016 |
| Vote (rules, n-grams, encoder) | 0.918 | 0.898 | 0.966 | 0.893 | 0.122 |

## Experiments

### 1. Other sources and a better classic model

Code: `cmdgate/crosseval.py`. Seven other datasets, each mapped to our yes/no decision with a mapping written down in the module. The mappings are ours and none means exactly what our question means, so read these as stress tests, not as accuracy on our decision. `agentapprove` was kept out of all training and selection.

Baselines, at a 0.5 cut-off (log lines with split `x-<source>`):

| Source | Commands | Rules accuracy / AUROC | N-gram accuracy / AUROC | N-gram share flagged |
|---|---|---|---|---|
| shell-safety (synthetic allow / ask / deny) | 2,951 | 0.540 / 0.599 | 0.667 / 0.764 | 0.401 |
| agentapprove (synthetic tool calls, held out) | 1,543 | 0.541 / 0.602 | 0.669 / 0.712 | 0.616 |
| permission-corpus (obfuscation) | 310 | 0.497 / 0.492 | 0.632 / 0.692 | 0.665 |
| shellrisk-bench (attack catalogues; a different meaning of risky) | 4,192 | 0.938 / 0.549 | 0.754 / 0.657 | 0.240 |
| rogue-security | 55 | 0.582 / 0.644 | 0.782 / 0.832 | 0.582 |
| swe-chat (real Claude Code commands, all expected harmless) | 3,018 | – | – | 0.355 |
| infraset (sysadmin agent, presumed harmless, not a false-alarm rate) | 5,000 | – | – | 0.577 |

The n-gram model calls 35.5% of real Claude Code commands risky. Those commands are three to six times longer than the training commands, with heredocs and chains.

Training experiments, all character 1–5 n-grams into a five-level logistic regression, tuned on train out-of-fold:

| Change | Train out-of-fold AUROC | agentapprove AUROC | swe-chat flagged | Verdict |
|---|---|---|---|---|
| None (current n-gram) | 0.926 | 0.712 | 0.355 | – |
| Weight each family once | 0.937 | 0.716 | 0.290 | Helped: cheapest gain, fewer false flags |
| Family weight + one rewrite per command at half weight | 0.936 | 0.722 | 0.321 | Helped a little on every labelled source. Saved as `models/ngram_plus.joblib` |
| Family weight + shell-safety rows | 0.941–0.943 | 0.678–0.688 | 0.503–0.529 | Hurt transfer, though it looks good in-distribution. Not used |
| Family weight + chained commands labelled by their worst part | 0.931 | 0.697 | 0.561 | Hurt: taught "long means risky". Not used |

The saved model (`ngram-plus`) on validation, looked at once: accuracy 0.925, 93.2% automated at 5% error, AUROC 0.970, severe recall 0.925. That is above the starting vote on every column. It still flags 32% of real Claude Code commands, so the gap to real traffic is open.

### 2. Kev fine-tunes

Code: `cmdgate/kev_data.py` (partitions in the contract schema), `cmdgate/kev_variants.py` (family cap), Kev's own Modal app for training. All runs are Kev-0.8B from the released checkpoint, a LoRA adapter, trained on the train split only. Calibration is 105 train families held out whole; development is the validation split. Validation numbers are the project's metrics on calibrated probabilities (log lines `kev-<run>`, split `validation`).

| Run | Train rows | Passes | Accuracy | Automated at 5% error | AUROC | Severe recall | Calibration error | Hard set, agreed | Cost |
|---|---|---|---|---|---|---|---|---|---|
| Released model, no fine-tune | – | – | 0.564 | 0.000 | 0.596 | 0.023 | 0.053 | 0.493 | – |
| `cmdrisk-08b-v1`: every command | 19,817 | 1 | 0.913 | 0.885 | 0.963 | 0.889 | 0.022 | 0.829 | $1.31 |
| `cmdrisk-08b-cap8-e1`: 8 per family | 4,114 | 1 | 0.921 | 0.923 | 0.972 | 0.934 | 0.028 | 0.824 | $0.34 |
| **`cmdrisk-08b-cap16-e1`: 16 per family** | 6,747 | 1 | **0.941** | **0.975** | 0.964 | **0.969** | 0.029 | 0.824 | $0.72 |
| `cmdrisk-08b-cap16-e1-s1`: same, second seed | 6,747 | 1 | 0.938 | 0.965 | 0.965 | 0.962 | 0.036 | 0.846 | $0.46 |
| `cmdrisk-08b-cap16-e3`: 16 per family | 6,747 | 3 | 0.930 | 0.943 | 0.962 | 0.939 | 0.015 | 0.840 | $1.69 |
| `cmdrisk-08b-cap40-e1`: 40 per family | 12,736 | 1 | 0.912 | 0.761 | 0.958 | 0.738 | 0.007 | 0.818 | $1.12 |

Costs are GPU time at the listed L40S rate; Modal's bill also includes CPU and memory.

- **Fine-tuning is what makes Kev usable here:** +35 points over the released model, with no loss on Kev's own public check (0.813 before, 0.815 after).
- **Capping at 16 per family is a real but modest gain.** Paired family bootstrap against the uncapped run: +0.028 accuracy, 95% range +0.004 to +0.059; the second seed gives +0.025. Most of it comes from three families. Caps of 8 and 40 and three passes cannot be told apart from the uncapped run. Why 40 does worse than 16 is unexplained (one seed).
- **No gain on the hard set.** Every fine-tuned run is at 0.82–0.85 on the agreed cases.
- **Where Kev stands apart is real traffic.** On 3,018 real Claude Code commands that should all pass, the capped run flags 7.9% at a 0.5 cut-off (10.7% at its validation cut-off). The n-gram model flags 32–36% and the encoder ensembles 31%. On held-out agentapprove its AUROC is 0.770 (n-gram 0.712), though its probabilities there are badly calibrated (calibration error 0.25). Commands over 650 characters were cut to 650 for this scoring (560 of 4,561).
- **Failed:** the Kev-4B reference run (`cmdrisk-4b-cap16-e2`) was cancelled near the end of training when the local connection dropped; no weights, about $3.75 lost. Not rerun: Kev's notes put 4B at 15.9 GB peak on a 32 GB Mac, so it is not a candidate for a 16 GB machine anyway. A two-pass uncapped run was stopped early on purpose.
- **Selected number:** six variants were compared on validation, so 0.941 is slightly optimistic. `cmdrisk-08b-cap16-e1` was the first capped run launched and its seed replicate agrees, so it is the one to ship. Weights: `runs/cmdrisk-08b-cap16-e1/checkpoint/` (62 MB adapter; needs the Qwen3.5-0.8B base).

### 3. Encoder runs on a cloud GPU

Code: `cmdgate/modal_encoder.py`, `cmdgate/encoder_log.py`. Tuned on three of five family folds of train (13,580 commands), last epoch, temperature fitted on other held-out train families. 31 configurations are in the log as `enc-<name>`, split `train-oof`.

- **Sampling and regularisation made no measurable difference.** On ModernBERT-base, family caps of 3 to 40, family weighting, a lower learning rate, label smoothing, token masking, a yes/no head and token swaps all land at 0.861–0.880 out-of-fold accuracy, inside run-to-run noise.
- **The errors are whole concepts.** Half of the out-of-fold errors sit in about ten held-out families (for example reading a private key, `find -delete`, copying an image onto a disk device). With the concept absent from training there is nothing to learn it from.
- **Prior knowledge in the base model helped.** Qwen2.5-Coder-0.5B and Qwen2.5-0.5B with a classification head reach 0.895–0.907 out-of-fold against about 0.875 for ModernBERT base and large. The 1.5B version only reaches 0.912.
- **Did not train:** DeBERTa-v3 base and large and gte-modernbert (loss was NaN from the first step, not debugged); SmolLM2-360M underfit (0.781).

Validation, eight looks, models fitted on all of train:

| Model | Accuracy | Automated at 5% error | AUROC | Severe recall | Calibration error | Hard set, agreed | swe-chat flagged |
|---|---|---|---|---|---|---|---|
| ModernBERT-base, original recipe rerun | 0.906 | 0.865 | 0.965 | 0.758 | 0.042 | 0.762 | 0.333 |
| ModernBERT-large, 8 per family | 0.922 | 0.910 | 0.961 | 0.773 | 0.049 | 0.768 | 0.280 |
| Qwen2.5-Coder-0.5B, 8 per family, three seeds | 0.916–0.946 | 0.866–0.990 | 0.959–0.976 | 0.537–0.805 | 0.036–0.054 | 0.784–0.798 | 0.337–0.351 |
| Mean of the three seeds | 0.940 | 0.975 | 0.979 | 0.562 | 0.064 | 0.801 | 0.306 |
| Mean of the three seeds and Qwen2.5-0.5B (`enc-ens-qwen4`) | 0.940 | 0.980 | 0.981 | 0.666 | 0.069 | 0.818 | 0.308 |

The ensemble matches Kev on validation accuracy and automation, but its severe recall swings between seeds, it is four 494M-parameter models (about 4 GB), and it flags 31% of real commands where Kev flags 8%. The rerun of the original recipe scores 0.906 where the recorded encoder has 0.913: the recorded run chose its epoch and temperature on validation.

### 4. Plain averages of models

Code: `cmdgate/blend.py`. The mean of the members' saved probabilities, no fitted weights.

| Blend | Validation accuracy | Automated at 5% error | AUROC | Severe recall | Calibration error | Hard set, agreed |
|---|---|---|---|---|---|---|
| Kev cap-16, two seeds | 0.942 | 0.977 | 0.967 | 0.970 | 0.038 | not scored |
| Kev cap-16 + improved n-gram | 0.938 | 0.972 | 0.973 | 0.970 | 0.022 | 0.815 |
| Kev cap-16 + improved n-gram + `enc-ens-qwen4` | 0.942 | 0.984 | 0.981 | 0.973 | 0.045 | 0.826 |
| Kev cap-16 + `enc-ens-qwen4` | 0.946 | 0.994 | 0.981 | 0.973 | 0.058 | 0.826 |

Averaging adds at most half a point of accuracy over Kev alone. Every model family has stalled at about 0.94 on validation and 0.82–0.85 on the hard set, far from the hoped-for 0.99.

### 5. Adaption-expanded training data

Code: `cmdgate/adaption_data.py`. The export (`data/adaption/d812e1c6/`, not committed) is the train split expanded by Adaption: 72,053 rows, 16,107 distinct commands, of which 9,843 are train commands and about 6,200 are new. On the shared commands its yes/no label matches ours on all 9,843. Only the command is used; the explanation that follows it in each prompt names the label. Dropped: 21 commands that are in validation, 27 in test, 254 with both a risky and a not-risky label, about 200 prose rows without a command. Kept: 15,691 commands, 5,831 of them added to train. The added commands are 84% risky (train: 51%).

Same recipe as `cmdrisk-08b-cap16-e1` (Kev-0.8B, one pass, learning rate 5e-5). Log lines `kev-cmdrisk-08b-adaption-*`.

| Train file | Rows | Validation accuracy | Automated at 5% error | AUROC | Severe recall | Hard set, agreed | swe-chat flagged | agentapprove AUROC |
|---|---|---|---|---|---|---|---|---|
| Baseline: 16 per family (two seeds) | 6,747 | 0.941 / 0.938 | 0.975 / 0.965 | 0.964 / 0.965 | 0.969 / 0.962 | 0.824 / 0.846 | 0.079 | 0.770 |
| `plus`: baseline rows + all added commands | 12,578 | 0.925 | 0.942 | 0.978 | 0.902 | 0.810 | 0.262 | 0.816 |
| `full`: the export as the whole train split, no cap | 14,545 | 0.854 | 0.315 | 0.946 | 0.567 | 0.818 | not scored | not scored |
| `balanced`: baseline rows + 945 added not-risky + 945 added risky | 8,637 | 0.934 | 0.961 | 0.973 | 0.950 | 0.818 | 0.110 | 0.791 |

- **The export as the training split is worse** on validation by 8.5 points.
- **Adding its new commands is mixed, and worse where it matters.** It ranks better (AUROC up on validation and on held-out agentapprove) but is 1.5 points less accurate on validation, no better on the hard set, and flags 26% of real Claude Code commands where the baseline flags 8%. The added commands are mostly risky, so the model learns to flag more.
- **Balancing the added commands removes most of the damage but shows no gain.** `balanced` is within seed noise of the baseline on validation accuracy, slightly better at ranking, slightly worse on severe recall, level on the hard set, and flags 11% of real commands against 8%.
- **Verdict: not adopted as the training split.** No variant beats `cmdrisk-08b-cap16-e1` on validation accuracy, the hard set or real-command false alarms. Each variant is one seed.
- The first launch of `plus` and `full` was cancelled by Modal when the local network dropped (about $0.40). Relaunched as server-side spawned calls, names ending `-e1b`.

A second source of new concepts, about 300 command families written by us, was not produced: the agent writing it was stopped by a model safeguard.

### 6. A larger teacher and distillation

Code: `cmdgate/distill.py`. Log lines `kev-cmdrisk-9b-cap16-e1` and `kev-cmdrisk-08b-distill*`.

**Teacher.** Kev-9B, fine-tuned with the cap-16 recipe (one pass, H100). It needs more memory than a 16 GB Mac has, so it cannot ship.

| | Kev-0.8B cap-16 | Kev-9B cap-16 |
|---|---|---|
| Validation accuracy | 0.941 | 0.942 |
| AUROC | 0.964 | 0.975 |
| Severe recall at 1% false alarms | 0.969 | 0.985 |
| Calibration error | 0.029 | 0.016 |
| Hard set, agreed cases | 0.824 (local server 0.826) | **0.947** |
| Hard set, all 400 | 0.800 | 0.913 |
| swe-chat flagged | 0.079 | **0.038** |

A model more than ten times larger scores the same on validation, so that split is capped by its data and labels, not by model size. On the hard set and on real commands, where knowing what a command does matters, the larger model is far better.

**Distillation.** The teacher scored 12,000 real, unlabeled InfraSet commands (none of them read by any test). It was confident (P at or below 0.1, or at or above 0.9) on 8,732: 7,223 not risky, 1,509 risky. Two Kev-0.8B students were trained with the cap-16 recipe on the cap-16 rows plus those commands, with the teacher's yes/no answer as the label (contract schema, checked with `cmdgate.kev_check`).

| Student train file | Rows | Validation accuracy | Automated at 5% error | Severe recall | Hard set, agreed | swe-chat flagged | agentapprove AUROC |
|---|---|---|---|---|---|---|---|
| Baseline: cap-16 only (two seeds) | 6,747 | 0.941 / 0.938 | 0.975 / 0.965 | 0.969 / 0.962 | 0.824 / 0.846 | 0.079 | 0.770 |
| + all 8,732 teacher-labelled commands | 15,479 | 0.927 | 0.927 | 0.926 | 0.852 | 0.181 | 0.804 |
| + 1,509 risky and 3,018 not risky | 11,274 | 0.916 | 0.895 | 0.923 | 0.849 | 0.116 | 0.823 |

- **The teacher's advantage did not transfer.** Both students are at the baseline's seed-to-seed level on the hard set, lower on validation, and flag more real commands, not fewer.
- **Why, as far as the runs show:** the pool is sysadmin work on disposable machines (restarts, edits under /etc, package installs), which is not what a coding agent runs, and the teacher's edge is knowledge held in its 9B base, which 9,000 yes/no labels on a different kind of command do not carry over. A pool of real coding-agent commands was not available: the only one we have is a test set.
- **Verdict: not adopted.** `cmdrisk-08b-cap16-e1` stays the shipped model. Each student is one seed.

### 7. Kev-4B: the largest Kev that runs on the laptop

Log lines `kev-cmdrisk-4b-cap16-e1`, `kev-4b-cap16-local` and `blend-kev08-kev4*`. Kev-4B, fine-tuned with the cap-16 recipe (one pass, H100, contract schema, same training file as the 0.8B and the 9B).

| | Kev-0.8B cap-16 | Kev-4B cap-16 | Mean of the two | Kev-9B cap-16 |
|---|---|---|---|---|
| Validation accuracy | 0.941 | 0.900 | 0.933 | 0.942 |
| Automated at 5% error | 0.975 | 0.856 | 0.968 | 0.987 |
| AUROC | 0.964 | 0.967 | 0.976 | 0.975 |
| Severe recall at 1% false alarms | 0.969 | 0.913 | 0.991 | 0.985 |
| Hard set, agreed cases | 0.824 | **0.919** | 0.908 | 0.947 |
| Hard set, all 400 | 0.800 | 0.898 | 0.885 | 0.913 |
| swe-chat flagged | 0.079 | **0.037** | 0.046 | 0.038 |
| Runs on the Apple M2 with 16 GB | yes, 131 ms per command | yes, about 810 ms per command | yes, both loaded | no |

- **It generalises much better than the 0.8B:** nine points more on the hard set and half the false alarms on real commands, close to the 9B.
- **It scores lower on validation** (0.900 against 0.941; one seed). It ranks as well as the 0.8B there (AUROC 0.967), so the loss is at the 0.5 cut-off on a few large families.
- **It runs on the laptop, verified.** The fine-tuned checkpoint (160 MB adapter on the 9.3 GB base) was served on the Apple M2 with the network off: 78 s to load with the base cached, 9.4 GB of memory, 400 hard-set commands in 325 s. Its answers match the cloud scoring: mean difference in P(risky) 0.003, no decision of 400 differs. Loading folds the adapter into the base and swaps heavily on 16 GB while it does.
- **The plain mean of the 0.8B and the 4B is the best-rounded local option:** within a point of the 0.8B on validation, 0.908 on the hard set, 4.6% of real commands flagged, and the highest severe recall of any local model (0.991). It costs both models' memory (about 11 GB) and time (about 0.9 s per command). The numbers are from saved predictions, nothing fitted.
- The Kev-4B weights are in `runs/cmdrisk-4b-cap16-e1/checkpoint/` on the training machine and on the Modal volume; they are not in the published model repo yet.

## Cloud spend

Hard ceiling for the day: $42. From `modal billing report --for today` after the last run: $36.76 in total (encoder runs $6.88, everything else Kev fine-tuning and scoring). Lost to cancelled runs: about $3.75 (Kev-4B) and $0.40 (first Adaption launch).

A run started with `modal run --detach` is still cancelled if the launching machine loses its connection while the call is open. The Adaption runs were therefore started from a copy of Kev's Modal app in which `train` calls `.spawn()` instead of `.remote()`, so the job no longer depends on the client.

## Hard set

`cmdgate/hard_cases.jsonl` holds 400 commands we wrote to be hard for a string classifier: 191 risky, 209 not, in look-alike pairs across ten trap categories (obfuscation, cloud and database, PowerShell and cmd, a flag that decides, code execution, quiet destruction, secrets, privilege, scary but harmless, path scope). None appears in train or validation.

- **Labels are ours.** A language model wrote them and a second one, shown only the commands and the criteria, labelled them again. The two agreed on 390 of 400 (97.5%). No person has reviewed them.
- **Ambiguous cases** (43): every disagreement, plus cases the author marked as arguable. The remaining 357 are the "agreed" subset.
- **Never trained on, never tuned on.** Score a model with `uv run --extra ml python -m cmdgate.hardset --scorer ngram`, `--endpoint <url> --name <name>` or `--preds <file> --name <name>`.

Accuracy at a 0.5 cut-off, from the `hard` and `hard-agreed` lines in the log:

| Model | All 400 | Agreed 357 | Ambiguous 43 | AUROC, agreed |
|---|---|---|---|---|
| Most common answer | 0.478 | 0.476 | 0.488 | – |
| Rules | 0.608 | 0.633 | 0.395 | 0.628 |
| Character n-grams | 0.698 | 0.714 | 0.558 | 0.802 |

The n-gram model flags anything with a URL, `curl` or the text `rm -rf`, even inside `echo` or a comment (`echo "rm -rf /"` scores 0.997), and misses quiet verbs (`npm publish` 0.075, `mv ~/.ssh /tmp/` 0.119). Its weakest categories are scary but harmless (0.563), secrets (0.600) and path scope (0.621).
