"""Check Kev fine-tune partitions against the training contract (docs/cmdrisk-train-contract.md).

    uv run --extra ml python -m cmdgate.kev_check data/kev/cmdrisk-cap16 [more folders ...]

For every line of train.jsonl, calibration.jsonl and development.jsonl:
the record is `{"state": {"command": <non-empty string>}, "questions": {"risky": ...}}`
and nothing else, and the `risky` question is the wording of `cmdgate.questions`
plus a boolean `label`.

Across files: no command of the validation or test split is in train.jsonl or
calibration.jsonl, no train command is in calibration.jsonl, and
development.jsonl is the validation split with its labels. Test commands are
read only to be compared. Exits with status 1 if any folder fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .dataset import Command, load_all
from .questions import RISKY_ID, RISKY_QUESTION

PARTS = ("train", "calibration", "development")


def record_problem(record: object) -> str | None:
    """Why one record breaks the contract, or None when it follows it."""
    if not isinstance(record, dict) or set(record) != {"state", "questions"}:
        return "top-level keys are not exactly state and questions"
    state = record["state"]
    if not isinstance(state, dict) or set(state) != {"command"}:
        return "state is not {\"command\": ...}"
    if not isinstance(state["command"], str) or not state["command"].strip():
        return "command is empty or not a string"
    questions = record["questions"]
    if not isinstance(questions, dict) or set(questions) != {RISKY_ID}:
        return "questions are not exactly the risky question"
    question = dict(questions[RISKY_ID])
    if not isinstance(question.pop("label", None), bool):
        return "label is missing or not a boolean"
    if question != RISKY_QUESTION:
        return "question type, instructions or criteria differ from cmdgate.questions"
    return None


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def check_folder(folder: Path, splits: dict[str, list[Command]]) -> list[str]:
    """Every way this folder breaks the contract; empty when it follows it."""
    problems: list[str] = []
    records: dict[str, list[dict]] = {}
    for part in PARTS:
        path = folder / f"{part}.jsonl"
        if not path.exists():
            problems.append(f"{part}.jsonl is missing")
            continue
        records[part] = read(path)
        bad = [(n, why) for n, record in enumerate(records[part], 1) if (why := record_problem(record))]
        if bad:
            problems.append(f"{part}.jsonl: {len(bad)} records break the schema, first at line {bad[0][0]}: {bad[0][1]}")
    if problems:
        return problems

    def commands(part: str) -> set[str]:
        return {r["state"]["command"] for r in records[part]}

    held_out = {c.command for c in splits["validation"]} | {c.command for c in splits["test"]}
    for part in ("train", "calibration"):
        leaked = len(commands(part) & held_out)
        if leaked:
            problems.append(f"{part}.jsonl holds {leaked} commands of the validation or test split")
    shared = len(commands("train") & commands("calibration"))
    if shared:
        problems.append(f"{shared} commands are in both train.jsonl and calibration.jsonl")
    expected = [(c.command, c.risky) for c in splits["validation"]]
    found = [(r["state"]["command"], r["questions"][RISKY_ID]["label"]) for r in records["development"]]
    if found != expected:
        problems.append("development.jsonl is not the validation split with its labels, in order")
    return problems


def main() -> None:
    folders = [Path(arg) for arg in sys.argv[1:]]
    if not folders:
        raise SystemExit(__doc__)
    splits = load_all()
    failed = False
    for folder in folders:
        problems = check_folder(folder, splits)
        failed = failed or bool(problems)
        sizes = ", ".join(f"{part} {len(read(folder / f'{part}.jsonl'))}" for part in PARTS
                          if (folder / f"{part}.jsonl").exists())
        print(f"{folder}: {'FAIL' if problems else 'ok'} ({sizes})")
        for problem in problems:
            print(f"  {problem}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
