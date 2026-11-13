"""Read and validate dataset.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path("dataset.toml")
SPLITS = ("train", "eval", "holdout")
DEFAULT_POSITIVE = ["1", "1.0", "true", "yes"]
DEFAULT_NEGATIVE = ["0", "0.0", "false", "no"]


class ConfigError(SystemExit):
    pass


@dataclass(frozen=True)
class Dataset:
    name: str
    description: str
    source: str
    licence: str
    question: str
    files: dict[str, Path]
    sha256: dict[str, str]
    rows: dict[str, int]
    base_url: str
    id: str
    target: str
    positive: tuple[str, ...]
    negative: tuple[str, ...]
    numeric: tuple[str, ...]
    categorical: tuple[str, ...]
    log1p: tuple[str, ...]
    lookup: str
    stratify: str
    error_budget: float
    min_support: int
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def features(self) -> list[str]:
        return list(self.numeric) + list(self.categorical)

    @property
    def input_columns(self) -> list[str]:
        """The only columns inference may read: the record ID and the features."""
        return [self.id] + self.features


def load(path: Path = CONFIG_PATH) -> Dataset:
    if not path.exists():
        raise ConfigError(f"{path} not found; copy the template from the repo and fill it in")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    ds, files, cols, policy = (raw.get(k, {}) for k in ("dataset", "files", "columns", "policy"))
    dataset = Dataset(
        name=ds.get("name", ""), description=ds.get("description", ""), source=ds.get("source", ""),
        licence=ds.get("licence", ""), question=ds.get("question", ""),
        files={s: Path(files.get(s, "")) for s in SPLITS},
        sha256={s: files.get("sha256", {}).get(s, "") for s in SPLITS},
        rows={s: int(files.get("rows", {}).get(s, 0)) for s in SPLITS},
        base_url=raw.get("download", {}).get("base_url", ""),
        id=cols.get("id", ""), target=cols.get("target", ""),
        positive=tuple(str(v).lower() for v in cols.get("positive", DEFAULT_POSITIVE)),
        negative=tuple(str(v).lower() for v in cols.get("negative", DEFAULT_NEGATIVE)),
        numeric=tuple(cols.get("numeric", [])), categorical=tuple(cols.get("categorical", [])),
        log1p=tuple(cols.get("log1p", [])), lookup=cols.get("lookup", ""), stratify=cols.get("stratify", ""),
        error_budget=float(policy.get("error_budget", 0.01)), min_support=int(policy.get("min_support", 500)),
        raw=raw,
    )
    validate(dataset)
    return dataset


def validate(d: Dataset) -> None:
    problems = []
    for name in ("name", "question", "id", "target", "lookup"):
        if not getattr(d, name):
            problems.append(f"set `{name}`")
    if not d.features:
        problems.append("list at least one column under `numeric` or `categorical`")
    if d.lookup and d.lookup not in d.features:
        problems.append("`lookup` must be one of the input columns")
    if set(d.log1p) - set(d.numeric):
        problems.append("`log1p` columns must also be listed under `numeric`")
    if set(d.numeric) & set(d.categorical):
        problems.append("a column cannot be both numeric and categorical")
    leaked = {d.id, d.target} & set(d.features)
    if leaked:
        problems.append(f"{sorted(leaked)} cannot be an input column")
    if set(d.positive) & set(d.negative) or not d.positive or not d.negative:
        problems.append("`positive` and `negative` label values must be non-empty and disjoint")
    if not 0 < d.error_budget < 0.5:
        problems.append("`error_budget` must be between 0 and 0.5")
    if problems:
        raise ConfigError("dataset.toml is not ready:\n  - " + "\n  - ".join(problems))
