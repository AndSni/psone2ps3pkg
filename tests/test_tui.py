from __future__ import annotations

import asyncio
from pathlib import Path

from psone2ps3pkg.tui import PkgApp
from test_disc import _make_iso


def test_app_mounts_and_has_widgets():
    async def go() -> None:
        async with PkgApp().run_test() as pilot:
            await pilot.pause()
            app = pilot.app
            assert app.query_one("#path")
            assert app.query_one("#go")
            assert app.query_one("#log")

    asyncio.run(go())


def test_scan_reports_a_bad_directory(tmp_path: Path):
    """A folder with nothing usable drives the worker down its failure path."""

    async def go() -> None:
        async with PkgApp().run_test() as pilot:
            app = pilot.app
            app._scan(str(tmp_path))
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._plan is None
            assert app.query_one("#go").disabled
            assert "No disc image" in str(app.query_one("#summary").render())

    asyncio.run(go())


def test_scan_accepts_a_ps1_image(tmp_path: Path):
    iso = _make_iso(b"BOOT = cdrom:\\SLUS_007.89;1\r\n")
    # wrap each 2048 logical sector in a raw 2352-byte MODE2/2352 sector so the
    # image is not rejected as a data-only 2048 ISO
    sync = b"\x00\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\x00"
    wrapped = bytearray()
    for i in range(0, len(iso), 2048):
        wrapped += sync + b"\x00\x00\x00\x02" + b"\x00" * 8 + iso[i : i + 2048] + b"\x00" * 280
    (tmp_path / "game.bin").write_bytes(bytes(wrapped))
    (tmp_path / "game.cue").write_text('FILE "game.bin" BINARY\n  TRACK 01 MODE2/2352\n')

    async def go() -> None:
        async with PkgApp().run_test() as pilot:
            app = pilot.app
            app._scan(str(tmp_path))
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app._plan is not None
            assert app._plan.info.serial == "SLUS-00789"
            assert not app.query_one("#go").disabled

    asyncio.run(go())
