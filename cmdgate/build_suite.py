"""Write CLI Command Risk splits as suites the evaluation runner can score.

    uv run --extra ml python -m cmdgate.build_suite

One request per distinct command. `family` is the command's primary risk
category, so the runner's table breaks results down by category; `group` is
the template family, for bootstrap ranges.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from thinkfast.suite import write_jsonl

from .dataset import SPLITS, Command, load_all
from .questions import LEVEL_ID, LEVELS, RISKY_ID, request_for


def item_for(command: Command, with_level: bool = False) -> dict[str, Any]:
    labels: dict[str, Any] = {RISKY_ID: command.risky}
    if with_level:
        labels[LEVEL_ID] = command.level
    return {"id": command.id, "family": command.category, "group": command.family,
            "level": LEVELS[command.level], "request": request_for(command.command, with_level), "labels": labels}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--with-level", action="store_true", help="also ask the five-level score question")
    args = parser.parse_args()
    splits = load_all()
    for split in SPLITS:
        path = args.out / f"cmdrisk_{split}.jsonl"
        write_jsonl([item_for(c, args.with_level) for c in splits[split]], path)
        risky = sum(c.risky for c in splits[split])
        print(f"{path}: {len(splits[split])} commands, {risky} risky, "
              f"{len({c.family for c in splits[split]})} families")


if __name__ == "__main__":
    main()
