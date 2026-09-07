"""Textual drag-and-drop front end."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

from psone2ps3pkg.cli import clean_drop_path
from psone2ps3pkg.convert import Plan, PkgError, PkgResult, build_pkg, plan_directory
from psone2ps3pkg.disc import DiscError


class PkgApp(App):
    CSS = """
    Screen { layout: vertical; }
    #intro { padding: 1 2; }
    #row { height: auto; padding: 0 2; }
    #path { width: 1fr; }
    #go { width: 20; margin-left: 2; }
    #summary { padding: 1 2; height: auto; color: $text-muted; }
    #log { border: round $panel; margin: 1 2; }
    """

    BINDINGS = [("q", "quit", "Quit"), ("ctrl+c", "quit", "Quit")]

    def __init__(self) -> None:
        super().__init__()
        self._plan: Plan | None = None
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(
            "Drag the game folder onto this window, then press Enter.\n"
            "It must hold one PlayStation 1 image: a .cue + .bin, a bare .bin/.img, a .iso, or a .chd.\n"
            "The .pkg is written into that same folder.",
            id="intro",
        )
        with Horizontal(id="row"):
            yield Input(placeholder="/path/to/game folder", id="path")
            yield Button("Create PKG", id="go", variant="primary", disabled=True)
        yield Static("", id="summary")
        yield RichLog(id="log", wrap=True, markup=True)
        yield Footer()

    # --- helpers -------------------------------------------------------- #
    @property
    def log_view(self) -> RichLog:
        return self.query_one("#log", RichLog)

    @property
    def summary_view(self) -> Static:
        return self.query_one("#summary", Static)

    def _set_summary(self, text: str, colour: str | None = None) -> None:
        body = escape(text)
        self.summary_view.update(f"[{colour}]{body}[/{colour}]" if colour else body)

    # --- events ------------------------------------------------------- #
    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "path":
            self._scan(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "go" and self._plan and not self._busy:
            self._build()

    # --- scan ------------------------------------------------------- #
    def _scan(self, raw: str) -> None:
        if self._busy or not raw.strip():
            return
        path = Path(clean_drop_path(raw))
        self._plan = None
        self.query_one("#go", Button).disabled = True
        self.log_view.clear()
        self._set_summary("Reading disc…")
        self.log_view.write(f"[dim]Scanning[/dim] {escape(str(path))}")
        self._scan_worker(str(path))

    @work(thread=True, exclusive=True, group="scan")
    def _scan_worker(self, path: str) -> None:
        try:
            plan = plan_directory(path)
        except (DiscError, PkgError) as exc:
            self.call_from_thread(self._scan_failed, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._scan_failed, f"Unexpected error: {exc}")
        else:
            self.call_from_thread(self._scan_ok, plan)

    def _scan_ok(self, plan: Plan) -> None:
        self._plan = plan
        info, disc = plan.info, plan.disc
        lines = [
            f"{disc.primary.name}  ({disc.kind})",
            f"Serial: {info.serial or 'unknown'}    Region: {info.region}",
            f"Title:  {info.title or '—'}",
            f"Output: {plan.output.name}",
        ]
        lines += [f"! {w}" for w in plan.warnings]
        self._set_summary("\n".join(lines))
        self.log_view.write("[green]Recognised as a PlayStation 1 disc.[/green]")
        self.query_one("#go", Button).disabled = False

    def _scan_failed(self, message: str) -> None:
        self._set_summary(message, colour="red")
        self.log_view.write(f"[red]{escape(message)}[/red]")

    # --- build ---------------------------------------------------- #
    def _build(self) -> None:
        assert self._plan is not None
        self._busy = True
        self.query_one("#go", Button).disabled = True
        self.query_one("#path", Input).disabled = True
        self.log_view.write("[b]Building PKG…[/b] this takes a minute or two.")
        self._build_worker(self._plan)

    @work(thread=True, exclusive=True, group="build")
    def _build_worker(self, plan: Plan) -> None:
        def emit(line: str) -> None:
            self.call_from_thread(self.log_view.write, _fmt(line))

        try:
            result = build_pkg(plan.directory, on_line=emit, plan=plan)
        except (DiscError, PkgError) as exc:
            self.call_from_thread(self._build_done, None, str(exc))
        except Exception as exc:  # noqa: BLE001
            self.call_from_thread(self._build_done, None, f"Unexpected error: {exc}")
        else:
            self.call_from_thread(self._build_done, result, None)

    def _build_done(self, result: PkgResult | None, error: str | None) -> None:
        self._busy = False
        self.query_one("#path", Input).disabled = False
        self.query_one("#go", Button).disabled = False
        if error or result is None:
            msg = f"FAILED: {error}"
            self.log_view.write(f"[red]{escape(msg)}[/red]")
            self._set_summary(msg, colour="red")
            return
        mib = result.size / (1024 * 1024)
        msg = (
            f"Done.  {result.path}\n"
            f"{mib:.1f} MiB   content-id {result.content_id}\n"
            "Copy it to the PS3 and install with the Package Manager (CFW/HEN required)."
        )
        self.log_view.write(f"[green]{escape('Done: ' + str(result.path))}[/green]")
        self._set_summary(msg, colour="green")


def _fmt(line: str) -> str:
    safe = escape(line)
    low = line.lower()
    if line.startswith("$ "):
        return f"[dim]{safe}[/dim]"
    if "finished" in low or " created" in low:
        return f"[green]{safe}[/green]"
    if line.startswith("XXXX") or "not found" in low or "fail" in low or "error" in low:
        return f"[yellow]{safe}[/yellow]"
    return safe


def run() -> None:
    PkgApp().run()
