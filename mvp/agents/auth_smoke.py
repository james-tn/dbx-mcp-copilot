"""Small Databricks auth smoke probe for the planner runtime."""

from __future__ import annotations

import asyncio
import inspect
import json

try:
    from .databricks_sql import DatabricksSqlAuthError, DatabricksSqlClient, DatabricksSqlError
except ImportError:
    from databricks_sql import DatabricksSqlAuthError, DatabricksSqlClient, DatabricksSqlError

CURRENT_USER_SQL = "SELECT current_user() AS current_user"
AUTH_SMOKE_INSTRUCTIONS = (
    "Return the resolved Databricks user identity and runtime diagnostics for the "
    "current environment by running SELECT current_user() AS current_user."
)


class DatabricksAuthSmokeAgent:
    """Small auth smoke probe for the planner container."""

    def __init__(self, client: DatabricksSqlClient | None = None) -> None:
        self.client = client or DatabricksSqlClient()

    def probe(self) -> dict[str, object]:
        try:
            rows = self.client.query_sql(CURRENT_USER_SQL)
            if inspect.isawaitable(rows):
                rows = asyncio.run(rows)
            current_user = rows[0].get("current_user") if rows else None
            return {
                "ok": True,
                "current_user": current_user,
                "row_count": len(rows),
            }
        except DatabricksSqlAuthError as exc:
            return {
                "ok": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        except DatabricksSqlError as exc:
            return {
                "ok": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        except Exception as exc:  # pragma: no cover - defensive fallback for smoke use
            return {
                "ok": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        finally:
            close = getattr(self.client, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    asyncio.run(result)

    def run(self) -> None:
        print(json.dumps(self.probe(), ensure_ascii=False))
