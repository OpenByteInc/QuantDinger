# Contributing to QuantDinger

Thanks for your interest in contributing to **QuantDinger**.

QuantDinger is a **local-first, private AI-driven quantitative trading workspace**.
It is built for people who care about:
- data sovereignty
- local execution
- transparent systems
- engineering over hype

This document explains **how to contribute** and **what contribution means here**.

---

## ⚠️ Please Read First

QuantDinger is **not a DAO**.
There is **no token**, **no airdrop**, and **no financial incentive** at this stage.

If you are looking for short-term rewards, promotions, or token speculation,
this project is probably **not a good fit**.

If you are interested in:
- building credible infrastructure
- publishing real work under your name
- shaping an early-stage system with long-term value

you are very welcome here.

---

## 🧭 What Contribution Means

Contributing to QuantDinger means creating **public, verifiable work**.

Your contributions:
- are attributed publicly on GitHub
- can be referenced in your resume or portfolio
- remain valuable to you beyond this project

This is a **builder-first environment**.

---

## 🧑‍💻 Ways to Contribute

### 1) Core Engineering
- Python strategy engine
- execution logic
- AI / LLM agent workflows
- backtesting and data pipelines

Small, focused improvements are preferred.

---

### 2) Strategy & Research
- example strategies
- research notebooks
- execution experiments
- performance analysis

This is a good place to demonstrate how you think.

---

### 3) Documentation & Explanation
- tutorials and setup guides
- architecture explanations
- design rationale

Clear explanations matter as much as good code.

---

### 4) Content & Advocacy
- technical blog posts
- demo videos
- system breakdowns
- honest reviews or critiques

You are not “marketing”.
You are explaining something real.

---

## 🔗 Communication Channels

- **Issues**: bug reports and feature requests
- **Discussions**: questions, ideas, and design conversations
- **Community**: official links are listed in `README.md`

If you plan a large change, please open a discussion first.

---

## 🗺️ Roadmap Contributions

Public roadmap work is indexed in [ROADMAP.md](ROADMAP.md). Roadmap epics describe
product direction and must be split into focused tasks before implementation.

Only work marked **Ready** and **help wanted** is available for direct
implementation. Items in **Proposed** or **RFC** need product or architecture
agreement first.

To claim a roadmap task:

1. Comment on the issue with the exact task you want to own.
2. Include a short plan, the modules you expect to change, and how you will test
   the result.
3. Wait for a maintainer to confirm the scope and assign the issue.
4. Open a draft pull request or post a progress update within seven days.
5. Link the pull request to the issue and keep the change reviewable.

Claims are coordination tools, not permanent reservations. A maintainer may
release an inactive claim after seven days without a draft pull request or
progress update. If you need more time, leave a short update on the issue.

Do not open a pull request that attempts to implement an entire roadmap epic.
Execution-critical changes, public API changes, database migrations, and new
runtime dependencies require an agreed RFC or scoped issue first.

---

## 🛠️ Development Setup

This repository contains:

- `backend_api_python/`: Flask backend + strategy runtime
- `docker-compose.yml` / `docker-compose.ghcr.yml`: deployment stacks

The web UI source lives in the separate private **QuantDinger-Vue** repo, which publishes `ghcr.io/openbyteinc/quantdinger-frontend` to GHCR on every `v*` tag — both Compose files pull that image directly.

### Backend (Python)

```bash
python scripts/bootstrap_env.py
cd backend_api_python
pip install -r requirements.txt
python run.py
```

`bootstrap_env.py` creates the project-root `.env`, generates missing local
credentials, and preserves existing values. Set `DATABASE_URL` explicitly when
the backend runs directly on the host instead of through Docker Compose.

### Frontend

The SPA lives in the private **QuantDinger-Vue** repo. Tagging a release there (`git tag vX.Y.Z && git push --tags`) triggers `.github/workflows/release-frontend.yml`, which builds a multi-arch image and pushes it to `ghcr.io/openbyteinc/quantdinger-frontend`. No frontend artefacts are committed here — pin the consumed tag via `IMAGE_TAG` (or `FRONTEND_TAG` for a per-side override) in a root-level `.env`.

For local iteration without publishing, clone the Vue repo into `./QuantDinger-Vue/` (gitignored) and run `docker compose -f docker-compose.yml -f docker-compose.build.yml up --build` — see **DEVELOPMENT.md → Building frontend from local source**.

---

## Code Organization

Read [`docs/architecture/ARCHITECTURE.md`](docs/architecture/ARCHITECTURE.md), [`docs/architecture/EXTENSION_GUIDE.md`](docs/architecture/EXTENSION_GUIDE.md), and [`docs/architecture/MODULE_BOUNDARIES.md`](docs/architecture/MODULE_BOUNDARIES.md) before larger backend changes.

- Keep routes thin: validate input, call a service, return JSON.
- Put exchange, broker, market data, and strategy behavior behind small services or adapters.
- Avoid growing legacy hotspot files when a focused sibling module can hold new behavior.
- Keep code comments, docstrings, and log messages in English unless a user-facing translation or external provider field requires another language.
- Prefer simple, readable code over clever abstractions.
- Ask for clarification before changing unclear product behavior.

---

## 🌿 Branching & Pull Requests

### Branch naming

- `fix/xxx` — bug fixes
- `feat/xxx` — new features
- `docs/xxx` — documentation
- `chore/xxx` — maintenance

### Pull request guidelines

Please include:

- what changed and why
- how to test
- screenshots/GIFs for UI changes (if applicable)
- backward compatibility notes (if any)

Keep PRs focused and reviewable.

### Developer Certificate of Origin

Every commit must certify the
[Developer Certificate of Origin 1.1](https://developercertificate.org/) by
including a `Signed-off-by` trailer. Add it automatically with:

```bash
git commit -s
```

The sign-off states that you created the contribution or otherwise have the
legal right to submit it under this project's Apache-2.0 license. Pull requests
with an unsigned commit fail the DCO check.

---

## 🧪 Testing & Verification

Major functionality and security-sensitive fixes must add or update automated
tests. Bug fixes should include a regression test unless the pull request
documents why automated reproduction is not practical.

Run the checks relevant to the changed component and record them in the pull
request. The canonical commands, CI coverage, and release-gate policy are in
[docs/getting-started/TESTING.md](docs/getting-started/TESTING.md).

Python contributions must pass Ruff and follow the existing formatting and
naming conventions. Keep public interfaces backward compatible or document the
approved migration. Never merge a change with a known exploitable medium-or-
higher security finding.

---

## API documentation

QuantDinger uses **OpenAPI 3** as the HTTP contract SSOT.

| Surface | Spec | When to update |
|---------|------|----------------|
| Human Web API | [`docs/api/openapi.yaml`](docs/api/openapi.yaml) | Any flask-smorest route in `app/openapi/` |
| Agent Gateway | [`docs/agent/agent-openapi.json`](docs/agent/agent-openapi.json) | Any `/api/agent/v1` route |

**Regenerate human spec:**

```bash
cd backend_api_python
python scripts/export_openapi.py
```

Read [`docs/architecture/API_CONVENTIONS.md`](docs/architecture/API_CONVENTIONS.md) before adding Public endpoints.
CI (`.github/workflows/openapi-ci.yml`) runs Spectral lint, export diff, and oasdiff breaking checks.

---

## 🔐 Security

Please do not open public issues for security vulnerabilities.

For security reports, contact the maintainer via the email in README.md and include:

- description of the issue
- steps to reproduce
- impact assessment

---

## 📜 License

By contributing, you agree that your contributions will be licensed
under the project's license (see LICENSE) and certify each commit under the
Developer Certificate of Origin as described above.

Dependency additions, vendored code, copied examples, generated assets, and
changes to distribution behavior must also follow the
[open source license compliance procedure](docs/compliance/LICENSE_COMPLIANCE_PROCEDURE.md).
Include the component source, version, license, intended use, and required
notices in the pull request. Do not submit material that you do not have the
right to contribute.

---

## 🧠 A Note on the Future

QuantDinger may explore incentive or alignment mechanisms in the future.
Nothing is promised, scheduled, or guaranteed.

What is guaranteed:

- your work will be visible
- your name will be attached to it
- your contribution will remain yours

Build carefully. Build openly. Build things that last.
