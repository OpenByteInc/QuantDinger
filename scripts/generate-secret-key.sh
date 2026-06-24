#!/bin/bash
# Helper script to generate a secure SECRET_KEY for QuantDinger
# Usage: ./scripts/generate-secret-key.sh

set -e

ENV_FILE="backend_api_python/.env"

# Check if .env exists.
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: $ENV_FILE not found"
    echo "Please run: cp backend_api_python/env.example backend_api_python/.env"
    exit 1
fi

# Generate a secure random key.
NEW_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

# Update SECRET_KEY in .env file.
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    sed -i '' "s|^SECRET_KEY=.*|SECRET_KEY=$NEW_KEY|" "$ENV_FILE"
else
    # Linux
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$NEW_KEY|" "$ENV_FILE"
fi

echo "[OK] SECRET_KEY generated and updated in $ENV_FILE"
echo ""
echo "Generated key: $NEW_KEY"
echo ""
echo "You can now start the application:"
echo "  docker-compose up -d --build"
