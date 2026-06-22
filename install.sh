#!/usr/bin/env bash
#
# QuantDinger interactive installer for Linux and macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash
#
# Custom install directory:
#   curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash -s -- /opt/quantdinger
#
# Optional environment overrides:
#   QUANTDINGER_INSTALL_REF=main
#   QUANTDINGER_INSTALL_DIR=/opt/quantdinger
#

set -eu

if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    BLUE='\033[0;34m'
    CYAN='\033[0;36m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BLUE=''
    CYAN=''
    NC=''
fi

INSTALL_DIR="${1:-${QUANTDINGER_INSTALL_DIR:-$HOME/quantdinger}}"
INSTALL_REF="${QUANTDINGER_INSTALL_REF:-main}"
GITHUB_RAW="https://raw.githubusercontent.com/brokermr810/QuantDinger/${INSTALL_REF}"
COMPOSE_FILE="docker-compose.yml"
BACKEND_ENV="backend.env"
ROOT_ENV=".env"

COMPOSE_CMD=""
ADMIN_USER_VALUE=""
ADMIN_PASSWORD_VALUE=""
ADMIN_EMAIL_VALUE=""
FRONTEND_PORT_VALUE=""
BACKEND_PORT_VALUE=""
POSTGRES_PASSWORD_VALUE=""
IMAGE_PREFIX_VALUE=""
SECRET_KEY_VALUE=""

say() {
    printf '%b\n' "$1"
}

fail() {
    say "${RED}Error: $1${NC}" >&2
    exit 1
}

need_command() {
    command -v "$1" >/dev/null 2>&1 || fail "$1 is required but was not found"
}

read_from_terminal() {
    # curl ... | bash gives the script body on stdin. Keep prompts interactive by
    # reading answers from the controlling terminal whenever one is available.
    if [ -r /dev/tty ]; then
        IFS= read -r value < /dev/tty || value=""
    else
        IFS= read -r value || value=""
    fi
    printf '%s' "$value"
}

read_secret_from_terminal() {
    if [ -r /dev/tty ]; then
        stty -echo < /dev/tty 2>/dev/null || true
        IFS= read -r value < /dev/tty || value=""
        stty echo < /dev/tty 2>/dev/null || true
    else
        stty -echo 2>/dev/null || true
        IFS= read -r value || value=""
        stty echo 2>/dev/null || true
    fi
    printf '\n' >&2
    printf '%s' "$value"
}

read_line() {
    prompt="$1"
    default_value="${2:-}"
    if [ -n "$default_value" ]; then
        printf '%b' "${CYAN}${prompt} [${default_value}]: ${NC}" >&2
    else
        printf '%b' "${CYAN}${prompt}: ${NC}" >&2
    fi
    value="$(read_from_terminal)"
    if [ -z "$value" ]; then
        value="$default_value"
    fi
    printf '%s' "$value"
}

read_secret() {
    prompt="$1"
    printf '%b' "${CYAN}${prompt}: ${NC}" >&2
    read_secret_from_terminal
}

read_secret_optional() {
    prompt="$1"
    printf '%b' "${CYAN}${prompt}: ${NC}" >&2
    read_secret_from_terminal
}

env_get() {
    file="$1"
    key="$2"
    [ -f "$file" ] || return 0
    grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true
}

env_set() {
    file="$1"
    key="$2"
    value="$3"
    touch "$file"
    tmp="${file}.tmp.$$"
    if grep -qE "^${key}=" "$file"; then
        awk -v key="$key" -v value="$value" '
            BEGIN { replaced = 0 }
            index($0, key "=") == 1 { print key "=" value; replaced = 1; next }
            { print }
            END { if (!replaced) print key "=" value }
        ' "$file" > "$tmp"
    else
        cp "$file" "$tmp"
        printf '%s=%s\n' "$key" "$value" >> "$tmp"
    fi
    mv "$tmp" "$file"
}

random_hex() {
    bytes="${1:-32}"
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex "$bytes"
    elif [ -r /dev/urandom ]; then
        od -An -N"$bytes" -tx1 /dev/urandom | tr -d ' \n'
    else
        date +%s%N | sha256sum | awk '{print $1}'
    fi
}

check_prerequisites() {
    say "${BLUE}QuantDinger installer${NC}"
    say "Install directory: ${INSTALL_DIR}"
    say "Source ref: ${INSTALL_REF}"
    say ""

    need_command curl
    need_command docker

    if ! docker info >/dev/null 2>&1; then
        fail "Docker is installed but the Docker daemon is not running"
    fi

    if docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE_CMD="docker-compose"
    else
        fail "Docker Compose v2 is required"
    fi
}

prepare_directory() {
    mkdir -p "$INSTALL_DIR"
    cd "$INSTALL_DIR"
}

download_files() {
    say "${YELLOW}Downloading compose and backend environment template...${NC}"
    curl -fsSL "${GITHUB_RAW}/docker-compose.ghcr.yml" -o "$COMPOSE_FILE"
    if [ ! -f "$BACKEND_ENV" ]; then
        curl -fsSL "${GITHUB_RAW}/backend_api_python/env.example" -o "$BACKEND_ENV"
    fi
    touch "$ROOT_ENV"
}

collect_settings() {
    existing_user=$(env_get "$BACKEND_ENV" "ADMIN_USER")
    existing_email=$(env_get "$BACKEND_ENV" "ADMIN_EMAIL")
    existing_password=$(env_get "$BACKEND_ENV" "ADMIN_PASSWORD")
    existing_frontend_port=$(env_get "$ROOT_ENV" "FRONTEND_PORT")
    existing_backend_port=$(env_get "$ROOT_ENV" "BACKEND_PORT")
    existing_pg_password=$(env_get "$ROOT_ENV" "POSTGRES_PASSWORD")
    existing_image_prefix=$(env_get "$ROOT_ENV" "IMAGE_PREFIX")

    ADMIN_USER_VALUE=$(read_line "Admin username" "${existing_user:-quantdinger}")
    ADMIN_EMAIL_VALUE=$(read_line "Admin email (optional)" "${existing_email:-}")

    if [ -n "$existing_password" ] && [ "$existing_password" != "123456" ]; then
        entered_password=$(read_secret_optional "Admin password (leave blank to keep existing)")
        ADMIN_PASSWORD_VALUE="${entered_password:-$existing_password}"
    else
        while true; do
            pass1=$(read_secret "Admin password")
            pass2=$(read_secret "Confirm admin password")
            if [ -z "$pass1" ]; then
                say "${RED}Admin password cannot be empty.${NC}"
                continue
            fi
            if [ "$pass1" = "123456" ]; then
                say "${RED}Do not use the built-in default password 123456.${NC}"
                continue
            fi
            if [ "$pass1" != "$pass2" ]; then
                say "${RED}Passwords do not match.${NC}"
                continue
            fi
            ADMIN_PASSWORD_VALUE="$pass1"
            break
        done
    fi

    FRONTEND_PORT_VALUE=$(read_line "Frontend port" "${existing_frontend_port:-8888}")
    BACKEND_PORT_VALUE=$(read_line "Backend bind address" "${existing_backend_port:-127.0.0.1:5000}")

    if [ -n "$existing_pg_password" ]; then
        POSTGRES_PASSWORD_VALUE="$existing_pg_password"
    else
        POSTGRES_PASSWORD_VALUE=$(random_hex 18)
    fi

    say ""
    say "Image source:"
    say "  1) global/default"
    say "  2) mainland China mirror (docker.m.daocloud.io/library/)"
    source_choice=$(read_line "Select image source" "1")
    if [ -n "$existing_image_prefix" ]; then
        IMAGE_PREFIX_VALUE="$existing_image_prefix"
    elif [ "$source_choice" = "2" ]; then
        IMAGE_PREFIX_VALUE="docker.m.daocloud.io/library/"
    else
        IMAGE_PREFIX_VALUE=""
    fi

    existing_secret=$(env_get "$BACKEND_ENV" "SECRET_KEY")
    if [ -n "$existing_secret" ] && [ "$existing_secret" != "quantdinger-secret-key-change-me" ]; then
        SECRET_KEY_VALUE="$existing_secret"
    else
        SECRET_KEY_VALUE=$(random_hex 32)
    fi
}

write_settings() {
    env_set "$BACKEND_ENV" "SECRET_KEY" "$SECRET_KEY_VALUE"
    env_set "$BACKEND_ENV" "ADMIN_USER" "$ADMIN_USER_VALUE"
    env_set "$BACKEND_ENV" "ADMIN_PASSWORD" "$ADMIN_PASSWORD_VALUE"
    env_set "$BACKEND_ENV" "ADMIN_EMAIL" "$ADMIN_EMAIL_VALUE"
    env_set "$BACKEND_ENV" "FRONTEND_URL" "http://localhost:${FRONTEND_PORT_VALUE}"

    env_set "$ROOT_ENV" "FRONTEND_PORT" "$FRONTEND_PORT_VALUE"
    env_set "$ROOT_ENV" "BACKEND_PORT" "$BACKEND_PORT_VALUE"
    env_set "$ROOT_ENV" "POSTGRES_PASSWORD" "$POSTGRES_PASSWORD_VALUE"
    env_set "$ROOT_ENV" "IMAGE_PREFIX" "$IMAGE_PREFIX_VALUE"

    chmod 600 "$BACKEND_ENV" "$ROOT_ENV" 2>/dev/null || true
}

start_stack() {
    say "${YELLOW}Pulling images...${NC}"
    $COMPOSE_CMD -f "$COMPOSE_FILE" pull
    say "${YELLOW}Starting services...${NC}"
    $COMPOSE_CMD -f "$COMPOSE_FILE" up -d
}

wait_for_backend() {
    say "${YELLOW}Waiting for backend health check...${NC}"
    api_url="http://127.0.0.1:${BACKEND_PORT_VALUE##*:}/api/health"
    attempt=1
    while [ "$attempt" -le 45 ]; do
        if curl -sf "$api_url" >/dev/null 2>&1; then
            say "${GREEN}Backend is ready.${NC}"
            return 0
        fi
        printf '  waiting... (%s/45)\n' "$attempt"
        sleep 2
        attempt=$((attempt + 1))
    done
    say "${YELLOW}Backend is still starting. Check logs with:${NC}"
    say "  cd ${INSTALL_DIR}"
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} logs -f backend"
}

print_summary() {
    say ""
    say "${GREEN}QuantDinger is ready.${NC}"
    say ""
    say "Web UI:      http://localhost:${FRONTEND_PORT_VALUE}"
    say "API:         http://127.0.0.1:${BACKEND_PORT_VALUE##*:}"
    say "Directory:   ${INSTALL_DIR}"
    say "Username:    ${ADMIN_USER_VALUE}"
    say "Password:    the password you entered during installation"
    say ""
    say "Useful commands:"
    say "  cd ${INSTALL_DIR}"
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} ps"
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} logs -f backend"
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} pull && ${COMPOSE_CMD} -f ${COMPOSE_FILE} up -d"
    say ""
    say "${YELLOW}Trading involves substantial risk. Start with paper trading and small test accounts.${NC}"
}

main() {
    check_prerequisites
    prepare_directory
    download_files
    collect_settings
    write_settings
    start_stack
    wait_for_backend
    print_summary
}

main
