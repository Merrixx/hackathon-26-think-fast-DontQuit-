"""Fit the classic candidates on train and compare them, plus any decision-API model, on the same eval rows.

    uv run python -m tagger.compare
    uv run python -m tagger.compare --sample 10000 --endpoint http://127.0.0.1:8009/v1/systemone --label kev-0.8b-real-50k

Use --sample to score a slower model (e.g. a Kev checkpoint on CPU): the same
fixed random sample of eval rows is used for every candidate and every run, so
iterations stay comparable. Writes results/compare.json and appends one line
per candidate to results/compare_history.jsonl.

The in-sample automation column uses the starter kit's automation_rate, which
picks its threshold on the rows it scores; the frozen policy is tagger.freeze.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from thinkfast.metrics import Decision, automation_rate

from .config import load
from .data import load_split, read_raw
from .engine import DEFAULT_ENDPOINT, HttpEngine
from .models import CANDIDATES
from .policy import choose_policy, policy_report

SAMPLE_SEED = 20261114
RESULTS = Path("results")
CALIBRATION_EDGES = [0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.9, 1.0001]


def calibration_table(y: np.ndarray, p: np.ndarray) -> list[dict]:
    rows = []
    for lo, hi in zip(CALIBRATION_EDGES[:-1], CALIBRATION_EDGES[1:]):
        mask = (p >= lo) & (p < hi)
        n = int(mask.sum())
        rows.append({"bin": f"{lo:.2f}-{min(hi, 1.0):.2f}", "count": n,
                     "mean_p": float(p[mask].mean()) if n else None,
                     "observed": float(y[mask].mean()) if n else None})
    return rows


def evaluate(y: np.ndarray, p: np.ndarray, budget: float, min_support: int) -> dict:
    decisions = [Decision("eval", "noul", {"true": float(v), "false": 1 - float(v)},
                          "true" if t else "false") for t, v in zip(y, p)]
    varied = len(np.unique(p)) > 1 and 0 < y.sum() < len(y)
    policy = choose_policy(p, y, budget=budget, min_support=min_support)
    return {"rows": int(len(y)),
            "roc_auc": float(roc_auc_score(y, p)) if varied else None,
            "pr_auc": float(average_precision_score(y, p)) if y.sum() else None,
            "brier": float(brier_score_loss(y, p)),
            "automation_in_sample_5pct": automation_rate(decisions)[0],
            "policy_in_sample": {"lower": policy.lower, "upper": policy.upper,
                                 **policy_report(p, y, policy)},
            "calibration": calibration_table(y, p)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=int, default=0, help="score only this many eval rows (fixed sample)")
    parser.add_argument("--endpoint", nargs="?", const=DEFAULT_ENDPOINT, help="also score a decision-API model")
    parser.add_argument("--label", default="api_model", help="name for the --endpoint model in the results")
    parser.add_argument("--workers", type=int, default=4, help="parallel requests to --endpoint")
    parser.add_argument("--skip-classic", action="store_true", help="only score --endpoint")
    args = parser.parse_args()
    ds = load()

    dev = load_split(ds, "eval")
    raw_dev = read_raw(ds.files["eval"])
    if args.sample and args.sample < len(dev):
        idx = np.sort(np.random.default_rng(SAMPLE_SEED).choice(len(dev), size=args.sample, replace=False))
        dev, raw_dev = dev.iloc[idx].reset_index(drop=True), raw_dev.iloc[idx].reset_index(drop=True)
    y = dev["label"].to_numpy()

    results = {"dataset": ds.name, "eval_rows": len(dev), "eval_positive_rate": float(y.mean()),
               "sample": args.sample or None, "models": {}}
    if not args.skip_classic:
        train = load_split(ds, "train")
        results["train_rows"] = len(train)
        results["train_positive_rate"] = float(train["label"].mean())
        for name, cls in CANDIDATES.items():
            started = time.perf_counter()
            model = cls(ds).fit(train)
            fit_s = time.perf_counter() - started
            results["models"][name] = {**evaluate(y, model.predict_proba(dev), ds.error_budget, ds.min_support),
                                       "fit_seconds": round(fit_s, 2)}
    if args.endpoint:
        engine = HttpEngine(ds, args.endpoint, args.label, workers=args.workers)
        started = time.perf_counter()
        p = engine.predict_frame(raw_dev)
        results["models"][args.label] = {**evaluate(y, p, ds.error_budget, ds.min_support),
                                         "endpoint": args.endpoint,
                                         "score_seconds": round(time.perf_counter() - started, 2)}

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "compare.json").write_text(json.dumps(results, indent=2))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with (RESULTS / "compare_history.jsonl").open("a") as f:
        for name, m in results["models"].items():
            row = {k: v for k, v in m.items() if k != "calibration"}
            f.write(json.dumps({"at_utc": stamp, "dataset": ds.name, "sample": results["sample"],
                                "candidate": name, **row}) + "\n")
    print_table(results, ds.error_budget)
    print(f"\nsaved {RESULTS / 'compare.json'}; appended {RESULTS / 'compare_history.jsonl'}")


def fmt(value, spec: str) -> str:
    return "n/a" if value is None else format(value, spec)


def print_table(results: dict, budget: float) -> None:
    print(f"dataset {results['dataset']} | eval {results['eval_rows']:,} rows "
          f"(positive rate {results['eval_positive_rate']:.4f})\n")
    print(f"{'candidate':<24}{'ROC AUC':>9}{'PR AUC':>9}{'Brier':>9}{'auto@5%':>9}"
          f"{f'cov@{budget:.0%}':>9}{'err':>8}   (auto and cov are in-sample)")
    for name, m in results["models"].items():
        pol = m["policy_in_sample"]
        print(f"{name:<24}{fmt(m['roc_auc'], '.4f'):>9}{fmt(m['pr_auc'], '.4f'):>9}{m['brier']:>9.5f}"
              f"{m['automation_in_sample_5pct']:>9.3f}{fmt(pol['coverage'], '.3f'):>9}"
              f"{fmt(pol['selective_error'], '.4f'):>8}")


if __name__ == "__main__":
    main()
