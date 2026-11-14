"""Train and score fine-tuned text encoders for the command gate on a Modal GPU.

    uvx modal run cmdgate/modal_encoder.py::smoke
    uvx modal run cmdgate/modal_encoder.py::sweep --names mb-cap8,mb-full --folds 0,1,2
    uvx modal run cmdgate/modal_encoder.py::final --name mb-cap8 --epoch 6 --temperature 1.8
    uvx modal volume get cmdgate-encoder models/enc-mb-cap8 models/
    uvx modal run cmdgate/modal_encoder.py::score --names mb-cap8 --commands hard.json --tag hard

`sweep` fits each named configuration on train with whole template families
held out (`cmdgate.lab.family_folds`, k=5, seed 0) and writes the held-out
logits of every evaluated epoch to `results/preds/raw/`. `final` fits one
configuration on all of train, stops at the epoch that won out-of-fold, saves
the model on the volume in the layout `cmdgate.encoder.EncoderScorer` loads and
writes its validation logits. Scoring and logging happen on the Mac, in
`cmdgate.encoder_log`. The container downloads the train and validation
parquet files from the Hub at the revision pinned in `cmdgate.fetch_data`; the
test split is never downloaded.
"""

from __future__ import annotations

import json
import math
import random
import re
import time
from pathlib import Path
from typing import Any

import modal

APP = "cmdgate-encoder"
VOLUME = "/vol"
RAW_DIR = Path("results/preds/raw")
MAX_TOKENS = 128

DEFAULTS: dict[str, Any] = {
    "base": "answerdotai/ModernBERT-base",
    "head": "level5",        # level5: five risk levels; binary: risky yes/no
    "cap": 0,                # at most this many commands per family per epoch, redrawn every epoch; 0 = all
    "weight_power": 0.0,     # loss weight family size ** -power
    "epochs": 3,
    "eval_every": 1,
    "lr": 5e-5,
    "batch": 32,
    "seed": 0,
    "label_smoothing": 0.0,
    "mask_prob": 0.0,        # share of input tokens replaced by the mask token while training
    "aug": 0.0,              # probability of a label-keeping token swap per training command
    "weight_decay": 0.01,
    "amp": True,
    "prompt": False,         # wrap the command in the question and its criteria (needs the same wrap when served)
    "max_tokens": MAX_TOKENS,
    "limit": 0,              # smoke test: train on this many random rows
}

MB, MBL = "answerdotai/ModernBERT-base", "answerdotai/ModernBERT-large"
CAP8 = {"cap": 8, "epochs": 10}
CONFIGS: dict[str, dict[str, Any]] = {
    "smoke": {"epochs": 1, "limit": 600},
    # ModernBERT-base: sampling, regularisation, head
    "mb-full": {},
    "mb-fw1": {"weight_power": 1.0},
    "mb-cap8": {**CAP8},
    "mb-cap16": {"cap": 16, "epochs": 8},
    "mb-cap40-e2": {"cap": 40, "epochs": 2},
    "mb-cap40-e4": {"cap": 40, "epochs": 4},
    "mb-cap20-e4": {"cap": 20, "epochs": 4},
    "mb-fw05": {"weight_power": 0.5},
    "mb-cap3": {"cap": 3, "epochs": 16, "eval_every": 2},
    "mb-cap8-lr2": {**CAP8, "lr": 2e-5},
    "mb-cap8-reg": {**CAP8, "label_smoothing": 0.1, "mask_prob": 0.15},
    "mb-cap8-bin": {**CAP8, "head": "binary"},
    "mb-cap8-aug": {**CAP8, "aug": 0.5},
    # other bases, same recipe
    "codebert-cap8": {**CAP8, "base": "microsoft/codebert-base"},
    "unixcoder-cap8": {**CAP8, "base": "microsoft/unixcoder-base"},
    "deberta-cap8": {**CAP8, "base": "microsoft/deberta-v3-base", "amp": False},
    "gte-cap8": {**CAP8, "base": "Alibaba-NLP/gte-modernbert-base"},
    "bge-cap8": {**CAP8, "base": "BAAI/bge-base-en-v1.5"},
    "mbl-cap8": {**CAP8, "base": MBL, "lr": 2e-5},
    "mbl-full": {"base": MBL, "lr": 2e-5},
    "smol-cap8": {**CAP8, "base": "HuggingFaceTB/SmolLM2-360M", "lr": 3e-5},
    "qwenc-cap8": {**CAP8, "base": "Qwen/Qwen2.5-Coder-0.5B", "lr": 3e-5},  # 494M parameters: over the 400M target
    "mbl-cap8-prompt": {**CAP8, "base": MBL, "lr": 2e-5, "prompt": True, "max_tokens": 224},
    "qwenc-cap8-prompt": {**CAP8, "base": "Qwen/Qwen2.5-Coder-0.5B", "lr": 3e-5, "prompt": True, "max_tokens": 224},
    "qwenc-full": {"base": "Qwen/Qwen2.5-Coder-0.5B", "lr": 3e-5},
    "qwenc-cap8-lr1": {**CAP8, "base": "Qwen/Qwen2.5-Coder-0.5B", "lr": 1e-5},
    "qwenc-cap40-e2": {"cap": 40, "epochs": 2, "base": "Qwen/Qwen2.5-Coder-0.5B", "lr": 3e-5},
    "qwenc-cap8-reg": {**CAP8, "base": "Qwen/Qwen2.5-Coder-0.5B", "lr": 3e-5, "label_smoothing": 0.1, "aug": 0.5},
    "qwen-cap8": {**CAP8, "base": "Qwen/Qwen2.5-0.5B", "lr": 3e-5},
    "qwenc15-cap8": {**CAP8, "base": "Qwen/Qwen2.5-Coder-1.5B", "lr": 2e-5},  # 1.5B: far over the target, for scale
    "debertal-cap8": {**CAP8, "base": "microsoft/deberta-v3-large", "lr": 1e-5, "amp": False},
}


def resolve(name: str) -> dict[str, Any]:
    """The full configuration behind a name; a `-s<seed>` suffix reruns a named one with another seed."""
    stem, _, seed = name.rpartition("-s")
    if stem in CONFIGS and seed.isdigit():
        return {**DEFAULTS, **CONFIGS[stem], "seed": int(seed), "name": name}
    return {**DEFAULTS, **CONFIGS[name], "name": name}


image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install("torch", "transformers", "pandas", "pyarrow", "huggingface-hub", "sentencepiece", "protobuf",
                      "safetensors")
         .env({"HF_HOME": f"{VOLUME}/hf", "TOKENIZERS_PARALLELISM": "false"}))
if modal.is_local():
    root = Path(__file__).resolve().parent.parent
    for package in ("cmdgate", "thinkfast"):
        image = image.add_local_dir(root / package, f"/root/{package}", ignore=["__pycache__", "*.pyc"])

app = modal.App(APP, image=image)
volume = modal.Volume.from_name(APP, create_if_missing=True)

# ---- label-keeping token swaps: same kind of device, address or file, different spelling ----

DEVICES = ["/dev/sda", "/dev/sdb", "/dev/sdc", "/dev/vda", "/dev/nvme0n1", "/dev/nvme1n1", "/dev/mmcblk0", "/dev/xvdf"]
DEVICE_RE = re.compile(r"/dev/(?:sd[a-z]|vd[a-z]|xvd[a-z]|nvme\dn\d|mmcblk\d)(?![\w])")
OCTET_RE = re.compile(r"\b((?:192\.0\.2|198\.51\.100|203\.0\.113|10\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3})\.)(\d{1,3})\b")
FILE_RE = re.compile(r"(?<![\w.-])([A-Za-z][\w-]{2,})\.(img|iso|txt|log|md|csv|tar|tgz|gz|zip|bak|sql|png|jpg|pdf|html)\b")
STEMS = ["notes", "report", "data", "archive", "output", "draft", "sample", "build", "image", "export", "old",
         "results", "snapshot", "dump", "release", "summary"]
KEEP = re.compile(r"secret|cred|key|pass|token|shadow|env|auth|priv|cert|wallet|history|backup|prod", re.I)


def augment(command: str, rng: random.Random) -> str:
    """Swap device names, the last octet of private or documentation addresses, and plain file stems."""
    command = DEVICE_RE.sub(lambda m: rng.choice(DEVICES), command)
    command = OCTET_RE.sub(lambda m: f"{m.group(1)}{rng.randint(1, 254)}" if m.group(2) != "0" else m.group(0), command)
    return FILE_RE.sub(lambda m: m.group(0) if KEEP.search(m.group(1)) else f"{rng.choice(STEMS)}.{m.group(2)}",
                       command)


# ---- container side ----

def load_data() -> tuple[list, list]:
    """Train and validation commands, by the project's own loader, from the Hub at the pinned revision."""
    from huggingface_hub import snapshot_download

    from cmdgate.dataset import load_split
    from cmdgate.fetch_data import SOURCES

    source = next(s for s in SOURCES if s.name == "cli-command-risk")
    target = Path(VOLUME) / "data" / source.name
    snapshot_download(source.repo_id, repo_type="dataset", revision=source.revision,
                      allow_patterns=["data/train.parquet", "data/validation.parquet"], local_dir=target)
    train = load_split("train", target / "data")
    seen = frozenset(c.command for c in train)
    return train, load_split("validation", target / "data", seen)


def targets_of(commands: list, head: str) -> list[int]:
    return [int(c.risky) if head == "binary" else c.level for c in commands]


def wrap(command: str, cfg: dict[str, Any]) -> str:
    """The text the model reads: the command, or with `prompt` the question, its criteria and the command."""
    if not cfg.get("prompt"):
        return command
    from cmdgate.questions import RISKY_QUESTION as q

    return (f"{q['instructions']}\nYes: {q['criteria']['true']}\nNo: {q['criteria']['false']}\n"
            f"Command: {command}\nRisk:")


def predict(model, tokenizer, texts: list[str], amp: bool, batch_size: int = 256,
            max_tokens: int = MAX_TOKENS) -> list[list[float]]:
    import torch

    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out: list[list[float]] = [[] for _ in texts]
    model.eval()
    with torch.no_grad():
        for start in range(0, len(order), batch_size):
            idx = order[start:start + batch_size]
            batch = tokenizer([texts[i] for i in idx], padding=True, truncation=True, max_length=max_tokens,
                              return_tensors="pt").to("cuda")
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp):
                logits = model(**batch).logits
            for i, row in zip(idx, logits.float().cpu().tolist()):
                out[i] = [round(x, 4) for x in row]
    return out


def mean_log_loss(logits: list[list[float]], labels: list[int]) -> float:
    total = 0.0
    for row, label in zip(logits, labels, strict=True):
        top = max(row)
        total += top + math.log(sum(math.exp(x - top) for x in row)) - row[label]
    return total / len(labels)


def fit(cfg: dict[str, Any], train_part: list, held: list, stop_at: int = 0, save_to: Path | None = None) -> dict:
    """Fine-tune on `train_part`; return held-out logits per evaluated epoch (only `stop_at` when it is set)."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    from cmdgate.lab import family_weights

    rng = random.Random(cfg["seed"])
    torch.manual_seed(cfg["seed"])
    head, amp = cfg["head"], cfg["amp"]
    tokenizer = AutoTokenizer.from_pretrained(cfg["base"])
    model = AutoModelForSequenceClassification.from_pretrained(
        cfg["base"], num_labels=2 if head == "binary" else 5).to("cuda")
    if tokenizer.pad_token is None:  # decoder bases
        tokenizer.pad_token = tokenizer.eos_token
    model.config.pad_token_id = tokenizer.pad_token_id
    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
    weight = dict(zip((c.id for c in train_part), family_weights(train_part, cfg["weight_power"])))
    by_family: dict[str, list] = {}
    for c in train_part:
        by_family.setdefault(c.family, []).append(c)

    def epoch_rows() -> list:
        cap = cfg["cap"]
        rows = [c for members in by_family.values()
                for c in (rng.sample(members, cap) if cap and len(members) > cap else members)]
        rng.shuffle(rows)
        return rows

    per_epoch = math.ceil(len(epoch_rows()) / cfg["batch"])
    steps = cfg["epochs"] * per_epoch
    schedule = get_linear_schedule_with_warmup(optimizer, int(0.06 * steps), steps)
    loss_fn = torch.nn.CrossEntropyLoss(reduction="none", label_smoothing=cfg["label_smoothing"])
    special = torch.tensor(tokenizer.all_special_ids, device="cuda")
    held_texts, held_labels = [wrap(c.command, cfg) for c in held], targets_of(held, head)
    history, logits_at = [], {}
    last = stop_at or cfg["epochs"]
    for epoch in range(1, last + 1):
        model.train()
        rows, running, started = epoch_rows(), 0.0, time.perf_counter()
        for n, start in enumerate(range(0, len(rows), cfg["batch"]), 1):
            chunk = rows[start:start + cfg["batch"]]
            texts = [augment(c.command, rng) if cfg["aug"] and rng.random() < cfg["aug"] else c.command for c in chunk]
            batch = tokenizer([wrap(t, cfg) for t in texts], padding=True, truncation=True,
                              max_length=cfg["max_tokens"], return_tensors="pt").to("cuda")
            if cfg["mask_prob"]:
                ids = batch["input_ids"]
                swap = (torch.rand(ids.shape, device="cuda") < cfg["mask_prob"]) & ~torch.isin(ids, special)
                batch["input_ids"] = torch.where(swap, tokenizer.mask_token_id, ids)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp):
                logits = model(**batch).logits
            losses = loss_fn(logits.float(), torch.tensor(targets_of(chunk, head), device="cuda"))
            loss = (losses * torch.tensor([weight[c.id] for c in chunk], device="cuda")).mean()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            schedule.step()
            optimizer.zero_grad()
            running += loss.item()
        record = {"epoch": epoch, "train_loss": round(running / n, 4), "seconds": round(time.perf_counter() - started, 1)}
        if (epoch == stop_at) if stop_at else (epoch % cfg["eval_every"] == 0 or epoch == cfg["epochs"]):
            logits_at[str(epoch)] = predict(model, tokenizer, held_texts, amp, max_tokens=cfg["max_tokens"])
            record["held_log_loss"] = round(mean_log_loss(logits_at[str(epoch)], held_labels), 4)
        history.append(record)
        print(f"{cfg['name']}: {record}", flush=True)
    if save_to is not None:
        if head == "binary":
            widen_binary_head(model)
        save_to.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(save_to)
        tokenizer.save_pretrained(save_to)
    return {"history": history, "logits": logits_at, "train_rows": len(train_part), "rows_per_epoch": len(epoch_rows()),
            "parameters": sum(p.numel() for p in model.parameters())}


def widen_binary_head(model) -> None:
    """Turn a not-risky/risky head into five level logits (safe, -, moderate, -, -) so `EncoderScorer` can read it."""
    import torch

    for parent in model.modules():
        for name, child in parent.named_children():
            if isinstance(child, torch.nn.Linear) and child.out_features == 2:
                wide = torch.nn.Linear(child.in_features, 5).to(child.weight.device)
                with torch.no_grad():
                    wide.weight.zero_()
                    wide.bias.fill_(-1e4)
                    wide.weight[0], wide.weight[2] = child.weight[0], child.weight[1]
                    wide.bias[0], wide.bias[2] = child.bias[0], child.bias[1]
                setattr(parent, name, wide)
                model.config.num_labels = 5
                model.num_labels = 5
                return
    raise RuntimeError("no two-way output layer found")


def run_oof(cfg: dict[str, Any], folds_wanted: list[int]) -> list[dict]:
    import torch

    from cmdgate.lab import family_folds

    train, _ = load_data()
    volume.commit()
    folds = family_folds(train, k=5, seed=0)
    if cfg["limit"]:
        picked = set(random.Random(cfg["seed"]).sample(range(len(train)), cfg["limit"]))
    out = []
    for fold in folds_wanted:
        started = time.perf_counter()
        inside = [c for i, (c, f) in enumerate(zip(train, folds)) if f != fold and (not cfg["limit"] or i in picked)]
        held = [c for c, f in zip(train, folds) if f == fold]
        result = fit(cfg, inside, held)
        out.append({"config": cfg, "split": "train-oof", "fold": fold, "ids": [c.id for c in held], **result,
                    "gpu": torch.cuda.get_device_name(0), "seconds": round(time.perf_counter() - started, 1)})
    return out


def run_final(cfg: dict[str, Any], epoch: int, temperature: float) -> dict:
    import torch

    from cmdgate.questions import LEVELS

    train, validation = load_data()
    started = time.perf_counter()
    out_dir = Path(VOLUME) / "models" / f"enc-{cfg['name']}"
    result = fit(cfg, train, validation, stop_at=epoch, save_to=out_dir)
    meta = {**{k: cfg[k] for k in DEFAULTS}, "levels": LEVELS, "batch_size": cfg["batch"], "epoch": epoch,
            "temperature": temperature, "temperature_from": "out-of-fold train families",
            "train_rows": len(train), "history": result["history"], "parameters": result["parameters"]}
    (out_dir / "cmdgate.json").write_text(json.dumps(meta, indent=2))
    volume.commit()
    return {"config": cfg, "split": "validation", "fold": None, "ids": [c.id for c in validation], **result,
            "epoch": epoch, "temperature": temperature, "gpu": torch.cuda.get_device_name(0),
            "seconds": round(time.perf_counter() - started, 1)}


COMMON = {"volumes": {VOLUME: volume}, "timeout": 3 * 3600, "max_containers": 10}


@app.function(gpu="A10G", **COMMON)
def oof_a10g(cfg: dict[str, Any], folds: list[int]) -> list[dict]:
    return run_oof(cfg, folds)


@app.function(gpu="L40S", **COMMON)
def oof_l40s(cfg: dict[str, Any], folds: list[int]) -> list[dict]:
    return run_oof(cfg, folds)


@app.function(gpu="A10G", **COMMON)
def final_a10g(cfg: dict[str, Any], epoch: int, temperature: float) -> dict:
    return run_final(cfg, epoch, temperature)


@app.function(gpu="L40S", **COMMON)
def final_l40s(cfg: dict[str, Any], epoch: int, temperature: float) -> dict:
    return run_final(cfg, epoch, temperature)


# ---- Mac side ----

def write_raw(result: dict) -> Path:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    part = "validation" if result["fold"] is None else f"fold{result['fold']}"
    path = RAW_DIR / f"enc-{result['config']['name']}_{part}.json"
    path.write_text(json.dumps(result))
    return path


@app.local_entrypoint()
def smoke() -> None:
    for result in oof_a10g.remote(resolve("smoke"), [0]):
        print(write_raw(result), result["history"], result["gpu"], f"{result['seconds']}s")


@app.local_entrypoint()
def sweep(names: str, folds: str = "0,1,2", gpu: str = "a10g") -> None:
    fn = {"a10g": oof_a10g, "l40s": oof_l40s}[gpu]
    wanted = [int(f) for f in folds.split(",")]
    jobs = [(resolve(name), wanted) for name in names.split(",")]
    for results in fn.starmap(jobs, order_outputs=False, return_exceptions=True):
        if isinstance(results, Exception):
            print(f"FAILED: {results!r}")
            continue
        for result in results:
            print(write_raw(result), result["gpu"], f"{result['seconds']}s",
                  [(h["epoch"], h["train_loss"], h.get("held_log_loss")) for h in result["history"]])


@app.local_entrypoint()
def final(name: str, epoch: int, temperature: float, gpu: str = "a10g") -> None:
    fn = {"a10g": final_a10g, "l40s": final_l40s}[gpu]
    result = fn.remote(resolve(name), epoch, temperature)
    print(write_raw(result), result["gpu"], f"{result['seconds']}s", result["history"])


# ---- score saved models on other command sets (the hard set, external sources): test only, never trained on ----

@app.function(gpu="A10G", **COMMON)
def score_saved(name: str, commands: dict[str, str], max_tokens: int = 0) -> dict[str, Any]:
    """P(risky) per command id from the model saved as `enc-<name>`, at its saved temperature.

    Also the share of commands longer than the token limit, which the model reads cut off.
    """
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from cmdgate.encoder import softmax
    from cmdgate.questions import risky_probability

    volume.reload()
    path = Path(VOLUME) / "models" / f"enc-{name}"
    meta = json.loads((path / "cmdgate.json").read_text())
    limit = max_tokens or meta.get("max_tokens", MAX_TOKENS)
    tokenizer = AutoTokenizer.from_pretrained(path)
    model = AutoModelForSequenceClassification.from_pretrained(path).to("cuda")
    texts = [wrap(c, meta) for c in commands.values()]
    cut = sum(len(ids) > limit for ids in tokenizer(texts)["input_ids"]) / len(texts)
    logits = predict(model, tokenizer, texts, amp=False, batch_size=32, max_tokens=limit)
    return {"truncated_share": cut, "max_tokens": limit,
            "scores": {i: risky_probability(softmax(row, meta["temperature"])) for i, row in zip(commands, logits)}}


@app.local_entrypoint()
def score(names: str, commands: str, tag: str, max_tokens: int = 0) -> None:
    """Score saved models on a JSON file of {id: command}; writes results/preds/enc-<name>_<tag>.json."""
    rows = json.loads(Path(commands).read_text())
    wanted = names.split(",")
    for name, out in zip(wanted, score_saved.starmap([(n, rows, max_tokens) for n in wanted])):
        path = RAW_DIR.parent / f"enc-{name}_{tag}.json"
        path.write_text(json.dumps(out["scores"]))
        print(path, len(out["scores"]), f"truncated at {out['max_tokens']} tokens: {out['truncated_share']:.3f}")
