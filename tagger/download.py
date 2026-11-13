"""Fetch (optionally) and verify the configured dataset files.

    uv run python -m tagger.download --verify   # check train and eval on disk
    uv run python -m tagger.download            # fetch train and eval from [download].base_url, then check

The holdout is never fetched or read here; `tagger.holdout` does that after freeze.
"""

from __future__ import annotations

import argparse
import urllib.request

from .config import load
from .data import check_file

PUBLIC_SPLITS = ("train", "eval")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="only check files already on disk")
    args = parser.parse_args()
    ds = load()

    failed = False
    for split in PUBLIC_SPLITS:
        path = ds.files[split]
        if not args.verify and not path.exists():
            if not ds.base_url:
                raise SystemExit(f"{path} missing and no [download].base_url set; add the file by hand")
            path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(f"{ds.base_url.rstrip('/')}/{path.name}", path)
        problems = check_file(ds, split)
        checks = [c for c, on in (("sha256", ds.sha256[split]), ("rows", ds.rows[split])) if on]
        status = "BAD" if problems else "ok "
        print(f"{status} {split:<6}{path}  (checked: {', '.join(checks) or 'existence only'})")
        for p in problems:
            print(f"      {p}")
        failed |= bool(problems)
    if failed:
        raise SystemExit("verification failed")


if __name__ == "__main__":
    main()
