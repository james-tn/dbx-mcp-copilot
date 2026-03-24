#!/usr/bin/env bash

bootstrap_status_file_for_mode() {
  local root_dir="$1"
  local mode="$2"
  printf '%s/infra/outputs/bootstrap-status-%s.json\n' "$root_dir" "$mode"
}

_bootstrap_status_patch() {
  local status_file="$1"
  shift

  python3 - <<'PY' "$status_file" "$@"
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
path.parent.mkdir(parents=True, exist_ok=True)
if path.exists():
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {}
else:
    data = {}

for raw in sys.argv[2:]:
    key, value = raw.split("=", 1)
    if value in {"true", "false", "null"}:
      parsed = json.loads(value)
    else:
      parsed = value
    data[key] = parsed

data["updated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY
}

bootstrap_status_init() {
  local status_file="$1"
  local mode="$2"
  local phase="$3"
  local input_file="$4"
  local runtime_file="$5"
  local split_mode="$6"

  _bootstrap_status_patch "$status_file" \
    "mode=$mode" \
    "phase=$phase" \
    "status=running" \
    "input_file=$input_file" \
    "runtime_file=$runtime_file" \
    "split_responsibility_mode=$split_mode" \
    "last_successful_step=" \
    "next_required_role=" \
    "next_step_script=" \
    "message="
}

bootstrap_status_note_step() {
  local status_file="$1"
  local step_name="$2"
  _bootstrap_status_patch "$status_file" \
    "status=running" \
    "last_successful_step=$step_name" \
    "message="
}

bootstrap_status_pause() {
  local status_file="$1"
  local role_name="$2"
  local next_step_script="$3"
  local message="$4"

  _bootstrap_status_patch "$status_file" \
    "status=paused" \
    "next_required_role=$role_name" \
    "next_step_script=$next_step_script" \
    "message=$message"
}

bootstrap_status_fail() {
  local status_file="$1"
  local message="$2"

  _bootstrap_status_patch "$status_file" \
    "status=failed" \
    "message=$message"
}

bootstrap_status_complete() {
  local status_file="$1"
  local message="$2"

  _bootstrap_status_patch "$status_file" \
    "status=completed" \
    "next_required_role=" \
    "next_step_script=" \
    "message=$message"
}
