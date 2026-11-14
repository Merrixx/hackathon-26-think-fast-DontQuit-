"""Score a model on shell commands from other datasets: does it hold up beyond its training data?

    uv run --extra ml python -m cmdgate.crosseval --scorer ngram
    uv run --extra ml python -m cmdgate.crosseval --scorer ngram-plus
    uv run --extra ml python -m cmdgate.crosseval --endpoint http://127.0.0.1:8009/v1/systemone --name kev
    uv run --extra ml python -m cmdgate.crosseval --export data/kev/crosseval      # requests + ids, no labels
    uv run --extra ml python -m cmdgate.crosseval --preds answers.json --source agentapprove --name kev
    uv run --extra ml python -m cmdgate.crosseval --train-plus                    # fits models/ngram_plus.joblib

Every source is turned into `cmdgate.dataset.Command` rows for the gate's own
yes/no decision ("should a person approve this command first?"). None of the
sources asks exactly that question, so each loader states its label mapping and
how far it is from ours; `SOURCES` holds the same text for reports. Only shell
commands are kept, one row per distinct command; a command whose rows disagree
on the mapped label is dropped, and so is any command that also appears in the
CLI Command Risk train split. Sources above `CAP` commands are cut to a
fixed-seed sample. The CLI Command Risk test split is never read here.

Mapped rows carry a placeholder level, because the sources have no five-level
scale: not risky is `safe`, risky is `high` (shell-safety: `ask` is `moderate`,
`deny` is `high`). In the logged metrics `severe_recall_at_1pct_fpr` therefore
means "recall on the risky rows at 1% false alarms" for the external sources.
A source with one class has no AUROC; its accuracy is one minus its flag rate.

Each run is appended to the experiment log with split `x-<source>` and its
P(risky) per command is saved to `results/preds/<name>_x-<source>.json`.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import random
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .dataset import Command, load_split
from .questions import LEVELS, RISKY_FROM, request_for

RAW_DIR = Path("data/raw")
PREDS_DIR = Path("results/preds")
RESULTS_DIR = Path("results")
PLUS_PATH = Path("models/ngram_plus.joblib")
PLUS_NAME = "ngram-plus"
CAP = 5000
# Chosen on train out-of-fold scores and the external sources other than `HELD_OUT`; see docs/cmdgate-experiments.jsonl.
PLUS_CONFIG = {"family_weight_power": 1.0, "aug_copies": 1, "aug_weight": 0.5, "C": 10.0}
SEED = 0

SAFE = LEVELS.index("safe")
MODERATE = LEVELS.index("moderate")
HIGH = LEVELS.index("high")

# agentapprove: the 28 policy rules, sorted by what a "true" says about our decision.
AA_RISKY = frozenset({
    "permanent-deletion", "git-force-push-or-delete-ref", "download-and-execute-code", "read-credential-material",
    "share-credential-material", "change-security-or-persistence", "git-discard-local-changes",
    "git-history-rewrite", "git-commit-credential-files", "change-production-state", "deploy-to-production",
    "database-destructive-statement", "database-production-write", "edit-shell-profile-or-dotfiles"})
AA_AMBIGUOUS = frozenset({
    "send-data-to-external-host", "upload-file-contents-externally", "run-with-elevated-privileges",
    "replace-existing-file-content", "move-to-trash", "git-push-specific-repository", "git-change-remote-or-hooks",
    "disable-tests-or-checks", "message-another-person", "communication-outside-slack", "public-discussion-post",
    "slack-dm-specific-person"})
SHELL_TOOLS = frozenset({"bash", "shell", "sh", "terminal", "run_command", "execute_command"})


def command_id(source: str, command: str) -> str:
    return f"x-{source}-{hashlib.sha1(command.encode()).hexdigest()[:12]}"


def as_command(source: str, command: str, level: int, category: str, family: str) -> Command:
    return Command(command_id(source, command), command, level, category or source, family or source)


def distinct(rows: Iterable[Command], exclude: frozenset[str] = frozenset()) -> list[Command]:
    """One row per distinct command, first kept. A command whose rows disagree on risky is dropped,
    as is an empty command or one in `exclude`."""
    kept: dict[str, Command] = {}
    conflicted: set[str] = set()
    for row in rows:
        text = row.command
        if not text.strip() or text in exclude:
            continue
        if text in kept and kept[text].risky != row.risky:
            conflicted.add(text)
        kept.setdefault(text, row)
    return [row for text, row in kept.items() if text not in conflicted]


def capped(rows: list[Command], cap: int = CAP, seed: int = SEED) -> list[Command]:
    """At most `cap` rows: a fixed-seed sample, in the original order."""
    if len(rows) <= cap:
        return rows
    picked = set(random.Random(seed).sample(range(len(rows)), cap))
    return [row for i, row in enumerate(rows) if i in picked]


# ---------------------------------------------------------------- label mappings

def shell_safety_level(label: str) -> int:
    """allow -> not risky; ask -> risky (moderate); deny -> risky (high)."""
    return {"allow": SAFE, "ask": MODERATE, "deny": HIGH}[label]


def agentapprove_label(truth: dict[str, str]) -> bool | None:
    """Risky, not risky, or None when the policy labels do not settle our decision.

    Risky when any rule in `AA_RISKY` is true. Not risky when no rule in `AA_RISKY` or `AA_AMBIGUOUS` is true and
    no `AA_RISKY` rule is "unknown". Everything else is None and left out: a case whose only true rules are ones
    our decision does not settle (sending data to a host, a plain `git push`, running with `sudo`, overwriting a
    file, moving to trash, messaging someone). `read-only-inspection` and `act-on-instructions-in-content` are
    ignored; "excluded" counts as not true.
    """
    true = {rule for rule, value in truth.items() if value == "true"}
    if true & AA_RISKY:
        return True
    if true & AA_AMBIGUOUS or any(truth.get(rule) == "unknown" for rule in AA_RISKY):
        return None
    return False


def rogue_command(data_to_evaluate: str) -> str | None:
    """The shell command of a tool-call message, or None when the message is not a shell tool call."""
    try:
        message = json.loads(data_to_evaluate)
    except json.JSONDecodeError:
        try:
            message = ast.literal_eval(data_to_evaluate)
        except (ValueError, SyntaxError):
            return None
    if not isinstance(message, dict):
        return None
    calls = message.get("tool_calls") or ([message["tool_call"]] if message.get("tool_call") else [])
    for call in calls if isinstance(calls, list) else []:
        if not isinstance(call, dict):
            continue
        call = call.get("function", call)
        arguments = call.get("arguments", call.get("input"))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                continue
        if str(call.get("name", "")).lower() in SHELL_TOOLS and isinstance(arguments, dict) \
                and isinstance(arguments.get("command"), str):
            return arguments["command"]
    return None


# ---------------------------------------------------------------- loaders

def _jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _shell_safety(splits: Sequence[str], raw_dir: Path) -> list[Command]:
    return [as_command("shell-safety", r["command"], shell_safety_level(r["label"]), r["shell"],
                       f"{r['category']}/{r['reason']}")
            for split in splits for r in _jsonl(raw_dir / "shell-safety" / f"{split}.jsonl")]


def load_shell_safety(raw_dir: Path = RAW_DIR) -> list[Command]:
    """shell-safety v1.1 (MIT, synthetic), validation + test splits; its train split is kept for training rows.

    Mapping: allow -> not risky, ask and deny -> risky. Close to our decision but not the same: the label was
    written with a session context (git state, the user's last prompt) that we do not give the model, and it is a
    permission policy, so `ask` also covers things our decision calls quick to undo. About a fifth of the rows are
    PowerShell or cmd, which CLI Command Risk does not have (the shell is the row's category).
    """
    return _shell_safety(("validation", "test"), raw_dir)


def load_shell_safety_train(raw_dir: Path = RAW_DIR) -> list[Command]:
    """shell-safety train split with the same mapping, for use as extra training rows only."""
    return _shell_safety(("train",), raw_dir)


def load_infraset(raw_dir: Path = RAW_DIR) -> list[Command]:
    """InfraSet (Apache-2.0): commands a sysadmin agent issued on disposable virtual machines. NO LABELS.

    Every row is given "not risky" as a PRESUMED label, so the only number that means anything is the flag rate,
    and it is not a false-alarm rate: the agent had root on throwaway machines and many commands restart
    services, rewrite files under /etc or reboot, which our decision calls risky on a real machine. Read it as
    "how much of a real admin session would the gate stop".
    """
    import pandas as pd

    rows = pd.read_parquet(raw_dir / "infraset" / "data" / "commands.parquet", columns=["run_id", "command"])
    return [as_command("infraset", r.command, SAFE, r.run_id.split("/")[0], "/".join(r.run_id.split("/")[:3]))
            for r in rows.itertuples()]


def load_swe_chat(raw_dir: Path = RAW_DIR) -> list[Command]:
    """SWE-chat auto mode (ODC-BY): the Bash tool calls of 250 real Claude Code sessions, all `expected: ALLOW`.

    Mapping: every command -> not risky. The source's claim is about the session ("ordinary development work, any
    block is a false positive"), not about each command out of context: the sessions do hold `git push`,
    `rm -rf` of build folders, `sudo` and installs that our decision may fairly send to a person. The flag rate
    is an upper bound on false alarms on real traffic.
    """
    import pandas as pd

    rows = pd.read_parquet(raw_dir / "swe-chat-auto-mode" / "data" / "eval-250.parquet",
                           columns=["session_id", "repo_id", "transcript_json"])
    out = []
    for r in rows.itertuples():
        for context in json.loads(r.transcript_json)["contexts"]:
            for message in context["messages"]:
                blocks = message["content"] if isinstance(message["content"], list) else []
                for block in blocks:
                    if block.get("type") == "tool_use" and block.get("name") == "Bash" \
                            and isinstance(block.get("input", {}).get("command"), str):
                        out.append(as_command("swe-chat", block["input"]["command"], SAFE, "bash",
                                              str(r.session_id)))
    return out


def load_agentapprove(raw_dir: Path = RAW_DIR) -> list[Command]:
    """Coding-agent tool-call safety (CC BY 4.0, synthetic): Bash calls from main dev, main holdout and confirmation.

    Each case has 28 policy-rule labels, not one risk label; `agentapprove_label` maps the `tool_only` labels
    (the command alone, as our model sees it) to our decision and leaves out the cases they do not settle. The
    mapping is ours, so part of any error is the mapping. The commands are long, multi-line, and written to be
    hard (decoys, hard negatives). This source is held out of all training and tuning.
    """
    out = []
    for part in ("main/dev", "main/holdout", "confirmation/holdout"):
        for r in _jsonl(raw_dir / "agentapprove" / "data" / f"{part}.jsonl"):
            if r["tool_name"] != "Bash":
                continue
            arguments = json.loads(r["tool_arguments"]) if isinstance(r["tool_arguments"], str) \
                else r["tool_arguments"]
            label = agentapprove_label(r["truth_tool_only"])
            if label is None or not isinstance(arguments.get("command"), str):
                continue
            out.append(as_command("agentapprove", arguments["command"], HIGH if label else SAFE,
                                  part.split("/")[0], r["family"]))
    return out


def load_permission_corpus(raw_dir: Path = RAW_DIR) -> list[Command]:
    """Permission command corpus, gold config (Apache-2.0, 321 rows, all three splits): an obfuscation test.

    Mapping: `actual_label` bad -> risky, good -> not risky. "bad" is "hazardous", close to ours, but 45% of the
    rows hide the command (base64, hex, split strings) and a third are PowerShell or cmd. A harmless command run
    through `eval "$(...)"` is labelled good here, while our decision would send unreadable code to a person. The
    category is `plain` or `obfuscated`.
    """
    import pandas as pd

    rows = pd.concat(pd.read_csv(raw_dir / "permission-corpus" / "gold" / f"{split}.csv")
                     for split in ("train", "validation", "test"))
    return [as_command("permission-corpus", str(r.text), HIGH if r.actual_label == "bad" else SAFE,
                       str(r.obfuscation_label), str(r.risk_class)) for r in rows.itertuples()]


def load_shellrisk_bench(raw_dir: Path = RAW_DIR) -> list[Command]:
    """ShellRisk-Bench test split (mixed licences incl. GPL-3.0 and unlicensed rows): TEST ONLY, never train on it.

    Mapping: risky -> risky, not_risky -> not risky, but the label means something else: "risky" is "comes from an
    attack-technique catalogue" (Atomic Red Team, GTFOBins, payload lists) and "not_risky" is "comes from a benign
    task trajectory or nl2bash", inferred per source and not checked per command. Many catalogue rows are
    harmless as written (`less /etc/hosts` as a GTFOBins read) and benign trajectories hold `rm -rf` and `pip
    install`. It measures "does the gate separate attack-catalogue commands from development traffic". The
    category is the upstream source.
    """
    import pandas as pd

    rows = pd.read_parquet(raw_dir / "shellrisk-bench" / "data" / "test-00000-of-00001.parquet")
    return [as_command("shellrisk-bench", r.command, HIGH if r.label == "risky" else SAFE, r.source, r.source)
            for r in rows.itertuples()]


def load_rogue_security(raw_dir: Path = RAW_DIR) -> list[Command]:
    """Coding-agent security benchmark (CC BY-NC 4.0: small test only, never train on it): shell tool calls only.

    Mapping: label "0" -> not risky, any violation code -> risky. The label judges a message inside a session:
    safe means "authorised by the user's goal or not harmful", so `kill -9` of the user's own test run is safe
    and the same command elsewhere is not. Without the session our model cannot see that.
    """
    import pandas as pd

    rows = pd.read_parquet(raw_dir / "rogue-security" / "data" / "test-00000-of-00001.parquet")
    out = []
    for r in rows[rows.message_type == "tool call"].itertuples():
        command = rogue_command(r.data_to_evaluate)
        if command is not None:
            out.append(as_command("rogue-security", command, SAFE if r.label == "0" else HIGH,
                                  r.category_and_criticality.split(" (")[0], "rogue-security"))
    return out


@dataclass(frozen=True)
class Source:
    name: str
    load: Callable[[Path], list[Command]]
    labelled: bool      # False: one presumed class, only the flag rate is meaningful
    may_train: bool     # licence and role allow its rows in a training set (never the rows scored here)


SOURCES = {s.name: s for s in [
    Source("shell-safety", load_shell_safety, True, True),
    Source("infraset", load_infraset, False, False),
    Source("swe-chat", load_swe_chat, False, False),
    Source("agentapprove", load_agentapprove, True, False),
    Source("permission-corpus", load_permission_corpus, True, False),
    Source("shellrisk-bench", load_shellrisk_bench, True, False),
    Source("rogue-security", load_rogue_security, True, False),
]}
HELD_OUT = "agentapprove"   # never used to fit or to pick a configuration

_cache: dict[tuple[str, str], list[Command]] = {}


def train_commands() -> list[Command]:
    if ("", "train") not in _cache:
        _cache[("", "train")] = load_split("train")
    return _cache[("", "train")]


def load_source(name: str, raw_dir: Path = RAW_DIR) -> list[Command]:
    """A source's distinct commands, minus those in CLI Command Risk train, capped at `CAP`."""
    key = (str(raw_dir), name)
    if key not in _cache:
        seen = frozenset(c.command for c in train_commands())
        _cache[key] = capped(distinct(SOURCES[name].load(raw_dir), seen))
    return _cache[key]


def extra_training_rows(raw_dir: Path = RAW_DIR) -> list[Command]:
    """shell-safety train rows that may join a training set: distinct, and in no evaluation set.

    Dropped: commands in CLI Command Risk train or validation, and commands in any external test source.
    """
    blocked = {c.command for c in train_commands()} | {c.command for c in load_split("validation")}
    for name in SOURCES:
        blocked |= {c.command for c in load_source(name, raw_dir)}
    return distinct(load_shell_safety_train(raw_dir), frozenset(blocked))


# ---------------------------------------------------------------- scoring

def validation_cutoff(name: str, results_dir: Path = RESULTS_DIR) -> float | None:
    """The confidence at which the model stayed inside the error budget on validation, if it was evaluated."""
    path = results_dir / f"{name}_validation.json"
    if path.exists():
        return float(json.loads(path.read_text())["automation_confidence"])
    saved = results_dir / "preds" / f"{name}_validation.json"
    if not saved.exists():
        return None
    # no harness result yet: the same cut-off, from the model's saved validation predictions
    from thinkfast.metrics import automation_rate

    from .evaluate import decisions_for

    preds = json.loads(saved.read_text())
    commands = [c for c in load_split("validation", exclude=frozenset(c.command for c in train_commands()))
                if c.id in preds]
    return automation_rate(decisions_for(commands, [preds[c.id] for c in commands]))[1]


def flag_rates(scores: Sequence[float], cutoff: float | None) -> dict[str, float]:
    """Share called risky at 0.5, and the share the engine would not allow on its own at the validation cut-off.

    The engine allows a command only when P(risky) <= 1 - cutoff. A cut-off of 1.0 means no threshold kept the
    model inside the error budget, so it asks every time.
    """
    rates = {"flag_rate_at_0.5": sum(s >= 0.5 for s in scores) / len(scores)}
    if cutoff is not None:
        allow_at = 1.0 - cutoff
        rates["ask_rate_at_validation_cutoff"] = 1.0 if cutoff >= 1.0 else sum(s > allow_at for s in scores) / len(scores)
    return rates


def score_predictions(name: str, source: str, preds: dict[str, float], config: dict[str, Any] | None = None,
                      cutoff_name: str | None = None, preds_dir: Path = PREDS_DIR) -> dict[str, float]:
    """Score {command_id: P(risky)} on one source, log the run as `x-<source>` and save the predictions.

    Commands without a prediction count as 0.5, as a failed request does in the runner.
    """
    from .lab import log_run

    commands = load_source(source)
    missing = sum(c.id not in preds for c in commands)
    scores = [float(preds.get(c.id, 0.5)) for c in commands]
    rates = flag_rates(scores, validation_cutoff(cutoff_name or name))
    note = "; ".join(f"{k}={v:.4f}" for k, v in rates.items())
    if not SOURCES[source].labelled:
        note += "; presumed not risky, no labels: read the flag rate only"
    if missing:
        note += f"; {missing} commands had no prediction (scored 0.5)"
    result = log_run(name, f"x-{source}", commands, scores, config, note)
    result = {k: v for k, v in result.items() if not math.isnan(v)} | rates
    preds_dir.mkdir(parents=True, exist_ok=True)
    (preds_dir / f"{name}_x-{source}.json").write_text(json.dumps({c.id: s for c, s in zip(commands, scores)}))
    risky = sum(c.risky for c in commands)
    print(f"  {source}: {len(commands)} commands, {risky} risky | {note}", flush=True)
    return result


def score_scorer(name: str, scorer: Any, sources: Sequence[str], config: dict[str, Any] | None = None,
                 cutoff_name: str | None = None) -> dict[str, dict[str, float]]:
    from .scorers import risky_scores

    out = {}
    for source in sources:
        commands = load_source(source)
        scores, _levels = risky_scores(scorer, [c.command for c in commands])
        out[source] = score_predictions(name, source, {c.id: s for c, s in zip(commands, scores)}, config,
                                        cutoff_name)
    return out


def export(out_dir: Path, sources: Sequence[str]) -> None:
    """Per source: `<source>.jsonl`, one decision API request per line without labels, and `<source>.ids.json`,
    the command ids in the same order. Answers come back as {id: P(risky)} for `--preds`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in sources:
        commands = load_source(source)
        with (out_dir / f"{source}.jsonl").open("w") as handle:
            for c in commands:
                handle.write(json.dumps(request_for(c.command)) + "\n")
        (out_dir / f"{source}.ids.json").write_text(json.dumps([c.id for c in commands]))
        print(f"{out_dir / source}.jsonl: {len(commands)} requests")


def read_preds(path: Path, source: str) -> dict[str, float]:
    """{id: p} as written, or a list of p in the order of the exported ids."""
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return {c.id: float(p) for c, p in zip(load_source(source), data, strict=True)}
    return {str(k): float(v) for k, v in data.items()}


# ---------------------------------------------------------------- ngram-plus: the n-gram model with wider training data

_FILE = re.compile(r"(?<![\w.$~-])([a-z][a-z0-9_]{2,14})\.(log|txt|json|csv|md|py|js|ts|yaml|yml|html|bak|tmp|out)\b")
_HOST = re.compile(r"\b(example|internal|corp|acme)\.(com|org|net|io)\b")
_QUOTABLE = re.compile(r"^[A-Za-z0-9_./-]{3,}$")
_STEMS = ("report", "notes", "server", "output", "results", "index", "main", "data", "debug", "summary", "config2",
          "draft", "items", "build_info", "trace", "users_list", "metrics", "changes")
_DOMAINS = ("example", "internal", "corp", "acme")
_TLDS = ("com", "org", "net", "io")
_DIRS = ("src", "./project", "~/work/app", "/tmp/build", "repo", "services/api", "$PROJECT_DIR", "/workspace",
         "packages/core", "~/dev/site")
_SUFFIXES = (" 2>&1", " 2>/dev/null", " 2>&1 | tail -{n}", " 2>&1 | head -{n}", " && echo done", "; echo $?",
             " | cat", "; echo ---")
_PROBES = ("pwd", "ls", "ls -la", "git status", "echo ---", "date", "whoami", "git log --oneline -5", "cat README.md",
           "hostname", "uname -a", "git diff --stat", "echo start")
_NO_REWRITE = re.compile(r"<<|#|\n|&\s*$|;\s*$|\\$")
_SECRETISH = re.compile(r"secret|token|passw|credential|private|key|\.env|shadow|id_", re.IGNORECASE)


def augment(command: str, rng: random.Random) -> str:
    """One rewrite of a command that keeps its answer: other file, host or folder names, other quoting, a harmless
    `cd`, probe or output filter around it. Names that look like secrets are left alone, and so are system paths:
    which path a command touches is what the label depends on.
    """
    out = command
    edits = rng.sample(("file", "host", "quote", "cd", "suffix", "probe"), k=rng.choice((1, 2, 2, 3)))
    if "file" in edits:
        out = _FILE.sub(lambda m: m.group(0) if _SECRETISH.search(m.group(0))
                        else f"{rng.choice(_STEMS)}.{m.group(2)}", out)
    if "host" in edits:
        out = _HOST.sub(lambda m: f"{rng.choice(_DOMAINS)}.{rng.choice(_TLDS)}", out)
    if "quote" in edits:
        words = out.split(" ")
        plain = [i for i, w in enumerate(words) if i and _QUOTABLE.match(w) and not w.startswith("-")]
        if plain and "'" not in out and '"' not in out:
            i = rng.choice(plain)
            quote = rng.choice("'\"")
            words[i] = f"{quote}{words[i]}{quote}"
            out = " ".join(words)
    if _NO_REWRITE.search(out):
        return out
    if "suffix" in edits and "|" not in out and ">" not in out:
        out += rng.choice(_SUFFIXES).format(n=rng.choice((5, 20, 30, 50)))
    if "probe" in edits:
        probe, joiner = rng.choice(_PROBES), rng.choice((" && ", "; ", " ; echo ---; "))
        out = f"{probe}{joiner}{out}" if rng.random() < 0.5 else f"{out}{joiner}{probe}"
    if "cd" in edits:
        out = f"cd {rng.choice(_DIRS)} && {out}"
    return out


def augmented(commands: Sequence[Command], copies: int, seed: int = SEED) -> list[Command]:
    """`copies` rewrites per command, same level and family; rewrites equal to their source are dropped."""
    rng = random.Random(seed)
    out = []
    for c in commands:
        seen = {c.command}
        for _ in range(copies):
            text = augment(c.command, rng)
            if text not in seen:
                seen.add(text)
                out.append(Command(c.id + "+aug", text, c.level, c.category, c.family))
    return out


def composed(commands: Sequence[Command], count: int, seed: int = SEED) -> list[Command]:
    """`count` chains of two or three single-line train commands; the chain's level is its worst part.

    Real agent commands are chains (`a && b; c`); CLI Command Risk has one short command per row. A chain is
    risky exactly when a part is, so the label follows from the parts. The family is the worst part's family.
    """
    rng = random.Random(seed)
    simple = [c for c in commands if not _NO_REWRITE.search(c.command)]
    calm = [c for c in simple if not c.risky]
    out = []
    for _ in range(count):
        # mostly harmless parts, so that risky chains are risky because of one part, as in real sessions
        parts = [rng.choice(simple)] + [rng.choice(calm) for _ in range(rng.choice((1, 1, 2)))]
        rng.shuffle(parts)
        worst = max(parts, key=lambda c: c.level)
        text = parts[0].command
        for part in parts[1:]:
            text += rng.choice((" && ", "; ", " ; echo ---; ", " || ")) + part.command
        out.append(Command(worst.id + "+chain", text, worst.level, worst.category, worst.family))
    return out


def build_plus_pipeline(c: float = 10.0):
    """Same features and model as `cmdgate.ngram`; only the training rows and their weights differ."""
    from .ngram import build_pipeline

    return build_pipeline(c)


def plus_training_set(train: Sequence[Command], config: dict[str, Any],
                      extra: Sequence[Command] | None = None) -> tuple[list[Command], list[float]]:
    """Rows and weights for one configuration.

    config: family_weight_power (weights of the CLI Command Risk rows, mean 1), aug_copies and aug_weight,
    chains (count as a share of the train rows) and chain_weight, shell_safety_weight (0 leaves the rows out),
    shell_safety_ask (false drops the `ask` rows).
    Rewrites and chains are made from `train` only, so inside a fold they never see the held-out families.
    """
    from .lab import family_weights

    rows = list(train)
    weights = family_weights(rows, config.get("family_weight_power", 0.0))
    by_id = {c.id: w for c, w in zip(rows, weights)}
    if config.get("aug_copies"):
        more = augmented(train, config["aug_copies"])
        rows += more
        weights += [by_id[c.id.removesuffix("+aug")] * config.get("aug_weight", 1.0) for c in more]
    if config.get("chains"):
        more = composed(train, int(config["chains"] * len(train)))
        rows += more
        weights += [config.get("chain_weight", 1.0)] * len(more)
    if config.get("shell_safety_weight"):
        more = list(extra if extra is not None else extra_training_rows())
        if not config.get("shell_safety_ask", True):   # keep allow and deny, leave out the policy-dependent `ask`
            more = [c for c in more if c.level != MODERATE]
        rows += more
        weights += [config["shell_safety_weight"]] * len(more)
    return rows, weights


def fit_plus(train: Sequence[Command], config: dict[str, Any], extra: Sequence[Command] | None = None):
    rows, weights = plus_training_set(train, config, extra)
    model = build_plus_pipeline(config.get("C", 10.0))
    return model.fit([c.command for c in rows], [c.level for c in rows],
                     logisticregression__sample_weight=weights)


class NgramPlusScorer:
    """Loader for `models/ngram_plus.joblib`: {"model": pipeline, "config": the training configuration}."""

    def __init__(self, path: Path = PLUS_PATH) -> None:
        import joblib

        saved = joblib.load(path)
        self.model, self.config = saved["model"], saved["config"]

    def levels(self, command: str) -> list[float]:
        return self.batch_levels([command])[0]

    def batch_levels(self, commands: list[str]) -> list[list[float]]:
        probs = self.model.predict_proba(commands)
        full = [[0.0] * len(LEVELS) for _ in commands]
        for column, level in enumerate(self.model.classes_):
            for row, p in zip(full, probs[:, column]):
                row[int(level)] = float(p)
        return full


def load_ngram_plus(path: Path = PLUS_PATH) -> NgramPlusScorer:
    return NgramPlusScorer(path)


def train_plus(config: dict[str, Any] | None = None, path: Path = PLUS_PATH) -> None:
    """Fit the chosen configuration on all of CLI Command Risk train plus its extra rows, and save it."""
    import joblib

    config = config or PLUS_CONFIG
    model = fit_plus(train_commands(), config)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "config": config}, path, compress=3)
    print(f"saved {path} with {config}")


def main() -> None:
    from .scorers import IN_PROCESS, EndpointScorer, load_scorer

    in_process = tuple(s for s in IN_PROCESS if s != "encoder") + (PLUS_NAME,)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--scorer", choices=in_process, help="a model run in this process")
    what.add_argument("--endpoint", help="URL of an engine that serves the decision API")
    what.add_argument("--export", type=Path, metavar="DIR", help="write each source as decision API requests")
    what.add_argument("--preds", type=Path, help="score a file of {id: P(risky)}; needs --source and --name")
    what.add_argument("--train-plus", action="store_true", help=f"fit and save {PLUS_PATH} with PLUS_CONFIG")
    parser.add_argument("--name", help="name in the log and the prediction files; required with --endpoint")
    parser.add_argument("--source", action="append", choices=tuple(SOURCES), help="repeatable; default: all")
    args = parser.parse_args()
    sources = args.source or list(SOURCES)
    if args.train_plus:
        train_plus()
        return
    if args.export:
        export(args.export, sources)
        return
    if (args.endpoint or args.preds) and not args.name:
        parser.error("--endpoint and --preds need --name")
    if args.preds:
        if len(sources) != 1:
            parser.error("--preds needs exactly one --source")
        score_predictions(args.name, sources[0], read_preds(args.preds, sources[0]))
        return
    if args.endpoint:
        scorer: Any = EndpointScorer(args.endpoint)
    elif args.scorer == PLUS_NAME:
        scorer = load_ngram_plus()
    else:
        scorer = load_scorer(args.scorer, train_commands())
    name = args.name or args.scorer
    config = {"model": getattr(scorer, "config", None)} if args.scorer == PLUS_NAME else {}
    score_scorer(name, scorer, sources, config)


if __name__ == "__main__":
    main()
