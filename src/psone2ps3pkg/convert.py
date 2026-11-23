"""Drive the bundled pop-fe engine to turn a PS1 game into a console package.

Targets:

* ``ps3`` - a PS3 PS One Classic ``.pkg`` (install from the XMB).
* ``psp`` - a PSP / PS Vita (Adrenaline) ``EBOOT.PBP`` under ``PSP/GAME/<ID>/``.
* ``ps2`` - a POPStarter ``.VCD`` under ``POPS/`` (+ cover art under ``ART/``).

CD-audio is kept as raw tracks (``--whole-disk`` / ``--psp-use-cdda``) so no
ATRAC3 encoder is needed and no music is lost.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from psone2ps3pkg.disc import (
    DiscError,
    DiscImage,
    _Track,
    cue_file_entries,
    cue_needs_rewrite,
    resolve_cue_file,
)
from psone2ps3pkg.engine import Engine, EngineError, ensure_engine
from psone2ps3pkg.library import Game

Logger = Callable[[str], None]


class PkgError(Exception):
    pass


class Cancelled(PkgError):
    pass


class Target(str, Enum):
    PS3 = "ps3"
    PSP = "psp"
    PS2 = "ps2"

    @property
    def label(self) -> str:
        return {"ps3": "PS3 PKG", "psp": "PSP / VITA EBOOT", "ps2": "PS2 POPSTARTER"}[self.value]


@dataclass
class Options:
    target: Target = Target.PS3
    output_dir: Path | None = None  # None: next to the game's first disc
    ps1_newemu: bool = False  # PS3 only
    artwork: bool = True  # download cover / background art


@dataclass
class Result:
    game: Game
    outputs: list[Path] = field(default_factory=list)
    error: str | None = None
    content_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


# Lines pop-fe prints when atracdenc is missing. With raw CD-audio they are
# wrong (the audio is kept), so they are hidden from the log.
_NOISE = re.compile(
    r"^(X{10,}|atracdenc not found\.?|Can not convert CDDA tracks\.?|"
    r"Creating EBOOT\.PBP without support for CDDA audio\.?|"
    r"Please see README file for how to install atracdenc|"
    r"Converting .*TRACK_\d+_\d+\.wav to .*\.aea|"
    # optional pop-fe modules this app does not use
    r"(rarfile|PyPDF2) is not installed\.|You should install requests by running:|"
    r"pip3 install \w+|You need to install python module pillow and opencv-contrib-python|"
    r"PLEASE REPORT THIS MD5 AND GAME SO I CAN ADD IT TO THE DATABASE|"
    r"Failed to parse PDF\.)$"
)

# pop-fe output line -> rough fraction of one game's build.
_STAGES = [
    ("Processing ", 0.05),
    ("MD5 FINGERPRINT", 0.12),
    ("Title: ", 0.22),
    ("Create EBOOT.PBP", 0.30),
    ("Create PBP file", 0.35),
    ("Install VCD", 0.40),
    ("Create ISO.BIN.DAT", 0.50),
    ("Signing ", 0.62),
    ("Create ISO.BIN.EDAT", 0.72),
    ("Create PKG", 0.82),
    ("Finished.", 0.98),
]


def stage_of(line: str) -> float | None:
    """How far a build is, judged from one pop-fe output line."""
    for prefix, frac in _STAGES:
        if line.startswith(prefix):
            return frac
    return None


def output_dir_for(game: Game, options: Options) -> Path:
    return (options.output_dir or game.source_dir).expanduser()


def expected_output(game: Game, options: Options) -> Path:
    """The main file or folder a build will produce (for display)."""
    out = output_dir_for(game, options)
    if options.target is Target.PS3:
        return out / f"{sanitise(game.title)} [{game.serial or 'PS1'}].pkg"
    if options.target is Target.PSP:
        return out / "PSP" / "GAME" / (game.serial or "PS1").replace("-", "")
    return out / "POPS"


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def build_game(
    game: Game,
    options: Options,
    on_line: Logger = lambda _s: None,
    cancel: threading.Event | None = None,
) -> Result:
    """Build one game. Never raises for a per-game failure: see Result.error."""
    try:
        return Result(game, *_build(game, options, on_line, cancel or threading.Event()))
    except Cancelled:
        return Result(game, error="cancelled")
    except (PkgError, DiscError, EngineError, OSError) as exc:
        return Result(game, error=str(exc))


def _build(game: Game, options: Options, on_line: Logger, cancel: threading.Event):
    engine = ensure_engine(on_line)
    out_dir = output_dir_for(game, options)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PkgError(f"cannot create output folder {out_dir}: {exc}") from exc
    if not os.access(out_dir, os.W_OK):
        raise PkgError(f"output folder is not writable: {out_dir}")

    work = engine.dir / "pop-fe-work"
    stage = engine.dir / "pop-fe-input"
    for d in (work, stage):
        shutil.rmtree(d, ignore_errors=True)

    try:
        inputs = [str(_stage_input(d.image, stage / str(i), on_line))
                  for i, d in enumerate(game.discs)]
        args = ["--no-libcrypt"]
        if not options.artwork:
            args.append("--force-no-assets")

        started = time.time()
        if options.target is Target.PS3:
            pkg = expected_output(game, options)
            pkg.unlink(missing_ok=True)
            args += ["--whole-disk", "--ps3-pkg", str(pkg)]
            if options.ps1_newemu:
                args.append("--ps1-newemu")
            _run(engine.invocation("pop-fe.py", *args, *inputs), engine, on_line, cancel,
                 cleanup=lambda: pkg.unlink(missing_ok=True))
            if not pkg.is_file():
                raise PkgError("pop-fe finished without producing a PKG")
            meta = read_pkg_header(pkg)
            if meta["magic"] != b"\x7fPKG":
                pkg.unlink(missing_ok=True)
                raise PkgError("output file is not a valid PS3 PKG (bad magic)")
            return [pkg], None, meta["content_id"]

        if options.target is Target.PSP:
            dest = out_dir / "PSP" / "GAME"
            dest.mkdir(parents=True, exist_ok=True)
            args += ["--psp-use-cdda", "--psp-dir", str(dest)]
            _run(engine.invocation("pop-fe.py", *args, *inputs), engine, on_line, cancel,
                 cleanup=lambda: _remove_new(dest, "EBOOT.PBP", started))
            made = _new_files(dest, "EBOOT.PBP", started)
            if not made:
                raise PkgError("pop-fe finished without producing an EBOOT.PBP")
            for eboot in made:
                with open(eboot, "rb") as fh:
                    magic = fh.read(4)
                if magic != b"\x00PBP":
                    raise PkgError(f"{eboot} is not a valid PBP file")
            return made, None, None

        # PS2 / POPStarter
        for sub in ("POPS", "ART"):
            (out_dir / sub).mkdir(parents=True, exist_ok=True)
        args += ["--ps2-dir", str(out_dir)]
        _run(engine.invocation("pop-fe.py", *args, *inputs), engine, on_line, cancel,
             cleanup=lambda: _remove_new(out_dir / "POPS", "*.VCD", started))
        made = _new_files(out_dir / "POPS", "*.VCD", started)
        if not made:
            raise PkgError("pop-fe finished without producing a .VCD")
        return made, None, None
    finally:
        for d in (work, stage):
            shutil.rmtree(d, ignore_errors=True)


def _new_files(root: Path, pattern: str, since: float) -> list[Path]:
    return sorted(p for p in root.rglob(pattern) if p.is_file() and p.stat().st_mtime >= since - 2)


def _remove_new(root: Path, pattern: str, since: float) -> None:
    for p in _new_files(root, pattern, since):
        p.unlink(missing_ok=True)
        try:
            p.parent.rmdir()  # only if now empty
        except OSError:
            pass


def _run(
    cmd: list[str],
    engine: Engine,
    on_line: Logger,
    cancel: threading.Event,
    cleanup: Callable[[], None] = lambda: None,
) -> None:
    if cancel.is_set():
        raise Cancelled()
    on_line("$ " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=engine.dir,
        env=engine.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        errors="replace",
        bufsize=1,
        start_new_session=True,  # one process group: cancel kills helpers too
    )

    def watch() -> None:
        while proc.poll() is None:
            if cancel.wait(0.2):
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    tail: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if _NOISE.match(line.strip()):
            continue
        on_line(line)
        tail.append(line)
        del tail[:-30]
    rc = proc.wait()
    watcher.join(timeout=1)
    if cancel.is_set() or rc != 0:
        cleanup()  # no half-written package left behind
    if cancel.is_set():
        raise Cancelled()
    if rc != 0:
        raise PkgError(f"pop-fe exited with status {rc}.\nLast lines:\n" + "\n".join(tail[-12:]))


# --------------------------------------------------------------------------- #
# input staging
# --------------------------------------------------------------------------- #
def _stage_input(disc: DiscImage, stage: Path, on_line: Logger) -> Path:
    """Return a path pop-fe accepts (.cue / .bin / .img / .chd)."""
    if disc.kind == "cue":
        if not cue_needs_rewrite(disc.primary):
            return disc.primary
        # Windows paths or wrong letter case in FILE lines: write a copy of the
        # cue that names the real track files by absolute path.
        stage.mkdir(parents=True, exist_ok=True)
        text = disc.primary.read_text(errors="replace")
        for name in cue_file_entries(disc.primary):
            real = resolve_cue_file(disc.primary, name)
            text = re.sub(
                r'(FILE\s+)("?)' + re.escape(name) + r'\2',
                lambda m, real=real: f'{m.group(1)}"{real}"',
                text,
                count=1,
            )
        fixed = stage / disc.primary.name
        fixed.write_text(text)
        on_line(f"Fixed track paths in {disc.primary.name}.")
        return fixed
    if disc.kind in ("bin", "chd"):
        return disc.primary
    if disc.kind == "iso":
        stage.mkdir(parents=True, exist_ok=True)
        t = _Track(disc.primary)
        mode = "MODE1/2352" if t.offset == 16 else "MODE2/2352"
        t.close()
        cue = stage / (disc.primary.stem + ".cue")
        cue.write_text(f'FILE "{disc.primary}" BINARY\n  TRACK 01 {mode}\n    INDEX 01 00:00:00\n')
        on_line(f"Wrote a temporary cue for the ISO ({mode}).")
        return cue
    raise PkgError(f"unsupported image kind: {disc.kind}")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def read_pkg_header(path: Path) -> dict:
    with open(path, "rb") as fh:
        head = fh.read(0x60)
    return {
        "magic": head[:4],
        "item_count": struct.unpack_from(">I", head, 0x14)[0],
        "total_size": struct.unpack_from(">Q", head, 0x18)[0],
        "content_id": head[0x30:0x54].split(b"\x00")[0].decode("ascii", "replace"),
    }


def sanitise(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:100] or "PlayStation Game"


__all__ = [
    "Target",
    "Options",
    "Result",
    "PkgError",
    "build_game",
    "expected_output",
    "output_dir_for",
]
