"""Write family-capped variants of the Kev fine-tune partitions.

    uv run --extra ml python -m cmdgate.kev_variants --cap 16      # writes data/kev/cmdrisk-cap16/{train,calibration,development}.jsonl

The train split has template families of very uneven size (one command up to
200). A cap keeps at most N commands of each family in train.jsonl, drawn with
a fixed seed, so no template dominates the fine-tune. Calibration and
development are the same records `cmdgate.kev_data` writes: calibration is the
same train families held out whole, development is the validation split. The
test split is never read here.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from .dataset import Command, load_all
from .kev_data import partitions, record_for


def cap_families(commands: list[Command], cap: int, seed: int = 0) -> list[Command]:
    """At most `cap` commands per family, picked at random with a fixed seed; original order kept."""
    by_family: dict[str, list[int]] = {}
    for i, c in enumerate(commands):
        by_family.setdefault(c.family, []).append(i)
    rng = random.Random(seed)
    keep: set[int] = set()
    for family in sorted(by_family):
        rows = by_family[family]
        keep.update(rows if len(rows) <= cap else rng.sample(rows, cap))
    return [c for i, c in enumerate(commands) if i in keep]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cap", type=int, required=True, help="most commands kept per train family")
    parser.add_argument("--out", type=Path, help="default: data/kev/cmdrisk-cap<N>")
    parser.add_argument("--calibration-share", type=float, default=0.1, help="share of train families held out")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    out = args.out or Path(f"data/kev/cmdrisk-cap{args.cap}")
    out.mkdir(parents=True, exist_ok=True)
    parts = partitions(load_all(), args.calibration_share)
    parts["train"] = cap_families(parts["train"], args.cap, args.seed)
    held_out = {c.command for c in parts["calibration"]} | {c.command for c in parts["development"]}
    assert not held_out & {c.command for c in parts["train"]}, "a held-out command is in train"
    for name, commands in parts.items():
        path = out / f"{name}.jsonl"
        with path.open("w") as handle:
            for command in commands:
                handle.write(json.dumps(record_for(command)) + "\n")
        print(f"{path}: {len(commands)} commands, {sum(c.risky for c in commands)} risky, "
              f"{len({c.family for c in commands})} families")


if __name__ == "__main__":
    main()
