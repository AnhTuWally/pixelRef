"""Third-party plugins: sources, trust, signatures, conflicts and the activity monitor.

* nothing in a plugin folder runs until the user trusts it; approval is pinned
  to the plugin's files, and a changed plugin needs approving again
* publishers sign plugins (Ed25519); signed plugins from trusted publishers
  load without asking, tampered ones don't load at all
* several copies of one id: one is used, the others are listed, the user
  picks; built-in ids are reserved
* a second plugin registering a taken name fails (unless it declares an override)
* the activity monitor reports what third-party plugins do beyond their
  declared permissions
* the Plugins window: search, filters, details page, approval, sources
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path

from harness import APP, Harness, tmp_dir

COMPLETE = '''
license = "MIT"
[author]
name = "Jo Maker"
organization = "Example Studio"
[source]
homepage = "https://example.com/plugins"
'''


def write_plugin(folder: Path, plugin_id: str, manifest: str = "", code: str = "def activate(ctx):\n    pass\n",
                 name: str | None = None) -> Path:
    plugin = folder / (name or plugin_id)
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.toml").write_text(f'id = "{plugin_id}"\nname = "{plugin_id}"\n{manifest}\n', encoding="utf-8")
    (plugin / "__init__.py").write_text(code, encoding="utf-8")
    return plugin


def marker_code(marker: Path, body: str = "    pass\n") -> str:
    """Plugin code that leaves a file behind as soon as it is imported."""
    return f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\ndef activate(ctx):\n{body}"


def headless(paths=(), trusted=(), settings=None, builtins=True, quiet=True):
    from pixelref.core.packages import PackageManager
    from pixelref.core.session import Session
    from pixelref.core.settings import MemorySettings

    session = Session(plugin_paths=list(paths), trusted_plugin_paths=list(trusted), builtins=builtins,
                      settings=settings if settings is not None else MemorySettings(), make_current=False,
                      packages=PackageManager(root=tmp_dir()))
    if quiet:  # the tests' files live in the temp folder, which the monitor normally ignores
        session.plugins.monitor.quiet_folders = []
    return session.start()


def make_key(name: str = "Example Studio") -> Path:
    from pixelref.core.plugin_trust import generate_key

    key = tmp_dir() / "studio.pem"
    generate_key(key, name)
    return key


def forget(prefix: str = "pixelref_plugins.") -> None:
    for name in [n for n in sys.modules if n.startswith(prefix)]:
        del sys.modules[name]


# ---- Approval ---------------------------------------------------------------------------------------------------


def test_a_new_plugin_does_not_run_until_approved():
    folder = tmp_dir()
    marker = tmp_dir() / "ran.txt"
    write_plugin(folder, "newcomer", code=marker_code(marker, "    ctx.command('hi', lambda: 'hi')\n"))
    session = headless([folder])
    try:
        record = session.plugins.get("newcomer")
        assert record.trust == "unverified" and not record.active
        assert not marker.exists(), "its code was never imported"
        assert session.plugins.needs_review() == ["newcomer"]
        info = session.run("plugin_info", "newcomer")
        assert info["needs_review"] and info["trust_label"] == "Not reviewed"
        try:
            session.run("load_plugin", "newcomer")
        except Exception as exc:  # noqa: BLE001
            assert "approval" in str(exc), exc
        else:
            raise AssertionError("enabling an unreviewed plugin must fail")
        assert not marker.exists()
        session.run("plugin_trust", "newcomer")
        assert record.active and marker.exists() and session.run("newcomer.hi") == "hi"
        assert record.trust == "trusted" and session.plugins.needs_review() == []
    finally:
        session.shutdown()
        forget()


def test_approval_is_pinned_to_the_files():
    from pixelref.core.settings import MemorySettings

    folder = tmp_dir()
    plugin = write_plugin(folder, "pinned", code="def activate(ctx):\n    ctx.command('v', lambda: 1)\n")
    settings = MemorySettings()
    session = headless([folder], settings=settings)
    session.run("plugin_trust", "pinned")
    assert session.run("pinned.v") == 1
    session.shutdown()
    forget()
    # The next start: approved, the same files -> runs without asking.
    session = headless([folder], settings=settings)
    assert session.plugins.get("pinned").active
    session.shutdown()
    forget()
    # Someone changes the code: it doesn't run, and says what changed.
    (plugin / "__init__.py").write_text("def activate(ctx):\n    ctx.command('v', lambda: 2)\n", encoding="utf-8")
    (plugin / "extra.py").write_text("X = 1\n", encoding="utf-8")
    session = headless([folder], settings=settings)
    try:
        record = session.plugins.get("pinned")
        assert record.trust == "changed" and not record.active and record.state == "untrusted"
        assert session.plugins.needs_review() == ["pinned"]
        assert session.run("plugin_changes", "pinned") == ["+ extra.py", "~ __init__.py"]
        session.run("plugin_trust", "pinned")
        assert session.run("pinned.v") == 2
    finally:
        session.shutdown()
        forget()


def test_files_changed_after_approval_are_caught_when_the_code_is_loaded():
    folder = tmp_dir()
    plugin = write_plugin(folder, "racer", code="def activate(ctx):\n    ctx.command('v', lambda: 1)\n")
    session = headless([folder])
    try:
        session.plugins.trust_plugin("racer")
        # Changed between the approval and the import: the import checks again.
        (plugin / "__init__.py").write_text("def activate(ctx):\n    ctx.command('v', lambda: 666)\n",
                                            encoding="utf-8")
        assert not session.plugins.activate("racer")
        record = session.plugins.get("racer")
        assert record.trust == "changed" and record.state == "untrusted"
        assert not session.commands.exists("racer.v")
        # Reload re-checks too.
        session.plugins.trust_plugin("racer")
        session.plugins.set_enabled("racer", True)
        assert session.run("racer.v") == 666
        (plugin / "__init__.py").write_text("def activate(ctx):\n    ctx.command('v', lambda: 3)\n",
                                            encoding="utf-8")
        assert not session.plugins.reload("racer") and record.trust == "changed"
    finally:
        session.shutdown()
        forget()


def test_revoking_switches_off_and_forgets():
    folder = tmp_dir()
    write_plugin(folder, "revocable")
    session = headless([folder])
    try:
        session.run("plugin_trust", "revocable")
        assert session.plugins.is_active("revocable")
        session.run("plugin_revoke_trust", "revocable")
        record = session.plugins.get("revocable")
        assert not record.active and record.trust == "unverified" and record.state == "disabled"
        assert session.plugins.needs_review() == [], "switched off on purpose: no nagging"
    finally:
        session.shutdown()
        forget()


# ---- Signatures ---------------------------------------------------------------------------------------------------


def test_signed_plugins_from_trusted_publishers_load_without_asking():
    from pixelref.core.errors import PluginError
    from pixelref.core.plugin_trust import read_public_key, sign_plugin

    folder = tmp_dir()
    plugin = write_plugin(folder, "signed_one", COMPLETE, code="def activate(ctx):\n    ctx.command('v', lambda: 1)\n")
    key = make_key()
    check = sign_plugin(plugin, key, "Example Studio")
    assert check.status == "valid" and check.signer == "Example Studio"
    public = read_public_key(key.with_name("studio.pub.toml"))
    assert public["key_id"] == check.key_id
    session = headless([folder])
    try:
        record = session.plugins.get("signed_one")
        assert record.trust == "unverified" and "isn't one of your trusted publishers" in record.trust_reason
        session.run("add_trusted_publisher", str(key.with_name("studio.pub.toml")))
        assert record.trust == "verified" and record.active, "enabled by default once verified"
        assert session.run("trusted_publishers")[0]["name"] == "Example Studio"
        session.run("remove_trusted_publisher", public["key_id"])
        assert record.trust == "unverified" and not record.active
    finally:
        session.shutdown()
        forget()
    # A plugin that doesn't say who made it can't be signed.
    bare = write_plugin(tmp_dir(), "anonymous")
    try:
        sign_plugin(bare, key, "Example Studio")
    except PluginError as exc:
        assert "author.name" in str(exc) and "license" in str(exc)
    else:
        raise AssertionError("signing needs provenance")


def test_a_tampered_signed_plugin_never_runs():
    from pixelref.core.plugin_trust import sign_plugin

    folder = tmp_dir()
    marker = tmp_dir() / "ran.txt"
    plugin = write_plugin(folder, "tampered", COMPLETE)
    key = make_key()
    sign_plugin(plugin, key, "Example Studio")
    (plugin / "__init__.py").write_text(marker_code(marker), encoding="utf-8")
    session = headless([folder])
    try:
        session.run("add_trusted_publisher", str(key.with_name("studio.pub.toml")))
        record = session.plugins.get("tampered")
        assert record.trust == "invalid" and "__init__.py" in record.trust_reason
        assert not record.active and not marker.exists()
        try:
            session.run("plugin_trust", "tampered")
        except Exception as exc:  # noqa: BLE001
            assert "allow_invalid" in str(exc)
        else:
            raise AssertionError("an invalid signature needs an explicit override")
        assert not marker.exists()
        session.run("plugin_trust", "tampered", allow_invalid=True)
        assert record.active and marker.exists() and record.trust == "trusted"
    finally:
        session.shutdown()
        forget()


def test_a_signature_by_a_different_key_with_the_same_id_is_not_trusted():
    """Copying a trusted publisher's key id into a signature made with another key fails."""
    from pixelref.core.plugin_trust import sign_plugin

    folder = tmp_dir()
    plugin = write_plugin(folder, "impostor", COMPLETE)
    real = make_key("Real")
    fake = make_key("Fake")
    sign_plugin(plugin, fake, "Real")
    signature = (plugin / "SIGNATURE.toml").read_text(encoding="utf-8")
    from pixelref.core.plugin_trust import read_public_key

    real_id = read_public_key(real.with_name("studio.pub.toml"))["key_id"]
    fake_id = read_public_key(fake.with_name("studio.pub.toml"))["key_id"]
    (plugin / "SIGNATURE.toml").write_text(signature.replace(fake_id, real_id), encoding="utf-8")
    session = headless([folder])
    try:
        session.run("add_trusted_publisher", str(real.with_name("studio.pub.toml")))
        assert session.plugins.get("impostor").trust == "invalid"
        assert "key id" in session.plugins.get("impostor").trust_reason
    finally:
        session.shutdown()
        forget()


def test_plugin_cli_signs_and_verifies():
    from pixelref.plugin_cli import main

    work = tmp_dir()
    plugin = write_plugin(work, "cli_signed", COMPLETE)
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        assert main(["keygen", str(work / "me.pem"), "--name", "Me"]) == 0
        assert main(["verify", str(plugin)]) == 1, "not signed yet"
        assert main(["sign", str(plugin), "--key", str(work / "me.pem")]) == 0
        assert main(["verify", str(plugin), "--publisher", str(work / "me.pub.toml")]) == 0
        assert main(["hash", str(plugin)]) == 0
        (plugin / "__init__.py").write_text("# changed\n", encoding="utf-8")
        assert main(["verify", str(plugin)]) == 1
    text = out.getvalue()
    assert "Signed" in text and "Valid signature by Me" in text and "INVALID" in text


# ---- Sources ----------------------------------------------------------------------------------------------------


def test_sources_policies_and_precedence():
    folder = tmp_dir()
    write_plugin(folder, "from_added", code="def activate(ctx):\n    ctx.command('v', lambda: 1)\n")
    session = headless()
    try:
        source = session.run("add_plugin_source", str(folder), label="Studio")
        assert source["kind"] == "added" and source["policy"] == "ask"
        assert session.plugins.get("from_added").trust == "unverified"
        assert session.run("plugins_needing_review") == ["from_added"]
        info = next(s for s in session.run("plugin_sources") if s["kind"] == "added")
        assert info["plugins"] == 1 and info["title"] == "Studio"
        # Only trusted publishers: an unsigned plugin can't even be approved.
        session.run("set_plugin_source", str(folder), policy="signed_only")
        assert session.plugins.get("from_added").trust == "blocked"
        try:
            session.run("plugin_trust", "from_added")
        except Exception:  # noqa: BLE001
            pass
        else:
            raise AssertionError("blocked by the folder's policy")
        # A developer folder: everything runs.
        session.run("set_plugin_source", str(folder), policy="trust_all")
        assert session.plugins.get("from_added").trust == "session" and session.run("from_added.v") == 1
        # Switching the folder off removes its plugins at once.
        session.run("set_plugin_source", str(folder), enabled=False)
        assert session.plugins.get("from_added") is None and not session.commands.exists("from_added.v")
        session.run("set_plugin_source", str(folder), enabled=True)
        assert session.plugins.is_active("from_added")
        session.run("remove_plugin_source", str(folder))
        assert session.plugins.get("from_added") is None
        assert not any(s["kind"] == "added" for s in session.run("plugin_sources"))
    finally:
        session.shutdown()
        forget()


def test_env_and_command_line_folders_need_approval_unless_trusted():
    env_folder = tmp_dir()
    write_plugin(env_folder, "from_env")
    trusted = tmp_dir()
    write_plugin(trusted, "from_trusted")
    single = write_plugin(tmp_dir(), "single_plugin")
    old = os.environ.get("PIXELREF_PLUGIN_PATH")
    os.environ["PIXELREF_PLUGIN_PATH"] = str(env_folder)
    try:
        session = headless([single], trusted=[trusted])
        try:
            assert session.plugins.get("from_env").trust == "unverified"
            assert not session.plugins.is_active("from_env")
            assert session.plugins.get("single_plugin").trust == "unverified", "a source can be one plugin folder"
            assert session.plugins.get("from_trusted").trust == "session"
            assert session.plugins.is_active("from_trusted")
            kinds = {s["kind"] for s in session.run("plugin_sources")}
            assert {"builtin", "env", "cli"} <= kinds
        finally:
            session.shutdown()
    finally:
        if old is None:
            os.environ.pop("PIXELREF_PLUGIN_PATH", None)
        else:
            os.environ["PIXELREF_PLUGIN_PATH"] = old
        forget()


# ---- Conflicts --------------------------------------------------------------------------------------------------


def test_two_copies_of_a_plugin_are_listed_and_the_user_picks():
    first, second = tmp_dir(), tmp_dir()
    write_plugin(first, "twin", 'version = "1.0.0"', code="def activate(ctx):\n    ctx.command('v', lambda: 1)\n")
    write_plugin(second, "twin", 'version = "2.0.0"', code="def activate(ctx):\n    ctx.command('v', lambda: 2)\n")
    session = headless(trusted=[first, second])
    try:
        info = session.run("plugin_info", "twin")
        assert info["version"] == "1.0.0", "the earlier source wins"
        assert [c["version"] for c in info["conflicts"]] == ["2.0.0"]
        assert session.run("twin.v") == 1
        session.run("plugin_resolve", "twin", str(second / "twin"))
        assert session.run("twin.v") == 2, "the other copy's code runs now"
        assert session.run("plugin_info", "twin")["conflicts"][0]["version"] == "1.0.0"
    finally:
        session.shutdown()
        forget()


def test_built_in_ids_are_reserved():
    folder = tmp_dir()
    marker = tmp_dir() / "ran.txt"
    write_plugin(folder, "rect", code=marker_code(marker), name="fake_rect")
    write_plugin(folder, "select", 'replaces = "select"', name="fake_select")
    session = headless(trusted=[folder])
    try:
        record = session.plugins.get("rect")
        assert record.manifest.builtin and record.active, "the built-in copy is used"
        other = record.conflicts[0]
        assert "replaces" in other.blocked and not marker.exists()
        try:
            session.run("plugin_resolve", "rect", str(folder / "fake_rect"))
        except Exception:  # noqa: BLE001
            pass
        else:
            raise AssertionError("a plugin can't take a built-in's id without saying so")
        select = session.plugins.get("select")
        assert select.manifest.builtin and "foundation" in select.conflicts[0].blocked
    finally:
        session.shutdown()
        forget()
    # One that declares itself a replacement can be picked (a feature plugin, not a foundation one).
    folder = tmp_dir()
    write_plugin(folder, "rect", 'replaces = "rect"', name="my_rect",
                 code="def activate(ctx):\n    ctx.command('mine', lambda: 'mine')\n")
    session = headless(trusted=[folder])
    try:
        session.run("plugin_resolve", "rect", str(folder / "my_rect"))
        assert session.run("rect.mine") == "mine" and not session.commands.exists("rect.create")
    finally:
        session.shutdown()
        forget()


def test_manifest_rules_for_third_party_plugins():
    from pixelref.core.errors import PluginError
    from pixelref.core.plugins import PluginManifest

    folder = tmp_dir()
    for plugin_id, extra, problem in (
        ("Bad-Id", "", "snake_case"),
        ("pixelref_tools", "", "reserved"),
        ("bad_perm", '[permissions]\nnetwork = "yes"', "true or false"),
        ("bad_override", 'overrides = ["thing:x"]', "overrides"),
        ("bad_replace", 'replaces = "other"', "replaces"),
    ):
        plugin = write_plugin(folder, plugin_id, extra, name=f"p_{len(problem)}_{plugin_id.lower()}")
        try:
            PluginManifest.load(plugin, builtin=False)
        except PluginError as exc:
            assert problem in str(exc), (plugin_id, exc)
        else:
            raise AssertionError(f"{plugin_id} should not load")
    plugin = write_plugin(folder, "full", '''
foundation = true
tags = ["shapes"]
category = "Drawing"
surprise = 1
''' + COMPLETE + '''
[permissions]
network = true
teleport = true
''')
    manifest = PluginManifest.load(plugin, builtin=False)
    assert manifest.author == {"name": "Jo Maker", "organization": "Example Studio"}
    assert manifest.license == "MIT" and manifest.homepage == "https://example.com/plugins"
    assert manifest.permissions == frozenset({"network"}) and manifest.tags == ["shapes"]
    assert not manifest.foundation, "only built-in plugins can be foundation plugins"
    assert manifest.publish_missing == []
    warnings = " ".join(manifest.warnings)
    assert "surprise" in warnings and "teleport" in warnings and "foundation" in warnings
    misplaced = write_plugin(folder, "misplaced", COMPLETE + 'tags = ["late"]\n')
    assert "Unknown keys in [source]: tags" in " ".join(PluginManifest.load(misplaced, builtin=False).warnings)


# ---- Registrations --------------------------------------------------------------------------------------------


def test_a_second_plugin_taking_a_name_fails_unless_it_declares_an_override():
    folder = tmp_dir()
    write_plugin(folder, "first_one", code=(
        "def activate(ctx):\n"
        "    ctx.command('go', lambda: 'first', alias='go_now')\n"
        "    ctx.provide_service('greeter', 'first')\n"))
    write_plugin(folder, "squatter", 'requires = ["first_one"]', code=(
        "def activate(ctx):\n    ctx.command('go', lambda: 'squatter', alias='go_now')\n"))
    write_plugin(folder, "replacer", 'requires = ["first_one"]\n'
                                     'overrides = ["command:first_one.go", "service:greeter"]', code=(
        "def activate(ctx):\n"
        "    ctx.command('first_one.go', lambda: 'replaced', alias='go_now')\n"
        "    ctx.provide_service('greeter', 'replacer')\n"))
    write_plugin(folder, "alias_thief", code="def activate(ctx):\n    ctx.command('x', lambda: 0, alias='create_ellipse')\n")
    session = headless(trusted=[folder])
    try:
        squatter = session.plugins.get("squatter")
        assert squatter.state == "error" and "Command alias 'go_now' is already registered by plugin" in squatter.error
        assert session.plugins.is_active("replacer")
        assert session.run("first_one.go") == "replaced" and session.run("go_now") == "replaced"
        assert session.services.get("greeter") == "replacer"
        session.run("unload_plugin", "replacer")
        assert session.run("first_one.go") == "first" and session.run("go_now") == "first", "the original is back"
        assert session.services.get("greeter") == "first"
        # Built-in plugins activate first: a third-party plugin can't knock one out by taking its names.
        assert session.plugins.is_active("rect")
        thief = session.plugins.get("alias_thief")
        assert thief.state == "error" and "plugin 'rect'" in thief.error
    finally:
        session.shutdown()
        forget()


def test_plugin_wheels_are_only_used_for_that_plugins_packages():
    folder = tmp_dir()
    for plugin_id in ("wheel_a", "wheel_b"):
        plugin = write_plugin(folder, plugin_id)
        (plugin / "wheels").mkdir()
    write_plugin(folder, "wheel_untrusted", name="wheel_untrusted")
    (folder / "wheel_untrusted" / "wheels").mkdir()
    session = headless([folder])
    try:
        for plugin_id in ("wheel_a", "wheel_b"):
            session.plugins.trust_plugin(plugin_id)
        packages = session.packages
        args = packages._pip_install_args(tmp_dir(), ["anything"], ["wheel_a"])
        links = [args[i + 1] for i, arg in enumerate(args) if arg == "--find-links"]
        assert str(folder / "wheel_a" / "wheels") in links
        assert str(folder / "wheel_b" / "wheels") not in links, "another plugin's wheels aren't used"
        args = packages._pip_install_args(tmp_dir(), ["anything"], ["wheel_untrusted"])
        assert str(folder / "wheel_untrusted" / "wheels") not in args, "an unapproved plugin's wheels aren't used"
        assert set(session.plugins.wheel_folders()) == {folder / "wheel_a" / "wheels", folder / "wheel_b" / "wheels"}
    finally:
        session.shutdown()
        forget()


# ---- The activity monitor -----------------------------------------------------------------------------------------


MONITORED = '''
import dataclasses
import socket
import subprocess
import sys
from pathlib import Path


@dataclasses.dataclass
class Settings:  # the standard library compiles code here: not the plugin's doing
    size: int = 1


def activate(ctx):
    ctx.command("write", lambda path: Path(path).write_text("x"))
    ctx.command("lookup", lambda: socket.getaddrinfo("localhost", 80))
    ctx.command("spawn", lambda: subprocess.run([sys.executable, "-c", "pass"], check=True))
    ctx.command("dynamic", lambda: exec("value = 1 + 1", {}))
    ctx.command("save", lambda path: ctx.run("save_scene", path))
'''


def test_the_activity_monitor_reports_undeclared_activity():
    folder = tmp_dir()
    write_plugin(folder, "watched", '[permissions]\nsubprocess = true', code=MONITORED)
    session = headless(trusted=[folder])
    target = tmp_dir() / "out.txt"
    try:
        assert session.plugins.is_active("watched")
        assert session.plugins.monitor.entries("watched") == [], "defining a dataclass isn't 'dynamic code'"
        session.run("watched.write", str(target))
        session.run("watched.lookup")
        session.run("watched.spawn")
        session.run("watched.dynamic")
        session.run("watched.save", str(tmp_dir() / "board.pxref"))
        entries = session.run("plugin_activity", "watched")
        kinds = {(e["kind"], e["violation"]) for e in entries}
        assert ("write_files", True) in kinds and ("network", True) in kinds
        assert ("subprocess", False) in kinds, "declared: recorded, not a violation"
        assert ("dynamic_code", True) in kinds
        saved = [e for e in entries if e["kind"] == "write_files" and "board.pxref" in e["detail"]]
        assert saved and all(e["via_pixelref"] and not e["violation"] for e in saved), "pixelRef saved it for the plugin"
        assert any(e["detail"].startswith("rename ") for e in saved), "renames count as writing"
        written = next(e for e in entries if e["detail"] == str(target))
        assert written["file"].endswith("__init__.py") and written["line"] > 0
        summary = session.run("plugin_info", "watched")["activity"]
        assert summary["violations"] >= 3 and summary["counts"]["subprocess"] == 1
        # Built-in plugins aren't watched.
        assert session.plugins.monitor.entries("rect") == []
        # Off: nothing is recorded.
        session.run("clear_plugin_activity", "watched")
        session.run("plugin_monitor", False)
        session.run("watched.write", str(target))
        assert session.run("plugin_activity", "watched") == []
        session.run("plugin_monitor", True)
    finally:
        session.shutdown()
        forget()


def test_a_monitor_bug_never_breaks_what_it_watches():
    folder = tmp_dir()
    write_plugin(folder, "fragile", code=MONITORED)
    session = headless(trusted=[folder])
    monitor = session.plugins.monitor

    def broken(*_args):
        raise RuntimeError("monitor bug")

    original = monitor._classify
    monitor._classify = broken
    target = tmp_dir() / "out.txt"
    try:
        session.run("fragile.write", str(target))
        assert target.read_text() == "x"
    finally:
        monitor._classify = original
        session.shutdown()
        forget()


def test_monitor_ignores_quiet_folders_and_logs_violations_to_a_file():
    folder = tmp_dir()
    write_plugin(folder, "logged", code=MONITORED)
    session = headless(trusted=[folder], quiet=False)
    log = tmp_dir() / "activity.jsonl"
    session.plugins.monitor.log_path = log
    try:
        session.run("logged.write", str(tmp_dir() / "scratch.txt"))  # the temp folder: quiet
        assert session.run("plugin_activity", "logged") == []
        session.run("logged.lookup")
        assert '"kind": "network"' in log.read_text(encoding="utf-8")
    finally:
        session.shutdown()
        forget()


# ---- Docs ---------------------------------------------------------------------------------------------------------


def test_plugin_docs_show_provenance_and_the_readme():
    folder = tmp_dir()
    plugin = write_plugin(folder, "documented", 'tags = ["vector"]\n' + COMPLETE + '[permissions]\nnetwork = true\n')
    (plugin / "README.md").write_text("# Documented\n\nDraws **wonderful** things.\n", encoding="utf-8")
    session = headless([folder])
    try:
        entry = session.docs.get("plugin:documented")
        body = session.docs.markdown(entry)
        assert "Jo Maker" in body and "Not reviewed" in body and "network" in body
        assert "wonderful" in body and "### Documented" in body, "README headings sit under the entry's"
        assert any(e.name == "documented" for e in session.docs.search("vector", kinds="plugin"))
        assert session.docs.get("guide:security") is not None, "the safety guide is in the Help browser"
    finally:
        session.shutdown()
        forget()


# ---- The Plugins window ------------------------------------------------------------------------------------------


def clear_plugin_settings(session) -> None:
    for key in ("plugins/trust", "plugins/publishers", "plugins/sources", "plugins/resolved", "plugins/disabled",
                "plugins/enabled", "plugins/risk_notice_seen", "plugins/developer_tools", "plugins/monitor",
                "macros/risk_notice_seen"):
        session.settings.remove(key)


def test_plugins_window_search_filters_details_and_approval():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog

    from pixelref.ui import plugin_manager
    from pixelref.ui.plugin_manager import ApprovalDialog, PluginManagerDialog

    folder = tmp_dir()
    plugin = write_plugin(folder, "shiny", 'tags = ["sparkle"]\n' + COMPLETE + '[permissions]\nnetwork = true\n',
                          code="def activate(ctx):\n    ctx.command('polish', lambda: 'shiny')\n")
    (plugin / "README.md").write_text("# Shiny\n\nMakes things **shine**.\n", encoding="utf-8")
    h = Harness(plugin_paths=(folder,), trusted=False)
    clear_plugin_settings(h.session)
    dialog = PluginManagerDialog(h.app, h.window)
    try:
        assert h.app.notify_plugin_review() == ["shiny"]
        assert not dialog.notice.isHidden(), "the first time: what plugins can do"
        dialog.dismiss_notice()
        assert dialog.notice.isHidden() and h.session.settings.value("plugins/risk_notice_seen", False, type=bool)
        assert "need" in dialog.risk_banner.text() and "review" in dialog.risk_banner.text()
        dialog.risk_banner.linkActivated.emit("guide")
        assert dialog.tabs.currentWidget() is dialog.safety, "Learn more opens the Safety tab, not a window behind"
        assert "What it can't protect you from" in dialog.safety.toPlainText()
        dialog.tabs.setCurrentIndex(0)
        row = next(i for i, info in enumerate(dialog.model.rows) if info["id"] == "shiny")
        assert dialog.model.index(row, 1).data() == "awaiting review"
        assert dialog.model.index(row, 4).data() == "? Not reviewed"
        # Filters and search.
        dialog.set_chip("attention")
        assert dialog.visible_ids() == ["shiny"]
        dialog.set_chip("builtin")
        assert "shiny" not in dialog.visible_ids() and "rect" in dialog.visible_ids()
        dialog.set_chip("all")
        for query in ("jo maker", "sparkle", "example studio"):
            dialog.search.setText(query)
            assert "shiny" in dialog.visible_ids(), query
        dialog.search.setText("ellipse")
        assert "rect" in dialog.visible_ids(), "found by a command it provides (rect.create_ellipse)"
        dialog.search.clear()
        # The details page.
        dialog.select_plugin("shiny")
        page = dialog.details.toPlainText()
        for expected in ("Jo Maker", "Example Studio", "not signed", "MIT", "Network", "shine", "Not reviewed"):
            assert expected in page, expected
        assert dialog.trust_button.text().startswith("Review")
        # The approval dialog says what approving means; Cancel is the default.
        info = h.session.plugins.info("shiny")
        approval = ApprovalDialog(h.app, info, [], dialog)
        assert "permissions" in approval.findChildren(type(dialog.risk_banner))[0].text()
        assert "Network" in approval.details.toPlainText()
        approval.reject()
        # Approving from the window (the dialog answered "Approve").
        original = ApprovalDialog.exec
        ApprovalDialog.exec = lambda self: QDialog.Accepted
        try:
            dialog.model.setData(dialog.model.index(row, 0), Qt.Checked, Qt.CheckStateRole)
        finally:
            ApprovalDialog.exec = original
        assert h.session.plugins.is_active("shiny") and h.session.run("shiny.polish") == "shiny"
        row = next(i for i, info in enumerate(dialog.model.rows) if info["id"] == "shiny")
        assert dialog.model.index(row, 4).data() == "● Approved"
        assert "Revoke" in dialog.trust_button.text()
        assert h.app.notify_plugin_review() == []
        assert plugin_manager.status_text(h.session.plugins.info("shiny")) == "active"
    finally:
        dialog.reject()
        clear_plugin_settings(h.session)
        h.close()
        forget()


def test_plugins_window_invalid_signature_needs_explicit_confirmation():
    from pixelref.core.plugin_trust import sign_plugin
    from pixelref.ui.plugin_manager import ApprovalDialog

    folder = tmp_dir()
    plugin = write_plugin(folder, "broken_seal", COMPLETE)
    sign_plugin(plugin, make_key(), "Example Studio")
    (plugin / "__init__.py").write_text("# tampered\ndef activate(ctx):\n    pass\n", encoding="utf-8")
    h = Harness(plugin_paths=(folder,), trusted=False)
    try:
        info = h.session.plugins.info("broken_seal")
        assert info["trust"] == "invalid"
        approval = ApprovalDialog(h.app, info, [], h.window)
        assert not approval.approve_button.isEnabled()
        approval.confirm_invalid.setChecked(True)
        assert approval.approve_button.isEnabled()
        approval.accept()
        assert approval.allow_invalid
    finally:
        clear_plugin_settings(h.session)
        h.close()
        forget()


def test_plugins_window_sources_tab_and_resolving_copies():
    from PySide6.QtCore import Qt, QUrl

    from pixelref.ui.plugin_manager import PluginManagerDialog

    first, second = tmp_dir(), tmp_dir()
    write_plugin(first, "doubled", 'version = "1.0.0"')
    write_plugin(second, "doubled", 'version = "2.0.0"')
    h = Harness(plugin_paths=(first,), trusted=True)
    clear_plugin_settings(h.session)
    dialog = PluginManagerDialog(h.app, h.window)
    try:
        dialog.add_source(str(second), confirm=False)
        assert dialog.proxy.chip == "attention", "new plugins to look at"
        sources = [dialog.sources_tree.topLevelItem(i).data(0, Qt.UserRole)
                   for i in range(dialog.sources_tree.topLevelItemCount())]
        assert any(s["kind"] == "added" and s["plugins"] == 1 for s in sources)
        # Added folders come before command-line ones: the new copy is used, and needs approving.
        doubled = h.session.plugins.get("doubled")
        assert doubled.manifest.version == "2.0.0" and doubled.trust == "unverified" and not doubled.active
        dialog.select_plugin("doubled")
        page = dialog.details.toPlainText()
        assert "Other copies found" in page and "1.0.0" in page and "Use this copy" in page
        dialog._on_link(QUrl("resolve:0"))
        doubled = h.session.plugins.get("doubled")
        assert doubled.manifest.version == "1.0.0" and doubled.active, "the trusted copy runs again"
        h.session.run("plugin_resolve", "doubled", str(second / "doubled"))
        assert h.session.plugins.get("doubled").trust == "unverified"
        # Trust everything in a folder only after typing its name.
        dialog.confirm_trust_all = lambda path: False
        item = next(dialog.sources_tree.topLevelItem(i) for i in range(dialog.sources_tree.topLevelItemCount())
                    if dialog.sources_tree.topLevelItem(i).data(0, Qt.UserRole)["kind"] == "added")
        item.setSelected(True)
        dialog.policy_combo.setCurrentIndex(dialog.policy_combo.findData("trust_all"))
        dialog._on_policy(0)
        assert h.session.plugins.get("doubled").trust == "unverified"
        dialog.confirm_trust_all = lambda path: True
        dialog.policy_combo.setCurrentIndex(dialog.policy_combo.findData("trust_all"))
        dialog._on_policy(0)
        assert h.session.plugins.get("doubled").trust == "session"
        assert "trust everything" in dialog.risk_banner.text()
        assert h.app.actions.get("help.security") is not None, "Help menu: Staying Safe…"
        APP.processEvents()
    finally:
        dialog.reject()
        clear_plugin_settings(h.session)
        h.close()
        forget()
