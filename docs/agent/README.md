# Agent documentation (English)

This folder holds **agent-facing** material for coding assistants (Cursor, Claude Code, Codex, CLI bots, etc.).

| Document | Purpose |
|----------|---------|
| [MCP_SETUP.md](MCP_SETUP.md) | Wire Cursor / Claude Code / Codex / remote agents to a QuantDinger backend via the `quantdinger-mcp` MCP server (local stdio + remote HTTP) |
| [AGENT_ENVIRONMENT_DESIGN.md](AGENT_ENVIRONMENT_DESIGN.md) | Architecture: layered contracts (docs → commands → API/MCP), security boundaries, roadmap, implementation checklist |
| [AI_INTEGRATION_DESIGN.md](AI_INTEGRATION_DESIGN.md) | How external AI agents (P4) and autonomous strategy AIs (P5) consume QuantDinger via a versioned, scoped Agent Gateway |
| [AGENT_QUICKSTART.md](AGENT_QUICKSTART.md) | Operator + integrator walkthrough: issue a token, call the Gateway, run paper trades |
| [agent-openapi.json](agent-openapi.json) | Machine-readable contract for `/api/agent/v1` (OpenAPI 3.0) |
| [../API_CONVENTIONS.md](../API_CONVENTIONS.md) | Shared HTTP conventions (envelopes, auth, Public/Internal tiers) |
| [../api/openapi.yaml](../api/openapi.yaml) | Human Web API spec (flask-smorest; migration in progress) |

**Language policy:** Agent-oriented docs and [`.cursor/skills/`](../../.cursor/skills/) in this repository are **English only** so the same text works across tools and locales.

For human product overviews in other languages, see the root [README.md](../../README.md) and [docs/README_CN.md](../README_CN.md).
