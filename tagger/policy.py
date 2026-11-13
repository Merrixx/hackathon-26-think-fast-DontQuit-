"""Two-cutoff tagging policy, chosen on dev data and then frozen.

    p <= lower  -> tag "no"       (automatic)
    p >= upper  -> tag "yes"      (automatic)
    otherwise   -> "review"       (sent to a person)

p is the model's probability that the answer to the dataset question is yes.

Each side is chosen separately to tag as many dev rows as possible while the
share of wrong tags on that side stays within the error budget. Because both
sides respect the budget, the combined automatic error does too.

Support rule (fixed before any holdout scoring): a tag is enabled only if at
least MIN_SUPPORT dev rows receive it AND the 95% Wilson upper bound of its dev
error rate is within the budget. The bound guards against cutoffs that only
look safe because few rows sit beyond them.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

ERROR_BUDGET = 0.01  # defaults; dataset.toml [policy] overrides them
MIN_SUPPORT = 500
Z_95 = 1.959964

NO, YES, REVIEW = "no", "yes", "review"


@dataclass(frozen=True)
class Policy:
    lower: float | None  # None: "no" tag disabled
    upper: float | None  # None: "yes" tag disabled
    budget: float = ERROR_BUDGET
    min_support: int = MIN_SUPPORT

    def tag(self, p: float) -> str:
        if self.lower is not None and p <= self.lower:
            return NO
        if self.upper is not None and p >= self.upper:
            return YES
        return REVIEW

    def tags(self, p: np.ndarray) -> np.ndarray:
        out = np.full(len(p), REVIEW, dtype=object)
        if self.lower is not None:
            out[p <= self.lower] = NO
        if self.upper is not None:
            out[p >= self.upper] = YES
        return out

    def to_dict(self) -> dict:
        return asdict(self)


def wilson_upper(errors: int, n: int, z: float = Z_95) -> float:
    if n == 0:
        return 1.0
    phat = errors / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    spread = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (centre + spread) / denom


def _best_prefix(scores: np.ndarray, wrong: np.ndarray, budget: float, min_support: int) -> tuple[int, float | None]:
    """Largest prefix (in the given order) that meets the support rule.

    Rows with equal scores are taken or left together, since a cutoff cannot
    split them. Returns (rows tagged, cutoff score) or (0, None).
    """
    errors = np.cumsum(wrong)
    n = np.arange(1, len(scores) + 1)
    # last index of each run of equal scores: the only valid prefix ends
    ends = np.flatnonzero(np.append(scores[1:] != scores[:-1], True))
    best_k, best_cut = 0, None
    for i in ends:
        k, e = int(n[i]), int(errors[i])
        if k >= min_support and wilson_upper(e, k) <= budget:
            best_k, best_cut = k, float(scores[i])
    return best_k, best_cut


def choose_policy(p: np.ndarray, y: np.ndarray, budget: float = ERROR_BUDGET,
                  min_support: int = MIN_SUPPORT) -> Policy:
    """Pick both cutoffs on dev data. y is 1 for a yes."""
    p, y = np.asarray(p, dtype=float), np.asarray(y, dtype=int)
    asc = np.argsort(p, kind="stable")
    _, lower = _best_prefix(p[asc], y[asc] == 1, budget, min_support)
    desc = asc[::-1]
    _, upper = _best_prefix(p[desc], y[desc] == 0, budget, min_support)
    if lower is not None and upper is not None and lower >= upper:
        upper = None  # cannot happen with a sane budget; keep the tags disjoint anyway
    return Policy(lower=lower, upper=upper, budget=budget, min_support=min_support)


def policy_report(p: np.ndarray, y: np.ndarray, policy: Policy) -> dict:
    """Counts, coverage and selective error. Zero denominators give None (N/A)."""
    tags = policy.tags(np.asarray(p, dtype=float))
    y = np.asarray(y, dtype=int)

    def side(tag: str, wrong_label: int) -> dict:
        mask = tags == tag
        n, wrong = int(mask.sum()), int((y[mask] == wrong_label).sum())
        return {"tagged": n, "wrong": wrong, "error": wrong / n if n else None,
                "error_wilson_upper_95": wilson_upper(wrong, n) if n else None}

    no_side, yes_side = side(NO, 1), side(YES, 0)
    automatic = no_side["tagged"] + yes_side["tagged"]
    wrong = no_side["wrong"] + yes_side["wrong"]
    review = int((tags == REVIEW).sum())
    return {
        "rows": int(len(y)),
        "no": no_side,
        "yes": yes_side,
        "review": review,
        "review_positives": int(y[tags == REVIEW].sum()),
        "automatic": automatic,
        "wrong_automatic": wrong,
        "coverage": automatic / len(y) if len(y) else None,
        "selective_error": wrong / automatic if automatic else None,
    }
