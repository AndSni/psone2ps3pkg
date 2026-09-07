#!/usr/bin/env bash
# Install the psone2ps3pkg binary to ~/.local/bin and a menu launcher to
# ~/.local/share/applications. Works from a source checkout (uses dist/) or
# from an unpacked release (binary + .desktop sitting next to this script).
#
#   ./install.sh [path-to-binary]     install
#   ./install.sh --uninstall          remove
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
BIN_DEST="$BIN_DIR/psone2ps3pkg"
DESKTOP_DEST="$APP_DIR/psone2ps3pkg.desktop"

if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "-u" ]; then
    rm -fv "$BIN_DEST" "$DESKTOP_DEST"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "uninstalled."
    exit 0
fi

BIN_SRC="${1:-}"
if [ -z "$BIN_SRC" ]; then
    for c in "$here/psone2ps3pkg" "$here/dist/psone2ps3pkg" "$here/../dist/psone2ps3pkg"; do
        [ -x "$c" ] && BIN_SRC="$c" && break
    done
fi
if [ -z "$BIN_SRC" ] || [ ! -x "$BIN_SRC" ]; then
    echo "error: no psone2ps3pkg binary found." >&2
    echo "usage: $0 [path-to-binary]   (or build one first with scripts/build_release.sh)" >&2
    exit 1
fi

DESKTOP_SRC=""
for c in "$here/psone2ps3pkg.desktop" "$here/packaging/psone2ps3pkg.desktop" "$here/../packaging/psone2ps3pkg.desktop"; do
    [ -f "$c" ] && DESKTOP_SRC="$c" && break
done

mkdir -p "$BIN_DIR" "$APP_DIR"
install -m 755 "$BIN_SRC" "$BIN_DEST"
echo "installed: $BIN_DEST"

if [ -n "$DESKTOP_SRC" ]; then
    sed -e "s#^Exec=.*#Exec=$BIN_DEST#" -e "s#^TryExec=.*#TryExec=$BIN_DEST#" \
        "$DESKTOP_SRC" > "$DESKTOP_DEST"
    chmod 644 "$DESKTOP_DEST"
    echo "installed: $DESKTOP_DEST"
    command -v update-desktop-database >/dev/null && update-desktop-database "$APP_DIR" 2>/dev/null || true
fi

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "note: $BIN_DIR is not on your PATH — add it to run 'psone2ps3pkg' from any shell." ;;
esac
