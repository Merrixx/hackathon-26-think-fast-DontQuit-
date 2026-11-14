"""Serve a command-risk scorer behind the decision API.

    uv run --extra ml python -m cmdgate.engine --scorer ngram

A scorer is anything with `levels(command) -> list[float]`, a probability per
risk level in `cmdgate.questions.LEVELS`. The engine answers a yes/no question
with the probability that the command is risky and a five-level score question
with the levels themselves; any other question gets a uniform answer, which the
evaluation runner scores as an unconfident guess.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

from thinkfast.metrics import question_options, uniform

from .questions import LEVELS, command_from_state, risky_probability
from .rules import RulesScorer


class Scorer(Protocol):
    def levels(self, command: str) -> list[float]: ...


class BadRequest(ValueError):
    """The request is not a valid decision API request for this engine."""


def decide(scorer: Scorer, body: dict[str, Any]) -> dict[str, Any]:
    """Answer every question in one decision API request."""
    questions = body.get("questions")
    if not isinstance(questions, dict) or not questions:
        raise BadRequest("questions must be a non-empty object")
    try:
        command = command_from_state(body.get("state"))
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    levels = [max(0.0, float(p)) for p in scorer.levels(command)]
    total = sum(levels) or 1.0
    levels = [p / total for p in levels]
    answers: dict[str, Any] = {}
    for qid, question in questions.items():
        qtype = question.get("type")
        if qtype == "noul":
            answers[qid] = {"type": "noul", "noul": risky_probability(levels)}
        elif qtype == "score" and len(question.get("criteria") or []) == len(LEVELS):
            answers[qid] = {"type": "score", "probabilities": {str(i): p for i, p in enumerate(levels)}}
        else:
            answers[qid] = {"type": question.get("type"), "probabilities": uniform(question_options(question))}
    return {"answers": answers}


def make_handler(scorer: Scorer, name: str) -> type[BaseHTTPRequestHandler]:
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
                self.send(HTTPStatus.OK, {"data": [{"name": name, "description": "cmdgate engine"}]})
            else:
                self.send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/systemone":
                self.send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("content-length", 0))
                self.send(HTTPStatus.OK, decide(scorer, json.loads(self.rfile.read(length) or b"{}")))
            except (BadRequest, json.JSONDecodeError, ValueError) as exc:
                self.send(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": str(exc)})
            except Exception as exc:  # a scorer failure must not stop the server
                self.send(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def load_scorer(name: str) -> Scorer:
    if name == "rules":
        return RulesScorer()
    if name == "ngram":
        from .ngram import NgramScorer
        return NgramScorer()
    if name == "hashreg":
        from .hashreg import HashRegScorer
        return HashRegScorer()
    raise ValueError(f"unknown scorer: {name}")


SCORERS = ("rules", "ngram", "hashreg")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scorer", choices=SCORERS, default="rules")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8009)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(load_scorer(args.scorer), args.scorer))
    print(f"decision API on http://{args.host}:{args.port}/v1/systemone ({args.scorer})")
    server.serve_forever()


if __name__ == "__main__":
    main()
