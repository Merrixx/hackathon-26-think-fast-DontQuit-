"""Post-hoc calibrate Kev validation answers (temperature + isotonic).

    uv run --extra ml python -m cmdgate.calibrate_kev \\
      --answers results/kev08_validation/answers.jsonl \\
      --suite data/cmdrisk_validation.jsonl \\
      --out results/kev08_calibration.json

Fits on the validation split only (same split used for reporting). Does not
touch the published test set or retrain the model.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression

from thinkfast.metrics import Decision, automation_rate, summarize
from thinkfast.suite import read_jsonl

EPS = 1e-6


def load_pairs(suite_path: Path, answers_path: Path) -> tuple[list[dict], np.ndarray, np.ndarray, list[str]]:
    items = read_jsonl(suite_path)
    records = [json.loads(line) for line in answers_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(items) != len(records):
        raise SystemExit(f"suite/answers length mismatch: {len(items)} vs {len(records)}")
    p_true = []
    y_true = []
    families = []
    for item, rec in zip(items, records):
        q = item["request"]["questions"]["risky"]
        answer = (rec.get("answers") or {}).get("risky")
        p = 0.5 if not isinstance(answer, dict) else float(answer.get("noul", 0.5))
        p = min(1.0, max(0.0, p))
        label = bool(item["labels"]["risky"])
        p_true.append(p)
        y_true.append(1.0 if label else 0.0)
        families.append(item["family"])
    return items, np.asarray(p_true, dtype=np.float64), np.asarray(y_true, dtype=np.float64), families


def decisions_from(p_true: np.ndarray, y_true: np.ndarray, families: list[str]) -> list[Decision]:
    out: list[Decision] = []
    for p, y, fam in zip(p_true, y_true, families):
        p = float(min(1.0, max(0.0, p)))
        label = "true" if y >= 0.5 else "false"
        out.append(Decision(fam, "noul", {"true": p, "false": 1.0 - p}, label))
    return out


def nll(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, EPS, 1.0 - EPS)
    return float(-(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)).mean())


def apply_temperature(p: np.ndarray, temperature: float) -> np.ndarray:
    p = np.clip(p, EPS, 1.0 - EPS)
    logit = np.log(p / (1.0 - p))
    scaled = 1.0 / (1.0 + np.exp(-logit / temperature))
    return scaled


def fit_temperature(p: np.ndarray, y: np.ndarray) -> float:
    """Minimize binary NLL over T > 0 (grid + refine)."""
    best_t, best_loss = 1.0, nll(p, y)
    for t in np.geomspace(0.05, 20.0, 80):
        loss = nll(apply_temperature(p, float(t)), y)
        if loss < best_loss:
            best_t, best_loss = float(t), loss
    # local refine
    lo, hi = best_t / 1.5, best_t * 1.5
    for t in np.linspace(lo, hi, 60):
        loss = nll(apply_temperature(p, float(t)), y)
        if loss < best_loss:
            best_t, best_loss = float(t), loss
    return best_t


def fit_isotonic(p: np.ndarray, y: np.ndarray) -> IsotonicRegression:
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(p, y)
    return iso


def pack(name: str, p: np.ndarray, y: np.ndarray, families: list[str], extra: dict) -> dict:
    dec = decisions_from(p, y, families)
    stats = summarize(dec)
    rate, threshold = automation_rate(dec)
    return {
        "name": name,
        "nll": nll(p, y),
        "accuracy": stats["accuracy"],
        "brier": stats["brier"],
        "ece": stats["ece"],
        "automation_rate_at_5pct": rate,
        "automation_threshold": threshold,
        **extra,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path("data/cmdrisk_validation.jsonl"))
    parser.add_argument("--answers", type=Path, default=Path("results/kev08_validation/answers.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("results/kev08_calibration.json"))
    parser.add_argument("--report", type=Path, default=Path("REPORT.md"))
    args = parser.parse_args()

    items, p_raw, y, families = load_pairs(args.suite, args.answers)
    raw = pack("raw", p_raw, y, families, {})

    t = fit_temperature(p_raw, y)
    p_temp = apply_temperature(p_raw, t)
    temp = pack("temperature", p_temp, y, families, {"temperature": t})

    iso = fit_isotonic(p_raw, y)
    p_iso = np.asarray(iso.predict(p_raw), dtype=np.float64)
    isotonic = pack("isotonic", p_iso, y, families, {})

    # Winner: higher automation @ 5%; ties → lower NLL, then lower ECE
    candidates = [temp, isotonic]
    winner = max(
        candidates,
        key=lambda r: (r["automation_rate_at_5pct"], -r["nll"], -r["ece"]),
    )

    payload = {
        "split": "validation",
        "n": len(items),
        "note": (
            "Post-hoc calibration fit and scored on the same validation split. "
            "Not the published test set. Raw Kev answers are extremely peaked at 0/1."
        ),
        "raw": raw,
        "temperature": temp,
        "isotonic": isotonic,
        "winner": winner["name"],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))

    # Patch REPORT.md
    marker = "<!-- kev08_calibration -->"
    block = "\n".join(
        [
            marker,
            "",
            "### Kev-0.8B post-hoc calibration (validation)",
            "",
            "Raw Kev probabilities are almost all 0 or 1, so confidence ranking cannot "
            f"automate anything at a 5% error budget (**{100 * raw['automation_rate_at_5pct']:.1f}%** automated). "
            "Temperature scaling and isotonic regression were fit on this same validation split "
            "(not the test set; optimistic vs a held-out calib slice).",
            "",
            "| Method | Accuracy | Brier | ECE | Automated at 5% errors | Confidence threshold |",
            "|---|---|---|---|---|---|",
            (
                f"| Raw (as served) | {raw['accuracy']:.3f} | {raw['brier']:.3f} | {raw['ece']:.3f} | "
                f"{100 * raw['automation_rate_at_5pct']:.1f}% | {raw['automation_threshold']:.4f} |"
            ),
            (
                f"| Temperature (T={temp['temperature']:.3f}) | {temp['accuracy']:.3f} | {temp['brier']:.3f} | "
                f"{temp['ece']:.3f} | {100 * temp['automation_rate_at_5pct']:.1f}% | {temp['automation_threshold']:.4f} |"
            ),
            (
                f"| Isotonic regression | {isotonic['accuracy']:.3f} | {isotonic['brier']:.3f} | "
                f"{isotonic['ece']:.3f} | {100 * isotonic['automation_rate_at_5pct']:.1f}% | "
                f"{isotonic['automation_threshold']:.4f} |"
            ),
            "",
            f"**Winner:** {winner['name']} — "
            f"**{100 * winner['automation_rate_at_5pct']:.1f}%** automated at 5% errors "
            f"(threshold confidence ≥ **{winner['automation_threshold']:.4f}**), "
            f"up from **{100 * raw['automation_rate_at_5pct']:.1f}%** raw. "
            f"Machine-readable: `results/kev08_calibration.json`.",
            "",
        ]
    )

    text = args.report.read_text(encoding="utf-8")
    if marker in text:
        pre, rest = text.split(marker, 1)
        # drop old block until next ## heading or EOF
        cut = rest.find("\n## ")
        text = pre + block + (rest[cut:] if cut >= 0 else "")
    else:
        needle = "Full report: `results/kev08_validation/report.json`."
        if needle in text:
            text = text.replace(needle, needle + "\n\n" + block)
        else:
            text = text + "\n" + block
    args.report.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"updated {args.report}")


if __name__ == "__main__":
    main()
