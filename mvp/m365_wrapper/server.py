"""Container entry point for the thin M365 wrapper service."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "3978")),
        factory=False,
    )


if __name__ == "__main__":
    main()
