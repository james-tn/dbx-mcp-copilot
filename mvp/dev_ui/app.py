"""
Local planner chat UI for testing the planner service without Microsoft 365.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import msal
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

_SESSION_COOKIE_NAME = "dap_local_session"
_CACHE_LOCK = threading.Lock()


@dataclass
class LocalAuthSession:
    caller_bearer: str = ""
    claims: DebugTokenClaims | None = None
    planner_session_id: str = ""
    turns: list[dict[str, str]] = field(default_factory=list)


@dataclass
class PendingSignIn:
    message: str
    task: asyncio.Task[tuple[str, DebugTokenClaims]]


_LOCAL_AUTH_SESSIONS: dict[str, LocalAuthSession] = {}
_PENDING_SIGN_INS: dict[str, PendingSignIn] = {}


def get_local_debug_public_client_id() -> str:
    return os.environ.get("LOCAL_DEBUG_PUBLIC_CLIENT_ID", "").strip()


def get_local_debug_public_client_scope() -> str:
    configured = os.environ.get("LOCAL_DEBUG_PUBLIC_CLIENT_SCOPE", "").strip()
    if configured:
        return configured
    audience = get_debug_expected_audience().rstrip("/")
    return f"{audience}/access_as_user"


def get_local_debug_cache_path() -> Path:
    configured = os.environ.get("LOCAL_DEBUG_PUBLIC_CLIENT_CACHE_PATH", "").strip()
    if configured:
        return Path(os.path.expanduser(configured))
    return Path.home() / ".cache" / "daily-account-planner" / "local_debug_token_cache.json"


def get_local_debug_authority() -> str:
    tenant_id = os.environ.get("AZURE_TENANT_ID", "").strip()
    if not tenant_id:
        raise RuntimeError("AZURE_TENANT_ID is required for local sign-in.")
    return f"https://login.microsoftonline.com/{tenant_id}"


def _load_token_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    cache_path = get_local_debug_cache_path()
    with _CACHE_LOCK:
        if cache_path.exists():
            cache.deserialize(cache_path.read_text(encoding="utf-8"))
    return cache


def _save_token_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    cache_path = get_local_debug_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_LOCK:
        cache_path.write_text(cache.serialize(), encoding="utf-8")


def _build_public_client(
    cache: msal.SerializableTokenCache,
) -> msal.PublicClientApplication:
    client_id = get_local_debug_public_client_id()
    if not client_id:
        raise RuntimeError(
            "LOCAL_DEBUG_PUBLIC_CLIENT_ID is required. Run setup-local-debug-public-client.sh first."
        )
    return msal.PublicClientApplication(
        client_id=client_id,
        authority=get_local_debug_authority(),
        token_cache=cache,
    )


def _try_acquire_cached_caller_token() -> tuple[str, DebugTokenClaims] | None:
    cache = _load_token_cache()
    app_client = _build_public_client(cache)
    accounts = app_client.get_accounts()
    if not accounts:
        return None

    result = app_client.acquire_token_silent(
        scopes=[get_local_debug_public_client_scope()],
        account=accounts[0],
    )
    _save_token_cache(cache)
    access_token = str((result or {}).get("access_token") or "").strip()
    if not access_token:
        return None
    claims = validate_debug_token(
        access_token,
        expected_audience=get_debug_expected_audience(),
    )
    return access_token, claims


def _start_pending_sign_in() -> PendingSignIn:
    cache = _load_token_cache()
    app_client = _build_public_client(cache)
    flow = app_client.initiate_device_flow(scopes=[get_local_debug_public_client_scope()])
    if "user_code" not in flow:
        raise RuntimeError(f"Failed to create local sign-in device flow: {flow!r}")

    async def _await_completion() -> tuple[str, DebugTokenClaims]:
        result = await asyncio.to_thread(app_client.acquire_token_by_device_flow, flow)
        _save_token_cache(cache)
        access_token = str((result or {}).get("access_token") or "").strip()
        if not access_token:
            raise RuntimeError(f"Failed to acquire local debug token: {result!r}")
        claims = validate_debug_token(
            access_token,
            expected_audience=get_debug_expected_audience(),
        )
        return access_token, claims

    message = str(flow.get("message") or "").strip() or "Complete the local sign-in flow."
    return PendingSignIn(message=message, task=asyncio.create_task(_await_completion()))


def _ensure_browser_session(request: Request) -> tuple[str, LocalAuthSession, bool]:
    session_cookie = str(request.cookies.get(_SESSION_COOKIE_NAME, "") or "").strip()
    new_cookie = False
    if not session_cookie:
        session_cookie = f"local-ui-{secrets.token_hex(16)}"
        new_cookie = True
    return session_cookie, _LOCAL_AUTH_SESSIONS.setdefault(session_cookie, LocalAuthSession()), new_cookie


def _attach_browser_session(response: HTMLResponse | RedirectResponse, session_cookie: str, should_set: bool) -> None:
    if not should_set:
        return
    response.set_cookie(
        _SESSION_COOKIE_NAME,
        session_cookie,
        httponly=True,
        samesite="lax",
        secure=False,
    )


def _clear_sign_in(session_cookie: str) -> None:
    pending = _PENDING_SIGN_INS.pop(session_cookie, None)
    if pending is not None and not pending.task.done():
        pending.task.cancel()
    _LOCAL_AUTH_SESSIONS[session_cookie] = LocalAuthSession()


def _sync_pending_sign_in(session_cookie: str, auth_session: LocalAuthSession) -> tuple[str, bool]:
    pending = _PENDING_SIGN_INS.get(session_cookie)
    if pending is None:
        return "", False
    if not pending.task.done():
        return pending.message, True
    _PENDING_SIGN_INS.pop(session_cookie, None)
    try:
        caller_bearer, claims = pending.task.result()
    except Exception as exc:
        return f"Local sign-in failed: {exc}", False
    auth_session.caller_bearer = caller_bearer
    auth_session.claims = claims
    return "", False


def _escape_html(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_turns(turns: list[dict[str, str]]) -> str:
    if not turns:
        return (
            '<div class="empty-state">'
            "<h2>Ready to chat</h2>"
            "<p>Sign in, then ask for a morning briefing, top accounts, or outreach guidance.</p>"
            "</div>"
        )

    bubbles: list[str] = []
    for row in turns:
        role = str(row.get("role") or "").strip().lower()
        text = _escape_html(str(row.get("text") or "").strip())
        if not text:
            continue
        if role == "user":
            label = "You"
            css_class = "bubble user"
        else:
            label = "Daily Account Planner"
            css_class = "bubble assistant"
        bubbles.append(
            f'<article class="{css_class}">'
            f'<div class="bubble-label">{label}</div>'
            f'<div class="bubble-text">{text}</div>'
            "</article>"
        )

    return '<section class="transcript">' + "".join(bubbles) + "</section>"


def _page(
    *,
    message: str = "",
    session_id: str = "",
    reply: str = "",
    prompt: str = "",
    authenticated_as: str = "",
    sign_in_message: str = "",
    sign_in_pending: bool = False,
    turns: list[dict[str, str]] | None = None,
) -> str:
    escaped_message = message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_prompt = prompt.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_session_id = session_id.replace("&", "&amp;").replace('"', "&quot;")
    escaped_local_client_id = get_local_debug_public_client_id().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_local_scope = get_local_debug_public_client_scope().replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    escaped_sign_in_message = _escape_html(sign_in_message)
    escaped_authenticated_as = _escape_html(authenticated_as)
    auth_note = ""
    if authenticated_as:
        auth_note = (
            f'<div class="note success">Signed in as <strong>{escaped_authenticated_as}</strong>.'
            '<form method="post" action="/auth/sign-out" class="inline-form">'
            '<button type="submit" class="secondary">Sign out</button>'
            "</form></div>"
        )
    else:
        auth_note = (
            '<div class="note">'
            '<strong>Sign in required.</strong> Use the local debug public client to authenticate once per browser session.'
            '<form method="post" action="/auth/start" class="inline-form">'
            '<button type="submit">Sign in</button>'
            "</form></div>"
        )
    pending_note = f'<div class="note auth-flow"><pre>{escaped_sign_in_message}</pre></div>' if escaped_sign_in_message else ""
    session_note = (
        f'<div class="session-meta">Conversation ID: <code>{escaped_session_id}</code>'
        '<form method="post" action="/chat/reset" class="inline-form">'
        '<button type="submit" class="secondary subtle">New chat</button>'
        "</form></div>"
        if escaped_session_id
        else ""
    )
    auto_refresh = (
        "<script>window.setTimeout(function(){ window.location.reload(); }, 3000);</script>"
        if sign_in_pending
        else ""
    )
    prompt_disabled = "disabled" if not authenticated_as else ""
    transcript_html = _render_turns(list(turns or []))
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
      .frame {{
        display: grid;
        grid-template-columns: minmax(0, 1fr);
        gap: 16px;
      }}
      h1 {{ margin: 0 0 8px; font-size: 32px; }}
      p {{ margin: 0 0 16px; line-height: 1.5; }}
      form {{ padding: 0; }}
      .inline-form {{ display: inline; padding: 0; margin-left: 12px; }}
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
      textarea {{ min-height: 96px; resize: vertical; }}
      button {{
        background: #0c6b3f;
        color: #fff;
        border: 0;
        border-radius: 999px;
        padding: 12px 20px;
        font: inherit;
        font-weight: 700;
        cursor: pointer;
      }}
      button.secondary {{
        background: #45556b;
      }}
      button.subtle {{
        padding: 8px 14px;
        font-size: 14px;
      }}
      .note, .reply {{
        margin: 0 32px 24px;
        padding: 16px 18px;
        border-radius: 12px;
      }}
      .note {{ background: #fff4d6; }}
      .note.success {{ background: #e7f7ed; }}
      .note.auth-flow pre {{
        white-space: pre-wrap;
        margin: 0;
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      }}
      .meta {{ color: #5a6776; font-size: 14px; }}
      .chat-shell {{
        margin: 0 32px 32px;
        border: 1px solid rgba(28,36,48,0.1);
        border-radius: 18px;
        background: linear-gradient(180deg, rgba(247,250,252,0.95), rgba(255,255,255,0.98));
        overflow: hidden;
      }}
      .chat-header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        padding: 18px 20px;
        border-bottom: 1px solid rgba(28,36,48,0.08);
      }}
      .chat-header h2 {{
        margin: 0;
        font-size: 18px;
      }}
      .session-meta {{
        color: #5a6776;
        font-size: 14px;
      }}
      .session-meta code {{
        padding: 2px 6px;
        border-radius: 999px;
        background: rgba(28,36,48,0.08);
      }}
      .transcript {{
        display: flex;
        flex-direction: column;
        gap: 14px;
        padding: 20px;
        min-height: 320px;
        max-height: 58vh;
        overflow-y: auto;
      }}
      .bubble {{
        max-width: min(78%, 760px);
        border-radius: 18px;
        padding: 14px 16px;
        box-shadow: 0 8px 20px rgba(28,36,48,0.08);
      }}
      .bubble.user {{
        align-self: flex-end;
        background: linear-gradient(135deg, #0c6b3f, #198754);
        color: #fff;
        border-bottom-right-radius: 6px;
      }}
      .bubble.assistant {{
        align-self: flex-start;
        background: #fff;
        color: #1c2430;
        border: 1px solid rgba(28,36,48,0.08);
        border-bottom-left-radius: 6px;
      }}
      .bubble-label {{
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        opacity: 0.75;
        margin-bottom: 8px;
      }}
      .bubble-text {{
        white-space: pre-wrap;
        line-height: 1.55;
      }}
      .composer {{
        border-top: 1px solid rgba(28,36,48,0.08);
        padding: 18px 20px 20px;
        background: rgba(255,255,255,0.9);
      }}
      .composer-row {{
        display: flex;
        align-items: flex-end;
        gap: 12px;
      }}
      .composer textarea {{
        min-height: 88px;
      }}
      .composer-actions {{
        display: flex;
        justify-content: flex-end;
        margin-top: 12px;
      }}
      .empty-state {{
        min-height: 260px;
        display: grid;
        place-items: center;
        text-align: center;
        color: #5a6776;
        padding: 32px;
      }}
      .empty-state h2 {{
        margin: 0 0 8px;
        color: #1c2430;
      }}
      .sr-only {{
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        white-space: nowrap;
        border: 0;
      }}
    </style>
  </head>
  <body>
    <div class="shell">
      <div class="hero">
        <h1>Local Planner Chat</h1>
        <p>Sign in with the local debug public client to test the planner directly without M365.</p>
        <p class="meta">Planner base URL: {get_planner_base_url() or 'not configured'}</p>
        <p class="meta">Local debug client: {escaped_local_client_id or 'not configured'} · scope: {escaped_local_scope}</p>
      </div>
      {auth_note}
      {pending_note}
      {f'<div class="note">{escaped_message}</div>' if escaped_message else ''}
      <div class="chat-shell">
        <div class="chat-header">
          <h2>Planner Conversation</h2>
          {session_note or '<div class="session-meta">New conversation</div>'}
        </div>
        {transcript_html}
        <div class="composer">
          <form method="post" action="/chat">
            <input type="hidden" name="session_id" value="{escaped_session_id}" />
            <label class="sr-only" for="prompt">Prompt</label>
            <textarea id="prompt" name="prompt" placeholder="Ask for a morning briefing, top accounts, or outreach guidance." {prompt_disabled}>{escaped_prompt}</textarea>
            <div class="composer-actions">
              <button type="submit" {prompt_disabled}>Send</button>
            </div>
          </form>
        </div>
      </div>
    </div>
    {auto_refresh}
  </body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    session_cookie, auth_session, should_set_cookie = _ensure_browser_session(request)
    sign_in_message, sign_in_pending = _sync_pending_sign_in(session_cookie, auth_session)
    authenticated_as = ""
    if auth_session.claims is not None:
        authenticated_as = auth_session.claims.upn or auth_session.claims.user_id
    response = HTMLResponse(
        _page(
            authenticated_as=authenticated_as,
            sign_in_message=sign_in_message,
            sign_in_pending=sign_in_pending,
            session_id=auth_session.planner_session_id,
            turns=auth_session.turns,
        )
    )
    _attach_browser_session(response, session_cookie, should_set_cookie)
    return response


@app.post("/auth/start", response_class=RedirectResponse)
async def auth_start(request: Request) -> RedirectResponse:
    session_cookie, auth_session, should_set_cookie = _ensure_browser_session(request)
    _sync_pending_sign_in(session_cookie, auth_session)
    if auth_session.claims is None:
        cached = _try_acquire_cached_caller_token()
        if cached is not None:
            auth_session.caller_bearer, auth_session.claims = cached
        elif session_cookie not in _PENDING_SIGN_INS:
            _PENDING_SIGN_INS[session_cookie] = _start_pending_sign_in()
    response = RedirectResponse(url="/", status_code=303)
    _attach_browser_session(response, session_cookie, should_set_cookie)
    return response


@app.post("/auth/sign-out", response_class=RedirectResponse)
async def auth_sign_out(request: Request) -> RedirectResponse:
    session_cookie, _, should_set_cookie = _ensure_browser_session(request)
    _clear_sign_in(session_cookie)
    response = RedirectResponse(url="/", status_code=303)
    _attach_browser_session(response, session_cookie, should_set_cookie)
    return response


@app.post("/chat/reset", response_class=RedirectResponse)
async def chat_reset(request: Request) -> RedirectResponse:
    session_cookie, auth_session, should_set_cookie = _ensure_browser_session(request)
    auth_session.planner_session_id = ""
    auth_session.turns = []
    response = RedirectResponse(url="/", status_code=303)
    _attach_browser_session(response, session_cookie, should_set_cookie)
    return response


@app.post("/chat", response_class=HTMLResponse)
async def chat(
    request: Request,
    prompt: str = Form(...),
    session_id: str = Form(""),
) -> HTMLResponse:
    session_cookie, auth_session, should_set_cookie = _ensure_browser_session(request)
    sign_in_message, sign_in_pending = _sync_pending_sign_in(session_cookie, auth_session)
    authenticated_as = ""
    if auth_session.claims is not None:
        authenticated_as = auth_session.claims.upn or auth_session.claims.user_id

    prompt = prompt.strip()
    if not prompt:
        response = HTMLResponse(
            _page(
                message="Prompt is required.",
                session_id=session_id or auth_session.planner_session_id,
                prompt=prompt,
                authenticated_as=authenticated_as,
                sign_in_message=sign_in_message,
                sign_in_pending=sign_in_pending,
                turns=auth_session.turns,
            ),
            status_code=400,
        )
        _attach_browser_session(response, session_cookie, should_set_cookie)
        return response

    planner_base_url = get_planner_base_url()
    if not planner_base_url:
        response = HTMLResponse(
            _page(
                message="PLANNER_API_BASE_URL or PLANNER_SERVICE_BASE_URL must be set in .env for local chat.",
                session_id=session_id or auth_session.planner_session_id,
                prompt=prompt,
                authenticated_as=authenticated_as,
                sign_in_message=sign_in_message,
                sign_in_pending=sign_in_pending,
                turns=auth_session.turns,
            ),
            status_code=500,
        )
        _attach_browser_session(response, session_cookie, should_set_cookie)
        return response

    if not auth_session.caller_bearer or auth_session.claims is None:
        response = HTMLResponse(
            _page(
                message="Sign in first before sending chat turns.",
                session_id=session_id or auth_session.planner_session_id,
                prompt=prompt,
                authenticated_as="",
                sign_in_message=sign_in_message,
                sign_in_pending=sign_in_pending,
                turns=auth_session.turns,
            ),
            status_code=401,
        )
        _attach_browser_session(response, session_cookie, should_set_cookie)
        return response

    try:
        user_assertion = extract_bearer_token(f"Bearer {auth_session.caller_bearer}")
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
            effective_session_id = (
                session_id.strip()
                or auth_session.planner_session_id.strip()
                or f"local-{secrets.token_hex(8)}"
            )
            payload = await client.send_turn_payload(
                session_id=effective_session_id,
                text=prompt,
                access_token=planner_token,
            )
            reply = str(payload.get("reply", "")).strip()
            turns = list(payload.get("turns", []) or [])
            auth_session.planner_session_id = effective_session_id
            auth_session.turns = [
                {
                    "role": str(row.get("role") or ""),
                    "text": str(row.get("text") or ""),
                }
                for row in turns
            ]
    except (DebugAuthConfigurationError, DebugAuthValidationError, DebugAuthOboError) as exc:
        response = HTMLResponse(
            _page(
                message=str(exc),
                session_id=session_id or auth_session.planner_session_id,
                prompt=prompt,
                authenticated_as=authenticated_as,
                turns=auth_session.turns,
            ),
            status_code=401,
        )
        _attach_browser_session(response, session_cookie, should_set_cookie)
        return response
    except (PlannerServiceAuthError, PlannerServiceError, RuntimeError) as exc:
        response = HTMLResponse(
            _page(
                message=str(exc),
                session_id=session_id or auth_session.planner_session_id,
                prompt=prompt,
                authenticated_as=authenticated_as,
                turns=auth_session.turns,
            ),
            status_code=500,
        )
        _attach_browser_session(response, session_cookie, should_set_cookie)
        return response

    response = HTMLResponse(
        _page(
            message=f"Authenticated as {claims.upn or claims.user_id}. Session: {effective_session_id}",
            session_id=effective_session_id,
            prompt=prompt,
            authenticated_as=claims.upn or claims.user_id,
            turns=auth_session.turns,
        )
    )
    _attach_browser_session(response, session_cookie, should_set_cookie)
    return response


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
