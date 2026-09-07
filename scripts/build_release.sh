#!/usr/bin/env bash
# Build a standalone Linux onefile executable with PyInstaller.
#
# The committed vendor/pop-fe stays source-only (pkgcrypt is normally compiled
# on first run — see engine.py). For a frozen binary there is no compiler on
# the end user's machine, so this script stages a private copy of vendor/,
# precompiles pkgcrypt against the exact interpreter PyInstaller is about to
# embed, and bundles that staged copy instead. engine.py finds the prebuilt
# .so at runtime and skips compilation.
set -euo pipefail
cd "$(dirname "$0")/.."

REPO_ROOT="$(pwd)"
BUILD="$REPO_ROOT/build/frozen"
VENV="$BUILD/venv"
STAGE_VENDOR="$BUILD/vendor/pop-fe"

rm -rf "$BUILD" dist/psone2ps3pkg
mkdir -p "$BUILD"

echo "== build venv =="
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e .
"$VENV/bin/pip" install --quiet "pyinstaller>=6.10"

echo "== staging vendor/pop-fe and precompiling pkgcrypt =="
mkdir -p "$(dirname "$STAGE_VENDOR")"
cp -r vendor/pop-fe "$STAGE_VENDOR"
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

echo "== pyinstaller onefile =="
"$VENV/bin/pyinstaller" \
    --onefile \
    --name psone2ps3pkg \
    --add-data "$STAGE_VENDOR:vendor/pop-fe" \
    --collect-all textual \
    --collect-all PIL \
    --collect-all Crypto \
    --collect-all pycdlib \
    --collect-all ecdsa \
    --collect-all requests \
    --collect-all urllib3 \
    --collect-all certifi \
    --collect-all idna \
    --collect-all charset_normalizer \
    --distpath dist \
    --workpath "$BUILD/work" \
    --specpath "$BUILD" \
    src/psone2ps3pkg/__main__.py

echo
echo "Built: dist/psone2ps3pkg"
dist/psone2ps3pkg --version
