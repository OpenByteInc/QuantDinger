#!/usr/bin/env bash
#
# QuantDinger interactive installer for Linux and macOS.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash
#
# Custom install directory:
#   curl -fsSL https://raw.githubusercontent.com/OpenByteInc/QuantDinger/main/install.sh | bash -s -- /opt/quantdinger
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
GITHUB_RAW="https://raw.githubusercontent.com/OpenByteInc/QuantDinger/${INSTALL_REF}"
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=".env"
ENV_TEMPLATE=".env.example"
LEGACY_ENV="backend.env"

COMPOSE_CMD=""
ADMIN_USER_VALUE=""
ADMIN_PASSWORD_VALUE=""
ADMIN_EMAIL_VALUE=""
FRONTEND_PORT_VALUE=""
MOBILE_PORT_VALUE=""
BACKEND_PORT_VALUE=""
POSTGRES_PASSWORD_VALUE=""
IMAGE_PREFIX_VALUE=""
SECRET_KEY_VALUE=""
ADMIN_CREDENTIALS_REUSED="false"
ENV_EXISTED_BEFORE_INSTALL="false"

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

env_get() {
    file="$1"
    key="$2"
    [ -f "$file" ] || return 0
    raw_value=$(grep -E "^${key}=" "$file" | tail -n 1 | cut -d= -f2- || true)

    # Decode values previously written by env_set_quoted. This is intentionally
    # limited to dotenv's basic quote escapes; environment files are never sourced.
    case "$raw_value" in
        \"*\")
            raw_value=${raw_value#\"}
            raw_value=${raw_value%\"}
            printf '%s' "$raw_value" | sed -e 's/\\\"/"/g' -e 's/\\\\/\\/g'
            ;;
        \'*\')
            raw_value=${raw_value#\'}
            raw_value=${raw_value%\'}
            printf '%s' "$raw_value" | sed -e "s/\\\\'/'/g" -e 's/\\\\/\\/g'
            ;;
        *)
            printf '%s' "$raw_value"
            ;;
    esac
}

postgres_password_from_url() {
    database_url="$1"
    case "$database_url" in
        postgres://*:*@*|postgresql://*:*@*)
            credentials=${database_url#*://}
            credentials=${credentials%%@*}
            printf '%s' "${credentials#*:}"
            ;;
    esac
}

postgres_password_from_container() {
    docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' quantdinger-db 2>/dev/null \
        | sed -n 's/^POSTGRES_PASSWORD=//p' \
        | head -n 1
}

has_existing_postgres_volume() {
    project_name="${COMPOSE_PROJECT_NAME:-$(basename "$INSTALL_DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"
    [[ -n "$project_name" ]] || project_name="quantdinger"
    [[ -n "$(docker volume ls -q \
        --filter "label=com.docker.compose.project=${project_name}" \
        --filter "label=com.docker.compose.volume=postgres_data" 2>/dev/null)" ]]
}

env_set() {
    file="$1"
    key="$2"
    value="$3"
    touch "$file"
    tmp="${file}.tmp.$$"
    replaced="false"
    : > "$tmp"

    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            "${key}="*)
                if [ "$replaced" != "true" ]; then
                    printf '%s=%s\n' "$key" "$value" >> "$tmp"
                    replaced="true"
                fi
                ;;
            *)
                printf '%s\n' "$line" >> "$tmp"
                ;;
        esac
    done < "$file"

    if [ "$replaced" != "true" ]; then
        printf '%s=%s\n' "$key" "$value" >> "$tmp"
    fi
    mv "$tmp" "$file"
}

dotenv_quote() {
    printf '"'
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
    printf '"'
}

env_set_quoted() {
    file="$1"
    key="$2"
    value="$3"
    env_set "$file" "$key" "$(dotenv_quote "$value")"
}

sync_env_missing() {
    target="$1"
    template="$2"
    legacy="${3:-}"
    if [ ! -f "$target" ]; then
        if [ -n "$legacy" ] && [ -f "$legacy" ]; then
            cp -p "$legacy" "$target"
            say "Created ${target} from legacy ${legacy}."
            legacy=""
        else
            cp "$template" "$target"
            say "Created ${target} from ${template}."
        fi
    fi

    additions="${target}.additions.$$"
    legacy_source="/dev/null"
    if [ -n "$legacy" ] && [ -f "$legacy" ]; then
        legacy_source="$legacy"
    fi

    awk '
        function assignment_key(line, value) {
            value = line
            sub(/^[[:space:]]*export[[:space:]]+/, "", value)
            if (value !~ /^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=/) return ""
            sub(/[[:space:]]*=.*/, "", value)
            return value
        }
        FILENAME == ARGV[1] {
            key = assignment_key($0)
            if (key != "") seen[key] = 1
            next
        }
        {
            key = assignment_key($0)
            if (key != "" && !seen[key]) {
                print $0
                seen[key] = 1
            }
        }
    ' "$target" "$legacy_source" "$template" > "$additions"

    if [ ! -s "$additions" ]; then
        rm -f "$additions"
        say "Environment is up to date: ${target}"
        return 0
    fi

    if [ -s "$target" ]; then
        backup="${target}.bak.$(date -u +%Y%m%dT%H%M%SZ)"
        backup_index=1
        while [ -e "$backup" ]; do
            backup="${target}.bak.$(date -u +%Y%m%dT%H%M%SZ).${backup_index}"
            backup_index=$((backup_index + 1))
        done
        cp -p "$target" "$backup"
        say "Environment backup created: ${backup}"
    fi
    {
        if [ -s "$target" ]; then
            printf '\n'
        fi
        printf '# Added automatically during QuantDinger install/update\n'
        cat "$additions"
    } >> "$target"
    count=$(wc -l < "$additions" | tr -d '[:space:]')
    rm -f "$additions"
    say "Added ${count} missing environment key(s) to ${target}; existing values were preserved."
}

has_edge_whitespace() {
    value="$1"
    trimmed_value=$(printf '%s' "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    [ "$value" != "$trimmed_value" ]
}

password_byte_length() {
    LC_ALL=C printf '%s' "$1" | wc -c | tr -d '[:space:]'
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
    if [[ -f "$ENV_FILE" || -f "$LEGACY_ENV" ]]; then
        ENV_EXISTED_BEFORE_INSTALL="true"
    fi
}

download_files() {
    say "${YELLOW}Downloading compose and unified environment template...${NC}"
    curl -fsSL "${GITHUB_RAW}/docker-compose.ghcr.yml" -o "$COMPOSE_FILE"
    curl -fsSL "${GITHUB_RAW}/.env.example" -o "$ENV_TEMPLATE"
    sync_env_missing "$ENV_FILE" "$ENV_TEMPLATE" "$LEGACY_ENV"
}

collect_settings() {
    existing_user=$(env_get "$ENV_FILE" "ADMIN_USER")
    existing_email=$(env_get "$ENV_FILE" "ADMIN_EMAIL")
    existing_password=$(env_get "$ENV_FILE" "ADMIN_PASSWORD")
    existing_frontend_port=$(env_get "$ENV_FILE" "FRONTEND_PORT")
    existing_mobile_port=$(env_get "$ENV_FILE" "MOBILE_PORT")
    existing_backend_port=$(env_get "$ENV_FILE" "BACKEND_PORT")
    existing_pg_password=$(env_get "$ENV_FILE" "POSTGRES_PASSWORD")
    if [[ -z "$existing_pg_password" ]]; then
        existing_pg_password=$(postgres_password_from_container || true)
        if [[ -n "$existing_pg_password" ]]; then
            say "${YELLOW}Recovered the existing PostgreSQL credential from the current container.${NC}"
        fi
    fi
    if [[ -z "$existing_pg_password" ]]; then
        existing_database_url=$(env_get "$ENV_FILE" "DATABASE_URL")
        existing_pg_password=$(postgres_password_from_url "$existing_database_url")
        if [[ -n "$existing_pg_password" ]]; then
            say "${YELLOW}Recovered the existing PostgreSQL credential from DATABASE_URL.${NC}"
        fi
    fi
    if [[ -z "$existing_pg_password" ]] \
        && { [[ "$ENV_EXISTED_BEFORE_INSTALL" = "true" ]] || has_existing_postgres_volume; }; then
        fail "The existing installation has no recoverable PostgreSQL password. Refusing to generate a replacement because it could disconnect existing data. Restore POSTGRES_PASSWORD in ${ENV_FILE} before continuing."
    fi
    existing_image_prefix=$(env_get "$ENV_FILE" "IMAGE_PREFIX")

    existing_user_length=${#existing_user}
    existing_password_length=${#existing_password}
    existing_password_bytes=$(password_byte_length "$existing_password")
    if [ -n "$existing_password" ] \
        && [ "$existing_password" != "123456" ] \
        && [ "$existing_user_length" -ge 3 ] \
        && [ "$existing_user_length" -le 50 ] \
        && ! has_edge_whitespace "$existing_user" \
        && [ "$existing_password_length" -ge 6 ] \
        && [ "$existing_password_bytes" -le 72 ]; then
        ADMIN_USER_VALUE="$existing_user"
        ADMIN_PASSWORD_VALUE="$existing_password"
        ADMIN_CREDENTIALS_REUSED="true"
        say "${YELLOW}Existing administrator credentials detected; keeping the configured username and password.${NC}"
        say "Change administrator credentials from Profile after signing in."
    else
        while true; do
            ADMIN_USER_VALUE=$(read_line "Admin username" "${existing_user:-quantdinger}")
            admin_user_length=${#ADMIN_USER_VALUE}
            if [ "$admin_user_length" -lt 3 ] || [ "$admin_user_length" -gt 50 ]; then
                say "${RED}Admin username must be 3-50 characters.${NC}"
                continue
            fi
            if has_edge_whitespace "$ADMIN_USER_VALUE"; then
                say "${RED}Admin username cannot start or end with whitespace.${NC}"
                continue
            fi
            break
        done

        while true; do
            pass1=$(read_secret "Admin password")
            pass2=$(read_secret "Confirm admin password")
            if [ "${#pass1}" -lt 6 ]; then
                say "${RED}Admin password must be at least 6 characters.${NC}"
                continue
            fi
            if [ "$(password_byte_length "$pass1")" -gt 72 ]; then
                say "${RED}Admin password must be at most 72 UTF-8 bytes.${NC}"
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
    ADMIN_EMAIL_VALUE=$(read_line "Admin email (optional)" "${existing_email:-}")

    FRONTEND_PORT_VALUE=$(read_line "Frontend port" "${existing_frontend_port:-8888}")
    MOBILE_PORT_VALUE=$(read_line "Mobile H5 port" "${existing_mobile_port:-8889}")
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

    existing_secret=$(env_get "$ENV_FILE" "SECRET_KEY")
    if [ -n "$existing_secret" ] && [ "$existing_secret" != "quantdinger-secret-key-change-me" ] && [ "${#existing_secret}" -ge 10 ]; then
        SECRET_KEY_VALUE="$existing_secret"
    else
        SECRET_KEY_VALUE=$(random_hex 32)
    fi
}

write_settings() {
    env_set_quoted "$ENV_FILE" "SECRET_KEY" "$SECRET_KEY_VALUE"
    env_set_quoted "$ENV_FILE" "ADMIN_USER" "$ADMIN_USER_VALUE"
    env_set_quoted "$ENV_FILE" "ADMIN_PASSWORD" "$ADMIN_PASSWORD_VALUE"
    env_set_quoted "$ENV_FILE" "ADMIN_EMAIL" "$ADMIN_EMAIL_VALUE"
    env_set "$ENV_FILE" "FRONTEND_PORT" "$FRONTEND_PORT_VALUE"
    env_set "$ENV_FILE" "MOBILE_PORT" "$MOBILE_PORT_VALUE"
    env_set "$ENV_FILE" "BACKEND_PORT" "$BACKEND_PORT_VALUE"
    env_set "$ENV_FILE" "POSTGRES_PASSWORD" "$POSTGRES_PASSWORD_VALUE"
    env_set "$ENV_FILE" "IMAGE_PREFIX" "$IMAGE_PREFIX_VALUE"
    env_set "$ENV_FILE" "FRONTEND_URL" "http://localhost:${FRONTEND_PORT_VALUE},http://localhost:${MOBILE_PORT_VALUE}"

    chmod 600 "$ENV_FILE" 2>/dev/null || true
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
        if curl -sf --max-time 2 "$api_url" >/dev/null 2>&1; then
            say "${GREEN}Backend is ready.${NC}"
            return 0
        fi
        printf '  waiting... (%s/45)\n' "$attempt"
        sleep 2
        attempt=$((attempt + 1))
    done
    say "${RED}Backend did not become healthy within the expected startup window. Check logs with:${NC}"
    say "  cd ${INSTALL_DIR}"
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} logs -f backend"
    fail "Installation did not complete successfully"
}

verify_settings_storage() {
    say "${YELLOW}Verifying system settings storage...${NC}"
    if $COMPOSE_CMD -f "$COMPOSE_FILE" exec -T -u 10001:10001 backend \
        sh -c 'test -f /app/.env && test -r /app/.env && test -w /app/.env' \
        >/dev/null 2>&1; then
        say "${GREEN}System settings storage is writable.${NC}"
        return 0
    fi

    say "${RED}The backend runtime user cannot write /app/.env.${NC}"
    say "Inspect the host file and container ownership with:"
    say "  ls -ln ${INSTALL_DIR}/${ENV_FILE}"
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} exec -T -u 10001:10001 backend ls -ln /app/.env"
    fail "Installation stopped because system settings cannot be saved"
}

verify_admin_credentials() {
    say "${YELLOW}Verifying administrator credentials...${NC}"
    if $COMPOSE_CMD -f "$COMPOSE_FILE" exec -T backend python -c '
from app.config.settings import Config
from app.services.user_service import get_user_service

user = get_user_service().authenticate(
    Config.ADMIN_USER,
    Config.ADMIN_PASSWORD,
    update_last_login=False,
)
raise SystemExit(0 if user and user.get("role") == "admin" else 1)
' >/dev/null 2>&1; then
        say "${GREEN}Administrator credentials verified.${NC}"
        return 0
    fi

    say "${RED}The configured administrator credentials do not match the database.${NC}"
    say "This usually means an existing PostgreSQL volume already contains a different account."
    say "Inspect the existing account and backend startup log with:"
    say "  docker exec quantdinger-db psql -U quantdinger -d quantdinger -c \"SELECT id, username, role, status FROM qd_users;\""
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} logs backend"
    fail "Installation stopped because the administrator login could not be verified"
}

print_summary() {
    say ""
    say "${GREEN}QuantDinger is ready.${NC}"
    say ""
    say "Web UI:      http://localhost:${FRONTEND_PORT_VALUE}"
    say "Mobile H5:   http://localhost:${MOBILE_PORT_VALUE}"
    say "API:         http://127.0.0.1:${BACKEND_PORT_VALUE##*:}"
    say "Directory:   ${INSTALL_DIR}"
    say "Username:    ${ADMIN_USER_VALUE}"
    if [ "$ADMIN_CREDENTIALS_REUSED" = "true" ]; then
        say "Password:    existing administrator password"
    else
        say "Password:    the password you entered during installation"
    fi
    say ""
    say "Useful commands:"
    say "  cd ${INSTALL_DIR}"
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} ps"
    say "  ${COMPOSE_CMD} -f ${COMPOSE_FILE} logs -f backend"
    say "  Rerun this installer to update images and append newly added .env keys safely."
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
    verify_settings_storage
    verify_admin_credentials
    print_summary
}

if [ "${QUANTDINGER_INSTALL_LIB_ONLY:-false}" != "true" ]; then
    main
fi
