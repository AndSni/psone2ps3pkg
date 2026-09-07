"""Command-line entry point.

No arguments  -> launch the drag-and-drop TUI.
A directory   -> build the PKG for that folder without the TUI.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from psone2ps3pkg import __version__


def _internal_exec(argv: list[str]) -> int:
    """Run a vendored pop-fe helper script in-process.

    A standalone (PyInstaller) build has no real `python3` on the target
    machine, but pop-fe's PS3 packer shells out to one for sign3.py and
    pkg.py. `engine.py` points those calls at a shim that re-invokes us with
    `--internal-exec <script> <args...>`; we run the script here with runpy so
    it behaves exactly as if a real interpreter had executed it, no subprocess
    needed. See engine.own_invocation() / engine._write_shim().
    """
    if not argv:
        print("--internal-exec requires a script path", file=sys.stderr)
        return 2
    script, *rest = argv
    script_path = str(Path(script).resolve())
    script_dir = str(Path(script_path).parent)

    old_argv = sys.argv
    old_path = list(sys.path)
    sys.argv = [script, *rest]
    # runpy is documented to add the script's directory to sys.path itself,
    # but that relies on sys.path[0] meaning "cwd" the way a real interpreter
    # resolves it -- inside a frozen build sys.path[0] is the bundle, not cwd,
    # so pop-fe's sibling imports (e.g. `from bchunk import bchunk`) silently
    # fail. Do it ourselves with an absolute path instead of trusting that.
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        runpy.run_path(script_path, run_name="__main__")
    except SystemExit as exc:
        code = exc.code
        return code if isinstance(code, int) else (0 if code is None else 1)
    finally:
        sys.argv = old_argv
        sys.path[:] = old_path
    return 0


def clean_drop_path(text: str) -> str:
    """Normalise whatever a terminal produced from a drag-and-drop."""
    s = text.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    if s.startswith("file://"):
        s = unquote(urlparse(s).path)
    s = s.replace("\\ ", " ").replace("\\\\", "\\")
    s = s.rstrip("/") or "/"
    return str(Path(s).expanduser())


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] == "--internal-exec":
        return _internal_exec(raw[1:])

    parser = argparse.ArgumentParser(
        prog="psone2ps3pkg",
        description="Turn a PlayStation 1 disc image into a PS3 PS One Classic .pkg.",
    )
    parser.add_argument("directory", nargs="?", help="Folder containing the PS1 image (cue/bin, iso, or chd).")
    parser.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation (headless mode).")
    parser.add_argument("--check", action="store_true", help="Check that the build tools are present, then exit.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    if args.check:
        return _check()

    if args.directory:
        return _headless(clean_drop_path(args.directory), assume_yes=args.yes)

    if not sys.stdout.isatty():
        parser.error("no directory given and not attached to a terminal")

    from psone2ps3pkg.tui import run as run_tui

    run_tui()
    return 0


def _check() -> int:
    from psone2ps3pkg.engine import ensure_engine, find_chdman

    ok = True
    try:
        engine = ensure_engine(print)
        print(f"engine ready:   {engine.dir}")
    except Exception as exc:  # noqa: BLE001 - report any setup failure verbatim
        print(f"engine FAILED:  {exc}")
        ok = False

    chdman = find_chdman()
    print(f"chdman:         {chdman or 'not found (only needed for .chd input)'}")
    return 0 if ok else 1


def _headless(directory: str, assume_yes: bool) -> int:
    from psone2ps3pkg.convert import PkgError, build_pkg, plan_directory
    from psone2ps3pkg.disc import DiscError

    try:
        plan = plan_directory(directory)
    except (DiscError, PkgError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Directory : {plan.directory}")
    print(f"Image     : {plan.disc.primary.name}  ({plan.disc.kind})")
    print(f"Serial    : {plan.info.serial or 'unknown'}   Region: {plan.info.region}")
    print(f"Output    : {plan.output.name}")
    for w in plan.warnings:
        print(f"note      : {w}")

    if not assume_yes:
        try:
            reply = input("\nBuild this PKG? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    try:
        result = build_pkg(plan.directory, on_line=print, plan=plan)
    except (DiscError, PkgError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 2

    mib = result.size / (1024 * 1024)
    print(f"\nDone: {result.path}")
    print(f"      {mib:.1f} MiB   content-id {result.content_id}")
    return 0
