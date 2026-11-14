"""Shell-aware features: what a command does to what, instead of which characters it holds.

A command is split into its pipeline segments; each segment gives its program,
subcommand, flags and the *kind* of every argument (a block device, a system
directory, the home folder, a folder that regenerates, a secret, a URL, ...).
Programs and argument kinds are also emitted as pairs, so a model can learn
"rm on a system directory" from one template family and apply it to another
that spells the path differently. Character n-grams cannot do that: they tie
the risk to the spelling.

`features(command)` returns a list of feature strings for a bag-of-features
vectoriser. It never raises: text it cannot parse falls back to whitespace
tokens.
"""

from __future__ import annotations

import re
import shlex

# Prefixes that run another program. They are kept as features and skipped when finding the program.
WRAPPERS = frozenset({"sudo", "doas", "env", "time", "nohup", "nice", "ionice", "command", "exec", "timeout",
                      "stdbuf", "busybox", "builtin", "setsid", "chronic",
                      # shell keywords that precede a program, including fish's `and`, `or`, `not`
                      "and", "or", "not", "then", "do", "else", "if", "while", "until", "!", "{", "}"})
SEPARATORS = frozenset({"|", "||", "&&", ";", "&", "|&", ";;", "(", ")"})
REDIRECTS = frozenset({">", ">>", "<", "<<", "<<<", ">&", "&>", ">|", "<>"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "fish", "python", "python3", "perl", "ruby", "node", "php",
                    "pwsh", "powershell", "eval", "source", "."})

_URL = re.compile(r"^(https?|ftp|ssh|git|s3|gs)://", re.IGNORECASE)
_PSEUDO_DEVICE = re.compile(r"^/dev/(null|zero|u?random|std(in|out|err)|tty|fd/\d+|full)$")
_SYSTEM = re.compile(r"^/(etc|usr|bin|sbin|boot|lib|lib64|var|sys|proc|opt|root|srv|run|System|Library|Applications"
                     r"|Windows|private)(/|$)")
_HOME = re.compile(r"^(~|\$HOME|\$\{HOME\}|/home(/|$)|/Users(/|$))")
_HOME_ROOT = re.compile(r"^(~|\$HOME|\$\{HOME\}|/home/[^/]+|/Users/[^/]+)/?(\*|\.\*)?$")
_TEMP = re.compile(r"^(/tmp|/var/tmp|\$TMPDIR|\$\{TMPDIR\})(/|$)")
_MOUNT = re.compile(r"^/(mnt|media|Volumes)(/|$)")
_SECRET = re.compile(r"(\.ssh|id_(rsa|dsa|ecdsa|ed25519)|\.env\b|credential|\.aws|\.netrc|shadow|sudoers|\.pem\b"
                     r"|\.key\b|kube/config|\.gnupg|\.pgpass|htpasswd|keychain|secret|token|passw)", re.IGNORECASE)
_SECRET_NAME = re.compile(r"(PASS|TOKEN|SECRET|KEY|CRED|AUTH)", re.IGNORECASE)
_REGENERATES = re.compile(r"(^|/)(build|dist|node_modules|\.cache|__pycache__|coverage|target|\.pytest_cache|out"
                          r"|\.next|\.tox|\.mypy_cache|\.gradle|vendor|tmp|scratch)(/|$)")
_OCTAL_MODE = re.compile(r"^[0-7]{3,4}$")
_SYMBOLIC_MODE = re.compile(r"^[ugoa]*[+=-][rwxXstugo]+(,[ugoa]*[+=-][rwxXstugo]+)*$")
_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?[A-Za-z%]{0,3}$")
_WORD = re.compile(r"^[A-Za-z][A-Za-z0-9_.+-]*$")
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.DOTALL)
_SHORT_FLAGS = re.compile(r"^-[A-Za-z]{2,3}$")


def tokens(command: str) -> list[str]:
    """Shell words and operators, quotes removed; whitespace tokens if the quoting is broken."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return command.split()


def kinds(arg: str) -> list[str]:
    """What an argument refers to. The first entry is its main kind; the rest are extra marks."""
    out: list[str]
    if _URL.match(arg):
        out = ["URL", "URL_PLAIN" if arg.lower().startswith(("http://", "ftp://")) else "URL_TLS"]
    elif _PSEUDO_DEVICE.match(arg):
        out = ["PSEUDODEV", "PSEUDODEV:" + arg.rsplit("/", 1)[-1]]
    elif arg.startswith("/dev/"):
        out = ["BLOCKDEV", "PARTITION" if re.search(r"(\dp?\d+|[a-z]\d+)$", arg) else "WHOLEDISK"]
    elif arg in ("/", "/*", "/.", "/.*"):
        out = ["ROOT"]
    elif _TEMP.match(arg):
        out = ["TEMP"]
    elif _SYSTEM.match(arg):
        parts = [p for p in arg.split("/") if p]
        out = ["SYSTEM", "SYSTEM:" + parts[0], f"SYSDEPTH:{min(len(parts), 3)}"]
    elif _HOME.match(arg):
        out = ["HOME"] + (["HOMEROOT"] if _HOME_ROOT.match(arg) else [])
    elif _MOUNT.match(arg):
        out = ["MOUNT"]
    elif arg.startswith("$") or arg.startswith("${"):
        out = ["VAR"] + (["SECRETVAR"] if _SECRET_NAME.search(arg.split("/", 1)[0]) else [])
        if "/" in arg:
            out.append("VARPATH")
    elif _OCTAL_MODE.match(arg):
        out = ["MODE", "MODE:" + arg[-3:], "MODE_WORLD_WRITE" if int(arg[-1]) & 2 else "MODE_NO_WORLD_WRITE"]
    elif _SYMBOLIC_MODE.match(arg):
        out = ["MODE", "SMODE:" + arg]
    elif _NUMBER.match(arg):
        out = ["NUM"]
    elif arg.startswith("/"):
        out = ["ABSPATH"]
    elif "/" in arg or arg.startswith("."):
        out = ["RELPATH"]
    elif _WORD.match(arg):
        out = ["WORD"]
    else:
        out = ["OTHER"]
    if "$(" in arg or "`" in arg:
        out.append("SUBSTITUTION")
    if "*" in arg or "?" in arg:
        out.append("DOTGLOB" if ".*" in arg else "GLOB")
    if _SECRET.search(arg):
        out.append("SECRET")
    if _REGENERATES.search(arg):
        out.append("REGENERATES")
    if " " in arg.strip():
        out.append("SCRIPT")
    return out


def _flag_names(token: str) -> list[str]:
    """`-rf` as `-r`, `-f`; `--force=yes` as `--force`; anything else, such as find's `-exec`, as written."""
    if token.startswith("--"):
        return [token.split("=", 1)[0]]
    if _SHORT_FLAGS.match(token):
        return ["-" + letter for letter in token[1:]]
    return [token]


def _segment(words: list[str], out: list[str], prefix: str, piped: bool, depth: int) -> str | None:
    """Features of one pipeline segment; returns its program."""
    i = 0
    while i < len(words):
        word = words[i]
        base = word.rsplit("/", 1)[-1]
        if base in WRAPPERS:
            out.append(f"{prefix}wrap:{base}")
        elif not (_ASSIGNMENT.match(word) or word.startswith("-") or _NUMBER.match(word)):
            break
        i += 1
    if i == len(words):
        return None
    program = words[i].rsplit("/", 1)[-1]
    out.append(f"{prefix}prog:{program}")
    if piped:
        out.append(f"{prefix}pipe_into:{program}")
        if program in SHELLS:
            out.append(f"{prefix}pipe_into_shell")
    positional = 0
    for word in words[i + 1:]:
        out.append(f"{prefix}tok:{word[:40]}")
        assignment = _ASSIGNMENT.match(word)
        if word.startswith("-") and len(word) > 1 and not _NUMBER.match(word):
            for flag in _flag_names(word):
                out += [f"{prefix}flag:{flag}", f"{prefix}{program}~{flag}"]
            if "=" in word:
                out += [f"{prefix}{program}~{word.split('=', 1)[0]}={k}" for k in kinds(word.split("=", 1)[1])]
            continue
        if assignment:
            key, value = assignment.groups()
            out.append(f"{prefix}{program}~{key}=")
            out += [f"{prefix}{program}~{key}={k}" for k in kinds(value)]
            out += [f"{prefix}arg:{k}" for k in kinds(value)]
            continue
        positional += 1
        found = kinds(word)
        out += [f"{prefix}arg:{k}" for k in found]
        out += [f"{prefix}{program}~{k}" for k in found]
        if positional <= 2 and found[0] == "WORD":
            out.append(f"{prefix}{program}~sub{positional}:{word}")
        if "SCRIPT" in found and depth < 2:
            _command(word, out, prefix + "in:", depth + 1)
    return program


def _command(command: str, out: list[str], prefix: str, depth: int) -> None:
    words: list[str] = []
    programs: list[str] = []
    piped = False
    stream = tokens(command) + [";"]
    skip = False
    for n, token in enumerate(stream):
        if skip:
            skip = False
            continue
        if token in REDIRECTS:
            target = stream[n + 1] if n + 1 < len(stream) - 1 else ""
            out += [f"{prefix}redir:{token}"] + [f"{prefix}redir:{token}{k}" for k in kinds(target)] if target else []
            skip = bool(target)
            continue
        if token in SEPARATORS:
            if words:
                program = _segment(words, out, prefix, piped, depth)
                if program:
                    programs.append(program)
            out.append(f"{prefix}op:{token}")
            piped = token in ("|", "|&")
            words = []
            continue
        words.append(token)
    out += [f"{prefix}flow:{a}>{b}" for a, b in zip(programs, programs[1:])]
    out.append(f"{prefix}segments:{min(len(programs), 4)}")


def features(command: str) -> list[str]:
    """The feature strings of one command."""
    out: list[str] = []
    _command(command, out, "", 0)
    return out
