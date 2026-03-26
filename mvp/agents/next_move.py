"""
Next Move Agent — propensity ranking and personalized outreach.

Finds highest-potential accounts using Databricks-backed propensity data,
explains why, shows contacts, and drafts JOLT-methodology emails.
"""

from pathlib import Path

from agent_framework import Agent
from agent_framework.azure import AzureOpenAIResponsesClient

try:
    from .auth_context import get_request_user_assertion
    from .config import get_customer_backend_enabled
    from .customer_backend import (
        CustomerBackendConfigurationError,
        CustomerDataAccessError,
        SalesTeamResolutionError,
        get_customer_tool_backend_router,
    )
    from .databricks_tools import get_account_contacts, get_top_opportunities
except ImportError:
    from auth_context import get_request_user_assertion
    from config import get_customer_backend_enabled
    from customer_backend import (
        CustomerBackendConfigurationError,
        CustomerDataAccessError,
        SalesTeamResolutionError,
        get_customer_tool_backend_router,
    )
    from databricks_tools import get_account_contacts, get_top_opportunities

_PROMPT_FILE = Path(__file__).resolve().parent / "next_move_prompt.txt"
_SCOPE_GUIDANCE_PLACEHOLDER = "{{DYNAMIC_SCOPE_GUIDANCE}}"


def _load_next_move_instructions() -> str:
    """Load the bundled Next Move system prompt."""
    if _PROMPT_FILE.exists():
        return _PROMPT_FILE.read_text(encoding="utf-8").strip()
    return (
        "You are Next Move, a propensity and outreach agent for Veeam field sellers. "
        "Use the available tools to find top accounts and draft outreach."
    )


NEXT_MOVE_INSTRUCTIONS = _load_next_move_instructions()


def _render_scope_guidance(resolved_territories: list[str]) -> str:
    normalized = [territory.strip() for territory in resolved_territories if territory and territory.strip()]
    if normalized:
        if len(normalized) == 1:
            scope_summary = f"The signed-in seller currently resolves to territory `{normalized[0]}`."
        else:
            scope_summary = (
                "The signed-in seller currently resolves to these territories: "
                + ", ".join(f"`{territory}`" for territory in normalized)
                + "."
            )
        return f"""## Scope Handling

{scope_summary}

- By default, call `get_top_opportunities` with no `territory` argument.
- If the seller explicitly wants to narrow or switch scope, `get_top_opportunities` may accept a `territory` filter with one territory or a comma-separated list of territories.
- If no territory filter is specified, all resolved territories from the signed-in seller scope should be considered.
- Only ask the seller for a territory if they explicitly want to override the detected scope.
"""

    return """## Scope Handling

No territories are currently resolved for the signed-in seller scope.

- Ask the seller to provide a territory before calling `get_top_opportunities`.
- The territory is mandatory in this case.
- The seller may provide one territory or a comma-separated list of territories.
- Once the seller provides it, pass that value through the `territory` argument.
"""


async def build_next_move_instructions_for_request() -> str:
    scope_guidance = _render_scope_guidance([])
    if get_customer_backend_enabled() and get_request_user_assertion():
        try:
            territories = await get_customer_tool_backend_router().sales_team_resolver.resolve()
        except (
            CustomerBackendConfigurationError,
            CustomerDataAccessError,
            SalesTeamResolutionError,
        ):
            territories = []
        scope_guidance = _render_scope_guidance(territories)

    if _SCOPE_GUIDANCE_PLACEHOLDER in NEXT_MOVE_INSTRUCTIONS:
        return NEXT_MOVE_INSTRUCTIONS.replace(_SCOPE_GUIDANCE_PLACEHOLDER, scope_guidance.strip())
    return f"{scope_guidance.strip()}\n\n{NEXT_MOVE_INSTRUCTIONS}"


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_next_move_agent(
    client: AzureOpenAIResponsesClient,
    *,
    instructions: str | None = None,
) -> Agent:
    """Create the Next Move agent with semantic Databricks tools."""
    return client.as_agent(
        name="NextMove",
        description=(
            "Specialist for focus ranking, contact selection, and seller outreach "
            "using Databricks secure views."
        ),
        instructions=instructions or NEXT_MOVE_INSTRUCTIONS,
        tools=[get_top_opportunities, get_account_contacts],
    )
