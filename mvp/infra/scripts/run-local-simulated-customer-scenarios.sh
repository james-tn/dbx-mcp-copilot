#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

cd "$ROOT_DIR"
uv run --project "$ROOT_DIR" --group dev python -m pytest -q agents/tests/test_local_customer_planner_scenarios.py
