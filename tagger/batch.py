"""Batch replay and review queue.

    uv run python -m tagger.batch replay  --batch demo --input data/raw/eval.csv
    uv run python -m tagger.batch summary --batch demo
    uv run python -m tagger.batch queue   --batch demo --limit 10 [--where COLUMN=VALUE]
    uv run python -m tagger.batch show    --batch demo --id <record id>
    uv run python -m tagger.batch review  --batch demo --id <record id> --tag yes --note "why"
    uv run python -m tagger.batch export  --batch demo

Replay reads only the allowlisted input columns (ID + features from
dataset.toml); a label column in the file is never loaded, so the batch opens
with the answers hidden. Predictions, reviewer decisions and, later via
tagger.score, historical outcomes live in separate files under
results/batches/<batch>/. Reopening a batch keeps every reviewer decision.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .config import Dataset, load
from .engine import load_frozen, predictions_sha256
from .policy import NO, REVIEW, YES

BATCH_ROOT = Path("results/batches")
REVIEW_TAGS = (YES, NO, "pending")


def batch_dir(name: str) -> Path:
    return BATCH_ROOT / name


def read_inputs(ds: Dataset, path: Path) -> pd.DataFrame:
    """Read only the allowlisted input columns, so labels never enter the batch."""
    header = pd.read_csv(path, nrows=0).columns
    missing = [c for c in ds.input_columns if c not in header]
    if missing:
        sys.exit(f"{path} lacks input columns {missing}")
    return pd.read_csv(path, usecols=ds.input_columns, dtype=str, keep_default_na=False)[ds.input_columns]


def load_reviews(folder: Path) -> dict:
    path = folder / "reviews.json"
    return json.loads(path.read_text()) if path.exists() else {}


def load_meta(folder: Path) -> dict:
    path = folder / "batch.json"
    if not path.exists():
        sys.exit(f"no batch in {folder}; run `tagger.batch replay` first")
    return json.loads(path.read_text())


def load_predictions(ds: Dataset, folder: Path) -> pd.DataFrame:
    load_meta(folder)
    return pd.read_csv(folder / "predictions.csv", dtype={ds.id: str})


def replay(ds: Dataset, raw: pd.DataFrame, folder: Path, source: str) -> dict:
    """Score raw input rows with the frozen engine and save the batch. Returns batch metadata."""
    engine, policy, frozen = load_frozen(ds)
    started = time.perf_counter()
    p = engine.predict_frame(raw)
    seconds = time.perf_counter() - started
    folder.mkdir(parents=True, exist_ok=True)
    # Full float precision, so tags recomputed from the saved file match exactly at the cutoffs.
    pd.DataFrame({ds.id: raw[ds.id], "p_yes": p, "proposed_tag": policy.tags(p)}).to_csv(
        folder / "predictions.csv", index=False)
    raw.to_csv(folder / "inputs.csv", index=False)
    meta = {"input": source, "rows": int(len(raw)), "dataset": ds.name, "question": ds.question,
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": frozen["model"], "engine": frozen["engine"], "policy": policy.to_dict(),
            "predictions_sha256": predictions_sha256(raw[ds.id], p), "inference_seconds": round(seconds, 3)}
    (folder / "batch.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def cmd_replay(ds: Dataset, args) -> None:
    folder = batch_dir(args.batch)
    if (folder / "predictions.csv").exists() and not args.force:
        sys.exit(f"{folder} already has predictions; reopen it with summary/queue, or pass --force")
    meta = replay(ds, read_inputs(ds, args.input), folder, Path(args.input).as_posix())
    print(f"scored {meta['rows']:,} rows in {meta['inference_seconds']:.2f} s; saved {folder}")
    print_summary(ds, folder)


def cutoff_text(op: str, cut) -> str:
    return "(tag disabled)" if cut is None else f"p {op} {cut:.4f}"


def print_summary(ds: Dataset, folder: Path) -> None:
    preds, meta, reviews = load_predictions(ds, folder), load_meta(folder), load_reviews(folder)
    counts = preds["proposed_tag"].value_counts()
    decided = sum(1 for r in reviews.values() if r["tag"] != "pending")
    pol = meta["policy"]
    print(f"\nbatch {folder.name}: {meta['rows']:,} rows from {meta['input']}")
    print(f"  question: {meta['question']}")
    print(f"  model {meta['model']}, error budget {pol['budget']:.1%} per automatic tag")
    print(f"  yes     automatic  {cutoff_text('>=', pol['upper']):<16}{counts.get(YES, 0):>9,}")
    print(f"  no      automatic  {cutoff_text('<=', pol['lower']):<16}{counts.get(NO, 0):>9,}")
    print(f"  review  person     {'':<16}{counts.get(REVIEW, 0):>9,}   reviewed so far: {decided:,}")


def describe(ds: Dataset, row: pd.Series, pol: dict) -> str:
    reason = [f"p > {pol['lower']:.4f} (no cutoff)" if pol["lower"] is not None else "no tag disabled",
              f"p < {pol['upper']:.4f} (yes cutoff)" if pol["upper"] is not None else "yes tag disabled"]
    fields = "  ".join(f"{c}={row[c] or '-'}" for c in ds.features)
    return f"{row[ds.id]:<16} p_yes={row['p_yes']:.4f}  deferred: {', '.join(reason)}\n    {fields}"


def joined(ds: Dataset, folder: Path) -> pd.DataFrame:
    inputs = pd.read_csv(folder / "inputs.csv", dtype=str, keep_default_na=False)
    return load_predictions(ds, folder).merge(inputs, on=ds.id)


def cmd_queue(ds: Dataset, args) -> None:
    folder = batch_dir(args.batch)
    rows, reviews, pol = joined(ds, folder), load_reviews(folder), load_meta(folder)["policy"]
    queue = rows[rows["proposed_tag"] == REVIEW]
    queue = queue[[reviews.get(i, {}).get("tag", "pending") == "pending" for i in queue[ds.id]]]
    for condition in args.where or []:
        column, _, value = condition.partition("=")
        if column not in queue.columns:
            sys.exit(f"--where: unknown column {column!r}")
        queue = queue[queue[column] == value]
    queue = queue.sort_values("p_yes", ascending=False)
    print(f"{len(queue):,} rows waiting for review (highest p_yes first)\n")
    for _, row in queue.head(args.limit).iterrows():
        print(describe(ds, row, pol))


def cmd_show(ds: Dataset, args) -> None:
    folder = batch_dir(args.batch)
    row = joined(ds, folder).query(f"`{ds.id}` == @args.id")
    if row.empty:
        sys.exit(f"{args.id} is not in batch {args.batch}")
    row, pol = row.iloc[0], load_meta(folder)["policy"]
    print(describe(ds, row, pol) if row["proposed_tag"] == REVIEW else
          f"{row[ds.id]} p_yes={row['p_yes']:.4f} automatic tag: {row['proposed_tag']}")
    review = load_reviews(folder).get(args.id)
    print(f"    reviewer: {review}" if review else "    reviewer: none yet")


def cmd_review(ds: Dataset, args) -> None:
    folder = batch_dir(args.batch)
    if args.id not in set(load_predictions(ds, folder)[ds.id]):
        sys.exit(f"{args.id} is not in batch {args.batch}")
    reviews = load_reviews(folder)
    reviews[args.id] = {"tag": args.tag, "note": args.note,
                        "at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (folder / "reviews.json").write_text(json.dumps(reviews, indent=2) + "\n")
    print(f"recorded {args.id}: {args.tag}")


def cmd_export(ds: Dataset, args) -> None:
    folder = batch_dir(args.batch)
    preds, reviews = load_predictions(ds, folder), load_reviews(folder)

    def disposition(row) -> str:
        if row["proposed_tag"] != REVIEW:
            return f"auto:{row['proposed_tag']}"
        tag = reviews.get(row[ds.id], {}).get("tag", "pending")
        return "pending" if tag == "pending" else f"human:{tag}"

    out = preds.assign(disposition=preds.apply(disposition, axis=1))
    out.to_csv(folder / "decisions.csv", index=False)
    print(out["disposition"].value_counts().to_string())
    print(f"saved {folder / 'decisions.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("replay")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--force", action="store_true", help="overwrite predictions (reviews.json is kept)")
    sub.add_parser("summary")
    p = sub.add_parser("queue")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--where", action="append", help="COLUMN=VALUE filter on an input column; repeatable")
    sub.add_parser("show").add_argument("--id", required=True)
    p = sub.add_parser("review")
    p.add_argument("--id", required=True)
    p.add_argument("--tag", choices=REVIEW_TAGS, required=True)
    p.add_argument("--note", default="")
    sub.add_parser("export")
    for sp in sub.choices.values():
        sp.add_argument("--batch", required=True)
    args = parser.parse_args()
    ds = load()
    commands = {"replay": cmd_replay, "summary": lambda d, a: print_summary(d, batch_dir(a.batch)),
                "queue": cmd_queue, "show": cmd_show, "review": cmd_review, "export": cmd_export}
    commands[args.command](ds, args)


if __name__ == "__main__":
    main()
