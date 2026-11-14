"""Score a model on our hand-made hard set of tricky shell commands.

    uv run --extra ml python -m cmdgate.hardset --scorer rules
    uv run --extra ml python -m cmdgate.hardset --scorer ngram
    uv run --extra ml python -m cmdgate.hardset --endpoint http://127.0.0.1:8009/v1/systemone --name kev
    uv run --extra ml python -m cmdgate.hardset --export data/hard_requests.jsonl
    uv run --extra ml python -m cmdgate.hardset --preds results/preds/kev_hard.json --name kev

`cmdgate/hard_cases.jsonl` holds commands built to fool a string classifier:
minimal pairs that look alike and carry opposite labels (a dry run next to the
real thing, a cache path next to the home folder, a decoded payload that is
printed next to one that is piped into a shell).

The labels are ours, not the dataset's. A language model wrote the commands and
labelled them against the criteria in `cmdgate.questions`; a second language
model, shown only the commands and the criteria, labelled them again
(`second_label`). Cases where the two disagree, or where the author already saw
room for disagreement, are marked `ambiguous` and reported apart from the
agreed ones. No person has reviewed the labels. None of the commands appears
in the train or validation split of CLI Command Risk.

This set is for testing only. Never use it as training data, and never tune on
it: every run is appended to `docs/cmdgate-experiments.jsonl` (split `hard`,
and `hard-agreed` for the cases both labellers agree on).

`--export` writes one decision-API request per line, without labels, so a
model on another machine can answer them; each line also carries the case
`id`. Bring the answers back as `{id: P(risky)}` and score them with `--preds`
or `score_predictions`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .dataset import Command
from .questions import LEVELS, RISKY_FROM, request_for

CASES_PATH = Path(__file__).with_name("hard_cases.jsonl")
PREDS_DIR = Path("results/preds")
SCORERS = ("rules", "ngram", "prior")
SPLIT = "hard"
SPLIT_AGREED = "hard-agreed"
WORST = 15


def load_rows(path: Path = CASES_PATH) -> list[dict[str, Any]]:
    """The raw cases, one dict per line of the file."""
    with path.open() as lines:
        return [json.loads(line) for line in lines if line.strip()]


def load_cases(path: Path = CASES_PATH) -> list[Command]:
    """The cases as `Command`s: the trap slug is the category and every case is its own family."""
    return [Command(row["id"], row["command"], LEVELS.index(row["level"]), row["category"], row["id"])
            for row in load_rows(path)]


def ambiguous_ids(path: Path = CASES_PATH) -> set[str]:
    """Ids of the cases marked ambiguous, including every one the two labellers disagree on."""
    return {row["id"] for row in load_rows(path) if row.get("ambiguous")}


def export_requests(out: Path, path: Path = CASES_PATH) -> int:
    """Write the cases as decision-API requests, one per line, with the case id and no label."""
    cases = load_cases(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as lines:
        for case in cases:
            lines.write(json.dumps({"id": case.id, **request_for(case.command)}, ensure_ascii=False) + "\n")
    return len(cases)


def accuracy(commands: list[Command], scores: list[float]) -> float:
    """Share of cases where P(risky) falls on the labelled side of 0.5."""
    if not commands:
        return float("nan")
    return sum((s >= 0.5) == c.risky for c, s in zip(commands, scores)) / len(commands)


def _subset(commands: list[Command], scores: list[float], keep: set[str]) -> tuple[list[Command], list[float]]:
    pairs = [(c, s) for c, s in zip(commands, scores) if c.id in keep]
    return [c for c, _ in pairs], [s for _, s in pairs]


def report(name: str, commands: list[Command], scores: list[float], ambiguous: set[str],
           config: dict[str, Any] | None = None, log: bool = True, preds_dir: Path | None = PREDS_DIR) -> dict[str, Any]:
    """Print the hard-set scores of one model, log the run and save its predictions."""
    agreed_ids = {c.id for c in commands} - ambiguous
    agreed, agreed_scores = _subset(commands, scores, agreed_ids)
    unsure, unsure_scores = _subset(commands, scores, ambiguous)
    by_category: dict[str, tuple[int, float]] = {}
    for category in sorted({c.category for c in commands}):
        part, part_scores = _subset(commands, scores, {c.id for c in commands if c.category == category})
        by_category[category] = (len(part), accuracy(part, part_scores))
    result = {"accuracy": accuracy(commands, scores), "accuracy_agreed": accuracy(agreed, agreed_scores),
              "accuracy_ambiguous": accuracy(unsure, unsure_scores),
              "by_category": {k: v[1] for k, v in by_category.items()}}

    risky = sum(c.risky for c in commands)
    print(f"{name} on the hard set: {len(commands)} cases, {risky} risky")
    print(f"  accuracy, all cases        {result['accuracy']:.4f}   (n={len(commands)})")
    print(f"  accuracy, agreed cases     {result['accuracy_agreed']:.4f}   (n={len(agreed)})")
    print(f"  accuracy, ambiguous cases  {result['accuracy_ambiguous']:.4f}   (n={len(unsure)})")
    print("  accuracy per trap category")
    for category, (count, value) in sorted(by_category.items(), key=lambda item: item[1][1]):
        print(f"    {category:<20}{value:.4f}   (n={count})")
    mistakes = sorted(((abs(s - 0.5), c, s) for c, s in zip(commands, scores) if (s >= 0.5) != c.risky),
                      key=lambda item: -item[0])
    print(f"  {min(WORST, len(mistakes))} most confident mistakes of {len(mistakes)}")
    for _, case, score in mistakes[:WORST]:
        flag = " (ambiguous)" if case.id in ambiguous else ""
        shown = case.command.replace("\n", "\\n")
        print(f"    {case.id}  P(risky)={score:.3f}  label={'risky' if case.risky else 'not risky'}{flag}  {shown}")

    if log:
        from .lab import log_run
        note = "hand-made hard set, model-written labels; test only"
        log_run(name, SPLIT, commands, scores, config, note)
        log_run(name, SPLIT_AGREED, agreed, agreed_scores, config, note)
    if preds_dir is not None:
        preds_dir.mkdir(parents=True, exist_ok=True)
        (preds_dir / f"{name}_hard.json").write_text(
            json.dumps({c.id: s for c, s in zip(commands, scores)}, indent=1))
    return result


def score_predictions(name: str, preds: dict[str, float], path: Path = CASES_PATH, log: bool = True,
                      preds_dir: Path | None = PREDS_DIR) -> dict[str, Any]:
    """Score `{case id: P(risky)}` computed elsewhere, with the same report and log lines as the CLI."""
    commands = load_cases(path)
    missing = [c.id for c in commands if c.id not in preds]
    if missing:
        raise ValueError(f"{len(missing)} cases have no prediction, first: {missing[:3]}")
    scores = [min(1.0, max(0.0, float(preds[c.id]))) for c in commands]
    return report(name, commands, scores, ambiguous_ids(path), {"source": "predictions"}, log, preds_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--scorer", choices=SCORERS, help="a cheap model run in this process")
    source.add_argument("--endpoint", help="URL of an engine that serves the decision API")
    source.add_argument("--preds", type=Path, help="a JSON file of {id: P(risky)} computed elsewhere")
    source.add_argument("--export", type=Path, metavar="PATH",
                        help="write the cases as decision-API requests (no labels) and exit")
    parser.add_argument("--name", help="name in the log and the predictions file; required with --endpoint and --preds")
    args = parser.parse_args()
    if args.export:
        print(f"wrote {export_requests(args.export)} requests to {args.export}")
        return
    if (args.endpoint or args.preds) and not args.name:
        parser.error("--endpoint and --preds need --name")
    if args.preds:
        score_predictions(args.name, json.loads(args.preds.read_text()))
        return

    from .scorers import EndpointScorer, load_scorer, risky_scores

    commands = load_cases()
    scorer = EndpointScorer(args.endpoint) if args.endpoint else load_scorer(args.scorer)
    scores, _ = risky_scores(scorer, [c.command for c in commands])
    failed = getattr(scorer, "failed", 0)
    if args.endpoint and failed == len(commands):
        raise SystemExit(f"every request to {args.endpoint} failed; is the engine running?")
    if failed:
        print(f"{failed} requests failed and count as P(risky)=0.5")
    source_name = "endpoint" if args.endpoint else "in-process"
    report(args.name or args.scorer, commands, scores, ambiguous_ids(), {"source": source_name, "failed": failed})


if __name__ == "__main__":
    main()
