"""Serve the frozen classic model on the decision API (POST /v1/systemone).

    uv run python -m tagger.serve            # 127.0.0.1:8009

Every `noul` question in a request gets the model's probability of yes for the
request's `state` (the feature columns from dataset.toml, as text), plus the
frozen policy's proposed tag. Other question types are answered with an even
split, which the runner scores as a guess. Kev checkpoints don't need this:
`kev.serve` serves the same API itself.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pandas as pd

from thinkfast.metrics import question_options, uniform

from .config import load
from .engine import LocalEngine, load_frozen


def answer(engine: LocalEngine, policy, ds, body: dict) -> dict:
    state = body.get("state")
    if not isinstance(state, dict):
        raise ValueError("state must be an object with the dataset's feature columns")
    row = {c: "" if state.get(c) is None else str(state.get(c)) for c in ds.features}
    row[ds.id] = "request"
    p = float(engine.predict_frame(pd.DataFrame([row]))[0])
    answers = {}
    for qid, q in (body.get("questions") or {}).items():
        if q.get("type") == "noul":
            answers[qid] = {"type": "noul", "noul": p, "tag": policy.tag(p)}
        else:
            answers[qid] = {"type": q.get("type"), "probabilities": uniform(question_options(q))}
    return {"model": body.get("model", "local"), "answers": answers}


def make_handler(engine, policy, ds):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path.rstrip("/") != "/v1/systemone":
                self.reply(404, {"error": "only POST /v1/systemone is served"})
                return
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
                self.reply(200, answer(engine, policy, ds, body))
            except Exception as exc:  # keep serving after any bad request
                self.reply(400, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, *args):
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8009)
    args = parser.parse_args()
    ds = load()
    engine, policy, meta = load_frozen(ds)
    if not isinstance(engine, LocalEngine):
        raise SystemExit("the frozen engine is a decision API already; serve it with its own server (kev.serve)")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(engine, policy, ds))
    print(f"serving {meta['model']} for {ds.name!r} on http://{args.host}:{args.port}/v1/systemone")
    server.serve_forever()


if __name__ == "__main__":
    main()
