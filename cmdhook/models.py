"""The models the hook backend can serve, what each needs on disk, and how each is loaded.

| Name | What it is | Needs |
|---|---|---|
| `prior`, `rules` | fitted on the train split when loaded | the CLI Command Risk dataset in `data/raw/` |
| `ngram` | character n-grams | `models/ngram.joblib` |
| `ngram-plus` | n-grams with wider training data | `models/ngram_plus.joblib` |
| `encoder` | the fine-tuned encoder | `models/encoder/` |
| `enc-<name>` | any other fine-tuned encoder | `models/enc-<name>/` |
| `kev` | a Kev checkpoint, served by a Kev checkout that `cmdhook.start` launches | `CMDHOOK_KEV_RUN`, `CMDHOOK_KEV_DIR` |
| an endpoint | an engine that is already running behind the decision API | `CMDHOOK_ENDPOINTS` |

All paths are relative to the repo root; `cmdhook.setup` and `cmdhook.start` run from there.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config

MODELS_DIR = Path("models")
DATASET = Path("data/raw/cli-command-risk/data/train.parquet")
ENCODER_FILES = ("config.json", "model.safetensors", "tokenizer.json", "cmdgate.json")
KEV = "kev"

# The libraries a kind of model imports, and the command that makes its weights.
NEEDS = {
    "fitted": (("pandas", "pyarrow"), "uv run --extra ml python -m cmdgate.fetch_data"),
    "ngram": (("sklearn", "joblib"), "uv run --extra ml python -m cmdgate.ngram"),
    "ngram-plus": (("sklearn", "joblib"), "uv run --extra ml python -m cmdgate.crosseval --train-plus"),
    "encoder": (("torch", "transformers"), "uv run --extra ml python -m cmdgate.encoder"),
    "kev": ((), "see 'Fine-tuning Kev on a cloud GPU' in the README"),
    "endpoint": ((), ""),
}


@dataclass(frozen=True)
class Model:
    name: str
    kind: str
    path: Path | None = None
    url: str | None = None

    @property
    def in_process(self) -> bool:
        return self.url is None

    def problems(self, config: Config) -> list[str]:
        """Why this model cannot be served right now; empty when it is ready."""
        libraries, remedy = NEEDS[self.kind]
        found = [f"Python package {lib!r} is not installed (run with `uv run --extra ml`)"
                 for lib in libraries if importlib.util.find_spec(lib) is None]
        hint = f" ({remedy})" if remedy else ""
        if self.kind in ("fitted", "ngram", "ngram-plus") and not self.path.is_file():
            found.append(f"{self.path} is missing{hint}")
        elif self.kind == "encoder":
            found.extend(_encoder_problems(self.path, hint))
        elif self.kind == "kev":
            found.extend(_kev_problems(config, hint))
        return found


def _encoder_problems(path: Path, hint: str) -> list[str]:
    if not path.is_dir():
        return [f"{path}/ is missing{hint}"]
    found = [f"{path / name} is missing" for name in ENCODER_FILES if not (path / name).is_file()]
    tokenizer = path / "tokenizer.json"
    if tokenizer.is_file():
        try:
            json.loads(tokenizer.read_bytes())
        except ValueError:  # a download that was cut off leaves a file that does not parse
            found.append(f"{tokenizer} is damaged, not valid JSON: fetch the model folder again")
    meta = path / "cmdgate.json"
    if meta.is_file():
        try:
            saved = json.loads(meta.read_text())
        except ValueError:
            return [*found, f"{meta} is not valid JSON"]
        if "temperature" not in saved:
            found.append(f"{meta} has no temperature")
        if saved.get("prompt"):
            found.append(f"{path} was trained on a prompt-wrapped command, which this backend does not serve")
    return found


def _kev_problems(config: Config, hint: str) -> list[str]:
    found = []
    if not config.kev_run:
        found.append(f"CMDHOOK_KEV_RUN is not set to a Kev checkpoint, such as runs/<name>/checkpoint{hint}")
    elif not Path(config.kev_run).exists():
        found.append(f"Kev checkpoint {config.kev_run} is missing{hint}")
    if not (Path(config.kev_dir) / "kev" / "serve.py").is_file():
        found.append(f"no Kev checkout at CMDHOOK_KEV_DIR={config.kev_dir}")
    if shutil.which("uv") is None:
        found.append("uv is not on PATH; it starts the Kev server")
    return found


def kev_url(config: Config) -> str:
    return f"http://127.0.0.1:{config.kev_port}/v1/systemone"


def resolve(name: str, config: Config) -> Model:
    """The model behind a name in the settings. Raises `ValueError` for a name that is none of the above."""
    if name in config.endpoints:
        return Model(name, "endpoint", url=config.endpoints[name])
    if name == KEV:
        return Model(name, "kev", url=kev_url(config))
    if name in ("prior", "rules"):
        return Model(name, "fitted", DATASET)
    if name == "ngram":
        return Model(name, "ngram", MODELS_DIR / "ngram.joblib")
    if name == "ngram-plus":
        return Model(name, "ngram-plus", MODELS_DIR / "ngram_plus.joblib")
    if name == "encoder" or name.startswith("enc-"):
        return Model(name, "encoder", MODELS_DIR / name)
    raise ValueError(f"unknown model {name!r}; on disk: {', '.join(on_disk()) or 'none'}")


def selected(config: Config) -> list[Model]:
    return [resolve(name, config) for name in config.names]


def on_disk() -> list[str]:
    """Every in-process model whose files are present."""
    names = ["prior", "rules"] if DATASET.is_file() else []
    names += [name for name, file in (("ngram", "ngram.joblib"), ("ngram-plus", "ngram_plus.joblib"))
              if (MODELS_DIR / file).is_file()]
    if MODELS_DIR.is_dir():
        names += sorted(p.name for p in MODELS_DIR.iterdir()
                        if (p.name == "encoder" or p.name.startswith("enc-")) and (p / "model.safetensors").is_file())
    return names


def kev_checkpoints(runs: Path = Path("runs")) -> list[str]:
    """Kev checkpoints on disk, as values for `CMDHOOK_KEV_RUN`."""
    return sorted(str(p) for p in runs.glob("*/checkpoint") if p.is_dir())


def load(model: Model, train: list | None = None) -> Any:
    """The scorer for one model. `train` is the train split, shared by the models fitted on it."""
    from cmdgate.scorers import EndpointScorer, load_scorer

    if not model.in_process:
        return EndpointScorer(model.url)
    if model.kind == "fitted":
        return load_scorer(model.name, train)
    if model.kind == "ngram":
        from cmdgate.ngram import NgramScorer
        return NgramScorer(model.path)
    if model.kind == "ngram-plus":
        from cmdgate.crosseval import load_ngram_plus
        return load_ngram_plus(model.path)
    from cmdgate.encoder import EncoderScorer
    return EncoderScorer(model.path)


def load_all(models: list[Model]) -> dict[str, Any]:
    """A scorer per model, in the order given. The train split is read once, and only if a model needs it."""
    train = None
    if any(m.kind == "fitted" for m in models):
        from cmdgate.dataset import load_split
        train = load_split("train")
    return {m.name: load(m, train) for m in models}
