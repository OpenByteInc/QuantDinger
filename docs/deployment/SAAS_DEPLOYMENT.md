# QuantDinger SaaS deployment guide

SaaS is not completed by one environment variable. It adds public registration, domains and email, billing, authorization, auditing, backup, and continuous monitoring to a production deployment. This guide defines the sequence and acceptance boundary.

## 1. Scope

Use this mode when one operator hosts QuantDinger for multiple users. Personal and internal-team deployments should follow the [installation overview](../getting-started/INSTALLATION.md) without enabling public registration or billing unnecessarily.

## 2. Build the production foundation

1. Follow [cloud deployment](CLOUD_DEPLOYMENT_EN.md) with a pinned image release.
2. Configure domains, Nginx, HTTPS, and a firewall exposing only `80/443`.
3. Complete [production hardening](PRODUCTION_HARDENING.md) and [observability](OBSERVABILITY.md).
4. Prefer managed PostgreSQL, Redis, and Kafka at production scale; follow [distributed runtime deployment](DISTRIBUTED_RUNTIME_SCALING.md) before adding hosts.

## 3. Core environment

Review at least:

```ini
QUANTDINGER_DEPLOYMENT_MODE=saas
FRONTEND_URL=https://app.example.com,https://m.example.com
ENABLE_REGISTRATION=true
ALLOW_LOCAL_DESKTOP_BROKERS=false
BILLING_ENABLED=true
```

Use stable, unique `SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, and database credentials. Do not regenerate them after launch: sessions, credential decryption, or database connectivity can break.

## 4. Registration, email, and identity

- Decide between public and invite-only registration.
- Configure SMTP and test registration, password recovery, alerts, and billing email.
- Configure OAuth callback URLs with the [OAuth guide](OAUTH_CONFIG_EN.md).
- Separate administrators and regular users; see [multi-user operation](MULTI_USER_SETUP.md).
- Publish real Terms and Privacy URLs instead of examples.

## 5. Billing

Before enabling billing, configure the Stripe merchant, products/prices, webhook, redirect URLs, and production keys. Webhooks require HTTPS and signature verification. Resolve tax, refund, dispute, and jurisdiction obligations before accepting payments.

Test subscription creation, renewal, cancellation, failed payment, webhook replay idempotency, plan entitlements, and refund reconciliation.

## 6. Tenant and trading isolation

- Validate user and tenant ownership on the server for every request.
- Grant minimum scopes to APIs, Agent Tokens, and broker credentials.
- Encrypt broker secrets server-side and keep plaintext out of logs.
- Audit support actions; never ask for a user's password.
- Start with research, backtests, and paper trading. Gate live trading separately by policy and jurisdiction.

## 7. Pre-launch acceptance

Verify identity lifecycle, cross-tenant isolation, billing idempotency, migrations and workers, TLS and secret rotation, automated backups with a restore drill, alert ownership, rollback, and incident communication.

## 8. Rollout

Progress through internal administrators, invited users, limited paid users, and finally public registration. At each stage inspect authentication failures, API latency, queue lag, database connections, payment failures, and trading incidents.

Continue with [post-installation setup](../getting-started/POST_INSTALL_SETUP.md) and the [installation FAQ](INSTALLATION_FAQ.md).

