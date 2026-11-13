"""Score a model on a split with the gate's own metrics.

    uv run --extra ml python -m cmdgate.evaluate --scorer ngram --split validation
    uv run --extra ml python -m cmdgate.evaluate --scorer vote --members ngram,encoder
    uv run --extra ml python -m cmdgate.evaluate --endpoint http://127.0.0.1:8009/v1/systemone --name kev

`--scorer` runs a model in this process, without the HTTP hop. `--endpoint`
scores any engine that serves the decision API (Kev, the bridge) with the same
metrics, from its answer to the `risky` question. `vote` is the mean of its
members; add an endpoint to it with `--member-endpoint kev=<url>`.

It reports the runner's numbers (accuracy, Brier, calibration error, automation
rate at a 5% error budget) for the yes/no question, plus ranking quality, the
recall on high and critical commands at a 1% false-alarm rate on safe ones, and
a 95% range from resampling whole template families. Results go to
`results/<name>_<split>.json`. The engine takes each model's allow and ask
cut-offs from the validation file, and the KI-Guard dashboard shows the scores.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path
from typing import Any

from thinkfast.metrics import ERROR_BUDGET, Decision, automation_rate, summarize

from .dataset import SPLITS, Command, load_all
from .questions import LEVELS
from .scorers import (IN_PROCESS, VOTE, EndpointScorer, VoteScorer, load_scorer, load_scorers, parse_endpoints,
                      risky_scores)

HIGH = LEVELS.index("high")
SAFE = LEVELS.index("safe")


def score_commands(scorer: Any, commands: list[Command]) -> tuple[list[float], list[list[float]] | None]:
    """P(risky) per command, and the probability per level when the scorer has one."""
    return risky_scores(scorer, [c.command for c in commands])


def decisions_for(commands: list[Command], scores: list[float]) -> list[Decision]:
    return [Decision(c.category, "noul", {"true": s, "false": 1.0 - s}, "true" if c.risky else "false")
            for c, s in zip(commands, scores)]


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


def metrics(commands: list[Command], scores: list[float],
            level_probs: list[list[float]] | None = None) -> dict[str, float]:
    result = summarize(decisions_for(commands, scores))
    result["auroc"] = auroc(scores, [c.risky for c in commands])
    result["severe_recall_at_1pct_fpr"] = recall_at_fpr(scores, [c.level for c in commands])
    if level_probs is not None:
        result["level_accuracy"] = sum(max(range(len(p)), key=p.__getitem__) == c.level
                                       for c, p in zip(commands, level_probs)) / len(commands)
    for runner_only in ("failed", "decisions", "automation_threshold"):
        result.pop(runner_only, None)
    return result


def family_bootstrap(commands: list[Command], scores: list[float], level_probs: list[list[float]] | None = None,
                     resamples: int = 500, seed: int = 0) -> dict[str, tuple[float, float]]:
    """95% range of each metric when whole template families are resampled."""
    by_family: dict[str, list[int]] = {}
    for i, c in enumerate(commands):
        by_family.setdefault(c.family, []).append(i)
    families = sorted(by_family)
    rng = random.Random(seed)
    samples: dict[str, list[float]] = {}
    for _ in range(resamples):
        picked = [i for f in rng.choices(families, k=len(families)) for i in by_family[f]]
        levels = [level_probs[i] for i in picked] if level_probs is not None else None
        for name, value in metrics([commands[i] for i in picked], [scores[i] for i in picked], levels).items():
            if not math.isnan(value):
                samples.setdefault(name, []).append(value)
    return {name: (sorted(v)[int(0.025 * len(v))], sorted(v)[int(0.975 * len(v)) - 1]) for name, v in samples.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scorer", choices=(*IN_PROCESS, VOTE), help="a model run in this process")
    source.add_argument("--endpoint", help="URL of an engine that serves the decision API")
    parser.add_argument("--name", help="name in the results file; required with --endpoint")
    parser.add_argument("--members", default="", help="for vote: in-process members, comma-separated")
    parser.add_argument("--member-endpoint", action="append", default=[], metavar="NAME=URL",
                        help="for vote: a member behind the decision API; repeatable")
    parser.add_argument("--split", choices=SPLITS, default="validation")
    parser.add_argument("--resamples", type=int, default=500)
    parser.add_argument("--out", type=Path, default=Path("results"))
    args = parser.parse_args()
    if args.endpoint and not args.name:
        parser.error("--endpoint needs --name")
    name = args.name or args.scorer
    splits = load_all()
    commands = splits[args.split]
    members: list[str] = []
    if args.endpoint:
        scorer: Any = EndpointScorer(args.endpoint)
    elif args.scorer == VOTE:
        try:
            loaded = load_scorers([m for m in args.members.split(",") if m], parse_endpoints(args.member_endpoint),
                                  splits["train"])
            scorer = VoteScorer(loaded)
        except ValueError as exc:
            parser.error(str(exc))
        members = sorted(loaded)
    else:
        scorer = load_scorer(args.scorer, splits["train"])
    started = time.perf_counter()
    scores, level_probs = score_commands(scorer, commands)
    seconds = time.perf_counter() - started
    failed = getattr(scorer, "failed", 0)
    if args.endpoint and failed == len(commands):
        raise SystemExit(f"every request to {args.endpoint} failed; is the engine running?")
    point = metrics(commands, scores, level_probs)
    ranges = family_bootstrap(commands, scores, level_probs, args.resamples)
    families = len({c.family for c in commands})
    print(f"{name} on {args.split}: {len(commands)} commands, {families} families, {failed} failed")
    for metric, value in point.items():
        low, high = ranges.get(metric, (float('nan'), float('nan')))
        print(f"  {metric:<28}{value:>9.4f}   [{low:.4f}, {high:.4f}]")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{name}_{args.split}.json").write_text(json.dumps(
        {"scorer": name, "split": args.split, "source": "endpoint" if args.endpoint else "in-process",
         "members": members, "commands": len(commands), "families": families, "failed": failed,
         "ms_per_command": 1000 * seconds / len(commands), "error_budget": ERROR_BUDGET,
         "automation_confidence": automation_rate(decisions_for(commands, scores))[1],
         "metrics": point, "ranges": ranges}, indent=2))


if __name__ == "__main__":
    main()
