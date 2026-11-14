"""Start the hook backend with the models and thresholds from the settings.

    uv run --extra ml python -m cmdhook.start
    CMDHOOK_MODELS=ngram,encoder CMDHOOK_THRESHOLD=0.2 uv run --extra ml python -m cmdhook.start

It loads every model in `CMDHOOK_MODELS` into this process. If `kev` is one of
them, it first launches the Kev server from the checkout in `CMDHOOK_KEV_DIR`
with the checkpoint in `CMDHOOK_KEV_RUN`, waits for it to answer, and stops it
again on exit. Settings come from `cmdhook/.env`; a variable in the environment
wins. Run `cmdhook.setup` first to see what is missing.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import models as registry
from .config import ROOT, Config, ConfigError, load
from .server import COMBINE_TEXT, make_server, score


def start_kev(config: Config) -> subprocess.Popen:
    """Launch the Kev server and wait until it answers."""
    command = ["uv", "run", "--extra", "serve", "python", "-m", "kev.serve",
               "--run", str(Path(config.kev_run).resolve()), "--port", str(config.kev_port)]
    print(f"starting Kev on port {config.kev_port} from {config.kev_dir} ...", flush=True)
    # Kev has its own environment; this process's virtual environment must not leak into it.
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    process = subprocess.Popen(command, cwd=config.kev_dir, env=env)
    deadline = time.monotonic() + config.kev_start_seconds
    url = f"http://127.0.0.1:{config.kev_port}/v1/models"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"Kev stopped while starting, exit code {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2):
                return process
        except (urllib.error.URLError, OSError):
            time.sleep(1)
    process.terminate()
    raise SystemExit(f"Kev did not answer on {url} within {config.kev_start_seconds:.0f} s (CMDHOOK_KEV_START_SECONDS)")


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    os.chdir(ROOT)
    try:
        config = load()
        selected = registry.selected(config)
    except (ConfigError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
    if not selected:
        raise SystemExit("CMDHOOK_MODELS is empty: name at least one model")
    problems = [f"{model.name}: {problem}" for model in selected for problem in model.problems(config)]
    if problems:
        raise SystemExit("not ready, see `python -m cmdhook.setup`:\n  " + "\n  ".join(problems))

    # A plain kill must still reach the `finally` below, or the Kev server would be left running.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    kev = start_kev(config) if any(model.kind == "kev" for model in selected) else None
    try:
        print("loading models ...", flush=True)
        try:
            scorers = registry.load_all(selected)
        except Exception as exc:  # name the cause instead of a traceback; `cmdhook.setup` says which model
            raise SystemExit(f"a model did not load ({type(exc).__name__}: {exc}); see `python -m cmdhook.setup`") from None
        score(scorers, "git status")  # the first call to a model is slow; make it here, not on a real command
        server = make_server(scorers, config)
        print(f"hook backend on http://{config.host}:{config.port}/v1/hook")
        print(f"  {COMBINE_TEXT[config.combine]}; pass: {config.on_pass}; fail: {config.on_fail}")
        for name in scorers:
            print(f"  {name:<18}allow at or below {config.threshold_for(name):.3f}")
        if config.combine == "mean" and config.thresholds:
            print("  note: with CMDHOOK_COMBINE=mean only CMDHOOK_THRESHOLD decides; the per-model thresholds are shown, not used")
        sys.stdout.flush()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nstopping")
    finally:
        if kev is not None:
            kev.terminate()
            kev.wait(timeout=10)


if __name__ == "__main__":
    main()
