"""Load CLI Command Risk as one row per distinct command.

The published rows repeat each command once per shell and expand each seed
template into many near-identical commands. A row's template family is in its
id (`<family>-<shell>-<hash>`); families never cross the published splits, so
they are the unit for sampling, folds and bootstrap ranges. Rows written from
a published reference (`os-` ids) are each their own family.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .questions import LEVELS, RISKY_FROM

DATA_DIR = Path("data/raw/cli-command-risk/data")
SPLITS = ("train", "validation", "test")


@dataclass(frozen=True)
class Command:
    id: str
    command: str
    level: int
    category: str
    family: str

    @property
    def risky(self) -> bool:
        return self.level >= RISKY_FROM


def family_of(row_id: str) -> str:
    stub, _shell, command_hash = row_id.rsplit("-", 2)
    return f"{stub}-{command_hash}" if stub.startswith("os-") else stub


def load_split(split: str, data_dir: Path = DATA_DIR, exclude: frozenset[str] = frozenset()) -> list[Command]:
    """Distinct commands of one split, first row kept; commands in `exclude` are dropped.

    A command whose rows disagree on the risk level is dropped as well.
    """
    import pandas as pd

    rows = pd.read_parquet(data_dir / f"{split}.parquet",
                           columns=["id", "command", "risk_level", "risk_categories"])
    conflicted = rows.groupby("command")["risk_level"].nunique()
    conflicted = set(conflicted[conflicted > 1].index)
    rows = rows.drop_duplicates("command")
    return [Command(r.id, r.command, LEVELS.index(r.risk_level), r.risk_categories[0], family_of(r.id))
            for r in rows.itertuples() if r.command not in conflicted and r.command not in exclude]


def load_all(data_dir: Path = DATA_DIR) -> dict[str, list[Command]]:
    """All three splits; validation and test drop any command that also appears in train."""
    train = load_split("train", data_dir)
    seen = frozenset(c.command for c in train)
    return {"train": train, "validation": load_split("validation", data_dir, seen),
            "test": load_split("test", data_dir, seen)}
