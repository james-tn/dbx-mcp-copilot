"""Uvicorn entry point for the MCP server."""

from __future__ import annotations

import uvicorn

from .app import fastapi_app


def main() -> None:
    uvicorn.run(fastapi_app, host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
