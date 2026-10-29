# Install QuantDinger on Windows

This guide targets personal use and LAN testing on Windows 10/11. Use a Linux cloud host for a public production service.

## 1. Prerequisites

- 64-bit Windows 10 22H2 or Windows 11.
- Hardware virtualization enabled in BIOS/UEFI.
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with the WSL 2 backend and Compose v2.
- At least 8 GB RAM and 20 GB free disk; 16 GB RAM is recommended for concurrent AI and backtest work.

Verify in PowerShell:

```powershell
docker version
docker compose version
docker info
```

If `docker info` fails, Docker Desktop is usually not running; that is not a QuantDinger application failure.

## 2. Install

Open PowerShell as your normal user:

```powershell
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

The installer asks for the administrator, email, password, and ports. The default directory is `%USERPROFILE%\quantdinger`. To choose another location:

```powershell
$env:QUANTDINGER_INSTALL_DIR = "D:\QuantDinger"
irm https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.ps1 | iex
```

Do not casually change the directory of an existing instance. Compose may treat the new directory as a separate project.

## 3. Verify

```powershell
Set-Location "$HOME\quantdinger"
docker compose ps
docker compose logs --tail 100 backend
```

Open `http://127.0.0.1:8888`, `http://127.0.0.1:8889`, and `http://127.0.0.1:5000/api/health`. Core services should be `running` or `healthy`; the first database migration may take several minutes.

## 4. Upgrade

Rerun the installer to synchronize Compose, environment templates, and images. Existing values are preserved and `.env` is backed up when needed. A manual update is:

```powershell
docker compose pull
docker compose up -d
docker compose ps
```

Back up the database first. Never use `docker compose down -v` unless permanent volume deletion is intentional.

## 5. Operate

```powershell
docker compose stop
docker compose start
docker compose restart backend
docker compose logs -f --tail 200 backend
```

## 6. LAN access

Ports bind to `127.0.0.1` by default. LAN access requires an intentional `.env` bind change and a Windows Firewall rule. Never expose PostgreSQL, Redis, Kafka, or backend debug ports to the public internet. For a public service, follow [cloud deployment](CLOUD_DEPLOYMENT_EN.md).

## 7. Common Windows issues

- Missing WSL 2: run `wsl --install`, restart, and then start Docker Desktop.
- Disabled virtualization: check Task Manager, Performance, CPU, Virtualization.
- Port collision: use `Get-NetTCPConnection -LocalPort 8888` or choose another installer port.
- Insufficient Docker memory: increase Docker Desktop/WSL resources instead of repeatedly restarting containers.
- Download failure: inspect proxy, TLS, and GitHub Raw access; never copy an installer from an untrusted mirror.

Continue with [installation troubleshooting](INSTALL_TROUBLESHOOTING.md) or [post-installation setup](../getting-started/POST_INSTALL_SETUP.md).

