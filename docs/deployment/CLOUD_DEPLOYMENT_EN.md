# QuantDinger Cloud Deployment Guide

This guide describes the current production-style Docker deployment for QuantDinger on a cloud server. It covers the recommended GHCR image deployment, optional source deployment, Nginx, HTTPS, upgrades, and common troubleshooting.

For first-time Docker pull and Postgres startup issues, also see [Installation Troubleshooting](INSTALL_TROUBLESHOOTING.md).

## Recommended Architecture

Use one public domain with a host-level Nginx reverse proxy:

- Public web URL: `https://app.example.com`
- Optional mobile H5 URL: `https://m.example.com`
- Host Nginx listens on `80/443`
- Docker `frontend` binds to `127.0.0.1:8888`
- Docker `mobile` binds to `127.0.0.1:8889`
- Docker `backend` binds to `127.0.0.1:5000`
- Docker `postgres`, both Redis roles, and Kafka stay on the private Docker
  network; any diagnostic host bindings must remain loopback-only

Only expose `80` and `443` to the public internet. Keep `5000`, `5432`,
`6379`, and `29092` private.

## 1. Prepare the Server

Recommended baseline:

- Ubuntu 22.04 / 24.04 or Debian 12
- 4 vCPU / 8 GB RAM minimum for a light single-host installation
- 8 vCPU / 16 GB RAM recommended when strategies, AI jobs, and backtests run together
- 50 GB+ disk space, with additional capacity for PostgreSQL backups and Kafka retention
- Security group or firewall allows `22`, `80`, and `443`
- A domain such as `app.example.com`

Create DNS records:

```text
app.example.com -> your server public IP
m.example.com   -> your server public IP  # optional mobile H5 domain
```

Verify DNS:

```bash
ping app.example.com
```

## 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
docker compose version
```

Use Compose v2 commands: `docker compose ...`.

## 3. Choose a Deployment Mode

### Recommended: GHCR prebuilt images

Use this mode for normal cloud deployment. It pulls backend, web frontend, and mobile H5 images from GHCR. No local Python or Node build is required.

The one-command installer is recommended. It creates the installation
directory, generates random local credentials, asks for the initial
administrator, and starts the services:

```bash
curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash -s -- ~/quantdinger
```

For a manually managed download, fetch the environment bootstrap script as
well:

```bash
mkdir -p ~/quantdinger/scripts
cd ~/quantdinger
curl -O https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/docker-compose.ghcr.yml
curl -O https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/.env.example
curl -o scripts/bootstrap_env.py https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/scripts/bootstrap_env.py
python3 scripts/bootstrap_env.py --compose-file docker-compose.ghcr.yml
```

Edit `.env` before first start:

```ini
ADMIN_USER=your_admin_user
ADMIN_PASSWORD=your_strong_password
FRONTEND_URL=https://app.example.com,https://m.example.com
ALLOW_LOCAL_DESKTOP_BROKERS=false
```

The bootstrap script generates `POSTGRES_PASSWORD`, `SECRET_KEY`, and
`CREDENTIAL_ENCRYPTION_KEY` without printing them. Do not regenerate the
PostgreSQL password of a running installation. Re-running the same command
during an upgrade preserves existing values and can recover the database
password from the current container or a legacy `DATABASE_URL`. The same file
also contains Compose orchestration settings:

```ini
FRONTEND_HOST=127.0.0.1
FRONTEND_PORT=8888
MOBILE_HOST=127.0.0.1
MOBILE_PORT=8889
# The first origin is the default post-login OAuth redirect target.
FRONTEND_URL=https://app.example.com,https://m.example.com
BACKEND_PORT=127.0.0.1:5000
DB_PORT=127.0.0.1:5432
REDIS_BIND=127.0.0.1:6379

# Pin a release instead of floating latest, for example:
# IMAGE_TAG=5.5.1

# Use a Docker Hub mirror for postgres/redis when needed:
# IMAGE_PREFIX=docker.m.daocloud.io/library/
```

Start:

```bash
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml up -d
docker compose -f docker-compose.ghcr.yml ps
```

### BT Panel Docker plugin and port changes

After BT Panel imports a Compose project, its container detail page usually cannot reliably change ports managed by Compose. Do not edit the port mapping on an already-created container. Update the project-root `.env`, then recreate the affected services:

```bash
docker compose -f docker-compose.ghcr.yml up -d --force-recreate frontend mobile backend
```

The `backend` container listening internally on port `5000` is part of the application contract and is expected to remain fixed. `BACKEND_PORT=127.0.0.1:5000` controls only the host binding; frontend containers reach the API through `backend:5000` on the Docker network. To change the user-facing local port, update `FRONTEND_PORT` / `MOBILE_PORT` and recreate the containers.

In production, never publish `5000`, `5432`, `6379`, or `29092` directly to
the internet. Expose only `80/443` through BT Panel or 1Panel Nginx/OpenResty:
send page requests to the host-local Web frontend (default
`127.0.0.1:8888`) and send `/api/` directly to the backend at
`127.0.0.1:5000`. This remains same-origin in the browser while preventing a
second proxy hop in the frontend container from replacing the real client IP.

### Optional: full repository deployment

Use this mode only when you need to build the backend from local source.

```bash
git clone https://github.com/OpenByteInc/QuantDinger.git
cd QuantDinger
python3 scripts/bootstrap_env.py
```

Edit `.env`:

```ini
ADMIN_USER=your_admin_user
ADMIN_PASSWORD=your_strong_password
FRONTEND_URL=https://app.example.com,https://m.example.com
ALLOW_LOCAL_DESKTOP_BROKERS=false
```

Start:

```bash
docker compose pull
docker compose up -d --build
docker compose ps
```

## 4. Unified Environment File and Updates

All deployment and application settings use one project-root file:

| File | Used by | Purpose |
|------|---------|---------|
| `.env` | Docker Compose and every backend process | Runtime settings, credentials, ports, image tags, database, Redis, Kafka, and worker settings |

After pulling an update, run `python3 scripts/bootstrap_env.py` before any
Compose command (add `--compose-file docker-compose.ghcr.yml` for a manually
managed GHCR deployment). After bootstrap, Compose runs the one-shot `env-sync`
service before database and Kafka initialization. It appends new fields and
imports values that exist only in legacy backend environment files without
replacing current values or comments. A timestamped backup is created only when
`.env` changes. The manual command remains available for preview and maintenance:

```bash
python scripts/sync_env.py --env-file .env --template .env.example --backup
```

The one-command installer performs the same merge whenever it is rerun. For a
manual one-time old deployment migration, pass `--legacy backend.env` or
`--legacy backend_api_python/.env`; legacy files remain untouched.

You may also rerun `python3 scripts/bootstrap_env.py` before an upgrade. It
keeps `POSTGRES_PASSWORD` from `.env`, recovers it from a legacy `DATABASE_URL`,
or reads it from the existing `quantdinger-db` container, in that order. If a
PostgreSQL data volume exists but the credential cannot be recovered, the
command stops instead of generating a new password and disconnecting the
existing database. Restore the original value from an `.env` backup; do not
delete the volume.

## 5. Event runtime and replica settings

The default stack now starts Kafka, a topic initializer, the Kafka audit
consumer, strategy dispatcher workers, strategy evaluator workers, and trading
workers. Kafka is internal infrastructure; keep port `29092` bound to loopback
or a private network and never expose it directly to the Internet.

Single-host replica counts belong in the project-root `.env`:

```ini
TRADING_WORKER_REPLICAS=1
STRATEGY_DISPATCHER_REPLICAS=1
STRATEGY_EVALUATOR_REPLICAS=1
```

The first-install default is one replica per role. This keeps a low-load
single-host deployment predictable and reduces memory, database connections,
and operational complexity. A single host can still run multiple replicas;
increase them gradually after measuring CPU, Kafka lag, and strategy latency.

After changing them, recreate only those roles:

```bash
docker compose -f docker-compose.ghcr.yml up -d --force-recreate \
  trading-worker strategy-dispatcher-worker strategy-evaluator-worker
```

The default Compose file is not a multi-host cluster definition. Before adding
application servers, move PostgreSQL, both Redis roles, and Kafka to shared
external services and use an orchestrator or an equivalent deployment layer.
See [distributed runtime deployment and scaling](DISTRIBUTED_RUNTIME_SCALING.md)
for the release sequence, ownership model, rollback boundary, and 100,000-
strategy prerequisites.

## 6. Configure Nginx

Install Nginx:

```bash
sudo apt update
sudo apt install -y nginx
```

Create `/etc/nginx/sites-available/quantdinger.conf`:

Split page and API traffic at the host proxy while keeping the same public origin: send `/api/` directly to the backend bound on localhost port `5000`, and send all other page requests to Web port `8888` or mobile H5 port `8889`. Do not send all traffic through a frontend container and then proxy the API a second time. The current backend validates the forwarding peer, and a second proxy can replace `X-Real-IP` with a Docker gateway address.

```nginx
server {
    listen 80;
    server_name app.example.com;

    client_max_body_size 20m;

    location = /api/ai/chat/message/stream {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_request_buffering off;
        proxy_connect_timeout 75s;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        send_timeout 600s;
        add_header X-Accel-Buffering "no" always;
        add_header Cache-Control "no-cache, no-transform" always;
    }

    location ^~ /api/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_connect_timeout 75s;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8888;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name m.example.com;

    client_max_body_size 20m;

    location = /api/ai/chat/message/stream {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_request_buffering off;
        proxy_connect_timeout 75s;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        send_timeout 600s;
        add_header X-Accel-Buffering "no" always;
        add_header Cache-Control "no-cache, no-transform" always;
    }

    location ^~ /api/ {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_connect_timeout 75s;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8889;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

If you do not need a separate mobile domain, omit the second server block. Users can still access the mobile H5 service through the bound port or a path/domain you configure yourself. Port `5000` remains bound to `127.0.0.1` for access by Nginx/OpenResty on the same host; this does not expose the backend port directly to the internet.

Enable:

```bash
sudo ln -s /etc/nginx/sites-available/quantdinger.conf /etc/nginx/sites-enabled/quantdinger.conf
sudo nginx -t
sudo systemctl reload nginx
```

If using UFW:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

## 7. Enable HTTPS

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d app.example.com -d m.example.com
sudo certbot renew --dry-run
```

If you only configured `app.example.com`, request only that domain.

Open:

```text
https://app.example.com
https://m.example.com
```

## 8. Optional API Subdomain

The recommended setup stays same-origin while splitting traffic at the host reverse proxy:

```text
Browser -> https://app.example.com -> host Nginx -> /      -> frontend:8888
                                                 -> /api/ -> backend:5000
```

If you need a separate `api.example.com`, expose only the host-local backend through Nginx:

```nginx
server {
    listen 80;
    server_name api.example.com;

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Also set `FRONTEND_URL` in the backend runtime env to include every public frontend origin.

## 9. Operations

For GHCR deployment:

```bash
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs -f backend
docker compose -f docker-compose.ghcr.yml logs -f postgres
docker compose -f docker-compose.ghcr.yml logs -f kafka strategy-dispatcher-worker strategy-evaluator-worker trading-worker
docker compose -f docker-compose.ghcr.yml restart backend
```

Update GHCR images:

```bash
python3 scripts/bootstrap_env.py --compose-file docker-compose.ghcr.yml
docker compose -f docker-compose.ghcr.yml pull
docker compose -f docker-compose.ghcr.yml run --rm migration
docker compose -f docker-compose.ghcr.yml run --rm kafka-init
docker compose -f docker-compose.ghcr.yml up -d --remove-orphans
```

For full repository deployment:

```bash
git pull --ff-only
python3 scripts/bootstrap_env.py
docker compose build backend
docker compose run --rm migration
docker compose run --rm kafka-init
docker compose up -d --remove-orphans
```

Back up Postgres before major upgrades:

```bash
docker exec quantdinger-db pg_dump -U quantdinger quantdinger > quantdinger_backup.sql
```

## 10. Postgres 18 and Existing Data

The current default Postgres image is `postgres:18.3-alpine`, with `PGDATA=/var/lib/postgresql/18/docker`.

If you already have a Postgres 16 data volume, do not start it with Postgres 18 directly. Either:

- keep using a matching Postgres 16 image until you migrate;
- export/import with `pg_dump` and `pg_restore`;
- run a proper `pg_upgrade` migration.

For a disposable development database only, use the command that matches your deployment mode.

GHCR deployment:

```bash
docker compose -f docker-compose.ghcr.yml down -v
docker compose -f docker-compose.ghcr.yml up -d
```

Full repository deployment:

```bash
docker compose down -v
docker compose up -d
```

Do not use `down -v` on production data.

## 11. Troubleshooting

### Image pull failures

If `redis`, `postgres`, or Docker Hub images fail to pull, set an image mirror in project-root `.env`:

```ini
IMAGE_PREFIX=docker.m.daocloud.io/library/
```

Then retry:

```bash
docker compose -f docker-compose.ghcr.yml pull
```

If GHCR images fail:

```bash
docker pull ghcr.io/openbyteinc/quantdinger-backend:latest
docker pull ghcr.io/openbyteinc/quantdinger-frontend:latest
docker pull ghcr.io/openbyteinc/quantdinger-mobile:latest
```

Common causes include network blocks, private package visibility, or a pinned tag that does not exist.

### Backend exits immediately

Check:

```bash
docker compose -f docker-compose.ghcr.yml logs --tail=100 backend
```

Common causes:

- invalid backend env syntax;
- missing or placeholder `SECRET_KEY` in full repository mode;
- database not healthy;
- wrong `DATABASE_URL` override.

### Nginx 502 or blank page

Check local services first:

```bash
curl http://127.0.0.1:8888/health
curl http://127.0.0.1:8889/health
curl http://127.0.0.1:5000/api/health
sudo nginx -t
```

Then inspect containers:

```bash
docker compose -f docker-compose.ghcr.yml ps
docker compose -f docker-compose.ghcr.yml logs --tail=100 frontend
docker compose -f docker-compose.ghcr.yml logs --tail=100 backend
```

### Registration or login IP is a Docker gateway

If every audit record shows a private address such as `172.17.0.1`, `172.18.0.1`, or `172.19.0.1`, the backend is recording the Docker gateway instead of the real client. This commonly happens when the host proxy sends all traffic to port `8888` or `8889`, after which the frontend container proxies `/api/` a second time and replaces `X-Real-IP`.

Use the split configuration from section 6:

- send `/api/` on both Web and mobile domains directly to `127.0.0.1:5000`;
- send Web page requests under `/` to `127.0.0.1:8888`;
- send mobile H5 page requests under `/` to `127.0.0.1:8889`;
- have every trusted proxy overwrite `X-Real-IP` and append the forwarding chain with `$proxy_add_x_forwarded_for`;
- do not make the backend trust arbitrary public `X-Forwarded-For` headers merely to fix the display, because clients could then forge audit IPs.

1Panel/OpenResty commonly stores a general `location /` in a separate `proxy/*.conf` file. When replacing it with a complete site configuration, do not retain another include that defines the same location, or Nginx will reject the duplicate. Existing audit rows are not rewritten; validate with a new login or test account after reloading the proxy.

If Cloudflare or another CDN is in front of the domain, `$remote_addr` on the host may be a CDN edge address. Configure the Nginx `real_ip` module with the provider's current official egress CIDRs and restrict origin access to trusted CDN traffic. Do not blindly trust a client-supplied `CF-Connecting-IP` or similar header.

### AI streaming stops after about 50–60 seconds

Typical symptoms:

- `/api/ai/chat/message/stream` returns partial content and then becomes a failed request in the browser after roughly 50–60 seconds;
- the client subsequently falls back to the non-streaming `/api/ai/chat/message` endpoint;
- the backend container did not restart or run out of memory, and `chat_message_stream failed` may be absent from backend logs.

This usually means an outer reverse proxy, such as host Nginx or 1Panel OpenResty, is still using default proxy timeouts and response buffering. A 600-second timeout inside the Docker frontend does not help when the outer proxy closes the SSE connection first.

Add an exact SSE location to every public domain that serves AI chat and send it directly to the backend bound on host-local port `5000`:

```nginx
location = /api/ai/chat/message/stream {
    proxy_pass http://127.0.0.1:5000;

    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Connection "";

    proxy_buffering off;
    proxy_cache off;
    proxy_request_buffering off;

    proxy_connect_timeout 75s;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
    send_timeout 600s;

    add_header X-Accel-Buffering "no" always;
    add_header Cache-Control "no-cache, no-transform" always;
}
```

1Panel OpenResty commonly mounts site configuration and logs at:

```text
/opt/1panel/www/sites/<domain>/proxy/
/opt/1panel/www/sites/<domain>/log/access.log
/opt/1panel/www/sites/<domain>/log/error.log
```

Validate before reloading; replace the container name with the value from your installation:

```bash
docker exec <openresty-container> /usr/local/openresty/nginx/sbin/nginx -t
docker exec <openresty-container> /usr/local/openresty/nginx/sbin/nginx -s reload
```

If the stream still fails, search the proxy error log for `upstream timed out`, `upstream prematurely closed connection`, `499`, `502`, and `504`. Also inspect the backend container's `RestartCount` and `OOMKilled` state.

### Exchange or LLM network requests need a proxy

For backend runtime outbound requests, set `PROXY_URL` in `.env`.

Inside Docker, do not use `127.0.0.1` for a host proxy unless the proxy is running inside the same container. Use a reachable host address, for example:

```ini
PROXY_URL=socks5h://host.docker.internal:10808
```

On Linux, you may need to expose your proxy on a private interface or configure Docker host gateway support.

### Public ports

Do not expose these publicly:

- `5000`
- `5432`
- `6379`

Publicly expose only:

- `80`
- `443`
