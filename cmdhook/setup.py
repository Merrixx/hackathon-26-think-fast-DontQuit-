"""Check that the hook app is ready to run, and say what is missing if it is not.

    uv run --extra ml python -m cmdhook.setup            # uv installs the Python dependencies first
    uv run --extra ml python -m cmdhook.setup --no-load  # check the files only, do not load the models

It creates `cmdhook/.env` from `.env.example` if there is none, checks the
libraries and the files of every model in `CMDHOOK_MODELS`, loads each
in-process model and scores one command with it, and prints the hook entry for
Claude Code's settings. Weights are trained, not downloaded: for a missing
model it names the command that makes it. The exit code is 0 only when every
selected model is ready.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time

from . import models as registry
from .config import APP_DIR, ENV_PATH, EXAMPLE_PATH, ROOT, Config, ConfigError, load

SAMPLE = "git status"


def hook_settings() -> dict:
    """The entry that installs the hook in Claude Code's settings."""
    return {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": f'node "{APP_DIR / "hook.js"}"'}]}]}}


def check_models(config: Config, load_models: bool) -> bool:
    """Print one line per selected model; True when all are ready."""
    ready = True
    try:
        selected = registry.selected(config)
    except ValueError as exc:
        print(f"  FAIL  {exc}")
        return False
    usable = []
    for model in selected:
        problems = model.problems(config)
        if problems:
            ready = False
            print(f"  FAIL  {model.name}")
            for problem in problems:
                print(f"          {problem}")
        elif model.kind == "kev":
            print(f"  ok    {model.name:<18}checkpoint {config.kev_run}; cmdhook.start launches it on port {config.kev_port}")
        elif not model.in_process:
            print(f"  ok    {model.name:<18}endpoint {model.url} (not called here)")
        else:
            usable.append(model)
    if not load_models:
        for model in usable:
            print(f"  ok    {model.name:<18}{model.path} (not loaded)")
        return ready
    from cmdgate.scorers import risky_scores

    train = None
    for model in usable:
        try:
            started = time.perf_counter()
            if model.kind == "fitted" and train is None:
                from cmdgate.dataset import load_split
                train = load_split("train")
            scorer = registry.load(model, train)
            loaded = time.perf_counter() - started
            risky_scores(scorer, [SAMPLE])  # the first call warms the model up
            started = time.perf_counter()
            p = risky_scores(scorer, [SAMPLE])[0][0]
            ms = 1000 * (time.perf_counter() - started)
            print(f"  ok    {model.name:<18}loaded in {loaded:.1f} s, `{SAMPLE}` risk {p:.3f} in {ms:.0f} ms")
        except Exception as exc:  # any load failure is a setup finding, not a crash
            ready = False
            print(f"  FAIL  {model.name:<18}does not load: {type(exc).__name__}: {exc}")
    return ready


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-load", action="store_true", help="check the files only, do not load the models")
    args = parser.parse_args()
    os.chdir(ROOT)

    print("Settings")
    if not ENV_PATH.exists():
        shutil.copyfile(EXAMPLE_PATH, ENV_PATH)
        print(f"  created {ENV_PATH.relative_to(ROOT)} from {EXAMPLE_PATH.name}")
    try:
        config = load()
    except ConfigError as exc:
        raise SystemExit(f"  FAIL  {exc}") from None
    thresholds = ", ".join(f"{name} {config.threshold_for(name):g}" for name in config.names)
    print(f"  models     {', '.join(config.names)}")
    print(f"  thresholds {thresholds} (allow when the risk is at or below)")
    print(f"  combine    {config.combine}; pass: {config.on_pass}; fail: {config.on_fail}")
    print(f"  backend    http://{config.host}:{config.port}")

    print("Tools")
    node = shutil.which("node")
    print(f"  {'ok  ' if node else 'FAIL'}  node {'at ' + node if node else 'is not on PATH; the hook needs it'}")

    print("Models")
    ready = check_models(config, not args.no_load) and bool(node)

    spare = [name for name in registry.on_disk() if name not in config.names]
    if spare:
        print(f"Also on disk, not selected: {', '.join(spare)}")
    checkpoints = registry.kev_checkpoints()
    if checkpoints and registry.KEV not in config.names:
        print(f"Kev checkpoints on disk (set CMDHOOK_KEV_RUN and add kev to CMDHOOK_MODELS): {', '.join(checkpoints)}")

    print("\nClaude Code hook: merge this into .claude/settings.json (one project) or ~/.claude/settings.json (all)")
    print(json.dumps(hook_settings(), indent=2))
    if not ready:
        raise SystemExit("\nNot ready: fix the lines marked FAIL, or change CMDHOOK_MODELS in cmdhook/.env")
    print("\nReady. Start the backend with: uv run --extra ml python -m cmdhook.start")


if __name__ == "__main__":
    main()
