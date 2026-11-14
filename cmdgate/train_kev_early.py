"""Chunked Kev fine-tune with holdout early stopping (wrapper around kev.train).

`kev.train` has no patience API. This script:

1. Carves ``--val-frac`` from the **train** JSONL only (never published val/test).
2. Trains in ``--chunk-steps`` optimizer-step chunks via ``python -m kev.train --max_steps``.
3. Scores the holdout with ``python -m kev.benchmark`` after each chunk.
4. Stops when the holdout metric fails to improve for ``--patience`` chunks.
5. Copies the best chunk checkpoint to ``--out``.

    # from a Kev checkout (has the package + GPU deps):
    python /path/to/cmdgate/train_kev_early.py \\
      --data /path/to/data/cmdrisk_kev_train.jsonl \\
      --out runs/cmdrisk-0.8b-es \\
      --init-from jaredpalmer/kev-0.8b \\
      --device cuda --chunk-steps 400 --patience 3 --val-frac 0.05

Requires ``python -m kev.train`` / ``kev.benchmark`` on PATH (run inside the Kev
venv or with ``--kev-python`` pointing at it).
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> None:
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def split_train(data: Path, work: Path, val_frac: float, seed: int) -> tuple[Path, Path, int, int]:
    rows = load_jsonl(data)
    rng = random.Random(seed)
    order = list(range(len(rows)))
    rng.shuffle(order)
    n_val = max(1, int(round(len(order) * val_frac)))
    if n_val >= len(order):
        raise SystemExit("--val-frac leaves no training rows")
    val_rows = [rows[i] for i in order[:n_val]]
    train_rows = [rows[i] for i in order[n_val:]]
    train_path = work / "train_fit.jsonl"
    holdout_path = work / "holdout.jsonl"
    write_jsonl(train_rows, train_path)
    write_jsonl(val_rows, holdout_path)
    meta = {
        "source_data": str(data),
        "seed": seed,
        "val_frac": val_frac,
        "n_train": len(train_rows),
        "n_holdout": len(val_rows),
        "note": "Holdout carved from train JSONL only; not the published validation/test suites.",
    }
    (work / "split.json").write_text(json.dumps(meta, indent=2) + "\n")
    return train_path, holdout_path, len(train_rows), len(val_rows)


def metric_from_report(report: dict, metric: str) -> float:
    clean = report.get("clean") or {}
    if metric == "objective":
        # higher is better (negative macro NLL)
        return float(report["objective"])
    if metric == "acc":
        return float(clean.get("acc", clean.get("accuracy", 0.0)))
    if metric == "nll":
        # lower is better → return negated for "higher is better" comparisons
        if "nll" in clean:
            return -float(clean["nll"])
        # fall back to objective
        return float(report["objective"])
    raise SystemExit(f"unknown metric: {metric}")


def copy_checkpoint(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True, help="labelled Kev JSONL (train split only)")
    parser.add_argument("--out", type=Path, required=True, help="final best checkpoint directory")
    parser.add_argument("--init-from", default="jaredpalmer/kev-0.8b")
    parser.add_argument("--base", default="Qwen/Qwen3.5-0.8B-Base")
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--accum", type=int, default=8)
    parser.add_argument("--device", default="cuda", help="cuda on DGX; mps on Mac")
    parser.add_argument("--val-frac", type=float, default=0.05)
    parser.add_argument("--chunk-steps", type=int, default=400, help="optimizer steps per early-stop chunk")
    parser.add_argument("--patience", type=int, default=3, help="chunks without holdout improve before stop")
    parser.add_argument("--max-chunks", type=int, default=20, help="hard cap on chunks (safety)")
    parser.add_argument("--metric", choices=("objective", "acc", "nll"), default="objective",
                        help="holdout metric from kev.benchmark (higher-is-better after transform)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--kev-python", default=sys.executable,
                        help="Python that can import kev (DGX: Kev venv python)")
    parser.add_argument("--work", type=Path, default=None, help="scratch dir (default: <out>.earlystop-work)")
    args = parser.parse_args()

    if not (0.0 < args.val_frac < 0.5):
        raise SystemExit("--val-frac must be in (0, 0.5)")
    if args.chunk_steps < 1 or args.patience < 1 or args.max_chunks < 1:
        raise SystemExit("--chunk-steps, --patience, --max-chunks must be >= 1")

    work = args.work or Path(str(args.out) + ".earlystop-work")
    work.mkdir(parents=True, exist_ok=True)
    chunks_dir = work / "chunks"
    best_dir = work / "best"
    history_path = work / "history.jsonl"
    chunks_dir.mkdir(parents=True, exist_ok=True)

    train_path, holdout_path, n_train, n_hold = split_train(args.data, work, args.val_frac, args.seed)
    log(f"split: train_fit={n_train} holdout={n_hold} chunk_steps={args.chunk_steps} patience={args.patience}")

    py = args.kev_python
    best_score: float | None = None
    bad = 0
    history: list[dict] = []
    prev_ckpt = args.init_from

    for chunk in range(1, args.max_chunks + 1):
        chunk_out = chunks_dir / f"chunk-{chunk:02d}"
        if chunk_out.exists():
            shutil.rmtree(chunk_out)
        log(f"=== chunk {chunk}/{args.max_chunks} init_from={prev_ckpt} ===")
        run(
            [
                py, "-m", "kev.train",
                "--data", str(train_path),
                "--base", args.base,
                "--init_from", str(prev_ckpt),
                "--lr", str(args.lr),
                "--epochs", "1",
                "--batch", str(args.batch),
                "--accum", str(args.accum),
                "--max_steps", str(args.chunk_steps),
                "--device", args.device,
                "--out", str(chunk_out),
            ]
        )

        bench_out = work / f"bench-chunk-{chunk:02d}"
        if bench_out.exists():
            shutil.rmtree(bench_out)
        run(
            [
                py, "-m", "kev.benchmark",
                "--run", str(chunk_out),
                "--data", str(holdout_path),
                "--device", args.device,
                "--out", str(bench_out),
            ]
        )
        report = json.loads((bench_out / "report.json").read_text())
        score = metric_from_report(report, args.metric)
        clean = report.get("clean") or {}
        row = {
            "chunk": chunk,
            "metric": args.metric,
            "score": score,
            "objective": report.get("objective"),
            "clean_acc": clean.get("acc"),
            "clean_nll": clean.get("nll"),
            "checkpoint": str(chunk_out),
        }
        history.append(row)
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        log(f"chunk {chunk}: {args.metric}={score:.6f} clean.acc={clean.get('acc')} objective={report.get('objective')}")

        improved = best_score is None or score > best_score + 1e-6
        if improved:
            best_score = score
            bad = 0
            copy_checkpoint(chunk_out, best_dir)
            (best_dir / "early_stop_meta.json").write_text(
                json.dumps({"best_chunk": chunk, "best_score": best_score, "metric": args.metric}, indent=2) + "\n"
            )
            log(f"new best → {best_dir}")
        else:
            bad += 1
            log(f"no improve ({bad}/{args.patience})")
            if bad >= args.patience:
                log("early stop")
                break

        prev_ckpt = str(chunk_out)

    if not best_dir.exists():
        raise SystemExit("no best checkpoint produced")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    copy_checkpoint(best_dir, args.out)
    summary = {
        "out": str(args.out),
        "best_score": best_score,
        "metric": args.metric,
        "chunks_run": len(history),
        "history": history,
        "work": str(work),
        "train_fit": str(train_path),
        "holdout": str(holdout_path),
    }
    (args.out / "early_stop_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (work / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    log(f"done: best {args.metric}={best_score:.6f} → {args.out}")


if __name__ == "__main__":
    main()
