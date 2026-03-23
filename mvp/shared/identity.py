"""Shared environment detection helpers."""

from __future__ import annotations

import os

IDENTITY_MARKERS = (
    "IDENTITY_ENDPOINT",
    "MSI_ENDPOINT",
    "AZURE_CLIENT_ID",
    "CONTAINER_APP_NAME",
)


def is_hosted_environment() -> bool:
    return any(os.environ.get(marker) for marker in IDENTITY_MARKERS)
