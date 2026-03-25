# Veeam Revenue Intelligence: M365 Copilot Agent Design

> Historical strategy document only.
> This file is not the implementation source of truth for the current `mcp-dev`
> branch. Use [`mvp/daily-account-planner-architecture.md`](/mnt/c/testing/veeam/revenue_intelligence/mvp/daily-account-planner-architecture.md)
> and [`mvp/mvp-setup-and-deployment-runbook.md`](/mnt/c/testing/veeam/revenue_intelligence/mvp/mvp-setup-and-deployment-runbook.md)
> for the current architecture and operator flow.

## Building Account Pulse & Next Move for Microsoft 365 Copilot

> **Version:** 1.0 | **Date:** 2026-03-17
> **Target Platform:** Microsoft 365 Copilot + Copilot Studio
> **Data Platform:** Azure Databricks (same tenant)
> **Deployment Channel:** Microsoft Teams / M365 Copilot app

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Current State — Claude Code Local PoC](#2-current-state--claude-code-local-poc)
3. [Target State — M365 Copilot Agent Architecture](#3-target-state--m365-copilot-agent-architecture)
4. [Platform Capabilities Reference — Three Agent Patterns](#4-platform-capabilities-reference--three-agent-patterns)
5. [M365 Agents SDK Deep Dive (Pro-Code Path)](#5-m365-agents-sdk-deep-dive-pro-code-path)
6. [Azure Databricks Connectivity Design](#6-azure-databricks-connectivity-design)
7. [Agent 1: Account Pulse — Design](#7-agent-1-account-pulse--design)
8. [Agent 2: Next Move — Design](#8-agent-2-next-move--design)
9. [Orchestrator: Daily Account Planner — Design](#9-orchestrator-daily-account-planner--design)
10. [Gap Analysis](#10-gap-analysis)
11. [Implementation Roadmap](#11-implementation-roadmap)
12. [Security & Governance](#12-security--governance)
13. [Appendix: Feature Mapping Matrix](#appendix-feature-mapping-matrix)

---

## 1. Executive Summary

This document designs the migration of two agentic sales-intelligence applications — **Account Pulse** and **Next Move** — from a locally-run Claude Code + MCP architecture to **Microsoft 365 Copilot-facing agents** deployed inside the Veeam M365 tenant.

**Key design goals:**
- All account data sourced from **Azure Databricks** (no local Excel files)
- Web intelligence via **Bing Search grounding** (replaces Claude `web_search` tool)
- SEC EDGAR data via **REST API tool** or **Azure Function** (replaces direct Python HTTP calls)
- Seller-facing experience delivered in **Microsoft Teams** and the **M365 Copilot app**
- Multi-agent orchestration via Copilot Studio **parent/child agent** pattern
- Enterprise security via **Microsoft Entra ID** and same-tenant Azure connectivity

## 1.1 Implemented MVP Snapshot

The repository has now converged on a **pro-code custom engine** implementation
rather than a Copilot Studio-authored runtime:

- a thin **M365 wrapper** runs in Azure Container Apps and exposes
  `POST /api/messages`
- a stateful **planner service** runs separately in Azure Container Apps and owns
  orchestration, session state, and Databricks access
- specialist behaviors such as **Account Pulse** and **Next Move** are implemented
  inside the planner codebase, not as Copilot Studio child agents
- the signed-in seller reaches Databricks through a chained delegated OBO flow:
  M365 -> wrapper/bot auth -> planner API -> Databricks
- secure mode uses a private planner path and a private ACA seed job for
  Databricks bootstrap data

So this document is still useful for strategic platform choice, but the current
MVP implementation in this repo is already aligned with the **Path B / pro-code
custom engine** direction described later in the document.

---

## 2. Current State — Claude Code Local PoC

### 2.1 Account Pulse (v1 PoC)

| Dimension | Current Design |
|-----------|---------------|
| **Purpose** | Daily intelligence briefing — scans news, SEC filings, and cybersecurity events for a seller's accounts |
| **Data Source** | Local Excel file (`accounts.xlsx`) — real seller territory export |
| **Web Search** | Claude `web_search` tool — general news + cybersecurity queries per Global Ultimate |
| **SEC EDGAR** | Direct Python HTTP calls to SEC EDGAR API (no API key) |
| **Output** | Prioritized briefing: 4-tier signal classification + Veeam Lens insights |
| **Execution** | MCP server (Claude Desktop) or Headless runner (scheduled daily 6 AM via Task Scheduler) |
| **LLM** | Claude Sonnet 4.6 |

**v2 roadmap (already specified):** Replace Excel with live Databricks query, add D&B enrichment, customer/prospect context, communication recency — all from Databricks.

### 2.2 Next Move (v1 PoC)

| Dimension | Current Design |
|-----------|---------------|
| **Purpose** | Propensity ranking + personalized JOLT outreach — finds best accounts, explains why, drafts emails |
| **Data Source** | Live Databricks (via MCP tools: `lookup_rep`, `get_top_opportunities`, `get_account_contacts`) |
| **Propensity Model** | AIQ scores table (`account_iq_scores`) — Xf score, sub-scores, 30+ boolean play flags, "why" fields |
| **Contacts** | AIQ contacts table (`aiq_contact`) — engagement level, contact stage, do-not-call filtering |
| **Output** | 5 formats: Quick List, Full Briefing, Single Account, Draft Email, Follow-Up Email |
| **Email Methodology** | JOLT framework with role-based framing, multi-threading nudge, alternative subjects |
| **Execution** | MCP server (Claude Desktop) — prompt-only server; data tools from separate `aiq-agent` MCP server |
| **LLM** | Claude Sonnet 4.6 |

---

## 3. Target State — M365 Copilot Agent Architecture

**Current implementation note:** the deployed MVP uses the same logical agent
roles shown below, but the runtime boundary is:

- M365 / Teams
- thin wrapper service
- planner service with in-code specialist agents

That means the "parent/child" relationship is implemented in application code
and prompts, not in Copilot Studio runtime artifacts.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Microsoft 365 Copilot App / Teams            │
│              (Seller interacts via natural language)             │
├─────────────────────────────────────────────────────────────────┤
│              PARENT AGENT: Daily Account Planner                │
│     ┌──────────────┐    ┌──────────────┐                        │
│     │ Account Pulse │    │  Next Move   │     (+ future agents) │
│     │ (Child Agent) │    │ (Child Agent)│                        │
│     └──────┬───────┘    └──────┬───────┘                        │
│            │                   │                                 │
├────────────┼───────────────────┼─────────────────────────────────┤
│        TOOLS LAYER             │                                 │
│  ┌─────────────────┐  ┌───────┴──────────┐  ┌────────────────┐ │
│  │  Databricks SQL  │  │  Bing Web Search │  │  SEC EDGAR API │ │
│  │  (REST API Tool  │  │  (Knowledge -    │  │  (REST API Tool│ │
│  │   or Azure Func) │  │   Web Grounding) │  │   or Az Func)  │ │
│  └────────┬────────┘  └──────────────────┘  └───────┬────────┘ │
│           │                                          │          │
├───────────┼──────────────────────────────────────────┼──────────┤
│  ┌────────┴────────┐                        ┌────────┴────────┐ │
│  │ Azure Databricks│                        │    SEC EDGAR    │ │
│  │  SQL Warehouse  │                        │   (Public API)  │ │
│  │  (Same Tenant)  │                        │                 │ │
│  └─────────────────┘                        └─────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Platform Capabilities Reference — Three Agent Patterns

Microsoft 365 offers **three fundamentally different patterns** for building agents. This is the most important architectural decision for this project.

### 4.1 The Three Patterns

```
┌─────────────────────────────────────────────────────────────────────┐
│                  PATTERN 1: DECLARATIVE AGENT                       │
│                                                                     │
│  "Configure, don't code"                                            │
│                                                                     │
│  ┌──────────────────────┐                                           │
│  │ M365 Copilot         │  You provide:                             │
│  │ Orchestrator (fixed) │  - Instructions (JSON manifest, 8K chars) │
│  │ + Foundation Model   │  - Knowledge (SharePoint, web URLs)       │
│  │ (Microsoft's choice) │  - Actions (connectors, REST API, MCP)    │
│  └──────────────────────┘                                           │
│  Built via: Copilot Studio UI, Agent Builder, Agents Toolkit        │
│  Channels: M365 Copilot app, Teams                                  │
│  Orchestrator: Microsoft's (you don't control it)                   │
│  LLM: Microsoft's (you don't choose it)                             │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│          PATTERN 2: COPILOT STUDIO CUSTOM ENGINE AGENT              │
│                                                                     │
│  "Low-code custom orchestration"                                    │
│                                                                     │
│  ┌──────────────────────┐                                           │
│  │ Copilot Studio       │  You configure:                           │
│  │ Orchestrator         │  - Topics (conversation flows with nodes) │
│  │ (configurable)       │  - Tools (connectors, REST, MCP, flows)   │
│  │ + Copilot Studio LLM │  - Knowledge (SharePoint, Dataverse, web) │
│  └──────────────────────┘  - Parent/child agent orchestration       │
│  Built via: Copilot Studio web UI                                   │
│  Channels: Teams, web, mobile, Slack, any Azure Bot channel         │
│  Orchestrator: Copilot Studio's generative orchestration            │
│  LLM: Copilot Studio's (you don't choose it)                       │
│  Extras: Autonomous triggers, scheduled runs, topic loops           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│              PATTERN 3: CUSTOM ENGINE AGENT (PRO-CODE)              │
│                                                                     │
│  "Full code control — bring your own everything"                    │
│                                                                     │
│  ┌──────────────────────┐                                           │
│  │ YOUR Code            │  You build:                               │
│  │ YOUR Orchestrator    │  - Agent logic (Python, C#, JS)           │
│  │ (Semantic Kernel,    │  - Orchestration (LangGraph, SK, custom)  │
│  │  LangGraph, custom)  │  - Tool integrations (any)               │
│  │ YOUR LLM choice      │  - Multi-agent coordination              │
│  └──────────────────────┘                                           │
│  Built via: VS Code + M365 Agents Toolkit, or Microsoft Foundry    │
│  Channels: M365 Copilot, Teams, partner apps, websites, mobile     │
│  Orchestrator: YOURS (Semantic Kernel, LangGraph, LangChain, etc.) │
│  LLM: YOURS (Azure OpenAI, Anthropic, Llama, any model)            │
│  Hosting: Azure (App Service, Container Apps, Foundry Agent Service)│
└─────────────────────────────────────────────────────────────────────┘
```

### 4.2 Detailed Comparison

| Dimension | Declarative Agent | Copilot Studio Custom | Pro-Code Custom Engine |
|-----------|------------------|----------------------|----------------------|
| **Development** | No-code / low-code | Low-code (visual) | Pro-code (Python/C#/JS) |
| **Tooling** | Agent Builder / Copilot Studio / Agents Toolkit | Copilot Studio web UI | VS Code + M365 Agents Toolkit, or Foundry portal |
| **Orchestrator** | Microsoft's M365 Copilot orchestrator (fixed) | Copilot Studio generative orchestration (configurable) | **Bring your own** (Semantic Kernel, LangGraph, LangChain, custom) |
| **LLM / Model** | Microsoft's foundation model (no choice) | Copilot Studio's model (no choice) | **Any model** (Azure OpenAI GPT-4o, Anthropic Claude, Llama, DeepSeek, etc.) |
| **Instructions** | 8,000 char JSON manifest | Unlimited (topic-based authoring) | Unlimited (code) |
| **Knowledge** | SharePoint, web URLs, Copilot connectors | SharePoint, Dataverse, web, Bing, custom | **Any** (you code the retrieval) |
| **Tools / Actions** | Connectors, REST API, MCP, Agent Flows, Code Interpreter | Connectors, REST API, MCP, Agent Flows, Topics | **Any** (you code the tool calls) |
| **Multi-agent** | Not native | Parent/child orchestration | **Full control** — LangGraph multi-agent, Semantic Kernel agents, custom |
| **Channels** | M365 Copilot app, Teams | Teams, web, mobile, Slack, Azure Bot channels | M365 Copilot, Teams, partner apps, web, mobile |
| **Autonomous triggers** | Not native | Schedule, event, message triggers | **Full control** — timer triggers, event-driven, etc. |
| **Long-running tasks** | Constrained by M365 Copilot turn timeouts | Constrained by Copilot Studio turn timeouts (~100-200s) | **Full control** — async patterns, background jobs, streaming |
| **Hosting** | Microsoft-managed (no infra to manage) | Microsoft-managed (Copilot Studio SaaS) | **You manage** — Azure App Service, Container Apps, or Foundry Agent Service |
| **Complexity** | Lowest | Medium | Highest |
| **Publishing** | My organization | My organization | My organization, ISV/store, 10+ channels |

### 4.3 Three Pro-Code SDK Paths

If you go pro-code (Pattern 3), there are three sub-options:

| SDK / Path | What It Is | Orchestrator Support | Hosting |
|-----------|-----------|---------------------|---------|
| **M365 Agents SDK** | Full-stack, multi-channel agent framework | Semantic Kernel, LangChain, any custom | Azure App Service, Container Apps |
| **Teams AI Library (Teams SDK)** | Teams-specific agent framework with built-in Action Planner | Built-in Azure OpenAI action planner | Azure (Teams-optimized) |
| **Microsoft Foundry Agent Service** | Managed platform for deploying agents as containers | Agent Framework, LangGraph, custom code | **Fully managed** by Foundry (container-based) |

Foundry Agent Service supports three agent types:
- **Prompt agents**: No-code, defined via instructions + tools in Foundry portal
- **Workflow agents** (preview): Visual multi-agent orchestration with branching, human-in-the-loop
- **Hosted agents** (preview): YOUR code deployed as containers — Foundry manages scaling, identity, observability

All three can publish directly to Teams and M365 Copilot.

### 4.4 Recommendation for This Project

Given the requirements of Account Pulse and Next Move:

| Requirement | Declarative | Copilot Studio | Pro-Code Custom Engine |
|-------------|:-----------:|:--------------:|:---------------------:|
| Complex instructions (700+ lines) | 8K limit | Topic-based (works) | Unlimited (code) |
| Multi-agent orchestration | No | Parent/child | Full control |
| Custom tool iteration (scan N accounts) | Unreliable | Topic loops (fragile) | **Native** (Python for-loop) |
| Long-running async processing | No control | ~100-200s timeout | **Full control** |
| Parallel I/O (50+ concurrent API calls) | No | No | **Native** (`asyncio.gather`) |
| Web search with time filtering | Bing grounding (no time filter) | Bing grounding (no time filter) | **Direct Bing API** |
| Scheduled headless execution | No | Autonomous triggers | Timer triggers |
| Choose LLM model | No | No | **Yes** |
| Future flexibility (new agents, models) | Low | Medium | **High** |

**Revised recommendation — Three viable paths:**

#### Path A: Copilot Studio Custom Agent (Low-code, original recommendation)

- Agent intelligence stays in the Copilot Studio platform
- Heavy processing pushed to Azure Functions ("fat functions")
- Works if you accept that the Azure Functions do most of the real work
- Agent becomes mostly a "formatting and chat" layer
- **Pro:** Lowest dev effort, no infra to manage, IT-friendly
- **Con:** The "smarts" are split between agent instructions and Azure Functions — harder to maintain; long-running gap persists

#### Path B: Pro-Code Custom Engine via Foundry Agent Service (Full control)

- Agent logic written in **Python** (or C#) with full orchestration control
- Use **LangGraph** or **Semantic Kernel** for multi-agent orchestration
- Deploy as **hosted agents** on Foundry Agent Service (managed hosting)
- Publish to **Teams and M365 Copilot** directly from Foundry
- **Pro:** Full control over iteration, async, parallelism, model choice — solves ALL identified gaps natively
- **Con:** Requires Python/C# development; Foundry hosted agents still in preview

#### Path C: Hybrid (Pragmatic recommendation)

- **Next Move** → Copilot Studio Custom Agent (simpler workflow, 5 accounts, acceptable latency)
- **Account Pulse** → Pro-Code Custom Engine via Foundry (long-running, parallel I/O, complex iteration)
- **Daily Account Planner (parent)** → Copilot Studio routes to both
- Copilot Studio can invoke a Foundry-hosted agent as a tool/action

This lets you start with Copilot Studio for the simpler agent and use pro-code only where the platform constraints genuinely block you.

### 4.4.1 Current repo decision

The current Daily Account Planner MVP has effectively chosen the **pro-code
custom engine** route:

- wrapper and planner both run as custom Azure services
- the top-level planner and specialist agents are code-defined
- Databricks access is controlled by the planner service boundary
- M365 packaging and publishing are handled after Azure deployment, not by
  rebuilding the agent in Copilot Studio

That choice was driven by the exact constraints called out in the comparison
table above:

- long-running turns
- parallel I/O for Account Pulse
- explicit control of OBO token flow
- deterministic planner-owned business tools
- secure/private Azure deployment options

### 4.5 Tool Types Available in Copilot Studio

(Applicable to Paths A and C for the Copilot Studio agents)

| Tool Type | How It Works | Use For |
|-----------|-------------|---------|
| **Connector (prebuilt)** | 1000+ Power Platform connectors | Standard SaaS integrations |
| **Custom Connector** | OpenAPI spec + auth config | Proprietary APIs |
| **REST API** | Upload OpenAPI v2 spec, configure auth | Direct API integration (e.g., Databricks SQL API, SEC EDGAR) |
| **Agent Flow** | Power Automate cloud flow as tool | Multi-step data transformations, long-running work |
| **MCP Server** | Connect to MCP server endpoint | Existing MCP-based integrations |
| **Prompt** | Single-turn LLM prompt with knowledge | Classification, translation, generation |
| **Code Interpreter** | Python execution sandbox | Data analysis, chart generation |

### 4.6 Web Search Capabilities

- **Bing Search grounding** (Copilot Studio): Agent can search public web — but no time-range control
- **Bing Custom Search** (Copilot Studio): Scoped web search to specific domains/sites
- **Bing Search API v7** (Pro-code / Azure Function): Full control including `freshness` parameter for time filtering
- **Foundry built-in web search tool**: Available for Foundry-hosted agents (preview)

### 4.7 Multi-Agent Orchestration Options

**Copilot Studio:**
- Parent agent routes user intent to child agents
- Each child has its own tools, knowledge, instructions
- Tool limit: 128 per agent (recommended: 25-30)

**Pro-Code (LangGraph / Semantic Kernel):**
- Full programmatic control over agent routing
- Conditional branching, parallel execution, state machines
- No tool limits
- Can implement supervisor, hierarchical, or swarm patterns

**Foundry Workflow Agents:**
- Visual multi-agent orchestration in Foundry portal
- Sequential, branching, and group-chat patterns
- Human-in-the-loop steps supported

---

## 5. M365 Agents SDK Deep Dive (Pro-Code Path)

This section elaborates on Pattern 3 — building custom engine agents with code and deploying them to M365 channels.

### 5.1 What Is the M365 Agents SDK?

The M365 Agents SDK is a **framework for building full-stack agents** that surface in Microsoft 365 Copilot, Teams, and other channels. It is:

- **Model-agnostic** — bring Azure OpenAI, Anthropic, Llama, or any LLM
- **Orchestrator-agnostic** — use Semantic Kernel, LangGraph, LangChain, OpenAI Agents, or custom
- **Language support** — Python, C#, JavaScript
- **The successor to Bot Framework SDK** — same activity protocol, new APIs

### 5.1.1 "Bot" vs "Agent" — Clarifying the Terminology

A common question: **Is an M365 agent just an M365 bot?** The answer is **yes, at the infrastructure layer — but no, at the intelligence layer.**

```
┌─────────────────────────────────────────────────────────┐
│              THE EVOLUTION                                │
│                                                          │
│  Bot Framework SDK v4         M365 Agents SDK            │
│  ─────────────────────  ──→   ─────────────────────      │
│  ActivityHandler              AgentApplication            │
│  "Bot"                        "Agent"                     │
│  Rule-based / intent          LLM-powered / autonomous    │
│  waterfall dialogs            tool-use + reasoning loops  │
│                                                          │
│  SAME: Azure Bot Service, Activity Protocol,             │
│        POST /api/messages, Teams manifest bots[] array   │
└─────────────────────────────────────────────────────────┘
```

| Layer | Bot or Agent? | Explanation |
|-------|:------------:|-------------|
| **Transport** | Bot | Azure Bot Service routes messages via the Activity Protocol. The Teams manifest still uses `"bots": [...]`. The endpoint is still `POST /api/messages`. This is identical for bots and agents. |
| **SDK** | Renamed | M365 Agents SDK is the direct successor to Bot Framework SDK v4. `AgentApplication` replaced `ActivityHandler`. Same turn-based message processing model. |
| **Intelligence** | Agent | The "agent" distinction is the AI layer on top — LLM reasoning, tool calling, multi-step autonomy, memory, planning. A bot follows scripted flows; an agent reasons and acts. |
| **Channel Registration** | Bot | You still create an Azure Bot Resource in Azure Portal, register an Entra ID app, and configure messaging endpoint. Identical plumbing. |
| **Branding** | Agent | Microsoft rebranded to signal the industry shift from simple chatbots to autonomous AI agents. |

**Bottom line:** When you build a custom engine agent with the M365 Agents SDK, you are building on top of the same bot infrastructure (Azure Bot Service, Activity Protocol, `POST /api/messages`). The term "agent" reflects the added intelligence layer — LLM orchestration, tool use, reasoning loops, and autonomy — that distinguishes these from traditional rule-based bots. The plumbing is the same; the brains are new.

### 5.2 How It Works — Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                     YOUR AGENT CODE                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  AgentApplication                                          │  │
│  │  ├── onActivity("message") → YOUR handler                 │  │
│  │  ├── onConversationUpdate("membersAdded") → YOUR handler  │  │
│  │  └── ... any activity type                                │  │
│  └────────────────────────────────────────────────────────────┘  │
│                          │                                       │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  YOUR Orchestration Layer                                  │  │
│  │  (Semantic Kernel / LangGraph / LangChain / custom)        │  │
│  │  ├── Tool: query Databricks (databricks-sdk)              │  │
│  │  ├── Tool: Bing Search API (requests)                     │  │
│  │  ├── Tool: SEC EDGAR API (requests)                       │  │
│  │  └── Tool: anything you code                              │  │
│  └────────────────────────────────────────────────────────────┘  │
│                          │                                       │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  YOUR LLM Client                                           │  │
│  │  (Azure OpenAI / Anthropic / any)                          │  │
│  └────────────────────────────────────────────────────────────┘  │
│                          │                                       │
│  Exposes: POST /api/messages  (HTTP endpoint)                    │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│              AZURE BOT SERVICE (Azure Bot Resource)               │
│  ├── Routes messages between channels and your /api/messages     │
│  ├── Handles authentication (Entra ID)                           │
│  ├── Channels: Teams, M365 Copilot, Web Chat, Slack, etc.       │
│  └── Messaging endpoint: https://yourapp.azurewebsites.net/     │
│                           api/messages                            │
└──────────────────────┬───────────────────────────────────────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
    ┌──────────┐ ┌──────────┐ ┌──────────┐
    │  Teams   │ │   M365   │ │ Web Chat │
    │          │ │  Copilot  │ │          │
    └──────────┘ └──────────┘ └──────────┘
```

**Key insight:** Your agent is a **web application** that exposes `POST /api/messages`. Azure Bot Service routes messages from Teams/Copilot to that endpoint. Your code handles the message, calls LLMs, tools, etc., and sends responses back.

### 5.3 What You Build (Python Example)

```python
# app.py — simplified Account Pulse agent skeleton
from microsoft_agents.hosting.core import AgentApplication, TurnState, TurnContext, MemoryStorage
from microsoft_agents.hosting.aiohttp import CloudAdapter, start_agent_process
from aiohttp.web import Application, Request, Response, run_app

# Your orchestration (Semantic Kernel, LangGraph, or plain Python)
from account_pulse_agent import AccountPulseOrchestrator

orchestrator = AccountPulseOrchestrator()  # your code

AGENT_APP = AgentApplication[TurnState](
    storage=MemoryStorage(), adapter=CloudAdapter()
)

@AGENT_APP.activity("message")
async def on_message(context: TurnContext, state: TurnState):
    user_message = context.activity.text

    # Send immediate acknowledgment
    await context.send_activity("Scanning your accounts...")

    # YOUR orchestration logic — full Python control
    # - Query Databricks for accounts
    # - Parallel Bing + EDGAR searches via asyncio.gather
    # - LLM classification and Veeam Lens
    # - Format output
    briefing = await orchestrator.run(user_message, territory="GreatLakes-ENT-Named-1")

    await context.send_activity(briefing)
```

Inside `AccountPulseOrchestrator`, you have **full Python** — `asyncio.gather`, `databricks-sdk`, `httpx`, Semantic Kernel, LangGraph, any LLM client. No platform constraints.

### 5.4 What You Need to Deploy to M365

The deployment pipeline has **4 layers**:

```
┌───────────────────────────────────────────────────────────┐
│  LAYER 1: YOUR CODE (web app)                             │
│  - Python/C#/JS agent using M365 Agents SDK               │
│  - Exposes POST /api/messages                              │
│  - Deploy to Azure App Service, Container Apps, or any     │
│    hosting that gives you an HTTPS endpoint                │
└───────────────────────────┬───────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────┐
│  LAYER 2: AZURE BOT RESOURCE                              │
│  - Created in Azure portal or via CLI                      │
│  - Links your HTTPS endpoint to the Bot Service            │
│  - Configured with Entra ID app registration               │
│  - Messaging endpoint: https://yourapp/api/messages        │
│  - Enable "Microsoft Teams" channel                        │
└───────────────────────────┬───────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────┐
│  LAYER 3: ENTRA ID APP REGISTRATION                       │
│  - App ID + secret (or Managed Identity)                   │
│  - Used for bot-to-channel authentication                  │
│  - Scoped permissions for your agent                       │
└───────────────────────────┬───────────────────────────────┘
                            │
┌───────────────────────────▼───────────────────────────────┐
│  LAYER 4: TEAMS / M365 COPILOT APP MANIFEST               │
│  - manifest.json + color.png + outline.png → manifest.zip │
│  - References the Entra ID App ID                          │
│  - Defines agent name, description, scopes                 │
│  - Upload via Microsoft Admin Portal (MAC) or Agents Toolkit│
│  - "Upload Custom App" → agent appears in Teams + Copilot  │
└───────────────────────────────────────────────────────────┘

**Current operator shape in this repo:** the deployment path has been
operatorized into two main scripts:

1. `mvp/infra/scripts/bootstrap-azure-demo.sh open|secure`
2. `mvp/infra/scripts/bootstrap-m365-demo.sh open|secure`

The operator fills a small input env with tenant, subscription, naming prefix,
and two seller UPNs. The bootstrap scripts generate the runtime env, create or
reuse Azure resources, build/publish images, configure Entra apps and consent,
and then publish/install the Teams package.
```

### 5.5 The App Manifest (manifest.json)

This is the **package that Teams/Copilot needs** to recognize your agent. Structure:

```json
{
  "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.21/MicrosoftTeams.schema.json",
  "manifestVersion": "1.21",
  "version": "1.0.0",
  "id": "<<AAD_APP_CLIENT_ID>>",
  "developer": {
    "name": "Veeam Revenue Intelligence",
    "websiteUrl": "https://veeam.com",
    "privacyUrl": "https://veeam.com/privacy",
    "termsOfUseUrl": "https://veeam.com/terms"
  },
  "name": { "short": "Daily Account Planner", "full": "Veeam Daily Account Planner" },
  "description": {
    "short": "AI sales companion for Veeam sellers",
    "full": "Orchestrates Account Pulse (intelligence briefing) and Next Move (propensity outreach)"
  },
  "icons": { "outline": "outline.png", "color": "color.png" },
  "accentColor": "#005F4B",
  "bots": [{
    "botId": "<<AAD_APP_CLIENT_ID>>",
    "scopes": ["personal", "team", "groupChat"],
    "commandLists": [{
      "scopes": ["personal"],
      "commands": [
        { "title": "Morning Briefing", "description": "Get your daily account intelligence briefing" },
        { "title": "Where should I focus?", "description": "See your top propensity accounts" },
        { "title": "Help", "description": "Show available commands" }
      ]
    }]
  }],
  "validDomains": ["yourapp.azurewebsites.net"]
}
```

**That's it.** Three files zipped → uploaded to Microsoft Admin Portal → agent appears in Teams and M365 Copilot.

### 5.6 Development Workflow

| Step | What | Tool |
|------|------|------|
| **1. Scaffold** | Create project from template | M365 Agents Toolkit (VS Code extension) or `pip install microsoft-agents-hosting-aiohttp` |
| **2. Code** | Write agent logic, orchestration, tools | VS Code + Python/C#/JS |
| **3. Test locally** | Run agent on localhost:3978, test via Agents Playground | `python app.py` + `teamsapptester` |
| **4. Deploy** | Push to Azure App Service or Container Apps | VS Code deploy, `az webapp deploy`, GitHub Actions |
| **5. Register** | Create Azure Bot resource, set messaging endpoint | Azure Portal or CLI |
| **6. Manifest** | Create manifest.json + icons → zip | Manual or Agents Toolkit |
| **7. Publish** | Upload app package to M365 Admin Portal | Microsoft Admin Center → Integrated Apps → Upload Custom App |
| **8. Use** | Agent appears in Teams + M365 Copilot | Sellers chat with agent in Teams |

### 5.7 Why This Matters for Account Pulse & Next Move

| Concern | How M365 Agents SDK Solves It |
|---------|------------------------------|
| **Long-running scans** | Your Python code manages the full lifecycle — send "scanning..." message, run async I/O, send results when done. No platform timeout constraints. |
| **Parallel I/O** | Native `asyncio.gather` — fire 50 Bing + EDGAR requests concurrently |
| **Databricks access** | Direct `databricks-sdk` Python client — no Azure Function intermediary needed |
| **Web search with time filter** | Direct Bing Search API v7 call with `freshness` param |

### 5.8 Operational lessons from the implemented MVP

The current implementation surfaced a few practical rules that are worth making
explicit for future M365 agentic services:

- **Admin consent must be treated as a hard prerequisite**, not as a soft
  warning after deployment
- **App registrations should be persisted by app ID/object ID**, not re-found by
  display name alone in a customer tenant
- **Operator-owned input config should stay small**, while generated runtime
  values stay script-owned
- **Repeatability improves when runtime config is tied to an input signature** so
  stale URLs, secrets, and app IDs do not leak into a new tenant or prefix
- **Fresh data platforms need bootstrap safeguards** such as auto-creating a
  Databricks SQL warehouse rather than assuming one already exists
| **Complex instructions** | System prompt is just a Python string — unlimited length |
| **Multi-agent** | LangGraph supervisor pattern or Semantic Kernel agents — full control |
| **Model choice** | Azure OpenAI GPT-4o, Anthropic Claude, or any model via API |
| **Streaming** | Can send incremental messages to user as results come in |
| **Scheduled execution** | Not built into channel (user must initiate), but agent can check cache from a scheduled Azure Function |
| **Proactive messaging** | Bot Framework proactive messaging — push morning briefing to seller in Teams at 7 AM |

### 5.8 Long-Running Operations in Teams — Channel Constraints and Solutions

#### The Critical Constraint

Teams imposes a **~15 second HTTP response timeout** on bot message handlers. If your `POST /api/messages` handler doesn't return HTTP 200 within ~15 seconds, Teams will retry the request and may show an error to the user.

**However**, this does NOT mean your work must complete in 15 seconds. You just need to **acknowledge receipt** quickly and do the real work asynchronously.

#### Teams Channel Timeout Summary

| Layer | Limit | Impact |
|-------|-------|--------|
| **Teams → Bot HTTP response** | ~15 seconds | Must return HTTP 200 within this window |
| **Bot → Teams proactive message** | No hard limit | Once you return 200, you can send messages at any time |
| **Typing indicator** | ~3-4 seconds before it disappears | SDK auto-refreshes via `startTypingTimer()` — user sees continuous "typing..." |
| **M365 Copilot orchestrator** | ~100-200s for full turn | Only affects declarative/Copilot Studio agents, NOT custom engine agents |

#### Built-in SDK Support

The M365 Agents SDK provides three mechanisms for long-running operations:

**1. Typing Indicator Timer** — User sees "bot is typing..." while work runs:
```python
# Auto-start on every turn:
AGENT_APP = AgentApplication[TurnState](
    storage=MemoryStorage(),
    adapter=CloudAdapter(),
    start_typing_timer=True,       # sends typing indicators automatically
    long_running_messages=True     # enables long-running message support
)
```

**2. Proactive Messaging** — Send results after background work completes:
```python
# Built-in first-class API:
await AGENT_APP.send_proactive_activity(
    bot_app_id,
    conversation_reference,   # stored from the original user message
    completed_briefing_text
)
```

**3. Update-in-Place** — Replace a placeholder message with completed content:
```
PUT /v3/conversations/{conversationId}/activities/{activityId}
{ "type": "message", "text": "Here is your completed briefing:\n\n..." }
```

#### Recommended Pattern for Account Pulse (Long-Running)

```python
@AGENT_APP.activity("message")
async def on_message(context: TurnContext, state: TurnState):
    user_message = context.activity.text

    # ── WITHIN 15-SECOND HTTP WINDOW ──

    # 1. Send immediate acknowledgment
    await context.send_activity(
        "Scanning your 23 accounts across 18 parent companies. "
        "This takes about 15-20 seconds — I'll send results shortly..."
    )

    # 2. Store conversation reference for proactive messaging
    conv_ref = TurnContext.get_conversation_reference(context.activity)

    # 3. Fire-and-forget background task
    asyncio.create_task(
        run_briefing_and_notify(conv_ref, territory="GreatLakes-ENT-Named-1")
    )

    # HTTP 200 returns immediately to Teams ✓

async def run_briefing_and_notify(conv_ref, territory):
    """Runs in background — no HTTP timeout pressure."""

    # 4. Load accounts from Databricks
    accounts = await databricks_client.get_accounts(territory)

    # 5. Parallel web search + EDGAR for ALL accounts at once
    tasks = []
    for gu in accounts.global_ultimates:
        tasks.append(bing_search(f'"{gu}" news', freshness="Day"))
        tasks.append(bing_search(f'"{gu}" ransomware OR breach', freshness="Day"))
        if accounts.segment in ("Enterprise", "Commercial"):
            tasks.append(edgar_lookup(gu))

    results = await asyncio.gather(*tasks)  # 50+ calls in parallel → ~5-10s

    # 6. LLM classification + Veeam Lens
    briefing = await llm_classify_and_format(results, accounts)

    # 7. Send completed briefing via proactive message
    await AGENT_APP.send_proactive_activity(APP_ID, conv_ref, briefing)
```

**Result:** User sees "Scanning your accounts..." immediately, then 15-20 seconds later, the completed briefing appears in the same chat. No polling, no Azure Function async dance.

#### Recommended Pattern for Next Move (Moderate-Running)

Next Move is lighter (~20-40s) but still benefits from the same pattern:

```python
@AGENT_APP.activity("message")
async def on_message(context: TurnContext, state: TurnState):
    # Typing indicator runs automatically (startTypingTimer=True)
    # User sees "bot is typing..." throughout

    # For Next Move, the work may complete within the HTTP window
    # Use typing indicator as the UX bridge

    result = await next_move_orchestrator.run(context.activity.text)
    await context.send_activity(result)
```

If Next Move latency grows (e.g., full briefing with 5 emails), promote to the same fire-and-forget pattern as Account Pulse.

### 5.9 Proactive Messaging (Scheduled Briefing Push)

Unlike Copilot Studio, pro-code agents can **proactively message users** via Bot Framework:

```
┌──────────────────────────────────────────────────────────────┐
│  5:30 AM — Azure Function (timer trigger)                    │
│  ├── Pre-computes briefings for all territories              │
│  └── Stores in cache (Azure Table / Cosmos DB)               │
├──────────────────────────────────────────────────────────────┤
│  7:00 AM — Azure Function (timer trigger)                    │
│  ├── For each seller with cached briefing:                   │
│  │   └── Call Bot Framework proactive messaging API          │
│  │       └── Send briefing to seller's Teams chat            │
│  └── Seller opens Teams, sees morning briefing waiting       │
└──────────────────────────────────────────────────────────────┘
```

This directly replaces the current Claude Code headless runner + Windows Task Scheduler approach.

---

## 6. Azure Databricks Connectivity Design

### 6.1 The Challenge

There is **no native "Azure Databricks" connector** in the Power Platform connector gallery that supports arbitrary SQL queries against Databricks SQL Warehouse. The Databricks Power Platform connector is designed for job/notebook orchestration, not ad-hoc SQL.

### 6.2 Recommended Approach: Azure Function + REST API Tool

For **Copilot Studio agents (Paths A/C)**, the cleanest pattern is:

For **Pro-Code agents (Path B)**, the Azure Function layer is optional — your Python code can call the Databricks SQL API directly via `databricks-sdk`.

The Copilot Studio pattern:

```
Copilot Studio Agent
        │
        ▼
  REST API Tool (OpenAPI v2 spec)
        │
        ▼
  Azure Function (Python/C#)
    ├── Authenticates to Databricks via Entra ID (managed identity)
    ├── Executes parameterized SQL via Databricks SQL Statement Execution API
    ├── Returns structured JSON
    └── Hosted in same Azure tenant
```

**Why Azure Functions:**
- Same-tenant deployment — no cross-tenant auth complexity
- Managed Identity for Databricks authentication (no stored secrets)
- Parameterized queries prevent SQL injection
- Response shaping (raw Databricks JSON → agent-friendly JSON)
- Rate limiting and caching can be added
- OpenAPI spec auto-generated by Azure Functions

### 6.3 Azure Function Endpoints (Required)

| Function | Purpose | SQL Target / Source | Used By |
|----------|---------|-----------------|---------|
| `GET /accounts/{territory}` | Get all accounts for a seller's territory | Territory-to-account mapping table | Account Pulse, Next Move |
| `GET /rep/{name}` | Look up rep → territory mapping | Rep lookup table | Next Move |
| `GET /propensity/{territory}?top=N` | Get top N accounts by Xf score | `account_iq_scores` | Next Move |
| `GET /contacts/{account_id}` | Get contacts for an account (deduplicated, do-not-call filtered, ranked) | `aiq_contact` | Next Move |
| `GET /account-details/{account_id}` | Get detailed account info (D&B, segment, customer/prospect status) | Multiple tables (enrichment) | Account Pulse (v2) |
| `POST /briefing/{territory}` | **Start async full territory scan** (Bing news + SEC EDGAR for all Global Ultimates, parallelized). Returns job ID + account list immediately. | Databricks + Bing API + SEC EDGAR | Account Pulse |
| `GET /briefing/{job_id}` | **Poll for briefing results** — returns status + compiled signals when complete | Azure Table/Cosmos (job store) | Account Pulse |
| `GET /briefing/cache/{territory}` | **Get pre-computed morning briefing** (written by 5:30 AM scheduled run) | Azure Table/Cosmos (cache store) | Account Pulse |
| `GET /news/{company}?type={general\|cyber}&freshness=48h` | **Bing Search with time filtering** — used directly for single-account queries | Bing Search API v7 | Account Pulse |
| `GET /edgar/{company}` | **SEC EDGAR lookup** — recent filings with rate limiting + CIK cache | SEC EDGAR API | Account Pulse |

### 6.4 Alternative Approach: Direct REST API Tool to Databricks SQL API

Copilot Studio's **REST API tool** could point directly to the Databricks SQL Statement Execution API:

- **Endpoint:** `https://<workspace>.azuredatabricks.net/api/2.0/sql/statements`
- **Auth:** OAuth 2.0 via Microsoft Entra ID (same tenant)
- **Limitation:** Requires building SQL query strings in the agent instructions, which is fragile and risks prompt injection

**Verdict:** Azure Function intermediary is **strongly recommended** over direct Databricks API access.

### 6.5 Alternative Approach: MCP Server

Since Copilot Studio supports MCP natively, an alternative is to host an MCP server (e.g., on Azure App Service) that wraps Databricks queries — similar to the current `aiq-agent` MCP server. This preserves the existing tool interface while enabling Copilot Studio integration.

| Approach | Pros | Cons |
|----------|------|------|
| **Azure Function + REST API Tool** | Clean OpenAPI contract, managed identity, easy to test independently, auto-scales | Extra deployment to manage |
| **Direct Databricks SQL API** | No intermediary needed | Fragile SQL in prompts, auth complexity in agent, no query parameterization |
| **MCP Server on Azure** | Reuses existing MCP tool interface, familiar pattern | MCP support in Copilot Studio is newer, requires hosting MCP server |

**Primary Recommendation:** Azure Function + REST API Tool

---

## 7. Agent 1: Account Pulse — Design

### 6.1 Agent Configuration

| Field | Value |
|-------|-------|
| **Type** | Copilot Studio Custom Agent (Child Agent) |
| **Name** | Account Pulse |
| **Description** | Daily intelligence briefing agent for Veeam field sellers. Scans trusted external sources for news, SEC filings, and cybersecurity events related to a seller's accounts, then delivers a prioritized briefing with Veeam-relevant insights. |
| **Channel** | Teams, M365 Copilot app (via parent Daily Account Planner) |

### 6.2 Instructions (Summary — full prompt to be authored in Copilot Studio)

Key instruction elements to embed:
- Priority framework (4 tiers: Threat > Change > Business > Regulatory)
- Veeam Lens (5 themes: Data Resilience, Cloud Migration, Regulatory, AI Adoption, Business Growth)
- Segment-dependent formatting (Enterprise/Commercial: full cards; Velocity: top-5 one-liners)
- Hallucination guardrails (every claim needs source, never fabricate URLs, never speculate)
- Account grouping by Global Ultimate (scan parent company, not subsidiaries)

> **Note:** Copilot Studio custom agents do not have an 8,000 char instruction limit when using topic-based authoring. The full Account Pulse system prompt (~700 lines) can be broken across multiple topics.

### 6.3 Tools

| Tool | Type | Purpose | Configuration |
|------|------|---------|---------------|
| **Get Seller Accounts** | REST API Tool | Retrieve accounts for a territory from Databricks | Azure Function `GET /accounts/{territory}` |
| **Get Cached Briefing** | REST API Tool | Retrieve pre-computed morning briefing (fast path) | Azure Function `GET /briefing/cache/{territory}` |
| **Start Briefing Scan** | REST API Tool | Start async full-territory scan (returns job ID immediately) | Azure Function `POST /briefing/{territory}` |
| **Get Briefing Results** | REST API Tool | Poll for async scan results | Azure Function `GET /briefing/{job_id}` |
| **News Search** | REST API Tool | Search Bing for time-filtered news about a single company | Azure Function `GET /news/{company}?type={general\|cyber}&freshness=48h` |
| **SEC EDGAR Lookup** | REST API Tool | Search SEC EDGAR for recent filings by company name | Azure Function `GET /edgar/{company}` |
| **Bing Web Search** | Knowledge (Web Grounding) | Fallback general web search | Bing Search grounding enabled at agent level |

### 6.4 Knowledge Sources

| Source | Type | Purpose |
|--------|------|---------|
| **Public web (Bing)** | Web search grounding | General news, cybersecurity events, business news |
| **Veeam Lens reference** | SharePoint document | Veeam theme definitions, product mapping, competitive context |

### 6.5 Workflow in Copilot Studio

**Path A — Morning Briefing (pre-computed, fast)**
```
User: "Give me my morning briefing"
         │
         ▼
  Parent Agent routes to Account Pulse child
         │
         ▼
  ┌─── Account Pulse Agent ───────────────────────────────────────┐
  │                                                                │
  │  1. IDENTIFY SELLER → extract territory                        │
  │                                                                │
  │  2. CHECK CACHE (Tool: Get Cached Briefing)                    │
  │     └── Calls GET /briefing/cache/{territory}                  │
  │     └── Pre-computed at 5:30 AM by scheduled Azure Function    │
  │                                                                │
  │  3. IF cache hit:                                              │
  │     ├── APPLY Veeam Lens to cached signal data                 │
  │     ├── FORMAT output by segment                               │
  │     └── RETURN briefing to user (~5-10 seconds total)          │
  │                                                                │
  │  4. IF cache miss: → fall through to Path B                    │
  └────────────────────────────────────────────────────────────────┘
```

**Path B — Ad-Hoc Full Territory Scan (async, longer)**
```
User: "What's happening in my accounts?" (midday, no cache)
         │
         ▼
  ┌─── Account Pulse Agent ───────────────────────────────────────┐
  │                                                                │
  │  1. IDENTIFY SELLER → extract territory                        │
  │                                                                │
  │  2. START ASYNC SCAN (Tool: Start Briefing Scan)               │
  │     └── Calls POST /briefing/{territory}                       │
  │     └── Azure Function fires ALL Bing + EDGAR calls in         │
  │         parallel (asyncio.gather / Task.WhenAll)               │
  │     └── Returns immediately: job_id + account_count            │
  │                                                                │
  │  3. SET EXPECTATIONS                                           │
  │     └── "Scanning 23 accounts across 18 parent companies.      │
  │          This will take about 15-20 seconds..."                │
  │                                                                │
  │  4. POLL FOR RESULTS (Tool: Get Briefing Results)              │
  │     └── Calls GET /briefing/{job_id}                           │
  │     └── Returns compiled signals when complete                 │
  │                                                                │
  │  5. CLASSIFY signals by 4-tier priority                        │
  │  6. APPLY Veeam Lens to each signal                            │
  │  7. FORMAT output by segment                                   │
  │     └── ENT/COM: Full signal cards, sorted Tier 1→4           │
  │     └── VEL: Top 5 one-liners                                 │
  │     └── No news: "Your accounts are quiet today"              │
  │                                                                │
  │  8. RETURN briefing to user                                    │
  └────────────────────────────────────────────────────────────────┘
```

**Path C — Single Account Deep Dive (direct, fast)**
```
User: "What's happening at Ford?"
         │
         ▼
  ┌─── Account Pulse Agent ───────────────────────────────────────┐
  │                                                                │
  │  1. IDENTIFY account → "Ford"                                  │
  │                                                                │
  │  2. WEB SEARCH — direct calls (only 2-3 queries, fast)        │
  │     ├── Tool: News Search → GET /news/Ford?type=general        │
  │     └── Tool: News Search → GET /news/Ford?type=cyber          │
  │                                                                │
  │  3. SEC EDGAR (if ENT/COM)                                     │
  │     └── Tool: EDGAR Lookup → GET /edgar/Ford                   │
  │                                                                │
  │  4. CLASSIFY + Veeam Lens + FORMAT                             │
  │  5. RETURN (~5-10 seconds total)                               │
  └────────────────────────────────────────────────────────────────┘
```

### 6.6 Starter Prompts

| Prompt | Description |
|--------|-------------|
| "Give me my morning briefing" | Full territory scan |
| "What's happening at {account}?" | Single account deep dive |
| "Any cybersecurity incidents I should know about?" | Filtered to Tier 1 threats |

### 6.7 Inputs / Outputs (as Child Agent)

**Inputs:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `territory_string` | String | No | Seller's territory identifier (e.g., `GreatLakes-ENT-Named-1`) |
| `account_name` | String | No | Specific account for single-account query |

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| `briefing_markdown` | String | Formatted briefing text |
| `signal_count` | Number | Total signals found |
| `tier1_count` | Number | Count of Tier 1 (Threat) signals |

---

## 8. Agent 2: Next Move — Design

### 7.1 Agent Configuration

| Field | Value |
|-------|-------|
| **Type** | Copilot Studio Custom Agent (Child Agent) |
| **Name** | Next Move |
| **Description** | Propensity and outreach agent for Veeam sellers. Identifies highest-potential accounts using AI-powered propensity scores, explains why each account is a fit, identifies who to contact, and drafts JOLT-methodology outreach emails. |
| **Channel** | Teams, M365 Copilot app (via parent Daily Account Planner) |

### 7.2 Instructions (Summary)

Key instruction elements:
- Propensity score interpretation (Xf score, sub-scores, momentum)
- "Why" field translation logic (40+ data-engineer strings → seller-friendly language)
- 30+ sales play definitions across 3 categories (New Logo, Cross-Sell/Upsell, Special)
- Contact selection logic (priority: engaged executives → any executives → engaged practitioners → most recent)
- JOLT email methodology (Judge, Offer, Recommend, Limit, Take Risk Off)
- Role-based framing guide (CISO vs CTO vs CIO vs Director vs Practitioner)
- 5 output format specifications
- Error handling rules (no contacts → no fabrication, same-org duplicates → alert)

### 7.3 Tools

| Tool | Type | Purpose | Configuration |
|------|------|---------|---------------|
| **Lookup Rep** | REST API Tool | Map rep name → territory string | Azure Function `GET /rep/{name}` |
| **Get Top Opportunities** | REST API Tool | Get top N accounts by Xf propensity score for a territory | Azure Function `GET /propensity/{territory}?top=5` |
| **Get Account Contacts** | REST API Tool | Get contacts for an account with engagement data | Azure Function `GET /contacts/{account_id}` |

### 7.4 Knowledge Sources

| Source | Type | Purpose |
|--------|------|---------|
| **JOLT methodology guide** | SharePoint document | Email drafting reference, tone rules, role-based framing |
| **Play definitions** | SharePoint document | Detailed play descriptions, qualifying criteria |
| **Why-field translation table** | SharePoint document | Mapping of raw data strings to seller-friendly language |

### 7.5 Workflow in Copilot Studio

```
User: "Where should I focus today?"
         │
         ▼
  Parent Agent routes to Next Move child
         │
         ▼
  ┌─── Next Move Agent ───────────────────────────────────────────┐
  │                                                                │
  │  1. IDENTIFY SELLER                                            │
  │     ├── Detect territory string in input (bypass lookup)       │
  │     └── OR call Lookup Rep tool to resolve name → territory    │
  │                                                                │
  │  2. GET TOP ACCOUNTS (Tool: Get Top Opportunities)             │
  │     └── Returns: top 5 by Xf score + sub-scores + play flags  │
  │                  + "why" fields                                │
  │                                                                │
  │  3. FOR EACH ACCOUNT:                                          │
  │     ├── 3a. TRANSLATE "why" fields to seller language          │
  │     ├── 3b. IDENTIFY applicable plays from boolean flags       │
  │     ├── 3c. DETECT signals (momentum, intent, upsell,         │
  │     │       competitive)                                       │
  │     └── 3d. GET CONTACTS (Tool: Get Account Contacts)          │
  │         ├── Deduplicate by name                                │
  │         ├── Filter do-not-call                                 │
  │         └── Rank by selection logic                            │
  │                                                                │
  │  4. DETERMINE output format                                    │
  │     ├── Broad question → Format 3 (Quick List) as default     │
  │     ├── "Full briefing" → Format 1                             │
  │     ├── Specific account → Format 2 (Deep Dive)               │
  │     ├── "Draft email" → Format 4                               │
  │     └── "Follow up" → Format 5                                 │
  │                                                                │
  │  5. FOR FORMATS 1, 2, 4, 5:                                    │
  │     └── DRAFT JOLT email with role-based framing               │
  │         ├── Subject + 2 alternatives                           │
  │         ├── Multi-threading nudge                              │
  │         └── "Personalize before sending" reminder              │
  │                                                                │
  │  6. RETURN formatted response                                  │
  └────────────────────────────────────────────────────────────────┘
```

### 7.6 Starter Prompts

| Prompt | Description |
|--------|-------------|
| "Where should I focus today?" | Quick List (Format 3) of top 5 accounts |
| "Give me the full briefing" | Full briefing with emails (Format 1) |
| "What's the play for {account}?" | Single account deep dive (Format 2) |
| "Draft me an email to {contact} at {account}" | Email only (Format 4) |

### 7.7 Inputs / Outputs (as Child Agent)

**Inputs:**
| Name | Type | Required | Description |
|------|------|----------|-------------|
| `territory_string` | String | No | Territory identifier (e.g., `GreatLakes-ENT-Named-1`) |
| `rep_name` | String | No | Seller name for lookup |
| `account_name` | String | No | Specific account for deep dive |
| `contact_name` | String | No | Specific contact for email draft |
| `format` | String | No | Output format override (quick_list, full, deep_dive, email, followup) |

**Outputs:**
| Name | Type | Description |
|------|------|-------------|
| `response_markdown` | String | Formatted response in requested format |
| `top_account_name` | String | Highest-ranked account name |
| `top_xf_score` | Number | Highest Xf score in results |

---

## 9. Orchestrator: Daily Account Planner — Design

### 8.1 Parent Agent Configuration

| Field | Value |
|-------|-------|
| **Type** | Copilot Studio Custom Agent (Parent Agent) |
| **Name** | Daily Account Planner |
| **Description** | Your AI sales companion for Veeam. Orchestrates account intelligence, propensity analysis, and outreach preparation. Ask about your accounts, where to focus, or get your morning briefing. |
| **Channel** | Microsoft Teams, M365 Copilot app |

### 8.2 Child Agents

| Child Agent | Route When |
|-------------|-----------|
| **Account Pulse** | User asks about news, briefings, what's happening in accounts |
| **Next Move** | User asks about focus, top accounts, propensity, emails, outreach |
| *(Future: Quota Coach)* | User asks about quota, pipeline, forecast |
| *(Future: Deal Coach)* | User asks about deal strategy, competitive positioning |

### 8.3 Instructions

```
You are the Daily Account Planner for Veeam sellers. You orchestrate
multiple AI sub-agents to help sellers prepare for their day.

ROUTING RULES:
- Questions about news, briefings, cyber events, SEC filings → Account Pulse
- Questions about focus, top accounts, propensity, outreach, emails → Next Move
- Questions about both ("full morning prep") → Run Account Pulse first,
  then Next Move, combine results

CONTEXT PASSING:
- When a seller identifies themselves, pass territory_string to both agents
- When a seller asks about a specific account, pass account_name to the
  relevant agent

OUT OF SCOPE:
- Quota/pipeline questions → "Quota Coach is coming in a future update"
- Deal strategy → "Deal Coach is coming in a future update"
- General Veeam product questions → redirect to Veeam documentation
```

### 8.4 Authentication

- **Microsoft Entra ID** (single sign-on via Teams)
- The signed-in user's UPN maps to a seller identity in Databricks
- Azure Functions use **Managed Identity** for Databricks access
- No additional login required for the seller

---

## 10. Gap Analysis

### 9.1 Capability Gaps

| Feature | Current (Claude Code) | Copilot Studio | Gap | Mitigation |
|---------|----------------------|----------------|-----|------------|
| **Instruction length** | Unlimited system prompt | 8K chars (Agent Builder) / Unlimited (Topics) | Medium | Use Copilot Studio custom agent with topic-based authoring. Reference knowledge docs for detailed rules (e.g., "why" translation table). |
| **Web search precision** | Claude `web_search` with custom queries + time-range filtering | Bing Search grounding with less control over time-range constraints | **High** | Build time-filtering into Azure Function or use Bing Custom Search with date parameters. Alternatively, use a dedicated web-search Azure Function that calls Bing Search API with `freshness` parameter. |
| **SEC EDGAR direct HTTP** | Python `requests` with rate limiting + CIK caching | No native SEC EDGAR connector | Medium | Azure Function wrapping SEC EDGAR API with same logic (rate limiting, CIK cache, suffix stripping). Expose as REST API tool. |
| **Multi-step chained tool calls** | Claude natively chains: load accounts → search each → classify → format | Copilot Studio generative orchestration handles tool selection but iteration over lists is not native | **High** | Option A: Azure Function does the batch work (accepts territory, returns enriched account list with news pre-fetched). Option B: Use topic-based authoring with loop nodes. Option C: Lean on generative orchestration to call tools iteratively. |
| **Excel file parsing** | `openpyxl` library | Not needed (replaced by Databricks) | None | N/A — fully replaced by Databricks. |
| **Scheduled/headless execution** | Windows Task Scheduler + headless Python runner | Copilot Studio autonomous triggers (schedule-based) | Low | Use autonomous agent trigger on schedule (e.g., 6:00 AM daily). Requires Copilot Studio premium. |
| **Output formatting precision** | Full markdown control with emoji, blockquotes, tables | Markdown supported in Teams adaptive cards and chat | Low | Test formatting in Teams. Use adaptive cards for rich structured output. |
| **Hallucination control** | System prompt guardrails + source-required rules | Instruction-based guardrails + knowledge grounding | Medium | Embed hallucination rules in agent instructions. Use knowledge grounding to anchor responses. Test extensively with adversarial prompts. |
| **"Why" field translation** | Hardcoded translation logic in system prompt | Must fit in instructions or knowledge | Low | Store translation table as SharePoint knowledge source. Reference in instructions. |
| **JOLT email methodology** | Full prompt with 30+ rules | Must fit in instructions or knowledge | Low | Store JOLT guide as SharePoint knowledge source. Core rules in instructions. |
| **Contact deduplication** | Python logic with name matching | No native dedup | Medium | Handle in Azure Function (`GET /contacts/{account_id}` returns already-deduplicated, filtered, ranked contacts). |
| **Single-account deep dive** | Supported via user query | Supported via child agent input routing | None | Route `account_name` input to child agent. |

### 9.2 Critical Gaps — Detailed Analysis

#### Gap 1: Web Search Time Filtering (HIGH)

**Problem:** Account Pulse requires news from the **last 48 hours only**. Bing Search grounding in Copilot Studio does not natively expose time-range parameters.

**Mitigations (choose one):**

| Option | Approach | Trade-off |
|--------|----------|-----------|
| **A. Bing Search Azure Function** | Azure Function calls Bing Search API v7 with `freshness=Day` parameter, returns only recent results | Full control, but adds another Azure Function |
| **B. Bing Custom Search** | Configure Bing Custom Search instance with freshness constraints | Less flexible, requires separate Bing resource |
| **C. Instruction-based filtering** | Tell agent to ignore results older than 48 hours | Relies on LLM compliance; Bing may return older results |
| **D. Accept Bing defaults** | Use Bing grounding as-is, rely on recency bias | Simplest but least precise |

**Recommendation:** Option A (Bing Search Azure Function) for Account Pulse; supplements the grounding approach with precise time control.

#### Gap 2: Iterative Tool Calls Over Lists (HIGH)

**Problem:** Account Pulse needs to scan N accounts (each requiring 2-3 web searches + 1 SEC lookup). The current Claude approach iterates naturally. Copilot Studio's generative orchestration may not iterate cleanly over a list of accounts.

**Mitigations:**

| Option | Approach | Trade-off |
|--------|----------|-----------|
| **A. "Fat" Azure Function** | Single function accepts territory, internally loops through accounts, performs all web searches and SEC lookups, returns compiled briefing data | Moves intelligence logic out of agent into Azure Function; agent just formats |
| **B. Topic with loop node** | Author a topic in Copilot Studio that iterates over the account list | Topic authoring is more complex, but keeps logic in agent |
| **C. Trust generative orchestration** | Provide clear instructions; let the LLM iterate through accounts calling tools | May work for small account lists but unreliable for many accounts |

**Recommendation:** Option A (Fat Azure Function) for reliability. The function returns structured JSON with all signals pre-collected; the agent applies the Veeam Lens and formats.

#### Gap 3: Long-Running Process Execution (HIGH)

**Problem:** Both agents — especially Account Pulse — are **long-running processes** that will exceed Copilot Studio conversational timeouts and user patience thresholds.

**Latency estimates:**

| Agent | Operation | Estimated Time |
|-------|-----------|---------------|
| **Account Pulse** | Load accounts from Databricks | 1-2s |
| | Web search (2 queries × 15-25 Global Ultimates) | 30-75s (Bing API latency ~1-2s/call) |
| | SEC EDGAR lookups (15-25 calls, rate-limited 5/sec) | 3-5s |
| | LLM classification + Veeam Lens + formatting | 5-15s |
| | **Total (full territory)** | **40-100 seconds** |
| **Next Move** | Rep lookup + top opportunities | 2-3s |
| | Contact lookups (5 accounts) | 3-5s |
| | LLM: translate "why" + classify plays + draft 5 JOLT emails | 15-30s |
| | **Total (full briefing with emails)** | **20-40 seconds** |

**Platform constraints:**

| Constraint | Limit | Impact |
|-----------|-------|--------|
| Copilot Studio single tool call timeout | ~100-200s | Account Pulse "fat function" may hit this |
| Azure Function (Consumption plan) timeout | 5 min (max 10 min) | Sufficient, but cold start adds latency |
| Azure Function (Premium plan) timeout | 30 min | Sufficient |
| User expectation in Teams chat | ~10-15s before anxiety | Both agents exceed this |
| Teams adaptive card render | Must receive response to render | No streaming/partial updates in tool responses |

**Mitigations:**

| Option | Approach | Best For | Trade-off |
|--------|----------|----------|-----------|
| **A. Async with proactive messaging** | Azure Function returns immediately with a job ID. Agent tells user "Scanning your 23 accounts — I'll message you when ready (~60s)." Background process completes → proactive Teams message via Bot Framework. | Account Pulse (full territory) | Requires Bot Framework proactive messaging setup; more complex architecture |
| **B. Chunked responses** | Azure Function processes accounts in batches (e.g., 5 at a time). Agent calls function multiple times, delivering partial results: "Here are the first 5 accounts... scanning the next batch now." | Account Pulse (full territory) | Multiple round-trips; user sees incremental results (good UX); but multiple LLM turns add up |
| **C. Pre-computed briefings** | Scheduled Azure Function (e.g., 5:30 AM daily) pre-computes all briefings and stores in Azure Table Storage / Cosmos DB. Agent retrieves the cached result instantly. | Account Pulse (scheduled use case) | Stale data (morning snapshot); doesn't support ad-hoc "what's happening at X?" queries |
| **D. Optimized fat function with parallel calls** | Azure Function uses `asyncio`/`Task.WhenAll` to parallelize all Bing + EDGAR calls (not sequential). Cuts 75s down to ~10-15s. | Both agents | Best first optimization; reduces the problem significantly |
| **E. Agent Flow with long-running action** | Use Power Automate agent flow as the tool instead of REST API. Agent flows support longer execution and can use the "wait for approval/response" pattern. | Account Pulse | More complex to build; ties to Power Automate licensing |

**Recommended approach — layered strategy:**

```
┌─────────────────────────────────────────────────────────────────┐
│                     LAYER 1: Optimize First                     │
│  Azure Function parallelizes all Bing + EDGAR calls internally  │
│  Target: reduce Account Pulse from 40-100s → 10-20s             │
│  Target: Next Move stays at 20-40s (already acceptable)         │
├─────────────────────────────────────────────────────────────────┤
│                     LAYER 2: Set Expectations                   │
│  Agent sends immediate acknowledgment:                          │
│  "Scanning 23 accounts across 18 parent companies.              │
│   This takes about 15-20 seconds..."                            │
├─────────────────────────────────────────────────────────────────┤
│                     LAYER 3: Pre-Compute for Morning Briefing   │
│  Scheduled function at 5:30 AM pre-generates briefing data      │
│  Agent checks cache first → instant response for morning use    │
│  Falls back to live scan for ad-hoc queries                     │
├─────────────────────────────────────────────────────────────────┤
│                     LAYER 4: Async Fallback (if needed)         │
│  If territory is very large (50+ accounts):                     │
│  → Return partial results immediately (top 10 by priority)      │
│  → Background process completes full scan                       │
│  → Proactive Teams message with complete briefing               │
└─────────────────────────────────────────────────────────────────┘
```

**Impact on Azure Function design:**

The Account Pulse "fat function" must be redesigned as:

| Endpoint | Purpose | Pattern |
|----------|---------|---------|
| `POST /briefing/{territory}` | Start full territory scan | Returns job ID + immediate partial data (account list + segment) |
| `GET /briefing/{job_id}` | Poll for results | Returns status + results when complete |
| `GET /briefing/cache/{territory}` | Get pre-computed morning briefing | Returns cached results or 404 |
| `POST /briefing/{territory}/batch?offset=0&limit=5` | Chunked scan | Processes 5 Global Ultimates at a time |

The function should internally use **parallel async I/O** (`asyncio.gather` in Python / `Task.WhenAll` in C#) to fire all Bing + EDGAR requests concurrently rather than sequentially.

### 9.3 Gaps Summary

| Gap | Severity | Recommended Mitigation |
|-----|----------|----------------------|
| Long-running process execution | **Critical** | Parallel async I/O + pre-computed cache + chunked responses + expectation setting (see layered strategy above) |
| Web search time filtering | High | Bing Search Azure Function with `freshness` param |
| Iterative multi-account scanning | High | "Fat" Azure Function that batch-processes accounts |
| SEC EDGAR integration | Medium | Azure Function wrapper |
| Contact deduplication | Medium | Handle in Azure Function |
| Instruction length | Medium | Topic-based authoring + SharePoint knowledge docs |
| Hallucination control | Medium | Strict instructions + knowledge grounding + testing |
| Scheduled execution | Low | Autonomous agent triggers |
| Output formatting | Low | Adaptive cards + markdown |

---

## 11. Implementation Roadmap

### Phase 1: Foundation (Weeks 1-3)

| Task | Description | Owner |
|------|-------------|-------|
| **1.1** Provision Azure Function App | Create Function App (**Premium plan** for warm instances + 30-min timeout) in same tenant as Databricks. Configure Managed Identity. | Platform Team |
| **1.2** Build Databricks query functions | Implement `GET /accounts/{territory}`, `GET /rep/{name}`, `GET /propensity/{territory}`, `GET /contacts/{account_id}` (contacts pre-deduplicated, do-not-call filtered, ranked) | Backend Dev |
| **1.3** Build Bing Search function | Implement `GET /news/{company}?type={general\|cyber}&freshness=48h` calling Bing Search API v7 with `freshness` parameter. API key stored in Key Vault. | Backend Dev |
| **1.4** Build SEC EDGAR function | Implement `GET /edgar/{company}` with rate limiting (5 req/sec), CIK cache, company-name suffix stripping | Backend Dev |
| **1.5** Build async briefing pipeline | Implement `POST /briefing/{territory}` (start scan), `GET /briefing/{job_id}` (poll), `GET /briefing/cache/{territory}` (cached). Uses **parallel async I/O** for all Bing + EDGAR calls. Job state stored in Azure Table Storage. | Backend Dev |
| **1.6** Build scheduled pre-computation | Timer-triggered function (5:30 AM daily) that pre-computes briefings for all active territories, stores results in cache. | Backend Dev |
| **1.7** Generate OpenAPI v2 specs | Auto-generate from Azure Function App for all endpoints | Backend Dev |
| **1.8** Test all Azure Functions | Unit tests + integration tests against Databricks, Bing API, and SEC EDGAR. Load test async pipeline with realistic territory sizes (30-50 accounts). | Backend Dev |

### Phase 2: Agent Build (Weeks 3-5)

| Task | Description | Owner |
|------|-------------|-------|
| **2.1** Create parent agent (Daily Account Planner) | Configure in Copilot Studio with routing instructions | Agent Builder |
| **2.2** Create Account Pulse child agent | Configure instructions, add REST API tools, add Bing grounding, add knowledge sources | Agent Builder |
| **2.3** Create Next Move child agent | Configure instructions, add REST API tools, add knowledge sources (JOLT guide, play definitions, why-translation) | Agent Builder |
| **2.4** Upload knowledge documents | JOLT methodology, Veeam Lens definitions, play definitions, why-translation table → SharePoint | Agent Builder |
| **2.5** Configure starter prompts | Add all starter prompts for each agent | Agent Builder |
| **2.6** Configure authentication | Entra ID SSO, Managed Identity for Azure Functions | Platform Team |

### Phase 3: Testing & Refinement (Weeks 5-7)

| Task | Description | Owner |
|------|-------------|-------|
| **3.1** Functional testing | Test all 3 agents with real seller territories from Databricks | Agent Builder + QA |
| **3.2** Output format testing | Verify all 5 Next Move formats + all 3 Account Pulse segment formats | Agent Builder + QA |
| **3.3** Hallucination testing | Adversarial prompts, source verification, URL fabrication checks | QA |
| **3.4** Performance testing | Measure e2e latency: morning briefing from cache (target: < 10s), ad-hoc full scan (target: < 30s), single account (target: < 10s), Next Move full (target: < 40s) | Platform Team |
| **3.5** Instruction tuning | Iterate on instructions based on output quality | Agent Builder |
| **3.6** User acceptance | Demo to pilot sellers, gather feedback | Product |

### Phase 4: Pilot Deployment (Week 7-8)

| Task | Description | Owner |
|------|-------------|-------|
| **4.1** Publish to Teams | Deploy agents to pilot seller group in Teams | Platform Team |
| **4.2** Monitor usage & feedback | Track adoption, error rates, feedback | Product |
| **4.3** Iterate | Refine based on pilot feedback | Agent Builder |

### Future Phases

| Phase | Features |
|-------|---------|
| **Phase 5** | Autonomous scheduled briefing (7 AM daily push to Teams) |
| **Phase 6** | Cross-agent wiring (Account Pulse news in Next Move emails) |
| **Phase 7** | Additional child agents (Quota Coach, Deal Coach, Voice of Customer) |

---

## 12. Security & Governance

### 11.1 Authentication Flow

```
Seller (Teams SSO)
    │
    ▼
Microsoft Entra ID
    │
    ├── Token for Copilot Studio agent
    │
    └── Copilot Studio → Azure Function
            │
            ├── Azure Function Managed Identity
            │       │
            │       └── Databricks SQL Warehouse
            │           (Unity Catalog row-level security)
            │
            └── Azure Function → SEC EDGAR (public, no auth)
                Azure Function → Bing Search API (API key in Key Vault)
```

### 11.2 Data Security

| Concern | Mitigation |
|---------|-----------|
| **Data residency** | Azure Functions and Databricks in same Azure region |
| **Data in transit** | All connections over HTTPS/TLS 1.2+ |
| **Credentials** | No stored credentials — Managed Identity for Databricks; Bing API key in Azure Key Vault |
| **Row-level security** | Databricks Unity Catalog restricts data to seller's territory |
| **PII in contacts** | Contact data (email, phone) displayed only in agent response; not stored in Copilot Studio |
| **Do-not-call compliance** | Filtering done in Azure Function; excluded contacts never reach the agent |

### 11.3 Governance

| Policy | Implementation |
|--------|---------------|
| **Agent approval** | Copilot Studio admin approval required before publishing |
| **DLP policies** | Power Platform DLP policies block agents from accessing unauthorized connectors |
| **Audit logging** | All agent interactions logged via M365 audit log |
| **Usage analytics** | Copilot Studio analytics dashboard for adoption tracking |

---

## Appendix: Feature Mapping Matrix

### Account Pulse Feature Mapping

| Feature | Claude Code PoC | Copilot Studio Design | Status |
|---------|----------------|----------------------|--------|
| Load seller accounts from Databricks | v2 (roadmap) | REST API Tool → Azure Function | Ready to build |
| Load seller accounts from Excel | v1 (implemented) | N/A — removed | Superseded |
| Web search — general news | Claude `web_search` | Bing Search Azure Function | Ready to build |
| Web search — cyber events | Claude `web_search` | Bing Search Azure Function | Ready to build |
| SEC EDGAR lookup | Python `requests` | REST API Tool → Azure Function | Ready to build |
| 4-tier signal classification | System prompt | Agent instructions | Ready to configure |
| Veeam Lens (5 themes) | System prompt | Agent instructions + SharePoint knowledge | Ready to configure |
| Segment detection | Python `openpyxl` | Azure Function returns segment with account data | Ready to build |
| Global Ultimate grouping | Python logic | Azure Function returns grouped data | Ready to build |
| Enterprise/Commercial format | System prompt | Agent instructions | Ready to configure |
| Velocity format (top 5) | System prompt | Agent instructions | Ready to configure |
| No-news format | System prompt | Agent instructions | Ready to configure |
| Hallucination guardrails | System prompt | Agent instructions | Ready to configure |
| Scheduled daily briefing | Windows Task Scheduler | Copilot Studio autonomous trigger | Phase 5 |
| Single account deep dive | Supported | Child agent input: `account_name` | Ready to configure |
| D&B enrichment | v2 (roadmap) | Azure Function endpoint | Phase 6+ |
| Communication recency | v2 (roadmap) | Azure Function endpoint | Phase 6+ |

### Next Move Feature Mapping

| Feature | Claude Code PoC | Copilot Studio Design | Status |
|---------|----------------|----------------------|--------|
| Rep → territory lookup | MCP tool (`lookup_rep`) | REST API Tool → Azure Function | Ready to build |
| Top opportunities by Xf score | MCP tool (`get_top_opportunities`) | REST API Tool → Azure Function | Ready to build |
| Account contacts with engagement | MCP tool (`get_account_contacts`) | REST API Tool → Azure Function | Ready to build |
| Contact deduplication | MCP tool logic | Azure Function logic | Ready to build |
| Do-not-call filtering | MCP tool logic | Azure Function logic | Ready to build |
| Contact ranking | System prompt | Agent instructions | Ready to configure |
| "Why" field translation (40+ rules) | System prompt | SharePoint knowledge doc + instructions | Ready to configure |
| 30+ sales play definitions | System prompt | SharePoint knowledge doc + instructions | Ready to configure |
| JOLT email drafting | System prompt | SharePoint knowledge doc + instructions | Ready to configure |
| Role-based framing | System prompt | Agent instructions | Ready to configure |
| Format 1: Full Briefing | System prompt | Agent instructions | Ready to configure |
| Format 2: Single Account | System prompt | Agent instructions | Ready to configure |
| Format 3: Quick List (default) | System prompt | Agent instructions | Ready to configure |
| Format 4: Draft Email | System prompt | Agent instructions | Ready to configure |
| Format 5: Follow-Up Email | System prompt | Agent instructions | Ready to configure |
| Multi-threading nudge | System prompt | Agent instructions | Ready to configure |
| Territory override detection | System prompt | Agent instructions + child agent input | Ready to configure |
| Same-org duplicate detection | System prompt | Agent instructions | Ready to configure |
| Stale contact warning | System prompt | Agent instructions | Ready to configure |
| Cross-agent news in emails | v2 (roadmap) | Cross-agent wiring (Account Pulse → Next Move) | Phase 6 |

---

*End of design document*
