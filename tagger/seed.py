"""Build the files for the Adaption -> Kev loop.

    uv run python -m tagger.seed seed --rows 50000      # real train sample: CSV for Adaption + Kev JSONL
    uv run python -m tagger.seed eval --rows 10000      # real eval sample for Kev, stays local
    uv run python -m tagger.seed jsonl --input data/adaption/expanded_100k.csv --out data/kev/expanded_100k.jsonl

`seed` writes the same real rows twice: a CSV to upload to Adaption
(adaptionlabs.ai, "Improve" then "Expand"), and a Kev training JSONL (one
request per line, label inside the question, as kev.train expects). Every CSV
row carries source=real and its ID, so real rows stay traceable once Adaption
adds generated ones. The same command at a larger --rows is the real-only
control for each synthetic iteration.

`eval` samples only the eval split and is never uploaded: every Kev iteration
is scored on these same real rows.

`jsonl` converts an Adaption export back to Kev training JSONL. Rows without
a source value are counted as generated.

Samples are stratified by the configured `stratify` column and the label, with
a fixed random seed, so reruns give identical files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Dataset, load
from .data import parse_labels, read_raw
from .engine import QUESTION_ID, question, to_state

OUT_DIR = Path("data/kev")
SEED = 20261114


def stratified_sample(ds: Dataset, raw: pd.DataFrame, rows: int, seed: int) -> pd.DataFrame:
    """Sample `rows` rows keeping each (stratify value, label) group's share."""
    if rows >= len(raw):
        return raw.copy()
    keys = [ds.target] + ([ds.stratify] if ds.stratify else [])
    rng = np.random.default_rng(seed)
    picked = []
    for _, idx in sorted(raw.groupby(keys).indices.items()):
        take = round(rows * len(idx) / len(raw))
        picked.extend(rng.choice(idx, size=min(take, len(idx)), replace=False))
    return raw.iloc[np.sort(picked)].reset_index(drop=True)


def kev_lines(ds: Dataset, raw: pd.DataFrame):
    labels = parse_labels(ds, raw[ds.target])
    for row, label in zip(raw.to_dict("records"), labels):
        q = {**question(ds), "label": bool(label)}
        yield json.dumps({"state": to_state(ds, row), "questions": {QUESTION_ID: q}})


def suite_lines(ds: Dataset, raw: pd.DataFrame):
    """The starter kit's suite format, for thinkfast.run_eval against any engine on port 8009."""
    labels = parse_labels(ds, raw[ds.target])
    for row, label in zip(raw.to_dict("records"), labels):
        yield json.dumps({"id": row[ds.id], "family": ds.name,
                          "request": {"state": to_state(ds, row), "questions": {QUESTION_ID: question(ds)}},
                          "labels": {QUESTION_ID: bool(label)}})


def write_lines(path: Path, lines) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for line in lines:
            f.write(line + "\n")
            n += 1
    return n


def describe(ds: Dataset, sample: pd.DataFrame, label: str) -> None:
    y = parse_labels(ds, sample[ds.target])
    print(f"{label}: {len(sample):,} rows, {int(y.sum()):,} positives ({y.mean():.4f})")


def size_tag(rows: int) -> str:
    return f"{rows // 1000}k" if rows >= 1000 else str(rows)


def cmd_seed(ds: Dataset, args) -> None:
    raw = read_raw(ds.files["train"])
    sample = stratified_sample(ds, raw, args.rows, args.seed)
    describe(ds, sample, "seed (real, train split)")
    stem = f"seed_real_{size_tag(args.rows)}"
    sample[ds.input_columns + [ds.target]].assign(source="real").to_csv(OUT_DIR / f"{stem}.csv", index=False)
    n = write_lines(OUT_DIR / f"{stem}.jsonl", kev_lines(ds, sample))
    print(f"wrote {OUT_DIR / (stem + '.csv')} (upload to Adaption) and {OUT_DIR / (stem + '.jsonl')} ({n:,} lines)")


def cmd_eval(ds: Dataset, args) -> None:
    raw = read_raw(ds.files["eval"])
    sample = stratified_sample(ds, raw, args.rows, args.seed)
    describe(ds, sample, "eval sample (real, eval split, local only)")
    stem = f"eval_real_{size_tag(args.rows)}"
    write_lines(OUT_DIR / f"{stem}.jsonl", kev_lines(ds, sample))
    write_lines(OUT_DIR / f"{stem}_suite.jsonl", suite_lines(ds, sample))
    print(f"wrote {OUT_DIR / (stem + '.jsonl')} (kev.benchmark) and {OUT_DIR / (stem + '_suite.jsonl')} "
          "(thinkfast.run_eval)")


def cmd_jsonl(ds: Dataset, args) -> None:
    raw = pd.read_csv(args.input, dtype=str, keep_default_na=False)
    missing = [c for c in ds.features + [ds.target] if c not in raw.columns]
    if missing:
        raise SystemExit(f"{args.input} lacks columns {missing}; got {list(raw.columns)}")
    source = raw["source"].replace("", "generated") if "source" in raw.columns else pd.Series("generated", index=raw.index)
    text = raw[ds.target].astype(str).str.strip().str.lower()
    readable = text.isin(ds.positive + ds.negative)
    kept = raw[readable]
    n = write_lines(args.out, kev_lines(ds, kept))
    print(f"{args.input}: {len(raw):,} rows, source counts {source.value_counts().to_dict()}")
    describe(ds, kept, "converted")
    print(f"wrote {args.out} ({n:,} lines; {int((~readable).sum())} rows skipped for an unreadable label)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, default in (("seed", 50_000), ("eval", 10_000)):
        p = sub.add_parser(name)
        p.add_argument("--rows", type=int, default=default)
        p.add_argument("--seed", type=int, default=SEED)
    p = sub.add_parser("jsonl")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    ds = load()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    {"seed": cmd_seed, "eval": cmd_eval, "jsonl": cmd_jsonl}[args.command](ds, args)


if __name__ == "__main__":
    main()
