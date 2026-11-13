"""Simple learned baseline: character n-grams into a logistic regression.

    uv run --extra ml python -m cmdgate.ngram            # trains, saves models/ngram.joblib

It predicts the five risk levels from the command string alone. The
regularisation strength is chosen on the validation split by log loss.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .dataset import Command, load_all
from .questions import LEVELS

MODEL_PATH = Path("models/ngram.joblib")
C_GRID = (0.3, 1.0, 3.0, 10.0, 30.0)


def build_pipeline(c: float):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline

    return make_pipeline(
        TfidfVectorizer(analyzer="char", ngram_range=(1, 5), sublinear_tf=True, min_df=2, lowercase=False),
        LogisticRegression(C=c, max_iter=2000),
    )


def train(train_set: list[Command], validation: list[Command]):
    """The pipeline with the lowest validation log loss, and that loss per C tried."""
    from sklearn.metrics import log_loss

    losses = {}
    best = None
    for c in C_GRID:
        model = build_pipeline(c).fit([x.command for x in train_set], [x.level for x in train_set])
        probs = model.predict_proba([x.command for x in validation])
        losses[c] = log_loss([x.level for x in validation], probs, labels=list(range(len(LEVELS))))
        if best is None or losses[c] < losses[best[0]]:
            best = (c, model)
    return best[1], losses


class NgramScorer:
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
