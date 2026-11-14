"""Score the logits `cmdgate.modal_encoder` brought back and log them with the bench's own metrics.

    uv run --extra ml python -m cmdgate.encoder_log oof mb-cap8             # pick the epoch out-of-fold, log it
    uv run --extra ml python -m cmdgate.encoder_log val mb-cap8             # log the final model's validation scores
    uv run --extra ml python -m cmdgate.encoder_log mean NAME train-oof enc-a enc-b   # mean P(risky) of members

`oof` reads `results/preds/raw/enc-<name>_fold<k>.json`. For every evaluated
epoch it fits a temperature per fold on the other held-out folds (so no
temperature has seen the rows it scales), and keeps the epoch with the lowest
Brier score (`--last` keeps the last epoch instead: single epochs swing by a point or two between runs). It writes `results/preds/enc-<name>_train-oof.json`, appends one
line to the experiment log and prints the temperature fitted on all held-out
folds, which is the one the final model is saved with. No model is loaded here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .dataset import Command, load_split
from .encoder import fit_temperature, softmax
from .evaluate import metrics
from .lab import log_run
from .questions import RISKY_FROM

PREDS = Path("results/preds")
RAW = PREDS / "raw"
SHOWN = ("accuracy", "automation_rate_at_5pct", "auroc", "severe_recall_at_1pct_fpr", "ece", "brier")


def commands_by_id(split: str) -> dict[str, Command]:
    train = load_split("train")
    if split == "train-oof":
        return {c.id: c for c in train}
    return {c.id: c for c in load_split(split, exclude=frozenset(c.command for c in train))}


def p_risky(row: list[float], temperature: float) -> float:
    probs = softmax(row, temperature)
    return probs[1] if len(probs) == 2 else sum(probs[RISKY_FROM:])


def target(command: Command, width: int) -> int:
    return int(command.risky) if width == 2 else command.level


def write_preds(name: str, split: str, scores: dict[str, float]) -> Path:
    PREDS.mkdir(parents=True, exist_ok=True)
    path = PREDS / f"{name}_{split}.json"
    path.write_text(json.dumps({k: round(v, 6) for k, v in scores.items()}))
    return path


def oof(name: str, epoch: int | None, log: bool, last: bool = False) -> None:
    lookup = commands_by_id("train-oof")
    parts = [json.loads(p.read_text()) for p in sorted(RAW.glob(f"enc-{name}_fold*.json"))]
    if not parts:
        raise SystemExit(f"no fold files for {name} in {RAW}")
    folds = [p["fold"] for p in parts]
    cfg = parts[0]["config"]
    table = {}
    for ep in sorted(set.intersection(*(set(p["logits"]) for p in parts)), key=int):
        labels = [[target(lookup[i], len(p["logits"][ep][0])) for i in p["ids"]] for p in parts]
        scores: dict[str, float] = {}
        for k, part in enumerate(parts):
            if len(parts) > 1:
                rest = [(row, y) for j, other in enumerate(parts) if j != k
                        for row, y in zip(other["logits"][ep], labels[j])]
                temperature = fit_temperature([r for r, _ in rest], [y for _, y in rest])
            else:
                temperature = 1.0
            scores.update({i: p_risky(row, temperature) for i, row in zip(part["ids"], part["logits"][ep])})
        pooled = fit_temperature([r for p in parts for r in p["logits"][ep]], [y for ys in labels for y in ys])
        table[ep] = (metrics([lookup[i] for i in scores], list(scores.values())), scores, pooled)
        print(f"  epoch {ep:>2}  T={pooled:5.2f}  " + "  ".join(f"{k[:10]}={table[ep][0][k]:.4f}" for k in SHOWN))
    best = str(epoch) if epoch else max(table, key=int) if last else min(table, key=lambda ep: table[ep][0]["brier"])
    _, scores, pooled = table[best]
    print(f"{name}: epoch {best}, temperature on all held-out folds {pooled:.4f}, folds {folds}")
    if log:
        write_preds(f"enc-{name}", "train-oof", scores)
        log_run(f"enc-{name}", "train-oof", [lookup[i] for i in scores], list(scores.values()),
                config={**cfg, "folds": folds, "epoch": int(best), "temperature_all_folds": round(pooled, 4),
                        "gpu": parts[0]["gpu"], "seconds": sum(p["seconds"] for p in parts)},
                note=f"Modal GPU; held-out families, folds {folds} of 5; "
                     f"{'last epoch' if last else 'epoch given' if epoch else 'epoch chosen by out-of-fold Brier'}; "
                     "temperature cross-fitted on the other held-out folds")


def val(name: str) -> None:
    lookup = commands_by_id("validation")
    raw = json.loads((RAW / f"enc-{name}_validation.json").read_text())
    rows = raw["logits"][str(raw["epoch"])]
    scores = {i: p_risky(row, raw["temperature"]) for i, row in zip(raw["ids"], rows)}
    write_preds(f"enc-{name}", "validation", scores)
    log_run(f"enc-{name}", "validation", [lookup[i] for i in scores], list(scores.values()),
            config={**raw["config"], "epoch": raw["epoch"], "temperature": raw["temperature"], "gpu": raw["gpu"],
                    "seconds": raw["seconds"]},
            note="Modal GPU; fitted on all of train; epoch and temperature chosen on held-out train families")


def mean(name: str, split: str, members: list[str]) -> None:
    lookup = commands_by_id(split)
    preds = [json.loads((PREDS / f"{m}_{split}.json").read_text()) for m in members]
    shared = [i for i in preds[0] if all(i in p for p in preds)]
    scores = {i: sum(p[i] for p in preds) / len(preds) for i in shared}
    write_preds(name, split, scores)
    log_run(name, split, [lookup[i] for i in scores], list(scores.values()), config={"members": members},
            note="mean P(risky) of the members, each calibrated on held-out train families")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("oof")
    p.add_argument("names", nargs="+")
    p.add_argument("--epoch", type=int, help="log this epoch instead of the best by Brier")
    p.add_argument("--last", action="store_true", help="log the last epoch, where the learning rate has decayed")
    p.add_argument("--no-log", action="store_true", help="print the per-epoch table only")
    p = sub.add_parser("val")
    p.add_argument("names", nargs="+")
    p = sub.add_parser("mean")
    p.add_argument("name")
    p.add_argument("split")
    p.add_argument("members", nargs="+")
    args = parser.parse_args()
    if args.action == "oof":
        for name in args.names:
            oof(name, args.epoch, not args.no_log, args.last)
    elif args.action == "val":
        for name in args.names:
            val(name)
    else:
        mean(args.name, args.split, args.members)


if __name__ == "__main__":
    main()
