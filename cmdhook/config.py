"""Settings for the hook app, read from `cmdhook/.env` and the process environment.

Every setting is a `CMDHOOK_*` variable. A variable set in the environment
wins over the same variable in the file, so one start can override the file:

    CMDHOOK_THRESHOLD=0.2 uv run --extra ml python -m cmdhook.start

`cmdhook/.env.example` lists every setting with its default. `hook.js` reads
the same file for the address of the backend.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
ENV_PATH = APP_DIR / ".env"
EXAMPLE_PATH = APP_DIR / ".env.example"
PREFIX = "CMDHOOK_"

COMBINE = ("all", "mean", "any")
ON_PASS = ("allow", "defer")
ON_FAIL = ("ask", "deny")


class ConfigError(ValueError):
    """A setting has a value the app cannot use."""


@dataclass(frozen=True)
class Config:
    models: tuple[str, ...] = ("rules", "ngram", "encoder")
    endpoints: dict[str, str] = field(default_factory=dict)
    threshold: float = 0.5
    thresholds: dict[str, float] = field(default_factory=dict)
    combine: str = "all"
    on_pass: str = "allow"
    on_fail: str = "ask"
    host: str = "127.0.0.1"
    port: int = 8019
    kev_run: str = ""
    kev_dir: str = "../kev"
    kev_port: int = 8011
    kev_start_seconds: float = 300.0
    log: str = "~/.ki-guard/log.jsonl"

    @property
    def names(self) -> list[str]:
        """Every model the backend serves: the listed ones, then the endpoints."""
        return [*self.models, *(name for name in self.endpoints if name not in self.models)]

    def threshold_for(self, name: str) -> float:
        return self.thresholds.get(env_key(name), self.threshold)


def env_key(name: str) -> str:
    """A model name as it appears in `CMDHOOK_THRESHOLD_<NAME>`: upper case, anything else as `_`."""
    return re.sub(r"[^A-Z0-9]", "_", name.upper())


def parse_env(text: str) -> dict[str, str]:
    """`KEY=value` lines. Blank lines and `#` comments are skipped; one pair of quotes around a value is dropped."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if not sep:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def read_settings(path: Path = ENV_PATH, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The non-empty `CMDHOOK_*` settings, prefix dropped; the environment wins over the file."""
    merged = parse_env(path.read_text()) if path.exists() else {}
    merged.update(os.environ if environ is None else environ)
    return {key[len(PREFIX):]: value for key, value in merged.items() if key.startswith(PREFIX) and value.strip()}


def _probability(key: str, value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        number = -1.0
    if not 0.0 <= number <= 1.0:
        raise ConfigError(f"{PREFIX}{key} must be a number from 0 to 1, got {value!r}")
    return number


def _choice(key: str, value: str, allowed: tuple[str, ...]) -> str:
    if value not in allowed:
        raise ConfigError(f"{PREFIX}{key} must be one of {', '.join(allowed)}, got {value!r}")
    return value


def _number(key: str, value: str, kind: type) -> float:
    try:
        return kind(value)
    except ValueError:
        raise ConfigError(f"{PREFIX}{key} must be a number, got {value!r}") from None


def _names(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def load(path: Path = ENV_PATH, environ: Mapping[str, str] | None = None) -> Config:
    """The settings as a `Config`. Raises `ConfigError` for a value the app cannot use."""
    raw = read_settings(path, environ)
    default = Config()
    endpoints = {}
    for spec in _names(raw.get("ENDPOINTS", "")):
        name, sep, url = spec.partition("=")
        if not sep or not name or not url:
            raise ConfigError(f"{PREFIX}ENDPOINTS expects name=url entries, got {spec!r}")
        endpoints[name] = url
    models = tuple(_names(raw["MODELS"])) if "MODELS" in raw else default.models
    if len(set(models)) != len(models):
        raise ConfigError(f"{PREFIX}MODELS lists a model twice: {', '.join(models)}")
    return Config(
        models=models,
        endpoints=endpoints,
        threshold=_probability("THRESHOLD", raw["THRESHOLD"]) if "THRESHOLD" in raw else default.threshold,
        thresholds={key[len("THRESHOLD_"):]: _probability(key, value)
                    for key, value in raw.items() if key.startswith("THRESHOLD_")},
        combine=_choice("COMBINE", raw.get("COMBINE", default.combine), COMBINE),
        on_pass=_choice("ON_PASS", raw.get("ON_PASS", default.on_pass), ON_PASS),
        on_fail=_choice("ON_FAIL", raw.get("ON_FAIL", default.on_fail), ON_FAIL),
        host=raw.get("HOST", default.host),
        port=int(_number("PORT", raw["PORT"], int)) if "PORT" in raw else default.port,
        kev_run=raw.get("KEV_RUN", default.kev_run),
        kev_dir=raw.get("KEV_DIR", default.kev_dir),
        kev_port=int(_number("KEV_PORT", raw["KEV_PORT"], int)) if "KEV_PORT" in raw else default.kev_port,
        kev_start_seconds=(_number("KEV_START_SECONDS", raw["KEV_START_SECONDS"], float)
                           if "KEV_START_SECONDS" in raw else default.kev_start_seconds),
        log="" if raw.get("LOG", default.log).lower() in ("off", "none", "0") else raw.get("LOG", default.log),
    )
