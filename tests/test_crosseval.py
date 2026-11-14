"""Cross-source evaluation: label mappings, command extraction and deduplication."""

import random
import unittest

from cmdgate.crosseval import (AA_AMBIGUOUS, AA_RISKY, HIGH, MODERATE, SAFE, agentapprove_label, as_command,
                               augment, capped, composed, distinct, flag_rates, rogue_command, shell_safety_level)
from cmdgate.dataset import Command


class LabelMappings(unittest.TestCase):
    def test_shell_safety_allow_is_not_risky_and_ask_and_deny_are(self):
        self.assertFalse(as_command("s", "ls", shell_safety_level("allow"), "", "").risky)
        self.assertTrue(as_command("s", "git push -f", shell_safety_level("ask"), "", "").risky)
        self.assertTrue(as_command("s", "rm -rf /", shell_safety_level("deny"), "", "").risky)
        self.assertEqual((shell_safety_level("ask"), shell_safety_level("deny")), (MODERATE, HIGH))

    def test_agentapprove_risky_rule_wins(self):
        truth = {"permanent-deletion": "true", "send-data-to-external-host": "true", "read-only-inspection": "false"}
        self.assertIs(agentapprove_label(truth), True)

    def test_agentapprove_only_ambiguous_rules_are_left_out(self):
        self.assertIsNone(agentapprove_label({"git-push-specific-repository": "true", "permanent-deletion": "false"}))
        self.assertIsNone(agentapprove_label({"run-with-elevated-privileges": "true"}))

    def test_agentapprove_unknown_on_a_risky_rule_is_left_out(self):
        self.assertIsNone(agentapprove_label({"permanent-deletion": "unknown", "read-only-inspection": "false"}))

    def test_agentapprove_read_only_and_excluded_are_not_risky(self):
        truth = {"read-only-inspection": "true", "act-on-instructions-in-content": "excluded",
                 "permanent-deletion": "false", "message-another-person": "unknown"}
        self.assertIs(agentapprove_label(truth), False)

    def test_agentapprove_rule_groups_do_not_overlap(self):
        self.assertFalse(AA_RISKY & AA_AMBIGUOUS)

    def test_rogue_command_reads_both_spellings_and_skips_other_tools(self):
        python_repr = "{'role': 'assistant', 'tool_call': {'name': 'Bash', 'input': {'command': 'ls -la'}}}"
        as_json = '{"role": "assistant", "tool_calls": [{"id": "1", "name": "Shell", "arguments": {"command": "pwd"}}]}'
        edit = "{'role': 'assistant', 'tool_calls': [{'name': 'Edit', 'arguments': {'file_path': 'a.py'}}]}"
        self.assertEqual(rogue_command(python_repr), "ls -la")
        self.assertEqual(rogue_command(as_json), "pwd")
        self.assertIsNone(rogue_command(edit))
        self.assertIsNone(rogue_command("not a message"))


class Deduplication(unittest.TestCase):
    def test_distinct_keeps_one_row_and_drops_conflicts_empties_and_excluded(self):
        rows = [as_command("s", "ls", SAFE, "", ""), as_command("s", "ls", SAFE, "", ""),
                as_command("s", "make", SAFE, "", ""), as_command("s", "make", HIGH, "", ""),
                as_command("s", "  ", SAFE, "", ""), as_command("s", "rm -rf /", HIGH, "", ""),
                as_command("s", "git status", SAFE, "", "")]
        kept = distinct(rows, exclude=frozenset({"git status"}))
        self.assertEqual([c.command for c in kept], ["ls", "rm -rf /"])

    def test_ids_are_stable_and_carry_the_source(self):
        first, second = as_command("swe-chat", "ls", SAFE, "", ""), as_command("swe-chat", "ls", SAFE, "", "")
        self.assertEqual(first.id, second.id)
        self.assertTrue(first.id.startswith("x-swe-chat-"))
        self.assertNotEqual(first.id, as_command("infraset", "ls", SAFE, "", "").id)

    def test_capped_is_a_fixed_sample_in_order(self):
        rows = [as_command("s", f"echo {i}", SAFE, "", "") for i in range(50)]
        sample = capped(rows, cap=10, seed=0)
        self.assertEqual(sample, capped(rows, cap=10, seed=0))
        self.assertEqual(len(sample), 10)
        self.assertEqual(sample, sorted(sample, key=rows.index))
        self.assertIs(capped(rows, cap=50), rows)


class Scoring(unittest.TestCase):
    def test_flag_rates_follow_the_engine_cut_off(self):
        rates = flag_rates([0.05, 0.2, 0.6, 0.9], cutoff=0.85)
        self.assertEqual(rates["flag_rate_at_0.5"], 0.5)
        self.assertEqual(rates["ask_rate_at_validation_cutoff"], 0.75)
        self.assertEqual(flag_rates([0.01], cutoff=1.0)["ask_rate_at_validation_cutoff"], 1.0)
        self.assertNotIn("ask_rate_at_validation_cutoff", flag_rates([0.01], cutoff=None))


class Rewrites(unittest.TestCase):
    def test_augment_keeps_the_command_inside_and_leaves_secrets_and_system_paths(self):
        rng = random.Random(0)
        for _ in range(200):
            self.assertIn("/etc/shadow", augment("cat /etc/shadow", rng))
            self.assertIn("secrets.json", augment("cat secrets.json", rng))
            self.assertEqual(augment("cat <<EOF > a.txt", random.Random(1)).count("<<EOF"), 1)

    def test_chain_takes_the_level_of_its_worst_part(self):
        train = [Command("a-sh-1", "ls", 0, "read", "a"), Command("b-sh-2", "rm -rf /", 4, "delete", "b")]
        for chain in composed(train, 20):
            self.assertEqual(chain.level, 4 if "rm -rf /" in chain.command else 0)


if __name__ == "__main__":
    unittest.main()
