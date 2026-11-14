"""Short letter-probability distillation from a teacher decision API into a student.

Teacher is whichever of Kev / Qwen scored higher on validation automation @ 5%.
Student is the other. Soft targets are the teacher's P(A)=P(true) on the train
split only. A few hundred Adam steps; keep the result only if validation
automation rises (caller decides).

    # Kev student (pointer-head / LoRA already in a Kev run dir): use kev.train with soft labels
    # Qwen student: this script updates a merged HF causal LM toward teacher letter probs.

    uv run python cmdgate/distill_letters.py \\
      --data data/cmdrisk_qwen_train.jsonl \\
      --student runs/cmdrisk-qwen06/merged \\
      --teacher-endpoint http://127.0.0.1:8010/v1/systemone \\
      --steps 400 --out runs/cmdrisk-qwen06-kd
"""

from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoModelForCausalLM, AutoTokenizer


def post_json(url: str, body: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def teacher_p_true(endpoint: str, command: str) -> float:
    body = {
        "model": "local",
        "state": {"command": command},
        "questions": {
            "risky": {
                "type": "noul",
                "instructions": (
                    "Should a person approve this shell command before an AI coding agent runs it?"
                ),
                "criteria": {
                    "true": (
                        "Undoing it takes real effort or backups, or it exposes secrets, "
                        "runs unverified code, changes privileges, or disrupts the machine or its network."
                    ),
                    "false": (
                        "It only reads or inspects, or makes a local change that is easy to undo, "
                        "with no secrets and no privilege change."
                    ),
                },
            }
        },
    }
    answers = post_json(endpoint, body).get("answers") or {}
    a = answers.get("risky") or {}
    if "noul" in a:
        return float(a["noul"])
    probs = (a.get("probabilities") or {})
    if "true" in probs:
        return float(probs["true"])
    # bridge letter form shouldn't appear on Kev, but tolerate A/B mass
    return 0.5


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="bridge-prompt JSONL (train only)")
    parser.add_argument("--student", type=Path, required=True, help="HF causal LM dir (merged)")
    parser.add_argument("--teacher-endpoint", required=True)
    parser.add_argument("--steps", type=int, default=400)
    parser.add_argument("--lr", type=float, default=5e-6)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=None, help="optional JSONL of teacher soft labels")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    rows = [json.loads(line) for line in args.data.read_text(encoding="utf-8").splitlines() if line.strip()]
    cache_path = args.cache or (args.out / "teacher_soft.jsonl")
    soft: list[float] = []
    if cache_path.exists():
        soft = [float(json.loads(line)["p_true"]) for line in cache_path.read_text().splitlines() if line.strip()]
        print(f"loaded {len(soft)} soft labels from {cache_path}")
    if len(soft) < len(rows):
        with cache_path.open("w", encoding="utf-8") as f:
            for i, row in enumerate(rows):
                if i < len(soft):
                    p = soft[i]
                else:
                    p = teacher_p_true(args.teacher_endpoint, row["command"])
                    soft.append(p)
                    if (i + 1) % 50 == 0:
                        print(f"teacher soft {i + 1}/{len(rows)} p_true={p:.3f}")
                f.write(json.dumps({"command": row["command"], "p_true": soft[i]}) + "\n")
        print(f"wrote {cache_path}")

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(args.student, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.student, torch_dtype=torch.float32, trust_remote_code=True
    ).to(device)
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)

    letter_ids = {
        "A": tok.encode("A", add_special_tokens=False)[-1],
        "B": tok.encode("B", add_special_tokens=False)[-1],
    }
    print(f"letter token ids: {letter_ids}")

    n = len(rows)
    step = 0
    while step < args.steps:
        idx = step % n
        row = rows[idx]
        p_true = soft[idx]
        prompt = tok.apply_chat_template(row["messages"], tokenize=False, add_generation_prompt=True)
        enc = tok(prompt, return_tensors="pt").to(device)
        out = model(**enc)
        logits = out.logits[0, -1, [letter_ids["A"], letter_ids["B"]]]
        log_probs = F.log_softmax(logits, dim=-1)
        target = torch.tensor([p_true, 1.0 - p_true], device=device, dtype=log_probs.dtype)
        loss = -(target * log_probs).sum()
        loss.backward()
        if (step + 1) % args.batch == 0:
            opt.step()
            opt.zero_grad()
        if step % 20 == 0 or step + 1 == args.steps:
            print(f"step {step + 1}/{args.steps} loss={loss.item():.4f} p_true*={p_true:.3f}")
        step += 1

    model.save_pretrained(args.out / "merged")
    tok.save_pretrained(args.out / "merged")
    (args.out / "distill_config.json").write_text(
        json.dumps(
            {
                "student": str(args.student),
                "teacher_endpoint": args.teacher_endpoint,
                "steps": args.steps,
                "lr": args.lr,
                "data": str(args.data),
            },
            indent=2,
        )
    )
    print(f"wrote {args.out / 'merged'}")


if __name__ == "__main__":
    main()
