"""Minimal OpenAI-compatible chat server with top_logprobs for thinkfast.bridge.

    # from an env that has torch + transformers (e.g. the Kev checkout):
    uv run python /path/to/scripts/hf_logprobs_server.py \\
      --model /path/to/runs/cmdrisk-qwen06/merged --port 8080

Only implements POST /v1/chat/completions with max_tokens=1 and logprobs=true,
which is what the bridge needs.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


class Model:
    def __init__(self, path: str, device: str):
        self.name = path
        self.device = device
        self.tok = AutoTokenizer.from_pretrained(path, trust_remote_code=True)
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        dtype = torch.float32 if device == "mps" else torch.bfloat16
        self.model = AutoModelForCausalLM.from_pretrained(
            path, torch_dtype=dtype, trust_remote_code=True
        ).to(device)
        self.model.eval()

    @torch.inference_mode()
    def top_logprobs(self, messages: list[dict[str, str]], k: int) -> list[dict[str, Any]]:
        prompt = self.tok.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        enc = self.tok(prompt, return_tensors="pt").to(self.device)
        logits = self.model(**enc).logits[0, -1]
        log_probs = torch.log_softmax(logits.float(), dim=-1)
        values, indices = torch.topk(log_probs, k=min(k, log_probs.numel()))
        out = []
        for lp, idx in zip(values.tolist(), indices.tolist()):
            token = self.tok.decode([idx])
            out.append({"token": token, "logprob": float(lp)})
        return out


def make_handler(model: Model) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path in ("/health", "/ready", "/v1/models"):
                self._send(HTTPStatus.OK, {"data": [{"id": model.name}]})
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/chat/completions":
                self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                messages = body.get("messages") or []
                k = int(body.get("top_logprobs") or 20)
                tops = model.top_logprobs(messages, k)
                # chosen token = highest mass
                best = max(tops, key=lambda e: e["logprob"])
                self._send(
                    HTTPStatus.OK,
                    {
                        "model": model.name,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": best["token"]},
                                "logprobs": {
                                    "content": [
                                        {
                                            "token": best["token"],
                                            "logprob": best["logprob"],
                                            "top_logprobs": tops,
                                        }
                                    ]
                                },
                            }
                        ],
                    },
                )
            except Exception as exc:  # keep serving
                self._send(HTTPStatus.BAD_GATEWAY, {"error": f"{type(exc).__name__}: {exc}"})

        def log_message(self, *args: Any) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="HF model dir or hub id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--device", choices=("auto", "mps", "cpu"), default="auto")
    args = parser.parse_args()
    if args.device == "cpu":
        device = "cpu"
    elif args.device == "mps":
        device = "mps"
    else:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = Model(args.model, device)
    print(f"hf logprobs server {model.name} on {device} http://{args.host}:{args.port}/v1")
    ThreadingHTTPServer((args.host, args.port), make_handler(model)).serve_forever()


if __name__ == "__main__":
    main()
