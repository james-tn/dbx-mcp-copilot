from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


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
            "CUSTOMER_REP_LOOKUP_STATIC_MAP_JSON_PATH": "fixtures/customer_rep_lookup_static_map.json",
            "WRAPPER_BASE_URL": "https://wrapper.example.com",
        }
    )

    run_script(script, env)
    rendered = output_env_file.read_text(encoding="utf-8")

    assert "PLANNER_API_IMAGE=example.azurecr.io/daily-account-planner/planner:secure-abc123" in rendered
    assert "WRAPPER_IMAGE=example.azurecr.io/daily-account-planner/wrapper:secure-abc123" in rendered
    assert "AZURE_OPENAI_AUTO_ROLE_ASSIGN=false" in rendered
    assert "ACA_ENVIRONMENT_NAME=aca-secure-int" in rendered
    assert "CUSTOMER_DATABRICKS_WAREHOUSE_ID=warehouse-123" in rendered
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
