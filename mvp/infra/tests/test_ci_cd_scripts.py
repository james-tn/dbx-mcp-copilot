from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]


def run_script(script_path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script_path)],
        check=True,
        text=True,
        capture_output=True,
        cwd=ROOT_DIR,
        env=env,
    )


def test_ci_write_release_metadata_emits_expected_contract(tmp_path: Path) -> None:
    script = ROOT_DIR / "infra" / "scripts" / "ci-write-release-metadata.sh"
    output_path = tmp_path / "release-metadata.json"

    env = os.environ.copy()
    env.update(
        {
            "OUTPUT_PATH": str(output_path),
            "GIT_SHA": "abc123def456",
            "GIT_REF": "integration",
            "BUILT_AT_UTC": "2026-03-28T12:00:00Z",
            "PLANNER_IMAGE": "example.azurecr.io/daily-account-planner/planner:secure-abc123",
            "PLANNER_IMAGE_DIGEST": "sha256:planner",
            "WRAPPER_IMAGE": "example.azurecr.io/daily-account-planner/wrapper:secure-abc123",
            "WRAPPER_IMAGE_DIGEST": "sha256:wrapper",
            "M365_PACKAGE_ARTIFACT_NAME": "m365-package-abc123",
        }
    )

    run_script(script, env)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload == {
        "git_sha": "abc123def456",
        "git_ref": "integration",
        "built_at_utc": "2026-03-28T12:00:00Z",
        "planner_image": "example.azurecr.io/daily-account-planner/planner:secure-abc123",
        "planner_image_digest": "sha256:planner",
        "wrapper_image": "example.azurecr.io/daily-account-planner/wrapper:secure-abc123",
        "wrapper_image_digest": "sha256:wrapper",
        "m365_package_artifact_name": "m365-package-abc123",
        "deployment_mode": "secure",
        "integration_profile": "secure-mock",
        "production_profile": "secure-customer",
    }


def test_ci_render_runtime_env_uses_release_metadata_without_mutating_examples(tmp_path: Path) -> None:
    script = ROOT_DIR / "infra" / "scripts" / "ci-render-runtime-env.sh"
    metadata_path = tmp_path / "release-metadata.json"
    output_env_file = tmp_path / "generated.env"
    secure_example = ROOT_DIR / ".env.secure.example"
    original_example = secure_example.read_text(encoding="utf-8")

    metadata_path.write_text(
        json.dumps(
            {
                "git_sha": "abc123def456",
                "git_ref": "integration",
                "built_at_utc": "2026-03-28T12:00:00Z",
                "planner_image": "example.azurecr.io/daily-account-planner/planner:secure-abc123",
                "planner_image_digest": "",
                "wrapper_image": "example.azurecr.io/daily-account-planner/wrapper:secure-abc123",
                "wrapper_image_digest": "",
                "m365_package_artifact_name": "m365-package-abc123",
                "deployment_mode": "secure",
                "integration_profile": "secure-mock",
                "production_profile": "secure-customer",
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "MODE": "secure",
            "PROFILE": "secure-mock",
            "OUTPUT_ENV_FILE": str(output_env_file),
            "RELEASE_METADATA_PATH": str(metadata_path),
            "AZURE_TENANT_ID": "tenant-id",
            "AZURE_SUBSCRIPTION_ID": "sub-id",
            "AZURE_RESOURCE_GROUP": "rg-integration",
            "AZURE_LOCATION": "eastus2",
            "ACA_ENVIRONMENT_NAME": "aca-secure-int",
            "ACR_NAME": "intacr",
            "AZURE_OPENAI_ENDPOINT": "https://example.openai.azure.com/",
            "AZURE_OPENAI_DEPLOYMENT": "gpt-5.2-chat",
            "PLANNER_API_CLIENT_ID": "planner-client-id",
            "PLANNER_API_CLIENT_SECRET": "planner-secret",
            "PLANNER_API_EXPECTED_AUDIENCE": "api://planner",
            "BOT_APP_ID": "bot-id",
            "BOT_APP_PASSWORD": "bot-secret",
            "CUSTOMER_DATABRICKS_HOST": "https://adb.example.azuredatabricks.net",
            "CUSTOMER_DATABRICKS_AZURE_RESOURCE_ID": "/subscriptions/sub-id/resourceGroups/rg/providers/Microsoft.Databricks/workspaces/dbx",
            "CUSTOMER_DATABRICKS_WAREHOUSE_ID": "warehouse-123",
            "CUSTOMER_TOP_OPPORTUNITIES_SOURCE": "catalog.schema.account_iq_scores",
            "CUSTOMER_CONTACTS_SOURCE": "catalog.schema.aiq_contact",
            "CONTAINER_REGISTRY_SERVER": "intacr.azurecr.io",
            "CONTAINER_REGISTRY_USERNAME": "intacr",
            "CONTAINER_REGISTRY_PASSWORD": "registry-secret",
            "WRAPPER_BASE_URL": "https://wrapper.example.com",
        }
    )

    run_script(script, env)
    rendered = output_env_file.read_text(encoding="utf-8")

    assert "PLANNER_API_IMAGE=example.azurecr.io/daily-account-planner/planner:secure-abc123" in rendered
    assert "WRAPPER_IMAGE=example.azurecr.io/daily-account-planner/wrapper:secure-abc123" in rendered
    assert "AZURE_OPENAI_AUTO_ROLE_ASSIGN=false" in rendered
    assert "ACA_ENVIRONMENT_NAME=aca-secure-int" in rendered
    assert "DATABRICKS_WAREHOUSE_ID=warehouse-123" in rendered
    assert "TOP_OPPORTUNITIES_SOURCE=catalog.schema.account_iq_scores" in rendered
    assert "CONTACTS_SOURCE=catalog.schema.aiq_contact" in rendered
    assert "CONTAINER_REGISTRY_SERVER=intacr.azurecr.io" in rendered
    assert "CONTAINER_REGISTRY_USERNAME=intacr" in rendered
    assert "CONTAINER_REGISTRY_PASSWORD=registry-secret" in rendered
    assert secure_example.read_text(encoding="utf-8") == original_example


def test_ci_redact_env_file_redacts_sensitive_values(tmp_path: Path) -> None:
    script = ROOT_DIR / "infra" / "scripts" / "ci-redact-env-file.sh"
    input_path = tmp_path / "runtime.env"
    output_path = tmp_path / "runtime.redacted.env"

    input_path.write_text(
        "\n".join(
            [
                "PLANNER_API_BASE_URL=https://planner.example.com",
                "PLANNER_API_CLIENT_SECRET=super-secret",
                "BOT_APP_PASSWORD=hunter2",
                "AZURE_OPENAI_API_KEY=abc123",
                "PLANNER_API_BEARER_TOKEN=opaque",
                "WRAPPER_BASE_URL=https://wrapper.example.com",
                "",
            ]
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "INPUT_ENV_FILE": str(input_path),
            "OUTPUT_ENV_FILE": str(output_path),
        }
    )

    run_script(script, env)
    rendered = output_path.read_text(encoding="utf-8")

    assert "PLANNER_API_BASE_URL=https://planner.example.com" in rendered
    assert "WRAPPER_BASE_URL=https://wrapper.example.com" in rendered
    assert "PLANNER_API_CLIENT_SECRET=<redacted>" in rendered
    assert "BOT_APP_PASSWORD=<redacted>" in rendered
    assert "AZURE_OPENAI_API_KEY=<redacted>" in rendered
    assert "PLANNER_API_BEARER_TOKEN=<redacted>" in rendered


def test_ci_download_release_artifact_prefers_push_run_when_pr_run_shares_sha(tmp_path: Path) -> None:
    script = ROOT_DIR / "infra" / "scripts" / "ci-download-release-artifact.sh"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    output_dir = tmp_path / "release"
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    runs_json = {
        "workflow_runs": [
            {"id": 2002, "event": "pull_request", "conclusion": "success"},
            {"id": 1001, "event": "push", "conclusion": "success"},
        ]
    }
    pr_artifacts_json = {
        "artifacts": [
            {"name": "m365-package-deadbeef", "expired": False, "archive_download_url": "https://example.invalid/pr.zip"}
        ]
    }
    push_artifacts_json = {
        "artifacts": [
            {
                "name": "release-metadata-deadbeef",
                "expired": False,
                "archive_download_url": "https://example.invalid/push.zip",
            }
        ]
    }

    (bin_dir / "gh").write_text(
        f"""#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
state_dir = Path({str(state_dir)!r})
(state_dir / "gh.calls").write_text((state_dir / "gh.calls").read_text() + " ".join(args) + "\\n" if (state_dir / "gh.calls").exists() else " ".join(args) + "\\n")
if args[:1] != ["api"]:
    raise SystemExit("unexpected gh arguments: " + " ".join(args))
request = args[1]
if "actions/workflows/ci.yml/runs" in request:
    print({json.dumps(runs_json)!r})
elif "actions/runs/2002/artifacts" in request:
    print({json.dumps(pr_artifacts_json)!r})
elif "actions/runs/1001/artifacts" in request:
    print({json.dumps(push_artifacts_json)!r})
else:
    raise SystemExit("unexpected gh api request: " + request)
""",
        encoding="utf-8",
    )
    (bin_dir / "curl").write_text(
        """#!/usr/bin/env python3
import sys
from pathlib import Path

args = sys.argv[1:]
output_path = Path(args[args.index("-o") + 1])
output_path.write_text("fake zip payload", encoding="utf-8")
""",
        encoding="utf-8",
    )
    (bin_dir / "unzip").write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
dest = Path(args[args.index("-d") + 1])
dest.mkdir(parents=True, exist_ok=True)
(dest / "release-metadata.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    for path in (bin_dir / "gh", bin_dir / "curl", bin_dir / "unzip"):
        path.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "ARTIFACT_NAME": "release-metadata-deadbeef",
            "RELEASE_SHA": "deadbeef",
            "OUTPUT_DIR": str(output_dir),
            "WORKFLOW_FILE": "ci.yml",
            "WAIT_SECONDS": "1",
            "POLL_INTERVAL_SECONDS": "0",
            "GITHUB_REPOSITORY": "example/repo",
            "GH_TOKEN": "test-token",
            "PATH": f"{bin_dir}:{env['PATH']}",
        }
    )

    run_script(script, env)

    assert (output_dir / "release-metadata.json").exists()
    gh_calls = (state_dir / "gh.calls").read_text(encoding="utf-8")
    assert "actions/runs/1001/artifacts" in gh_calls
    assert "actions/runs/2002/artifacts" not in gh_calls


def test_validate_planner_service_e2e_uses_azure_for_internal_hosts(tmp_path: Path) -> None:
    script = ROOT_DIR / "infra" / "scripts" / "validate-planner-service-e2e.sh"
    env_file = tmp_path / "internal.env"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    env_file.write_text(
        "\n".join(
            [
                "PLANNER_API_BASE_URL=https://planner.internal.example.com",
                "AZURE_SUBSCRIPTION_ID=sub-id",
                "AZURE_RESOURCE_GROUP=rg-integration",
                "PLANNER_ACA_APP_NAME=planner-app",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (bin_dir / "az").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
cat <<'JSON'
{
  "name": "planner-app",
  "properties": {
    "runningStatus": "Running",
    "provisioningState": "Succeeded",
    "latestReadyRevisionName": "planner-app--0000010",
    "configuration": {
      "ingress": {
        "fqdn": "planner.internal.example.com",
        "external": false
      }
    }
  }
}
JSON
""",
        encoding="utf-8",
    )
    (bin_dir / "curl").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho 'curl should not be called for internal planner validation' >&2\nexit 99\n",
        encoding="utf-8",
    )
    for path in (bin_dir / "az", bin_dir / "curl"):
        path.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PATH": f"{bin_dir}:{env['PATH']}",
        }
    )

    completed = run_script(script, env)

    assert "Planner URL is internal; validating Container App state via Azure control plane..." in completed.stdout
    assert '"running_status": "Running"' in completed.stdout
    assert "authenticated chat validation skipped" in completed.stdout


def test_validate_planner_service_e2e_uses_curl_for_public_hosts(tmp_path: Path) -> None:
    script = ROOT_DIR / "infra" / "scripts" / "validate-planner-service-e2e.sh"
    env_file = tmp_path / "public.env"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    env_file.write_text(
        "\n".join(
            [
                "PLANNER_API_BASE_URL=https://planner.example.com",
                "",
            ]
        ),
        encoding="utf-8",
    )

    (bin_dir / "curl").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$TMPDIR/curl.calls"
printf '{"status":"ok"}'
""",
        encoding="utf-8",
    )
    (bin_dir / "curl").chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "ENV_FILE": str(env_file),
            "PATH": f"{bin_dir}:{env['PATH']}",
            "TMPDIR": str(tmp_path),
        }
    )

    completed = run_script(script, env)

    curl_calls = (tmp_path / "curl.calls").read_text(encoding="utf-8")
    assert "https://planner.example.com/healthz" in curl_calls
    assert '{"status":"ok"}' in completed.stdout
    assert "authenticated chat validation skipped" in completed.stdout


def test_ci_build_release_artifacts_allows_skipped_m365_package() -> None:
    workflow_path = ROOT_DIR.parent / ".github" / "workflows" / "ci.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    build_release = workflow["jobs"]["build-release-artifacts"]
    condition = build_release["if"]

    assert "always()" in condition
    assert "needs.package-m365.result == 'skipped'" in condition
    assert "needs.package-m365.result == 'success'" in condition
