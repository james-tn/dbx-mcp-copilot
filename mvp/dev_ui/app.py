"""Small local UI for exercising the planner streaming endpoint."""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Daily Account Planner Dev UI", version="1.0.0")


def _planner_base_url() -> str:
    return os.environ.get("DEV_UI_PLANNER_BASE_URL", "http://localhost:8080").strip().rstrip("/")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    planner_base_url = _planner_base_url()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Daily Account Planner Dev UI</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {{
      color-scheme: light;
      --bg: #f5f0e7;
      --panel: #fffaf2;
      --ink: #1b2533;
      --muted: #5d6b7a;
      --accent: #0f7b6c;
      --accent-2: #f0a202;
      --border: #d9cfc1;
      --user: #dff3ef;
      --assistant: #fff;
    }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #fff9ef 0%, var(--bg) 70%);
      color: var(--ink);
    }}
    .shell {{
      max-width: 980px;
      margin: 0 auto;
      padding: 24px;
    }}
    .hero {{
      display: grid;
      gap: 10px;
      margin-bottom: 20px;
    }}
    .hero h1 {{
      font-size: clamp(2rem, 5vw, 3.5rem);
      margin: 0;
      line-height: 0.95;
      letter-spacing: -0.04em;
    }}
    .hero p {{
      margin: 0;
      color: var(--muted);
      max-width: 62ch;
    }}
    .panel {{
      background: rgba(255, 250, 242, 0.95);
      border: 1px solid var(--border);
      border-radius: 20px;
      box-shadow: 0 20px 60px rgba(27, 37, 51, 0.08);
      padding: 18px;
    }}
    .grid {{
      display: grid;
      gap: 14px;
      grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
      margin-bottom: 14px;
    }}
    label {{
      display: grid;
      gap: 8px;
      font-size: 0.9rem;
      color: var(--muted);
    }}
    input, textarea, button {{
      font: inherit;
    }}
    input, textarea {{
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px 14px;
      background: #fff;
      color: var(--ink);
    }}
    textarea {{
      min-height: 110px;
      resize: vertical;
    }}
    .actions {{
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
    }}
    button {{
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      background: linear-gradient(135deg, var(--accent), #125d73);
      color: #fff;
      cursor: pointer;
      font-weight: 600;
    }}
    .ghost {{
      background: #fff;
      color: var(--ink);
      border: 1px solid var(--border);
    }}
    .status {{
      color: var(--muted);
      font-size: 0.92rem;
    }}
    .chat {{
      display: grid;
      gap: 12px;
      margin-top: 16px;
    }}
    .msg {{
      border-radius: 18px;
      padding: 14px 16px;
      white-space: pre-wrap;
      line-height: 1.45;
      border: 1px solid var(--border);
    }}
    .msg.user {{ background: var(--user); }}
    .msg.assistant {{ background: var(--assistant); }}
    .eventlog {{
      margin-top: 16px;
      background: #18202b;
      color: #e2edf7;
      border-radius: 18px;
      padding: 16px;
      min-height: 180px;
      white-space: pre-wrap;
      overflow: auto;
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="hero">
      <h1>Daily Account Planner<br/>Dev UI</h1>
      <p>Stream planner turns locally, watch MCP tool events, and debug end-user pacing without going through Teams or Copilot on every iteration.</p>
    </div>
    <div class="panel">
      <div class="grid">
        <label>Planner API Base URL
          <input id="baseUrl" value="{planner_base_url}" />
        </label>
        <label>Bearer Token
          <input id="token" placeholder="Paste a planner bearer token" />
        </label>
        <label>Session ID
          <input id="sessionId" placeholder="Leave blank to auto-create" />
        </label>
      </div>
      <label>Message
        <textarea id="message">Where should I focus today?</textarea>
      </label>
      <div class="actions">
        <button id="sendBtn">Send Streamed Turn</button>
        <button id="clearBtn" class="ghost">Clear Output</button>
        <span id="status" class="status">Idle.</span>
      </div>
      <div id="chat" class="chat"></div>
      <div id="eventlog" class="eventlog"></div>
    </div>
  </div>
  <script>
    const chat = document.getElementById('chat');
    const eventLog = document.getElementById('eventlog');
    const statusEl = document.getElementById('status');

    function appendMessage(role, text) {{
      const div = document.createElement('div');
      div.className = `msg ${{role}}`;
      div.textContent = text;
      chat.appendChild(div);
      return div;
    }}

    function logEvent(value) {{
      eventLog.textContent += `${{value}}\\n`;
      eventLog.scrollTop = eventLog.scrollHeight;
    }}

    async function ensureSession(baseUrl, token, sessionId) {{
      if (sessionId) return sessionId;
      const response = await fetch(`${{baseUrl}}/api/chat/sessions`, {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${{token}}`,
        }},
        body: JSON.stringify({{}})
      }});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'Failed to create session');
      document.getElementById('sessionId').value = payload.session_id;
      return payload.session_id;
    }}

    async function sendTurn() {{
      const baseUrl = document.getElementById('baseUrl').value.trim().replace(/\\/$/, '');
      const token = document.getElementById('token').value.trim();
      const message = document.getElementById('message').value.trim();
      let sessionId = document.getElementById('sessionId').value.trim();
      if (!baseUrl || !token || !message) {{
        statusEl.textContent = 'Base URL, token, and message are required.';
        return;
      }}
      statusEl.textContent = 'Creating session...';
      sessionId = await ensureSession(baseUrl, token, sessionId);
      appendMessage('user', message);
      const assistantBubble = appendMessage('assistant', '');
      statusEl.textContent = 'Streaming...';
      logEvent(`--> session=${{sessionId}}`);

      const response = await fetch(`${{baseUrl}}/api/chat/sessions/${{sessionId}}/messages/stream`, {{
        method: 'POST',
        headers: {{
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${{token}}`,
        }},
        body: JSON.stringify({{ text: message }})
      }});
      if (!response.ok || !response.body) {{
        const text = await response.text();
        throw new Error(text || 'Streaming request failed');
      }}

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {{
        const {{ value, done }} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {{ stream: true }});
        const parts = buffer.split('\\n\\n');
        buffer = parts.pop() || '';
        for (const block of parts) {{
          const lines = block.split('\\n');
          let eventName = 'message';
          let data = '';
          for (const line of lines) {{
            if (line.startsWith('event:')) eventName = line.slice(6).trim();
            if (line.startsWith('data:')) data += line.slice(5).trim();
          }}
          if (!data) continue;
          const payload = JSON.parse(data);
          logEvent(`${{eventName}}: ${{JSON.stringify(payload)}}`);
          if (eventName === 'text_delta') {{
            assistantBubble.textContent += payload.delta || '';
          }}
          if (eventName === 'final') {{
            assistantBubble.textContent = payload.reply || assistantBubble.textContent;
            statusEl.textContent = 'Complete.';
          }}
          if (eventName === 'error') {{
            statusEl.textContent = 'Error.';
          }}
        }}
      }}
    }}

    document.getElementById('sendBtn').addEventListener('click', () => {{
      sendTurn().catch((error) => {{
        statusEl.textContent = error.message;
        logEvent(`error: ${{error.message}}`);
      }});
    }});

    document.getElementById('clearBtn').addEventListener('click', () => {{
      chat.innerHTML = '';
      eventLog.textContent = '';
      statusEl.textContent = 'Idle.';
    }});
  </script>
</body>
</html>"""
