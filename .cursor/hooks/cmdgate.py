#!/usr/bin/env python3
"""Command Gate for Cursor: beforeShellExecution → local decision API.

Mirrors cmdgate-plugin (Claude Code) wire format in hooks/gate.ts:
same four questions, same notice line, same default endpoint.
Fail-open: API down/timeout never bricks the agent (returns allow).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LEVELS = ("safe", "low", "moderate", "high", "critical")

# Same IDs/wording as cmdgate-plugin/hooks/gate.ts and vscode-extension/src/core.js
QUESTIONS: dict[str, Any] = {
    "dangerous": {
        "type": "noul",
        "instructions": (
            "Could this shell command cause serious damage, data loss or a "
            "security problem if it runs as written?"
        ),
    },
    "requires_root": {
        "type": "noul",
        "instructions": (
            "Does this shell command need root or administrator privileges to run?"
        ),
    },
    "risk_level": {
        "type": "score",
        "instructions": "How much damage could this shell command do if it runs as written?",
        "criteria": [
            "safe: read-only or routine, no plausible path to data loss, privilege change or disruption",
            "low: a local change that is quick to undo",
            "moderate: repairable without backups, with some effort",
            "high: needs backups to recover, exposes secrets to others, runs unverified code, or takes the machine down",
            "critical: destroys a disk, filesystem, system directory or home folder, or gives away root-level control",
        ],
    },
    "unsafe": {
        "type": "noul",
        "instructions": (
            "Should a person approve this shell command before an AI coding agent runs it?"
        ),
    },
}


def env_str(name: str, default: str) -> str:
    v = os.environ.get(name)
    return default if v is None or v == "" else v


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def build_request(command: str, model: str) -> dict[str, Any]:
    return {"model": model, "state": {"command": command}, "questions": QUESTIONS}


def clamp01(x: Any) -> float | None:
    try:
        n = float(x)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return min(1.0, max(0.0, n))


def read_yes_no(answer: Any, threshold: float) -> dict[str, Any] | None:
    if not isinstance(answer, dict):
        return None
    p = clamp01(answer.get("noul"))
    if p is None and isinstance(answer.get("probabilities"), dict):
        p = clamp01(answer["probabilities"].get("true"))
    if p is None:
        return None
    return {"p": p, "yes": p >= threshold}


def read_level(answer: Any) -> dict[str, Any] | None:
    if not isinstance(answer, dict) or not isinstance(answer.get("probabilities"), dict):
        return None
    probs = answer["probabilities"]
    raw = [max(0.0, float(probs.get(str(i), 0) or 0)) for i in range(len(LEVELS))]
    total = sum(raw)
    if total <= 0:
        return None
    probabilities = [v / total for v in raw]
    index = probabilities.index(max(probabilities))
    return {
        "index": index,
        "level": LEVELS[index],
        "p": probabilities[index],
        "probabilities": probabilities,
    }


def parse_answers(body: Any, threshold: float) -> dict[str, Any]:
    answers = body.get("answers") if isinstance(body, dict) else None
    if not isinstance(answers, dict):
        answers = {}
    return {
        "dangerous": read_yes_no(answers.get("dangerous"), threshold),
        "root": read_yes_no(answers.get("requires_root"), threshold),
        "risk": read_level(answers.get("risk_level")),
        "verdict": read_yes_no(answers.get("unsafe"), threshold),
    }


def pct(p: float) -> str:
    return f"{round(p * 100)}%"


def yes_no(v: dict[str, Any] | None) -> str:
    if not v:
        return "n/a"
    return f"{'yes' if v['yes'] else 'no'} {pct(v['p'])}"


def notice_line(o: dict[str, Any]) -> str:
    verdict = "no verdict" if not o["verdict"] else ("UNSAFE" if o["verdict"]["yes"] else "safe")
    risk = f"{o['risk']['level']} {pct(o['risk']['p'])}" if o["risk"] else "n/a"
    return (
        f"Command Gate: {verdict} · risk {risk} · "
        f"dangerous {yes_no(o['dangerous'])} · root {yes_no(o['root'])}"
    )


def offline_line(endpoint: str, reason: str) -> str:
    return f"Command Gate: no second opinion from {endpoint} ({reason})"


def is_flagged(o: dict[str, Any]) -> bool:
    """Match vscode-extension severity warning/error: ask the person."""
    high = o["risk"] is not None and o["risk"]["index"] >= LEVELS.index("high")
    if o["verdict"] and o["verdict"]["yes"]:
        return True
    if o["dangerous"] and o["dangerous"]["yes"]:
        return True
    if high:
        return True
    if o["root"] and o["root"]["yes"]:
        return True
    return False


def ask_app(endpoint: str, payload: dict[str, Any], timeout_s: float) -> Any:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=data,
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read().decode("utf-8")
        if resp.status < 200 or resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")
        return json.loads(raw)


def forward_to_ides(event: dict[str, Any], timeout_s: float = 1.0) -> None:
    """Optional: same ~/.cmdgate/windows protocol as Claude plugin / VS Code add-on."""
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if not home:
        return
    directory = Path(home) / ".cmdgate" / "windows"
    if not directory.is_dir():
        return
    body = json.dumps(event).encode("utf-8")
    for path in directory.glob("*.json"):
        try:
            win = json.loads(path.read_text(encoding="utf-8"))
            port = win.get("port")
            token = win.get("token")
            if not port or not token:
                continue
            req = urllib.request.Request(
                f"http://127.0.0.1:{int(port)}/event",
                data=body,
                headers={
                    "content-type": "application/json",
                    "x-cmdgate-token": str(token),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                resp.read()
        except Exception:
            continue


def extract_command(event: dict[str, Any]) -> str | None:
    for key in ("command", "cmd"):
        val = event.get(key)
        if isinstance(val, str) and val.strip():
            return val
    tool_input = event.get("tool_input") or event.get("input") or {}
    if isinstance(tool_input, dict):
        val = tool_input.get("command")
        if isinstance(val, str) and val.strip():
            return val
    return None


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def main() -> int:
    endpoint = env_str("CMDGATE_ENDPOINT", "http://127.0.0.1:8009/v1/systemone")
    model = env_str("CMDGATE_MODEL", "local")
    timeout_ms = env_float("CMDGATE_TIMEOUT_MS", 3000.0)
    threshold = env_float("CMDGATE_THRESHOLD", 0.5)
    forward = env_bool("CMDGATE_FORWARD_TO_IDE", True)
    # advise  = always allow (Claude-like: never change the decision; messages only)
    # ask_on_flag = ask when unsafe/dangerous/high+/root (default for Cursor UX)
    # always_ask = ask on every shell command, with the gate line
    mode = env_str("CMDGATE_MODE", "ask_on_flag").strip().lower()

    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        emit({"permission": "allow"})
        return 0

    command = extract_command(event if isinstance(event, dict) else {})
    if not command:
        emit({"permission": "allow"})
        return 0

    body: Any = None
    try:
        body = ask_app(endpoint, build_request(command, model), max(0.1, timeout_ms / 1000.0))
        opinion = parse_answers(body, threshold)
        line = notice_line(opinion)
        flagged = is_flagged(opinion)
    except Exception as err:
        line = offline_line(endpoint, str(err) or type(err).__name__)
        flagged = False
        opinion = None

    if forward:
        try:
            forward_to_ides(
                {
                    "source": "Cursor",
                    "tool": "Shell",
                    "command": command,
                    "description": "",
                    "result": body,
                }
            )
        except Exception:
            pass

    if mode == "advise":
        permission = "allow"
    elif mode == "always_ask":
        permission = "ask"
    else:  # ask_on_flag
        permission = "ask" if flagged else "allow"

    # Offline / parse failure: fail open (allow), still tell the agent.
    if opinion is None and mode != "always_ask":
        permission = "allow"

    out: dict[str, Any] = {
        "permission": permission,
        "agent_message": line,
    }
    if permission == "ask":
        out["user_message"] = line
    emit(out)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # Absolute fail-open for the Cursor hook contract.
        try:
            emit({"permission": "allow"})
        except Exception:
            pass
        raise SystemExit(0)
