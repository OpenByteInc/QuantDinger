# Security Policy

QuantDinger is a **local-first, self-hosted quantitative trading system**.

This policy defines the supported versions, security scope, vulnerability
reporting process, remediation targets, and public acknowledgments for the
project.

---

## Supported Versions

QuantDinger is under active development.

At this stage:
- The **`main` branch** is the only supported version for security updates.
- Older commits, forks, or modified deployments are **not actively supported**.

Users are strongly encouraged to stay up to date with the latest release
or commit when running QuantDinger in production environments.

---

## Security Model and Scope

QuantDinger is designed to run **entirely under the user’s control**.

### In Scope
We consider the following areas in scope for security review:

- Source code vulnerabilities in this repository
- Authentication and authorization logic within QuantDinger
- Handling of API keys, secrets, and credentials by the application
- Strategy execution logic and isolation boundaries
- Default configuration security issues

### Untrusted Code Boundary

QuantDinger treats uploaded indicator and Strategy API V2 source as untrusted.
Static source validation is a policy layer, not the sole security boundary.
Supported deployments execute indicator code, strategy discovery, backtests,
and live strategy callbacks in child processes that:

- start with a clean environment and no inherited file descriptors;
- receive only explicitly serialized market data, parameters, state, and
  bounded runtime context;
- exchange data with the trusted host through a typed JSON protocol;
- have no database handle, exchange or broker credential, application secret,
  or order-submission client;
- apply timeout, memory, file-size, and output-size limits; and
- on Linux, activate fail-closed seccomp and Landlock restrictions before
  untrusted handlers run.

Trusted API, evaluator, backtest, and trading workers retain authentication,
durable-state, reconciliation, credential, and exchange-order authority. An
order intent returned by a strategy sandbox is not authorization to trade; it
must pass the normal ownership, account, risk, lease, and fencing checks.
QuantDinger does not fall back to in-process untrusted-code execution when the
kernel boundary cannot be activated.

### Out of Scope
The following are outside the scope of this security policy:

- Misconfigured user environments (OS, Docker, firewall, cloud host)
- Compromised user machines or infrastructure
- Third-party services, exchanges, or APIs
- Modified or unofficial builds of QuantDinger

---

## Reporting a Vulnerability

Submit suspected security vulnerabilities through the private reporting
channel below.

### How to Report

Please **do not open a public GitHub issue** for security vulnerabilities.

Instead, report privately via email:

- **Email**: `support@quantdinger.com`
- **Subject**: `[Security] Brief description of the issue`

Please include:
- a clear description of the vulnerability
- steps to reproduce (if applicable)
- potential impact
- any suggested mitigations (optional)

---

## Response Expectations

We aim to:
- acknowledge reports within **72 hours**
- provide a preliminary assessment within **7 days**
- provide a remediation or mitigation target within **14 days**

Confirmed vulnerabilities are prioritized using impact, exploitability, and
exposure. Our normal remediation targets are:

- critical: mitigation or release within 7 days
- high: mitigation or release within 30 days
- medium: mitigation or release within 60 days
- low: scheduled according to risk and maintenance capacity

Timelines may vary depending on the complexity and severity of the issue.

If a report is accepted, we will coordinate a fix and, when appropriate,
a responsible public disclosure.

## Coordinated Vulnerability Disclosure

We ask reporters to keep vulnerability details private until a fix or agreed
mitigation is available. QuantDinger will maintain communication with the
reporter, prepare upgrade guidance, and publish an advisory or a security entry
in this document when disclosure is safe. Publicly known exploitable
vulnerabilities of medium or higher severity should not remain unpatched for
more than 60 days.

Confirmed vulnerabilities are disclosed through GitHub Security Advisories,
release notes, or the Security Advisories section below. Credit is provided
unless the reporter requests anonymity.

---

## Responsible Disclosure

We ask security researchers to:
- avoid exploiting vulnerabilities beyond proof of concept
- allow reasonable time for remediation before public disclosure
- act in good faith and with respect for users

We are happy to acknowledge responsible disclosures
in release notes or documentation, if desired.

---

## Security Advisories

### July 2026 — JWT authentication and authorization bypass (resolved)

A critical vulnerability affected deployments that used the previously
published default `SECRET_KEY`. Under those conditions, an unauthenticated
attacker could forge an HS256 access token. The former validation path also
allowed a token without a `token_version` claim to bypass session-version
checks and used the role carried in the token for authorization decisions.

The issue was resolved on **July 21, 2026**. QuantDinger now rejects missing,
known-default, and fewer-than-10-byte signing secrets (32 or more random bytes
remain strongly recommended); requires the JWT identity and session claims;
and validates the current user status, token version, and role against the
authoritative database record before granting access.

Administrators of deployments created before this fix should update to the
latest supported revision, replace `SECRET_KEY` with a unique high-entropy
value, and require users to sign in again. If an affected API was reachable by
untrusted networks, review access logs and rotate any broker, exchange, or
provider credentials that may have been exposed.

### August 2026 — Strategy and indicator execution isolation

Deployments that accepted untrusted strategy or indicator code before the
August 2026 hardening update should assume application environment secrets may
have been exposed. Upgrade to the latest `main` revision, review access logs,
and rotate JWT/session secrets, database credentials, provider tokens, and
connected broker or exchange credentials. Preserve access to credentials
encrypted with the previous `CREDENTIAL_ENCRYPTION_KEY` until they have been
re-encrypted or re-entered; changing that key without a migration makes stored
credentials unreadable.

The current supported isolation model also separates manifest discovery,
indicator execution, backtest callbacks, and live callbacks from trusted
application processes. Operators upgrading from an older build must recreate
the backend and worker containers so the new child-process boundary is active;
restarting only the web frontend is insufficient.

### September 2026 — Broker and strategy execution hardening (resolved)

Two authenticated security issues involving broker endpoint validation and
strategy compilation resource controls were resolved on **September 29,
2026**. The update restricts broker connections to approved endpoints and
enforces process isolation and resource limits during strategy discovery.

Operators should upgrade to the latest supported revision. Deployments that
allowed untrusted users to access broker configuration before the update
should review relevant logs and rotate affected broker credentials as a
precaution. Additional technical details are intentionally limited here to
support responsible disclosure.

## Security Acknowledgments

The following researchers reported security issues through the coordinated
disclosure process. Descriptions are intentionally limited to information that
is appropriate for public release.

| Researcher | Area | Disclosure date |
| --- | --- | --- |
| Risma Ajul | JWT authentication and authorization | July 2026 |
| Satrio | Strategy and indicator execution isolation | August 2026 |
| Jo Arsy | Proxy-header trust and IP-based controls | September 2026 |
| Dan Aridor and the [SPR{K3](https://sprk3.com/) security research team | Broker endpoint validation and strategy compilation resource controls | September 2026 |
| Nikhil Agrawal, Mettle Labs | Authorization and tenant-isolation controls | October 2026 |
| [Varkey Denny](https://github.com/mynamevarkey) | Strategy-code sandbox validation | November 2026 |
| goranai | Credential redaction in agent-facing interfaces | November 2026 |
| [Liam Giannini](https://github.com/Liam-G), [webcracy.ch](https://webcracy.ch) | Role authorization for trading operations; credited as finder | November 2026 |
| Aleff | Strategy and indicator sandbox process and file-access isolation | November 2026 |

---

## Disclaimer

QuantDinger is provided **as-is**, without warranty.

As a self-hosted system, users are responsible for:
- securing their own environments
- protecting API keys and credentials
- complying with applicable laws and regulations

---
