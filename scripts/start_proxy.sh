#!/usr/bin/env bash
# ==============================================================================
# start_proxy.sh - Controls the Claude -> Gemini proxy server lifecycle
# Usage:
#   ./scripts/start_proxy.sh          # Run in foreground with live console logs
#   ./scripts/start_proxy.sh --daemon # Run in background
#   ./scripts/start_proxy.sh --status # Check if server is running
#   ./scripts/start_proxy.sh --stop   # Stop running daemon instance
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_ROOT}/venv"
PID_FILE="${PROJECT_ROOT}/proxy.pid"
LOG_FILE="${PROJECT_ROOT}/proxy.log"

cd "${PROJECT_ROOT}"

# Ensure python venv exists
if [ ! -d "${VENV_DIR}" ]; then
    echo "[*] Creating Python virtual environment in ${VENV_DIR}..."
    python3 -m venv "${VENV_DIR}"
fi

# Activate virtual environment
source "${VENV_DIR}/bin/activate"

# Verify dependencies are installed
if ! python3 -c "import fastapi, uvicorn, httpx, dotenv, pydantic" 2>/dev/null; then
    echo "[*] Installing missing dependencies from requirements.txt..."
    pip install -r "${PROJECT_ROOT}/requirements.txt"
fi

# Load port and host from .env if present
PROXY_HOST=$(grep "^PROXY_HOST=" .env 2>/dev/null | cut -d '=' -f2 | tr -d ' "\r' || echo "127.0.0.1")
PROXY_PORT=$(grep "^PROXY_PORT=" .env 2>/dev/null | cut -d '=' -f2 | tr -d ' "\r' || echo "8080")
PROXY_HOST=${PROXY_HOST:-127.0.0.1}
PROXY_PORT=${PROXY_PORT:-8080}

is_running() {
    if [ -f "${PID_FILE}" ]; then
        PID=$(cat "${PID_FILE}")
        if kill -0 "${PID}" 2>/dev/null; then
            return 0
        fi
    fi
    # Also check if something is listening on the port
    if curl -s "http://${PROXY_HOST}:${PROXY_PORT}/health" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

case "$1" in
    --status)
        if is_running; then
            echo "[✓] Proxy server is RUNNING on http://${PROXY_HOST}:${PROXY_PORT}"
            curl -s "http://${PROXY_HOST}:${PROXY_PORT}/health" | python3 -m json.tool || true
            exit 0
        else
            echo "[-] Proxy server is NOT running."
            exit 1
        fi
        ;;

    --stop)
        if [ -f "${PID_FILE}" ]; then
            PID=$(cat "${PID_FILE}")
            echo "[*] Stopping proxy server (PID: ${PID})..."
            kill "${PID}" 2>/dev/null || true
            sleep 1
            if kill -0 "${PID}" 2>/dev/null; then
                kill -9 "${PID}" 2>/dev/null || true
            fi
            rm -f "${PID_FILE}"
            echo "[✓] Proxy server stopped."
        else
            echo "[-] No PID file found."
        fi
        exit 0
        ;;

    --daemon|-d)
        if is_running; then
            echo "[✓] Proxy server is ALREADY running on http://${PROXY_HOST}:${PROXY_PORT}"
            exit 0
        fi
        echo "[*] Starting proxy in background on http://${PROXY_HOST}:${PROXY_PORT}..."
        nohup setsid uvicorn app.main:app --host "${PROXY_HOST}" --port "${PROXY_PORT}" --log-level info >> "${LOG_FILE}" 2>&1 &
        BG_PID=$!
        echo "${BG_PID}" > "${PID_FILE}"
        disown "${BG_PID}" 2>/dev/null || true
        
        # Wait up to 5 seconds for health check
        for i in {1..20}; do
            if curl -s "http://${PROXY_HOST}:${PROXY_PORT}/health" >/dev/null 2>&1; then
                echo "[✓] Proxy started successfully (PID: $(cat "${PID_FILE}")). Logs: ${LOG_FILE}"
                exit 0
            fi
            sleep 0.25
        done
        echo "[!] Proxy started but health check timed out. Check ${LOG_FILE}"
        exit 0
        ;;

    *)
        if is_running; then
            echo "[!] Another proxy instance appears to be running on http://${PROXY_HOST}:${PROXY_PORT}"
            echo "    Run './scripts/start_proxy.sh --stop' first, or start directly."
        fi
        echo "[*] Starting Claude-Gemini proxy server in foreground..."
        exec uvicorn app.main:app --host "${PROXY_HOST}" --port "${PROXY_PORT}" --log-level info
        ;;
esac
