#!/bin/bash
#
# QuantDinger One-Click Installation Script
# https://github.com/brokermr810/QuantDinger
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash
#
# Custom install directory:
#   curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash -s -- /opt/quantdinger
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="${1:-$HOME/quantdinger}"
COMPOSE_SOURCE="docker-compose.ghcr.yml"
GITHUB_RAW="https://raw.githubusercontent.com/brokermr810/QuantDinger/main"
FRONTEND_PORT="${FRONTEND_PORT:-8888}"
BACKEND_PORT="${BACKEND_PORT:-5000}"

echo -e "${BLUE}"
echo "╔════════════════════════════════════════════════════════════╗"
echo "║           QuantDinger — AI Quant Operating System          ║"
echo "║                  One-Click Installation                    ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

check_docker() {
    echo -e "${YELLOW}Checking Docker...${NC}"
    if ! command -v docker &> /dev/null; then
        echo -e "${RED}Error: Docker is not installed.${NC}"
        echo "Install Docker first: https://docs.docker.com/get-docker/"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        echo -e "${RED}Error: Docker daemon is not running.${NC}"
        echo "Start Docker Desktop or the Docker service, then try again."
        exit 1
    fi

    if docker compose version &> /dev/null; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        echo -e "${RED}Error: Docker Compose is not available.${NC}"
        echo "Install Docker Compose: https://docs.docker.com/compose/install/"
        exit 1
    fi

    echo -e "${GREEN}✓ Docker is ready${NC}"
}

setup_directory() {
    echo -e "${YELLOW}Setting up installation directory: ${INSTALL_DIR}${NC}"
    mkdir -p "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    echo -e "${GREEN}✓ Directory ready${NC}"
}

download_files() {
    echo -e "${YELLOW}Downloading configuration files...${NC}"

    curl -fsSL "$GITHUB_RAW/$COMPOSE_SOURCE" -o docker-compose.yml

    if [ ! -f "backend.env" ]; then
        curl -fsSL "$GITHUB_RAW/backend_api_python/env.example" -o backend.env
        echo -e "${GREEN}✓ backend.env created from env.example${NC}"
    else
        echo -e "${GREEN}✓ backend.env already exists, keeping your config${NC}"
    fi

    echo -e "${GREEN}✓ Files downloaded${NC}"
}

pull_images() {
    echo -e "${YELLOW}Pulling Docker images (this may take a few minutes)...${NC}"
    $COMPOSE_CMD pull
    echo -e "${GREEN}✓ Images pulled${NC}"
}

start_services() {
    echo -e "${YELLOW}Starting QuantDinger services...${NC}"
    $COMPOSE_CMD up -d
    echo -e "${GREEN}✓ Services started${NC}"
}

wait_for_services() {
    echo -e "${YELLOW}Waiting for services to be ready...${NC}"

    local max_attempts=45
    local attempt=1

    while [ $attempt -le $max_attempts ]; do
        if curl -sf "http://127.0.0.1:${BACKEND_PORT}/api/health" > /dev/null 2>&1; then
            echo -e "${GREEN}✓ Backend is ready${NC}"
            break
        fi
        echo "  Waiting for backend... ($attempt/$max_attempts)"
        sleep 2
        ((attempt++))
    done

    if [ $attempt -gt $max_attempts ]; then
        echo -e "${YELLOW}Backend is still starting — check logs with: cd ${INSTALL_DIR} && ${COMPOSE_CMD} logs -f backend${NC}"
    fi
}

get_server_ip() {
    local public_ip
    public_ip=$(curl -s --max-time 3 ifconfig.me 2>/dev/null || curl -s --max-time 3 icanhazip.com 2>/dev/null || echo "")

    if [ -z "$public_ip" ]; then
        if command -v ip &> /dev/null; then
            public_ip=$(ip route get 1 2>/dev/null | awk '{print $7}' | head -1)
        elif command -v hostname &> /dev/null; then
            public_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
        fi
    fi

    echo "${public_ip:-127.0.0.1}"
}

print_success() {
    local SERVER_IP
    SERVER_IP=$(get_server_ip)

    echo ""
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗"
    echo -e "║              🎉 Installation Complete! 🎉                  ║"
    echo -e "╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${BLUE}Web UI:${NC}       http://${SERVER_IP}:${FRONTEND_PORT}"
    echo -e "  ${BLUE}API:${NC}          http://${SERVER_IP}:${BACKEND_PORT}"
    echo -e "  ${BLUE}Install dir:${NC}  ${INSTALL_DIR}"
    echo ""
    echo -e "  ${BLUE}Default login:${NC}  quantdinger / 123456"
    echo -e "  ${YELLOW}Change the admin password before any real use.${NC}"
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗"
    echo -e "║  Re-run the one-liner anytime to pull latest images        ║"
    echo -e "╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${GREEN}curl -fsSL https://raw.githubusercontent.com/brokermr810/QuantDinger/main/install.sh | bash${NC}"
    echo ""
    echo -e "${YELLOW}Quick commands:${NC}"
    echo "  cd ${INSTALL_DIR}"
    echo "  ${COMPOSE_CMD} logs -f          # View logs"
    echo "  ${COMPOSE_CMD} restart backend  # Restart API"
    echo "  ${COMPOSE_CMD} down             # Stop stack"
    echo "  ${COMPOSE_CMD} pull && ${COMPOSE_CMD} up -d   # Update"
    echo ""
    echo -e "${YELLOW}Next steps:${NC}"
    echo "  1. Open http://127.0.0.1:${FRONTEND_PORT} in your browser"
    echo "  2. Configure an LLM provider in backend.env for AI features"
    echo "  3. Connect exchange / broker API keys when ready"
    echo "  4. Issue an Agent token for Cursor / Claude Code / MCP"
    echo ""
    echo -e "${RED}⚠️  Risk warning: Trading involves substantial risk. Use only funds you can afford to lose.${NC}"
    echo ""
}

main() {
    check_docker
    setup_directory
    download_files
    pull_images
    start_services
    wait_for_services
    print_success
}

main
