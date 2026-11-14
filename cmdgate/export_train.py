"""Write train-only JSONL for Kev fine-tune and for bridge-prompt LoRA.

    uv run --extra ml python -m cmdgate.export_train

Uses the CLI Command Risk training split only. Validation and test are never
written here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from thinkfast.bridge import LETTERS, build_messages, options_for
from thinkfast.suite import write_jsonl

from .dataset import load_split
from .questions import RISKY_ID, RISKY_QUESTION, request_for


def kev_item(command: str, risky: bool) -> dict:
    # Kev train: API request shape with `label` on every question (true/false for noul).
    return {
        "state": {"command": command},
        "questions": {RISKY_ID: {**RISKY_QUESTION, "label": bool(risky)}},
    }


def bridge_record(command: str, risky: bool) -> dict:
    request = request_for(command)
    question = request["questions"][RISKY_ID]
    options = options_for(question)
    messages = build_messages(request["state"], question, options)
    letter = LETTERS[0 if risky else 1]  # A = true, B = false
    return {
        "messages": messages,
        "completion": letter,
        "label": risky,
        "command": command,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    train = load_split("train")
    kev_path = args.out / "cmdrisk_kev_train.jsonl"
    qwen_path = args.out / "cmdrisk_qwen_train.jsonl"
    write_jsonl([kev_item(c.command, c.risky) for c in train], kev_path)
    with qwen_path.open("w", encoding="utf-8") as f:
        for c in train:
            f.write(json.dumps(bridge_record(c.command, c.risky), ensure_ascii=False) + "\n")
    risky = sum(c.risky for c in train)
    print(f"{kev_path}: {len(train)} commands, {risky} risky")
    print(f"{qwen_path}: {len(train)} bridge-prompt rows, {risky} risky (A=true B=false)")


if __name__ == "__main__":
    main()
