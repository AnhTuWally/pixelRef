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

Before distributing builds, sign the executables. Windows SmartScreen and
antivirus tools treat unsigned PyInstaller apps with suspicion.
