"""Score a decision API on the validation suite, including severe recall.

    uv run python -m cmdgate.score_api \\
      --endpoint http://127.0.0.1:8010/v1/systemone \\
      --suite data/cmdrisk_validation.jsonl \\
      --out results/kev08_validation

Uses thinkfast.run_eval under the hood, then adds recall on high and critical
commands at a 1% false-alarm rate on safe ones (from each item's `level` field).
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

from thinkfast.metrics import probs_from_answer, report
from thinkfast.run_eval import print_table, run
from thinkfast.suite import read_jsonl

from .evaluate import auroc, recall_at_fpr
from .questions import LEVELS


def severe_metrics(items: list[dict], records: list[dict]) -> dict[str, float]:
    scores: list[float] = []
    levels: list[int] = []
    labels: list[bool] = []
    for item, rec in zip(items, records):
        answers = rec.get("answers") or {}
        q = item["request"]["questions"]["risky"]
        answer = answers.get("risky")
        if answer is None:
            p_true = 0.5
        else:
            probs = probs_from_answer(q, answer)
            p_true = float(probs.get("true", 0.5))
        scores.append(p_true)
        levels.append(LEVELS.index(item["level"]))
        labels.append(bool(item["labels"]["risky"]))
    return {
        "auroc": auroc(scores, labels),
        "severe_recall_at_1pct_fpr": recall_at_fpr(scores, levels, fpr=0.01),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("data/cmdrisk_validation.jsonl"))
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", default="local")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("results/api_validation"))
    args = parser.parse_args()

    items = read_jsonl(args.suite)
    if args.limit:
        items = items[: args.limit]
    decisions, latencies, records = run(
        items, args.endpoint, args.model, args.timeout, args.warmup,
        os.environ.get("DECISION_API_KEY"),
    )
    result = report(decisions, latencies)
    extra = severe_metrics(items, records)
    result["cmdgate"] = extra
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(result, indent=2))
    with (args.out / "answers.jsonl").open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    print_table(result)
    for name, value in extra.items():
        print(f"{name}: {value:.4f}" if not math.isnan(value) else f"{name}: nan")
    overall = result["overall"]
    print(
        f"summary: accuracy={overall['accuracy']:.4f} "
        f"auto@5%={overall['automation_rate_at_5pct']:.4f} "
        f"severe_recall@1%fpr={extra['severe_recall_at_1pct_fpr']:.4f}"
    )
    print(f"report: {args.out / 'report.json'}")


if __name__ == "__main__":
    main()
