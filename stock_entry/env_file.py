"""Reads chosen keys from the project's .env file (gitignored) into the environment.

Accepts `KEY=value`, `KEY = value`, `KEY = "value"` and `export KEY=value`; `#` lines are skipped.
A variable already set in the environment wins over the file, so GitHub / Streamlit secrets still apply.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)
PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_env_file(names: Iterable[str], path: Path | None = None) -> list[str]:
    """Set the `names` found in `path` that are not already set; returns the names it set (never values)."""
    path = PROJECT_ENV if path is None else path
    try:
        values = parse_env(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("Could not read %s: %s", path.name, exc)
        return []
    loaded = [name for name in names if values.get(name) and not os.environ.get(name)]
    for name in loaded:
        os.environ[name] = values[name]
    return loaded
