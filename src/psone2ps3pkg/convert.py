"""Drive the bundled pop-fe engine to turn a PS1 disc image into a PS3 PKG."""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from psone2ps3pkg.disc import (
    DiscError,
    DiscImage,
    DiscInfo,
    identify,
    scan_directory,
)
from psone2ps3pkg.engine import (
    Engine,
    EngineError,
    ensure_engine,
    find_chdman,
    game_title,
)

Logger = Callable[[str], None]


class PkgError(Exception):
    pass


@dataclass
class Plan:
    directory: Path
    disc: DiscImage
    info: DiscInfo
    output: Path
    warnings: list[str]


@dataclass
class PkgResult:
    path: Path
    content_id: str
    size: int
    plan: Plan


# --------------------------------------------------------------------------- #
# planning (cheap, no engine) — used by the TUI to preview before committing
# --------------------------------------------------------------------------- #
def plan_directory(directory: str | Path) -> Plan:
    directory = Path(directory).expanduser().resolve()
    disc = scan_directory(directory)
    warnings: list[str] = []

    extracted_track: Path | None = None
    try:
        if disc.kind == "chd":
            if find_chdman() is None:
                raise PkgError(
                    "This is a .chd and 'chdman' is not installed, so it cannot be read.\n"
                    "  Fedora:  sudo dnf install mame-tools\n"
                    "  Debian:  sudo apt install mame-tools"
                )
            extracted_track = _peek_chd_track(disc)

        info = identify(disc, chd_extracted_track=extracted_track)
    finally:
        if extracted_track is not None:
            shutil.rmtree(extracted_track.parent, ignore_errors=True)

    if info.note:
        warnings.append(info.note)
    if disc.kind == "bin" and not disc.has_cue:
        warnings.append(
            "No .cue alongside this image — if the game has CD-audio tracks they will "
            "not be included. Provide the .cue for full support."
        )
    if disc.kind == "iso" and info.sector_size == 2048:
        raise PkgError(
            "This .iso stores 2048-byte sectors (data only). PS1 packaging needs the raw "
            "2352-byte disc image so CD audio and FMV survive — re-dump as BIN/CUE or CHD."
        )
    if info.region == "PAL":
        warnings.append(
            "PAL disc: LibCrypt executable patching is skipped; pop-fe still writes the "
            "subchannel protection data. If a specific title stalls in-game, that is why."
        )

    info.title = info.title or game_title(info.serial)
    title = info.title or _clean_stem(disc.primary.stem)
    label = info.serial or "PS1"
    output = directory / f"{_sanitise(title)} [{label}].pkg"

    return Plan(directory=directory, disc=disc, info=info, output=output, warnings=warnings)


# --------------------------------------------------------------------------- #
# build (runs the engine)
# --------------------------------------------------------------------------- #
def build_pkg(
    directory: str | Path,
    on_line: Logger = lambda _s: None,
    plan: Plan | None = None,
) -> PkgResult:
    plan = plan or plan_directory(directory)
    engine = ensure_engine(on_line)

    work = engine.dir / "pop-fe-work"
    src_stage = engine.dir / "pop-fe-input"
    for d in (work, src_stage):
        shutil.rmtree(d, ignore_errors=True)

    try:
        input_path = _stage_input(plan.disc, src_stage, on_line)

        if plan.output.exists():
            plan.output.unlink()

        cmd = engine.invocation(
            "pop-fe.py",
            "--no-libcrypt",
            "--ps3-pkg",
            str(plan.output),
            str(input_path),
        )
        on_line("$ " + " ".join(cmd))
        tail = _run(cmd, engine, on_line)

        if not plan.output.exists():
            raise PkgError(
                "pop-fe finished without producing a PKG.\nLast lines:\n" + "\n".join(tail)
            )
        meta = _read_pkg_header(plan.output)
        if meta["magic"] != b"\x7fPKG":
            plan.output.unlink(missing_ok=True)
            raise PkgError("Output file is not a valid PS3 PKG (bad magic).")

        return PkgResult(
            path=plan.output,
            content_id=meta["content_id"],
            size=plan.output.stat().st_size,
            plan=plan,
        )
    finally:
        for d in (work, src_stage):
            shutil.rmtree(d, ignore_errors=True)


def _run(cmd: list[str], engine: Engine, on_line: Logger) -> list[str]:
    proc = subprocess.Popen(
        cmd,
        cwd=engine.dir,
        env=engine.env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    tail: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        on_line(line)
        tail.append(line)
        del tail[:-40]
    rc = proc.wait()
    if rc != 0:
        raise PkgError(
            f"pop-fe exited with status {rc}.\nLast lines:\n" + "\n".join(tail)
        )
    return tail


# --------------------------------------------------------------------------- #
# input staging
# --------------------------------------------------------------------------- #
def _stage_input(disc: DiscImage, stage: Path, on_line: Logger) -> Path:
    """Return a path pop-fe accepts (.cue / .bin / .img / .chd).

    pop-fe does not read .iso directly, so we synthesise a .cue for it.
    """
    if disc.kind == "cue":
        return disc.primary
    if disc.kind == "bin":
        return disc.primary
    if disc.kind == "chd":
        return disc.primary  # pop-fe extracts CHDs itself via chdman
    if disc.kind == "iso":
        stage.mkdir(parents=True, exist_ok=True)
        from psone2ps3pkg.disc import _Track  # local: internal helper

        t = _Track(disc.primary)
        mode = "MODE1/2352" if t.offset == 16 else "MODE2/2352"
        t.close()
        cue = stage / (disc.primary.stem + ".cue")
        cue.write_text(
            f'FILE "{disc.primary}" BINARY\n  TRACK 01 {mode}\n    INDEX 01 00:00:00\n'
        )
        on_line(f"Wrote a temporary cue for the ISO ({mode}).")
        return cue
    raise PkgError(f"Unsupported image kind: {disc.kind}")


def _peek_chd_track(disc: DiscImage) -> Path:
    """Extract a CHD to a temp bin/cue so we can identify it."""
    from psone2ps3pkg.paths import runtime_dir

    out = runtime_dir() / "chd-peek"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    cue = out / "disc.cue"
    binf = out / "disc.bin"
    proc = subprocess.run(
        ["chdman", "extractcd", "-f", "-i", str(disc.primary), "-o", str(cue), "-ob", str(binf)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not binf.is_file():
        shutil.rmtree(out, ignore_errors=True)
        raise PkgError("chdman could not extract this .chd:\n" + (proc.stderr or proc.stdout))
    return binf


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _read_pkg_header(path: Path) -> dict:
    with open(path, "rb") as fh:
        head = fh.read(0x60)
    return {
        "magic": head[:4],
        "item_count": struct.unpack_from(">I", head, 0x14)[0],
        "total_size": struct.unpack_from(">Q", head, 0x18)[0],
        "content_id": head[0x30:0x54].split(b"\x00")[0].decode("ascii", "replace"),
    }


def _clean_stem(stem: str) -> str:
    stem = re.sub(r"\s*[\(\[].*?[\)\]]", "", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_.")
    return stem or "PlayStation Game"


def _sanitise(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip()
    name = re.sub(r"\s+", " ", name)
    return name[:100] or "PlayStation Game"


__all__ = [
    "Plan",
    "PkgResult",
    "PkgError",
    "DiscError",
    "EngineError",
    "plan_directory",
    "build_pkg",
]
