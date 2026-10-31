# Python packages and the standalone build

pixelRef runs on PySide6 alone. Plugins that need more (numpy for heavy image
effects, say) declare it in `plugin.toml` (see [plugins.md](plugins.md#python-packages)).
Their users install it from the Plugins window, never from a terminal. This
page covers how that works, and how the standalone executable is built.

## The managed package folder

Packages installed from *Window → Plugins… → Python packages* go into a folder
pixelRef owns:

```text
<root>/py313-win-amd64/            one folder per Python version and platform
    site-packages/                 on sys.path, after the app's own packages
    site-packages.new/             a rebuilt folder, swapped in at the next start
    wheels/                        .whl files installed from disk, kept for rebuilds
    packages.json                  what the user asked for, and for which plugins
```

`<root>` is `%LOCALAPPDATA%\pixelRef\python` on Windows (local, not roaming:
compiled packages belong to one machine), `~/Library/Application
Support/pixelRef/python` on macOS and `$XDG_DATA_HOME/pixelRef/python` (or
`~/.local/share/…`) elsewhere. `$PIXELREF_PACKAGES_DIR` overrides it; the
tests use that.

There is one folder per Python version because a compiled package (numpy)
only works with the Python it was built for. When an update of pixelRef
brings a newer Python, the new version adopts the old folder's
`packages.json` and reinstalls everything in the background on first start.

### How changes are made

`packages.json` is the source of truth. pip's `--target` mode can't uninstall
or upgrade cleanly, and Windows locks compiled files that are in use, so:

- **Adding a package** installs it into a scratch folder, then moves it into
  `site-packages`. Packages already there keep their files. No restart is
  needed: the plugins waiting for it activate at once.
- **Removing or updating a package** rebuilds the whole folder from
  `packages.json` into `site-packages.new`. If none of the packages is loaded
  in the running app, the new folder is swapped in at once. Otherwise the swap
  happens at the next start, before any plugin is imported, and the Plugins
  window says *Restart pixelRef to finish*.
- Only prebuilt wheels are installed (`--only-binary=:all:`). Nothing is
  compiled on the user's machine, and no package build scripts run.
- `PySide6`, `shiboken6`, `pip` and `pixelref` can't be installed into the
  folder: a managed copy would shadow the app's own.
- Failures are explained in plain words ("No internet connection", "This
  package has no version for this computer…"). pip's full log is under
  **Details**.

The same operations are commands, for scripts and batch mode:
`pxcmd.packages.list()`, `.needed()`, `.info()`, `.install(…)`,
`.install_missing(plugin_id=None)`, `.install_file(path)`, `.update(names=None)`
and `.remove(names)`.

### Offline installs

- A plugin can ship wheels in a `wheels/` folder next to its `plugin.toml`.
  pip looks there first (`--find-links`), so its packages install without
  internet.
- **Install from file…** installs a downloaded `.whl`. The file is copied into
  `wheels/`, so later rebuilds still find it.
- pip honours the usual `HTTPS_PROXY` / `PIP_INDEX_URL` settings.

## Fully portable mode

By default pixelRef keeps settings, plugins, recovery files, caches and managed packages in the usual
per-user folders. To keep **everything beside the program** (a USB stick, a shared folder), put a
`portable.toml` next to `pixelRef.exe`:

```toml
[portable]
enabled = true
data_directory = "user-data"     # optional: a plain folder name inside the program's folder
```

Then, under `user-data/`:

```text
settings/pixelRef.ini     the settings (instead of the registry / plist / ~/.config)
config/                   keymap.toml
data/                     your plugins (plugins/), recovery files, plugin activity log
cache/                    effect caches
python/                   managed Python packages (one folder per Python version and platform)
boards/                   where new scenes are saved by default (a folder you choose still wins)
```

- Everything is found relative to the program's folder, never the current directory. Move the whole folder
  and it keeps working. `portable.toml` is optional and yours: unzip a new release over the old one, or let
  an update replace the program, and `portable.toml` and `user-data/` stay as they are.
- **Opt-in and not migrated.** Turning it on starts with empty settings and plugins; nothing is copied from
  (or deleted in) the per-user folders. Copy what you want by hand.
- A `portable.toml` that can't be used (bad TOML, an absolute path, `..`, a name the program uses) is
  ignored with a logged warning and the per-user folders are used, rather than guessing a location.
- Running from source (`python -m pixelref`) is never portable.
- `$PIXELREF_PACKAGES_DIR` still overrides the package folder.
- The folder must be writable. If the settings folder can't be created pixelRef still starts and keeps
  settings in the usual place for that run.
- Compiled packages are machine-specific: the per-version/platform folders keep a stick moved between
  Windows and Linux from loading the wrong binaries, but reinstall the packages on each.

## The standalone executable

```text
pip install -r packaging/requirements-build.txt
python packaging/build_exe.py           # build dist/pixelRef/, then smoke-test it
python packaging/build_exe.py --test-only
```

The result is one folder:

```text
dist/pixelRef/
    pixelRef.exe         the app (windowed)
    pixelref-cli.exe     the same app with a console: --batch scripts, --run-pip
    _internal/           Python, Qt and pixelRef, shared by both
        updater/         the update helper: pixelref-updater.exe with its own _internal/ (see below)
```

Choices made in `packaging/pixelref.spec`:

- **One folder, not one file.** A one-file exe unpacks itself to a temp
  folder on every launch: slow starts, and antivirus false positives.
- **pip is bundled**, with its certificates. There is no `python -m pip` in a
  frozen app, so the package manager runs `pixelref-cli.exe --run-pip …`. That
  is pixelRef's own Python, so pip picks wheels that match it. The console
  twin is needed because the windowed exe has no stdout for pip's log. pip is
  bundled as plain files, not inside the archive, because its vendored
  `distlib` can't find its resources there.
- **The whole standard library is bundled.** PyInstaller normally drops
  modules the app doesn't import, but packages installed later do import them
  (numpy needs `ctypes`, `unittest`…).
- **Unused Qt is left out**: Qt Quick/QML, Qt PDF, the virtual keyboard, the
  software OpenGL fallback and Qt's translations. This takes the folder from
  about 143 MB to about 97 MB.

`build_exe.py` then runs the *built* executables, because what breaks when
frozen only breaks when frozen. It prints four steps:

1. the bundled pip runs;
2. batch mode starts with every built-in plugin active, installs a package
   (offline, from a wheel the test builds) into the managed folder and
   imports it, and imports standard-library modules pixelRef never uses;
3. the UI kernel starts offscreen, imports an image and renders it, with all
   the image formats available;
4. the size of the built folder (a report, not a check).

The update helper (`packaging/updater/helper.spec`, entry point `helper_main.py`) is built first into
`dist/pixelref-updater/` and bundled as plain files in `_internal/updater/`. It is deliberately separate from the
app: its own Python runtime and `cryptography`, no Qt, pip or plugins (about 30 MB on Linux), because the app copies
it to `.pixelref-update/helper/<id>/` and runs it from there while the app's own files are replaced. A
test (`tests/test_update_packaging.py`) keeps its imports Qt-free. `build_exe.py` fails the build if the helper is
missing or doesn't run; smoke step 5 runs it in place (it must refuse to install while installing is disabled)
and from an install-shaped folder (it must find its install and report nothing to recover). Installing from inside
pixelRef stays unavailable until the flag is switched on; see
[update-release-process.md](update-release-process.md#status-and-go-live-checklist).

Before distributing builds, sign the executables. Windows SmartScreen and
antivirus tools treat unsigned PyInstaller apps with suspicion.
