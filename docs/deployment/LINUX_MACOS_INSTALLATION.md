# Install QuantDinger on Linux and macOS

This guide covers Linux workstations, single Linux servers, and local macOS evaluation. Public production systems must also configure a domain, HTTPS, backups, and monitoring.

## 1. Prerequisites

- Ubuntu 22.04/24.04 or Debian 12 is recommended; use a Docker Desktop-supported macOS release.
- Docker Engine or Docker Desktop with `docker compose version` available.
- Minimum 4 CPU cores, 8 GB RAM, and 20 GB free disk; production should start around 8 cores, 16 GB RAM, and 50 GB disk.
- `curl` and working GitHub/GHCR access.

Install Docker on Linux:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
newgrp docker
docker info
docker compose version
```

On macOS, install Docker Desktop and wait for the engine to become ready.

## 2. Install

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
```

Custom directory:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash -s -- /opt/quantdinger
```

Ensure the current user can write to `/opt/quantdinger`. Do not run the entire service as root merely to bypass permissions.

## 3. Verify

```bash
cd ~/quantdinger
docker compose ps
docker compose logs --tail 100 backend
curl -fsS http://127.0.0.1:5000/api/health
```

Open `http://127.0.0.1:8888`. On a remote host, use an SSH tunnel for temporary verification instead of exposing port 8888:

```bash
ssh -L 8888:127.0.0.1:8888 user@server
```

## 4. Public deployment

Complete the domain, Nginx, HTTPS, firewall, and pinned-image steps in [cloud deployment](CLOUD_DEPLOYMENT_EN.md). Only `22`, `80`, and `443` should be public; internal services stay on loopback or the Docker network.

## 5. Upgrade and operate

Rerunning the installer preserves current values and synchronizes new environment fields. Back up the database before upgrades:

```bash
cd ~/quantdinger
docker compose pull
docker compose up -d
docker compose ps
docker compose logs -f --tail 200 backend
```

Use `docker compose stop` to stop while preserving data. Never use `docker compose down -v` unless permanent volume deletion is intentional.

## 6. macOS notes

- Docker Desktop must remain running.
- Apple Silicon uses multi-architecture images. Diagnose the specific incompatible image before forcing `linux/amd64` globally.
- macOS can package the iOS client, but the server should still run through Docker. See [mobile client deployment](MOBILE_CLIENT_DEPLOYMENT.md).

Continue with [post-installation setup](../getting-started/POST_INSTALL_SETUP.md) or [installation troubleshooting](INSTALL_TROUBLESHOOTING.md).

