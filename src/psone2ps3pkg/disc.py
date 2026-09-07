"""Locate and identify a PlayStation 1 disc image inside a directory.

Supported inputs: ``.cue`` (+ ``.bin``/``.img`` tracks), a bare ``.bin``/``.img``,
a ``.iso``, or a ``.chd``. Anything that is not a PlayStation 1 disc is rejected.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

SYNC = b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00"

IMAGE_SUFFIXES = (".cue", ".chd", ".bin", ".img", ".iso")

# Serial prefixes seen on PS1 discs, grouped by broadcast standard.
_NTSC_PREFIXES = ("SLUS", "SCUS", "SLPS", "SLPM", "SCPS", "SIPS", "PAPX", "LSP")
_PAL_PREFIXES = ("SLES", "SCES", "SCED")


class DiscError(Exception):
    """Base class for anything wrong with the dropped directory."""


class NoDiscImage(DiscError):
    pass


class MultipleDiscImages(DiscError):
    pass


class UnreadableDisc(DiscError):
    pass


class NotPlayStation1(DiscError):
    pass


@dataclass
class DiscImage:
    """A single game's on-disk representation inside the working directory."""

    directory: Path
    kind: str  # "cue" | "bin" | "iso" | "chd"
    primary: Path  # the .cue / .bin / .img / .iso / .chd the user points pop-fe at
    data_track: Path  # the file that actually holds the ISO9660 filesystem
    extra_tracks: list[Path] = field(default_factory=list)
    has_cue: bool = False


@dataclass
class DiscInfo:
    serial: str | None
    region: str  # "NTSC" | "PAL" | "unknown"
    title: str | None = None
    sector_size: int = 2352
    note: str | None = None


# --------------------------------------------------------------------------- #
# directory scan
# --------------------------------------------------------------------------- #
def scan_directory(directory: str | Path) -> DiscImage:
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise DiscError(f"Not a directory: {directory}")

    by_suffix: dict[str, list[Path]] = {s: [] for s in IMAGE_SUFFIXES}
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix.lower() in by_suffix:
            by_suffix[entry.suffix.lower()].append(entry)

    cues = by_suffix[".cue"]
    chds = by_suffix[".chd"]
    bins = by_suffix[".bin"] + by_suffix[".img"]
    isos = by_suffix[".iso"]

    if len(cues) > 1:
        raise MultipleDiscImages(
            "More than one .cue here — put one game per folder:\n  "
            + "\n  ".join(p.name for p in cues)
        )
    if not cues and len(chds) > 1:
        raise MultipleDiscImages(
            "More than one .chd here — put one game per folder:\n  "
            + "\n  ".join(p.name for p in chds)
        )

    if cues:
        cue = cues[0]
        tracks = _cue_tracks(cue)
        if not tracks:
            raise UnreadableDisc(f"{cue.name} lists no usable track files.")
        missing = [t for t in tracks if not t.exists()]
        if missing:
            raise UnreadableDisc(
                f"{cue.name} references missing files:\n  "
                + "\n  ".join(m.name for m in missing)
            )
        return DiscImage(
            directory=directory,
            kind="cue",
            primary=cue,
            data_track=tracks[0],
            extra_tracks=tracks[1:],
            has_cue=True,
        )

    if chds:
        return DiscImage(directory, "chd", chds[0], chds[0])

    if len(bins) > 1:
        raise MultipleDiscImages(
            "Several .bin/.img files but no .cue to tie them together:\n  "
            + "\n  ".join(p.name for p in bins)
        )
    if bins:
        return DiscImage(directory, "bin", bins[0], bins[0])

    if len(isos) > 1:
        raise MultipleDiscImages(
            "More than one .iso here — put one game per folder:\n  "
            + "\n  ".join(p.name for p in isos)
        )
    if isos:
        return DiscImage(directory, "iso", isos[0], isos[0])

    raise NoDiscImage(
        "No disc image found. Expected a .cue + .bin, a bare .bin/.img, a .iso, or a .chd."
    )


def _cue_tracks(cue: Path) -> list[Path]:
    text = cue.read_text(errors="replace")
    names = re.findall(r'FILE\s+"([^"]+)"', text) or re.findall(r"FILE\s+(\S+)", text)
    out: list[Path] = []
    for name in names:
        p = Path(name)
        out.append(p if p.is_absolute() else cue.parent / p.name)
    return out


# --------------------------------------------------------------------------- #
# sector reader — presents a de-sectored 2048-byte logical view
# --------------------------------------------------------------------------- #
class _Track:
    def __init__(self, path: Path):
        self.fh = open(path, "rb")
        head = self.fh.read(16)
        if head[:12] == SYNC:
            self.raw = 2352
            mode = head[15]
            self.offset = 24 if mode == 2 else 16
        else:
            self.raw = 2048
            self.offset = 0

    def sector(self, lba: int) -> bytes:
        self.fh.seek(lba * self.raw + self.offset)
        return self.fh.read(2048)

    def run(self, lba: int, count: int) -> bytes:
        return b"".join(self.sector(lba + i) for i in range(count))

    def close(self) -> None:
        self.fh.close()


# --------------------------------------------------------------------------- #
# identification
# --------------------------------------------------------------------------- #
def identify(disc: DiscImage, chd_extracted_track: Path | None = None) -> DiscInfo:
    """Read the disc's ISO9660 filesystem and confirm it is a PS1 game.

    ``chd_extracted_track`` is the already-decompressed data-track BIN when the
    input is a CHD (the caller does the extraction so pop-fe does not repeat it).
    """
    track_path = chd_extracted_track or disc.data_track
    track = _Track(track_path)
    try:
        return _identify_track(track)
    finally:
        track.close()


def _identify_track(track: _Track) -> DiscInfo:
    pvd = track.sector(16)
    if pvd[1:6] != b"CD001":
        raise UnreadableDisc(
            "No ISO9660 volume descriptor. This does not look like a CD image, "
            "or the track is not the data track."
        )

    system_cnf = _read_root_file(track, pvd, "SYSTEM.CNF")
    if system_cnf is not None:
        text = system_cnf.decode("latin-1", "replace")
        if re.search(r"^\s*BOOT2\s*=", text, re.MULTILINE):
            raise NotPlayStation1(
                "This is a PlayStation 2 disc (SYSTEM.CNF uses BOOT2). "
                "This tool only handles PlayStation 1."
            )
        m = re.search(r"BOOT\s*=\s*cdrom:?\\?([A-Z0-9_.\-]+)", text, re.IGNORECASE)
        if m:
            serial = _normalise_serial(m.group(1))
            return DiscInfo(serial=serial, region=_region_for(serial), sector_size=track.raw)
        # SYSTEM.CNF present but no parsable BOOT line — still a PS1 layout.
        return DiscInfo(serial=None, region="unknown", sector_size=track.raw,
                        note="SYSTEM.CNF has no readable BOOT line; pop-fe will try to detect the serial.")

    # No SYSTEM.CNF. A few early PS1 discs boot PSX.EXE straight from the root.
    if _read_root_file(track, pvd, "PSX.EXE") is not None:
        return DiscInfo(serial=None, region="unknown", sector_size=track.raw,
                        note="No SYSTEM.CNF; booted straight to PSX.EXE. pop-fe will try to detect the serial.")

    volume_id = pvd[40:72].decode("latin-1", "replace").strip()
    if "PLAYSTATION" in volume_id.upper():
        return DiscInfo(serial=None, region="unknown", sector_size=track.raw,
                        note=f'Volume "{volume_id}" but no SYSTEM.CNF; pop-fe will try to detect the serial.')

    raise NotPlayStation1(
        "No SYSTEM.CNF and no PlayStation volume signature — not a recognisable PS1 disc."
    )


def _read_root_file(track: _Track, pvd: bytes, filename: str) -> bytes | None:
    root_record = pvd[156:190]
    extent = struct.unpack_from("<I", root_record, 2)[0]
    length = struct.unpack_from("<I", root_record, 10)[0]
    sectors = max(1, (length + 2047) // 2048)
    directory = track.run(extent, sectors)

    want = filename.upper()
    pos = 0
    while pos < len(directory):
        rec_len = directory[pos]
        if rec_len == 0:
            # advance to next sector boundary
            pos = (pos // 2048 + 1) * 2048
            continue
        rec = directory[pos : pos + rec_len]
        name_len = rec[32]
        name = rec[33 : 33 + name_len].decode("latin-1", "replace")
        name = name.split(";")[0].upper()
        if name == want:
            f_extent = struct.unpack_from("<I", rec, 2)[0]
            f_length = struct.unpack_from("<I", rec, 10)[0]
            f_sectors = max(1, (f_length + 2047) // 2048)
            return track.run(f_extent, f_sectors)[:f_length]
        pos += rec_len
    return None


def _normalise_serial(raw: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    m = re.match(r"([A-Z]+)(\d+)", compact)
    if not m:
        return compact
    return f"{m.group(1)}-{m.group(2)}"


def _region_for(serial: str | None) -> str:
    if not serial:
        return "unknown"
    prefix = serial.split("-")[0]
    if prefix in _PAL_PREFIXES:
        return "PAL"
    if prefix in _NTSC_PREFIXES:
        return "NTSC"
    return "unknown"
