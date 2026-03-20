"""
Next Move Agent — propensity ranking and personalized outreach.

Finds highest-potential accounts using Databricks-backed propensity data,
explains why, shows contacts, and drafts JOLT-methodology emails.
"""

from pathlib import Path

from agent_framework import Agent
from agent_framework.azure import AzureOpenAIResponsesClient

try:
    from .databricks_tools import get_account_contacts, get_scoped_accounts, get_top_opportunities, lookup_rep
except ImportError:
    from databricks_tools import get_account_contacts, get_scoped_accounts, get_top_opportunities, lookup_rep

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
    """Create the Next Move agent with semantic Databricks tools."""
    return client.as_agent(
        name="NextMove",
        description=(
            "Specialist for focus ranking, contact selection, and seller outreach "
            "using Databricks secure views."
        ),
        instructions=NEXT_MOVE_INSTRUCTIONS,
        tools=[get_scoped_accounts, lookup_rep, get_top_opportunities, get_account_contacts],
    )
