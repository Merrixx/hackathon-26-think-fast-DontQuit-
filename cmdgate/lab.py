"""Experiment bench for the command gate: tune inside train, log every run.

All model selection happens on folds of the train split that keep whole
template families together (`family_folds`), because the published validation
and test splits hold families the model has never seen. A model is fitted on
four fifths of the train families and scored on the held-out fifth; the
out-of-fold scores over all of train are the tuning signal. Validation is then
a benchmark that no tuning decision has read, and test is never read here.

Every scored run, on any split, is appended to `docs/cmdgate-experiments.jsonl`
with `log_run`, so the count of looks at validation is on record.
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .dataset import Command
from .evaluate import metrics

LOG_PATH = Path("docs/cmdgate-experiments.jsonl")
FOLDS = 5


def family_folds(commands: Sequence[Command], k: int = FOLDS, seed: int = 0) -> list[int]:
    """A fold number per command; every command of a template family gets the same fold."""
    families = sorted({c.family for c in commands})
    random.Random(seed).shuffle(families)
    fold_of = {family: i % k for i, family in enumerate(families)}
    return [fold_of[c.family] for c in commands]


def family_weights(commands: Sequence[Command], power: float = 0.5) -> list[float]:
    """A weight per command that shrinks with its family's size: size ** -power, scaled to mean 1.

    Power 0 counts every command once, power 1 counts every family once.
    """
    sizes: dict[str, int] = {}
    for c in commands:
        sizes[c.family] = sizes.get(c.family, 0) + 1
    raw = [sizes[c.family] ** -power for c in commands]
    mean = sum(raw) / len(raw)
    return [w / mean for w in raw]


def out_of_fold(fit_predict: Callable[[list[Command], list[Command]], Any], commands: Sequence[Command],
                folds: Sequence[int]) -> list[Any]:
    """Predictions for every command from a model fitted without that command's fold.

    `fit_predict(train_part, held_out)` returns one prediction per held-out command.
    """
    out: list[Any] = [None] * len(commands)
    for fold in sorted(set(folds)):
        inside = [c for c, f in zip(commands, folds) if f != fold]
        held = [i for i, f in enumerate(folds) if f == fold]
        for i, prediction in zip(held, fit_predict(inside, [commands[i] for i in held]), strict=True):
            out[i] = prediction
    return out


def log_run(name: str, split: str, commands: Sequence[Command], scores: Sequence[float],
            config: dict[str, Any] | None = None, note: str = "", path: Path = LOG_PATH) -> dict[str, float]:
    """Score P(risky) per command, append the run to the log and return its metrics.

    `split` says what was scored: `train-oof` for out-of-fold train scores, or a published split name.
    """
    result = metrics(list(commands), list(scores))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as log:
        log.write(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%S"), "name": name, "split": split,
                              "commands": len(commands), "config": config or {}, "note": note,
                              "metrics": {k: round(v, 4) for k, v in result.items()}}) + "\n")
    print(f"{name:<44}{split:<11}" + "  ".join(f"{k}={v:.4f}" for k, v in result.items()), flush=True)
    return result
