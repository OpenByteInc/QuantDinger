# QuantDinger installation and deployment overview

Use this page to choose the correct installation path before following a platform guide. The QuantDinger server, web surfaces, and mobile clients are separate layers: Windows, Linux, or a cloud host runs the service; H5, Android, and iOS connect to that service.

## Choose your goal

| Goal | Recommended path | Guide |
| --- | --- | --- |
| Personal use on Windows | Docker Desktop installer | [Windows installation](../deployment/WINDOWS_INSTALLATION.md) |
| Personal use on Linux or macOS | Docker installer | [Linux and macOS installation](../deployment/LINUX_MACOS_INSTALLATION.md) |
| Public cloud server | Ubuntu/Debian, Docker, and HTTPS | [Cloud deployment](../deployment/CLOUD_DEPLOYMENT_EN.md) |
| Multi-user SaaS | Production deployment, registration, email, billing, and monitoring | [SaaS deployment](../deployment/SAAS_DEPLOYMENT.md) |
| Mobile access or app packaging | H5, Android, or iOS client | [Mobile client deployment](../deployment/MOBILE_CLIENT_DEPLOYMENT.md) |
| AWS Marketplace | Marketplace image | [AWS Marketplace](../deployment/AWS_MARKETPLACE_DEPLOYMENT.md) |

## Recommended installation

Most users should run the prebuilt GHCR images. The installer downloads the Compose definition, creates required secrets and the administrator, and starts the stack without compiling Python or Vue locally.

Linux or macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

The default directories are `~/quantdinger` and `%USERPROFILE%\quantdinger`. The installer is safe to rerun for upgrades: it preserves existing `.env` values and data volumes.

## Addresses after installation

| Surface | Default local address | Purpose |
| --- | --- | --- |
| Desktop web | `http://127.0.0.1:8888` | Administration, research, backtesting, and trading |
| Mobile H5 | `http://127.0.0.1:8889` | Mobile-browser interface |
| Backend health | `http://127.0.0.1:5000/api/health` | Backend readiness |
| OpenAPI | `http://127.0.0.1:5000/openapi.json` | Machine-readable API contract |

If a page does not open, do not delete containers or volumes. Run `docker compose ps`, then follow [installation troubleshooting](../deployment/INSTALL_TROUBLESHOOTING.md).

## What to do next

1. Follow [post-installation setup](POST_INSTALL_SETUP.md) for passwords, URLs, registration, and providers.
2. Add a domain, HTTPS, backups, and monitoring before public exposure.
3. Complete the [first-run guide](FIRST_RUN.md), beginning with research, backtesting, and paper trading.
4. Complete the [live-trading safety checklist](../trading/LIVE_TRADING_SAFETY.md) before using real capital.

## When to build from source

Source deployment is for contributors who need to change the backend or build local images. Normal users and production hosts should prefer pinned GHCR releases to reduce build-time variation. See [cloud deployment](../deployment/CLOUD_DEPLOYMENT_EN.md#optional-full-repository-deployment).

## Security boundaries

- Never expose `5000`, `5432`, `6379`, or `29092` directly to the internet.
- Never commit `.env`, broker credentials, model keys, or payment secrets.
- Never delete PostgreSQL volumes as an installation shortcut; back up and verify recovery first.
- A mobile app is a client, not a standalone backend. It must reach a QuantDinger server.
