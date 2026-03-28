#!/usr/bin/env bash
set -euo pipefail

OUTPUT_PATH="${OUTPUT_PATH:-}"
GIT_SHA="${GIT_SHA:-${GITHUB_SHA:-}}"
GIT_REF="${GIT_REF:-${GITHUB_REF_NAME:-${GITHUB_REF:-}}}"
BUILT_AT_UTC="${BUILT_AT_UTC:-$(date -u +"%Y-%m-%dT%H:%M:%SZ")}"
PLANNER_IMAGE="${PLANNER_IMAGE:-}"
PLANNER_IMAGE_DIGEST="${PLANNER_IMAGE_DIGEST:-}"
WRAPPER_IMAGE="${WRAPPER_IMAGE:-}"
WRAPPER_IMAGE_DIGEST="${WRAPPER_IMAGE_DIGEST:-}"
M365_PACKAGE_ARTIFACT_NAME="${M365_PACKAGE_ARTIFACT_NAME:-}"
DEPLOYMENT_MODE="${DEPLOYMENT_MODE:-secure}"
INTEGRATION_PROFILE="${INTEGRATION_PROFILE:-secure-mock}"
PRODUCTION_PROFILE="${PRODUCTION_PROFILE:-secure-customer}"

if [[ -z "$OUTPUT_PATH" ]]; then
  echo "OUTPUT_PATH is required." >&2
  exit 1
fi

if [[ -z "$GIT_SHA" ]]; then
  echo "GIT_SHA is required." >&2
  exit 1
fi

if [[ -z "$PLANNER_IMAGE" ]]; then
  echo "PLANNER_IMAGE is required." >&2
  exit 1
fi

if [[ -z "$WRAPPER_IMAGE" ]]; then
  echo "WRAPPER_IMAGE is required." >&2
  exit 1
fi

python - <<'PY' \
  "$OUTPUT_PATH" \
  "$GIT_SHA" \
  "$GIT_REF" \
  "$BUILT_AT_UTC" \
  "$PLANNER_IMAGE" \
  "$PLANNER_IMAGE_DIGEST" \
  "$WRAPPER_IMAGE" \
  "$WRAPPER_IMAGE_DIGEST" \
  "$M365_PACKAGE_ARTIFACT_NAME" \
  "$DEPLOYMENT_MODE" \
  "$INTEGRATION_PROFILE" \
  "$PRODUCTION_PROFILE"
import json
import sys
from pathlib import Path

(
    output_path,
    git_sha,
    git_ref,
    built_at_utc,
    planner_image,
    planner_image_digest,
    wrapper_image,
    wrapper_image_digest,
    m365_package_artifact_name,
    deployment_mode,
    integration_profile,
    production_profile,
) = sys.argv[1:]

payload = {
    "git_sha": git_sha,
    "git_ref": git_ref,
    "built_at_utc": built_at_utc,
    "planner_image": planner_image,
    "planner_image_digest": planner_image_digest,
    "wrapper_image": wrapper_image,
    "wrapper_image_digest": wrapper_image_digest,
    "m365_package_artifact_name": m365_package_artifact_name,
    "deployment_mode": deployment_mode,
    "integration_profile": integration_profile,
    "production_profile": production_profile,
}

output = Path(output_path)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
