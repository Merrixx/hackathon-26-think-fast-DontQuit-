"""Reveal historical outcomes for a saved batch and score it.

    uv run python -m tagger.score --batch demo --key data/raw/eval.csv

Runs only after `tagger.batch replay` has saved predictions. Reads just the ID
and label columns of the key file, joins them to the saved predictions, and
writes score.json (machine-readable summary) and wrong_tags.csv (automatic tags
the history contradicts). Predictions and reviewer decisions are not modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from .batch import batch_dir, load_meta, load_predictions, load_reviews
from .compare import calibration_table
from .config import Dataset, load
from .data import parse_labels
from .policy import NO, REVIEW, YES, Policy, policy_report


def score_batch(ds: Dataset, folder: Path, key: pd.DataFrame, key_name: str) -> dict:
    preds, meta = load_predictions(ds, folder), load_meta(folder)
    key = pd.DataFrame({ds.id: key[ds.id].astype(str), "y": parse_labels(ds, key[ds.target])})
    rows = preds.merge(key, on=ds.id, how="left", validate="one_to_one")
    unmatched = int(rows["y"].isna().sum())
    if unmatched:
        sys.exit(f"{unmatched} predictions have no outcome in {key_name}; refusing to score a partial join")
    y, p = rows["y"].astype(int).to_numpy(), rows["p_yes"].to_numpy()
    policy = Policy(**meta["policy"])
    if (policy.tags(p) != rows["proposed_tag"].to_numpy()).any():
        sys.exit("saved tags do not match the frozen policy; predictions were edited")

    reviews = load_reviews(folder)
    human = [(reviews[i]["tag"], int(t)) for i, t in zip(rows[ds.id], y)
             if i in reviews and reviews[i]["tag"] != "pending"]
    wrong = rows[((rows["proposed_tag"] == NO) & (y == 1)) | ((rows["proposed_tag"] == YES) & (y == 0))]
    wrong.to_csv(folder / "wrong_tags.csv", index=False)
    in_review = rows["proposed_tag"] == REVIEW

    result = {
        "batch": folder.name, "dataset": ds.name, "key": key_name, "rows": int(len(rows)),
        "rows_in_key": int(len(key)), "positive_rate": float(y.mean()), "model": meta["model"],
        "policy": meta["policy"], "predictions_sha256": meta["predictions_sha256"],
        "roc_auc": float(roc_auc_score(y, p)) if 0 < y.sum() < len(y) else None,
        "pr_auc": float(average_precision_score(y, p)) if y.sum() else None,
        "brier": float(brier_score_loss(y, p)),
        "calibration": calibration_table(y, p),
        "policy_result": policy_report(p, y, policy),
        "review_queue_positive_rate": float(y[in_review].mean()) if in_review.any() else None,
        "human_reviews": {"decided": len(human), "correct": sum((tag == YES) == bool(t) for tag, t in human)},
    }
    (folder / "score.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def na(value, spec: str) -> str:
    return "N/A" if value is None else format(value, spec)


def print_score(r: dict) -> None:
    pr = r["policy_result"]
    print(f"batch {r['batch']}: {r['rows']:,} rows, positive rate {r['positive_rate']:.4f}")
    print(f"ROC AUC {na(r['roc_auc'], '.4f')}  PR AUC {na(r['pr_auc'], '.4f')}  Brier {r['brier']:.5f}\n")
    print(f"{'action':<8}{'tagged':>10}{'wrong':>8}{'error':>9}{'95% upper':>11}")
    for name in (YES, NO):
        s = pr[name]
        print(f"{name:<8}{s['tagged']:>10,}{s['wrong']:>8,}{na(s['error'], '.4f'):>9}"
              f"{na(s['error_wilson_upper_95'], '.4f'):>11}")
    print(f"{'review':<8}{pr['review']:>10,}   (positives among them: {pr['review_positives']:,})")
    print(f"\ncoverage {na(pr['coverage'], '.4f')}  selective error {na(pr['selective_error'], '.4f')}")
    print(f"Of {r['rows']:,} rows, the model tagged {pr['automatic']:,} automatically, made "
          f"{pr['wrong_automatic']:,} mistakes among those tags, and deferred {pr['review']:,}.")
    print("\ncalibration:      bin    count  mean_p  observed")
    for row in r["calibration"]:
        if row["count"]:
            print(f"  {row['bin']:>14} {row['count']:>8,} {row['mean_p']:>7.4f} {row['observed']:>9.4f}")
    h = r["human_reviews"]
    if h["decided"]:
        print(f"\nhuman reviews: {h['correct']} of {h['decided']} match the historical outcome")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--key", type=Path, required=True, help="CSV with the ID and label columns")
    args = parser.parse_args()
    ds = load()
    key = pd.read_csv(args.key, usecols=[ds.id, ds.target], dtype=str, keep_default_na=False)
    folder = batch_dir(args.batch)
    print_score(score_batch(ds, folder, key, args.key.as_posix()))
    print(f"\nsaved {folder / 'score.json'} and {folder / 'wrong_tags.csv'}")


if __name__ == "__main__":
    main()
