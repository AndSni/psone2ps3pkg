"""Prepare and locate the bundled pop-fe engine.

The vendored copy under ``vendor/pop-fe`` is treated as read-only. On first use
it is copied into a writable per-user runtime dir, and the ``pkgcrypt`` C
extension that pop-fe's PS3 packer needs is compiled once (the bundled
``setup.py`` uses ``distutils``, which is gone on Python 3.12+, so we invoke the
compiler directly).

pop-fe also shells out to a bare ``python3`` for two of its own helper scripts
(``sign3.py``, ``PSL1GHT/tools/ps3py/pkg.py``). That is a problem for a frozen
(PyInstaller) build, which has no real Python on the target machine at all. So
instead of relying on whatever ``python3`` happens to be on PATH, we point
pop-fe at a tiny shim that re-invokes *us* with ``--internal-exec <script>
<args...>``; ``cli._internal_exec`` then runs the script in-process with
``runpy``. This works identically whether we are a normal source install or a
frozen binary, since ``own_invocation()`` says how to re-launch ourselves.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from psone2ps3pkg.paths import runtime_dir, vendored_popfe

Logger = Callable[[str], None]


class EngineError(Exception):
    pass


@dataclass
class Engine:
    dir: Path
    env: dict[str, str]

    @property
    def ps3py(self) -> Path:
        return self.dir / "PSL1GHT" / "tools" / "ps3py"

    def invocation(self, script: str, *args: str) -> list[str]:
        """Argv to run a vendored pop-fe script as its own process.

        Goes through --internal-exec (see cli._internal_exec) rather than
        naming a real interpreter, so this works the same whether we are a
        source install or a frozen binary — see own_invocation().
        """
        return [*own_invocation(), "--internal-exec", script, *args]


def ensure_engine(log: Logger = lambda _s: None) -> Engine:
    src = vendored_popfe()
    if not (src / "pop-fe.py").is_file():
        raise EngineError(
            f"Bundled pop-fe not found at {src}. Run from a source checkout, or set "
            "PSONE2PS3PKG_POPFE to a pop-fe directory."
        )

    work = runtime_dir() / "engine"
    stamp = work / ".engine-revision"
    revision = _revision(src)

    if not stamp.exists() or stamp.read_text().strip() != revision:
        log("Preparing conversion engine (one-time copy)…")
        if work.exists():
            shutil.rmtree(work)
        work.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, work)
        stamp.write_text(revision)

    _build_pkgcrypt(work / "PSL1GHT" / "tools" / "ps3py", log)
    shim_dir = _write_shim(runtime_dir() / "shim")

    env = os.environ.copy()
    ps3py = str(work / "PSL1GHT" / "tools" / "ps3py")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (ps3py, env.get("PYTHONPATH", "")) if p)
    env["PATH"] = os.pathsep.join(p for p in (str(shim_dir), env.get("PATH", "")) if p)
    return Engine(dir=work, env=env)


def own_invocation() -> list[str]:
    """How to re-launch this program (source install or frozen binary)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "psone2ps3pkg"]


def _write_shim(shim_dir: Path) -> Path:
    """Write a fake `python3` that re-enters us via --internal-exec.

    pop-fe calls `subprocess.call(['python3', script, *args])`; PATH is set up
    so that resolves here instead of a real interpreter.
    """
    shim_dir.mkdir(parents=True, exist_ok=True)
    invocation = " ".join(shlex.quote(part) for part in own_invocation())
    script = shim_dir / "python3"
    script.write_text(f'#!/bin/sh\nexec {invocation} --internal-exec "$@"\n')
    script.chmod(0o755)
    return shim_dir


def _revision(src: Path) -> str:
    marker = src / ".vendor-revision"
    if marker.is_file():
        return marker.read_text().strip()
    # Fall back to a cheap content signature.
    newest = max((p.stat().st_mtime for p in src.rglob("*.py")), default=0.0)
    return f"mtime:{newest:.0f}"


def _build_pkgcrypt(ps3py: Path, log: Logger) -> None:
    if next(ps3py.glob("pkgcrypt*.so"), None) is not None:
        return

    cc = os.environ.get("CC") or shutil.which("gcc") or shutil.which("cc")
    if not cc:
        raise EngineError(
            "A C compiler is needed once to build pkgcrypt.\n"
            "  Fedora:  sudo dnf install gcc python3-devel\n"
            "  Debian:  sudo apt install build-essential python3-dev"
        )

    include = sysconfig.get_path("include")
    platinclude = sysconfig.get_path("platinclude")
    if not Path(include, "Python.h").is_file():
        raise EngineError(
            f"Python.h missing under {include}. Install the Python development headers.\n"
            "  Fedora:  sudo dnf install python3-devel\n"
            "  Debian:  sudo apt install python3-dev"
        )

    out = ps3py / "pkgcrypt.so"
    cmd = [cc, "-shared", "-fPIC", "-O2", f"-I{include}", f"-I{platinclude}",
           "crypt.c", "-o", out.name]
    log("Building pkgcrypt (one-time native build)…")
    result = subprocess.run(cmd, cwd=ps3py, capture_output=True, text=True)
    if result.returncode != 0 or not out.is_file():
        raise EngineError("Failed to build pkgcrypt:\n" + (result.stderr or result.stdout))


def find_chdman() -> str | None:
    return shutil.which("chdman")


def game_title(serial: str | None) -> str | None:
    """Look up a proper game title in pop-fe's offline game database."""
    if not serial:
        return None
    src = vendored_popfe()
    key = serial.replace("-", "").replace("_", "").upper()
    added = str(src) not in sys.path
    if added:
        sys.path.insert(0, str(src))
    try:
        import gamedb  # type: ignore

        entry = gamedb.games.get(key)
    except Exception:  # noqa: BLE001 - db is a nice-to-have, never fatal
        return None
    finally:
        if added:
            try:
                sys.path.remove(str(src))
            except ValueError:
                pass
    if isinstance(entry, dict):
        title = entry.get("title")
        return str(title) if title else None
    return None
