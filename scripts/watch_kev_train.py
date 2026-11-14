#!/usr/bin/env python3
"""Live progress bar for the Kev fine-tune log (does not touch the train job).

    python3 scripts/watch_kev_train.py                 # live bar (Ctrl-C to stop)
    python3 scripts/watch_kev_train.py --once          # single status line
    python3 scripts/watch_kev_train.py --log data/kev_train.log --interval 2

Parses lines like: ep0 step 330/5478 loss 0.26 ... 0.48s/rec
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

STEP_RE = re.compile(
    r"ep(?P<ep>\d+)\s+step\s+(?P<step>\d+)/(?P<total>\d+)\s+"
    r"loss\s+(?P<loss>[\d.]+)"
    r".*?(?P<srec>[\d.]+)s/rec"
)


def parse_latest(text: str) -> dict | None:
    match = None
    for match in STEP_RE.finditer(text):
        pass
    if match is None:
        return None
    d = match.groupdict()
    return {
        "ep": int(d["ep"]),
        "step": int(d["step"]),
        "total": int(d["total"]),
        "loss": float(d["loss"]),
        "s_rec": float(d["srec"]),
    }


def fmt_eta(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN
        return "?"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def bar(frac: float, width: int = 36) -> str:
    frac = max(0.0, min(1.0, frac))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def render(info: dict | None, log_path: Path, waiting: bool, *, once: bool = False) -> str:
    prefix = "" if once else "\r"
    if info is None:
        msg = "waiting for first step line…" if waiting else "no step lines in log yet"
        return f"{prefix}{msg}  ({log_path})"
    frac = info["step"] / info["total"] if info["total"] else 0.0
    rem_recs = max(0, info["total"] - info["step"]) * 8
    eta = rem_recs * info["s_rec"]
    return (
        f"{prefix}kev ep{info['ep']} [{bar(frac)}] {100 * frac:5.1f}%  "
        f"step {info['step']}/{info['total']}  "
        f"loss {info['loss']:.3f}  "
        f"{info['s_rec']:.3f}s/rec  "
        f"ETA {fmt_eta(eta)}"
    )


def read_info(log_path: Path) -> dict | None:
    if not log_path.exists():
        return None
    text = log_path.read_bytes()[-65536:].decode("utf-8", errors="replace")
    return parse_latest(text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--log",
        type=Path,
        default=Path("data/kev_train.log"),
        help="path to the tee'd kev.train log",
    )
    parser.add_argument("--interval", type=float, default=2.0, help="poll seconds")
    parser.add_argument(
        "--once",
        action="store_true",
        help="print one status line and exit (no live loop)",
    )
    args = parser.parse_args()

    if args.once:
        info = read_info(args.log)
        print(render(info, args.log, waiting=not args.log.exists(), once=True))
        return

    try:
        while True:
            if not args.log.exists():
                sys.stdout.write(render(None, args.log, waiting=True))
                sys.stdout.flush()
                time.sleep(args.interval)
                continue
            info = read_info(args.log)
            line = render(info, args.log, waiting=False)
            sys.stdout.write(line.ljust(max(len(line), 100)))
            sys.stdout.flush()
            if info and info["step"] >= info["total"]:
                sys.stdout.write("\n done.\n")
                return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
