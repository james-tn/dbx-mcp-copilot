"""
Local planner chat UI for testing the planner service without Microsoft 365.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

try:
    from ..m365_wrapper.debug_auth import (
        DebugAuthConfigurationError,
        DebugAuthOboError,
        DebugAuthValidationError,
        acquire_planner_token_on_behalf_of,
        extract_bearer_token,
        validate_debug_token,
    )
    from ..m365_wrapper.planner_client import PlannerServiceAuthError, PlannerServiceClient, PlannerServiceError
    from ..shared.runtime_env import ensure_runtime_env_loaded
except ImportError:
    _MODULE_PATH = Path(__file__).resolve()
    for _candidate_root in (_MODULE_PATH.parent.parent, _MODULE_PATH.parent):
        if (_candidate_root / "m365_wrapper").exists():
            if str(_candidate_root) not in sys.path:
                sys.path.insert(0, str(_candidate_root))
            break
    from m365_wrapper.debug_auth import (
        DebugAuthConfigurationError,
        DebugAuthOboError,
        DebugAuthValidationError,
        acquire_planner_token_on_behalf_of,
        extract_bearer_token,
        validate_debug_token,
    )
    from m365_wrapper.planner_client import PlannerServiceAuthError, PlannerServiceClient, PlannerServiceError
    from shared.runtime_env import ensure_runtime_env_loaded

ensure_runtime_env_loaded()


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def get_planner_base_url() -> str:
    return (
        os.environ.get("PLANNER_SERVICE_BASE_URL", "").strip()
        or os.environ.get("PLANNER_API_BASE_URL", "").strip()
    ).rstrip("/")


def get_debug_expected_audience() -> str:
    configured = os.environ.get("WRAPPER_DEBUG_EXPECTED_AUDIENCE", "").strip()
    if configured:
        return configured
    bot_sso_resource = os.environ.get("BOT_SSO_RESOURCE", "").strip()
    if bot_sso_resource:
        return bot_sso_resource
    bot_app_id = os.environ.get("BOT_APP_ID", "").strip()
    if bot_app_id:
        return f"api://botid-{bot_app_id}"
    raise RuntimeError("WRAPPER_DEBUG_EXPECTED_AUDIENCE or BOT_SSO_RESOURCE or BOT_APP_ID is required.")


app = FastAPI(title="Local Planner Chat", version="1.0.0")


def _page(*, message: str = "", session_id: str = "", reply: str = "", bearer: str = "", prompt: str = "") -> str:
    escaped_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_reply = reply.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_bearer = bearer.replace("&", "&amp;").replace('"', "&quot;")
    escaped_prompt = prompt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_session_id = session_id.replace("&", "&amp;").replace('"', "&quot;")
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Local Planner Chat</title>
    <style>
      body {{
        font-family: Georgia, 'Times New Roman', serif;
        background: linear-gradient(135deg, #f6f2e8, #dfe8f1);
        color: #1c2430;
        margin: 0;
        padding: 32px;
      }}
      .shell {{
        max-width: 960px;
        margin: 0 auto;
        background: rgba(255,255,255,0.92);
        border: 1px solid rgba(28,36,48,0.1);
        border-radius: 18px;
        box-shadow: 0 18px 50px rgba(28,36,48,0.12);
        overflow: hidden;
      }}
      .hero {{
        padding: 28px 32px 12px;
        background: radial-gradient(circle at top left, rgba(0,179,54,0.18), transparent 45%);
      }}
      h1 {{ margin: 0 0 8px; font-size: 32px; }}
      p {{ margin: 0 0 16px; line-height: 1.5; }}
      form {{ padding: 0 32px 32px; }}
      label {{ display: block; margin: 14px 0 6px; font-weight: 700; }}
      input, textarea {{
        width: 100%;
        box-sizing: border-box;
        border: 1px solid #c7d2df;
        border-radius: 10px;
        padding: 12px 14px;
        font: inherit;
        background: #fff;
      }}
      textarea {{ min-height: 140px; resize: vertical; }}
      button {{
        margin-top: 18px;
        background: #0c6b3f;
        color: #fff;
        border: 0;
        border-radius: 999px;
        padding: 12px 20px;
        font: inherit;
        font-weight: 700;
        cursor: pointer;
      }}
      .note, .reply {{
        margin: 0 32px 24px;
        padding: 16px 18px;
        border-radius: 12px;
      }}
      .note {{ background: #fff4d6; }}
      .reply {{ background: #eef6ff; white-space: pre-wrap; }}
      .meta {{ color: #5a6776; font-size: 14px; }}
    </style>
  </head>
  <body>
    <div class="shell">
      <div class="hero">
        <h1>Local Planner Chat</h1>
        <p>Use a bearer token from the same tenant to test the planner directly without M365.</p>
        <p class="meta">Planner base URL: {get_planner_base_url() or 'not configured'}</p>
      </div>
      {f'<div class="note">{escaped_message}</div>' if escaped_message else ''}
      {f'<div class="reply">{escaped_reply}</div>' if escaped_reply else ''}
      <form method="post" action="/chat">
        <label for="bearer">Caller bearer token</label>
        <textarea id="bearer" name="bearer">{escaped_bearer}</textarea>
        <label for="session_id">Session ID</label>
        <input id="session_id" name="session_id" value="{escaped_session_id}" placeholder="Optional; blank creates a fresh session" />
        <label for="prompt">Prompt</label>
        <textarea id="prompt" name="prompt">{escaped_prompt}</textarea>
        <button type="submit">Send To Planner</button>
      </form>
    </div>
  </body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    return HTMLResponse(_page())


@app.post("/chat", response_class=HTMLResponse)
async def chat(
    request: Request,
    bearer: str = Form(...),
    prompt: str = Form(...),
    session_id: str = Form(""),
) -> HTMLResponse:
    prompt = prompt.strip()
    if not prompt:
        return HTMLResponse(_page(message="Prompt is required.", bearer=bearer, session_id=session_id, prompt=prompt), status_code=400)

    planner_base_url = get_planner_base_url()
    if not planner_base_url:
        return HTMLResponse(
            _page(
                message="PLANNER_API_BASE_URL or PLANNER_SERVICE_BASE_URL must be set in .env for local chat.",
                bearer=bearer,
                session_id=session_id,
                prompt=prompt,
            ),
            status_code=500,
        )

    try:
        user_assertion = extract_bearer_token(f"Bearer {bearer.strip()}")
        claims = validate_debug_token(user_assertion, expected_audience=get_debug_expected_audience())
        planner_token = acquire_planner_token_on_behalf_of(
            user_assertion=user_assertion,
            expected_audience=get_debug_expected_audience(),
        )
        async with httpx.AsyncClient(timeout=float(os.environ.get("WRAPPER_FORWARD_TIMEOUT_SECONDS", "300"))) as http_client:
            client = PlannerServiceClient(
                base_url=planner_base_url,
                timeout_seconds=float(os.environ.get("WRAPPER_FORWARD_TIMEOUT_SECONDS", "300")),
                http_client=http_client,
            )
            effective_session_id = session_id.strip() or f"local-{secrets.token_hex(8)}"
            reply = await client.send_turn(
                session_id=effective_session_id,
                text=prompt,
                access_token=planner_token,
            )
        return HTMLResponse(
            _page(
                message=f"Authenticated as {claims.upn or claims.user_id}. Session: {effective_session_id}",
                session_id=effective_session_id,
                reply=reply,
                bearer=bearer,
                prompt=prompt,
            )
        )
    except (DebugAuthConfigurationError, DebugAuthValidationError, DebugAuthOboError) as exc:
        return HTMLResponse(_page(message=str(exc), bearer=bearer, session_id=session_id, prompt=prompt), status_code=401)
    except (PlannerServiceAuthError, PlannerServiceError, RuntimeError) as exc:
        return HTMLResponse(_page(message=str(exc), bearer=bearer, session_id=session_id, prompt=prompt), status_code=500)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
