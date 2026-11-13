"""Final, one-time score on the holdout with the frozen design.

    uv run python -m tagger.holdout

Run only after `tagger.freeze`. Steps:
1. Check the holdout file against its configured checksum and row count.
2. Split it in memory: inference gets only the allowlisted input columns
   (ID + features); the labels stay with the scorer, keyed by ID.
3. Replay the inputs through the frozen engine into results/batches/holdout/.
4. Join the labels and score, writing score.json and wrong_tags.csv.

Nothing is refit or retuned. A rerun with the same inputs must reproduce the
same predictions_sha256; the command reports whether it did.
"""

from __future__ import annotations

import argparse
import json

from .batch import batch_dir, replay
from .config import load
from .data import check_file, read_raw
from .score import print_score, score_batch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", default="holdout")
    args = parser.parse_args()
    ds = load()

    problems = check_file(ds, "holdout")
    if problems:
        raise SystemExit("holdout check failed: " + "; ".join(problems))
    raw = read_raw(ds.files["holdout"])
    missing = [c for c in ds.input_columns + [ds.target] if c not in raw.columns]
    if missing:
        raise SystemExit(f"holdout lacks columns {missing}")
    inputs, key = raw[ds.input_columns].copy(), raw[[ds.id, ds.target]].copy()
    del raw

    folder = batch_dir(args.batch)
    previous = json.loads((folder / "batch.json").read_text()) if (folder / "batch.json").exists() else None
    meta = replay(ds, inputs, folder, ds.files["holdout"].as_posix())
    if previous:
        same = previous["predictions_sha256"] == meta["predictions_sha256"]
        print(f"rerun: predictions {'reproduced exactly' if same else 'DIFFER from the previous run'}")

    result = score_batch(ds, folder, key, ds.files["holdout"].as_posix())
    if result["rows"] != len(key) or result["rows_in_key"] != len(inputs):
        raise SystemExit("row counts do not reconcile between inputs, predictions and key")
    print_score(result)
    print(f"\nsaved {folder}/ (predictions.csv, batch.json, score.json, wrong_tags.csv)")


if __name__ == "__main__":
    main()
