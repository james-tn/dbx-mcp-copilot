#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import re
import shlex
import sys
import uuid
from collections import OrderedDict
from pathlib import Path

SELLER_A_PLACEHOLDER = "__SELLER_A_UPN__"
SELLER_B_PLACEHOLDER = "__SELLER_B_UPN__"

REQUIRED_INPUTS = {
    "open": (
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP",
        "AZURE_LOCATION",
        "INFRA_NAME_PREFIX",
        "SELLER_A_UPN",
        "SELLER_B_UPN",
    ),
    "secure": (
        "AZURE_TENANT_ID",
        "AZURE_SUBSCRIPTION_ID",
        "AZURE_RESOURCE_GROUP",
        "AZURE_LOCATION",
        "INFRA_NAME_PREFIX",
        "SELLER_A_UPN",
        "SELLER_B_UPN",
    ),
}

MODE_DEFAULTS = {
    "open": {
        "AZURE_RESOURCE_GROUP": "rg-daily-account-planner",
        "AZURE_LOCATION": "eastus",
        "DEPLOYMENT_MODE": "open",
        "SECURE_DEPLOYMENT": "false",
        "INFRA_NAME_PREFIX": "dailyacctplanneropen",
        "WRAPPER_ENABLE_DEBUG_CHAT": "false",
        "APP_NAME_PREFIX": "",
        "M365_APP_SHORT_NAME": "Daily Planner",
        "M365_APP_FULL_NAME": "Daily Account Planner",
    },
    "secure": {
        "AZURE_RESOURCE_GROUP": "rg-daily-account-planner-secure",
        "AZURE_LOCATION": "eastus2",
        "DEPLOYMENT_MODE": "secure",
        "SECURE_DEPLOYMENT": "true",
        "INFRA_NAME_PREFIX": "dailyacctplannersec",
        "DATABRICKS_SKIP_CATALOG_CREATE": "true",
        "WRAPPER_ENABLE_DEBUG_CHAT": "true",
        "APP_NAME_PREFIX": "daily-account-planner-secure",
        "M365_APP_SHORT_NAME": "Daily Secured Planner",
        "M365_APP_FULL_NAME": "Daily Secured Account Planner",
    },
}

SIGNATURE_KEYS = (
    "AZURE_TENANT_ID",
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP",
    "AZURE_LOCATION",
    "INFRA_NAME_PREFIX",
    "SELLER_A_UPN",
    "SELLER_B_UPN",
)

RUNTIME_META_KEYS = {
    "BOOTSTRAP_INPUT_SIGNATURE",
}

NON_PRESERVED_RUNTIME_KEYS = {
    "CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON",
    "CUSTOMER_SCOPE_ACCOUNTS_STATIC_JSON_PATH",
    "CUSTOMER_SCOPE_ACCOUNTS_QUERY",
    "CUSTOMER_SCOPE_ACCOUNTS_SOURCE",
    "CUSTOMER_SCOPE_ACCOUNTS_CATALOG",
    "CUSTOMER_SCOPE_ACCOUNTS_SCHEMA",
    "CUSTOMER_SCOPE_ACCOUNTS_TABLE",
    "CUSTOMER_SALES_TEAM_STATIC_MAP_JSON",
    "CUSTOMER_SALES_TEAM_STATIC_MAP_JSON_PATH",
    "CUSTOMER_SALES_TEAM_MAPPING_QUERY",
    "CUSTOMER_SALES_TEAM_MAPPING_SOURCE",
    "CUSTOMER_SALES_TEAM_MAPPING_CATALOG",
    "CUSTOMER_SALES_TEAM_MAPPING_SCHEMA",
    "CUSTOMER_SALES_TEAM_MAPPING_TABLE",
    "CUSTOMER_SALES_TEAM_MAPPING_USER_COLUMN",
    "CUSTOMER_SALES_TEAM_MAPPING_TEAM_COLUMN",
}


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_env_file(path: str | Path) -> OrderedDict[str, str]:
    env: OrderedDict[str, str] = OrderedDict()
    env_path = Path(path)
    if not env_path.exists():
        return env
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        env[key.strip()] = _strip_quotes(value)
    return env


def _render_env_value(value: str) -> str:
    text = str(value)
    if text == "":
        return ""
    if re.fullmatch(r"[A-Za-z0-9_./:@%+,=#-]+", text):
        return text
    return shlex.quote(text)


def write_env_file(path: str | Path, values: OrderedDict[str, str]) -> None:
    env_path = Path(path)
    rendered_lines = [f"{key}={_render_env_value(value)}" for key, value in values.items()]
    env_path.write_text("\n".join(rendered_lines) + "\n", encoding="utf-8")


def csv_items(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def derive_demo_users(values: dict[str, str]) -> tuple[str, str]:
    seller_a = values.get("SELLER_A_UPN", "").strip()
    seller_b = values.get("SELLER_B_UPN", "").strip()
    fallback_lists = (
        csv_items(values.get("DATABRICKS_WORKSPACE_USER_UPNS", "")),
        csv_items(values.get("WRAPPER_DEBUG_ALLOWED_UPNS", "")),
    )

    for candidates in fallback_lists:
        if not seller_a and len(candidates) >= 1:
            seller_a = candidates[0]
        if not seller_b and len(candidates) >= 2:
            seller_b = candidates[1]
        if seller_a and seller_b:
            break

    return seller_a, seller_b


def _sanitize_compact(value: str, max_length: int) -> str:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return normalized[:max_length]


def _sanitize_name(value: str, suffix: str = "", max_length: int | None = None) -> str:
    normalized = re.sub(r"[^a-z0-9-]", "-", value.lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    candidate = f"{normalized}{suffix}" if suffix else normalized
    if max_length is not None:
        candidate = candidate[:max_length].rstrip("-")
    return candidate


def _deterministic_package_id(mode: str, prefix: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://veeam.invalid/daily-account-planner/{mode}/{prefix}"))


def _signature_seed(values: dict[str, str], mode: str) -> str:
    parts = [f"mode={mode}"]
    for key in SIGNATURE_KEYS:
        parts.append(f"{key}={values.get(key, '').strip()}")
    return "\n".join(parts)


def compute_input_signature(values: dict[str, str], mode: str) -> str:
    return hashlib.sha256(_signature_seed(values, mode).encode("utf-8")).hexdigest()


def _apply_defaults(values: OrderedDict[str, str], mode: str) -> OrderedDict[str, str]:
    rendered = OrderedDict(values)
    defaults = MODE_DEFAULTS[mode]
    for key, value in defaults.items():
        rendered[key] = rendered.get(key, "").strip() or value
    return rendered


def _operator_owned_keys(input_values: OrderedDict[str, str], mode: str) -> set[str]:
    owned = set(input_values)
    owned.update(REQUIRED_INPUTS[mode])
    return owned


def _preserved_runtime_values(
    mode: str,
    runtime_path: str | Path,
    input_values: OrderedDict[str, str],
) -> OrderedDict[str, str]:
    existing_runtime = load_env_file(runtime_path)
    if not existing_runtime:
        return OrderedDict()

    signature_values = _apply_defaults(OrderedDict(input_values), mode)
    current_signature = compute_input_signature(signature_values, mode)
    existing_signature = existing_runtime.get("BOOTSTRAP_INPUT_SIGNATURE", "").strip()
    if not existing_signature:
        legacy_signature_values = OrderedDict(existing_runtime)
        for key, value in input_values.items():
            legacy_signature_values[key] = value
        legacy_signature_values = _apply_defaults(legacy_signature_values, mode)
        existing_signature = compute_input_signature(legacy_signature_values, mode)

    if existing_signature != current_signature:
        return OrderedDict()

    operator_owned = _operator_owned_keys(input_values, mode)
    preserved = OrderedDict()
    for key, value in existing_runtime.items():
        if key in operator_owned or key in RUNTIME_META_KEYS or key in NON_PRESERVED_RUNTIME_KEYS:
            continue
        preserved[key] = value
    return preserved


def build_runtime_env(
    mode: str,
    runtime_example_path: str | Path,
    input_path: str | Path,
    runtime_path: str | Path,
) -> OrderedDict[str, str]:
    if mode not in MODE_DEFAULTS:
        raise ValueError(f"Unsupported mode: {mode}")

    input_values = load_env_file(input_path)
    runtime = load_env_file(runtime_example_path)
    for key, value in _preserved_runtime_values(mode, runtime_path, input_values).items():
        runtime[key] = value
    for key, value in input_values.items():
        runtime[key] = value

    defaults = MODE_DEFAULTS[mode]
    for key, value in defaults.items():
        runtime[key] = runtime.get(key, "").strip() or value

    prefix = runtime["INFRA_NAME_PREFIX"].strip()
    resource_group = runtime["AZURE_RESOURCE_GROUP"].strip()

    seller_a, seller_b = derive_demo_users(runtime)
    runtime["SELLER_A_UPN"] = seller_a
    runtime["SELLER_B_UPN"] = seller_b
    runtime["DATABRICKS_WORKSPACE_USER_UPNS"] = ",".join(item for item in (seller_a, seller_b) if item)
    runtime["WRAPPER_DEBUG_ALLOWED_UPNS"] = ",".join(item for item in (seller_a, seller_b) if item)

    runtime["DEPLOYMENT_MODE"] = mode
    runtime["SECURE_DEPLOYMENT"] = "true" if mode == "secure" else "false"
    app_name_prefix = runtime.get("APP_NAME_PREFIX", "").strip()
    if mode == "open":
        legacy_open_prefix = "daily-account-planner"
        if not app_name_prefix or app_name_prefix == legacy_open_prefix:
            app_name_prefix = f"{legacy_open_prefix}-{prefix}"
    else:
        app_name_prefix = app_name_prefix or defaults["APP_NAME_PREFIX"]
    runtime["APP_NAME_PREFIX"] = app_name_prefix
    runtime["AZURE_OPENAI_ACCOUNT_NAME"] = (
        runtime.get("AZURE_OPENAI_ACCOUNT_NAME", "").strip()
        or _sanitize_compact(f"{prefix}openai", 64)
    )
    runtime["AZURE_AI_FOUNDRY_ACCOUNT_NAME"] = (
        runtime.get("AZURE_AI_FOUNDRY_ACCOUNT_NAME", "").strip()
        or _sanitize_compact(f"{prefix}foundry", 64)
    )
    runtime["ACA_ENVIRONMENT_NAME"] = runtime.get("ACA_ENVIRONMENT_NAME", "").strip() or _sanitize_name(prefix, "-env", 60)
    runtime["PLANNER_ACA_APP_NAME"] = (
        runtime.get("PLANNER_ACA_APP_NAME", "").strip() or _sanitize_name(prefix, "-planner-api", 60)
    )
    runtime["WRAPPER_ACA_APP_NAME"] = (
        runtime.get("WRAPPER_ACA_APP_NAME", "").strip() or _sanitize_name(prefix, "-m365-wrapper", 60)
    )
    runtime["SECURE_VNET_NAME"] = runtime.get("SECURE_VNET_NAME", "").strip() or _sanitize_name(prefix, "-vnet", 60)
    runtime["DATABRICKS_WORKSPACE_NAME"] = (
        runtime.get("DATABRICKS_WORKSPACE_NAME", "").strip() or _sanitize_name(prefix, "-dbx", 60)
    )
    runtime["DATABRICKS_MANAGED_RESOURCE_GROUP"] = (
        runtime.get("DATABRICKS_MANAGED_RESOURCE_GROUP", "").strip() or f"{resource_group}-dbx-managed"
    )
    runtime["KEYVAULT_NAME"] = runtime.get("KEYVAULT_NAME", "").strip() or _sanitize_compact(f"{prefix}kv", 24)
    runtime["ACR_NAME"] = runtime.get("ACR_NAME", "").strip() or _sanitize_compact(f"{prefix}acr", 50)
    runtime["LOG_ANALYTICS_NAME"] = (
        runtime.get("LOG_ANALYTICS_NAME", "").strip() or _sanitize_name(prefix, "-logs", 63)
    )
    runtime["BOT_RESOURCE_NAME"] = (
        runtime.get("BOT_RESOURCE_NAME", "").strip() or _sanitize_compact(f"{prefix}bot", 42)
    )
    runtime["M365_APP_PACKAGE_ID"] = (
        runtime.get("M365_APP_PACKAGE_ID", "").strip() or _deterministic_package_id(mode, prefix)
    )
    runtime["M365_APP_SHORT_NAME"] = runtime.get("M365_APP_SHORT_NAME", "").strip() or defaults["M365_APP_SHORT_NAME"]
    runtime["M365_APP_FULL_NAME"] = runtime.get("M365_APP_FULL_NAME", "").strip() or defaults["M365_APP_FULL_NAME"]
    runtime["WRAPPER_ENABLE_DEBUG_CHAT"] = (
        runtime.get("WRAPPER_ENABLE_DEBUG_CHAT", "").strip() or defaults["WRAPPER_ENABLE_DEBUG_CHAT"]
    )
    runtime["MOCK_DATABRICKS_ENVIRONMENT"] = runtime.get("MOCK_DATABRICKS_ENVIRONMENT", "").strip() or "false"
    runtime["BOOTSTRAP_INPUT_SIGNATURE"] = compute_input_signature(runtime, mode)

    return runtime


def missing_required_inputs(values: dict[str, str], mode: str) -> list[str]:
    missing = []
    for key in REQUIRED_INPUTS[mode]:
        if not values.get(key, "").strip():
            missing.append(key)
    return missing


def render_seed_sql_template(template: str, seller_a_upn: str, seller_b_upn: str) -> str:
    if not seller_a_upn.strip() or not seller_b_upn.strip():
        raise ValueError("SELLER_A_UPN and SELLER_B_UPN are required to render the seed SQL.")
    return template.replace(SELLER_A_PLACEHOLDER, seller_a_upn).replace(SELLER_B_PLACEHOLDER, seller_b_upn)


def _command_render_runtime_env(args: argparse.Namespace) -> int:
    runtime = build_runtime_env(
        mode=args.mode,
        runtime_example_path=args.runtime_example,
        input_path=args.input_file,
        runtime_path=args.runtime_file,
    )
    missing = missing_required_inputs(runtime, args.mode)
    if missing:
        print("\n".join(missing), file=sys.stderr)
        return 1
    write_env_file(args.runtime_file, runtime)
    return 0


def _command_validate_inputs(args: argparse.Namespace) -> int:
    runtime = build_runtime_env(
        mode=args.mode,
        runtime_example_path=args.runtime_example,
        input_path=args.input_file,
        runtime_path=args.runtime_file,
    )
    missing = missing_required_inputs(runtime, args.mode)
    if missing:
        print("\n".join(missing), file=sys.stderr)
        return 1
    return 0


def _command_render_seed_sql(args: argparse.Namespace) -> int:
    rendered = render_seed_sql_template(
        Path(args.template).read_text(encoding="utf-8"),
        args.seller_a_upn,
        args.seller_b_upn,
    )
    Path(args.output).write_text(rendered, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_runtime = subparsers.add_parser("render-runtime-env")
    render_runtime.add_argument("--mode", choices=("open", "secure"), required=True)
    render_runtime.add_argument("--input-file", required=True)
    render_runtime.add_argument("--runtime-example", required=True)
    render_runtime.add_argument("--runtime-file", required=True)
    render_runtime.set_defaults(func=_command_render_runtime_env)

    validate_inputs = subparsers.add_parser("validate-inputs")
    validate_inputs.add_argument("--mode", choices=("open", "secure"), required=True)
    validate_inputs.add_argument("--input-file", required=True)
    validate_inputs.add_argument("--runtime-example", required=True)
    validate_inputs.add_argument("--runtime-file", required=True)
    validate_inputs.set_defaults(func=_command_validate_inputs)

    render_seed = subparsers.add_parser("render-seed-sql")
    render_seed.add_argument("--template", required=True)
    render_seed.add_argument("--output", required=True)
    render_seed.add_argument("--seller-a-upn", required=True)
    render_seed.add_argument("--seller-b-upn", required=True)
    render_seed.set_defaults(func=_command_render_seed_sql)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
