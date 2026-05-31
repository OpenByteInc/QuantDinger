# QuantDinger Web API (OpenAPI)

Human-facing REST API specification generated from **flask-smorest** (`app/openapi/`).

| File | Purpose |
|------|---------|
| [`openapi.yaml`](openapi.yaml) | Committed SSOT — update via `backend_api_python/scripts/export_openapi.py` |
| [`index.html`](index.html) | Static Redoc viewer — **must be served over HTTP** (see below) |

## View docs locally

ReDoc cannot load `openapi.yaml` when you double-click `index.html` (`file://` is blocked by browser same-origin policy). Use any static server from the repo root or this folder:

```bash
# Python (from docs/api/)
python -m http.server 8080
# open http://localhost:8080/index.html

# or from repo root
cd docs/api && npx --yes serve -p 8080
```

If you see `process is not defined`, refresh after pulling the latest `index.html` (includes a browser shim + pinned ReDoc 2.4.0).

## Conventions

See [`../API_CONVENTIONS.md`](../API_CONVENTIONS.md) for envelopes, auth, and Public/Internal tiers.

## Agent API

AI agents use a separate contract: [`../agent/agent-openapi.json`](../agent/agent-openapi.json).

## Regenerate

```bash
cd backend_api_python
pip install -r requirements.txt
python scripts/export_openapi.py
```

## Local interactive docs

With the backend running in debug mode (or `OPENAPI_ENABLED=true`):

- Swagger UI: http://localhost:5000/api/docs/swagger
- ReDoc: http://localhost:5000/api/docs/redoc
