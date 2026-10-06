#!/usr/bin/env bash
# ==============================================================================
# create_desktop_shortcut.sh - Auto-detects Linux terminal emulator and installs
# XDG/FreeDesktop .desktop shortcuts in ~/Desktop and ~/.local/share/applications/
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LAUNCHER_SCRIPT="${SCRIPT_DIR}/launch_claude.sh"
ICON_SOURCE="${PROJECT_ROOT}/assets/claude-icon.svg"

DESKTOP_DIR="${HOME}/Desktop"
USER_APPS_DIR="${HOME}/.local/share/applications"
USER_ICONS_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"

mkdir -p "${DESKTOP_DIR}"
mkdir -p "${USER_APPS_DIR}"
mkdir -p "${USER_ICONS_DIR}"

# 1. Install icon into standard XDG icon directory and keep project asset
cp -f "${ICON_SOURCE}" "${USER_ICONS_DIR}/claude-code.svg"
ICON_PATH="${USER_ICONS_DIR}/claude-code.svg"

# 2. Detect installed terminal emulator in Arch Linux
detect_terminal() {
    # If user explicitly specified TERMINAL in environment
    if [ -n "${TERMINAL}" ] && command -v "${TERMINAL}" >/dev/null 2>&1; then
        echo "${TERMINAL}"
        return
    fi

    # Detection in priority order: kitty -> alacritty -> konsole -> gnome-terminal -> etc.
    for term in kitty alacritty konsole gnome-terminal xfce4-terminal foot wezterm xterm; do
        if command -v "${term}" >/dev/null 2>&1; then
            echo "${term}"
            return
        fi
    done

    echo "xterm"
}

TERM_BIN=$(detect_terminal)
echo "[*] Detected terminal emulator: ${TERM_BIN}"

# Construct terminal-specific launch command
build_exec_command() {
    local term="$1"
    local launcher="$2"

    case "${term}" in
        konsole)
            echo "konsole -e \"${launcher}\""
            ;;
        gnome-terminal)
            echo "gnome-terminal -- \"${launcher}\""
            ;;
        kitty)
            echo "kitty -e \"${launcher}\""
            ;;
        alacritty)
            echo "alacritty -e \"${launcher}\""
            ;;
        xfce4-terminal)
            echo "xfce4-terminal -e \"${launcher}\""
            ;;
        foot)
            echo "foot \"${launcher}\""
            ;;
        wezterm)
            echo "wezterm start -- \"${launcher}\""
            ;;
        *)
            echo "${term} -e \"${launcher}\""
            ;;
    esac
}

EXEC_CMD=$(build_exec_command "${TERM_BIN}" "${LAUNCHER_SCRIPT}")

# 3. Create .desktop file content
DESKTOP_CONTENT="[Desktop Entry]
Version=1.0
Type=Application
Name=Claude Code (Gemini)
GenericName=AI Coding Assistant
Comment=Claude Code CLI emulated via local Google Gemini Proxy
Exec=${EXEC_CMD}
Icon=${ICON_PATH}
Terminal=false
Categories=Development;IDE;
Keywords=claude;code;ai;gemini;terminal;arch;
StartupNotify=true
"

TARGET_DESKTOP="${DESKTOP_DIR}/Claude-Code.desktop"
TARGET_APP="${USER_APPS_DIR}/claude-code.desktop"

echo "${DESKTOP_CONTENT}" > "${TARGET_DESKTOP}"
echo "${DESKTOP_CONTENT}" > "${TARGET_APP}"

# 4. Make scripts and desktop entries executable
chmod +x "${LAUNCHER_SCRIPT}"
chmod +x "${SCRIPT_DIR}/start_proxy.sh"
chmod +x "${SCRIPT_DIR}/create_desktop_shortcut.sh"
chmod +x "${TARGET_DESKTOP}"
chmod +x "${TARGET_APP}"

# Mark trusted in KDE / GNOME if gio or kdialog exists
if command -v gio >/dev/null 2>&1; then
    gio set "${TARGET_DESKTOP}" metadata::trusted true 2>/dev/null || true
fi

# 5. Validate desktop file syntax with desktop-file-validate if present
if command -v desktop-file-validate >/dev/null 2>&1; then
    echo "[*] Validating .desktop entry syntax..."
    desktop-file-validate "${TARGET_APP}" || true
fi

# 6. Update desktop applications database
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${USER_APPS_DIR}" 2>/dev/null || true
fi

echo "[✓] Successfully installed desktop shortcuts:"
echo "    - Desktop icon: ${TARGET_DESKTOP}"
echo "    - Application menu: ${TARGET_APP}"
echo "    - Configured Terminal: ${TERM_BIN}"
