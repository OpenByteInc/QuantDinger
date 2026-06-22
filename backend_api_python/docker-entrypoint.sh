#!/bin/sh
# QuantDinger Docker Entrypoint Script
# Checks and validates SECRET_KEY before starting the application

set -e

echo "============================================"
echo "  QuantDinger Backend - Starting..."
echo "============================================"

# Check if .env file exists
if [ ! -f /app/.env ]; then
    echo "[WARNING] .env file not found at /app/.env"
    echo "Creating .env from env.example..."
    if [ -f /app/env.example ]; then
        cp /app/env.example /app/.env
        echo "[INFO] Created .env from env.example"
        echo "[IMPORTANT] Please edit /app/.env and set a secure SECRET_KEY before restarting!"
    else
        echo "[ERROR] env.example not found. Cannot create .env automatically."
        exit 1
    fi
fi

# Check SECRET_KEY configuration
DEFAULT_SECRET="quantdinger-secret-key-change-me"
CURRENT_SECRET=$(grep -E "^SECRET_KEY=" /app/.env 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'" | xargs || echo "")

if [ -z "$CURRENT_SECRET" ]; then
    NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    echo "SECRET_KEY=${NEW_SECRET}" >> /app/.env
    echo "[AUTO] Generated random SECRET_KEY (was missing)."
    CURRENT_SECRET="$NEW_SECRET"
fi

# Auto-generate SECRET_KEY if using default (zero-config experience)
if [ "$CURRENT_SECRET" = "$DEFAULT_SECRET" ]; then
    NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    # Use a temp file + write-back instead of `sed -i`. When /app/.env is a
    # Docker bind-mount from the host (zero-repo GHCR deploy), `sed -i` fails
    # with "Device or resource busy" because it tries to rename(2) the inode
    # over a mount target. Truncate+write through the mount works fine and
    # propagates the new key back to the host file.
    TMP=$(mktemp)
    sed "s|SECRET_KEY=.*|SECRET_KEY=${NEW_SECRET}|" /app/.env > "$TMP"
    cat "$TMP" > /app/.env
    rm -f "$TMP"
    echo "[AUTO] Generated random SECRET_KEY (was default)."
    echo "[TIP]  For production, set a persistent SECRET_KEY in backend_api_python/.env"
fi

echo "[OK] SECRET_KEY is configured"
SECRET_LEN=$(printf '%s' "$CURRENT_SECRET" | wc -c | tr -d ' ')
if [ "$SECRET_LEN" -lt 32 ]; then
    echo "[WARNING] SECRET_KEY is only ${SECRET_LEN} bytes; RFC 7518 recommends >= 32 for HS256."
    echo "          Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    echo "          After updating .env, restart the stack; users must sign in again."
fi
echo ""

# Start the application
exec "$@"
