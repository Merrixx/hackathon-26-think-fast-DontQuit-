"""LoRA-tune Qwen3-0.6B so the next token after the bridge prompt is A or B.

    # from the Kev checkout (has torch / transformers / peft):
    uv run python /path/to/cmdgate/train_qwen_lora.py \\
      --data /path/to/data/cmdrisk_qwen_train.jsonl \\
      --out /path/to/runs/cmdrisk-qwen06 \\
      --device mps --early-stop --val-frac 0.05

Train split only (optional holdout carved from that file for early stopping).
Merges the adapter into the base and writes `merged/`.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import torch
from peft import LoraConfig, PeftModel, get_peft_model
from torch.utils.data import Dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    EarlyStoppingCallback,
    Trainer,
    TrainingArguments,
)


class LetterDataset(Dataset):
    def __init__(self, rows: list[dict], tokenizer, max_length: int = 1024):
        self.rows: list[dict] = []
        for row in rows:
            messages = row["messages"]
            completion = str(row["completion"]).strip().upper()[:1]
            prompt = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            full = prompt + completion
            enc = tokenizer(
                full,
                truncation=True,
                max_length=max_length,
                add_special_tokens=False,
            )
            prompt_ids = tokenizer(
                prompt,
                truncation=True,
                max_length=max_length,
                add_special_tokens=False,
            )["input_ids"]
            labels = list(enc["input_ids"])
            for i in range(min(len(prompt_ids), len(labels))):
                labels[i] = -100
            if len(labels) == len(prompt_ids):
                continue
            self.rows.append(
                {
                    "input_ids": enc["input_ids"],
                    "attention_mask": enc["attention_mask"],
                    "labels": labels,
                }
            )

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> dict:
        return self.rows[idx]


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--base", default="Qwen/Qwen3-0.6B")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=float, default=1.0)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--accum", type=int, default=8)
    parser.add_argument("--max_length", type=int, default=1024)
    parser.add_argument("--r", type=int, default=16)
    parser.add_argument(
        "--device",
        choices=("auto", "cuda", "mps", "cpu"),
        default="auto",
        help="auto: cuda if available, else mps, else cpu; use cuda on DGX",
    )
    parser.add_argument(
        "--early-stop",
        action="store_true",
        help="carve --val-frac from the train JSONL and stop when eval loss stops improving",
    )
    parser.add_argument(
        "--val-frac",
        type=float,
        default=0.05,
        help="fraction of --data held out for early stopping (train file only; never the published test split)",
    )
    parser.add_argument("--patience", type=int, default=3, help="eval checks with no improve before stop")
    parser.add_argument("--eval-steps", type=int, default=200, help="eval / early-stop check every N optimizer steps")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if args.device == "cpu":
        device = "cpu"
    elif args.device == "cuda":
        if not torch.cuda.is_available():
            raise SystemExit("--device cuda requested but torch.cuda.is_available() is False")
        device = "cuda"
    elif args.device == "mps":
        device = "mps"
    else:
        # auto
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

    use_bf16 = device == "cuda" and hasattr(torch.cuda, "is_bf16_supported") and torch.cuda.is_bf16_supported()
    use_fp16 = device == "cuda" and not use_bf16
    if device == "mps":
        load_dtype = torch.float32
    elif device == "cuda":
        load_dtype = torch.bfloat16 if use_bf16 else torch.float16
    else:
        load_dtype = torch.float32

    tok = AutoTokenizer.from_pretrained(args.base, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        args.base,
        torch_dtype=load_dtype,
        trust_remote_code=True,
    )
    lora = LoraConfig(
        r=args.r,
        lora_alpha=32,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    raw = load_jsonl(args.data)
    rng = random.Random(args.seed)
    order = list(range(len(raw)))
    rng.shuffle(order)
    eval_ds = None
    callbacks = []
    if args.early_stop:
        if not (0.0 < args.val_frac < 0.5):
            raise SystemExit("--val-frac must be in (0, 0.5)")
        n_val = max(1, int(round(len(order) * args.val_frac)))
        val_idx = set(order[:n_val])
        train_rows = [raw[i] for i in order[n_val:]]
        val_rows = [raw[i] for i in order[:n_val]]
        eval_ds = LetterDataset(val_rows, tok, args.max_length)
        callbacks.append(EarlyStoppingCallback(early_stopping_patience=args.patience))
        print(f"early-stop: train={len(train_rows)} val={len(val_rows)} patience={args.patience}")
    else:
        train_rows = [raw[i] for i in order]

    ds = LetterDataset(train_rows, tok, args.max_length)
    print(f"rows={len(ds)} device={device} bf16={use_bf16} fp16={use_fp16}")

    train_args = TrainingArguments(
        output_dir=str(args.out / "checkpoints"),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.accum,
        learning_rate=args.lr,
        logging_steps=20,
        eval_strategy="steps" if args.early_stop else "no",
        eval_steps=args.eval_steps if args.early_stop else None,
        save_strategy="steps" if args.early_stop else "steps",
        save_steps=args.eval_steps if args.early_stop else 2000,
        load_best_model_at_end=bool(args.early_stop),
        metric_for_best_model="eval_loss" if args.early_stop else None,
        greater_is_better=False if args.early_stop else None,
        save_total_limit=2 if args.early_stop else 1,
        report_to=[],
        remove_unused_columns=False,
        bf16=use_bf16,
        fp16=use_fp16,
        dataloader_pin_memory=(device == "cuda"),
        use_cpu=(device == "cpu"),
    )

    def collate(features: list[dict]) -> dict:
        max_len = max(len(f["input_ids"]) for f in features)
        batch = {"input_ids": [], "attention_mask": [], "labels": []}
        for f in features:
            pad = max_len - len(f["input_ids"])
            batch["input_ids"].append(f["input_ids"] + [tok.pad_token_id] * pad)
            batch["attention_mask"].append(f["attention_mask"] + [0] * pad)
            batch["labels"].append(f["labels"] + [-100] * pad)
        return {k: torch.tensor(v) for k, v in batch.items()}

    trainer = Trainer(
        model=model,
        args=train_args,
        train_dataset=ds,
        eval_dataset=eval_ds,
        data_collator=collate,
        callbacks=callbacks,
    )
    trainer.train()
    adapter_dir = args.out / "adapter"
    model.save_pretrained(adapter_dir)
    tok.save_pretrained(adapter_dir)

    print("merging adapter into base…")
    base = AutoModelForCausalLM.from_pretrained(
        args.base,
        torch_dtype=load_dtype,
        trust_remote_code=True,
    )
    merged = PeftModel.from_pretrained(base, str(adapter_dir))
    merged = merged.merge_and_unload()
    merged_dir = args.out / "merged"
    merged.save_pretrained(merged_dir)
    tok.save_pretrained(merged_dir)
    (args.out / "training_config.json").write_text(
        json.dumps(
            {
                "base": args.base,
                "data": str(args.data),
                "epochs": args.epochs,
                "lr": args.lr,
                "batch": args.batch,
                "accum": args.accum,
                "r": args.r,
                "rows": len(ds),
                "device": device,
                "early_stop": bool(args.early_stop),
                "val_frac": args.val_frac if args.early_stop else 0.0,
                "patience": args.patience if args.early_stop else None,
                "eval_steps": args.eval_steps if args.early_stop else None,
            },
            indent=2,
        )
    )
    print(f"adapter: {adapter_dir}")
    print(f"merged:  {merged_dir}")


if __name__ == "__main__":
    main()
