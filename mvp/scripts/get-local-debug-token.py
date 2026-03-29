#!/usr/bin/env python3
"""
Acquire a delegated wrapper/debug bearer token through device code flow.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import msal
from dotenv import load_dotenv


def load_runtime_env() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def default_scope() -> str:
    configured = os.environ.get("LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE", "").strip()
    if configured:
        return configured
    audience = (
        os.environ.get("WRAPPER_DEBUG_EXPECTED_AUDIENCE", "").strip()
        or os.environ.get("BOT_SSO_RESOURCE", "").strip()
    ).rstrip("/")
    if audience:
        return f"{audience}/access_as_user"
    return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", default=os.environ.get("AZURE_TENANT_ID", "").strip())
    parser.add_argument("--client-id", default=os.environ.get("LOCAL_DEBUG_PUBLIC_CLIENT_ID", "").strip())
    parser.add_argument("--scope", default=default_scope())
    parser.add_argument(
        "--cache-path",
        default=os.environ.get(
            "LOCAL_DEBUG_PUBLIC_CLIENT_CACHE_PATH",
            str(Path.home() / ".cache" / "daily-account-planner" / "local_debug_token_cache.json"),
        ),
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def load_cache(path: Path) -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if path.exists():
        cache.deserialize(path.read_text(encoding="utf-8"))
    return cache


def save_cache(path: Path, cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(cache.serialize(), encoding="utf-8")


def main() -> int:
    load_runtime_env()
    args = parse_args()

    tenant_id = args.tenant_id.strip()
    client_id = args.client_id.strip()
    scope = args.scope.strip()
    if not tenant_id:
        raise RuntimeError("AZURE_TENANT_ID or --tenant-id is required.")
    if not client_id:
        raise RuntimeError("LOCAL_DEBUG_PUBLIC_CLIENT_ID or --client-id is required.")
    if not scope:
        raise RuntimeError(
            "LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE, WRAPPER_DEBUG_EXPECTED_AUDIENCE, BOT_SSO_RESOURCE, or --scope is required."
        )

    cache_path = Path(os.path.expanduser(args.cache_path))
    cache = load_cache(cache_path)
    app = msal.PublicClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        token_cache=cache,
    )

    result = None
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(scopes=[scope], account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=[scope])
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to create device flow: {json.dumps(flow, indent=2)}")
        print(flow["message"], file=os.sys.stderr)
        result = app.acquire_token_by_device_flow(flow)

    save_cache(cache_path, cache)

    access_token = (result or {}).get("access_token")
    if not access_token:
        raise RuntimeError(f"Failed to acquire local debug token: {json.dumps(result, indent=2)}")

    if args.as_json:
        print(
            json.dumps(
                {
                    "access_token": access_token,
                    "scope": scope,
                    "client_id": client_id,
                    "tenant_id": tenant_id,
                    "expires_in": result.get("expires_in"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(access_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
