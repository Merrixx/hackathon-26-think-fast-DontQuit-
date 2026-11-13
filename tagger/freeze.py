"""Choose the cutoffs on eval for the shipped engine and freeze the design.

    uv run python -m tagger.freeze                         # classic model (default: boosted_trees)
    uv run python -m tagger.freeze --candidate logreg
    uv run python -m tagger.freeze --endpoint http://127.0.0.1:8009/v1/systemone --model kev-0.8b-mine \\
        --checkpoint "runs/mine (trained on data/kev/train_100k.jsonl)"

A classic model is fit on train and saved to models/model.joblib (gitignored,
rebuilt by this command). A decision-API model (a Kev checkpoint served by
kev.serve) is scored over HTTP; record which checkpoint it is with --checkpoint.
Writes frozen/policy.json (tracked). The holdout is not read. After this,
nothing is refit or retuned before tagger.holdout.
"""

from __future__ import annotations

import argparse
import json
import platform
from datetime import datetime, timezone

import joblib
import sklearn

from .config import load
from .data import load_split, read_raw, sha256
from .engine import FROZEN_PATH, MODEL_PATH, HttpEngine, predictions_sha256
from .models import CANDIDATES
from .policy import choose_policy, policy_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--candidate", default="boosted_trees", choices=[c for c in CANDIDATES if c != "constant_rate"])
    parser.add_argument("--endpoint", help="freeze a decision-API model instead of a classic one")
    parser.add_argument("--model", default="local", help="model name sent to --endpoint")
    parser.add_argument("--checkpoint", default="", help="which checkpoint/training data the endpoint serves")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    ds = load()

    dev = load_split(ds, "eval")
    y = dev["label"].to_numpy()
    record = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "dataset": ds.name, "dataset_source": ds.source, "dataset_files": {
                  s: {"path": ds.files[s].as_posix(), "sha256": ds.sha256[s] or None} for s in ("train", "eval")},
              "input_columns": ds.input_columns, "question": ds.question}

    if args.endpoint:
        engine = HttpEngine(ds, args.endpoint, args.model, workers=args.workers)
        p = engine.predict_frame(read_raw(ds.files["eval"]))
        record["engine"] = {**engine.describe(), "checkpoint": args.checkpoint}
        record["model"] = args.model
    else:
        train = load_split(ds, "train")
        model = CANDIDATES[args.candidate](ds).fit(train)
        p = model.predict_proba(dev)
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model.pipeline, MODEL_PATH)
        record["engine"] = {"kind": "local", "artifact": MODEL_PATH.as_posix()}
        record["model"] = args.candidate
        record["model_artifact_sha256"] = sha256(MODEL_PATH)
        record["trained_on"] = f"{ds.files['train'].as_posix()} ({len(train):,} rows)"

    policy = choose_policy(p, y, budget=ds.error_budget, min_support=ds.min_support)
    report = policy_report(p, y, policy)
    pred_hash = predictions_sha256(dev[ds.id], p)
    if FROZEN_PATH.exists():
        previous = json.loads(FROZEN_PATH.read_text())
        if previous.get("model") == record["model"] and previous.get("eval_predictions_sha256") != pred_hash:
            print("WARNING: eval predictions differ from the previously frozen run of this model")
    record.update({
        "cutoffs_chosen_on": f"{ds.files['eval'].as_posix()} ({len(dev):,} rows)",
        "policy": policy.to_dict(),
        "support_rule": f"a tag is enabled only if >= {ds.min_support} eval rows get it and the 95% Wilson "
                        f"upper bound of its eval error is <= {ds.error_budget:.1%}",
        "eval_policy_report_in_sample": report,
        "eval_predictions_sha256": pred_hash,
        "versions": {"python": platform.python_version(), "scikit-learn": sklearn.__version__},
    })
    FROZEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    FROZEN_PATH.write_text(json.dumps(record, indent=2) + "\n")

    def show(cut):
        return "disabled" if cut is None else f"{cut:.6f}"

    print(f"frozen {record['model']}: no if p <= {show(policy.lower)}, yes if p >= {show(policy.upper)}")
    print(f"eval (cutoffs chosen here, so in-sample): {report['no']['tagged']:,} no ({report['no']['wrong']} wrong), "
          f"{report['yes']['tagged']:,} yes ({report['yes']['wrong']} wrong), {report['review']:,} review; "
          f"coverage {report['coverage']:.4f}")
    print(f"saved {FROZEN_PATH}")


if __name__ == "__main__":
    main()
