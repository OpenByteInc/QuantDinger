# QuantDinger installation and deployment FAQ

This page answers common installation, upgrade, and post-deployment questions. See [installation troubleshooting](INSTALL_TROUBLESHOOTING.md) for database-password, migration, and image-pull recovery.

## What is required?

Docker with Compose v2 is required. Personal systems should have at least 4 cores and 8 GB RAM; concurrent AI, backtest, and trading workers benefit from 8 cores and 16 GB. Public hosts also need a domain, HTTPS, firewall, and backup capacity.

## Docker is installed, but the installer fails

Run `docker version`, `docker info`, and `docker compose version`. A failed `docker info` means the daemon is stopped or the user lacks access. A failed `docker compose` means Compose v2 is missing. On Windows, verify Docker Desktop and WSL 2.

## The page does not open

Run:

```bash
docker compose ps
docker compose logs --tail 200 backend
docker compose logs --tail 100 frontend
```

Check port collisions and the backend health endpoint. A visible frontend with failed login/API calls usually indicates the backend, reverse proxy, or `FRONTEND_URL`.

## What are the default credentials?

There is no universal default password. The installer creates an administrator. Use password recovery or administrator troubleshooting; never copy purported default credentials or edit password hashes directly.

## Where is data stored?

PostgreSQL, Redis, and Kafka use Docker named volumes; configuration is in the installation `.env`. Removing containers normally keeps volumes, but `docker compose down -v` deletes them. Back up both database data and required configuration before moving hosts.

## How do I upgrade?

Rerun the installer or run `docker compose pull`, `docker compose up -d`, and `docker compose ps`. Pin `IMAGE_TAG` in production, back up first, read release notes, and verify health, migrations, workers, and critical flows afterward.

## Should I reinstall to fix a problem?

Diagnose logs and health first. Rerunning the installer safely synchronizes configuration, but deleting the directory, `.env`, or volumes can rotate secrets and permanently lose data.

## Can I uninstall but keep data?

Use `docker compose stop` or `docker compose down`, and retain the installation directory and named volumes. Use `down -v` only after a verified backup and an explicit decision to erase data.

## Can Windows build iOS?

Windows can host the server and build Android. iOS signing and archives require macOS, Xcode, and an Apple Developer account. See [mobile client deployment](MOBILE_CLIENT_DEPLOYMENT.md).

## Why can a phone not reach computer localhost?

The phone's `localhost` is the phone itself. Use a LAN-reachable address or HTTPS domain and check the firewall, certificate, and API URL. Production apps should not depend on plaintext HTTP.

## Which cloud ports should be public?

Normally only SSH `22`, HTTP `80`, and HTTPS `443`. Keep `5000`, `5432`, `6379`, and `29092` on loopback or private networks.

## Why did an `.env` change not apply?

Environment is read when a container is created. Run `docker compose up -d --force-recreate <service-name>` and confirm you edited the active Compose project directory.

## OAuth returns to the wrong URL

Align the first `FRONTEND_URL`, provider callback, reverse-proxy protocol headers, and HTTPS origin exactly. Include both desktop and mobile origins when both are supported.

## Is SaaS just registration and billing?

No. It also requires tenant isolation, verified email, idempotent billing webhooks, recovery, alerts, auditing, Terms, and Privacy. Follow the [SaaS deployment guide](SAAS_DEPLOYMENT.md).

## Where next?

Use [installation troubleshooting](INSTALL_TROUBLESHOOTING.md), [administrator troubleshooting](ADMIN_AND_SETTINGS_TROUBLESHOOTING_EN.md), [cloud deployment](CLOUD_DEPLOYMENT_EN.md), the notification guides, or the [live-trading safety checklist](../trading/LIVE_TRADING_SAFETY.md).

