"""Download the datasets the command gate trains and tests on.

    uv run --extra ml python -m cmdgate.fetch_data

Data is fetched at pinned revisions into `data/raw/`, which git ignores: the
repo links its data instead of committing it. Each source carries its licence
and what the gate may use it for.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

RAW_DIR = Path("data/raw")


@dataclass(frozen=True)
class HubDataset:
    name: str
    repo_id: str
    revision: str
    patterns: tuple[str, ...]
    licence: str
    use: str


SOURCES = [
    HubDataset("cli-command-risk", "yannelli/cli-command-risk", "d902c94a302c03cb750ad7bf8d6b6bf6bd68d920",
               ("data/*.parquet", "README.md", "LICENSE", "manifest.json", "taxonomy.json"),
               "CC BY 4.0", "train, validation and primary test (tag v1.5)"),
    HubDataset("shellrisk-bench", "kontext-security/ShellRisk-Bench", "437467862139b4e9cdd5322024ef3434a67c7ec8",
               ("data/*.parquet", "README.md"),
               "other: mixed upstream, includes GPL-3.0 and unlicensed rows",
               "cross-source test only; never train on the gtfobins or payloads rows"),
    HubDataset("swe-chat-auto-mode", "alex-remedios/swe-chat-auto-mode", "0557f8175f919cf081710cf7e7ac3c8faac0b3f1",
               ("data/eval-250.parquet", "README.md", "scripts/*"),
               "ODC-BY", "false-alarm test on real Claude Code sessions, every action expected ALLOW"),
    HubDataset("agentapprove", "agentapprove/coding-agent-tool-call-safety", "939f5b969f8703141a78988b9336248550c6d369",
               ("data/**", "README.md", "LICENSE", "rulebook/RULEBOOK.md"),
               "CC BY 4.0", "cross-source test: Bash tool calls with policy labels"),
    HubDataset("infraset", "infraset/infraset", "d8d5b97e9e1d7d421fb6c62f68785586c9f4ff8d",
               ("data/commands.parquet", "README.md"),
               "Apache-2.0", "benign commands from a real sysadmin agent: hard negatives"),
    HubDataset("shell-safety", "tomngdev/shell-safety-v1.1", "fee89770c315d525ef2ee42adee6ef9725a7621e",
               ("*.jsonl", "README.md"),
               "MIT", "cross-source test or extra training rows (allow / ask / deny)"),
    HubDataset("permission-corpus", "cowWhySo/permission-command-corpus", "f9362497c1cc4bd16730cb5abd42396ad1d0142c",
               ("gold/*.csv", "README.md"),
               "Apache-2.0", "small obfuscation test"),
    HubDataset("rogue-security", "rogue-security/coding-agent-security-benchmark",
               "bf7ff748d80ca24db30b57c9255a2fb8884ed7eb", ("**/*.parquet", "README.md"),
               "CC BY-NC 4.0: non-commercial", "small test only"),
]


def fetch(source: HubDataset, raw_dir: Path = RAW_DIR) -> Path:
    from huggingface_hub import snapshot_download

    target = raw_dir / source.name
    snapshot_download(source.repo_id, repo_type="dataset", revision=source.revision,
                      allow_patterns=list(source.patterns), local_dir=target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=[s.name for s in SOURCES], help="fetch one source instead of all")
    args = parser.parse_args()
    for source in SOURCES:
        if args.only in (None, source.name):
            print(f"{source.name}: {source.repo_id}@{source.revision[:12]} -> {fetch(source)} [{source.licence}]")


if __name__ == "__main__":
    main()
