from __future__ import annotations

import os
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent.parent


def vendored_popfe() -> Path:
    """Location of the bundled pop-fe engine.

    Overridable with PSONE2PS3PKG_POPFE for development. In a frozen
    (PyInstaller) build, ``vendor/pop-fe`` is bundled as data and unpacked to
    ``sys._MEIPASS`` at startup instead of living next to the source tree.
    """
    override = os.environ.get("PSONE2PS3PKG_POPFE")
    if override:
        return Path(override).expanduser().resolve()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "vendor" / "pop-fe"
    return PROJECT_ROOT / "vendor" / "pop-fe"


def runtime_dir() -> Path:
    """Writable per-user dir for the mutable engine copy and build artifacts."""
    override = os.environ.get("PSONE2PS3PKG_HOME")
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "psone2ps3pkg"
