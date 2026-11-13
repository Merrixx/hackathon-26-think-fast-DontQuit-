"""Load the configured dataset and turn raw rows into model inputs.

Everything is read as text and converted here, so CSV files, seed files and API
requests all go through the same feature code. Inference only ever receives
the allowlisted input columns (ID + features); labels are read separately.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Dataset

MISSING = "__missing__"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_raw(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise SystemExit(f"{path} not found; put the dataset files where dataset.toml says (see data/README.md)")
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def check_file(ds: Dataset, split: str, raw: pd.DataFrame | None = None) -> list[str]:
    """Problems with one split file against its published checksum and row count."""
    path = ds.files[split]
    if not path.exists():
        return [f"{path} missing"]
    problems = []
    if ds.sha256[split] and sha256(path) != ds.sha256[split]:
        problems.append(f"{path} sha256 {sha256(path)} != expected {ds.sha256[split]}")
    if ds.rows[split]:
        n = len(raw) if raw is not None else len(read_raw(path))
        if n != ds.rows[split]:
            problems.append(f"{path} has {n:,} data rows, expected {ds.rows[split]:,}")
    return problems


def load_split(ds: Dataset, split: str) -> pd.DataFrame:
    """Features plus `label` for a public split. The holdout is refused."""
    if split == "holdout":
        raise PermissionError("the holdout is read only by `tagger.holdout`, after the design is frozen")
    raw = read_raw(ds.files[split])
    problems = check_file(ds, split, raw)
    if problems:
        raise SystemExit("; ".join(problems))
    missing = [c for c in ds.input_columns + [ds.target] if c not in raw.columns]
    if missing:
        raise SystemExit(f"{ds.files[split]} lacks columns {missing}; headers are {list(raw.columns)}")
    if raw[ds.id].duplicated().any():
        raise SystemExit(f"{ds.files[split]}: column {ds.id!r} is not a unique ID")
    df = build_features(ds, raw)
    df["label"] = parse_labels(ds, raw[ds.target])
    return df


def parse_labels(ds: Dataset, values: pd.Series) -> np.ndarray:
    text = values.astype(str).str.strip().str.lower()
    unknown = sorted(set(text) - set(ds.positive) - set(ds.negative))
    if unknown:
        raise SystemExit(f"label values {unknown[:10]} are neither `positive` nor `negative` in dataset.toml")
    return text.isin(ds.positive).astype(int).to_numpy()


def category_code(value) -> str:
    """Normalise a category so '1', '1.0' and 1 are the same code; blanks become MISSING."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return MISSING
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return MISSING
    try:
        number = float(text)
    except ValueError:
        return text
    return str(int(number)) if number.is_integer() else text


def build_features(ds: Dataset, raw: pd.DataFrame) -> pd.DataFrame:
    """Model inputs from the allowlisted input columns. Never reads the label."""
    missing = [c for c in ds.input_columns if c not in raw.columns]
    if missing:
        raise KeyError(f"input lacks columns {missing}")
    raw = raw[ds.input_columns]
    df = pd.DataFrame(index=raw.index)
    df[ds.id] = raw[ds.id].astype(str)
    for col in ds.numeric:
        values = pd.to_numeric(raw[col], errors="coerce")
        df[col] = np.log1p(values.clip(lower=0)) if col in ds.log1p else values
    for col in ds.categorical:
        df[col] = raw[col].map(category_code)
    return df
