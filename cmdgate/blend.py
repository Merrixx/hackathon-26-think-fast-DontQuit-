"""Average the saved predictions of several models and score the average like any other model.

    uv run --extra ml python -m cmdgate.blend --name blend-kev-ngram \\
        --members kev-cmdrisk-08b-cap16-e1,ngram-plus --splits validation,hard,x-swe-chat

Each member's P(risky) per command is read from `results/preds/<member>_<split>.json`.
The blend is their plain mean: no weights are fitted, so nothing is tuned on the
split it is scored on. Every member must have a prediction for every command.
Splits: `train-oof`, `validation`, `hard` (the hand-made hard set) and
`x-<source>` (a source in `cmdgate.crosseval`). Each scored split is one line
in the experiment log.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import crosseval, hardset
from .dataset import Command, load_split
from .lab import log_run

PREDS_DIR = Path("results/preds")


def mean_predictions(members: list[dict[str, float]]) -> dict[str, float]:
    """The mean per id. Every member must cover the same ids."""
    ids = set(members[0])
    for other in members[1:]:
        if set(other) != ids:
            raise ValueError(f"members disagree on {len(ids ^ set(other))} command ids")
    return {i: sum(m[i] for m in members) / len(members) for i in members[0]}


def split_commands(split: str) -> dict[str, Command]:
    train = load_split("train")
    if split == "train-oof":
        return {c.id: c for c in train}
    return {c.id: c for c in load_split(split, exclude=frozenset(c.command for c in train))}


def blend(name: str, members: list[str], split: str, preds_dir: Path = PREDS_DIR) -> None:
    scores = mean_predictions([json.loads((preds_dir / f"{m}_{split}.json").read_text()) for m in members])
    config = {"members": members, "rule": "mean P(risky)"}
    if split == "hard":
        hardset.score_predictions(name, scores)
    elif split.startswith("x-"):
        crosseval.score_predictions(name, split.removeprefix("x-"), scores, config, preds_dir=preds_dir)
    else:
        if split == "test":
            raise ValueError("the test split is scored once, by cmdgate.evaluate, after the design is frozen")
        lookup = split_commands(split)
        log_run(name, split, [lookup[i] for i in scores], list(scores.values()), config)
        (preds_dir / f"{name}_{split}.json").write_text(json.dumps(scores))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="name of the blend in the log and in results/preds/")
    parser.add_argument("--members", required=True, help="model names, comma-separated")
    parser.add_argument("--splits", default="validation", help="comma-separated splits to score")
    args = parser.parse_args()
    for split in args.splits.split(","):
        blend(args.name, args.members.split(","), split)


if __name__ == "__main__":
    main()
