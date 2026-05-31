# QuantDinger API conventions (OpenAPI SSOT)

This document defines the **contract rules** for QuantDinger HTTP APIs.
Machine-readable specs:

| Spec | Path | Audience |
|------|------|----------|
| Human Web API (flask-smorest) | [`docs/api/openapi.yaml`](api/openapi.yaml) | Frontend, integrators, community |
| Agent Gateway | [`docs/agent/agent-openapi.json`](agent/agent-openapi.json) | AI agents, MCP, automation |

Browse the human spec locally: open [`docs/api/index.html`](api/index.html) (Redoc).

---

## 1. Two API surfaces

### Human Web API (`/api/...`)

- Authenticated with **user JWT** (`Authorization: Bearer <jwt>`) unless noted.
- Used by the QuantDinger web/mobile UI.

### Agent Gateway (`/api/agent/v1/...`)

- Authenticated with **agent tokens** (`qd_agent_...`), scoped (`R/W/B/...`).
- Documented separately; do **not** mix agent routes into the human spec without an `x-agent-only` tag.
- See [`docs/agent/AGENT_QUICKSTART.md`](agent/AGENT_QUICKSTART.md).

---

## 2. Response envelopes

### Human API — success

```json
{
  "code": 1,
  "msg": "success",
  "data": { }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `code` | int | **`1` = success** |
| `msg` | string | Human-readable status |
| `data` | any | Payload; may be `null` |

### Human API — error

```json
{
  "code": 0,
  "msg": "Error description",
  "data": null
}
```

HTTP status is often `400`, `401`, `403`, or `500` depending on the route.

OpenAPI schemas: `HumanSuccessEnvelope`, `HumanErrorEnvelope` in `docs/api/openapi.yaml`.

### Agent Gateway — success / error

Agent routes use **`message`** (not `msg`) and **`code: 0` on success**.
Errors may include `details` and `retriable`. See `docs/agent/agent-openapi.json`.

---

## 3. Authentication

| Scheme | Header | Used by |
|--------|--------|---------|
| `HumanJWT` | `Authorization: Bearer <jwt>` | Human Web API |
| `AgentToken` | `Authorization: Bearer qd_agent_...` | `/api/agent/v1/*` only |

Obtain JWT via `POST /api/auth/login` (documented in a future migration phase).

---

## 4. Visibility tiers

Tag or extension every operation when migrating to flask-smorest:

| Tier | OpenAPI | Who may rely on it |
|------|---------|-------------------|
| **Public** | default tag, no extension | Open-source community, third-party clients |
| **Internal** | `x-visibility: internal` | QuantDinger product; may change without notice |
| **Private** | `x-visibility: private` | Admin / sensitive; minimal public docs |

**Public modules (migration priority):** `community`, `market`, `indicator`, `backtest`, `global-market`, `health`.

**Internal / sensitive:** `strategy`, `credentials`, `billing`, `quick-trade`, broker adapters (`ibkr`, `alpaca`, `mt5`).

---

## 5. Naming and versioning

- Paths: lowercase, kebab-case segments (`/api/global-market/...`).
- Query params: snake_case (`page_size`, `sort_by`).
- JSON bodies: snake_case (match existing backend).
- Document **breaking** changes in PR descriptions; CI runs `oasdiff` against committed `docs/api/openapi.yaml`.
- Future human API versioning: prefer `/api/v2/...` over silent breaking changes.

---

## 6. Pagination (lists)

Common shape inside `data`:

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 12
}
```

OpenAPI schema: `PaginationMeta` (fields may be inlined per route during migration).

---

## 7. Contributing API changes

1. **New Public route** — implement with flask-smorest in `app/openapi/routes/` (or migrate existing Blueprint).
2. **Regenerate spec** — `cd backend_api_python && python scripts/export_openapi.py`
3. **Commit** — include updated `docs/api/openapi.yaml` in the same PR.
4. **Agent routes** — update `docs/agent/agent-openapi.json` separately.
5. **PR checklist** — see [CONTRIBUTING.md](../CONTRIBUTING.md#api-documentation).

Local interactive docs (debug mode): `/api/docs/swagger` and `/api/docs/redoc` when `OPENAPI_ENABLED=true` or `PYTHON_API_DEBUG=true`.

---

## 8. Migration status

| Module | Status | Spec source |
|--------|--------|-------------|
| Health (`/`, `/health`, `/api/health`) | **Migrated** | flask-smorest |
| Agent Gateway | Hand-written OpenAPI + CI lint | `docs/agent/agent-openapi.json` |
| All other modules | Legacy Flask Blueprint | Not yet in `openapi.yaml` |

Phase 1+ will migrate `community`, `market`, etc. incrementally.
