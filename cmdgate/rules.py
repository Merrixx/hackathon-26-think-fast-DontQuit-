"""Rules baseline: a regex denylist of the kind permission systems ship today.

This is the "few rules" baseline the report compares the model against. A rule
has no real confidence, so a hit and a miss each map to one fixed probability
per risk level; `fit_level_rates` replaces the defaults with the level rates
measured among hits and among misses on a training split.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .questions import LEVELS

HIT_LEVELS = [0.1, 0.0, 0.1, 0.4, 0.4]
MISS_LEVELS = [0.9, 0.1, 0.0, 0.0, 0.0]

RULES: list[tuple[str, str]] = [
    ("recursive_force_delete", r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*[rR]|--recursive\s+--force|--force\s+--recursive)"),
    ("delete_via_find", r"\bfind\b.*\s(-delete|-exec\s+rm)\b"),
    ("disk_write", r"\b(dd\s+.*\bof=|mkfs(\.\w+)?\b|fdisk\b|parted\b|wipefs\b|shred\b)"),
    ("write_to_device", r">\s*/dev/(sd|nvme|disk|hd)"),
    ("pipe_to_shell", r"\b(curl|wget|fetch)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"),
    ("eval_remote", r"\b(eval|source|\.)\s+[\"']?\$\((curl|wget)\b"),
    ("privilege", r"\b(sudo|doas|su)\b"),
    ("open_permissions", r"\bchmod\s+(-[a-zA-Z]+\s+)*(777|a\+rwx|o\+w)\b"),
    ("recursive_owner", r"\bcho(wn|grp)\s+(-[a-zA-Z]*R|--recursive)"),
    ("git_history_rewrite", r"\bgit\s+(push\s+.*(--force\b|-f\b|--mirror)|reset\s+--hard|clean\s+-[a-zA-Z]*f|filter-(branch|repo)|branch\s+-D)"),
    ("drop_data", r"\b(drop\s+(table|database|schema)|truncate\s+table|delete\s+from\s+\w+\s*;?\s*$)"),
    ("infra_destroy", r"\b(terraform\s+(destroy|apply\s+.*-auto-approve)|kubectl\s+delete|helm\s+uninstall|docker\s+(system|volume|image)\s+prune|aws\s+\w+\s+(delete|terminate)\S*)"),
    ("kill_or_power", r"\b(kill\s+-9\s+-?1\b|killall\b|pkill\b|shutdown\b|reboot\b|halt\b|poweroff\b)"),
    ("fork_bomb", r":\(\)\s*\{\s*:\|:&\s*\};:"),
    ("reverse_shell", r"(/dev/tcp/|\bnc\b.*\s-e\b|\bncat\b.*--exec|\bsocat\b.*exec:)"),
    ("decode_and_run", r"\bbase64\s+(-d|--decode|-D)\b.*\|\s*(ba|z)?sh\b"),
    ("secrets", r"(\.ssh/|id_rsa|\.aws/credentials|\.env\b|/etc/(shadow|passwd|sudoers))"),
    ("history_or_logs", r"(\bhistory\s+-c\b|>\s*~?/?\S*(\.bash_history|\.zsh_history)|\brm\b.*\s/var/log)"),
    ("publish", r"\b(npm|pnpm|yarn)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b|\bgh\s+release\s+(create|delete)\b"),
    ("system_paths", r"(\s|^)(rm|mv|cp|chmod|chown|tee|>)\s[^|;&]*\s/(etc|usr|bin|sbin|boot|var|lib|System|Library)(/|\s|$)"),
]

_COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in RULES]


def matched_rules(command: str) -> list[str]:
    """Names of the rules a command trips, in rule order."""
    return [name for name, pattern in _COMPILED if pattern.search(command)]


class RulesScorer:
    """A probability per risk level from the denylist: one distribution for a hit, one for a miss."""

    def __init__(self, hit: list[float] | None = None, miss: list[float] | None = None) -> None:
        self.hit = hit or HIT_LEVELS
        self.miss = miss or MISS_LEVELS

    def levels(self, command: str) -> list[float]:
        return self.hit if matched_rules(command) else self.miss


def fit_level_rates(commands: Iterable[str], levels: Iterable[int]) -> tuple[list[float], list[float]]:
    """Measured share of each risk level among rule hits and among misses, as (hit, miss)."""
    counts = {True: [0] * len(LEVELS), False: [0] * len(LEVELS)}
    for command, level in zip(commands, levels, strict=True):
        counts[bool(matched_rules(command))][level] += 1

    def rates(row: list[int], default: list[float]) -> list[float]:
        total = sum(row)
        return [n / total for n in row] if total else default

    return rates(counts[True], HIT_LEVELS), rates(counts[False], MISS_LEVELS)
