from __future__ import annotations

from pathlib import Path

from psone2ps3pkg.convert import Options, Target, _stage_input, expected_output
from psone2ps3pkg.disc import cue_needs_rewrite, resolve_cue_file
from psone2ps3pkg.engine import game_entry
from psone2ps3pkg.library import scan_paths
from test_disc import _make_iso

SYNC = b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00"


def raw_image(serial: str = "SLUS_007.89", boot: str = "BOOT") -> bytes:
    """A 2352-byte-sector MODE2 image around a tiny ISO9660 filesystem."""
    cnf = f"{boot} = cdrom:\\{serial};1\r\nTCB = 4\r\n".encode()
    iso = _make_iso(cnf)
    out = bytearray()
    for i in range(len(iso) // 2048):
        out += SYNC + bytes([0, 2, i % 75, 2]) + bytes(8) + iso[i * 2048:(i + 1) * 2048] + bytes(280)
    return bytes(out)


def write_cue_game(folder: Path, stem: str, serial: str, cue_file_name: str | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.bin").write_bytes(raw_image(serial))
    cue = folder / f"{stem}.cue"
    cue.write_text(f'FILE "{cue_file_name or stem + ".bin"}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
    return cue


def test_scan_collection_with_noise(tmp_path: Path):
    write_cue_game(tmp_path, "Game A (USA)", "SLUS_900.01")
    write_cue_game(tmp_path / "sub", "Game B (Europe)", "SLES_900.02")
    (tmp_path / "Game A (USA).7z").write_bytes(b"7z")
    (tmp_path / "Game A (USA).iso").write_bytes(_make_iso(b"BOOT = cdrom:\\SLUS_900.01;1\r\n"))
    (tmp_path / "readme.txt").write_text("hi")

    lib = scan_paths([tmp_path])
    assert [g.serial for g in lib.games] == ["SLUS-90001", "SLES-90002"]
    assert lib.games[0].title == "Game A"
    assert lib.games[1].region == "PAL"
    reasons = {s.path.name: s.reason for s in lib.skipped}
    assert "archive" in reasons["Game A (USA).7z"]
    assert "2048-byte" in reasons["Game A (USA).iso"]
    assert "readme.txt" not in reasons


def test_duplicate_raw_iso_loses_to_cue(tmp_path: Path):
    write_cue_game(tmp_path, "Game", "SLUS_900.03")
    (tmp_path / "Game copy.iso").write_bytes(raw_image("SLUS_900.03"))
    lib = scan_paths([tmp_path])
    assert len(lib.games) == 1
    assert lib.games[0].discs[0].image.kind == "cue"
    assert any("duplicate" in s.reason for s in lib.skipped)


def test_ps2_disc_is_skipped(tmp_path: Path):
    (tmp_path / "ps2.bin").write_bytes(raw_image("SLUS_201.00", boot="BOOT2"))
    lib = scan_paths([tmp_path])
    assert not lib.games
    assert "PlayStation 2" in lib.skipped[0].reason


def test_multi_disc_grouped_by_name(tmp_path: Path):
    a, b = "SLUS_909.71", "SLUS_909.72"
    assert game_entry("SLUS-90971") is None and game_entry("SLUS-90972") is None
    write_cue_game(tmp_path, "Epic (USA) (Disc 2)", b)
    write_cue_game(tmp_path, "Epic (USA) (Disc 1)", a)
    lib = scan_paths([tmp_path])
    assert len(lib.games) == 1
    assert lib.games[0].serials == ["SLUS-90971", "SLUS-90972"]


def test_multi_disc_grouped_by_database(tmp_path: Path):
    # Gran Turismo 2: Arcade + Simulation discs share one database page.
    write_cue_game(tmp_path, "GT2_SIM", "SCUS_944.88")
    write_cue_game(tmp_path, "GT2_ARCADE", "SCUS_944.55")
    lib = scan_paths([tmp_path])
    assert len(lib.games) == 1
    assert lib.games[0].serials == ["SCUS-94455", "SCUS-94488"]
    assert "GRAN TURISMO 2" in lib.games[0].title


def test_windows_path_cue_is_resolved_and_restaged(tmp_path: Path):
    cue = write_cue_game(tmp_path, "Racer", "SLUS_900.05", cue_file_name="D:\\RIPS\\RACER.BIN")
    assert resolve_cue_file(cue, "D:\\RIPS\\RACER.BIN") == tmp_path / "Racer.bin"
    assert cue_needs_rewrite(cue)
    lib = scan_paths([cue])
    assert len(lib.games) == 1
    staged = _stage_input(lib.games[0].discs[0].image, tmp_path / "stage", lambda _s: None)
    assert staged != cue
    assert f'FILE "{tmp_path / "Racer.bin"}" BINARY' in staged.read_text()


def test_dropping_the_bin_uses_its_cue(tmp_path: Path):
    cue = write_cue_game(tmp_path, "Music Game", "SLUS_900.06")
    lib = scan_paths([tmp_path / "Music Game.bin"])
    assert len(lib.games) == 1
    assert lib.games[0].discs[0].image.primary == cue


def test_expected_output_names(tmp_path: Path):
    write_cue_game(tmp_path, "Game", "SLUS_900.07")
    game = scan_paths([tmp_path]).games[0]
    assert expected_output(game, Options(Target.PS3)).name == "Game [SLUS-90007].pkg"
    assert expected_output(game, Options(Target.PSP)).parts[-3:] == ("PSP", "GAME", "SLUS90007")
    out = tmp_path / "out"
    assert expected_output(game, Options(Target.PS2, output_dir=out)) == out / "POPS"


def test_stage_and_noise_filters():
    from psone2ps3pkg.convert import _NOISE, stage_of

    assert stage_of("Create PKG") == 0.82
    assert stage_of("random text") is None
    assert _NOISE.match("atracdenc not found.")
    assert _NOISE.match("pip3 install rarfile")
    assert not _NOISE.match("Create ISO.BIN.EDAT")


def test_build_passes_safe_popfe_flags(tmp_path: Path, monkeypatch):
    import psone2ps3pkg.convert as conv

    write_cue_game(tmp_path, "Game", "SLUS_900.08")
    game = scan_paths([tmp_path]).games[0]
    seen = []

    class FakeEngine:
        dir = tmp_path / "engine"
        env: dict = {}

        def invocation(self, script, *args):
            return [script, *args]

    def fake_run(cmd, engine, on_line, cancel, cleanup=lambda: None):
        seen.append(cmd)
        out = Path(cmd[cmd.index("--psp-dir") + 1]) / "SLUS90008"
        out.mkdir(parents=True)
        (out / "EBOOT.PBP").write_bytes(b"\x00PBP" + bytes(8))

    monkeypatch.setattr(conv, "ensure_engine", lambda log: FakeEngine())
    monkeypatch.setattr(conv, "_run", fake_run)
    res = conv.build_game(game, Options(Target.PSP, output_dir=tmp_path / "out"))
    assert res.ok, res.error
    cmd = seen[0]
    assert cmd[cmd.index("--snd0") + 1] == "no"
    assert "--psp-use-cdda" in cmd and "--no-libcrypt" in cmd
