"""Desktop-menu integration: the .desktop entry and --install / --uninstall."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "psone2ps3pkg"
ICON = Path(__file__).resolve().parent / "gui" / "icon.png"
MIME = "application/x-cue;application/x-cd-image;application/x-iso9660-image;application/x-raw-disk-image;"


def desktop_entry(exec_cmd: str = APP_ID) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=psone2ps3pkg\n"
        "GenericName=PS1 disc converter\n"
        "Comment=Turn PlayStation 1 disc images into PS3 PKG, PSP/Vita EBOOT or PS2 POPStarter files\n"
        f"Exec={exec_cmd} %F\n"
        f"Icon={APP_ID}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        f"MimeType={MIME}\n"
        "Keywords=playstation;ps1;psx;ps3;psp;vita;ps2;popstarter;pkg;eboot;pops;psone;classic;\n"
        "StartupWMClass=psone2ps3pkg\n"
    )


def _paths() -> tuple[Path, Path, Path]:
    bin_dir = Path(os.environ.get("XDG_BIN_HOME") or Path.home() / ".local" / "bin")
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return (
        bin_dir / APP_ID,
        data / "applications" / f"{APP_ID}.desktop",
        data / "icons" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png",
    )


def _refresh(data_apps: Path) -> None:
    for cmd in (["update-desktop-database", str(data_apps)],
                ["gtk-update-icon-cache", "-q", "-t", str(data_apps.parent / "icons" / "hicolor")]):
        if shutil.which(cmd[0]):
            subprocess.run(cmd, capture_output=True)


def install() -> int:
    """Copy this AppImage (or binary) to ~/.local/bin and add a menu entry."""
    src = os.environ.get("APPIMAGE") or (sys.executable if getattr(sys, "frozen", False) else None)
    bin_dest, desktop_dest, icon_dest = _paths()
    if src:
        src_path = Path(src).resolve()
        bin_dest.parent.mkdir(parents=True, exist_ok=True)
        if src_path != bin_dest.resolve():
            tmp = bin_dest.with_suffix(".new")
            shutil.copy2(src_path, tmp)
            tmp.chmod(0o755)
            tmp.replace(bin_dest)
        exec_cmd = str(bin_dest)
        print(f"installed: {bin_dest}")
    else:
        # Source checkout: point the menu at whatever is on PATH.
        found = shutil.which(APP_ID)
        if not found:
            print("error: run --install from the AppImage, or pip-install the package first", file=sys.stderr)
            return 1
        exec_cmd = found
    desktop_dest.parent.mkdir(parents=True, exist_ok=True)
    desktop_dest.write_text(desktop_entry(exec_cmd))
    print(f"installed: {desktop_dest}")
    if ICON.is_file():
        icon_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ICON, icon_dest)
        print(f"installed: {icon_dest}")
    _refresh(desktop_dest.parent)
    if str(bin_dest.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print(f"note: {bin_dest.parent} is not on PATH; the menu entry works anyway.")
    return 0


def uninstall() -> int:
    for p in _paths():
        if p.exists():
            p.unlink()
            print(f"removed: {p}")
    _refresh(_paths()[1].parent)
    return 0
