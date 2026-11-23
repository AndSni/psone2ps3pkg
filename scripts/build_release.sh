#!/usr/bin/env bash
# Build the single-file Linux AppImage:  dist/psone2ps3pkg-<version>-x86_64.AppImage
#
# 1. An isolated venv installs the app + PyInstaller.
# 2. A private copy of vendor/pop-fe gets pkgcrypt precompiled against the exact
#    interpreter PyInstaller embeds (end users need no compiler).
# 3. PyInstaller builds a one-folder bundle (fast start, no unpack to /tmp).
# 4. appimagetool wraps that folder into one executable file.
#
# For wide glibc compatibility, run this on an old base (CI uses Debian 11,
# glibc 2.31). Needs: python3 (3.10+) with headers, gcc, curl or wget.
# PYTHON=/path/to/python3 selects the interpreter.
set -euo pipefail
cd "$(dirname "$0")/.."

REPO_ROOT="$(pwd)"
BUILD="$REPO_ROOT/build/frozen"
VENV="$BUILD/venv"
STAGE_VENDOR="$BUILD/vendor/pop-fe"
APPDIR="$BUILD/AppDir"
TOOLS="$REPO_ROOT/build/tools"
PY="${PYTHON:-python3}"
ARCH=x86_64

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/psone2ps3pkg/__init__.py)"
OUT="dist/psone2ps3pkg-$VERSION-$ARCH.AppImage"

rm -rf "$BUILD" "$OUT"
mkdir -p "$BUILD" "$TOOLS" dist

echo "== build venv =="
"$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet .
"$VENV/bin/pip" install --quiet "pyinstaller>=6.10"

echo "== staging vendor/pop-fe and precompiling pkgcrypt =="
mkdir -p "$(dirname "$STAGE_VENDOR")"
cp -r vendor/pop-fe "$STAGE_VENDOR"
find "$STAGE_VENDOR" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -f "$STAGE_VENDOR"/PSL1GHT/tools/ps3py/pkgcrypt*.so
"$VENV/bin/python" - "$STAGE_VENDOR/PSL1GHT/tools/ps3py" <<'PY'
import subprocess
import sys
import sysconfig
from pathlib import Path

ps3py = Path(sys.argv[1])
include = sysconfig.get_path("include")
platinclude = sysconfig.get_path("platinclude")
cmd = ["cc", "-shared", "-fPIC", "-O2", f"-I{include}", f"-I{platinclude}", "crypt.c", "-o", "pkgcrypt.so"]
subprocess.run(cmd, cwd=ps3py, check=True)
print("pkgcrypt.so built for", sys.executable)
PY

echo "== pyinstaller onedir =="
"$VENV/bin/pyinstaller" \
    --noconfirm \
    --onedir \
    --name psone2ps3pkg \
    --add-data "$STAGE_VENDOR:vendor/pop-fe" \
    --add-data "$REPO_ROOT/src/psone2ps3pkg/gui/icon.png:psone2ps3pkg/gui" \
    --add-data "$REPO_ROOT/src/psone2ps3pkg/gui/fonts:psone2ps3pkg/gui/fonts" \
    --collect-all pygame \
    --collect-all PIL \
    --collect-all Crypto \
    --collect-all pycdlib \
    --collect-all ecdsa \
    --collect-all requests \
    --collect-all urllib3 \
    --collect-all certifi \
    --collect-all idna \
    --collect-all charset_normalizer \
    --hidden-import psone2ps3pkg.gui.app \
    --exclude-module tkinter \
    --distpath "$BUILD/dist" \
    --workpath "$BUILD/work" \
    --specpath "$BUILD" \
    src/psone2ps3pkg/__main__.py

echo "== AppDir =="
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/icons/hicolor/256x256/apps" "$APPDIR/usr/share/applications"
cp -a "$BUILD/dist/psone2ps3pkg" "$APPDIR/usr/lib/psone2ps3pkg"
# Base libraries every desktop has, in a newer version than the build box.
# Bundled old copies shadow the host's and crash the host GL driver (Mesa /
# LLVM need a newer libstdc++) as soon as the window opens.
for lib in libstdc++.so.6 libgcc_s.so.1 libz.so.1; do
    rm -f "$APPDIR/usr/lib/psone2ps3pkg/_internal/$lib"
done
cat > "$APPDIR/AppRun" <<'SH'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/lib/psone2ps3pkg/psone2ps3pkg" "$@"
SH
chmod 755 "$APPDIR/AppRun"
"$VENV/bin/python" -c "from psone2ps3pkg.desktop import desktop_entry; print(desktop_entry(), end='')" \
    > "$APPDIR/psone2ps3pkg.desktop"
cp "$APPDIR/psone2ps3pkg.desktop" "$APPDIR/usr/share/applications/"
cp src/psone2ps3pkg/gui/icon.png "$APPDIR/psone2ps3pkg.png"
cp src/psone2ps3pkg/gui/icon.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/psone2ps3pkg.png"
ln -sf psone2ps3pkg.png "$APPDIR/.DirIcon"

echo "== appimagetool =="
TOOL="$TOOLS/appimagetool-$ARCH.AppImage"
if [ ! -x "$TOOL" ]; then
    url="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    if command -v curl >/dev/null; then curl -fsSL -o "$TOOL" "$url"; else wget -q -O "$TOOL" "$url"; fi
    chmod +x "$TOOL"
fi
# Extract-and-run: works in containers / CI without FUSE.
APPIMAGE_EXTRACT_AND_RUN=1 ARCH=$ARCH "$TOOL" --no-appstream "$APPDIR" "$OUT"

echo
echo "Built: $OUT ($(du -h "$OUT" | cut -f1))"
APPIMAGE_EXTRACT_AND_RUN=1 "$OUT" --version
