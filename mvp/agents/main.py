"""
CLI entry point for testing agents locally.

Usage:
    python -m agents.main                          # Interactive mode with parent planner
    python -m agents.main --agent pulse             # Direct Account Pulse
    python -m agents.main --agent nextmove          # Direct Next Move
    python -m agents.main --agent planner           # Parent planner (default)
    python -m agents.main --query "Give me my briefing"  # Single query mode
"""

import asyncio
import argparse
import io
import sys

# Fix Windows console encoding for Unicode/emoji output
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

try:
    from .config import get_client, get_chat_client
    from .account_pulse import create_account_pulse_agent
    from .next_move import create_next_move_agent
    from .planner import create_planner_agent, create_runtime_planner_agent
except ImportError:
    from config import get_client, get_chat_client
    from account_pulse import create_account_pulse_agent
    from next_move import create_next_move_agent
    from planner import create_planner_agent, create_runtime_planner_agent


async def run_single(agent, query: str):
    """Run a single query and print the result."""
    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"{'='*60}\n")

    result = await agent.run(query)
    print(result.text)


async def run_interactive(agent, agent_name: str):
    """Run interactive conversation loop."""
    print(f"\n{'='*60}")
    print(f"  {agent_name} — Interactive Mode")
    print(f"  Type 'quit' or 'exit' to stop")
    print(f"{'='*60}\n")

    session = agent.create_session()

    while True:
        try:
            user_input = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye!")
            break

        print("\nAgent: ", end="", flush=True)
        result = await agent.run(user_input, session=session)
        print(result.text)


async def main():
    parser = argparse.ArgumentParser(description="Veeam Revenue Intelligence Agent CLI")
    parser.add_argument(
        "--agent",
        choices=["pulse", "nextmove", "planner", "router"],
        default="planner",
        help="Which agent to run (default: planner)",
    )
    parser.add_argument(
        "--query", "-q",
        type=str,
        default=None,
        help="Single query to run (non-interactive)",
    )
    args = parser.parse_args()

    client = get_client()

    if args.agent == "pulse":
        agent = create_account_pulse_agent(client)
        agent_name = "Account Pulse"
    elif args.agent == "nextmove":
        agent = create_next_move_agent(client)
        agent_name = "Next Move"
    elif args.agent == "router":
        chat_client = get_chat_client()
        agent = create_planner_agent(chat_client, client)
        agent_name = "Daily Account Planner Router"
    else:
        agent = create_runtime_planner_agent(client)
        agent_name = "Daily Account Planner"

    print(f"Loaded agent: {agent_name}")

    if args.query:
        await run_single(agent, args.query)
    else:
        await run_interactive(agent, agent_name)


if __name__ == "__main__":
    asyncio.run(main())
