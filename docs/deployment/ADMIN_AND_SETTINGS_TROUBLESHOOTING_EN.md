# Administrator Credentials and Settings Save Troubleshooting

This guide covers initial administrator credentials, existing database volumes,
and Settings UI write-permission errors for Docker deployments.

For general installation problems, see
[Installation Troubleshooting](INSTALL_TROUBLESHOOTING.md).

## Initial Administrator Behavior

On an empty database, QuantDinger creates the initial administrator from
`ADMIN_USER`, `ADMIN_PASSWORD`, and optional `ADMIN_EMAIL`. The password is
stored as a password hash (bcrypt in the standard image), never as plaintext.

For backward compatibility, a manual Docker deployment that leaves the example
administrator values unchanged can still sign in with `quantdinger` / `123456`.
Do not expose this credential to the internet. Set a non-default password before
the first start or change it immediately after the first login. The one-command
installer rejects `123456` as the selected password.

An existing PostgreSQL volume is persistent user data. Changing `ADMIN_USER` or
`ADMIN_PASSWORD` does not unconditionally replace existing accounts. Automatic
legacy migration occurs only when all of the following are true:

- the first active administrator still uses username `quantdinger` and password
  `123456`;
- a valid, non-default administrator username and password are configured;
- the requested username is not already used by another account.

QuantDinger never overwrites an administrator whose password has already been
changed. If another account already uses the requested username, migration stops
instead of promoting or merging that account.

## Where Settings UI Values Are Stored

The Settings UI saves values to PostgreSQL (`qd_runtime_settings`). Every
backend, worker, and Celery process applies them at startup and picks up later
saves within about 10 seconds, without a restart. Saved values survive restarts
and redeployments, so platforms without a shared host `.env` (for example
Railway) keep their settings. API keys and other password fields are stored
encrypted with `CREDENTIAL_ENCRYPTION_KEY`.

The environment file remains the baseline: a value saved in the Settings UI
overrides the same key from `.env` or the container environment. Process-owned
keys (`SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY`, and process-role or strategy
topology settings) only take effect at process start, so the Settings UI still
writes those to `/app/.env` and they require a restart.

## Settings UI Reports Save Failure

A save failure usually means PostgreSQL is unavailable, or a process-owned key
could not be written to `/app/.env`. The corresponding host file is:

| Deployment | Host file |
| --- | --- |
| All supported Compose deployments | project-root `.env` |

The backend application runs as UID/GID `10001`. Current backend images start as
root, prepare the mounted file, change `/app/.env` ownership to `10001:10001`,
restrict it to mode `600`, and then drop privileges. This behavior applies to
both one-command and manual Docker Compose installations.

Check the runtime user and write access:

```bash
docker compose exec -u 10001:10001 -T backend sh -c '
  id
  ls -ln /app/.env
  test -r /app/.env && test -w /app/.env \
    && echo writable=yes || echo writable=no
'
```

When using a manually downloaded `docker-compose.ghcr.yml`, add
`-f docker-compose.ghcr.yml` after `docker compose`.

Expected output includes UID `10001`, file owner/group `10001 10001`, mode
`600`, and `writable=yes`.

### Repair an Installation That Still Uses an Old Image

Update the backend image first. Re-downloading `install.sh` alone does not update
an already running container:

```bash
docker compose pull backend
docker compose up -d --force-recreate backend
```

If the old image left the host file owned by root, repair only the backend
runtime file:

```bash
# All supported Compose deployments
sudo chown 10001:10001 .env
sudo chmod 600 .env
```

Do not use `chmod 755` or `chmod -R 777`. These files contain administrator
credentials, API keys, OAuth secrets, and broker or exchange settings. Mode
`755` exposes secrets to other local users and still does not let UID `10001`
write a root-owned file.

## Read-Only and Special Docker Deployments

Automatic ownership repair cannot make a deliberately read-only mount writable.
The Settings UI is read-only by design in these cases:

- `/app/.env` is mounted with `:ro`;
- `docker-compose.production.yml` is enabled;
- the container is forced to start as non-root with `user: 10001:10001` before
  ownership can be prepared;
- rootless Docker, user namespace remapping, NFS, or another filesystem policy
  rejects `chown`.

For a hardened read-only deployment, edit the host environment file and recreate
the services:

```bash
docker compose pull
docker compose up -d --force-recreate
```

Return to the [main README](../../README.md).
