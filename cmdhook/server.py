"""The hook backend: every configured model scores a command, and the settings turn the scores into a decision.

`POST /v1/hook {"command": "..."}` answers with

- `decision`: `allow` or `defer` when the command passes (`CMDHOOK_ON_PASS`), `ask` or `deny` when it does not (`CMDHOOK_ON_FAIL`)
- `models`: for each model its risk `p`, its `threshold`, whether it `passed`, and the time it took in `ms`
- `mean`: the mean risk over the models
- `reason`: one line, and `summary`: the Command Gate block the plugins draw, with one row per model

A model passes when its risk is at or below its threshold. `CMDHOOK_COMBINE`
says how the models add up: `all` of them must pass, `any` one of them, or
their `mean` risk against `CMDHOOK_THRESHOLD`. A model that failed to answer
does not pass.

`POST /v1/systemone` is the decision API the Claude Code, Cursor and Codex
plugins and the VS Code add-on call. Its answer carries the same `decision`,
`reason` and `models` next to the `answers`, so every frontend can show what
each model said.

`POST /v1/check` and `GET /v1/models` are what the KI-Guard dashboard calls. They carry the same
decision and thresholds in the field names the dashboard reads; `"model": "<name>"` in a check asks
for one model's own decision. `GET /v1/config` shows the settings in use.

Every `/v1/hook` decision is appended to the file in `CMDHOOK_LOG`, which the dashboard counts.
"""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from cmdgate.engine import BadRequest, Gate, answers_for, make_handler
from cmdgate.questions import LEVELS, command_from_state, dangerous_probability
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
        risky, level_rows = risky_scores(scorer, [command])
        out[name] = {"p": float(risky[0]), "ms": 1000 * (time.perf_counter() - started),
                     "failed": getattr(scorer, "failed", 0) > failed_before}
        if level_rows is not None:
            out[name]["levels"] = [float(x) for x in level_rows[0]]
    return out


def mean_levels(scores: dict[str, dict[str, Any]]) -> list[float] | None:
    """The mean probability per risk level over the models that have levels and answered; None when none does."""
    rows = [row["levels"] for row in scores.values() if row.get("levels") and not row.get("failed")]
    if not rows:
        return None
    mean = [sum(column) / len(rows) for column in zip(*rows)]
    total = sum(mean) or 1.0
    return [x / total for x in mean]


def systemone_answer(scorers: dict[str, Any], config: Config, body: dict[str, Any]) -> dict[str, Any]:
    """The decision API answer for the plugins: the answers, plus the decision and every model's own result.

    The yes/no approval question is answered with the mean risk over the models, the level and
    "dangerous" questions from the models that have risk levels. `decision`, `reason` and `models` are the
    same fields `/v1/hook` returns, so a frontend can show what each model said.
    """
    questions = body.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise BadRequest("questions must be a non-empty object")
    try:
        command = command_from_state(body.get("state"))
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    scores = score(scorers, command)
    result = decide(scores, config, command)
    return {"answers": answers_for(questions, result["mean"], mean_levels(scores)),
            **{key: result[key] for key in ("decision", "passed", "combine", "threshold", "mean", "models", "reason")}}


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


# The block the Claude Code, Cursor and Codex plugins draw (cmdgate-claude-plugin/hooks/gate.ts), so the hook
# looks the same. Plain text with coloured Unicode markers: Claude Code shows hook text as is, without ANSI colours.
RISK_MARKERS = ["🟢", "🔵", "🟡", "🟠", "🔴"]


def block_row(label: str, marker: str, value: str, p: float | None = None) -> str:
    percent = "" if p is None else f"{round(p * 100)}%".rjust(4)
    return f"  {label:<11}{marker} {value:<10}{percent}".rstrip()


def summary(command: str, decision: str, reason: str, models: dict[str, dict[str, Any]], mean: float,
            config: Config) -> str:
    """The Command Gate block: the decision, risk level, P(dangerous) and one row per model."""
    unsafe = decision in ("ask", "deny")
    levels = mean_levels(models)
    lines = ["Command Gate", block_row("Verdict", "🔴" if unsafe else "🟢", "UNSAFE" if unsafe else "safe")]
    if levels:
        index = max(range(len(levels)), key=levels.__getitem__)
        dangerous = dangerous_probability(levels)
        lines.append(block_row("Risk", RISK_MARKERS[index], LEVELS[index], levels[index]))
        lines.append(block_row("Dangerous", "🔴" if dangerous >= config.threshold else "🟢",
                               "yes" if dangerous >= config.threshold else "no", dangerous))
    else:
        lines += [block_row("Risk", "⚪", "n/a"), block_row("Dangerous", "⚪", "n/a")]
    lines.append(block_row("Root", "⚪", "n/a"))
    lines.append(f"  {'Models':<11}{decision.upper()} · {reason} ({COMBINE_TEXT[config.combine]})")
    for name, row in models.items():
        if row.get("failed"):
            lines.append(block_row(name[:10], "⚪", "no answer"))
        else:
            lines.append(block_row(name[:10], "🟢" if row["passed"] else "🔴", "pass" if row["passed"] else "FAIL", row["p"]))
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


ALL_MODELS = "all models"


def dashboard_policy(threshold: float) -> dict[str, Any]:
    """A threshold from the settings, in the fields the dashboard draws as cut-offs."""
    return {"evaluated": True, "split": "settings", "confidence": 1.0 - threshold,
            "allow_at_or_below": threshold, "risky_at_or_above": threshold}


def check_answer(scorers: dict[str, Any], config: Config, body: dict[str, Any]) -> dict[str, Any]:
    """The hook's decision in the shape the KI-Guard dashboard reads; `model` picks one model's own decision."""
    command = body.get("command")
    if not isinstance(command, str) or not command.strip():
        raise BadRequest('expected {"command": "<shell command>"}')
    scores = score(scorers, command)
    every = decide(scores, config, command)
    name = body.get("model")
    one = name in scores
    result = decide({name: scores[name]}, config, command) if one else every
    verdict = lambda passed: "allow" if passed else "block" if config.on_fail == "deny" else "ask"  # noqa: E731
    models = {member: {**row, "verdict": verdict(row["passed"]), "bucket": "safe" if row["passed"] else "likely"}
              for member, row in every["models"].items()}
    return {"command": command, "model": name if one else ALL_MODELS,
            "p": scores[name]["p"] if one else every["mean"],
            "verdict": verdict(result["passed"]), "bucket": "safe" if result["passed"] else "likely",
            "reason": result["reason"], "decision": result["decision"], "combine": config.combine,
            "policy": dashboard_policy(config.threshold_for(name) if one else config.threshold),
            "models": models, "rules": matched_rules(command)}


def models_listing(scorers: dict[str, Any], config: Config) -> dict[str, Any]:
    """The served models with their thresholds, for the dashboard's model list."""
    return {"default": f"{COMBINE_TEXT[config.combine]} ({len(scorers)} models)",
            "data": [{"name": name, "members": [], **dashboard_policy(config.threshold_for(name))} for name in scorers]}


def log_decision(path: Path, answer: dict[str, Any], agent: str) -> None:
    """Append one hook decision to the log the dashboard counts. A log that cannot be written is not an error."""
    level = {"allow": "allow", "defer": "allow", "ask": "ask", "deny": "block"}[answer["decision"]]
    entry = {"t": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), "level": level,
             "bucket": {"allow": "safe", "ask": "likely", "block": "blocked"}[level], "agent": agent, "tool": "Bash",
             "text": answer["command"][:300], "p": answer["mean"], "model": "cmdhook",
             "hits": [f"model:{name}" for name, row in answer["models"].items() if not row["passed"]]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as log:
            log.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def make_hook_handler(scorers: dict[str, Any], config: Config, results_dir: Path = Path("results"),
                      log_path: Path | None = None) -> type[BaseHTTPRequestHandler]:
    """The engine's handler with `/v1/hook` and `/v1/config` added."""
    gate = Gate(scorers, results_dir=results_dir)
    base = make_handler(gate.scorer(gate.default), gate.default, gate)

    class Handler(base):
        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            if self.path == "/v1/config":
                self.send(HTTPStatus.OK, settings(config))
            elif self.path == "/v1/models":
                self.send(HTTPStatus.OK, models_listing(scorers, config))
            else:
                super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            answer = {"/v1/hook": hook_answer, "/v1/systemone": systemone_answer, "/v1/check": check_answer}.get(self.path)
            if answer is None:
                super().do_POST()
                return
            try:
                length = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise BadRequest("the request body must be a JSON object")
                result = answer(scorers, config, body)
                if self.path == "/v1/hook" and log_path is not None:
                    log_decision(log_path, result, str(body.get("agent") or "claude-code"))
                self.send(HTTPStatus.OK, result)
            except (BadRequest, json.JSONDecodeError, ValueError) as exc:
                self.send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            except Exception as exc:  # a scorer failure must not stop the server
                self.send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def make_server(scorers: dict[str, Any], config: Config) -> ThreadingHTTPServer:
    log_path = Path(config.log).expanduser() if config.log else None
    return ThreadingHTTPServer((config.host, config.port), make_hook_handler(scorers, config, log_path=log_path))
