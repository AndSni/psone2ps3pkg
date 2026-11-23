"""The pygame window: drop discs, pick games and options, convert.

Controls
  mouse            click anything, wheel scrolls
  arrows / d-pad   move, left/right change an option
  Enter / Space    (cross)    select
  Esc / Backspace  (circle)   back
  I                (triangle) details
  A                (square)   select all / none
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

try:  # SDL game-controller API: one button layout for all known pads
    from pygame._sdl2 import controller as _controller  # noqa: E402
except ImportError:  # pragma: no cover
    _controller = None  # type: ignore[assignment]

from psone2ps3pkg import __version__  # noqa: E402
from psone2ps3pkg.convert import (  # noqa: E402
    Options,
    Result,
    Target,
    build_game,
    expected_output,
    stage_of,
)
from psone2ps3pkg.gui import picker, settings  # noqa: E402
from psone2ps3pkg.gui import theme as th  # noqa: E402
from psone2ps3pkg.library import Game, Library, scan_paths  # noqa: E402

ICON = Path(__file__).with_name("icon.png")
TARGETS = [Target.PS3, Target.PSP, Target.PS2]
KOFI_URL = "https://ko-fi.com/asnidev"
SUPPORT_TEXT = "If you like this app, consider supporting me on "
SUPPORT_LINK = "Ko-fi"
SUPPORT_INTRO = 3.0  # seconds the support line shows at start
OPTION_VALUE_X = 190  # x of an option row's left arrow, from the row's left edge
TARGET_NOTES = {
    Target.PS3: "INSTALL ON PS3 (CFW / HEN) FROM PACKAGE MANAGER",
    Target.PSP: "COPY THE PSP FOLDER TO A PSP OR VITA (ADRENALINE)",
    Target.PS2: "COPY POPS AND ART TO A POPSTARTER USB / HDD",
}


@dataclass
class Item:
    key: str
    rect: pygame.Rect | None
    activate: Callable[[], None] | None = None
    left: Callable[[], None] | None = None
    right: Callable[[], None] | None = None
    row: int | None = None  # list index, for scrolling
    click: Callable[[tuple[int, int]], None] | None = None  # mouse action, if not `activate`


@dataclass
class Overlay:
    title: str
    lines: list[tuple[str, tuple[int, int, int]]]
    scroll: int = 0
    on_close: Callable[[], None] | None = None
    buttons: list[tuple[str, Callable[[], None]]] | None = None


class Browser:
    """In-window file browser, used when no native dialog tool exists."""

    def __init__(self, kind: str, start: Path, done: Callable[[list[Path] | None], None]):
        self.kind = kind
        self.done = done
        self.cwd = start if start.is_dir() else Path.home()
        self.scroll = 0
        self.entries: list[tuple[str, Path | None, str]] = []
        self.refresh()

    def refresh(self) -> None:
        rows: list[tuple[str, Path | None, str]] = []
        if self.kind in ("folder", "output"):
            rows.append(("USE THIS FOLDER", self.cwd, "use"))
        if self.cwd.parent != self.cwd:
            rows.append((".. (UP)", self.cwd.parent, "dir"))
        try:
            children = sorted(self.cwd.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            children = []
        for p in children:
            if p.name.startswith("."):
                continue
            try:
                if p.is_dir():
                    rows.append((p.name + "/", p, "dir"))
                elif self.kind == "file" and p.suffix.lower() in (".cue", ".bin", ".img", ".iso", ".chd"):
                    rows.append((p.name, p, "file"))
            except OSError:
                continue
        self.entries = rows
        self.scroll = 0

    def choose(self, i: int) -> None:
        label, path, kind = self.entries[i]
        if kind == "dir" and path is not None:
            self.cwd = path
            self.refresh()
        elif kind in ("use", "file") and path is not None:
            self.done([path])


class App:
    ROW_H = 42

    def __init__(self, paths: list[str] | None = None, surface: pygame.Surface | None = None):
        pygame.init()
        self.surface = surface or self._open_window()
        self.bg = th.Background()
        self.clock = pygame.time.Clock()
        self.t = 0.0
        self.start_time = time.monotonic()
        self.q: queue.Queue = queue.Queue()
        self.quit = False

        self.screen = "home"
        self.items: list[Item] = []
        self.focus_key: str | None = None
        self.overlay: Overlay | None = None
        self.browser: Browser | None = None
        self.busy_text: str | None = None

        self.sources: list[Path] = []
        self.lib: Library | None = None
        self.checked: dict[str, bool] = {}
        self.scroll = 0
        self.scan_status = ""

        self.results: list[Result] = []
        self.todo: list[Game] = []
        self.current = 0
        self.stage = 0.0
        self.game_started = 0.0
        self.log: deque[str] = deque(maxlen=300)
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None

        self.pending_drops: list[Path] = []
        self.last_drop = 0.0
        self.joysticks: dict[int, object] = {}
        self.controllers: dict[int, object] = {}
        self.hint_hits: list[tuple[str, pygame.Rect]] = []
        # version slot: support line at start and on hover, else the version
        self.mouse: tuple[int, int] = (-1, -1)
        self.support_mix = 1.0  # 1 = support line shown, 0 = version shown
        self.support_rect: pygame.Rect | None = None  # clickable when shown
        self.version_rect: pygame.Rect | None = None
        self.hand_cursor = False
        try:
            _controller.init()
        except (pygame.error, AttributeError):
            pass

        s = settings.load()
        self.options = Options(
            target=Target(s.get("target", "ps3")) if s.get("target") in ("ps3", "psp", "ps2") else Target.PS3,
            output_dir=Path(s["output_dir"]) if s.get("output_dir") else None,
            ps1_newemu=bool(s.get("ps1_newemu", False)),
            artwork=bool(s.get("artwork", True)),
        )
        self.last_dir = Path(s.get("last_dir") or Path.home())

        if paths:
            self.start_scan([Path(p) for p in paths])

    # ------------------------------------------------------------------ setup
    def _open_window(self) -> pygame.Surface:
        pygame.display.set_caption("psone2ps3pkg")
        if ICON.is_file():
            try:
                pygame.display.set_icon(pygame.image.load(str(ICON)))
            except pygame.error:
                pass
        # Draw at the real window size (no pixel upscaling): layout units are
        # multiplied by th.K, picked from the screen size.
        forced = os.environ.get("PSONE2PS3PKG_SCALE")
        if forced:
            th.set_scale(float(forced))
        elif os.environ.get("SDL_VIDEODRIVER") == "dummy":
            th.set_scale(1.0)
        else:
            try:
                sizes = pygame.display.get_desktop_sizes()
            except pygame.error:
                sizes = []
            th.set_scale(th.pick_scale(sizes[0] if sizes else None))
        surf = pygame.display.set_mode(th.window_size())
        pygame.key.set_repeat(320, 60)
        return surf

    # -------------------------------------------------------------- main loop
    def run(self) -> int:
        while not self.quit:
            dt = self.clock.tick(60) / 1000.0
            self.tick(dt, pygame.event.get())
            pygame.display.flip()
        self.shutdown()
        return 0

    def tick(self, dt: float, events: list) -> None:
        self.t += dt
        for ev in events:
            self.handle(ev)
        if self.pending_drops and time.monotonic() - self.last_drop > 0.25:
            drops, self.pending_drops = self.pending_drops, []
            self.on_drop(drops)
        self.drain()
        self.draw()

    def shutdown(self) -> None:
        self.cancel.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=8)
        self.save_settings()
        th.reset_fonts()
        pygame.quit()

    def save_settings(self) -> None:
        settings.save({
            "target": self.options.target.value,
            "output_dir": str(self.options.output_dir) if self.options.output_dir else None,
            "ps1_newemu": self.options.ps1_newemu,
            "artwork": self.options.artwork,
            "last_dir": str(self.last_dir),
        })

    # ------------------------------------------------------------ worker I/O
    def drain(self) -> None:
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                return
            kind = msg[0]
            if kind == "scan_progress":
                self.scan_status = msg[1]
            elif kind == "scan_done":
                self.on_scan_done(msg[1])
            elif kind == "scan_error":
                self.screen = "home"
                self.show("SCAN FAILED", [(msg[1], th.BAD)])
            elif kind == "line":
                self.log.append(msg[1])
                st = stage_of(msg[1])
                if st is not None:
                    self.stage = max(self.stage, st)
            elif kind == "game_start":
                self.current = msg[1]
                self.stage = 0.0
                self.game_started = time.monotonic()
                self.log.append("")
                self.log.append(f"== {self.todo[msg[1]].title} ==")
            elif kind == "game_done":
                self.results.append(msg[1])
            elif kind == "all_done":
                self.worker = None
                self.screen = "done"
                self.focus_key = "open"
            elif kind == "picked":
                self.busy_text = None
                self.on_picked(msg[1], msg[2])
            elif kind == "pick_failed":
                self.busy_text = None
                self.open_browser(msg[1])

    # -------------------------------------------------------------- actions
    def start_scan(self, paths: list[Path], merge: bool = False) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.sources = (self.sources + paths) if merge else list(paths)
        if paths:
            first = paths[0]
            self.last_dir = first if first.is_dir() else first.parent
        self.screen = "scan"
        self.scan_status = ""
        sources = list(self.sources)

        def work() -> None:
            try:
                lib = scan_paths(sources, progress=lambda s: self.q.put(("scan_progress", s)))
                self.q.put(("scan_done", lib))
            except Exception as exc:  # noqa: BLE001 - shown to the user
                self.q.put(("scan_error", f"{type(exc).__name__}: {exc}"))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def on_scan_done(self, lib: Library) -> None:
        self.worker = None
        if not lib.games:
            self.screen = "home"
            self.lib = None
            lines = [("No PlayStation 1 disc image was found.", th.TEXT), ("", th.TEXT)]
            lines += self._skipped_lines(lib)
            if not lib.skipped:
                lines.append(("Supported: .cue + .bin, .bin, .img, raw .iso, .chd", th.DIM))
            self.show("NO GAMES FOUND", lines)
            return
        old = self.checked
        self.lib = lib
        self.checked = {self.game_key(g): old.get(self.game_key(g), True) for g in lib.games}
        self.scroll = 0
        self.screen = "games"
        self.focus_key = "start"

    @staticmethod
    def game_key(g: Game) -> str:
        return str(g.discs[0].image.primary)

    def selected_games(self) -> list[Game]:
        if not self.lib:
            return []
        return [g for g in self.lib.games if self.checked.get(self.game_key(g))]

    def start_convert(self) -> None:
        games = self.selected_games()
        if not games or (self.worker and self.worker.is_alive()):
            return
        self.save_settings()
        self.todo = games
        self.results = []
        self.current = 0
        self.log.clear()
        self.cancel = threading.Event()
        self.game_started = time.monotonic()
        self.screen = "convert"
        self.focus_key = "cancel"
        options = Options(**vars(self.options))
        cancel = self.cancel

        def work() -> None:
            for i, game in enumerate(games):
                if cancel.is_set():
                    self.q.put(("game_done", Result(game, error="cancelled")))
                    continue
                self.q.put(("game_start", i))
                res = build_game(game, options, on_line=lambda s: self.q.put(("line", s)), cancel=cancel)
                self.q.put(("game_done", res))
            self.q.put(("all_done",))

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def choose(self, kind: str) -> None:
        start = self.options.output_dir if kind == "output" and self.options.output_dir else self.last_dir
        if not start or not Path(start).is_dir():
            start = Path.home()
        tool = picker.available()
        if not tool or os.environ.get("PSONE2PS3PKG_BUILTIN_PICKER"):
            self.open_browser(kind)
            return
        self.busy_text = "WAITING FOR THE FILE DIALOG…"

        def work() -> None:
            try:
                res = picker.pick(tool, kind, Path(start))
                self.q.put(("picked", kind, res))
            except OSError:
                self.q.put(("pick_failed", kind))

        threading.Thread(target=work, daemon=True).start()

    def open_browser(self, kind: str) -> None:
        start = self.options.output_dir if kind == "output" and self.options.output_dir else self.last_dir
        self.browser = Browser(kind, Path(start or Path.home()),
                               lambda res: self._browser_done(kind, res))
        self.focus_key = None

    def _browser_done(self, kind: str, res: list[Path] | None) -> None:
        self.browser = None
        self.on_picked(kind, res)

    def on_picked(self, kind: str, res: list[Path] | None) -> None:
        if not res:
            return
        if kind == "output":
            self.options.output_dir = res[0]
            self.save_settings()
        else:
            self.start_scan(res)

    def on_drop(self, paths: list[Path]) -> None:
        if self.screen in ("scan", "convert") or self.busy_text:
            return
        self.overlay = None
        self.browser = None
        self.start_scan(paths, merge=self.screen == "games")

    def back(self) -> None:
        if self.overlay:
            self.close_overlay()
        elif self.browser:
            self.browser.done(None)
        elif self.screen == "games":
            self.screen = "home"
            self.focus_key = None
        elif self.screen == "done":
            self.screen = "games" if self.lib else "home"
            self.focus_key = "start"
        elif self.screen == "convert":
            self.cancel.set()
        elif self.screen == "home":
            self.quit = True

    def show(self, title: str, lines, on_close=None, buttons=None) -> None:
        self.overlay = Overlay(title, lines, on_close=on_close, buttons=buttons)
        self.focus_key = None

    def close_overlay(self) -> None:
        ov, self.overlay = self.overlay, None
        if ov and ov.on_close:
            ov.on_close()

    def _skipped_lines(self, lib: Library) -> list[tuple[str, tuple]]:
        out: list[tuple[str, tuple]] = []
        for s in lib.skipped:
            out.append((s.path.name, th.TEXT))
            out.append(("   " + s.reason, th.DIM))
        return out

    def show_skipped(self) -> None:
        if self.lib:
            self.show(f"{len(self.lib.skipped)} FILES SKIPPED", self._skipped_lines(self.lib))

    def show_details(self, g: Game) -> None:
        lines: list[tuple[str, tuple]] = [
            (f"Serial: {', '.join(g.serials)}  ·  Region: {g.region}", th.TEXT),
            (f"Output: {expected_output(g, self.options)}", th.DIM),
            ("", th.TEXT),
        ]
        for i, d in enumerate(g.discs, 1):
            lines.append((f"Disc {i}: {d.image.primary}", th.TEXT))
            for extra in d.image.extra_tracks:
                lines.append(("   + " + extra.name, th.DIM))
        if g.warnings:
            lines.append(("", th.TEXT))
            for w in g.warnings:
                lines.append(("! " + w, th.WARN))
        self.show(g.title, lines)

    def toggle_all(self) -> None:
        if not self.lib:
            return
        new = not all(self.checked.values())
        for k in self.checked:
            self.checked[k] = new

    def cycle_target(self, step: int) -> None:
        i = TARGETS.index(self.options.target)
        self.options.target = TARGETS[(i + step) % len(TARGETS)]

    def open_output(self) -> None:
        for r in self.results:
            if r.ok and r.outputs:
                p = r.outputs[0]
                folder = p.parent if p.is_file() else p
                if self.options.target is Target.PSP:
                    folder = p.parent.parent.parent.parent  # .../PSP/GAME/<ID>/EBOOT.PBP
                elif self.options.target is Target.PS2:
                    folder = p.parent.parent  # .../POPS/<file>.VCD
                if not picker.open_folder(folder):
                    self.show("OUTPUT FOLDER", [(str(folder), th.TEXT)])
                return

    # ---------------------------------------------------------------- input
    def handle(self, ev) -> None:
        if ev.type == pygame.QUIT:
            if self.worker and self.worker.is_alive() and self.screen == "convert":
                self.cancel.set()
            self.quit = True
        elif ev.type == pygame.DROPFILE:
            self.pending_drops.append(Path(ev.file))
            self.last_drop = time.monotonic()
        elif ev.type == pygame.DROPTEXT:
            for line in ev.text.splitlines():
                line = line.strip()
                if line.startswith("file://"):
                    self.pending_drops.append(Path(unquote(urlparse(line).path)))
                    self.last_drop = time.monotonic()
        elif self.busy_text:
            return
        elif ev.type == pygame.KEYDOWN:
            self.on_key(ev.key)
        elif ev.type == getattr(pygame, "CONTROLLERDEVICEADDED", -1):
            try:
                c = _controller.Controller(ev.device_index)
                self.controllers[c.as_joystick().get_instance_id()] = c
            except (pygame.error, AttributeError):
                pass
        elif ev.type == getattr(pygame, "CONTROLLERBUTTONDOWN", -1):
            self.on_pad_button(ev.button)
        elif ev.type == pygame.JOYDEVICEADDED:
            # Pads SDL does not know as game controllers: raw joystick events.
            try:
                if _controller and _controller.is_controller(ev.device_index):
                    return
                j = pygame.joystick.Joystick(ev.device_index)
                self.joysticks[j.get_instance_id()] = j
            except pygame.error:
                pass
        elif ev.type == pygame.JOYHATMOTION:
            if getattr(ev, "instance_id", None) in self.controllers:
                return
            x, y = ev.value
            if y == 1:
                self.on_key(pygame.K_UP)
            elif y == -1:
                self.on_key(pygame.K_DOWN)
            elif x == -1:
                self.on_key(pygame.K_LEFT)
            elif x == 1:
                self.on_key(pygame.K_RIGHT)
        elif ev.type == pygame.JOYBUTTONDOWN:
            if getattr(ev, "instance_id", None) in self.controllers:
                return  # handled as CONTROLLERBUTTONDOWN
            # Unknown pad: guess the usual order (bottom, right, left, top).
            mapping = {0: "cross", 1: "circle", 2: "square", 3: "triangle"}
            if ev.button in mapping:
                self.press(mapping[ev.button])
        elif ev.type == pygame.MOUSEMOTION:
            # Hover only highlights; it never moves the selection, so the
            # pointer can travel to DETAILS without changing the chosen game.
            self.mouse = th.to_layout(ev.pos)
        elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            self.on_click(th.to_layout(ev.pos))
        elif ev.type == pygame.MOUSEWHEEL:
            self.on_wheel(-ev.y)

    def on_pad_button(self, button: int) -> None:
        c = pygame
        face = {
            getattr(c, "CONTROLLER_BUTTON_A", -10): "cross",      # bottom face button
            getattr(c, "CONTROLLER_BUTTON_B", -11): "circle",     # right
            getattr(c, "CONTROLLER_BUTTON_X", -12): "square",     # left
            getattr(c, "CONTROLLER_BUTTON_Y", -13): "triangle",   # top
            getattr(c, "CONTROLLER_BUTTON_BACK", -14): "circle",  # Select / Back / Share
            getattr(c, "CONTROLLER_BUTTON_START", -15): "cross",  # Start / Options
        }
        dpad = {
            getattr(c, "CONTROLLER_BUTTON_DPAD_UP", -20): pygame.K_UP,
            getattr(c, "CONTROLLER_BUTTON_DPAD_DOWN", -21): pygame.K_DOWN,
            getattr(c, "CONTROLLER_BUTTON_DPAD_LEFT", -22): pygame.K_LEFT,
            getattr(c, "CONTROLLER_BUTTON_DPAD_RIGHT", -23): pygame.K_RIGHT,
        }
        if button in face:
            self.press(face[button])
        elif button in dpad:
            self.on_key(dpad[button])

    def hints(self, s: pygame.Surface, items: list[tuple[str, str]]) -> None:
        # The last bar drawn in a frame (an overlay's) is the one on screen.
        self.hint_hits = th.hints(s, items)

    def press(self, kind: str) -> None:
        """A face button, from the hint bar or a pad."""
        key = {"cross": pygame.K_RETURN, "circle": pygame.K_ESCAPE,
               "triangle": pygame.K_i, "square": pygame.K_a}[kind]
        self.on_key(key)

    def focus_index(self) -> int:
        for i, it in enumerate(self.items):
            if it.key == self.focus_key:
                return i
        return -1

    def move_focus(self, step: int) -> None:
        if not self.items:
            return
        i = self.focus_index()
        i = 0 if i < 0 else max(0, min(len(self.items) - 1, i + step))
        self.focus_key = self.items[i].key
        self.ensure_visible(self.items[i])

    def ensure_visible(self, it: Item) -> None:
        if it.row is None:
            return
        if self.browser:
            vis = self._browser_visible()
            if it.row < self.browser.scroll:
                self.browser.scroll = it.row
            elif it.row >= self.browser.scroll + vis:
                self.browser.scroll = it.row - vis + 1
        elif self.screen == "games":
            vis = self._list_visible()
            if it.row < self.scroll:
                self.scroll = it.row
            elif it.row >= self.scroll + vis:
                self.scroll = it.row - vis + 1

    def on_key(self, key: int) -> None:
        i = self.focus_index()
        cur = self.items[i] if i >= 0 else None
        if key in (pygame.K_UP, pygame.K_w):
            self.move_focus(-1)
        elif key in (pygame.K_DOWN, pygame.K_s, pygame.K_TAB):
            self.move_focus(1)
        elif key in (pygame.K_LEFT,):
            if cur and cur.left:
                cur.left()
        elif key in (pygame.K_RIGHT,):
            if cur and cur.right:
                cur.right()
        elif key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
            if cur and cur.activate:
                cur.activate()
            elif cur is None:
                self.move_focus(1)
        elif key in (pygame.K_ESCAPE, pygame.K_BACKSPACE):
            self.back()
        elif key == pygame.K_i and self.screen == "games" and not self.overlay and not self.browser:
            if cur and cur.row is not None and self.lib:
                self.show_details(self.lib.games[cur.row])
            elif self.lib and self.lib.skipped:
                self.show_skipped()
        elif key == pygame.K_a and self.screen == "games" and not self.overlay and not self.browser:
            self.toggle_all()
        elif key == pygame.K_PAGEDOWN:
            self.on_wheel(5)
        elif key == pygame.K_PAGEUP:
            self.on_wheel(-5)

    def on_click(self, pos) -> None:
        if self.support_rect and self.support_mix > 0.5 and self.support_rect.collidepoint(pos):
            picker.open_folder(KOFI_URL)
            return
        for kind, rect in self.hint_hits:
            if rect.collidepoint(pos):
                self.press(kind)
                return
        for it in self.items:
            if it.rect and it.rect.collidepoint(pos):
                self.focus_key = it.key
                if it.click:
                    it.click(pos)
                    return
                if it.left and it.right:
                    # option rows: the left arrow steps back, the right arrow
                    # steps forward, the rest of the row activates
                    arrow_l = it.rect.left + OPTION_VALUE_X
                    if arrow_l - 8 <= pos[0] <= arrow_l + 16:
                        it.left()
                    elif pos[0] >= it.rect.right - 34:
                        it.right()
                    else:
                        (it.activate or it.right)()
                elif it.activate:
                    it.activate()
                return

    def on_wheel(self, step: int) -> None:
        if self.overlay:
            self.overlay.scroll = max(0, self.overlay.scroll + step)
        elif self.browser:
            n = len(self.browser.entries)
            self.browser.scroll = max(0, min(max(0, n - self._browser_visible()), self.browser.scroll + step))
        elif self.screen == "games" and self.lib:
            n = len(self.lib.games)
            self.scroll = max(0, min(max(0, n - self._list_visible()), self.scroll + step))

    # ---------------------------------------------------------------- drawing
    def draw(self) -> None:
        s = self.surface
        self.bg.draw(s, self.t)
        self.items = []
        if self.browser:
            self.draw_browser(s)
        else:
            getattr(self, f"draw_{self.screen}")(s)
        if self.overlay:
            self.items = []
            self.draw_overlay(s)
        if self.busy_text:
            self.items = []
            r = pygame.Rect(0, 0, 420, 90)
            r.center = (th.W // 2, th.H // 2)
            th.panel(s, r)
            th.disc(s, (r.left + 46, r.centery), 26, self.t)
            th.text(s, self.busy_text, 19, (r.left + 86, r.centery - 10), max_w=320)
        if self.focus_key is None or self.focus_index() < 0:
            if self.items:
                self.focus_key = self.items[0].key
        # boot fade
        age = time.monotonic() - self.start_time
        if age < 0.7:
            veil = pygame.Surface(s.get_size())
            veil.set_alpha(int(255 * (1 - age / 0.7)))
            s.blit(veil, (0, 0))

    def header(self, s: pygame.Surface, title: str, right: str = "") -> None:
        th.text(s, "PSONE", 30, (22, 14), th.TEXT)
        w = th.measure("PSONE", 30)[0]
        th.text(s, "2", 30, (24 + w, 14), th.ACCENT)
        w2 = th.measure("2", 30)[0]
        th.text(s, "PS3PKG", 30, (26 + w + w2, 14), th.TEXT)
        th.text(s, title, 18, (th.W // 2 + 40, 20), th.DIM, align="center")
        if right:
            th.text(s, right, 16, (th.W - 20, 22), th.FAINT, align="right")
            self.support_rect = self.version_rect = None
        else:
            self.version_slot(s)
        th.line(s, (80, 86, 130), (20, 46), (th.W - 20, 46))

    def version_slot(self, s: pygame.Surface) -> None:
        """Version number; the Ko-fi line shows for the first seconds and on hover."""
        x_right, y = th.W - 20, 22
        size = 14
        w_text = th.measure(SUPPORT_TEXT, size)[0]
        w_link = th.measure(SUPPORT_LINK, size)[0]
        heart_w = 16
        left = x_right - w_link - w_text - heart_w - 6
        support = pygame.Rect(left - 4, y - 4, x_right - left + 8, 24)
        version = pygame.Rect(x_right - th.measure(f"v{__version__}", 16)[0] - 6, y - 4,
                              th.measure(f"v{__version__}", 16)[0] + 10, 24)

        # target: shown during the intro, then only while the pointer is on it
        hover_zone = support if self.support_mix > 0.3 else version
        hovered = hover_zone.collidepoint(self.mouse)
        target = 1.0 if (self.t < SUPPORT_INTRO or hovered) else 0.0
        step = 1 / 60 * 3.0  # ~0.33 s fade
        if self.support_mix < target:
            self.support_mix = min(target, self.support_mix + step)
        elif self.support_mix > target:
            self.support_mix = max(target, self.support_mix - step)

        m = self.support_mix
        if m > 0:
            a = int(255 * m)
            th.heart(s, (left + heart_w / 2 - 2, y + 7), 13, alpha=a)
            th.text(s, SUPPORT_TEXT, size, (left + heart_w, y + 1), th.DIM, alpha=a, shadow=False)
            lx = left + heart_w + w_text
            th.text(s, SUPPORT_LINK, size, (lx, y + 1), th.ACCENT, alpha=a, shadow=False)
            if m > 0.6:  # plain lines cannot fade; show the underline when mostly visible
                th.line(s, th.ACCENT, (lx, y + 17), (lx + w_link, y + 17))
        if m < 1:
            th.text(s, f"v{__version__}", 16, (x_right, y), th.FAINT, align="right", alpha=int(255 * (1 - m)))
        self.support_rect = support
        self.version_rect = version

        want_hand = m > 0.5 and support.collidepoint(self.mouse)
        if want_hand != self.hand_cursor:
            self.hand_cursor = want_hand
            try:
                pygame.mouse.set_cursor(pygame.SYSTEM_CURSOR_HAND if want_hand else pygame.SYSTEM_CURSOR_ARROW)
            except (pygame.error, AttributeError):
                pass

    def focused(self, key: str) -> bool:
        return self.focus_key == key

    # home ---------------------------------------------------------------
    def draw_home(self, s: pygame.Surface) -> None:
        self.header(s, "")
        zone = pygame.Rect(50, 66, th.W - 100, 252)
        th.panel(s, zone, (10, 14, 40, 170))
        th.disc(s, (zone.centerx, zone.top + 92), 58, self.t)
        th.text(s, "DROP DISC IMAGES OR A FOLDER HERE", 24, (zone.centerx, zone.top + 170), align="center")
        th.text(s, "CUE + BIN  ·  CHD  ·  ISO  ·  IMG  —  ONE GAME OR A WHOLE COLLECTION", 15,
                (zone.centerx, zone.top + 202), th.DIM, align="center")
        self.items.append(Item("zone", zone, lambda: self.choose("folder")))

        b1 = pygame.Rect(0, 0, 220, 40)
        b2 = b1.copy()
        b1.topright = (th.W // 2 - 10, 342)
        b2.topleft = (th.W // 2 + 10, 342)
        th.button(s, b1, "CHOOSE FOLDER", self.focused("folder"), self.t)
        th.button(s, b2, "CHOOSE FILES", self.focused("file"), self.t)
        self.items.append(Item("folder", b1, lambda: self.choose("folder")))
        self.items.append(Item("file", b2, lambda: self.choose("file")))
        if self.focus_key in (None, "zone"):
            self.focus_key = "folder"
        th.text(s, f"OUTPUT: {self.options.target.label}  —  CHANGE IT AFTER THE SCAN", 15,
                (th.W // 2, 404), th.FAINT, align="center")
        self.hints(s, [("cross", "SELECT"), ("circle", "QUIT")])

    # scan ---------------------------------------------------------------
    def draw_scan(self, s: pygame.Surface) -> None:
        self.header(s, "")
        th.disc(s, (th.W // 2, 190), 70, self.t * 2.5)
        th.text(s, "READING DISCS…", 26, (th.W // 2, 290), align="center")
        th.text(s, self.scan_status, 16, (th.W // 2, 326), th.DIM, align="center", max_w=560)
        self.hints(s, [])

    # games --------------------------------------------------------------
    def _list_rect(self) -> pygame.Rect:
        return pygame.Rect(20, 56, th.W - 40, 216)

    def _list_visible(self) -> int:
        return (self._list_rect().height - 6) // self.ROW_H

    def draw_games(self, s: pygame.Surface) -> None:
        assert self.lib is not None
        games = self.lib.games
        n_sel = len(self.selected_games())
        self.header(s, "", f"{len(games)} GAME{'S' if len(games) != 1 else ''} FOUND")
        lr = self._list_rect()
        th.panel(s, lr)
        vis = self._list_visible()
        self.scroll = max(0, min(self.scroll, max(0, len(games) - vis)))
        for i, g in enumerate(games):
            key = f"row{i}"
            if not (self.scroll <= i < self.scroll + vis):
                self.items.append(Item(key, None, self._toggler(g), row=i))
                continue
            y = lr.top + 3 + (i - self.scroll) * self.ROW_H
            row = pygame.Rect(lr.left + 3, y, lr.width - 18, self.ROW_H - 1)
            if self.focused(key):
                th.fill_alpha(s, row, (120, 170, 255, 46))
                th.rect(s, th.ACCENT, row, 1)
            elif row.collidepoint(self.mouse):
                th.fill_alpha(s, row, (120, 170, 255, 18))
            cb = pygame.Rect(row.left + 10, row.centery - 9, 18, 18)
            th.checkbox(s, cb, self.checked.get(self.game_key(g), False))
            info = pygame.Rect(row.right - 34, row.centery - 11, 22, 22)
            th.circle(s, th.DIM, info.center, 10, 1.5)
            th.text(s, "i", 17, (info.centerx, info.top + 2), th.DIM, align="center", shadow=False)
            th.text(s, g.title, 19, (row.left + 40, row.top + 5), max_w=row.width - 90)
            discs = len(g.discs)
            sub = f"{' + '.join(g.serials)}  ·  {g.region}  ·  {discs} DISC{'S' if discs > 1 else ''}  ·  {g.kinds}"
            th.text(s, sub, 15, (row.left + 40, row.top + 24), th.DIM, max_w=row.width - 200, shadow=False)
            tag, colour = "", th.WARN
            if expected_output(g, self.options).exists():
                tag = "ALREADY BUILT"
            elif g.warnings:
                tag = "! NOTE"
            if tag:
                th.text(s, tag, 14, (row.right - 44, row.top + 25), colour, align="right", shadow=False)
            toggle = self._toggler(g)
            hit = cb.inflate(14, 14)  # a little larger than the box
            self.items.append(Item(key, row, toggle, row=i,
                                   click=lambda pos, hit=hit, toggle=toggle: hit.collidepoint(pos) and toggle()))
            # the info circle sits on top of the row; check it first on click
            self.items.insert(len(self.items) - 1, Item(f"info{i}", info, lambda g=g: self.show_details(g)))
        if len(games) > vis:
            track = pygame.Rect(lr.right - 10, lr.top + 4, 5, lr.height - 8)
            th.rect(s, (40, 44, 80), track)
            h = max(16, track.height * vis // len(games))
            y = track.top + (track.height - h) * self.scroll // max(1, len(games) - vis)
            th.rect(s, th.EDGE_LIGHT, (track.left, y, track.width, h))

        # skipped line
        if self.lib.skipped:
            r = th.text(s, f"{len(self.lib.skipped)} FILE{'S' if len(self.lib.skipped) != 1 else ''} SKIPPED — SHOW WHY",
                        15, (24, 280), th.WARN if self.focused("skipped") else th.DIM)
            self.items.append(Item("skipped", r.inflate(8, 4), self.show_skipped))
        th.text(s, TARGET_NOTES[self.options.target], 14, (th.W - 22, 281), th.DIM, align="right")

        # options
        op = pygame.Rect(20, 300, th.W - 40, 108)
        th.panel(s, op)
        o = self.options
        rows = [
            ("target", "OUTPUT TYPE", o.target.label, True,
             lambda: self.cycle_target(-1), lambda: self.cycle_target(1)),
            ("emu", "PS3 EMULATOR", ("PS1_NEWEMU" if o.ps1_newemu else "PS1_NETEMU (DEFAULT)")
             if o.target is Target.PS3 else "—", o.target is Target.PS3,
             self._flip("ps1_newemu"), self._flip("ps1_newemu")),
            ("art", "COVER ART", "DOWNLOAD" if o.artwork else "NONE (OFFLINE)", True,
             self._flip("artwork"), self._flip("artwork")),
            ("out", "SAVE TO", str(o.output_dir) if o.output_dir else "NEXT TO EACH GAME", True,
             self._clear_output, lambda: self.choose("output")),
        ]
        for j, (key, label, value, enabled, left, right) in enumerate(rows):
            r = pygame.Rect(op.left + 4, op.top + 4 + j * 25, op.width - 8, 25)
            if self.focused(key) and enabled:
                th.fill_alpha(s, r, (120, 170, 255, 40))
            th.text(s, label, 16, (r.left + 10, r.top + 6), th.DIM if enabled else th.FAINT, shadow=False)
            colour = th.TEXT if enabled else th.FAINT
            vx = r.left + OPTION_VALUE_X
            if enabled:
                arrow_c = th.ACCENT if self.focused(key) else th.FAINT
                th.polygon(s, arrow_c, [(vx, r.centery), (vx + 8, r.centery - 6), (vx + 8, r.centery + 6)])
                ex = r.right - 14
                th.polygon(s, arrow_c, [(ex, r.centery), (ex - 8, r.centery - 6), (ex - 8, r.centery + 6)])
            th.text(s, value, 17, (vx + 18, r.top + 5), colour, max_w=r.right - vx - 50,
                    left_ellipsis=key == "out", shadow=False)
            if enabled:
                activate = (lambda: self.choose("output")) if key == "out" else right
                self.items.append(Item(key, r, activate, left, right))

        back = pygame.Rect(20, 416, 150, 32)
        go = pygame.Rect(th.W - 20 - 260, 416, 260, 32)
        th.button(s, back, "BACK", self.focused("back"), self.t, glyph="circle")
        label = f"CONVERT {n_sel} GAME{'S' if n_sel != 1 else ''}" if n_sel else "NOTHING SELECTED"
        th.button(s, go, label, self.focused("start"), self.t, enabled=n_sel > 0, glyph="cross")
        self.items.append(Item("back", back, self.back))
        self.items.append(Item("start", go, self.start_convert))
        self.hints(s, [("cross", "SELECT"), ("triangle", "DETAILS"), ("square", "ALL / NONE"), ("circle", "BACK")])

    def _toggler(self, g: Game) -> Callable[[], None]:
        def f() -> None:
            k = self.game_key(g)
            self.checked[k] = not self.checked.get(k, False)
        return f

    def _flip(self, attr: str) -> Callable[[], None]:
        def f() -> None:
            setattr(self.options, attr, not getattr(self.options, attr))
        return f

    def _clear_output(self) -> None:
        self.options.output_dir = None

    # convert ------------------------------------------------------------
    def draw_convert(self, s: pygame.Surface) -> None:
        total = len(self.todo)
        idx = min(self.current, total - 1)
        game = self.todo[idx]
        self.header(s, "", "CANCELLING…" if self.cancel.is_set() else self.options.target.label)
        top = pygame.Rect(20, 58, th.W - 40, 132)
        th.panel(s, top)
        th.disc(s, (top.left + 58, top.top + 62), 42, self.t * 3)
        th.text(s, f"GAME {idx + 1} OF {total}", 16, (top.left + 120, top.top + 14), th.DIM)
        th.text(s, game.title, 24, (top.left + 120, top.top + 36), max_w=top.width - 140)
        elapsed = int(time.monotonic() - self.game_started)
        th.text(s, f"{len(game.discs)} DISC{'S' if len(game.discs) > 1 else ''}  ·  {elapsed // 60}:{elapsed % 60:02d}",
                16, (top.left + 120, top.top + 66), th.DIM)
        frac = (len(self.results) + (0 if len(self.results) > idx else self.stage)) / max(1, total)
        th.progress(s, pygame.Rect(top.left + 120, top.top + 94, top.width - 140, 18), frac, self.t, busy=True)

        logr = pygame.Rect(20, 200, th.W - 40, 206)
        th.panel(s, logr, (4, 6, 18, 225))
        lines = [ln for ln in self.log if not ln.startswith("$ ")][-11:]
        for k, ln in enumerate(lines):
            th.text(s, ln, 15, (logr.left + 10, logr.top + 8 + k * 17), th.DIM if not ln.startswith("==") else th.TEXT,
                    max_w=logr.width - 20, shadow=False)
        b = pygame.Rect(0, 0, 200, 32)
        b.midtop = (th.W // 2, 416)
        th.button(s, b, "CANCEL", self.focused("cancel"), self.t, enabled=not self.cancel.is_set(), glyph="circle")
        self.items.append(Item("cancel", b, self.cancel.set))
        self.hints(s, [("circle", "CANCEL")])

    # done ---------------------------------------------------------------
    def draw_done(self, s: pygame.Surface) -> None:
        ok = sum(1 for r in self.results if r.ok)
        self.header(s, "", f"{ok} OF {len(self.results)} DONE")
        box = pygame.Rect(20, 58, th.W - 40, 352)
        th.panel(s, box)
        y = box.top + 10
        for r in self.results:
            if y > box.bottom - 40:
                th.text(s, "…", 18, (box.left + 14, y), th.DIM)
                break
            if r.ok:
                th.lines(s, th.OK, False, [(box.left + 14, y + 10), (box.left + 20, y + 16), (box.left + 30, y + 3)], 2.5)
                th.text(s, r.game.title, 19, (box.left + 42, y), max_w=box.width - 60)
                where = str(r.outputs[0]) if r.outputs else ""
                th.text(s, where, 14, (box.left + 42, y + 20), th.DIM, max_w=box.width - 60,
                        left_ellipsis=True, shadow=False)
            else:
                th.line(s, th.BAD, (box.left + 16, y + 3), (box.left + 28, y + 15), 2.5)
                th.line(s, th.BAD, (box.left + 16, y + 15), (box.left + 28, y + 3), 2.5)
                th.text(s, r.game.title, 19, (box.left + 42, y), max_w=box.width - 60)
                err = (r.error or "").strip().splitlines()
                th.text(s, err[0] if err else "failed", 14, (box.left + 42, y + 20), th.BAD,
                        max_w=box.width - 60, shadow=False)
                if len(err) > 1:
                    full = r.error or ""
                    rr = pygame.Rect(box.right - 90, y, 80, 20)
                    th.text(s, "DETAILS", 14, (rr.right, y + 2), th.WARN, align="right", shadow=False)
                    self.items.append(Item(f"err{id(r)}", rr,
                                           lambda full=full, g=r.game: self.show(g.title, [(ln, th.DIM) for ln in full.splitlines()])))
            y += 44
        b1 = pygame.Rect(20, 416, 190, 32)
        b2 = pygame.Rect(0, 0, 190, 32)
        b2.midtop = (th.W // 2, 416)
        b3 = pygame.Rect(th.W - 210, 416, 190, 32)
        th.button(s, b1, "BACK TO LIST", self.focused("list"), self.t, glyph="circle")
        th.button(s, b2, "OPEN FOLDER", self.focused("open"), self.t, enabled=ok > 0, glyph="cross")
        th.button(s, b3, "NEW DISCS", self.focused("new"), self.t)
        self.items.append(Item("list", b1, self.back))
        if ok:
            self.items.append(Item("open", b2, self.open_output))
        self.items.append(Item("new", b3, self._new))
        self.hints(s, [("cross", "SELECT"), ("circle", "BACK")])

    def _new(self) -> None:
        self.lib = None
        self.sources = []
        self.screen = "home"
        self.focus_key = "folder"

    # browser ------------------------------------------------------------
    def _browser_visible(self) -> int:
        return (360 - 8) // 26

    def draw_browser(self, s: pygame.Surface) -> None:
        b = self.browser
        assert b is not None
        title = {"folder": "CHOOSE A FOLDER", "file": "CHOOSE A DISC IMAGE", "output": "CHOOSE THE OUTPUT FOLDER"}[b.kind]
        self.header(s, "", title)
        th.text(s, str(b.cwd), 16, (24, 54), th.DIM, max_w=th.W - 48, left_ellipsis=True)
        box = pygame.Rect(20, 76, th.W - 40, 360)
        th.panel(s, box)
        vis = self._browser_visible()
        for i, (label, _path, kind) in enumerate(b.entries):
            key = f"b{i}:{label}"
            if not (b.scroll <= i < b.scroll + vis):
                self.items.append(Item(key, None, lambda i=i: b.choose(i), row=i))
                continue
            r = pygame.Rect(box.left + 4, box.top + 4 + (i - b.scroll) * 26, box.width - 8, 26)
            if self.focused(key):
                th.fill_alpha(s, r, (120, 170, 255, 50))
            colour = th.OK if kind == "use" else (th.TEXT if kind == "file" else th.ACCENT)
            th.text(s, label, 17, (r.left + 10, r.top + 5), colour, max_w=r.width - 20, shadow=False)
            self.items.append(Item(key, r, lambda i=i: b.choose(i), row=i))
        if not b.entries:
            th.text(s, "(EMPTY)", 17, (box.centerx, box.top + 20), th.FAINT, align="center")
        self.hints(s, [("cross", "OPEN / USE"), ("circle", "CANCEL")])

    # overlay ------------------------------------------------------------
    def draw_overlay(self, s: pygame.Surface) -> None:
        ov = self.overlay
        assert ov is not None
        th.fill_alpha(s, (0, 0, th.W, th.H), (0, 0, 10, 160))
        box = pygame.Rect(40, 50, th.W - 80, 380)
        th.panel(s, box, (16, 20, 54, 245))
        th.text(s, ov.title, 21, (box.left + 16, box.top + 12), max_w=box.width - 32)
        th.line(s, (80, 86, 130), (box.left + 12, box.top + 40), (box.right - 12, box.top + 40))
        wrapped: list[tuple[str, tuple]] = []
        for line, colour in ov.lines:
            for w in th.wrap(line, 16, box.width - 36) if line else [""]:
                wrapped.append((w, colour))
        per = (box.height - 110) // 18
        ov.scroll = max(0, min(ov.scroll, max(0, len(wrapped) - per)))
        for k, (line, colour) in enumerate(wrapped[ov.scroll:ov.scroll + per]):
            th.text(s, line, 16, (box.left + 18, box.top + 50 + k * 18), colour, shadow=False)
        if len(wrapped) > per:
            th.text(s, f"{ov.scroll + 1}-{min(len(wrapped), ov.scroll + per)} / {len(wrapped)}  (SCROLL)", 13,
                    (box.right - 16, box.bottom - 70), th.FAINT, align="right", shadow=False)
        b = pygame.Rect(0, 0, 160, 34)
        b.midbottom = (box.centerx, box.bottom - 14)
        th.button(s, b, "OK", True, self.t, glyph="cross")
        self.items.append(Item("ok", b, self.close_overlay))
        self.focus_key = "ok"
        self.hints(s, [("cross", "OK")])


def run(paths: list[str] | None = None) -> int:
    app = App(paths)
    return app.run()
