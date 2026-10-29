# Documentation maintenance standard

QuantDinger's `docs/` directory is the product-documentation source of truth, and the website documentation is generated from it. Documentation is part of feature acceptance, not an after-release accessory.

## Information architecture

| Directory | Audience | Content |
| --- | --- | --- |
| `getting-started/` | New users | Installation, first use, 30-minute quickstart |
| `product/` | Research and strategy users | Product surfaces and end-to-end workflows |
| `deployment/` | Administrators and operators | Configuration, deployment, upgrade, recovery, troubleshooting |
| `trading/` | Strategy developers and traders | Strategy API, brokers, and live safety |
| `api/`, `agent/` | Developers and agents | HTTP, OpenAPI, MCP, and Agent Gateway |
| `security/`, `compliance/` | Security and assurance teams | Reporting, supply chain, and compliance evidence |
| `architecture/` | Contributors | Boundaries, processes, and extension rules |

## Documentation definition of done

A PR that changes user behavior, APIs, configuration, data, or operations must also:

1. Update the relevant guide and its maintained Chinese or English counterpart.
2. Update navigation, cross-links, configuration reference, and compatibility notes.
3. Document new environment variables: default, risk, restart requirement, and secret status.
4. Provide upgrade, rollback, and migration steps for a breaking change.
5. Update screenshots for changed UI; redact them, crop to the task, and identify the applicable version.
6. Run the website generator, internal-link validation, and language checks.

## Page standard

- Name the task in the title and state audience and prerequisites at the start.
- Present the recommended path before alternatives; commands must be copyable.
- Include expected outcomes and the next diagnostic step on failure.
- Warn explicitly about deletion, live execution, permissions, secrets, and irreversible actions.
- Do not publish temporary audits, roadmaps, or internal discussion as product tutorials.
- Do not promise unverified external-provider capability.

## Screenshot standard

- Use the current stable UI and show task context rather than decorative imagery.
- Hide API keys, tokens, cookies, emails, account and order numbers, and balances.
- Keep only key checkpoints; the written steps must remain usable without images.
- Use lowercase English filenames with hyphens and descriptive alternative text.
- Update or remove an image when UI changes make it inaccurate.

## Version review

Review installation, configuration, upgrade, restore, and live-safety pages at least once per stable release. Review provider, store-release, and OAuth pages when their external rules change. Mark applicability or remove navigation to an unmaintained page.

The release owner should verify that a new user can find installation, produce a first result after setup, and that an administrator can back up, upgrade, restore, and reach an actionable diagnostic page. Maintained Chinese and English links should remain paired.
