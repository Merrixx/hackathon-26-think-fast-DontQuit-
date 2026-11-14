"""The Claude Code hook app: settings, the model registry, the decision, and the hook against a live backend."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from cmdhook import models as registry
from cmdhook.config import APP_DIR, Config, ConfigError, load, parse_env
from cmdgate.questions import LEVEL_QUESTION, RISKY_QUESTION, question_kind
from cmdhook.server import decide, make_hook_handler, mean_levels, systemone_answer
from thinkfast.run_eval import post_json

HOOK = APP_DIR / "hook.js"


class FixedScorer:
    """Says the same P(risky) for every command."""

    def __init__(self, p: float) -> None:
        self.p = p

    def risky(self, command: str) -> float:
        return self.p


def scores(**risks: float) -> dict:
    return {name: {"p": p, "ms": 1.0, "failed": False} for name, p in risks.items()}


class ConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = Path(tmp.name) / ".env"

    def test_env_file_lines_are_parsed(self):
        text = '# a comment\n\nCMDHOOK_MODELS = "rules,ngram"\nexport CMDHOOK_PORT=9000\nnot a setting\n'
        self.assertEqual(parse_env(text), {"CMDHOOK_MODELS": "rules,ngram", "CMDHOOK_PORT": "9000"})

    def test_the_environment_wins_over_the_file(self):
        self.env.write_text("CMDHOOK_THRESHOLD=0.4\nCMDHOOK_COMBINE=mean\n")
        config = load(self.env, {"CMDHOOK_THRESHOLD": "0.1", "UNRELATED": "x"})
        self.assertEqual((config.threshold, config.combine), (0.1, "mean"))

    def test_defaults_apply_without_a_file_and_for_empty_values(self):
        self.assertEqual(load(self.env, {"CMDHOOK_MODELS": " "}), Config())

    def test_a_model_can_have_its_own_threshold(self):
        config = load(self.env, {"CMDHOOK_MODELS": "ngram,enc-mbl-cap8", "CMDHOOK_THRESHOLD": "0.5",
                                 "CMDHOOK_THRESHOLD_ENC_MBL_CAP8": "0.2"})
        self.assertEqual([config.threshold_for(name) for name in config.names], [0.5, 0.2])

    def test_endpoints_are_served_after_the_listed_models(self):
        config = load(self.env, {"CMDHOOK_MODELS": "ngram", "CMDHOOK_ENDPOINTS": "other=http://127.0.0.1:1/v1/systemone"})
        self.assertEqual(config.names, ["ngram", "other"])

    def test_unusable_values_are_rejected(self):
        for bad in ({"CMDHOOK_THRESHOLD": "1.5"}, {"CMDHOOK_THRESHOLD_NGRAM": "high"}, {"CMDHOOK_COMBINE": "most"},
                    {"CMDHOOK_ON_FAIL": "allow"}, {"CMDHOOK_PORT": "http"}, {"CMDHOOK_ENDPOINTS": "nourl"},
                    {"CMDHOOK_MODELS": "ngram,ngram"}):
            with self.subTest(bad), self.assertRaises(ConfigError):
                load(self.env, bad)


class RegistryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(tmp.name)  # the registry's paths are relative to the working directory

    def test_names_resolve_to_their_files(self):
        config = Config(endpoints={"other": "http://127.0.0.1:1/v1/systemone"})
        self.assertEqual(registry.resolve("ngram-plus", config).path, Path("models/ngram_plus.joblib"))
        self.assertEqual(registry.resolve("enc-mbl-cap8", config).path, Path("models/enc-mbl-cap8"))
        self.assertEqual(registry.resolve("other", config).kind, "endpoint")
        self.assertEqual(registry.resolve("kev", Config(kev_port=9001)).url, "http://127.0.0.1:9001/v1/systemone")
        with self.assertRaises(ValueError):
            registry.resolve("gpt", config)

    def test_missing_weights_are_reported(self):
        self.assertIn("models/ngram.joblib is missing", registry.resolve("ngram", Config()).problems(Config())[0])
        self.assertIn("models/encoder/ is missing", registry.resolve("encoder", Config()).problems(Config())[0])

    def test_an_encoder_needs_every_file_and_no_prompt_wrap(self):
        folder = Path("models/enc-x")
        folder.mkdir(parents=True)
        for name in ("config.json", "model.safetensors", "tokenizer.json"):
            (folder / name).write_text("{}")
        model = registry.resolve("enc-x", Config())
        self.assertEqual(model.problems(Config()), ["models/enc-x/cmdgate.json is missing"])
        (folder / "cmdgate.json").write_text(json.dumps({"temperature": 2.0}))
        self.assertEqual(model.problems(Config()), [])
        self.assertEqual(registry.on_disk(), ["enc-x"])
        (folder / "cmdgate.json").write_text(json.dumps({"temperature": 2.0, "prompt": True}))
        self.assertIn("prompt-wrapped", model.problems(Config())[0])
        (folder / "cmdgate.json").write_text(json.dumps({"temperature": 2.0}))
        (folder / "tokenizer.json").write_text('{"cut": "off')
        self.assertIn("tokenizer.json is damaged", model.problems(Config())[0])

    def test_kev_needs_a_checkpoint_and_a_checkout(self):
        problems = registry.resolve("kev", Config()).problems(Config(kev_dir="nowhere"))
        self.assertIn("CMDHOOK_KEV_RUN is not set", problems[0])
        self.assertIn("no Kev checkout", problems[1])
        Path("runs/a/checkpoint").mkdir(parents=True)
        self.assertEqual(registry.kev_checkpoints(), ["runs/a/checkpoint"])


class DecideTest(unittest.TestCase):
    def test_all_needs_every_model_at_or_below_its_threshold(self):
        config = Config(threshold=0.5, thresholds={"B": 0.1})
        self.assertEqual(decide(scores(a=0.5, b=0.1), config)["decision"], "allow")
        answer = decide(scores(a=0.2, b=0.11), config)
        self.assertEqual((answer["decision"], answer["passed"]), ("ask", False))
        self.assertEqual({name: row["passed"] for name, row in answer["models"].items()}, {"a": True, "b": False})
        self.assertIn("1 of 2 models above their threshold: b", answer["reason"])

    def test_any_needs_one_model(self):
        config = Config(combine="any", threshold=0.3)
        self.assertTrue(decide(scores(a=0.9, b=0.1), config)["passed"])
        self.assertFalse(decide(scores(a=0.9, b=0.4), config)["passed"])

    def test_mean_compares_the_mean_risk_to_the_global_threshold(self):
        config = Config(combine="mean", threshold=0.5, thresholds={"A": 0.0})
        answer = decide(scores(a=0.8, b=0.1), config)
        self.assertTrue(answer["passed"])
        self.assertAlmostEqual(answer["mean"], 0.45)
        self.assertFalse(decide(scores(a=0.9, b=0.2), config)["passed"])

    def test_the_outcomes_are_configurable(self):
        config = Config(on_pass="defer", on_fail="deny", threshold=0.5)
        self.assertEqual(decide(scores(a=0.1), config)["decision"], "defer")
        self.assertEqual(decide(scores(a=0.9), config)["decision"], "deny")

    def test_a_model_that_did_not_answer_does_not_pass(self):
        failed = {"a": {"p": 0.1, "ms": 1.0, "failed": False}, "b": {"p": 0.5, "ms": 1.0, "failed": True}}
        for combine in ("all", "mean"):
            with self.subTest(combine):
                self.assertFalse(decide(failed, Config(combine=combine, threshold=0.9))["passed"])
        self.assertTrue(decide(failed, Config(combine="any", threshold=0.9))["passed"])

    def test_the_summary_has_a_line_per_model(self):
        text = decide(scores(ngram=0.01, encoder=0.9), Config(), "rm -rf /")["summary"]
        self.assertIn("cmdhook: ASK", text)
        self.assertRegex(text, r"ngram\s+risk 0\.010 <= 0\.500\s+pass")
        self.assertRegex(text, r"encoder\s+risk 0\.900 >  0\.500\s+fail")


class BackendTest(unittest.TestCase):
    """The backend over HTTP, and hook.js against it."""

    def serve(self, config: Config, **risks: float) -> str:
        scorers = {name: FixedScorer(p) for name, p in risks.items()}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_hook_handler(scorers, config, Path(tmp.name)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        return f"http://127.0.0.1:{self.port}"

    def hook(self, *args: str, stdin: str = "", **env: str) -> subprocess.CompletedProcess:
        settings = {"CMDHOOK_PORT": str(self.port), "CMDHOOK_HOST": "127.0.0.1", "CMDHOOK_TIMEOUT_MS": "5000",
                    "CMDHOOK_ON_ERROR": "ask", "CMDHOOK_QUIET": "0", **env}
        return subprocess.run(["node", str(HOOK), *args], input=stdin, capture_output=True, text=True, timeout=30,
                              env={"PATH": os.environ["PATH"], **settings})

    def test_hook_endpoint_returns_the_breakdown(self):
        base = self.serve(Config(threshold=0.5), low=0.02, high=0.96)
        answer = post_json(f"{base}/v1/hook", {"command": "sudo rm -rf /"}, 5, None)
        self.assertEqual(answer["decision"], "ask")
        self.assertEqual({name: (row["p"], row["threshold"], row["passed"]) for name, row in answer["models"].items()},
                         {"low": (0.02, 0.5, True), "high": (0.96, 0.5, False)})
        self.assertIn("privilege", answer["rules"])

    def test_the_dashboard_gets_the_hooks_decision_and_every_model(self):
        base = self.serve(Config(threshold=0.5), low=0.02, high=0.96)
        check = post_json(f"{base}/v1/check", {"command": "ls"}, 5, None)
        self.assertEqual((check["verdict"], check["bucket"]), ("ask", "likely"))  # all must pass, `high` does not
        self.assertEqual({name: row["verdict"] for name, row in check["models"].items()}, {"low": "allow", "high": "ask"})
        one = post_json(f"{base}/v1/check", {"command": "ls", "model": "low"}, 5, None)
        self.assertEqual((one["verdict"], one["model"], one["p"]), ("allow", "low", 0.02))
        self.assertEqual(one["policy"]["allow_at_or_below"], 0.5)
        with self.assertRaises(Exception):
            post_json(f"{base}/v1/hook", {"command": " "}, 5, None)

    def test_a_denying_backend_reports_block_and_lists_its_models(self):
        base = self.serve(Config(threshold=0.5, on_fail="deny", thresholds={"HIGH": 0.2}), low=0.02, high=0.96)
        self.assertEqual(post_json(f"{base}/v1/check", {"command": "ls"}, 5, None)["verdict"], "block")
        with urllib.request.urlopen(f"{base}/v1/models", timeout=5) as response:
            listing = json.loads(response.read())
        self.assertEqual({row["name"]: row["allow_at_or_below"] for row in listing["data"]}, {"low": 0.5, "high": 0.2})

    def test_hook_decisions_are_logged_for_the_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.jsonl"
            handler = make_hook_handler({"high": FixedScorer(0.96)}, Config(threshold=0.5), Path(tmp), log_path=log)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            post_json(f"{base}/v1/hook", {"command": "rm -rf build"}, 5, None)
            post_json(f"{base}/v1/check", {"command": "ls"}, 5, None)  # a dashboard try-out is not a decision
            entries = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]["level"], entries[0]["bucket"], entries[0]["hits"]), ("ask", "likely", ["model:high"]))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_the_hook_passes_the_decision_to_claude_code(self):
        self.serve(Config(threshold=0.5), low=0.02)
        call = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}})
        out = json.loads(self.hook(stdin=call).stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertIn("risk 0.020 <= 0.500", out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("cmdhook: ALLOW", out["systemMessage"])
        self.assertNotIn("systemMessage", json.loads(self.hook(stdin=call, CMDHOOK_QUIET="1").stdout))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_a_failing_command_asks_or_denies_and_defer_stays_silent(self):
        call = json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}})
        self.serve(Config(on_fail="deny"), high=0.96)
        self.assertEqual(json.loads(self.hook(stdin=call).stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.serve(Config(on_pass="defer"), low=0.02)
        self.assertEqual(list(json.loads(self.hook(stdin=call).stdout)), ["systemMessage"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_check_mode_prints_the_scores_and_sets_the_exit_code(self):
        self.serve(Config(), low=0.02, high=0.96)
        done = self.hook("check", "git", "push", "--force")
        self.assertEqual(done.returncode, 1)
        self.assertIn("command: git push --force", done.stdout)
        self.assertRegex(done.stdout, r"high\s+risk 0\.960 >  0\.500\s+fail")

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_an_unreachable_backend_follows_on_error(self):
        self.serve(Config(), low=0.02)
        call = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
        dead = {"CMDHOOK_PORT": "1"}
        out = json.loads(self.hook(stdin=call, **dead).stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("did not answer", out["hookSpecificOutput"]["permissionDecisionReason"])
        deferred = json.loads(self.hook(stdin=call, CMDHOOK_ON_ERROR="defer", CMDHOOK_QUIET="1", **dead).stdout)
        self.assertEqual(list(deferred), ["systemMessage"])  # reported even when quiet, and no decision made
        self.assertIn("did not answer", deferred["systemMessage"])

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_other_tools_and_broken_input_get_no_answer(self):
        self.serve(Config(), high=0.96)
        edit = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": "a.py"}})
        for stdin in (edit, "not json"):
            done = self.hook(stdin=stdin)
            self.assertEqual((done.returncode, done.stdout), (0, ""))


class LevelScorer:
    """Says the same probability per risk level for every command."""

    def __init__(self, levels: list[float]) -> None:
        self.fixed = levels

    def levels(self, command: str) -> list[float]:
        return self.fixed


PLUGIN_QUESTIONS = {
    "dangerous": {"type": "noul", "instructions": "Could this shell command cause serious damage, data loss or a "
                                                  "security problem if it runs as written?"},
    "requires_root": {"type": "noul", "instructions": "Does this shell command need root or administrator "
                                                      "privileges to run?"},
    "risk_level": LEVEL_QUESTION,
    "unsafe": {"type": "noul", "instructions": RISKY_QUESTION["instructions"]},
}


class PluginAnswerTest(unittest.TestCase):
    def ask(self, scorers: dict, **settings) -> dict:
        return systemone_answer(scorers, Config(models=tuple(scorers), **settings),
                                {"state": {"command": "rm -rf build"}, "questions": PLUGIN_QUESTIONS})

    def test_questions_are_told_apart_by_what_they_ask(self):
        kinds = {qid: question_kind(qid, question) for qid, question in PLUGIN_QUESTIONS.items()}
        self.assertEqual(kinds, {"dangerous": "dangerous", "requires_root": "root", "risk_level": "level",
                                 "unsafe": "risky"})
        self.assertEqual(question_kind("risky", RISKY_QUESTION), "risky")

    def test_each_question_gets_its_own_number_and_root_gets_none(self):
        body = self.ask({"levels": LevelScorer([0.1, 0.1, 0.2, 0.5, 0.1]), "flat": FixedScorer(0.2)})
        answers = body["answers"]
        self.assertAlmostEqual(answers["unsafe"]["noul"], (0.8 + 0.2) / 2)
        self.assertAlmostEqual(answers["dangerous"]["noul"], 0.6)
        self.assertAlmostEqual(answers["risk_level"]["probabilities"]["3"], 0.5)
        self.assertNotIn("requires_root", answers)

    def test_without_a_level_model_only_the_approval_question_is_answered(self):
        answers = self.ask({"flat": FixedScorer(0.9)})["answers"]
        self.assertEqual(list(answers), ["unsafe"])
        self.assertIsNone(mean_levels({"flat": {"p": 0.9, "ms": 1.0, "failed": False}}))

    def test_the_answer_carries_the_decision_and_every_model(self):
        body = self.ask({"calm": FixedScorer(0.1), "wary": FixedScorer(0.9)}, threshold=0.5, combine="all")
        self.assertEqual(body["decision"], "ask")
        self.assertEqual({name: row["passed"] for name, row in body["models"].items()}, {"calm": True, "wary": False})
        self.assertIn("wary", body["reason"])


if __name__ == "__main__":
    unittest.main()
