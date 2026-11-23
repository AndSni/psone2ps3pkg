"""Find every PlayStation 1 game below one or more dropped paths.

A drop can be a single image file, a folder with one game, or a whole ROM
folder. The scan:

1. collects candidate discs (each ``.cue`` claims the track files it lists;
   ``.chd``, unclaimed ``.bin``/``.img`` and ``.iso`` stand alone),
2. reads each disc's ISO9660 filesystem to get its serial,
3. drops duplicates of the same disc (a ``.cue`` beats a raw ``.iso``),
4. groups the discs of a multi-disc game into one :class:`Game`.

Anything that cannot be used goes to :attr:`Library.skipped` with a reason.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from psone2ps3pkg.disc import (
    DiscError,
    DiscImage,
    DiscInfo,
    _cue_tracks,
    identify,
)
from psone2ps3pkg.engine import find_chdman, game_entry, host_env

ARCHIVE_SUFFIXES = (".7z", ".rar", ".zip")
MAX_DEPTH = 4
MAX_FILES = 20000

# Lower is better when the same disc shows up in several formats.
_KIND_RANK = {"cue": 0, "chd": 1, "bin": 2, "iso": 3}

Progress = Callable[[str], None]


@dataclass
class Disc:
    image: DiscImage
    info: DiscInfo
    number: int | None = None  # "(Disc 2)" in the file name, when present

    @property
    def label(self) -> str:
        return self.image.primary.name


@dataclass
class Game:
    title: str
    discs: list[Disc]
    warnings: list[str] = field(default_factory=list)

    @property
    def serial(self) -> str | None:
        return self.discs[0].info.serial

    @property
    def serials(self) -> list[str]:
        return [d.info.serial or "?" for d in self.discs]

    @property
    def region(self) -> str:
        return self.discs[0].info.region

    @property
    def source_dir(self) -> Path:
        return self.discs[0].image.primary.parent

    @property
    def kinds(self) -> str:
        return "/".join(sorted({d.image.kind.upper() for d in self.discs}))


@dataclass
class Skipped:
    path: Path
    reason: str


@dataclass
class Library:
    games: list[Game] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def scan_paths(paths: Iterable[str | Path], progress: Progress = lambda _s: None) -> Library:
    lib = Library()
    files: list[Path] = []
    seen: set[Path] = set()
    for raw in paths:
        p = Path(raw).expanduser()
        try:
            p = p.resolve()
        except OSError:
            pass
        if not p.exists():
            lib.skipped.append(Skipped(p, "does not exist"))
            continue
        for f in _walk(p) if p.is_dir() else [p, *_owning_cues(p)]:
            if f not in seen:
                seen.add(f)
                files.append(f)

    candidates = _candidates(files, lib)
    discs: list[Disc] = []
    for image in candidates:
        progress(f"Reading {image.primary.name}")
        try:
            info = _identify(image)
        except DiscError as exc:
            lib.skipped.append(Skipped(image.primary, str(exc)))
            continue
        if image.kind == "iso" and info.sector_size == 2048:
            lib.skipped.append(Skipped(
                image.primary,
                "data-only .iso (2048-byte sectors); PS1 needs a raw BIN/CUE or CHD image",
            ))
            continue
        discs.append(Disc(image, info, _disc_number(image.primary.stem)))

    discs = _dedupe(discs, lib)
    lib.games = _group(discs)
    lib.skipped.sort(key=lambda s: str(s.path).lower())
    return lib


# --------------------------------------------------------------------------- #
# file discovery
# --------------------------------------------------------------------------- #
def _walk(root: Path) -> list[Path]:
    out: list[Path] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - root_depth
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        for name in sorted(filenames):
            out.append(Path(dirpath) / name)
            if len(out) >= MAX_FILES:
                return out
    return out


def _owning_cues(track: Path) -> list[Path]:
    """Cue sheets next to a dropped .bin/.img that list it (keeps CD audio)."""
    if track.suffix.lower() not in (".bin", ".img"):
        return []
    out: list[Path] = []
    try:
        siblings = list(track.parent.iterdir())
    except OSError:
        return []
    for cue in siblings:
        if cue.suffix.lower() != ".cue" or not cue.is_file():
            continue
        try:
            if any(t.resolve() == track for t in _cue_tracks(cue)):
                out.append(cue.resolve())
        except OSError:
            continue
    return out


def _candidates(files: list[Path], lib: Library) -> list[DiscImage]:
    by_suffix: dict[str, list[Path]] = {}
    for f in files:
        by_suffix.setdefault(f.suffix.lower(), []).append(f)

    claimed: set[Path] = set()
    out: list[DiscImage] = []

    for cue in by_suffix.get(".cue", []):
        try:
            tracks = _cue_tracks(cue)
        except OSError as exc:
            lib.skipped.append(Skipped(cue, f"cannot read cue: {exc}"))
            continue
        if not tracks:
            lib.skipped.append(Skipped(cue, "cue sheet lists no track files"))
            continue
        missing = [t.name for t in tracks if not t.is_file()]
        claimed.update(t.resolve() for t in tracks if t.is_file())
        if missing:
            lib.skipped.append(Skipped(cue, "missing track file(s): " + ", ".join(missing)))
            continue
        out.append(DiscImage(cue.parent, "cue", cue, tracks[0], tracks[1:], has_cue=True))

    for chd in by_suffix.get(".chd", []):
        out.append(DiscImage(chd.parent, "chd", chd, chd))

    for suffix in (".bin", ".img"):
        for b in by_suffix.get(suffix, []):
            if b.resolve() not in claimed:
                out.append(DiscImage(b.parent, "bin", b, b))

    for iso in by_suffix.get(".iso", []):
        out.append(DiscImage(iso.parent, "iso", iso, iso))

    for suffix in ARCHIVE_SUFFIXES:
        for a in by_suffix.get(suffix, []):
            lib.skipped.append(Skipped(a, "archive; extract it first"))

    return out


# --------------------------------------------------------------------------- #
# identification
# --------------------------------------------------------------------------- #
def _identify(image: DiscImage) -> DiscInfo:
    if image.kind != "chd":
        return identify(image)
    if find_chdman() is None:
        raise DiscError("this is a .chd and 'chdman' (mame-tools) is not installed")
    tmp = Path(tempfile.mkdtemp(prefix="psone2ps3pkg-chd-"))
    try:
        binf = tmp / "disc.bin"
        proc = subprocess.run(
            ["chdman", "extractcd", "-f", "-i", str(image.primary),
             "-o", str(tmp / "disc.cue"), "-ob", str(binf)],
            capture_output=True, text=True, env=host_env(),
        )
        if proc.returncode != 0 or not binf.is_file():
            raise DiscError("chdman could not extract it: " + (proc.stderr or proc.stdout).strip()[-200:])
        return identify(image, chd_extracted_track=binf)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_DISC_RE = re.compile(r"\b(?:disc|disk|cd)\s*([1-9])\b", re.IGNORECASE)


def _disc_number(stem: str) -> int | None:
    m = _DISC_RE.search(stem)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- #
# de-duplication and grouping
# --------------------------------------------------------------------------- #
def _dedupe(discs: list[Disc], lib: Library) -> list[Disc]:
    best: dict[str, Disc] = {}
    out: list[Disc] = []
    for d in discs:
        if not d.info.serial:
            out.append(d)
            continue
        cur = best.get(d.info.serial)
        if cur is None:
            best[d.info.serial] = d
            continue
        keep, drop = (d, cur) if _KIND_RANK[d.image.kind] < _KIND_RANK[cur.image.kind] else (cur, d)
        best[d.info.serial] = keep
        lib.skipped.append(Skipped(drop.image.primary, f"duplicate of {keep.image.primary.name}"))
    return list(best.values()) + out


def _clean_title(stem: str) -> str:
    stem = re.sub(r"\s*[\(\[].*?[\)\]]", "", stem)
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" -_.")
    return stem or "PlayStation Game"


def _group(discs: list[Disc]) -> list[Game]:
    groups: dict[str, list[Disc]] = {}
    for d in discs:
        entry = game_entry(d.info.serial)
        if entry and entry.get("url"):
            key = "db:" + str(entry["url"])
        elif d.number is not None:
            # No database hit: tie "(Disc N)" files of one folder by name.
            key = f"name:{d.image.primary.parent}/{_clean_title(_DISC_RE.sub('', d.image.primary.stem)).lower()}"
        else:
            key = f"one:{d.image.primary}"
        groups.setdefault(key, []).append(d)

    games: list[Game] = []
    for members in groups.values():
        members.sort(key=lambda d: (d.number or 99, d.info.serial or "", d.image.primary.name))
        first = members[0]
        entry = game_entry(first.info.serial)
        title = (entry or {}).get("title") or _clean_title(first.image.primary.stem)
        for d in members:
            d.info.title = title
        game = Game(title=str(title), discs=members)
        if len(members) > 5:
            game.warnings.append("more than 5 discs; PS3 and PSP accept at most 5")
        for d in members:
            if d.info.note:
                game.warnings.append(d.info.note)
            if d.image.kind == "bin" and not d.image.has_cue:
                game.warnings.append(
                    f"{d.label}: no .cue; CD-audio tracks (if any) are not included"
                )
        if first.info.region == "PAL":
            game.warnings.append("PAL disc: LibCrypt protection data comes from pop-fe's database")
        games.append(game)

    games.sort(key=lambda g: g.title.lower())
    return games
