"""The models the gate can run, by name. The eval harness and the engine both load them from here.

A scorer has `levels(command)` (a probability per risk level) or, when it only
answers the yes/no question, `risky(command)`. `risky_scores` reads either kind.

- `prior`, `rules`: fitted on the train split when loaded, so they need the dataset on disk.
- `ngram`, `encoder`: load their saved model from `models/`.
- `ngram-plus`: the n-gram model fitted with family weights and rewrites (`cmdgate.crosseval`).
- `enc-<name>`: any encoder folder under `models/` saved in the encoder's layout (`cmdgate.modal_encoder`).
- an ensemble in `ENSEMBLES`: the mean P(risky) of the encoders it names.
- an endpoint: any engine that serves the decision API, such as Kev.
- `vote`: the mean P(risky) of its members.
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from typing import Any

from thinkfast.run_eval import post_json

from .questions import LEVELS, RISKY_ID, request_for, risky_probability
from .rules import RulesScorer, fit_level_rates

IN_PROCESS = ("prior", "rules", "ngram", "encoder")
VOTE = "vote"
MODELS_DIR = Path("models")
NGRAM_PLUS = "ngram-plus"
ENCODER_PREFIX = "enc-"
ENSEMBLES = {"enc-ens-qwen4": ("enc-qwenc-cap8", "enc-qwenc-cap8-s1", "enc-qwenc-cap8-s2", "enc-qwen-cap8")}


def saved_encoders(models_dir: Path = MODELS_DIR) -> list[str]:
    """Encoder folders under `models/` that hold a saved model and its temperature."""
    return sorted(folder.name for folder in models_dir.glob(f"{ENCODER_PREFIX}*")
                  if (folder / "cmdgate.json").exists() and (folder / "model.safetensors").exists())


def in_process_names(models_dir: Path = MODELS_DIR) -> list[str]:
    """Every model name `load_scorer` accepts right now."""
    encoders = saved_encoders(models_dir)
    ensembles = [name for name, members in ENSEMBLES.items() if set(members) <= set(encoders)]
    return [*IN_PROCESS, NGRAM_PLUS, *encoders, *ensembles]


class PriorScorer:
    """The training share of each level, for every command."""

    def __init__(self, shares: list[float]) -> None:
        self.shares = shares

    def levels(self, command: str) -> list[float]:
        return self.shares


class EndpointScorer:
    """An engine behind the decision API. It answers the yes/no question only, so it has no level probabilities."""

    def __init__(self, url: str, model: str = "local", timeout: float = 30.0) -> None:
        self.url, self.model, self.timeout = url, model, timeout
        self.failed = 0

    def risky(self, command: str) -> float:
        """P(risky) from the engine; an error or a missing answer counts as 0.5, as in the runner."""
        try:
            answers = post_json(self.url, {"model": self.model, **request_for(command)}, self.timeout, None)
            return min(1.0, max(0.0, float(answers["answers"][RISKY_ID]["noul"])))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, KeyError, TypeError, ValueError):
            self.failed += 1
            return 0.5


class VoteScorer:
    """The mean P(risky) of its members, each counted once."""

    def __init__(self, members: dict[str, Any]) -> None:
        if not members:
            raise ValueError("a vote needs at least one member")
        self.members = members

    def batch_risky(self, commands: list[str]) -> list[float]:
        columns = [risky_scores(member, commands)[0] for member in self.members.values()]
        return [sum(row) / len(row) for row in zip(*columns)]

    def risky(self, command: str) -> float:
        return self.batch_risky([command])[0]

    @property
    def failed(self) -> int:
        return sum(getattr(member, "failed", 0) for member in self.members.values())


def risky_scores(scorer: Any, commands: list[str]) -> tuple[list[float], list[list[float]] | None]:
    """P(risky) per command, and the probability per level when the scorer has one."""
    if hasattr(scorer, "batch_risky"):
        return scorer.batch_risky(commands), None
    if hasattr(scorer, "risky"):
        return [scorer.risky(c) for c in commands], None
    levels = scorer.batch_levels(commands) if hasattr(scorer, "batch_levels") else [scorer.levels(c) for c in commands]
    return [risky_probability(p) for p in levels], levels


def load_scorer(name: str, train: list | None = None) -> Any:
    """One in-process model by name. `train` is the train split, read from disk when a model needs it."""
    if name in ("prior", "rules"):
        if train is None:
            from .dataset import load_all
            train = load_all()["train"]
        if name == "prior":
            return PriorScorer([sum(c.level == i for c in train) / len(train) for i in range(len(LEVELS))])
        return RulesScorer(*fit_level_rates((c.command for c in train), (c.level for c in train)))
    if name == "ngram":
        from .ngram import NgramScorer
        return NgramScorer()
    if name == "encoder":
        from .encoder import EncoderScorer
        return EncoderScorer()
    if name == NGRAM_PLUS:
        from .crosseval import load_ngram_plus
        return load_ngram_plus()
    if name in ENSEMBLES:
        return VoteScorer({member: load_scorer(member) for member in ENSEMBLES[name]})
    if name in saved_encoders():
        from .encoder import EncoderScorer
        return EncoderScorer(MODELS_DIR / name)
    raise ValueError(f"unknown scorer: {name}; available: {', '.join(in_process_names())}")


def parse_endpoints(specs: list[str]) -> dict[str, str]:
    """`name=url` arguments as a dict."""
    endpoints = {}
    for spec in specs:
        name, sep, url = spec.partition("=")
        if not sep or not name or not url or name in in_process_names() or name in ENSEMBLES or name == VOTE:
            raise ValueError(f"expected name=url with a name that is not a built-in model, got {spec!r}")
        endpoints[name] = url
    return endpoints


def load_scorers(names: list[str], endpoints: dict[str, str], train: list | None = None) -> dict[str, Any]:
    """The named in-process models plus one scorer per endpoint, in a stable order."""
    scorers = {name: load_scorer(name, train) for name in names if name not in ENSEMBLES}
    for name in names:
        if name in ENSEMBLES:  # reuse members that are already loaded instead of loading them twice
            scorers[name] = VoteScorer({m: scorers[m] if m in scorers else load_scorer(m) for m in ENSEMBLES[name]})
    scorers.update({name: EndpointScorer(url) for name, url in endpoints.items()})
    return scorers
