# Adding a dataset

The `tagger` pipeline works on any yes/no dataset described in
[`../dataset.toml`](../dataset.toml). No code changes are needed to swap datasets.
Dataset files are never committed: everything under `data/raw/`,
`data/holdout/`, `data/adaption/` and `data/kev/` is gitignored.

## Folder layout

| Path | What goes there | Who writes it |
|---|---|---|
| `data/raw/train.csv` | Real training split (earliest period) | You, or `tagger.download` |
| `data/raw/eval.csv` | Real eval split (later period) | You, or `tagger.download` |
| `data/holdout/holdout.csv` | Real holdout split (latest period). Add it only after `tagger.freeze` | You |
| `data/adaption/` | Exports from Adaption (Improve / Expand) | You, from adaptionlabs.ai |
| `data/kev/` | Kev training and eval JSONL, the Adaption seed CSV | `tagger.seed` |

Paths can differ; `dataset.toml` `[files]` is what counts.

## Checklist

1. **Files.** Put the train and eval CSVs in `data/raw/`, one row per record with a header line.
2. **Describe the dataset.** Fill in `dataset.toml`:
   - `[dataset]`: name, one-line description, source URL, licence, and the yes/no `question` the model answers.
   - `[files.sha256]` and `[files.rows]`: published checksums and row counts, if the source has them.
   - `[columns]`:
     - `id`: a unique record ID.
     - `target`: the label column.
     - `positive` / `negative`: the label values that mean yes and no.
     - `numeric` / `categorical`: the input columns.
     - `log1p`: skewed amounts or counts.
     - `lookup`: one column for the simple baseline.
     - `stratify`: optional, e.g. a year column.
   - Leave out IDs, dates used for splitting, outcome fields and anything known only after the decision.
3. **Verify:** `uv run python -m tagger.download --verify`.
4. **Baselines:** `uv run python -m tagger.compare`.
5. **Adaption seed:** `uv run python -m tagger.seed seed --rows 50000`, then upload `data/kev/seed_real_50k.csv` to Adaption.
6. **Kev eval sample:** `uv run python -m tagger.seed eval --rows 10000`. Keep it local; it never goes to Adaption.

The full Adaption → Kev loop is in [`../PRD.md`](../PRD.md).
