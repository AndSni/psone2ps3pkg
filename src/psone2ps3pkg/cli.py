"""Command-line entry point.

No arguments          -> open the graphical window.
Paths                 -> open the window and scan those paths at once.
--headless PATH...    -> scan and build without a window.
"""

from __future__ import annotations

import argparse
import os
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

    # Our parent reads this output through a pipe: flush line by line so the
    # progress log is live (a frozen build may ignore PYTHONUNBUFFERED).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

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
    """Normalise whatever a terminal or file manager produced from a drop."""
    s = text.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    if s.startswith("file://"):
        s = unquote(urlparse(s).path)
    s = s.replace("\\ ", " ").replace("\\\\", "\\")
    s = s.rstrip("/") or "/"
    return str(Path(s).expanduser())


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
                or os.environ.get("SDL_VIDEODRIVER"))


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] == "--internal-exec":
        return _internal_exec(raw[1:])

    parser = argparse.ArgumentParser(
        prog="psone2ps3pkg",
        description="Turn PlayStation 1 disc images into PS3 PKG, PSP/Vita EBOOT or PS2 POPStarter files.",
    )
    parser.add_argument("paths", nargs="*", help="Disc image files or folders (a folder may hold many games).")
    parser.add_argument("--headless", action="store_true", help="Build without a window.")
    parser.add_argument("--list", action="store_true", help="Only list the games found, then exit.")
    parser.add_argument("-t", "--target", choices=["ps3", "psp", "ps2"], default="ps3",
                        help="Output type for --headless (default: ps3).")
    parser.add_argument("-o", "--out", help="Output folder (default: next to each game).")
    parser.add_argument("--newemu", action="store_true", help="PS3: use ps1_newemu instead of ps1_netemu.")
    parser.add_argument("--no-art", action="store_true", help="Do not download cover art.")
    parser.add_argument("-y", "--yes", action="store_true", help="Do not ask for confirmation.")
    parser.add_argument("--check", action="store_true", help="Check that the build tools are present, then exit.")
    parser.add_argument("--install", action="store_true", help="Add the app to your desktop menu (~/.local).")
    parser.add_argument("--uninstall", action="store_true", help="Remove the menu entry added by --install.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(raw)

    paths = [clean_drop_path(p) for p in args.paths]

    if args.check:
        return _check()

    if args.install or args.uninstall:
        from psone2ps3pkg import desktop

        return desktop.install() if args.install else desktop.uninstall()

    if args.headless or args.list:
        if not paths:
            parser.error("--headless and --list need at least one path")
        return _headless(paths, args)

    if not has_display():
        parser.error("no graphical display found; use --headless PATH to build without a window")

    from psone2ps3pkg.gui.app import run as run_gui

    return run_gui(paths)


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


def _headless(paths: list[str], args: argparse.Namespace) -> int:
    from psone2ps3pkg.convert import Options, Target, build_game, expected_output
    from psone2ps3pkg.library import scan_paths

    lib = scan_paths(paths)
    options = Options(
        target=Target(args.target),
        output_dir=Path(args.out).expanduser().resolve() if args.out else None,
        ps1_newemu=args.newemu,
        artwork=not args.no_art,
    )

    for i, game in enumerate(lib.games, 1):
        discs = len(game.discs)
        print(f"[{i}] {game.title}  {', '.join(game.serials)}  {game.region}  "
              f"{discs} disc{'s' if discs > 1 else ''}  ({game.kinds})")
        for d in game.discs:
            print(f"      {d.image.primary}")
        for w in game.warnings:
            print(f"      note: {w}")
    for s in lib.skipped:
        print(f"skipped: {s.path.name}: {s.reason}")

    if not lib.games:
        print("error: no PlayStation 1 games found", file=sys.stderr)
        return 2
    if args.list:
        return 0

    print(f"\nTarget: {options.target.label}")
    for game in lib.games:
        print(f"  -> {expected_output(game, options)}")

    if not args.yes:
        try:
            reply = input(f"\nBuild {len(lib.games)} game(s)? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Aborted.")
            return 1

    failed = 0
    for game in lib.games:
        print(f"\n== {game.title} ==")
        result = build_game(game, options, on_line=print)
        if result.ok:
            for p in result.outputs:
                print(f"Done: {p}")
            if result.content_id:
                print(f"      content-id {result.content_id}")
        else:
            failed += 1
            print(f"FAILED: {result.error}", file=sys.stderr)
    return 0 if failed == 0 else 2
