from __future__ import annotations

from pathlib import Path
import os
import sys


_TESTS_DIR = Path(__file__).resolve().parent
_AGENTS_DIR = _TESTS_DIR.parent
_MVP_DIR = _AGENTS_DIR.parent

for candidate in (str(_AGENTS_DIR), str(_MVP_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

os.environ.setdefault("MCP_BASE_URL", "http://mcp.test/mcp")
