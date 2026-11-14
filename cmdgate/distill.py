"""Distil a larger Kev into the small one: the teacher labels real, unlabeled commands.

    uv run --extra ml python -m cmdgate.distill pool                      # data/kev/distill-pool/
    uv run --extra ml python -m cmdgate.distill build --preds <file>      # data/kev/cmdrisk-distill/

`pool` writes real commands without labels for the teacher to score: a
fixed-seed sample of InfraSet (Apache-2.0, a sysadmin agent's commands). Left
out: the InfraSet sample `cmdgate.crosseval` tests on, every command of the
CLI Command Risk splits, of the other test sources and of the hard set, and
commands too long for Kev's training context. The file carries a placeholder
label because Kev's scorer needs one; it is never used.

`build` takes the teacher's `{id: P(risky)}` for the pool, keeps the commands
it is confident about, and writes a training file in the contract schema: the
family-capped train rows of `cmdgate.kev_variants` plus the pool commands with
the teacher's yes/no answer as the label. Calibration and development are the
partitions of `cmdgate.kev_data`, untouched. These labels come from a model.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

from . import crosseval, hardset
from .dataset import Command, load_split
from .kev_data import partitions, record_for
from .kev_variants import cap_families
from .questions import LEVELS

POOL_DIR = Path("data/kev/distill-pool")
MAX_CHARS = 650
SAFE, HIGH = LEVELS.index("safe"), LEVELS.index("high")


def test_material() -> set[str]:
    """Every command some evaluation reads, so none of it becomes training data."""
    seen = {c.command for split in ("train", "validation", "test") for c in load_split(split)}
    seen |= {c.command for c in hardset.load_cases()}
    for source in crosseval.SOURCES:
        seen |= {c.command for c in crosseval.load_source(source)}
    return seen


def write_pool(size: int, out: Path = POOL_DIR, seed: int = 0) -> None:
    excluded = test_material()
    commands = sorted({c.command.strip() for c in crosseval.load_infraset()} - excluded)
    commands = [c for c in commands if c and len(c) <= MAX_CHARS and c not in excluded]
    picked = random.Random(seed).sample(commands, min(size, len(commands)))
    out.mkdir(parents=True, exist_ok=True)
    rows = [Command(f"pool-{i}", command, SAFE, "pool", f"pool-{i}") for i, command in enumerate(picked)]
    with (out / "development.jsonl").open("w") as handle:
        for row in rows:
            handle.write(json.dumps(record_for(row)) + "\n")
    (out / "ids.json").write_text(json.dumps([[row.id, row.command] for row in rows]))
    shutil.copy("data/kev/cmdrisk/calibration.jsonl", out / "calibration.jsonl")
    print(f"{out}: {len(rows)} unlabeled commands of {len(commands)} eligible")


def confident(preds: dict[str, float], low: float, high: float) -> dict[str, bool]:
    """The teacher's yes/no answer for the commands it scores below `low` or above `high`."""
    return {i: p >= high for i, p in preds.items() if p <= low or p >= high}


def build(preds_path: Path, out: Path, cap: int, low: float, high: float, safe_per_risky: float = 0.0,
          pool: Path = POOL_DIR) -> None:
    labels = confident(json.loads(preds_path.read_text()), low, high)
    if safe_per_risky:  # the pool is mostly harmless: keep a fixed-seed sample of the not-risky answers
        safe = sorted(i for i, risky in labels.items() if not risky)
        keep = set(random.Random(0).sample(safe, min(len(safe), int(safe_per_risky * sum(labels.values())))))
        labels = {i: risky for i, risky in labels.items() if risky or i in keep}
    pool_rows = dict(json.loads((pool / "ids.json").read_text()))
    taught = [Command(i, pool_rows[i], HIGH if risky else SAFE, "distilled", i) for i, risky in sorted(labels.items())]
    train = load_split("train")
    seen = frozenset(c.command for c in train)
    parts = partitions({"train": train, "validation": load_split("validation", exclude=seen)}, 0.1)
    held = {c.command for c in parts["calibration"]} | {c.command for c in parts["development"]}
    parts["train"] = cap_families(parts["train"], cap) + [c for c in taught if c.command not in held]
    out.mkdir(parents=True, exist_ok=True)
    for name, commands in parts.items():
        with (out / f"{name}.jsonl").open("w") as handle:
            for command in commands:
                handle.write(json.dumps(record_for(command)) + "\n")
        print(f"{out / name}.jsonl: {len(commands)} commands, {sum(c.risky for c in commands)} risky")
    print(f"teacher labels kept: {len(taught)} of {len(pool_rows)} pool commands "
          f"({sum(c.risky for c in taught)} risky) at P <= {low} or P >= {high}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="action", required=True)
    pool = sub.add_parser("pool")
    pool.add_argument("--size", type=int, default=12000)
    make = sub.add_parser("build")
    make.add_argument("--preds", type=Path, required=True, help="{pool id: P(risky)} from the teacher")
    make.add_argument("--out", type=Path, default=Path("data/kev/cmdrisk-distill"))
    make.add_argument("--cap", type=int, default=16)
    make.add_argument("--low", type=float, default=0.1)
    make.add_argument("--high", type=float, default=0.9)
    make.add_argument("--safe-per-risky", type=float, default=0.0,
                      help="keep at most this many not-risky pool commands per risky one; 0 keeps all")
    args = parser.parse_args()
    if args.action == "pool":
        write_pool(args.size)
    else:
        build(args.preds, args.out, args.cap, args.low, args.high, args.safe_per_risky)


if __name__ == "__main__":
    main()
