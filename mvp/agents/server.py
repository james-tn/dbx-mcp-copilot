"""Container entry point for the ACA-hosted Daily Account Planner API."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        factory=False,
    )


if __name__ == "__main__":
    main()
