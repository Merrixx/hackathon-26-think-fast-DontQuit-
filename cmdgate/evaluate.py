"""Score a scorer on a split without the HTTP hop, with the gate's own metrics.

    uv run --extra ml python -m cmdgate.evaluate --scorer ngram --split validation

It reports the runner's numbers (accuracy, Brier, calibration error, automation
rate at a 5% error budget) for the yes/no question, plus ranking quality, the
recall on high and critical commands at a 1% false-alarm rate on safe ones, and
a 95% range from resampling whole template families.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any

from thinkfast.metrics import Decision, summarize

from .dataset import SPLITS, Command, load_all
from .questions import LEVELS, risky_probability
from .rules import RulesScorer, fit_level_rates

HIGH = LEVELS.index("high")
SAFE = LEVELS.index("safe")


def score_levels(scorer: Any, commands: list[Command]) -> list[list[float]]:
    texts = [c.command for c in commands]
    if hasattr(scorer, "batch_levels"):
        return scorer.batch_levels(texts)
    return [scorer.levels(t) for t in texts]


def auroc(scores: list[float], labels: list[bool]) -> float:
    """Probability that a risky command outscores a non-risky one (ties count half)."""
    order = sorted(range(len(scores)), key=scores.__getitem__)
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j < len(order) and scores[order[j]] == scores[order[i]]:
            j += 1
        for k in range(i, j):
            ranks[order[k]] = (i + j + 1) / 2
        i = j
    positives = sum(labels)
    negatives = len(labels) - positives
    if not positives or not negatives:
        return float("nan")
    return (sum(r for r, y in zip(ranks, labels) if y) - positives * (positives + 1) / 2) / (positives * negatives)


def recall_at_fpr(scores: list[float], levels: list[int], fpr: float = 0.01) -> float:
    """Share of high and critical commands flagged when at most `fpr` of safe ones are."""
    safe = sorted((s for s, lv in zip(scores, levels) if lv == SAFE), reverse=True)
    severe = [s for s, lv in zip(scores, levels) if lv >= HIGH]
    if not safe or not severe:
        return float("nan")
    allowed = int(fpr * len(safe))
    threshold = safe[allowed] if allowed < len(safe) else float("-inf")
    return sum(s > threshold for s in severe) / len(severe)


def metrics(commands: list[Command], level_probs: list[list[float]]) -> dict[str, float]:
    scores = [risky_probability(p) for p in level_probs]
    decisions = [Decision(c.category, "noul", {"true": s, "false": 1.0 - s}, "true" if c.risky else "false")
                 for c, s in zip(commands, scores)]
    result = summarize(decisions)
    result["auroc"] = auroc(scores, [c.risky for c in commands])
    result["severe_recall_at_1pct_fpr"] = recall_at_fpr(scores, [c.level for c in commands])
    result["level_accuracy"] = sum(max(range(len(p)), key=p.__getitem__) == c.level
                                   for c, p in zip(commands, level_probs)) / len(commands)
    for runner_only in ("failed", "decisions", "automation_threshold"):
        result.pop(runner_only, None)
    return result


def family_bootstrap(commands: list[Command], level_probs: list[list[float]], resamples: int = 500,
                     seed: int = 0) -> dict[str, tuple[float, float]]:
    """95% range of each metric when whole template families are resampled."""
    by_family: dict[str, list[int]] = {}
    for i, c in enumerate(commands):
        by_family.setdefault(c.family, []).append(i)
    families = sorted(by_family)
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {}
    for _ in range(resamples):
        picked = [i for f in rng.choices(families, k=len(families)) for i in by_family[f]]
        for name, value in metrics([commands[i] for i in picked], [level_probs[i] for i in picked]).items():
            if not math.isnan(value):
                samples.setdefault(name, []).append(value)
    return {name: (sorted(v)[int(0.025 * len(v))], sorted(v)[int(0.975 * len(v)) - 1]) for name, v in samples.items()}


def make_scorer(name: str, train: list[Command]) -> Any:
    if name == "rules":
        return RulesScorer(*fit_level_rates((c.command for c in train), (c.level for c in train)))
    if name == "prior":
        counts = [sum(c.level == i for c in train) / len(train) for i in range(len(LEVELS))]
        return type("PriorScorer", (), {"levels": lambda self, command: counts})()
    if name == "ngram":
        from .ngram import NgramScorer
        return NgramScorer()
    if name == "hashreg":
        from .hashreg import HashRegScorer
        return HashRegScorer()
    raise ValueError(f"unknown scorer: {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scorer", choices=["prior", "rules", "ngram", "hashreg"], required=True)
    parser.add_argument("--split", choices=SPLITS, default="validation")
    parser.add_argument("--resamples", type=int, default=500)
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args()
    splits = load_all()
    commands = splits[args.split]
    level_probs = score_levels(make_scorer(args.scorer, splits["train"]), commands)
    point = metrics(commands, level_probs)
    ranges = family_bootstrap(commands, level_probs, args.resamples)
    print(f"{args.scorer} on {args.split}: {len(commands)} commands, {len({c.family for c in commands})} families")
    for name, value in point.items():
        low, high = ranges.get(name, (float('nan'), float('nan')))
        print(f"  {name:<28}{value:>9.4f}   [{low:.4f}, {high:.4f}]")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.scorer}_{args.split}.json").write_text(
        json.dumps({"scorer": args.scorer, "split": args.split, "metrics": point, "ranges": ranges}, indent=2))


if __name__ == "__main__":
    main()
