"""Serve the gate's models: the decision API for the evaluation runner, and a check endpoint for the guard.

    uv run --extra ml python -m cmdgate.engine                               # every model found on disk
    uv run --extra ml python -m cmdgate.engine --scorer ngram                # one model
    uv run --extra ml python -m cmdgate.engine --scorers ngram,encoder --endpoint kev=http://127.0.0.1:8011/v1/systemone
    uv run --extra ml python -m cmdgate.engine --scorers rules,ngram-plus,enc-ens-qwen4 --default enc-ens-qwen4

With no `--scorers` it loads the light models found on disk (rules, both n-gram
models, the encoder). The large encoders under `models/enc-*` and their
ensemble take 2 to 3 GB of memory each, so they are loaded only when named.

`POST /v1/systemone` is the decision API. The default model answers a yes/no
question about approval with the probability that the command is risky. When
it has risk levels it also answers the five-level score question with the
levels and "is it dangerous" with P(high or critical). "Does it need root" gets
no answer: no model here knows. Any other question gets a uniform answer, which
the evaluation runner scores as an unconfident guess.

`POST /v1/check {"command": "...", "model": "ngram"}` is what the KI-Guard hook
and dashboard call. It returns the probability and verdict of the model asked,
and each model's own probability and verdict (`"all": true` asks every loaded model). A model may allow a command on its own only inside the cut-offs
measured for it by `cmdgate.evaluate` on the validation split, read from
`results/` or, on a fresh checkout, from the copy frozen into
`cmdgate/policy.json` by `cmdgate.policy`; a model with neither asks a person
every time. `GET /v1/models` lists the models with
their cut-offs and scores.
"""

from __future__ import annotations

import argparse
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from thinkfast.metrics import question_options, uniform

from .encoder import MODEL_DIR as ENCODER_DIR
from .ngram import MODEL_PATH as NGRAM_PATH
from .policy import POLICY_PATH
from .questions import command_from_state, dangerous_probability, question_kind, risky_probability
from .rules import matched_rules
from .scorers import (ENSEMBLES, MODELS_DIR, NGRAM_PLUS, VOTE, VoteScorer, in_process_names, load_scorers,
                      parse_endpoints, risky_scores)

RESULTS_DIR = Path("results")
POLICY_SPLIT = "validation"


class BadRequest(ValueError):
    """The request is not valid for this engine."""


def decide(scorer: Any, body: dict[str, Any]) -> dict[str, Any]:
    """Answer every question in one decision API request."""
    questions = body.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise BadRequest("questions must be a non-empty object")
    try:
        command = command_from_state(body.get("state"))
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    scores, level_rows = risky_scores(scorer, [command])
    levels = None
    if level_rows is not None:
        levels = [max(0.0, float(p)) for p in level_rows[0]]
        total = sum(levels) or 1.0
        levels = [p / total for p in levels]
    answers: dict[str, Any] = {}
    return {"answers": answers_for(questions, risky_probability(levels) if levels else scores[0], levels)}


def answers_for(questions: dict[str, Any], risky: float, levels: list[float] | None) -> dict[str, Any]:
    """One answer per question the models can answer.

    `risky` answers a yes/no question about approval, the levels answer the five-level score, and
    P(high or critical) answers "is it dangerous". A question no model here can answer ("does it need
    root") gets no entry, so a frontend shows it as not available instead of a number that means
    something else; so do the level and "dangerous" questions when no model has risk levels. Any other
    question gets a uniform answer, which the runner scores as an unconfident guess.
    """
    answers: dict[str, Any] = {}
    for qid, question in questions.items():
        kind = question_kind(qid, question)
        if kind == "risky":
            answers[qid] = {"type": "noul", "noul": risky}
        elif kind == "level" and levels:
            answers[qid] = {"type": "score", "probabilities": {str(i): p for i, p in enumerate(levels)}}
        elif kind == "dangerous" and levels:
            answers[qid] = {"type": "noul", "noul": dangerous_probability(levels)}
        elif kind in ("root", "dangerous", "level"):
            continue
        else:
            answers[qid] = {"type": question.get("type"), "probabilities": uniform(question_options(question))}
    return answers


def load_policy(name: str, members: list[str], results_dir: Path, frozen: Path | None = None) -> dict[str, Any]:
    """The cut-offs the harness measured for a model on validation, or why it has none.

    A result file in `results_dir` wins. Without one, the entry frozen into `frozen` (cmdgate.policy) is used.
    """
    path = results_dir / f"{name}_{POLICY_SPLIT}.json"
    try:
        if path.exists() or frozen is None:
            result = json.loads(path.read_text())
        else:
            result = json.loads(frozen.read_text())[name]
        confidence = float(result["automation_confidence"])
        metrics = result["metrics"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"evaluated": False, "why": f"no harness result in {path}"}
    if result.get("failed"):
        return {"evaluated": False, "why": f"{result['failed']} requests failed when it was evaluated"}
    if sorted(result.get("members") or []) != sorted(members):
        return {"evaluated": False, "why": f"evaluated with members {result.get('members')}, serving {sorted(members)}"}
    return {"evaluated": True, "split": POLICY_SPLIT, "error_budget": result.get("error_budget"),
            "confidence": confidence, "allow_at_or_below": 1.0 - confidence, "risky_at_or_above": confidence,
            "accuracy": metrics.get("accuracy"), "automation_rate": metrics.get("automation_rate_at_5pct")}


def verdict_for(p: float, policy: dict[str, Any]) -> tuple[str, str, str]:
    """(verdict, bucket, reason). Only an evaluated model inside its measured cut-off may allow."""
    if not policy["evaluated"]:
        return "ask", "unsure", f"Model not evaluated: {policy['why']}"
    confidence = policy["confidence"]
    if confidence >= 1.0:
        return "ask", "unsure", "No cut-off keeps this model within the error budget"
    # Same test as the runner's automation rate: the model's confidence in its own answer reaches the cut-off.
    if max(p, 1.0 - p) >= confidence:
        if p < 0.5:
            return "allow", "safe", f"Risk {p:.1%} is at or below the allow cut-off {1.0 - confidence:.1%}"
        return "ask", "likely", f"Risk {p:.1%} is at or above the risky cut-off {confidence:.1%}"
    return "ask", "unsure", f"Risk {p:.1%} is between the cut-offs"


class Gate:
    """The loaded models, the vote over them, and the cut-offs the harness measured for each."""

    def __init__(self, scorers: dict[str, Any], default: str | None = None, results_dir: Path = RESULTS_DIR,
                 frozen: Path | None = None) -> None:
        if not scorers:
            raise ValueError("the gate needs at least one model")
        self.scorers = scorers
        # An ensemble is left out of the vote: its members would be counted twice.
        self.voters = sorted(name for name in scorers if name != "prior" and name not in ENSEMBLES)
        self.names = [*scorers, VOTE] if len(self.voters) > 1 else list(scorers)
        self.default = default or (VOTE if VOTE in self.names else self.names[0])
        if self.default not in self.names:
            raise ValueError(f"default model {self.default!r} is not one of {self.names}")
        self.results_dir = results_dir
        self.frozen = frozen

    def scorer(self, name: str) -> Any:
        return VoteScorer({v: self.scorers[v] for v in self.voters}) if name == VOTE else self.scorers[name]

    def policy(self, name: str) -> dict[str, Any]:
        return load_policy(name, self.voters if name == VOTE else [], self.results_dir, self.frozen)

    def models(self) -> dict[str, Any]:
        return {"default": self.default,
                "data": [{"name": name, "members": self.voters if name == VOTE else [], **self.policy(name)}
                         for name in self.names]}

    def check(self, body: dict[str, Any]) -> dict[str, Any]:
        """One verdict for one command, from the requested model, with every model's probability if asked."""
        command = body.get("command")
        if not isinstance(command, str) or not command.strip():
            raise BadRequest('expected {"command": "<shell command>"}')
        name = body.get("model") or self.default
        if name not in self.names:
            raise BadRequest(f"unknown model {name!r}; serving {self.names}")
        wanted = self.voters if name == VOTE else [name]
        if body.get("all"):
            wanted = list(self.scorers)
        models: dict[str, Any] = {}
        for member in wanted:
            started = time.perf_counter()
            p = risky_scores(self.scorers[member], [command])[0][0]
            models[member] = {"p": p, "ms": 1000 * (time.perf_counter() - started)}
        if VOTE in self.names and (name == VOTE or body.get("all")):
            models[VOTE] = {"p": sum(models[v]["p"] for v in self.voters) / len(self.voters),
                            "ms": sum(models[v]["ms"] for v in self.voters)}
        for member, answer in models.items():  # every model's own verdict, from its own cut-offs
            answer["verdict"], answer["bucket"], answer["reason"] = verdict_for(answer["p"], self.policy(member))
        policy = self.policy(name)
        p = models[name]["p"]
        verdict, bucket, reason = verdict_for(p, policy)
        return {"command": command, "model": name, "p": p, "verdict": verdict, "bucket": bucket, "reason": reason,
                "policy": policy, "models": models, "rules": matched_rules(command)}


def make_handler(scorer: Any, name: str, gate: Gate | None = None) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            if self.path in ("/health", "/ready"):
                self.send(HTTPStatus.OK, {"ok": True})
            elif self.path == "/v1/models":
                listing = gate.models() if gate else {"data": [{"name": name, "description": "cmdgate engine"}]}
                self.send(HTTPStatus.OK, listing)
            else:
                self.send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path == "/v1/systemone":
                answer = lambda body: decide(scorer, body)  # noqa: E731
            elif self.path == "/v1/check" and gate:
                answer = gate.check
            else:
                self.send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise BadRequest("the request body must be a JSON object")
                self.send(HTTPStatus.OK, answer(body))
            except (BadRequest, json.JSONDecodeError, ValueError) as exc:
                self.send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            except Exception as exc:  # a scorer failure must not stop the server
                self.send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def models_on_disk() -> list[str]:
    """The in-process models that can be loaded right now."""
    return ["rules", *(["ngram"] if NGRAM_PATH.exists() else []),
            *([NGRAM_PLUS] if (MODELS_DIR / "ngram_plus.joblib").exists() else []),
            *(["encoder"] if ENCODER_DIR.exists() else [])]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--scorer", help="serve one in-process model")
    which.add_argument("--scorers", help="in-process models to serve, comma-separated; default: the light ones on "
                                         f"disk. Available now: {', '.join(in_process_names())}")
    parser.add_argument("--endpoint", action="append", default=[], metavar="NAME=URL",
                        help="also serve a model behind the decision API, such as Kev; repeatable")
    parser.add_argument("--default", help="model that answers when a request names none; default: the vote")
    parser.add_argument("--results", type=Path, default=RESULTS_DIR, help="folder with the harness results")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8009)
    args = parser.parse_args()
    names = [args.scorer] if args.scorer else args.scorers.split(",") if args.scorers else models_on_disk()
    try:
        gate = Gate(load_scorers(names, parse_endpoints(args.endpoint)), args.default, args.results, POLICY_PATH)
    except ValueError as exc:
        parser.error(str(exc))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(gate.scorer(gate.default), gate.default, gate))
    print(f"decision API on http://{args.host}:{args.port}/v1/systemone, default model {gate.default}")
    for model in gate.models()["data"]:
        state = (f"allow at or below {model['allow_at_or_below']:.3f}" if model["evaluated"]
                 else f"asks every time ({model['why']})")
        print(f"  {model['name']:<10}{state}")
    server.serve_forever()


if __name__ == "__main__":
    main()
