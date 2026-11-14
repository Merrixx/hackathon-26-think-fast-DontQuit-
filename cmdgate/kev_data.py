"""Write the three partitions a Kev fine-tune reads: train, calibration and development.

    uv run --extra ml python -m cmdgate.kev_data          # writes data/kev/cmdrisk/{train,calibration,development}.jsonl

Each line is a decision API request with a `label` on every question, the
format `kev.train` and Kev's Modal fine-tune app take. Calibration is a share
of the train split's template families, held out whole, so the temperature is
fitted on families the fine-tune never saw. Development is the validation
split. The test split is never read here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .dataset import Command, load_all
from .questions import LEVEL_ID, RISKY_ID, request_for


def record_for(command: Command, with_level: bool = False) -> dict[str, Any]:
    record = request_for(command.command, with_level)
    record["questions"] = {key: dict(question) for key, question in record["questions"].items()}
    record["questions"][RISKY_ID]["label"] = command.risky
    if with_level:
        record["questions"][LEVEL_ID]["label"] = command.level
    return record


def in_calibration(family: str, share: float) -> bool:
    """A stable draw per family, so the same families are held out on every run."""
    digest = hashlib.sha256(family.encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < share


def partitions(splits: dict[str, list[Command]], share: float) -> dict[str, list[Command]]:
    train = [c for c in splits["train"] if not in_calibration(c.family, share)]
    calibration = [c for c in splits["train"] if in_calibration(c.family, share)]
    return {"train": train, "calibration": calibration, "development": splits["validation"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("data/kev/cmdrisk"))
    parser.add_argument("--calibration-share", type=float, default=0.1, help="share of train families held out")
    parser.add_argument("--with-level", action="store_true", help="also train the five-level score question")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name, commands in partitions(load_all(), args.calibration_share).items():
        path = args.out / f"{name}.jsonl"
        with path.open("w") as handle:
            for command in commands:
                handle.write(json.dumps(record_for(command, args.with_level)) + "\n")
        print(f"{path}: {len(commands)} commands, {sum(c.risky for c in commands)} risky, "
              f"{len({c.family for c in commands})} families")


if __name__ == "__main__":
    main()
