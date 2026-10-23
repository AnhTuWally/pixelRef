# pixelRef — Security Audit

**Date:** 2026-10-21
**Scope:** `C:\PZ_Dev\pixelRef` (source tree `pixelref/`, `packaging/`, `tools/`, `examples/`; `venv/`, `dist/`, `build/` excluded as third-party artifacts)
**Targets:** Malicious patterns (backdoors, hardcoded secrets, exfiltration) and critical flaws (injection, XSS, path traversal, auth bypass)
**Method:** Two full passes. Pass 1: per-file sink analysis (dynamic execution, subprocess, deserialization, network, file I/O, secrets). Pass 2: system-level analysis of how components interact (trust boundaries, data flow between scene files → UI → OS, plugin/script/package chains, environment-variable attack surface).

---

## Verdict

**STATUS: SECURE**

No backdoors, no hardcoded secrets, no exfiltration channels, and no exploitable injection / XSS / path-traversal / auth-bypass flaws were found. All dynamic-execution and process-execution sinks are either intentional user-code features (local, user-owned content) or strictly validated.

---

## System-level (interaction) analysis

### 1. Trust boundary: local user content → code execution
The app's core design is a local reference board: everything it executes is content the user owns. The execution chains are:

| Chain | Entry | Sink | Assessment |
|---|---|---|---|
| Script Editor | User-typed code | `eval`/`exec` in [console/__init__.py](pixelref/builtins/console/__init__.py:59) | Intentional (Maya-style console); controlled namespace; `SystemExit` contained |
| Keymap | `keymap.toml` `python = "..."` | Console engine | Intentional user config; local file |
| Macros | `scripts/` folder, `$PIXELREF_SCRIPT_PATH` | Console engine | Intentional; local user folders |
| Plugins | Plugin folders, `$PIXELREF_PLUGIN_PATH` | `importlib` in [core/plugins.py](pixelref/core/plugins.py) | Intentional plugin architecture; local folders |
| Batch CLI | `pxcmd` script argument | `runpy.run_path` in [batch.py](pixelref/batch.py) | Intentional CLI feature |
| Startup | `userSetup.py` | Console engine | Intentional (documented in module docstring) |

**Interaction risk checked:** untrusted *data* (scene files, pasted content) cannot reach these sinks directly — it can only trigger *commands* (e.g. opening a link), which is the expected behavior of a link-opening feature.

### 2. Untrusted data → OS interaction (link opening)
- [text/ui.py](pixelref/builtins/text/ui.py:590) `resolve_link` / `open_link`: scene-file link text → `QDesktopServices.openUrl`. Schemes are passed through to the OS handler (http/https/mailto/file). This is standard link behavior; the content is user-authored scene data.
- [image/ui.py](pixelref/builtins/image/ui.py:408) `remote_urls`: only `http`/`https` schemes accepted for downloads; `QNetworkAccessManager` with `NoLessSafeRedirectPolicy` (no https→http downgrade).
- All other `openUrl` calls use `QUrl.fromLocalFile` (file scheme only).

### 3. Process execution (pip)
- [core/packages.py](pixelref/core/packages.py:455) `subprocess.Popen` and [ui/package_jobs.py](pixelref/ui/package_jobs.py) `QProcess`: argument lists (no shell), requirement strings validated by `_NAME_RE`/`_SPEC_RE` (alphanumeric + `._-`), `PROTECTED` set blocks shadowing PySide6/pip, env sanitized (`PIP_TARGET`/`PIP_USER` popped, `PYTHONNOUSERSITE` set).
- [pip_runner.py](pixelref/pip_runner.py) `runpy.run_module("pip")`: fixed module name.
- [packaging/build_exe.py](packaging/build_exe.py:41): build-time only, fixed argument lists.

### 4. Deserialization / parsing
- Scene files: strict `json.loads`, base64 with `validate=True` ([core/scene_file.py](pixelref/core/scene_file.py:235), [builtins/file/start.py](pixelref/builtins/file/start.py:71)), schema version gate, staged parse. (Other base64 decodes, e.g. clipboard and effects cache, omit `validate` — malformed input simply raises, no security impact.)
- PureRef `.pur` parser: bounds-checked reads, `MAX_DEPTH`/`MAX_POINTS` caps ([builtins/pureref/purformat.py](pixelref/builtins/pureref/purformat.py:88)).
- Keymap: `tomllib` (safe parser).
- **No** `pickle`, `marshal`, `shelve`, or `yaml.load` anywhere in the source tree.

### 5. XSS / HTML
- `setHtml` only with static generated help content ([builtins/help/__init__.py](pixelref/builtins/help/__init__.py:88)).
- Pasted HTML is parsed with a strict `<img src>` regex for URL extraction, not rendered.

### 6. Path traversal
- All file I/O is user-selected local paths; `resolve_path` resolves relative to the scene folder; atomic write-then-`os.replace` pattern throughout.

### 7. Secrets & exfiltration
- Only a random `uuid4` D-Bus portal token ([builtins/color_picker/screen.py](pixelref/builtins/color_picker/screen.py:103)).
- No hardcoded credentials, keys, or tokens.
- No sockets, no telemetry, no background network; the only network I/O is user-initiated http(s) image downloads.

### 8. Crypto
- `hashlib.sha1` in [builtins/effects/engine.py](pixelref/builtins/effects/engine.py) for cache keys only — non-security use, acceptable.

---

## Findings

None. (No Critical / High / Medium vulnerabilities.)

---

## Scan statistics

| Metric | Value |
|---|---|
| Python files in `pixelref/` source tree | **136** |
| Python files scanned total (incl. `packaging/`, `tools/`, `examples/`, tests) | **170** (136 + 27 tests + 4 tools + 2 packaging + 1 examples) |
| Dynamic-execution sinks verified | 6 (console eval/exec, keymap python, macros, plugins import, batch runpy, userSetup) |
| Process-execution sinks verified | 3 (subprocess.Popen, QProcess, runpy.run_module) + 1 build-time (packaging/build_exe.py) |
| Network sinks verified | 2 (QNetworkAccessManager downloads, D-Bus portal) |
| Deserialization paths verified | 4 (JSON, TOML, base64, .pur binary) |
| Passes completed | 2 (per-file + system-level) |

## Confidence score

**9 / 10** — High confidence. Every known dangerous sink in the tree was located via pattern sweeps and individually verified, and the cross-component data flows (untrusted data → execution sinks, untrusted data → OS handlers) were traced end-to-end. The residual 10% reflects the inherent nature of a plugin/script architecture: security depends on the local user trusting their own plugin/script/package content, which is by design and consistent with comparable tools (Maya, Blender, PureRef).

---

## How to stay secure — good practices while using pixelRef

The audit found no flaws in the software itself, but pixelRef is a **code-execution platform by design**: it runs Python from plugins, scripts, keymaps, and the console. Staying secure therefore means controlling *what content you let it run*.

### 1. Treat plugins, scripts, and packages as code — review before installing
- **Plugins** (`$PIXELREF_PLUGIN_PATH`, the plugin folder, or the Plugins window) are imported and executed at startup. Read a plugin's `__init__.py` before enabling it; a plugin can do anything the app can.
- **Scripts** (`scripts/` folder, `$PIXELREF_SCRIPT_PATH`, `userSetup.py`) run through the console engine. `userSetup.py` runs **every launch** — keep it minimal and under your control.
- **Packages** installed via the Plugins window are pip packages added to `sys.path`. Prefer well-known packages; a malicious package's import-time code runs when the app loads it.
- Prefer official/bundled plugins and reputable sources; treat third-party plugins like third-party npm/PyPI packages.

### 2. Be deliberate with the Script Editor and keymap
- The console (`eval`/`exec`) and `python = "..."` keymap entries execute arbitrary Python with full access to the app, filesystem, and network. Only run code you understand.
- When pasting code from the web into the console, read it first — it can read/write files or make network requests.
- Keep `keymap.toml` under version control or periodic review so unexpected `python`/`script` entries are noticed.

### 3. Handle untrusted scene files (`.pxref`) and PureRef boards carefully
- Scene files are JSON and cannot execute code directly, but they **can carry links** that open in your browser or OS handlers when you click them. Don't blindly click links in boards shared by strangers.
- Remote images are only fetched from `http`/`https` URLs you paste or that a scene references. Review URLs before pasting; the app does not fetch anything on its own.
- Open boards from untrusted sources in a state where you don't mind them referencing local paths (links resolve relative to the scene folder).

### 4. Protect the local environment the app trusts
- pixelRef inherits your user environment. If your account is compromised, so is the app — keep the OS account, Python interpreter, and pip index (PyPI) trusted.
- Don't run pixelRef as an admin/root account; it needs no elevated privileges.
- If you set `PIXELREF_PACKAGES_DIR`, `PIXELREF_PLUGIN_PATH`, or `PIXELREF_SCRIPT_PATH`, point them at folders only you can write to — anyone who can write there can run code at your next launch.

### 5. Keep the software current
- Update pixelRef regularly; this audit reflects the current tree, and future versions should be re-audited or re-verified.
- Keep PySide6/Qt and the Python runtime patched — they handle image decoding, network, and rendering, and are the largest third-party attack surface the app depends on.
- Image decoding (PNG/JPEG/etc.) is delegated to Qt; prefer well-formed images from trusted sources if you process large volumes of unknown files.

### 6. Operational hygiene
- **Back up your scene files and `keymap.toml`** — they are plain text and easy to diff after an unexpected change.
- **Least privilege for content:** store boards in normal user folders, not system directories.
- **Sandbox for experiments:** when testing untrusted plugins/scripts, use a dedicated user account or VM so any misbehavior is contained.
- **Audit after changes:** after adding plugins, scripts, or keymap entries, re-check `git diff`-style what changed in your config folders.
- **Don't paste secrets into the console output pane** expecting privacy — the console can echo commands, and output is visible in the UI.

### 7. Quick checklist

| Action | Why |
|---|---|
| Read plugin/script code before enabling | They run at startup with full local access |
| Review `userSetup.py` and `keymap.toml` periodically | They auto-execute every launch / on keypress |
| Don't click unknown links in shared boards | Links open in browser/OS handlers |
| Keep plugin/script/package folders user-writable-only | Write access = code execution at next launch |
| Run as a normal user, not admin | Limits blast radius of any bug or bad content |
| Update pixelRef, Qt, and Python regularly | Patches fix new vulnerabilities in the stack |
| Back up and diff your config folders | Detects unexpected changes to auto-run content |
