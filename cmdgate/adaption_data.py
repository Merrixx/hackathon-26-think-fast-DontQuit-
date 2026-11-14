"""Turn an Adaption-expanded export of the train split into Kev fine-tune partitions.

    uv run --extra ml python -m cmdgate.adaption_data --export data/adaption/<id> --mode plus
    uv run --extra ml python -m cmdgate.adaption_data --export data/adaption/<id> --mode full

The export is a folder of parquet parts with two columns: `prompt` (the command,
a blank line, then an explanation) and `completion` (a risk level). Only the
command is kept: the explanation names the label and is never a model input.

Rows are dropped when the prompt has no explanation part (some of those are
prose, not commands), when the level is not one we can map, when a command got
both a risky and a not-risky label, and when the command also appears in the
validation or test split. Test commands are read here only to be excluded.

Three training files, all in the contract schema of `cmdgate.kev_data`:

- `plus`: the family-capped train rows of `cmdgate.kev_variants`, plus every
  command the export adds to the train split.
- `balanced`: as `plus`, but the added risky commands are sampled down (seed 0)
  to the number of added not-risky ones. The export's additions are mostly
  risky, which shifts the model toward flagging.
- `full`: every command of the export, the ones from train and the added ones,
  without a cap.

Calibration and development are the partitions `cmdgate.kev_data` writes. Train
commands of the held-out calibration families are left out of both files. The
commands Adaption generated have no family id, so variants of a calibration
family may still be in train; the fitted temperature can be too confident for
that reason.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from .dataset import Command, load_split
from .kev_data import partitions, record_for
from .kev_variants import cap_families
from .questions import LEVELS

LEVEL_ALIASES = {"medium": "moderate", "severe": "high"}
FAMILY = "adaption"


def parse_rows(rows: list[tuple[str, str]]) -> dict[str, int]:
    """Command -> level index, for rows that have a command, a known level and one yes/no label."""
    from .questions import RISKY_FROM

    levels: dict[str, set[int]] = {}
    for prompt, completion in rows:
        command, separator, _explanation = prompt.rpartition("\n\n")
        level = LEVEL_ALIASES.get(completion.strip().lower(), completion.strip().lower())
        if not separator or not command.strip() or level not in LEVELS:
            continue
        levels.setdefault(command.strip(), set()).add(LEVELS.index(level))
    return {command: max(found) for command, found in levels.items()
            if len({level >= RISKY_FROM for level in found}) == 1}


def load_export(folder: Path) -> dict[str, int]:
    import pandas as pd

    frames = [pd.read_parquet(part, columns=["prompt", "completion"]) for part in sorted(folder.glob("*.parquet"))]
    if not frames:
        raise FileNotFoundError(f"no parquet parts in {folder}")
    rows = pd.concat(frames, ignore_index=True)
    return parse_rows(list(zip(rows["prompt"], rows["completion"], strict=True)))


def added_commands(export: dict[str, int], train: list[Command], held_out: set[str]) -> list[Command]:
    """Export commands that are in no published split, as their own one-command families."""
    known = {c.command for c in train} | held_out
    return [Command(f"{FAMILY}-{i}", command, level, FAMILY, f"{FAMILY}-{i}")
            for i, (command, level) in enumerate(sorted(export.items())) if command not in known]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export", type=Path, required=True, help="folder with the export's parquet parts")
    parser.add_argument("--mode", choices=("plus", "balanced", "full"), default="plus")
    parser.add_argument("--cap", type=int, default=16, help="per-family cap on train rows in plus mode")
    parser.add_argument("--out", type=Path, help="default: data/kev/cmdrisk-adaption-<mode>")
    args = parser.parse_args()
    out = args.out or Path(f"data/kev/cmdrisk-adaption-{args.mode}")

    train = load_split("train")
    seen = frozenset(c.command for c in train)
    validation, test = load_split("validation", exclude=seen), load_split("test", exclude=seen)
    parts = partitions({"train": train, "validation": validation}, 0.1)
    export = load_export(args.export)
    added = added_commands(export, train, {c.command for c in validation} | {c.command for c in test})
    if args.mode == "balanced":
        safe = [c for c in added if not c.risky]
        added = safe + random.Random(0).sample([c for c in added if c.risky], len(safe))
    if args.mode in ("plus", "balanced"):
        base = cap_families(parts["train"], args.cap)
    else:
        base = [c for c in parts["train"] if c.command in export]
    parts["train"] = base + added

    held = {c.command for c in parts["calibration"]} | {c.command for c in parts["development"]}
    assert not held & {c.command for c in parts["train"]}, "a held-out command is in train"
    out.mkdir(parents=True, exist_ok=True)
    for name, commands in parts.items():
        with (out / f"{name}.jsonl").open("w") as handle:
            for command in commands:
                handle.write(json.dumps(record_for(command)) + "\n")
        print(f"{out / name}.jsonl: {len(commands)} commands, {sum(c.risky for c in commands)} risky")
    print(f"export: {len(export)} usable commands, {len(added)} added to train, "
          f"{sum(c.risky for c in added)} of those risky; {len(base)} rows from the train split")


if __name__ == "__main__":
    main()
