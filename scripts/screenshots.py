"""Drive the real app off-screen and save screenshots of each screen.

    python scripts/screenshots.py ROM_FOLDER OUT_DIR [--convert GAME_INDEX --build-dir DIR]

ROM_FOLDER is scanned for real. With --convert, one game is converted for real
(output into --build-dir) to capture the progress and result screens.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("PSONE2PS3PKG_SCALE", "2")  # draw at 1280x960, sharp
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from psone2ps3pkg.gui.app import App  # noqa: E402


def pump(app: App, seconds: float = 0.0, until=None, timeout: float = 600) -> None:
    end = time.monotonic() + (timeout if until else seconds)
    while time.monotonic() < end:
        app.tick(1 / 60, pygame.event.get())
        if until and until():
            return
        time.sleep(1 / 60)
    if until:
        raise SystemExit(f"timed out waiting on screen {app.screen}")


def save(app: App, out: Path, name: str) -> None:
    app.tick(1 / 60, [])
    path = out / f"{name}.png"
    pygame.image.save(app.surface, str(path))
    print("saved", path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roms")
    ap.add_argument("out")
    ap.add_argument("--convert", type=int, default=None)
    ap.add_argument("--build-dir")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    app = App()
    pump(app, 1.2)
    save(app, out, "1-home")

    pygame.event.post(pygame.event.Event(pygame.DROPFILE, file=args.roms))
    pump(app, until=lambda: app.screen == "games")
    app.focus_key = "start"
    pump(app, 0.4)
    save(app, out, "2-games")

    app.focus_key = "row2" if app.lib and len(app.lib.games) > 2 else "row0"
    pump(app, 0.2)
    app.on_key(pygame.K_i)
    pump(app, 0.3)
    save(app, out, "3-details")
    app.close_overlay()

    app.show_skipped()
    pump(app, 0.3)
    save(app, out, "4-skipped")
    app.close_overlay()

    if args.convert is not None:
        from psone2ps3pkg.convert import Target

        assert app.lib
        app.options.output_dir = Path(args.build_dir) if args.build_dir else None
        app.options.target = Target.PS3
        for i, g in enumerate(app.lib.games):
            app.checked[app.game_key(g)] = i == args.convert
        app.focus_key = "target"
        pump(app, 0.3)
        save(app, out, "5-options")
        app.start_convert()
        pump(app, 25, until=None)
        save(app, out, "6-converting")
        pump(app, until=lambda: app.screen == "done", timeout=1800)
        pump(app, 0.5)
        save(app, out, "7-done")
        ok = all(r.ok for r in app.results)
        for r in app.results:
            print("result:", r.game.title, r.outputs, r.error)
        app.shutdown()
        return 0 if ok else 1

    app.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
