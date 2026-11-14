"""The hook backend: every configured model scores a command, and the settings turn the scores into a decision.

`POST /v1/hook {"command": "..."}` answers with

- `decision`: `allow` or `defer` when the command passes (`CMDHOOK_ON_PASS`), `ask` or `deny` when it does not (`CMDHOOK_ON_FAIL`)
- `models`: for each model its risk `p`, its `threshold`, whether it `passed`, and the time it took in `ms`
- `mean`: the mean risk over the models
- `reason`: one line, and `summary`: the same with one line per model

A model passes when its risk is at or below its threshold. `CMDHOOK_COMBINE`
says how the models add up: `all` of them must pass, `any` one of them, or
their `mean` risk against `CMDHOOK_THRESHOLD`. A model that failed to answer
does not pass.

`GET /v1/config` shows the settings in use. The server also answers everything
`cmdgate.engine` does (`/v1/check`, `/v1/systemone`, `/v1/models`), so KI-Guard
and the VS Code add-on can point at it.
"""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cmdgate.engine import BadRequest, Gate, make_handler
from cmdgate.rules import matched_rules
from cmdgate.scorers import risky_scores

from .config import Config

COMBINE_TEXT = {"all": "every model must pass", "any": "one model must pass", "mean": "the mean must pass"}


def score(scorers: dict[str, Any], command: str) -> dict[str, dict[str, Any]]:
    """Each model's risk for one command, with its time and whether it failed to answer."""
    out = {}
    for name, scorer in scorers.items():
        failed_before = getattr(scorer, "failed", 0)
        started = time.perf_counter()
        p = risky_scores(scorer, [command])[0][0]
        out[name] = {"p": float(p), "ms": 1000 * (time.perf_counter() - started),
                     "failed": getattr(scorer, "failed", 0) > failed_before}
    return out


def decide(scores: dict[str, dict[str, Any]], config: Config, command: str = "") -> dict[str, Any]:
    """The decision for one command from its scores, with the breakdown per model."""
    models = {}
    for name, row in scores.items():
        threshold = config.threshold_for(name)
        models[name] = {**row, "threshold": threshold, "passed": not row.get("failed") and row["p"] <= threshold}
    mean = sum(row["p"] for row in models.values()) / len(models)
    answered = not any(row.get("failed") for row in models.values())
    if config.combine == "mean":
        passed = answered and mean <= config.threshold
    else:
        passed = (all if config.combine == "all" else any)(row["passed"] for row in models.values())
    decision = config.on_pass if passed else config.on_fail
    over = [name for name, row in models.items() if not row["passed"]]
    if config.combine == "mean":
        reason = f"mean risk {mean:.3f} is {'at or below' if passed else 'above'} {config.threshold:.3f}"
        if not answered:
            reason = f"no answer from {', '.join(n for n, r in models.items() if r.get('failed'))}"
    elif not over:
        reason = f"all {len(models)} models at or below their threshold" if len(models) > 1 else "at or below the threshold"
    else:
        reason = f"{len(over)} of {len(models)} models above their threshold: {', '.join(over)}"
    return {"command": command, "decision": decision, "passed": passed, "combine": config.combine,
            "threshold": config.threshold, "mean": mean, "models": models, "reason": reason,
            "summary": summary(command, decision, reason, models, mean, config)}


def summary(command: str, decision: str, reason: str, models: dict[str, dict[str, Any]], mean: float,
            config: Config) -> str:
    """The decision and one line per model, as plain text."""
    shown = command if len(command) <= 80 else command[:77] + "..."
    width = max(len(name) for name in [*models, "mean"])
    lines = [f"cmdhook: {decision.upper()} - {reason} ({COMBINE_TEXT[config.combine]})", f"  command: {shown}"]
    for name, row in models.items():
        state = "no answer" if row.get("failed") else "pass" if row["passed"] else "fail"
        sign = "<=" if row["p"] <= row["threshold"] else "> "
        lines.append(f"  {name:<{width}}  risk {row['p']:.3f} {sign} {row['threshold']:.3f}  {state:<9}  {row['ms']:.0f} ms")
    if len(models) > 1:
        against = f" {'<=' if mean <= config.threshold else '> '} {config.threshold:.3f}" if config.combine == "mean" else ""
        lines.append(f"  {'mean':<{width}}  risk {mean:.3f}{against}")
    return "\n".join(lines)


def settings(config: Config) -> dict[str, Any]:
    """The settings in use, for `GET /v1/config`."""
    return {"models": {name: {"threshold": config.threshold_for(name)} for name in config.names},
            "threshold": config.threshold, "combine": config.combine,
            "on_pass": config.on_pass, "on_fail": config.on_fail}


def hook_answer(scorers: dict[str, Any], config: Config, body: dict[str, Any]) -> dict[str, Any]:
    command = body.get("command")
    if not isinstance(command, str) or not command.strip():
        raise BadRequest('expected {"command": "<shell command>"}')
    return {**decide(score(scorers, command), config, command), "rules": matched_rules(command)}


def make_hook_handler(scorers: dict[str, Any], config: Config, results_dir: Path = Path("results")
                      ) -> type[BaseHTTPRequestHandler]:
    """The engine's handler with `/v1/hook` and `/v1/config` added."""
    gate = Gate(scorers, results_dir=results_dir)
    base = make_handler(gate.scorer(gate.default), gate.default, gate)

    class Handler(base):
        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            if self.path == "/v1/config":
                self.send(HTTPStatus.OK, settings(config))
            else:
                super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/hook":
                super().do_POST()
                return
            try:
                length = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise BadRequest("the request body must be a JSON object")
                self.send(HTTPStatus.OK, hook_answer(scorers, config, body))
            except (BadRequest, json.JSONDecodeError, ValueError) as exc:
                self.send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            except Exception as exc:  # a scorer failure must not stop the server
                self.send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def make_server(scorers: dict[str, Any], config: Config) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((config.host, config.port), make_hook_handler(scorers, config))
