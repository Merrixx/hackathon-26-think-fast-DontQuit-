"""Fine-tuned text encoder: a pretrained transformer with a five-level classification head.

    uv run --extra ml python -m cmdgate.encoder          # trains, saves models/encoder/

It predicts the five risk levels from the command string alone, like the
n-gram baseline. The epoch is chosen on the validation split by log loss, and a
single temperature fitted on validation corrects over-confident probabilities.
The test split is never read here.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

from .dataset import Command, load_all
from .questions import LEVELS

MODEL_DIR = Path("models/encoder")
BASE = "answerdotai/ModernBERT-base"
MAX_TOKENS = 128
META = "cmdgate.json"


def pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def log_loss(logits: list[list[float]], labels: list[int], temperature: float = 1.0) -> float:
    """Mean negative log-probability of the true level after dividing the logits by `temperature`."""
    total = 0.0
    for row, label in zip(logits, labels, strict=True):
        scaled = [x / temperature for x in row]
        top = max(scaled)
        total += top + math.log(sum(math.exp(x - top) for x in scaled)) - scaled[label]
    return total / len(labels)


def fit_temperature(logits: list[list[float]], labels: list[int], low: float = 0.25, high: float = 8.0) -> float:
    """The temperature with the lowest log loss, by golden-section search on a log scale."""
    ratio = (math.sqrt(5) - 1) / 2
    a, b = math.log(low), math.log(high)
    loss = lambda t: log_loss(logits, labels, math.exp(t))  # noqa: E731
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    for _ in range(40):
        if loss(c) < loss(d):
            b, d = d, c
            c = b - ratio * (b - a)
        else:
            a, c = c, d
            d = a + ratio * (b - a)
    return math.exp((a + b) / 2)


def softmax(row: list[float], temperature: float) -> list[float]:
    scaled = [x / temperature for x in row]
    top = max(scaled)
    weights = [math.exp(x - top) for x in scaled]
    total = sum(weights)
    return [w / total for w in weights]


def predict_logits(model, tokenizer, texts: list[str], device: str, batch_size: int = 64) -> list[list[float]]:
    """Logits per text, in input order. Texts are batched by length to keep padding small."""
    import torch

    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out: list[list[float]] = [[] for _ in texts]
    model.eval()
    with torch.no_grad():
        for start in range(0, len(order), batch_size):
            idx = order[start:start + batch_size]
            batch = tokenizer([texts[i] for i in idx], padding=True, truncation=True, max_length=MAX_TOKENS,
                              return_tensors="pt").to(device)
            for i, row in zip(idx, model(**batch).logits.float().cpu().tolist()):
                out[i] = row
    return out


def train(train_set: list[Command], validation: list[Command], base: str, epochs: int, lr: float,
          batch_size: int, device: str, seed: int, out: Path) -> dict:
    """Fine-tune `base`, keep the epoch with the lowest validation log loss in `out`, and return its record."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    random.seed(seed)
    torch.manual_seed(seed)
    tokenizer = AutoTokenizer.from_pretrained(base)
    model = AutoModelForSequenceClassification.from_pretrained(base, num_labels=len(LEVELS)).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    steps = epochs * math.ceil(len(train_set) / batch_size)
    schedule = get_linear_schedule_with_warmup(optimizer, int(0.06 * steps), steps)
    val_texts, val_labels = [c.command for c in validation], [c.level for c in validation]
    history, best = [], None
    for epoch in range(1, epochs + 1):
        model.train()
        rows = random.sample(train_set, len(train_set))
        started, running = time.perf_counter(), 0.0
        for n, start in enumerate(range(0, len(rows), batch_size), 1):
            chunk = rows[start:start + batch_size]
            batch = tokenizer([c.command for c in chunk], padding=True, truncation=True, max_length=MAX_TOKENS,
                              return_tensors="pt").to(device)
            loss = model(**batch, labels=torch.tensor([c.level for c in chunk], device=device)).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            schedule.step()
            optimizer.zero_grad()
            running += loss.item()
            if n % 100 == 0:
                print(f"  epoch {epoch} step {n}: train loss {running / n:.4f}, "
                      f"{time.perf_counter() - started:.0f}s", flush=True)
        logits = predict_logits(model, tokenizer, val_texts, device)
        val_loss = log_loss(logits, val_labels)
        history.append({"epoch": epoch, "train_loss": running / n, "validation_log_loss": val_loss})
        print(f"epoch {epoch}: validation log loss {val_loss:.4f}", flush=True)
        if best is None or val_loss < best["validation_log_loss"]:
            temperature = fit_temperature(logits, val_labels)
            best = {"epoch": epoch, "validation_log_loss": val_loss, "temperature": temperature,
                    "validation_log_loss_scaled": log_loss(logits, val_labels, temperature)}
            out.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(out)
            tokenizer.save_pretrained(out)
    record = {"base": base, "levels": LEVELS, "epochs": epochs, "lr": lr, "batch_size": batch_size, "seed": seed,
              "train_rows": len(train_set), "validation_rows": len(validation), "history": history, **best}
    (out / META).write_text(json.dumps(record, indent=2))
    return record


class EncoderScorer:
    def __init__(self, path: Path = MODEL_DIR, device: str | None = None) -> None:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.device = device or pick_device()
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path).to(self.device)
        self.temperature = json.loads((path / META).read_text())["temperature"]

    def levels(self, command: str) -> list[float]:
        return self.batch_levels([command])[0]

    def batch_levels(self, commands: list[str]) -> list[list[float]]:
        logits = predict_logits(self.model, self.tokenizer, commands, self.device)
        return [softmax(row, self.temperature) for row in logits]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default=BASE, help="pretrained encoder on the Hugging Face Hub")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--lr", type=float, default=5e-5)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--device", default=None, help="cuda, mps or cpu; picked automatically if omitted")
    parser.add_argument("--limit", type=int, default=0, help="train on a random N rows only, for a smoke test")
    parser.add_argument("--out", type=Path, default=MODEL_DIR)
    args = parser.parse_args()
    splits = load_all()
    train_set = random.Random(args.seed).sample(splits["train"], args.limit) if args.limit else splits["train"]
    device = args.device or pick_device()
    print(f"fine-tuning {args.base} on {len(train_set)} commands, device {device}")
    record = train(train_set, splits["validation"], args.base, args.epochs, args.lr, args.batch, device,
                   args.seed, args.out)
    print(f"kept epoch {record['epoch']}: validation log loss {record['validation_log_loss']:.4f}, "
          f"{record['validation_log_loss_scaled']:.4f} at temperature {record['temperature']:.2f}")
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
