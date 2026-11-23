"""Drive the real pygame window headless (SDL dummy video driver)."""

from __future__ import annotations

import time
from pathlib import Path

import pygame
import pytest

from psone2ps3pkg.convert import Result, Target
from psone2ps3pkg.gui import app as gui_app
from test_library import write_cue_game


def pump(app, until, timeout: float = 10.0, events=()) -> None:
    for ev in events:
        pygame.event.post(ev)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.tick(1 / 60, pygame.event.get())
        if until():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out on screen={app.screen} overlay={app.overlay}")


def key(k):
    return pygame.event.Event(pygame.KEYDOWN, key=k, mod=0, unicode="", scancode=0)


@pytest.fixture
def app():
    a = gui_app.App()
    yield a
    a.shutdown()


def test_home_screen_has_both_pickers(app):
    pump(app, lambda: True)
    keys = [it.key for it in app.items]
    assert "folder" in keys and "file" in keys


def test_drop_folder_lists_games(app, tmp_path: Path):
    write_cue_game(tmp_path, "Alpha", "SLUS_900.11")
    write_cue_game(tmp_path, "Beta", "SLUS_900.12")
    (tmp_path / "x.rar").write_bytes(b"r")
    pump(app, lambda: app.screen == "games",
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path))])
    assert [g.title for g in app.lib.games] == ["Alpha", "Beta"]
    assert len(app.selected_games()) == 2
    pump(app, lambda: True)
    assert "skipped" in [it.key for it in app.items]


def test_second_drop_merges(app, tmp_path: Path):
    write_cue_game(tmp_path / "a", "Alpha", "SLUS_900.13")
    write_cue_game(tmp_path / "b", "Beta", "SLUS_900.14")
    pump(app, lambda: app.screen == "games",
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path / "a"))])
    pump(app, lambda: app.screen == "games" and len(app.lib.games) == 2,
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path / "b"))])


def test_empty_drop_shows_message(app, tmp_path: Path):
    (tmp_path / "notes.txt").write_text("nothing")
    pump(app, lambda: app.overlay is not None,
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path))])
    assert app.screen == "home"
    assert app.overlay.title == "NO GAMES FOUND"
    pump(app, lambda: app.overlay is None, events=[key(pygame.K_RETURN)])


def test_keyboard_options_and_toggle(app, tmp_path: Path):
    write_cue_game(tmp_path, "Alpha", "SLUS_900.15")
    pump(app, lambda: app.screen == "games",
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path))])
    pump(app, lambda: True)
    app.focus_key = "target"
    pump(app, lambda: app.options.target is Target.PSP, events=[key(pygame.K_RIGHT)])
    pump(app, lambda: app.options.target is Target.PS2, events=[key(pygame.K_RIGHT)])
    app.focus_key = "row0"
    pump(app, lambda: not app.selected_games(), events=[key(pygame.K_SPACE)])
    pump(app, lambda: len(app.selected_games()) == 1, events=[key(pygame.K_a)])
    pump(app, lambda: app.overlay is not None, events=[key(pygame.K_i)])


def test_convert_flow_with_fake_engine(app, tmp_path: Path, monkeypatch):
    write_cue_game(tmp_path, "Alpha", "SLUS_900.16")
    write_cue_game(tmp_path, "Beta", "SLUS_900.17")
    calls = []

    def fake_build(game, options, on_line, cancel):
        calls.append((game.title, options.target))
        on_line(f"building {game.title}")
        if game.title == "Beta":
            return Result(game, error="pop-fe exited with status 1.\nLast lines:\nboom")
        out = tmp_path / f"{game.title}.pkg"
        out.write_bytes(b"\x7fPKG")
        return Result(game, outputs=[out], content_id="UP9000-X")

    monkeypatch.setattr(gui_app, "build_game", fake_build)
    pump(app, lambda: app.screen == "games",
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path))])
    pump(app, lambda: True)
    assert app.focus_key == "start"
    pump(app, lambda: app.screen == "done", events=[key(pygame.K_RETURN)])
    assert [r.ok for r in app.results] == [True, False]
    assert calls == [("Alpha", Target.PS3), ("Beta", Target.PS3)]
    pump(app, lambda: True)
    assert any(it.key.startswith("err") for it in app.items)


def test_builtin_browser_picks_folder(app, tmp_path: Path, monkeypatch):
    write_cue_game(tmp_path / "games", "Alpha", "SLUS_900.18")
    app.last_dir = tmp_path
    monkeypatch.setattr(gui_app.picker, "available", lambda: None)
    app.choose("folder")
    assert app.browser is not None
    pump(app, lambda: True)
    names = [e[0] for e in app.browser.entries]
    assert names[0] == "USE THIS FOLDER" and "games/" in names
    app.browser.choose(names.index("games/"))
    app.browser.choose(0)
    pump(app, lambda: app.screen == "games")
    assert app.lib.games[0].title == "Alpha"


def test_settings_persist(tmp_path: Path):
    a = gui_app.App()
    a.options.target = Target.PSP
    a.options.output_dir = tmp_path
    a.shutdown()
    b = gui_app.App()
    try:
        assert b.options.target is Target.PSP
        assert b.options.output_dir == tmp_path
    finally:
        b.shutdown()


def click(pos):
    return [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos),
            pygame.event.Event(pygame.MOUSEBUTTONUP, button=1, pos=pos)]


def hint_pos(app, kind):
    for k, rect in app.hint_hits:
        if k == kind:
            return rect.center
    raise AssertionError(f"no {kind} in hint bar: {app.hint_hits}")


def test_hint_bar_select_and_back_are_clickable(app, tmp_path: Path):
    write_cue_game(tmp_path, "Alpha", "SLUS_900.21")
    pump(app, lambda: app.screen == "games",
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path))])
    pump(app, lambda: True)
    app.focus_key = "row0"
    pump(app, lambda: True)
    # SELECT toggles the focused game
    pump(app, lambda: not app.selected_games(), events=click(hint_pos(app, "cross")))
    # DETAILS opens the overlay, its OK in the bar closes it
    pump(app, lambda: app.overlay is not None, events=click(hint_pos(app, "triangle")))
    pump(app, lambda: True)
    pump(app, lambda: app.overlay is None, events=click(hint_pos(app, "cross")))
    # ALL / NONE, then BACK to the home screen
    pump(app, lambda: len(app.selected_games()) == 1, events=click(hint_pos(app, "square")))
    pump(app, lambda: True)
    pump(app, lambda: app.screen == "home", events=click(hint_pos(app, "circle")))


def test_option_arrows_click_in_their_direction(app, tmp_path: Path):
    write_cue_game(tmp_path, "Alpha", "SLUS_900.22")
    pump(app, lambda: app.screen == "games",
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path))])
    pump(app, lambda: True)
    row = next(it for it in app.items if it.key == "target").rect
    app.options.target = Target.PS3
    left = (row.left + gui_app.OPTION_VALUE_X + 4, row.centery)
    right = (row.right - 14, row.centery)
    pump(app, lambda: app.options.target is Target.PS2, events=click(left))   # back wraps
    pump(app, lambda: app.options.target is Target.PS3, events=click(right))


def test_pad_buttons(app, tmp_path: Path):
    write_cue_game(tmp_path, "Alpha", "SLUS_900.23")
    pump(app, lambda: app.screen == "games",
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path))])
    pump(app, lambda: True)
    app.focus_key = "row0"
    app.on_pad_button(pygame.CONTROLLER_BUTTON_A)          # cross: toggle
    assert not app.selected_games()
    app.on_pad_button(pygame.CONTROLLER_BUTTON_X)          # square: all
    assert app.selected_games()
    app.on_pad_button(pygame.CONTROLLER_BUTTON_DPAD_DOWN)  # moves focus
    assert app.focus_key != "row0"
    app.on_pad_button(pygame.CONTROLLER_BUTTON_BACK)       # Select/Back key: back
    assert app.screen == "home"


def test_window_scale_is_compact():
    from psone2ps3pkg.gui import theme as th

    assert th.pick_scale((2560, 1440)) == 1.25   # 800x600
    assert th.pick_scale((3840, 2160)) == 1.75   # 1120x840
    assert th.pick_scale((1920, 1080)) == 1.0    # 640x480, never smaller
    assert th.pick_scale((1366, 768)) == 1.0


def test_support_line_intro_hover_and_click(app, monkeypatch):
    opened = []
    monkeypatch.setattr(gui_app.picker, "open_folder", lambda target: opened.append(target) or True)

    def run(seconds):
        for _ in range(int(seconds * 60)):
            app.tick(1 / 60, [])

    run(1.0)
    assert app.support_mix == 1.0                 # intro: support line shown
    run(3.0)
    assert app.support_mix == 0.0                 # then the version
    ver = app.version_rect
    app.mouse = ver.center
    run(0.5)
    assert app.support_mix == 1.0                 # hover reveals it again
    app.on_click(app.support_rect.center)
    assert opened == [gui_app.KOFI_URL]
    app.mouse = (300, 300)
    run(0.5)
    assert app.support_mix == 0.0                 # leaving hides it
    app.on_click(app.support_rect.center)         # hidden: no click-through
    assert len(opened) == 1


def test_row_click_selects_checkbox_click_toggles(app, tmp_path: Path):
    write_cue_game(tmp_path, "Alpha", "SLUS_900.31")
    write_cue_game(tmp_path, "Beta", "SLUS_900.32")
    write_cue_game(tmp_path, "Gamma", "SLUS_900.33")
    pump(app, lambda: app.screen == "games",
         events=[pygame.event.Event(pygame.DROPFILE, file=str(tmp_path))])
    pump(app, lambda: True)
    rows = {it.key: it.rect for it in app.items if it.key.startswith("row")}

    # click the middle of the second row: it becomes the selected game, check mark unchanged
    pump(app, lambda: app.focus_key == "row1", events=click(rows["row1"].center))
    assert len(app.selected_games()) == 3

    # the pointer passes over other rows on its way to the bottom bar
    for y in (rows["row2"].centery, 300, 380):
        pygame.event.post(pygame.event.Event(pygame.MOUSEMOTION, pos=(300, y), rel=(0, 0), buttons=(0, 0, 0)))
    pump(app, lambda: True)
    assert app.focus_key == "row1"

    # DETAILS opens for the selected game (Beta)
    pump(app, lambda: app.overlay is not None, events=click(hint_pos(app, "triangle")))
    assert app.overlay.title == "Beta"
    app.close_overlay()
    pump(app, lambda: True)

    # only the checkbox toggles the check mark
    cb = (rows["row1"].left + 19, rows["row1"].centery)
    pump(app, lambda: len(app.selected_games()) == 2, events=click(cb))
    assert not app.checked[app.game_key(app.lib.games[1])]
