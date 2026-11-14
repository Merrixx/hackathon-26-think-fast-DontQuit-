"""Freeze each model's measured cut-off into a tracked file, so a fresh checkout needs no harness run.

    uv run --extra ml python -m cmdgate.policy            # results/*_validation.json -> cmdgate/policy.json
    uv run --extra ml python -m cmdgate.policy --check    # do the weights on disk still match the frozen ones?

A cut-off belongs to one set of trained weights: it does not change at run time,
only when the model is retrained. `cmdgate.evaluate` measures it on the
validation split and writes `results/<model>_validation.json`, which git
ignores. This module copies what the engine needs from those files into
`cmdgate/policy.json`, which is committed. The engine reads a result file when
there is one and the frozen entry otherwise.

Each entry records the SHA-256 of the model's weight file where the model is a
file in `models/`. `--check` compares it with the file on disk; a mismatch
means the model was retrained and its cut-off must be measured again. A model
behind an endpoint, such as Kev, has no local weight file to hash: its entry
names the checkpoint instead, and keeping the two together is on you.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from .scorers import ENSEMBLES, MODELS_DIR, NGRAM_PLUS

POLICY_PATH = Path(__file__).with_name("policy.json")
RESULTS_DIR = Path("results")
SPLIT = "validation"
KEPT = ("split", "source", "members", "commands", "families", "failed", "error_budget", "automation_confidence",
        "ms_per_command")
KEPT_METRICS = ("accuracy", "automation_rate_at_5pct", "auroc", "severe_recall_at_1pct_fpr", "ece")


def weight_files(name: str, models_dir: Path = MODELS_DIR) -> list[Path]:
    """The weight files a model name loads; empty for rules, the prior, votes and endpoints."""
    if name in ENSEMBLES:
        return [path for member in ENSEMBLES[name] for path in weight_files(member, models_dir)]
    candidates = {"ngram": models_dir / "ngram.joblib", NGRAM_PLUS: models_dir / "ngram_plus.joblib",
                  "encoder": models_dir / "encoder" / "model.safetensors"}
    path = candidates.get(name, models_dir / name / "model.safetensors")
    return [path] if path.exists() else []


def fingerprint(paths: list[Path]) -> str | None:
    """SHA-256 over the files' contents, in order; None when there is no file."""
    if not paths:
        return None
    digest = hashlib.sha256()
    for path in paths:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    return digest.hexdigest()


def freeze(results_dir: Path = RESULTS_DIR, notes: dict[str, str] | None = None) -> dict[str, Any]:
    frozen: dict[str, Any] = {}
    for path in sorted(results_dir.glob(f"*_{SPLIT}.json")):
        result = json.loads(path.read_text())
        if "automation_confidence" not in result:
            continue
        name = path.name.removesuffix(f"_{SPLIT}.json")
        entry = {key: result[key] for key in KEPT if key in result}
        entry["metrics"] = {key: result["metrics"][key] for key in KEPT_METRICS if key in result.get("metrics", {})}
        entry["weights_sha256"] = fingerprint(weight_files(name))
        if notes and name in notes:
            entry["checkpoint"] = notes[name]
        frozen[name] = entry
    return frozen


def load(path: Path = POLICY_PATH) -> dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def check(path: Path = POLICY_PATH) -> list[str]:
    """Models whose weights on disk are not the ones their frozen cut-off was measured on."""
    stale = []
    for name, entry in load(path).items():
        files = weight_files(name)
        if entry.get("weights_sha256") and files and fingerprint(files) != entry["weights_sha256"]:
            stale.append(name)
    return stale


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="compare weights on disk with the frozen fingerprints")
    parser.add_argument("--checkpoint", action="append", default=[], metavar="NAME=TEXT",
                        help="what an endpoint model was when it was evaluated, e.g. kev=runs/<run>/checkpoint")
    args = parser.parse_args()
    if args.check:
        stale = check()
        print("stale: " + ", ".join(stale) if stale else "every frozen cut-off matches the weights on disk")
        sys.exit(1 if stale else 0)
    notes = dict(spec.split("=", 1) for spec in args.checkpoint)
    frozen = freeze(notes=notes)
    POLICY_PATH.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n")
    for name, entry in frozen.items():
        print(f"{name:<22}cut-off {entry['automation_confidence']:.3f}  "
              f"{'weights hashed' if entry['weights_sha256'] else entry.get('checkpoint', 'no weight file')}")


if __name__ == "__main__":
    main()
