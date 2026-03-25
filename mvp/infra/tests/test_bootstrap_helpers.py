from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from infra.bootstrap_helpers import (  # noqa: E402
    backup_pre_mcpdev_files,
    build_runtime_env,
    compute_input_signature,
    derive_demo_users,
    missing_required_inputs,
    render_seed_sql_template,
)


def write_env(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def test_build_runtime_env_secure_derives_names_and_demo_users(tmp_path: Path) -> None:
    runtime_example = tmp_path / ".env.secure.example"
    runtime_file = tmp_path / ".env.secure"
    input_file = tmp_path / ".env.secure.inputs"

    write_env(
        runtime_example,
        """
        AZURE_OPENAI_DEPLOYMENT=gpt-5.2-chat
        WRAPPER_ENABLE_DEBUG_CHAT=true
        """,
    )
    write_env(
        input_file,
        """
        AZURE_TENANT_ID=tenant-id
        AZURE_SUBSCRIPTION_ID=sub-id
        AZURE_RESOURCE_GROUP=rg-daily-account-planner-secure
        AZURE_LOCATION=eastus2
        INFRA_NAME_PREFIX=dailyacctplannersec
        SELLER_A_UPN=seller-a@example.com
        SELLER_B_UPN=seller-b@example.com
        """,
    )

    runtime = build_runtime_env("secure", runtime_example, input_file, runtime_file)

    assert runtime["DEPLOYMENT_MODE"] == "secure"
    assert runtime["SECURE_DEPLOYMENT"] == "true"
    assert runtime["ACR_NAME"] == "dailyacctplannersecacr"
    assert runtime["KEYVAULT_NAME"] == "dailyacctplannerseckv"
    assert runtime["BOT_RESOURCE_NAME"] == "dailyacctplannersecbot"
    assert runtime["DATABRICKS_SKIP_CATALOG_CREATE"] == "true"
    assert runtime["DATABRICKS_WORKSPACE_USER_UPNS"] == "seller-a@example.com,seller-b@example.com"
    assert runtime["WRAPPER_DEBUG_ALLOWED_UPNS"] == "seller-a@example.com,seller-b@example.com"
    assert runtime["M365_APP_PACKAGE_ID"]


def test_build_runtime_env_open_derives_environment_specific_app_prefix(tmp_path: Path) -> None:
    runtime_example = tmp_path / ".env.example"
    runtime_file = tmp_path / ".env"
    input_file = tmp_path / ".env.inputs"

    write_env(runtime_example, "AZURE_OPENAI_DEPLOYMENT=gpt-5.2-chat")
    write_env(
        input_file,
        """
        AZURE_TENANT_ID=tenant-id
        AZURE_SUBSCRIPTION_ID=sub-id
        AZURE_RESOURCE_GROUP=rg-daily-account-planner
        AZURE_LOCATION=eastus
        INFRA_NAME_PREFIX=veempoc
        SELLER_A_UPN=seller-a@example.com
        SELLER_B_UPN=seller-b@example.com
        """,
    )

    runtime = build_runtime_env("open", runtime_example, input_file, runtime_file)

    assert runtime["APP_NAME_PREFIX"] == "daily-account-planner-veempoc"


def test_backup_pre_mcpdev_files_copies_existing_runtime_files_once(tmp_path: Path) -> None:
    for name in (".env", ".env.secure", ".env.inputs", ".env.secure.inputs"):
        (tmp_path / name).write_text(f"{name}=value\n", encoding="utf-8")

    backups = backup_pre_mcpdev_files(tmp_path)

    assert sorted(target for _, target in backups) == sorted(
        str(tmp_path / f"{name}.pre-mcpdev")
        for name in (".env", ".env.secure", ".env.inputs", ".env.secure.inputs")
    )
    assert (tmp_path / ".env.pre-mcpdev").read_text(encoding="utf-8") == ".env=value\n"

    second_backups = backup_pre_mcpdev_files(tmp_path)
    assert second_backups == []


def test_build_runtime_env_preserves_existing_generated_values_for_same_inputs(tmp_path: Path) -> None:
    runtime_example = tmp_path / ".env.example"
    runtime_file = tmp_path / ".env"
    input_file = tmp_path / ".env.inputs"

    write_env(runtime_example, "AZURE_OPENAI_DEPLOYMENT=gpt-5.2-chat")
    signature = compute_input_signature(
        {
            "AZURE_TENANT_ID": "tenant-id",
            "AZURE_SUBSCRIPTION_ID": "sub-id",
            "AZURE_RESOURCE_GROUP": "rg-daily-account-planner",
            "AZURE_LOCATION": "eastus",
            "INFRA_NAME_PREFIX": "dailyacctplanneropen",
            "SELLER_A_UPN": "seller-a@example.com",
            "SELLER_B_UPN": "seller-b@example.com",
        },
        "open",
    )
    write_env(
        runtime_file,
        """
        PLANNER_API_CLIENT_ID=existing-client-id
        BOT_APP_ID=existing-bot-id
        BOOTSTRAP_INPUT_SIGNATURE="""
        + signature
        + """
        """,
    )
    write_env(
        input_file,
        """
        AZURE_TENANT_ID=tenant-id
        AZURE_SUBSCRIPTION_ID=sub-id
        AZURE_RESOURCE_GROUP=rg-daily-account-planner
        AZURE_LOCATION=eastus
        INFRA_NAME_PREFIX=dailyacctplanneropen
        SELLER_A_UPN=seller-a@example.com
        SELLER_B_UPN=seller-b@example.com
        """,
    )

    runtime = build_runtime_env("open", runtime_example, input_file, runtime_file)

    assert runtime["PLANNER_API_CLIENT_ID"] == "existing-client-id"
    assert runtime["BOT_APP_ID"] == "existing-bot-id"
    assert runtime["DEPLOYMENT_MODE"] == "open"


def test_build_runtime_env_drops_stale_generated_values_when_inputs_change(tmp_path: Path) -> None:
    runtime_example = tmp_path / ".env.example"
    runtime_file = tmp_path / ".env"
    input_file = tmp_path / ".env.inputs"

    write_env(runtime_example, "AZURE_OPENAI_DEPLOYMENT=gpt-5.2-chat")
    stale_signature = compute_input_signature(
        {
            "AZURE_TENANT_ID": "old-tenant",
            "AZURE_SUBSCRIPTION_ID": "old-sub-id",
            "AZURE_RESOURCE_GROUP": "rg-old",
            "AZURE_LOCATION": "eastus",
            "INFRA_NAME_PREFIX": "oldprefix",
            "SELLER_A_UPN": "seller-a@example.com",
            "SELLER_B_UPN": "seller-b@example.com",
        },
        "open",
    )
    write_env(
        runtime_file,
        """
        PLANNER_API_CLIENT_ID=stale-client-id
        BOT_APP_ID=stale-bot-id
        WRAPPER_BASE_URL=https://stale.example.com
        BOOTSTRAP_INPUT_SIGNATURE="""
        + stale_signature
        + """
        """,
    )
    write_env(
        input_file,
        """
        AZURE_TENANT_ID=tenant-id
        AZURE_SUBSCRIPTION_ID=sub-id
        AZURE_RESOURCE_GROUP=rg-daily-account-planner
        AZURE_LOCATION=eastus
        INFRA_NAME_PREFIX=dailyacctplanneropen
        SELLER_A_UPN=seller-a@example.com
        SELLER_B_UPN=seller-b@example.com
        """,
    )

    runtime = build_runtime_env("open", runtime_example, input_file, runtime_file)

    assert "PLANNER_API_CLIENT_ID" not in runtime
    assert "BOT_APP_ID" not in runtime
    assert "WRAPPER_BASE_URL" not in runtime


def test_missing_required_inputs_detects_blank_values() -> None:
    values = OrderedDict(
        {
            "AZURE_TENANT_ID": "",
            "AZURE_SUBSCRIPTION_ID": "sub-id",
            "AZURE_RESOURCE_GROUP": "rg",
            "AZURE_LOCATION": "eastus2",
            "INFRA_NAME_PREFIX": "prefix",
            "SELLER_A_UPN": "seller-a@example.com",
            "SELLER_B_UPN": "",
        }
    )

    assert missing_required_inputs(values, "secure") == ["AZURE_TENANT_ID", "SELLER_B_UPN"]


def test_derive_demo_users_falls_back_to_workspace_user_list() -> None:
    seller_a, seller_b = derive_demo_users(
        {
            "DATABRICKS_WORKSPACE_USER_UPNS": "seller-a@example.com,seller-b@example.com",
        }
    )

    assert seller_a == "seller-a@example.com"
    assert seller_b == "seller-b@example.com"


def test_render_seed_sql_template_replaces_both_demo_users() -> None:
    rendered = render_seed_sql_template(
        "grant select on table foo to `__SELLER_A_UPN__`;\ngrant select on table foo to `__SELLER_B_UPN__`;\n",
        "seller-a@example.com",
        "seller-b@example.com",
    )

    assert "__SELLER_A_UPN__" not in rendered
    assert "__SELLER_B_UPN__" not in rendered
    assert "seller-a@example.com" in rendered
    assert "seller-b@example.com" in rendered
