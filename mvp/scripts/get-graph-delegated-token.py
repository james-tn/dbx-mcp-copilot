#!/usr/bin/env python3
"""
Acquire a delegated Microsoft Graph token through device code flow.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import msal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--scopes", required=True, help="Space-delimited Graph scopes.")
    parser.add_argument(
        "--cache-path",
        default=str(Path.home() / ".cache" / "daily-account-planner" / "graph_token_cache.json"),
    )
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
    args = parse_args()
    cache_path = Path(os.path.expanduser(args.cache_path))
    scopes = [scope for scope in args.scopes.split() if scope]
    cache = load_cache(cache_path)
    app = msal.PublicClientApplication(
        client_id=args.client_id,
        authority=f"https://login.microsoftonline.com/{args.tenant_id}",
        token_cache=cache,
    )

    accounts = app.get_accounts()
    result = None
    if accounts:
        result = app.acquire_token_silent(scopes=scopes, account=accounts[0])

    if not result:
        flow = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flow:
            raise RuntimeError(f"Failed to create device flow: {json.dumps(flow, indent=2)}")
        print(flow["message"], file=os.sys.stderr)
        result = app.acquire_token_by_device_flow(flow)

    save_cache(cache_path, cache)

    access_token = (result or {}).get("access_token")
    if not access_token:
        raise RuntimeError(f"Failed to acquire Microsoft Graph token: {json.dumps(result, indent=2)}")

    print(access_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
