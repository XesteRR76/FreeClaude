#!/usr/bin/env bash
# ==============================================================================
# launch_claude.sh - Launches Claude Code CLI with local Gemini Proxy routing
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Read proxy host & port from .env or default to 127.0.0.1:8080
PROXY_HOST=$(grep "^PROXY_HOST=" "${PROJECT_ROOT}/.env" 2>/dev/null | cut -d '=' -f2 | tr -d ' "\r' || echo "127.0.0.1")
PROXY_PORT=$(grep "^PROXY_PORT=" "${PROJECT_ROOT}/.env" 2>/dev/null | cut -d '=' -f2 | tr -d ' "\r' || echo "8080")
PROXY_HOST=${PROXY_HOST:-127.0.0.1}
PROXY_PORT=${PROXY_PORT:-8080}
PROXY_URL="http://${PROXY_HOST}:${PROXY_PORT}"

# ANSI Colors
CYAN="\033[1;36m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
BOLD="\033[1m"
RESET="\033[0m"

echo -e "${CYAN}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}║     Claude Code CLI — Powered by Google Gemini Proxy         ║${RESET}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════════╝${RESET}"

# Check if proxy is running; if not, boot it in daemon mode
if ! curl -s --max-time 1 "${PROXY_URL}/health" >/dev/null 2>&1; then
    echo -e "${YELLOW}[*] Proxy server is not running. Launching in background...${RESET}"
    "${SCRIPT_DIR}/start_proxy.sh" --daemon

    # Wait for proxy to become ready
    READY=0
    for i in {1..30}; do
        if curl -s --max-time 1 "${PROXY_URL}/health" >/dev/null 2>&1; then
            READY=1
            break
        fi
        sleep 0.2
    done

    if [ $READY -eq 1 ]; then
        echo -e "${GREEN}[✓] Proxy is UP and healthy on ${PROXY_URL}${RESET}"
    else
        echo -e "${YELLOW}[!] Warning: Proxy health check timed out. Proceeding anyway...${RESET}"
    fi
else
    echo -e "${GREEN}[✓] Connected to running Gemini proxy at ${PROXY_URL}${RESET}"
fi

# Configure Anthropic environment variables to redirect to local proxy
export ANTHROPIC_BASE_URL="${PROXY_URL}"
export ANTHROPIC_API_KEY="freeclaude"
export ANTHROPIC_AUTH_TOKEN="freeclaude"

echo -e "  • ${BOLD}ANTHROPIC_BASE_URL:${RESET} ${ANTHROPIC_BASE_URL}"
echo -e "  • ${BOLD}Proxy Logs:${RESET} ${PROJECT_ROOT}/proxy.log"
echo -e "  • ${BOLD}Launching Claude Code CLI...${RESET}\n"

# Verify claude binary exists
if ! command -v claude >/dev/null 2>&1; then
    echo -e "\033[1;31m[-] Error: 'claude' command not found in PATH.\033[0m"
    echo "    Please install Claude Code CLI (e.g. 'npm install -g @anthropic-ai/claude-code' or via yay)."
    read -rp "Press Enter to exit..."
    exit 1
fi

# Execute Claude Code CLI with passed arguments
exec claude --dangerously-skip-permissions "$@"

