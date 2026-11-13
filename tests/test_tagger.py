"""Tests for the dataset-agnostic tagger: config, policy and the freeze -> batch -> score path.

The pipeline test builds a tiny throwaway fixture in a temp folder. It checks
plumbing only and is not a dataset; real data goes in data/raw/ (data/README.md).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from tagger import config as config_mod
from tagger.data import MISSING, category_code
from tagger.policy import NO, REVIEW, YES, Policy, choose_policy, policy_report, wilson_upper

CONFIG = """
[dataset]
name = "fixture"
question = "Is the answer yes?"
[files]
train = "data/raw/train.csv"
eval = "data/raw/eval.csv"
holdout = "data/holdout/holdout.csv"
[columns]
id = "row_id"
target = "label"
numeric = ["amount", "score"]
categorical = ["kind"]
log1p = ["amount"]
lookup = "kind"
[policy]
error_budget = 0.05
min_support = 20
"""


def write_split(path: Path, n: int, seed: int, prefix: str) -> None:
    rng = np.random.default_rng(seed)
    score = rng.uniform(0, 1, n)
    kind = rng.choice(["a", "b", "c"], n)
    label = (rng.uniform(0, 1, n) < 0.05 + 0.9 * score ** 3).astype(int)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"row_id": [f"{prefix}{i}" for i in range(n)], "amount": rng.integers(0, 10_000, n),
                  "score": score.round(4), "kind": kind, "label": label}).to_csv(path, index=False)


def run(module, argv):
    """Run a tagger entry point with arguments, returning its printed output."""
    out = io.StringIO()
    old = sys.argv
    sys.argv = [module.__name__] + argv
    try:
        with contextlib.redirect_stdout(out):
            module.main()
    finally:
        sys.argv = old
    return out.getvalue()


class ConfigTest(unittest.TestCase):
    def test_template_is_rejected_until_filled(self):
        with self.assertRaises(SystemExit) as ctx:
            config_mod.load(Path("dataset.toml"))
        self.assertIn("not ready", str(ctx.exception))

    def test_label_cannot_be_a_feature(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.toml"
            path.write_text(CONFIG.replace('categorical = ["kind"]', 'categorical = ["kind", "label"]'))
            with self.assertRaises(SystemExit):
                config_mod.load(path)

    def test_category_codes(self):
        self.assertEqual(category_code("1.0"), "1")
        self.assertEqual(category_code(" "), MISSING)
        self.assertEqual(category_code(float("nan")), MISSING)
        self.assertEqual(category_code("CA"), "CA")


class PolicyTest(unittest.TestCase):
    def test_cutoffs_respect_budget_and_support(self):
        rng = np.random.default_rng(0)
        p = rng.uniform(0, 1, 5000)
        y = (rng.uniform(0, 1, 5000) < p).astype(int)
        policy = choose_policy(p, y, budget=0.05, min_support=100)
        report = policy_report(p, y, policy)
        for side in (YES, NO):
            if report[side]["tagged"]:
                self.assertLessEqual(report[side]["error_wilson_upper_95"], 0.05)
                self.assertGreaterEqual(report[side]["tagged"], 100)
        self.assertEqual(report["automatic"] + report["review"], 5000)

    def test_no_qualifying_policy_defers_everything(self):
        p, y = np.full(1000, 0.5), np.tile([0, 1], 500)
        policy = choose_policy(p, y, budget=0.01, min_support=10)
        self.assertEqual((policy.lower, policy.upper), (None, None))
        report = policy_report(p, y, policy)
        self.assertEqual(report["review"], 1000)
        self.assertEqual(report["coverage"], 0.0)
        self.assertIsNone(report["selective_error"])
        self.assertIsNone(report[YES]["error"])

    def test_ties_are_not_split(self):
        p = np.array([0.1] * 50 + [0.2] * 50)
        y = np.array([0] * 50 + [0] * 49 + [1])
        policy = choose_policy(p, y, budget=0.5, min_support=10)
        tags = policy.tags(p)
        self.assertEqual(len(set(tags[:50])), 1)
        self.assertEqual(len(set(tags[50:])), 1)

    def test_wilson_upper_bound(self):
        self.assertAlmostEqual(wilson_upper(0, 1000), 0.00383, places=4)
        self.assertGreater(wilson_upper(10, 1000), 0.01)

    def test_tag_and_tags_agree(self):
        policy = Policy(lower=0.1, upper=0.9)
        p = np.array([0.05, 0.1, 0.5, 0.9, 0.95])
        self.assertEqual(list(policy.tags(p)), [policy.tag(v) for v in p])
        self.assertEqual(list(policy.tags(p)), [NO, NO, REVIEW, YES, YES])


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        Path("dataset.toml").write_text(CONFIG)
        write_split(Path("data/raw/train.csv"), 2000, 1, "t")
        write_split(Path("data/raw/eval.csv"), 1000, 2, "e")
        write_split(Path("data/holdout/holdout.csv"), 800, 3, "h")

    def tearDown(self):
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_freeze_batch_review_score_holdout_and_api(self):
        from tagger import batch, freeze, holdout, score, serve
        from tagger.engine import load_frozen

        run(freeze, [])
        frozen = json.loads(Path("frozen/policy.json").read_text())
        self.assertEqual(frozen["model"], "boosted_trees")

        out = run(batch, ["replay", "--batch", "b", "--input", "data/raw/eval.csv"])
        self.assertIn("1,000 rows", out)
        preds = pd.read_csv("results/batches/b/predictions.csv")
        self.assertNotIn("label", pd.read_csv("results/batches/b/inputs.csv").columns)
        pending = preds[preds["proposed_tag"] == REVIEW]["row_id"].iloc[0]
        run(batch, ["review", "--batch", "b", "--id", pending, "--tag", "yes"])
        run(batch, ["replay", "--batch", "b", "--input", "data/raw/eval.csv", "--force"])
        self.assertIn(pending, json.loads(Path("results/batches/b/reviews.json").read_text()))
        self.assertIn("human:yes", run(batch, ["export", "--batch", "b"]))

        run(score, ["--batch", "b", "--key", "data/raw/eval.csv"])
        result = json.loads(Path("results/batches/b/score.json").read_text())
        pr = result["policy_result"]
        self.assertEqual(pr["automatic"] + pr["review"], 1000)
        self.assertEqual(result["human_reviews"]["decided"], 1)

        run(holdout, [])
        self.assertIn("reproduced exactly", run(holdout, []))

        from tagger import compare, seed
        self.assertIn("boosted_trees", run(compare, []))
        run(seed, ["seed", "--rows", "500"])
        upload = pd.read_csv("data/kev/seed_real_500.csv")
        self.assertEqual(len(upload), 500)
        self.assertEqual(set(upload["source"]), {"real"})
        line = json.loads(Path("data/kev/seed_real_500.jsonl").read_text().splitlines()[0])
        self.assertNotIn("row_id", line["state"])
        self.assertIn(line["questions"]["answer"]["label"], (True, False))
        upload.drop(columns="source").to_csv("export.csv", index=False)
        self.assertIn("500 lines", run(seed, ["jsonl", "--input", "export.csv", "--out", "data/kev/x.jsonl"]))

        engine, policy, _ = load_frozen(config_mod.load())
        body = {"state": {"amount": "120", "score": "0.97", "kind": "a"},
                "questions": {"q": {"type": "noul", "instructions": "?"}}}
        reply = serve.answer(engine, policy, config_mod.load(), body)
        self.assertGreater(reply["answers"]["q"]["noul"], 0.5)


if __name__ == "__main__":
    unittest.main()
