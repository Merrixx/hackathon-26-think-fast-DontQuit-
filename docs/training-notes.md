# Training notes (CLI Command Risk)

Practical knobs for the next fine-tunes. The shared label contract lives in
[`docs/cmdrisk-train-contract.md`](cmdrisk-train-contract.md).

Team folder on the GPU box: `~/teams/DontQuit/`. Keep JSONL + scripts there;
run Kev from `~/teams/DontQuit/kev/`.

## Sync files to the GPU box

From your laptop (event Wi‑Fi), after SSH works:

```bash
# JSONL + Qwen trainer + early-stop wrapper (adjust host if needed)
scp data/cmdrisk_qwen_train.jsonl data/cmdrisk_kev_train.jsonl \
  cmdgate/train_qwen_lora.py cmdgate/train_kev_early.py \
  hacker@10.10.45.4:~/teams/DontQuit/

# optional: whole cmdgate package for classical scorers later
scp -r cmdgate hacker@10.10.45.4:~/teams/DontQuit/cmdgate
```

On the box, expected layout:

```text
~/teams/DontQuit/
  cmdrisk_kev_train.jsonl
  cmdrisk_qwen_train.jsonl
  train_qwen_lora.py
  train_kev_early.py
  kev/                    # Kev checkout + venv
```

## Progress watcher (local Mac only)

```bash
python3 scripts/watch_kev_train.py          # live bar
python3 scripts/watch_kev_train.py --once
```

## Kev early-stop (`cmdgate/train_kev_early.py`)

`kev.train` has **no** patience API. Our wrapper carves 5% of the **train**
JSONL as holdout, trains in `--chunk-steps` via `--max_steps`, scores with
`kev.benchmark`, stops after `--patience` non-improves, copies best → `--out`.

```bash
# on the GPU box, from ~/teams/DontQuit/kev with that venv active
gpuq submit -t DontQuit -n 1 -- \
  bash -lc 'cd ~/teams/DontQuit/kev && \
    python ../train_kev_early.py \
      --data ../cmdrisk_kev_train.jsonl \
      --out runs/cmdrisk-0.8b-es \
      --init-from jaredpalmer/kev-0.8b \
      --device cuda --chunk-steps 400 --patience 3 --val-frac 0.05'
```

(`-t` = team, `-n` = GPU count. Confirm with `gpuq --help` on the box.)

## Qwen LoRA (`cmdgate/train_qwen_lora.py`)

HF early-stop: `--early-stop` carves `--val-frac` from the **train** JSONL only.

Local / Mac (when MPS is free):

```bash
uv run python cmdgate/train_qwen_lora.py \
  --data data/cmdrisk_qwen_train.jsonl \
  --out runs/cmdrisk-qwen06 \
  --device mps --early-stop --val-frac 0.05 --patience 3 --epochs 1
```

GPU queue (after `scp` above; use a Python env that has `torch` / `transformers` / `peft` — often the Kev venv):

```bash
gpuq submit -t DontQuit -n 1 -- \
  bash -lc 'cd ~/teams/DontQuit/kev && \
    python ../train_qwen_lora.py \
      --data ../cmdrisk_qwen_train.jsonl \
      --out ../runs/cmdrisk-qwen06 \
      --device cuda --early-stop --val-frac 0.05 --patience 3 --epochs 1'
```

Submit Qwen **after** the Kev early-stop job finishes if the box is one-GPU /
40 GB-per-job, so the two do not fight for the same card.

## Classical scorers (local, CPU)

| Scorer | What it is | Fit | Eval |
|---|---|---|---|
| `ngram` | Char TF‑IDF (1–5) + logistic | `uv run --extra ml python -m cmdgate.ngram` | `… -m cmdgate.evaluate --scorer ngram --split validation` |
| `hashreg` | **Hashed** word (1–2) + char_wb (3–5) features + logistic | `uv run --extra ml python -m cmdgate.hashreg` | `… -m cmdgate.evaluate --scorer hashreg --split validation` |
| `rules` | Regex denylist | (no fit) | `… --scorer rules` |

`hashreg` is the fourth voter candidate next to rules / n-gram / Kev / Qwen: same
five-level target, different features (hashing, mixed word+char, no IDF). Models
land in `models/hashreg.joblib` (gitignored). Serve:

```bash
uv run --extra ml python -m cmdgate.engine --scorer hashreg --port 8012
```

## Overnight chain (local; currently paused)

`scripts/chain_after_kev.py` — local MPS chain; cancelled while DGX is in use.
Do not run a second MPS job beside an active Mac train.

## Still out of scope

- Do not train or score the published **test** split until the design is frozen.
- Distillation waits until both Kev and Qwen have validation scores.
