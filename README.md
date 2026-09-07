# psone2ps3pkg

A drag-and-drop terminal tool that turns a **PlayStation 1** disc image into a
**PS3-installable PS One Classic `.pkg`**, written straight into the folder you
dropped.

It is a friendly front end around [pop-fe](https://github.com/sahlberg/pop-fe),
which is bundled in `vendor/pop-fe` (no network fetch of the engine). PS1 only —
PS2 discs and other systems are rejected.

## Use

### Standalone binary (no Python needed)

Grab `psone2ps3pkg-linux-x86_64` from the [latest release](../../releases/latest),
`chmod +x` it, and run it. It bundles Python, pop-fe and a prebuilt `pkgcrypt`,
so nothing else is required (Linux x86-64, glibc ≥ 2.35).

```sh
./psone2ps3pkg-linux-x86_64                          # TUI: drop a folder, press Enter
./psone2ps3pkg-linux-x86_64 "/path/to/game folder"   # build without the TUI
./psone2ps3pkg-linux-x86_64 --check                  # verify optional tools
```

### From source

```sh
./run.sh                          # launch the TUI, drop a folder onto it, press Enter
./run.sh "/path/to/game folder"   # build without the TUI
./run.sh --check                  # verify the build tools are present
```

The dropped folder must contain exactly one game as one of:

| Input | Notes |
|-------|-------|
| `.cue` + `.bin`/`.img` | preferred; multi-track (CD audio) supported |
| bare `.bin` / `.img`   | assumed single `MODE2/2352` track; CD-audio tracks are lost |
| `.iso`                 | only if it is a raw 2352-byte-sector image; a 2048-byte data-only ISO is refused |
| `.chd`                 | needs `chdman` on `PATH` |

Output is named `<Title> [<SERIAL>].pkg`, e.g. `GRAND THEFT AUTO 2 [SLUS-00789].pkg`.

## Requirements

The **standalone binary** needs only `chdman` (for `.chd` input) and, ideally,
an internet connection so pop-fe can fetch cover art / screenshots — offline
still produces a working PKG, just without the artwork.

Running **from source** additionally needs:

- Python 3.10+
- A C compiler + Python headers, used **once** to build pop-fe's `pkgcrypt`
  extension (the bundled `setup.py` relies on `distutils`, removed in Python 3.12+,
  so it is compiled directly):
  - Fedora: `sudo dnf install gcc python3-devel`
  - Debian/Ubuntu: `sudo apt install build-essential python3-dev`

`chdman` comes from `mame-tools` on both Fedora and Debian/Ubuntu.

First run copies `vendor/pop-fe` into `~/.local/share/psone2ps3pkg/engine/` (a
writable working copy) and builds `pkgcrypt` there. Delete that directory to force
a clean rebuild.

## Installing the PKG on PS3

Requires CFW (Evilnat/Ferrox/…) or HEN with Cobra.

1. Copy the `.pkg` to a FAT32 USB stick or FTP it to `/dev_hdd0/packages/`.
2. XMB → **Package Manager → Install Package Files** (or use multiMAN / IRISMAN).
3. The game shows up under PS1 titles. No RAP needed — the `ISO.BIN.EDAT` is
   signed with the free klicensee `ps1_netemu` accepts.

Content ID is `UP9000-<SERIAL>_00-0000000000000001`.

## Notes / limitations

- **LibCrypt (PAL games).** The tool passes `--no-libcrypt`, so the *executable*
  patch method is skipped, but pop-fe still bakes the subchannel protection data
  into the disc image, which is what `ps1_netemu` needs. If a specific PAL title
  stalls at a known LibCrypt checkpoint, that is the thing to revisit.
- **One game per folder.** Multiple `.cue`/`.chd`/`.bin` files in the same folder
  is an error rather than a guess.
- **Multi-disc games** are not handled in one shot — package each disc's folder
  separately.

## Layout

```
src/psone2ps3pkg/
  disc.py      scan a folder, identify the PS1 disc (ISO9660 + SYSTEM.CNF)
  engine.py    stage the bundled pop-fe, build pkgcrypt once
  convert.py   plan + run pop-fe --ps3-pkg, validate the PKG header
  tui.py       Textual UI
  cli.py       argument handling, headless build, --check
vendor/pop-fe/ bundled engine (see vendor/VENDOR.md for upstream revisions)
```

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Building the release binary

```sh
./scripts/build_release.sh      # -> dist/psone2ps3pkg
```

It creates an isolated venv, precompiles `pkgcrypt` against that interpreter,
stages it into a private copy of `vendor/pop-fe`, and runs PyInstaller in
`--onefile` mode. pop-fe shells out to a bare `python3` for two of its helper
scripts; in a frozen build there is none, so `engine.py` points those calls at a
shim that re-enters the binary via a hidden `--internal-exec` flag and runs the
script in-process with `runpy`.

`.github/workflows/release.yml` runs the same script on every `v*` tag and
attaches `psone2ps3pkg-linux-x86_64` (+ `.sha256`) to the GitHub release.
