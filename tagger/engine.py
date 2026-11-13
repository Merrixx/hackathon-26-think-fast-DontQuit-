"""Decision engines behind one interface: a local scikit-learn model or any decision API.

Both take raw input rows (text columns, as in the CSV) and return the
probability that the answer to the dataset question is yes. The HTTP engine is
how Kev checkpoints (served by `kev.serve` on port 8009) plug into the same
freeze, batch and score commands as the classic models.
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from .config import Dataset
from .data import build_features, sha256
from .policy import Policy

MODEL_PATH = Path("models/model.joblib")
FROZEN_PATH = Path("frozen/policy.json")
QUESTION_ID = "answer"
DEFAULT_ENDPOINT = "http://127.0.0.1:8009/v1/systemone"


def predictions_sha256(ids, p: np.ndarray) -> str:
    """Stable fingerprint of a prediction set, to show reruns reproduce it."""
    text = "\n".join(f"{i},{v:.10f}" for i, v in zip(ids, p))
    return hashlib.sha256(text.encode()).hexdigest()


def question(ds: Dataset) -> dict:
    return {"type": "noul", "instructions": ds.question}


def to_state(ds: Dataset, row: dict) -> dict:
    """What a decision API sees for one row: the feature values as text. No ID, no label."""
    return {c: "" if row.get(c) is None else str(row.get(c)) for c in ds.features}


class LocalEngine:
    kind = "local"

    def __init__(self, ds: Dataset, pipeline):
        self.ds, self.pipeline = ds, pipeline

    def predict_frame(self, raw: pd.DataFrame) -> np.ndarray:
        features = build_features(self.ds, raw)
        return self.pipeline.predict_proba(features[self.ds.features])[:, 1]

    def describe(self) -> dict:
        return {"kind": self.kind, "artifact": MODEL_PATH.as_posix()}


class HttpEngine:
    """Calls POST /v1/systemone once per row. A failed call raises: no silent 0.5 guesses."""

    kind = "http"

    def __init__(self, ds: Dataset, endpoint: str = DEFAULT_ENDPOINT, model: str = "local",
                 workers: int = 4, timeout: float = 60.0):
        self.ds, self.endpoint, self.model, self.workers, self.timeout = ds, endpoint, model, workers, timeout

    def predict_one(self, row: dict) -> float:
        body = {"model": self.model, "state": to_state(self.ds, row), "questions": {QUESTION_ID: question(self.ds)}}
        request = urllib.request.Request(self.endpoint, data=json.dumps(body).encode(),
                                         headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            answer = json.load(response)["answers"][QUESTION_ID]
        return min(1.0, max(0.0, float(answer["noul"])))

    def predict_frame(self, raw: pd.DataFrame) -> np.ndarray:
        rows = raw[self.ds.input_columns].to_dict("records")
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            return np.array(list(pool.map(self.predict_one, rows)), dtype=float)

    def describe(self) -> dict:
        return {"kind": self.kind, "endpoint": self.endpoint, "model": self.model}


def load_frozen(ds: Dataset) -> tuple[LocalEngine | HttpEngine, Policy, dict]:
    if not FROZEN_PATH.exists():
        raise SystemExit("no frozen design: run `uv run python -m tagger.freeze` first")
    meta = json.loads(FROZEN_PATH.read_text())
    if meta["dataset"] != ds.name:
        raise SystemExit(f"{FROZEN_PATH} was frozen for dataset {meta['dataset']!r}, not {ds.name!r}")
    policy = Policy(**meta["policy"])
    engine_meta = meta["engine"]
    if engine_meta["kind"] == "http":
        return HttpEngine(ds, engine_meta["endpoint"], engine_meta["model"]), policy, meta
    actual = sha256(MODEL_PATH) if MODEL_PATH.exists() else None
    if actual != meta["model_artifact_sha256"]:
        raise SystemExit(f"{MODEL_PATH} is missing or does not match the frozen record; rerun tagger.freeze")
    # joblib unpickles, so only load the artifact tagger.freeze wrote on this machine,
    # after its hash matched the frozen record above. Never load a downloaded one.
    return LocalEngine(ds, joblib.load(MODEL_PATH)), policy, meta
