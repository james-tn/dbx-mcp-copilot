#!/usr/bin/env bash
set -euo pipefail

ARTIFACT_NAME="${ARTIFACT_NAME:-}"
RELEASE_SHA="${RELEASE_SHA:-}"
OUTPUT_DIR="${OUTPUT_DIR:-}"
WORKFLOW_FILE="${WORKFLOW_FILE:-ci.yml}"
WAIT_SECONDS="${WAIT_SECONDS:-300}"
POLL_INTERVAL_SECONDS="${POLL_INTERVAL_SECONDS:-10}"
GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-}"
GH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"

if [[ -z "$ARTIFACT_NAME" ]]; then
  echo "ARTIFACT_NAME is required." >&2
  exit 1
fi

if [[ -z "$RELEASE_SHA" ]]; then
  echo "RELEASE_SHA is required." >&2
  exit 1
fi

if [[ -z "$OUTPUT_DIR" ]]; then
  echo "OUTPUT_DIR is required." >&2
  exit 1
fi

if [[ -z "$GITHUB_REPOSITORY" ]]; then
  echo "GITHUB_REPOSITORY is required." >&2
  exit 1
fi

if [[ -z "$GH_TOKEN" ]]; then
  echo "GH_TOKEN or GITHUB_TOKEN is required." >&2
  exit 1
fi

deadline=$(( $(date +%s) + WAIT_SECONDS ))
mkdir -p "$OUTPUT_DIR"

find_artifact_download_url() {
  local runs_json
  local run_id
  local artifacts_json

  runs_json="$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${WORKFLOW_FILE}/runs?head_sha=${RELEASE_SHA}&status=completed&per_page=20")"
  run_id="$(python - <<'PY' "$runs_json"
import json
import sys

payload = json.loads(sys.argv[1])
for run in payload.get("workflow_runs", []):
    if str(run.get("conclusion", "")).lower() == "success":
        print(run.get("id", ""))
        break
PY
)"
  if [[ -z "$run_id" ]]; then
    return 1
  fi

  artifacts_json="$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${run_id}/artifacts?per_page=100")"
  python - <<'PY' "$artifacts_json" "$ARTIFACT_NAME"
import json
import sys

payload = json.loads(sys.argv[1])
artifact_name = sys.argv[2]
for artifact in payload.get("artifacts", []):
    if artifact.get("name") == artifact_name and not artifact.get("expired", False):
        print(artifact.get("archive_download_url", ""))
        break
PY
}

download_url=""
while [[ -z "$download_url" ]]; do
  if download_url="$(find_artifact_download_url)"; then
    if [[ -n "$download_url" ]]; then
      break
    fi
  fi
  if (( $(date +%s) >= deadline )); then
    echo "Timed out waiting for artifact '$ARTIFACT_NAME' for sha '$RELEASE_SHA' from workflow '$WORKFLOW_FILE'." >&2
    exit 1
  fi
  sleep "$POLL_INTERVAL_SECONDS"
done

zip_path="$(mktemp)"
trap 'rm -f "$zip_path"' EXIT
curl -fsSL \
  -H "Authorization: Bearer $GH_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  "$download_url" \
  -o "$zip_path"
unzip -o "$zip_path" -d "$OUTPUT_DIR" >/dev/null
