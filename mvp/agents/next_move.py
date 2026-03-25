"""
Next Move Agent — propensity ranking and personalized outreach.

Finds highest-potential accounts using enterprise data tools loaded directly
from MCP, explains why, shows contacts, and drafts JOLT-methodology emails.
"""

from pathlib import Path

from agent_framework import Agent, MCPStreamableHTTPTool
from agent_framework.azure import AzureOpenAIResponsesClient
import httpx

try:
    from .auth_context import PlannerMcpBearerAuth
    from .config import get_mcp_base_url
except ImportError:
    from auth_context import PlannerMcpBearerAuth
    from config import get_mcp_base_url

_PROMPT_FILE = Path(__file__).resolve().parent / "next_move_prompt.txt"


def _load_next_move_instructions() -> str:
    """Load the bundled Next Move system prompt."""
    if _PROMPT_FILE.exists():
        return _PROMPT_FILE.read_text(encoding="utf-8").strip()
    return (
        "You are Next Move, a propensity and outreach agent for Veeam field sellers. "
        "Use the available tools to find top accounts and draft outreach."
    )


NEXT_MOVE_INSTRUCTIONS = _load_next_move_instructions()


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_next_move_agent(client: AzureOpenAIResponsesClient) -> Agent:
    """Create the Next Move agent with tools loaded directly from MCP."""
    enterprise_mcp_tool = MCPStreamableHTTPTool(
        name="enterprise_data",
        url=get_mcp_base_url(),
        description="Enterprise data tools served by the Daily Account Planner MCP server.",
        load_tools=True,
        load_prompts=False,
        allowed_tools=[
            "get_scoped_accounts",
            "lookup_rep",
            "get_top_opportunities",
            "get_account_contacts",
        ],
        request_timeout=60,
        http_client=httpx.AsyncClient(
            timeout=60.0,
            auth=PlannerMcpBearerAuth(),
        ),
    )
    return client.as_agent(
        name="NextMove",
        description=(
            "Specialist for focus ranking, contact selection, and seller outreach "
            "using enterprise account data tools loaded from MCP."
        ),
        instructions=NEXT_MOVE_INSTRUCTIONS,
        tools=[enterprise_mcp_tool],
    )
