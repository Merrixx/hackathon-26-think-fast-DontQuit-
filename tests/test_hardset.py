"""The hand-made hard set: file shape, label consistency, loader and scoring."""

import json
import tempfile
import unittest
from pathlib import Path

from cmdgate.dataset import Command
from cmdgate.hardset import (CASES_PATH, accuracy, ambiguous_ids, export_requests, load_cases, load_rows,
                             score_predictions)
from cmdgate.questions import LEVELS, RISKY_FROM, RISKY_ID, RISKY_QUESTION

REQUIRED = {"id", "command", "risky", "level", "category", "why", "second_label"}


class HardCasesFile(unittest.TestCase):
    def setUp(self):
        self.rows = load_rows()

    def test_every_line_parses_with_the_required_fields(self):
        with CASES_PATH.open() as lines:
            for number, line in enumerate(lines, 1):
                row = json.loads(line)
                self.assertLessEqual(REQUIRED, set(row), f"line {number}")
                self.assertIsInstance(row["risky"], bool)
                self.assertIsInstance(row["second_label"], bool)
                self.assertTrue(row["command"].strip() and row["why"].strip() and row["category"].strip())

    def test_size_is_in_the_planned_range(self):
        self.assertGreaterEqual(len(self.rows), 300)
        self.assertLessEqual(len(self.rows), 400)

    def test_ids_and_commands_are_unique(self):
        self.assertEqual(len({r["id"] for r in self.rows}), len(self.rows))
        self.assertEqual(len({r["command"] for r in self.rows}), len(self.rows))

    def test_label_matches_level(self):
        for row in self.rows:
            self.assertIn(row["level"], LEVELS, row["id"])
            self.assertEqual(row["risky"], LEVELS.index(row["level"]) >= RISKY_FROM, row["id"])

    def test_both_classes_present_overall_and_in_most_categories(self):
        self.assertIn(True, {r["risky"] for r in self.rows})
        self.assertIn(False, {r["risky"] for r in self.rows})
        share = sum(r["risky"] for r in self.rows) / len(self.rows)
        self.assertTrue(0.35 <= share <= 0.65, share)
        for category in {r["category"] for r in self.rows}:
            self.assertEqual({r["risky"] for r in self.rows if r["category"] == category}, {True, False}, category)

    def test_every_disagreement_is_marked_ambiguous(self):
        for row in self.rows:
            if row["risky"] != row["second_label"]:
                self.assertTrue(row.get("ambiguous"), row["id"])


class HardSetLoader(unittest.TestCase):
    def test_loader_returns_commands_with_own_families(self):
        cases = load_cases()
        rows = load_rows()
        self.assertEqual(len(cases), len(rows))
        self.assertTrue(all(isinstance(c, Command) for c in cases))
        self.assertEqual(len({c.family for c in cases}), len(cases))
        for case, row in zip(cases, rows):
            self.assertEqual((case.id, case.command, case.risky, case.category),
                             (row["id"], row["command"], row["risky"], row["category"]))
        self.assertLess(ambiguous_ids(), {c.id for c in cases})

    def test_export_has_the_question_and_no_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "requests.jsonl"
            count = export_requests(out)
            requests = [json.loads(line) for line in out.read_text().splitlines()]
        self.assertEqual(count, len(load_cases()))
        first = requests[0]
        self.assertEqual(set(first), {"id", "state", "questions"})
        self.assertEqual(first["questions"][RISKY_ID], RISKY_QUESTION)
        self.assertNotIn("label", json.dumps(requests))

    def test_score_predictions_on_perfect_and_inverted_answers(self):
        cases = load_cases()
        perfect = {c.id: 1.0 if c.risky else 0.0 for c in cases}
        result = score_predictions("perfect", perfect, log=False, preds_dir=None)
        self.assertEqual(result["accuracy"], 1.0)
        self.assertEqual(result["accuracy_agreed"], 1.0)
        inverted = score_predictions("inverted", {k: 1.0 - v for k, v in perfect.items()}, log=False, preds_dir=None)
        self.assertEqual(inverted["accuracy"], 0.0)
        with self.assertRaises(ValueError):
            score_predictions("partial", dict(list(perfect.items())[:5]), log=False, preds_dir=None)

    def test_accuracy_uses_the_half_threshold(self):
        cases = [Command("a", "rm -rf /", 4, "x", "a"), Command("b", "ls", 0, "x", "b")]
        self.assertEqual(accuracy(cases, [0.9, 0.1]), 1.0)
        self.assertEqual(accuracy(cases, [0.9, 0.5]), 0.5)


if __name__ == "__main__":
    unittest.main()
