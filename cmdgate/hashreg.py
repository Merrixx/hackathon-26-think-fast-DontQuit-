"""Hashed bag-of-features logistic baseline (distinct from char-TF-IDF n-grams).

    uv run --extra ml python -m cmdgate.hashreg

Uses ``HashingVectorizer`` word + character features into a multinomial logistic
regression over the five risk levels. Regularisation ``C`` is chosen on the
**validation** split by log loss (same protocol as ``cmdgate.ngram``). Training
rows come from the CLI Command Risk **train** split only; the published test
split is never used for fitting.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .dataset import Command, load_all
from .questions import LEVELS

MODEL_PATH = Path("models/hashreg.joblib")
C_GRID = (0.1, 0.3, 1.0, 3.0, 10.0)


def build_pipeline(c: float):
    from sklearn.feature_extraction.text import HashingVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import FeatureUnion, make_pipeline

    # Distinct from cmdgate.ngram (TfidfVectorizer char 1–5): fixed hashing, mixed
    # word + char views, no IDF, no vocabulary growth.
    features = FeatureUnion(
        [
            (
                "word",
                HashingVectorizer(
                    analyzer="word",
                    ngram_range=(1, 2),
                    n_features=2**18,
                    alternate_sign=False,
                    lowercase=True,
                    token_pattern=r"(?u)\b\w+\b|[^\w\s]",
                ),
            ),
            (
                "char",
                HashingVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    n_features=2**18,
                    alternate_sign=False,
                    lowercase=False,
                ),
            ),
        ]
    )
    return make_pipeline(
        features,
        LogisticRegression(C=c, max_iter=2000, solver="saga"),
    )


def train(train_set: list[Command], validation: list[Command]):
    """Pipeline with lowest validation log loss, and that loss per C."""
    from sklearn.metrics import log_loss

    losses: dict[float, float] = {}
    best = None
    x_train = [x.command for x in train_set]
    y_train = [x.level for x in train_set]
    x_val = [x.command for x in validation]
    y_val = [x.level for x in validation]
    labels = list(range(len(LEVELS)))
    for c in C_GRID:
        model = build_pipeline(c).fit(x_train, y_train)
        probs = model.predict_proba(x_val)
        losses[c] = log_loss(y_val, probs, labels=labels)
        if best is None or losses[c] < losses[best[0]]:
            best = (c, model)
    assert best is not None
    return best[1], losses


class HashRegScorer:
    def __init__(self, path: Path = MODEL_PATH) -> None:
        import joblib

        self.model = joblib.load(path)

    def levels(self, command: str) -> list[float]:
        return self.batch_levels([command])[0]

    def batch_levels(self, commands: list[str]) -> list[list[float]]:
        return self.model.predict_proba(commands).tolist()


def main() -> None:
    import joblib

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=MODEL_PATH)
    args = parser.parse_args()
    splits = load_all()
    model, losses = train(splits["train"], splits["validation"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.out)
    print("validation log loss by C:", {c: round(v, 4) for c, v in losses.items()})
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
