"""Native file / folder dialogs through zenity, kdialog, qarma or yad.

The dialog runs as a separate process, so the pygame window stays live. When
no helper is installed, the GUI falls back to its own in-window browser.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from psone2ps3pkg.engine import host_env

PATTERNS = ["*.cue", "*.bin", "*.img", "*.iso", "*.chd"]


def available() -> str | None:
    for tool in ("zenity", "kdialog", "qarma", "yad"):
        if shutil.which(tool):
            return tool
    return None


def _zenity_like(tool: str, kind: str, start: Path) -> list[str]:
    cmd = [tool, "--file-selection", f"--filename={start}/"]
    if kind == "file":
        pats = " ".join(PATTERNS + [p.upper() for p in PATTERNS])
        cmd += ["--multiple", "--separator=\n", "--title=Choose PS1 disc images",
                f"--file-filter=PS1 disc images | {pats}", "--file-filter=All files | *"]
    else:
        title = "Choose the output folder" if kind == "output" else "Choose a folder with PS1 games"
        cmd += ["--directory", f"--title={title}"]
    return cmd


def _kdialog(kind: str, start: Path) -> list[str]:
    if kind == "file":
        pats = " ".join(PATTERNS + [p.upper() for p in PATTERNS])
        return ["kdialog", "--multiple", "--separate-output", "--title", "Choose PS1 disc images",
                "--getopenfilename", str(start), f"{pats}|PS1 disc images"]
    title = "Choose the output folder" if kind == "output" else "Choose a folder with PS1 games"
    return ["kdialog", "--title", title, "--getexistingdirectory", str(start)]


def pick(tool: str, kind: str, start: Path) -> list[Path] | None:
    """Block until the user picks. None means cancel; raises OSError if broken."""
    cmd = _kdialog(kind, start) if tool == "kdialog" else _zenity_like(tool, kind, start)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=host_env())
    if proc.returncode != 0:
        if proc.returncode in (1, 255) and not proc.stdout.strip():
            return None
        raise OSError(proc.stderr.strip() or f"{tool} exited with {proc.returncode}")
    out = [Path(line) for line in proc.stdout.splitlines() if line.strip()]
    return out or None


def open_folder(path: Path | str) -> bool:
    """Open a folder or a URL with the desktop's default handler."""
    opener = shutil.which("xdg-open") or shutil.which("gio")
    if not opener:
        return False
    cmd = [opener, str(path)] if opener.endswith("xdg-open") else [opener, "open", str(path)]
    try:
        subprocess.Popen(cmd, env=host_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return False
    return True
