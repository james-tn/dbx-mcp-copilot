"""Helpers for loading the MVP runtime environment exactly once per process."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def get_runtime_env_path() -> Path:
    return Path(__file__).resolve().parent.parent / ".env"


@lru_cache(maxsize=1)
def ensure_runtime_env_loaded() -> Path:
    env_path = get_runtime_env_path()
    if "pytest" in sys.modules:
        allow_during_tests = os.environ.get("RI_LOAD_ENV_DURING_TESTS", "").strip().lower()
        if allow_during_tests not in {"1", "true", "yes", "on"}:
            return env_path
    if env_path.exists():
        from dotenv import load_dotenv

        load_dotenv(env_path)
    return env_path
