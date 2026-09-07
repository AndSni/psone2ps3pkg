from __future__ import annotations

import struct
from pathlib import Path

import pytest

from psone2ps3pkg.cli import clean_drop_path
from psone2ps3pkg.disc import (
    MultipleDiscImages,
    NoDiscImage,
    NotPlayStation1,
    _normalise_serial,
    _region_for,
    identify,
    scan_directory,
)

SECTOR = 2048


def _dir_record(name: bytes, extent: int, length: int, is_dir: bool = False) -> bytes:
    ident = name if name else b"\x00"
    base = 33 + len(ident)
    rec_len = base + (base % 2)
    rec = bytearray(rec_len)
    rec[0] = rec_len
    struct.pack_into("<I", rec, 2, extent)
    struct.pack_into(">I", rec, 6, extent)
    struct.pack_into("<I", rec, 10, length)
    struct.pack_into(">I", rec, 14, length)
    rec[25] = 0x02 if is_dir else 0x00
    rec[32] = len(ident)
    rec[33 : 33 + len(ident)] = ident
    return bytes(rec)


def _make_iso(system_cnf: bytes | None, volume_id: bytes = b"PLAYSTATION") -> bytes:
    total = 24
    img = bytearray(total * SECTOR)

    root_extent = 17
    root_records = _dir_record(b"", root_extent, SECTOR, is_dir=True)
    root_records += _dir_record(b"\x01", root_extent, SECTOR, is_dir=True)
    if system_cnf is not None:
        root_records += _dir_record(b"SYSTEM.CNF;1", 18, len(system_cnf))
        img[18 * SECTOR : 18 * SECTOR + len(system_cnf)] = system_cnf
    img[root_extent * SECTOR : root_extent * SECTOR + len(root_records)] = root_records

    pvd = bytearray(SECTOR)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    vid = volume_id[:32].ljust(32, b" ")
    pvd[40:72] = vid
    pvd[156:190] = _dir_record(b"", root_extent, SECTOR, is_dir=True).ljust(34, b"\x00")[:34]
    img[16 * SECTOR : 17 * SECTOR] = pvd
    return bytes(img)


@pytest.mark.parametrize(
    "raw,expected_tail",
    [
        ("'/home/x/Grand Theft Auto 2'", "Grand Theft Auto 2"),
        ('"/home/x/Some Game/"', "Some Game"),
        ("file:///home/x/Some%20Game", "Some Game"),
        ("/home/x/Some\\ Game", "Some Game"),
        ("  /home/x/Plain  ", "Plain"),
    ],
)
def test_clean_drop_path(raw, expected_tail):
    assert clean_drop_path(raw).endswith(expected_tail)


@pytest.mark.parametrize(
    "raw,out",
    [
        ("SLUS_007.89", "SLUS-00789"),
        ("SLUS-00789", "SLUS-00789"),
        ("sles_012.34", "SLES-01234"),
        ("SCUS94900", "SCUS-94900"),
    ],
)
def test_normalise_serial(raw, out):
    assert _normalise_serial(raw) == out


def test_region_for():
    assert _region_for("SLUS-00789") == "NTSC"
    assert _region_for("SLES-01234") == "PAL"
    assert _region_for(None) == "unknown"
    assert _region_for("ABCD-00001") == "unknown"


def test_scan_directory_cue_bin(tmp_path: Path):
    (tmp_path / "game.bin").write_bytes(b"\x00" * SECTOR)
    (tmp_path / "game.cue").write_text('FILE "game.bin" BINARY\n  TRACK 01 MODE2/2352\n')
    disc = scan_directory(tmp_path)
    assert disc.kind == "cue"
    assert disc.has_cue
    assert disc.data_track.name == "game.bin"


def test_scan_directory_bare_bin(tmp_path: Path):
    (tmp_path / "game.bin").write_bytes(b"\x00" * SECTOR)
    disc = scan_directory(tmp_path)
    assert disc.kind == "bin"
    assert not disc.has_cue


def test_scan_directory_empty(tmp_path: Path):
    with pytest.raises(NoDiscImage):
        scan_directory(tmp_path)


def test_scan_directory_two_cues(tmp_path: Path):
    (tmp_path / "a.cue").write_text('FILE "a.bin" BINARY\n')
    (tmp_path / "b.cue").write_text('FILE "b.bin" BINARY\n')
    with pytest.raises(MultipleDiscImages):
        scan_directory(tmp_path)


def test_identify_ps1(tmp_path: Path):
    iso = _make_iso(b"BOOT = cdrom:\\SLUS_007.89;1\r\nTCB = 4\r\n")
    p = tmp_path / "game.iso"
    p.write_bytes(iso)
    disc = scan_directory(tmp_path)
    info = identify(disc)
    assert info.serial == "SLUS-00789"
    assert info.region == "NTSC"


def test_identify_rejects_ps2(tmp_path: Path):
    iso = _make_iso(b"BOOT2 = cdrom0:\\SLUS_200.01;1\r\nVER = 1.00\r\n")
    (tmp_path / "game.iso").write_bytes(iso)
    disc = scan_directory(tmp_path)
    with pytest.raises(NotPlayStation1):
        identify(disc)


def test_identify_rejects_non_cd(tmp_path: Path):
    (tmp_path / "game.iso").write_bytes(b"\x00" * (24 * SECTOR))
    disc = scan_directory(tmp_path)
    with pytest.raises(Exception):
        identify(disc)
