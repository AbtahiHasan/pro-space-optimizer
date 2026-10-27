# Pro Space Optimizer

A modern Ubuntu desktop utility (Python + PySide6) that finds and safely
removes unnecessary development folders such as `node_modules`, `vendor`,
`.venv`, `__pycache__`, `build`, and `dist`.

You choose one or more **parent folders** and one or more **folder names**.
The app **recursively scans every subdirectory** of each parent folder and
lets you review, size, and delete the matches. When a directory matches, it is
added to the results and **not searched inside** (pruning), so a match nested
inside an already-matched folder is not reported twice.

---

## Features

- Select multiple parent folders with the native GTK/Qt folder picker.
- Parent folders are saved automatically and restored on next launch.
- Enter any folder names to search for (comma- or newline-separated). **No
  predefined checkboxes** — matching is case-sensitive on Linux.
- **Recursive scanning with pruning.** Every subdirectory is searched; a
  matched folder is not descended into, so nested duplicates are skipped (see
  [Scanning behaviour](#scanning-behaviour)).
- **Project-aware matching (optional).** Require a recognised project type
  before a folder is matched, with an ecosystem allow-list and a
  confidence threshold. A **detected ecosystem** column shows the project type
  of each result. See [Ecosystem detection](#ecosystem-detection).
- Symlinked directories are **not** followed by default.
- Folder sizes are calculated in the background and shown as `KB`/`MB`/`GB`/`TB`.
- Results table with checkbox selection, sorting, per-row **Delete** button,
  copy-path and open-parent-directory actions.
- Bulk actions: Select All, Deselect All, Delete Selected, Delete All Results.
- Two deletion modes: **Move to Trash (default)** and **Permanently delete**.
- Cooperative cancellation, responsive UI, thread cleanup on exit.
- Persistent settings (parents, names, deletion mode, theme, window geometry).
- Light and Dark themes.
- Optional administrator retry for permission-denied deletions (via `pkexec`).

---

## Project structure

```
pro-space-optimizer/
├── README.md
├── requirements.txt
├── pyproject.toml
├── Makefile                         # make deb / run / test / install / clean
├── main.py                          # source-checkout entry point
├── src/pro_space_optimizer/
│   ├── __init__.py                  # app metadata
│   ├── app.py                       # QApplication, logging, theming, main()
│   ├── main_window.py               # header, sidebar, pages, results table
│   ├── models.py                    # ScanResult + QAbstractTableModel
│   ├── settings.py                  # QSettings wrapper
│   ├── scanner.py                   # recursive scan with pruning + policy
│   ├── ecosystem_registry.py        # project/ecosystem detection registry
│   ├── size_calculator.py           # background-size walk
│   ├── deletion.py                  # validation + deletion orchestration
│   ├── trash_manager.py             # XDG Trash via `gio trash`
│   ├── permissions.py               # permission detection + pkexec retry
│   ├── privileged_helper.py         # standalone root helper (validated)
│   ├── workers.py                   # QThread workers + cancellation
│   ├── dialogs.py                   # confirmations / errors / About
│   ├── utils.py                     # size formatting, name & path safety
│   └── resources/icons/app.svg      # bundled application icon
├── resources/icons/app.svg          # source icon (used by install.sh)
├── packaging/
│   ├── pro-space-optimizer.desktop.in
│   ├── pro-space-optimizer.policy.in # optional PolicyKit action
│   ├── install.sh                    # per-user source installer
│   ├── uninstall.sh                  # per-user uninstaller
│   └── deb/                          # self-contained .deb builder
│       ├── build-deb.sh              # `make deb` runs this
│       ├── control.template
│       ├── postinst / prerm
│       ├── copyright
│       ├── pro-space-optimizer.desktop
│       └── pro-space-optimizer.launcher
└── tests/
    ├── conftest.py
    ├── test_scanner.py
    ├── test_ecosystem_registry.py
    ├── test_project_aware_scan.py
    ├── test_size_calculator.py
    ├── test_settings.py
    ├── test_deletion.py
    ├── test_trash_manager.py
    ├── test_workers.py
    └── test_models_and_utils.py
```

---

## Requirements

- Ubuntu 22.04 LTS or newer (developed and tested on Ubuntu 26.04).
- Python **3.9+**.
- PySide6 **6.5+**.
- For the Trash: `gio` (from GLib), already present on Ubuntu desktop.
- Optional, for administrator retry: `pkexec` (from PolicyKit).

### Installing OS packages

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip
# Trash support (usually already installed):
sudo apt install libglib2.0-bin
# Optional administrator retry support:
sudo apt install policykit-1
```

> **Note:** the tested environment used an Anaconda Python 3.14. The
> installer creates its own virtual environment from whichever `python3` is
> first on your `PATH`. To force a specific interpreter, set `PYTHON`:
> `PYTHON=/usr/bin/python3 ./packaging/install.sh`.

---

## Run from a terminal (source checkout)

```bash
# 1. (optional) create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. install dependencies
pip install -r requirements.txt

# 3. run
python3 main.py
```

Add `--verbose` (or `-v`) for debug logging. Logs are also written to
`~/.local/state/pro-space-optimizer/app.log`.

---

## Install for the current user

The installer creates a virtual environment, installs the app, a launcher in
`~/.local/bin`, an icon and a desktop entry. It does **not** run as root.

```bash
./packaging/install.sh
```

Afterwards launch it from the **Ubuntu application menu** ("Pro Space
Optimizer") or from a terminal:

```bash
~/.local/bin/pro-space-optimizer
```

If `~/.local/bin` is not on your `PATH`, add it:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

### Uninstall

```bash
./packaging/uninstall.sh          # keep configuration
./packaging/uninstall.sh --purge  # also remove configuration
```

---

## Build a self-contained `.deb` (no setup on the target)

This produces a `.deb` that **bundles its own Python interpreter and
PySide6/Qt** under `/opt/pro-space-optimizer`, so the target machine needs no
Python, no pip and no virtual environment — the only runtime libraries are
standard Qt/X11/GL libraries that apt resolves automatically.

### Build (one command)

```bash
make deb
```

This runs the test suite as a gate, then produces
`pro-space-optimizer_1.0.0-1_amd64.deb` (~69 MB). `make deb` needs, on the
**build** machine only:

- `python3`, `python3-pip`, `python3-venv`
- `dpkg-deb` (from `dpkg`)
- network access for the first build (downloads the pinned PySide6 wheels)

Skip the test gate with `SKIP_TESTS=1 make deb`. Pin a different PySide6 with
`PYSIDE_VERSION=6.11.0 make deb`.

### Install

```bash
sudo apt install ./pro-space-optimizer_1.0.0-1_amd64.deb
```

`apt` installs any missing runtime libraries automatically. The application
then appears in the **Ubuntu application menu** and can be launched with:

```bash
pro-space-optimizer
```

### Inspect / remove

```bash
make inspect                  # dpkg-deb -I and -c
sudo apt remove pro-space-optimizer
```

### What the package contains

| Path | Purpose |
|------|---------|
| `/opt/pro-space-optimizer/runtime/` | private Python + PySide6 bundle |
| `/opt/pro-space-optimizer/app/pro_space_optimizer/` | application code |
| `/usr/bin/pro-space-optimizer` | launcher |
| `/usr/share/applications/pro-space-optimizer.desktop` | menu entry |
| `/usr/share/icons/hicolor/scalable/apps/pro-space-optimizer.svg` | icon |
| `/usr/share/polkit-1/actions/local.pro-space-optimizer.delete.policy` | privilege prompt |
| `/usr/share/doc/pro-space-optimizer/` | copyright + README |

**Portability:** the bundle is built on the host's Python and glibc, so build
on the **oldest** Ubuntu you intend to support. The `.deb` built on Ubuntu
26.04 targets 26.04+. The dependency list auto-detects version-specific
libraries (`libicu`, `libdouble-conversion`, `libglib`, `libpng`, `libmtdev`)
at build time.

**Size:** bundling Python + Qt makes the `.deb` ~69 MB. If you prefer a tiny
package that depends on `python3-pyside6` from apt instead, that is a different
packaging strategy (not implemented here).

---

## Usage

1. **Parent Folders** — click **Add Folder…** and pick a folder. Its whole
   subtree is scanned recursively. Add as many as you like; duplicates are
   refused automatically.
2. **Folder Names to Find** — type names, e.g.
   `node_modules, vendor, .venv, __pycache__, build, dist`.
3. **Start Scan** — progress, the current location, the number of parent
   folders processed, the match count, and the total size are shown live.
   Click **Stop Scan** to cancel; already-found results are kept.
4. **Results** — tick rows, then use **Delete Selected**. Each row also has an
   individual **Delete** button. Right-click a row to copy its full path or
   open its parent directory in the file manager.
5. **Clear Results** empties the table only — **it never deletes anything from
   disk**.

---

## Scanning behaviour

This is the most important rule in the application.

For every selected parent folder, the scanner **recursively descends into all
subdirectories** (an explicit iterative walk using `os.scandir()`). When a
directory's name matches one of the searched names, it is added to the results
and the walk **does not descend into it**. This prunes the matched subtree at
the moment of the match, so a matching folder nested inside another matching
folder is never reported as a separate result. Traversal continues normally for
every directory outside the matched one.

Given:

```
/home/user/projects/
├── app1/
│   └── node_modules/
│       └── node_modules/      <- ignored (inside a match)
├── app2/
│   └── src/
│       └── node_modules/      <- found
└── node_modules/
    └── node_modules/          <- ignored (inside a match)
```

Scanning `/home/user/projects` for `node_modules` finds exactly:

```
/home/user/projects/app1/node_modules
/home/user/projects/app2/src/node_modules
/home/user/projects/node_modules
```

Additional guarantees:

- Traversal is iterative (an explicit stack), so very deep trees cannot
  exhaust the Python call stack.
- Only real directories are returned; ordinary files are never matches.
- Symlinked directories are **never followed** and are counted in the
  "symlinks skipped" summary. This prevents a match from redirecting a later
  delete outside the selected parent, and prevents symlink loops.
- Missing or inaccessible directories are reported (in a dialog and the
  status bar) — they are never silently skipped. A permission error in one
  branch does not stop the rest of the walk.
- Matching is **case-sensitive**, as on Linux.
- Cancellation (Stop Scan) is cooperative and checked throughout the walk;
  results already discovered are kept.

---

## Ecosystem detection

`src/pro_space_optimizer/ecosystem_registry.py` recognises 160+ project
ecosystems and frameworks from **marker files** (for example `package.json`,
`Cargo.toml`, `go.mod`, `composer.json`, `pom.xml`, `pubspec.yaml`,
`CMakeLists.txt`, `build.gradle`, `mix.exs`, `*.csproj`). Detection is
**declarative** — a registry of definitions is evaluated by a small engine, so
new ecosystems can be added without changing the scanner.

### Matching policies

The scanner supports two policies, selectable in the **Scan** panel:

- **Name only (default).** Any directory whose name matches is listed. Project
  type is still detected and shown, but never affects matching. This is the
  safe fallback when a project type is unknown.
- **Project-aware.** A name-matching folder is only accepted when a recognised
  project/ecosystem justifies it — the folder name must be a known dependency
  folder for the detected ecosystem (e.g. `node_modules` for a Node project,
  `target` for Rust, `vendor` for Composer/Go/Ruby), or a generic build folder
  (`build`, `dist`, …) inside a recognised project. Candidates that are
  rejected by the policy are **not pruned**: the walk continues inside them, so
  deeper valid matches are still found.

### Confidence

Every ecosystem has a confidence level. Only **high** markers (dedicated
manifests) and **medium** markers (strong but shared, or backed by additional
evidence) are treated as projects by default; a lone generic marker such as
`Makefile`, `README.md`, `.git/`, or `Dockerfile` never classifies a folder as
a language project. The required confidence is configurable in
**Settings → Ecosystem Detection**. When several ecosystems tie at the top
tier, the result is marked **ambiguous** rather than guessing.

### Safety

Detection never executes project code, build scripts, package managers, or
shell commands, and never installs dependencies. It only stats files and reads
small configuration files (bounded to 64 KB) with lenient parsers that never
raise on malformed input. Symlinked entries are never followed. Project-aware
filtering is **opt-in** and is never applied globally without the user
selecting it.

> Honest caveat: marker files are evidence, not proof. Some ecosystems (for
> example frameworks identified only by a dependency entry, WordPress, or
> CodeIgniter) can be missed or misclassified. Name-only mode always works as a
> fallback, and the ecosystem column always distinguishes *unknown* and
> *ambiguous* results.

---

## Folder sizes

Sizes are calculated in a background thread so the UI never freezes. While a
size is being computed the cell shows `Calculating…`.

**Calculated sizes can differ from on-disk usage.** Reasons include
filesystem block allocation (a 1-byte file still occupies a block), hard links
(counted once here via device+inode de-duplication), sparse files, and
permission restrictions that make some subdirectories unreadable (shown as
`Partial`). A matching directory is still a valid result even when its size
cannot be fully determined. Size calculation never looks for additional
matching directory names and never follows symlinks.

---

## Deletion safety

### Modes

- **Move to Trash** (default): delegates to `gio trash`, the freedesktop.org
  Trash implementation. It handles same- and cross-filesystem moves and writes
  proper `.trashinfo` metadata so Files/Nautilus can restore the entry. We do
  **not** implement Trash as "rename into a random folder". If a filesystem
  has no Trash (for example `tmpfs` mounted at `/tmp`), the app reports it and
  leaves the folder untouched.
- **Permanent deletion**: requires explicit confirmation showing the full path.

### Safeguards applied before every deletion

1. The target must exist and still be a directory.
2. The target must **not** be a symlink.
3. The target must be a **descendant of a saved parent folder** (scanning is
   recursive, so a match may be several levels deep; the target must still be
   strictly *inside* a user-approved parent, never equal to one and never
   outside them).
4. The target's name must still be one of the searched names.
5. The target's device/inode must match what was captured at scan time
   (defence against time-of-check/time-of-use races and symlink substitution).
6. Recursive permanent deletion refuses trees that contain symlinks.
7. No shell is used. Commands are built as argument lists (`shell=False`).
   User input is never interpreted as shell syntax, a regex, or a path.

Deleting a parent folder is never possible through the normal flow: parents are
not results, and results must be strictly inside a parent.

### Clearing vs deleting

**Clear Results** only clears the table (`ResultTableModel.clear()`); it never
touches the filesystem. This is covered by an automated test.

---

## Administrator privileges

The GUI runs entirely as your normal user and is never launched with `sudo`.

When a deletion fails specifically with a permission error, the app:

1. explains which operation failed and shows the path,
2. asks whether to retry **that single operation** with administrator
   privileges,
3. if you decline, leaves the folder untouched,
4. if you agree, invokes `pkexec` to run the small, dedicated
   `privileged_helper.py`, which independently validates the arguments,
   refuses symlinks, verifies device/inode identity, and performs exactly one
   deletion, returning a structured JSON result.

Passwords are never requested by the application and are never stored;
authentication is handled entirely by the operating system's PolicyKit agent.

### PolicyKit configuration (optional)

`pkexec` shows a generic prompt unless a policy action is installed. To
provide a dedicated prompt, install the provided policy file as root:

```bash
sudo cp packaging/pro-space-optimizer.policy.in \
        /usr/share/polkit-1/actions/local.pro-space-optimizer.delete.policy
```

> **Caveat:** `pkexec` behavior depends on your PolicyKit configuration and
> desktop environment. It is not guaranteed to work on every Ubuntu
> installation. If `pkexec` is unavailable or authentication is declined, the
> app falls back to leaving the folder untouched and (for Trash failures)
> explaining the problem. Non-privileged functionality is fully implemented.

---

## Settings

Stored per-user with `QSettings` at:

```
~/.config/ProSpaceOptimizer/ProSpaceOptimizer.conf
```

Persisted: saved parent folders, folder-name input, deletion mode, theme,
"calculate sizes" preference, and window geometry/state. No passwords or
tokens are ever stored. Missing or malformed values fall back to safe
defaults.

---

## Running the tests

The test suite uses `tmp_path` for all filesystem work, mocks privilege
escalation, and never deletes real user data.

```bash
python3 -m pytest -q
```

Covered behaviour includes: recursive discovery at multiple depths, pruning of
matches nested inside a matched folder, multiple names, case sensitivity,
regular files ignored, missing parents, symlinked directories ignored,
deep-tree traversal without stack overflow, cancellation, duplicate parent
prevention, settings persistence, directory sizes, disappearing files during
size calculation, single deletion, nested-match deletion, bulk deletion with
mixed success/failure, identity-mismatch refusal, and that clearing the results
table never deletes files.

---

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+O` | Add folder |
| `F5` | Start scan |
| `Esc` | Stop scan |
| `Ctrl+Shift+C` | Clear results |

---

## Known limitations

- `pkexec` escalation depends on PolicyKit configuration; see
  [Administrator privileges](#administrator-privileges).
- The Trash is unavailable on filesystems that lack a `.Trash` directory
  (e.g. `tmpfs`); in that case use permanent deletion with explicit
  confirmation.
- Sizes reflect apparent file sizes, not allocated blocks (see
  [Folder sizes](#folder-sizes)).
- The optional PolicyKit policy file must be installed as root to get a
  dedicated authentication prompt.

---

## License

MIT.
