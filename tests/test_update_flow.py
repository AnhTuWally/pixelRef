"""PR-08: preparing an update (download/verify/stage/preflight), the helper hand-over, startup hooks and the
UI service. Offline; the helper and the app's close flow are replaced by recorders."""

from __future__ import annotations

import io
import json
import os
import sys

import test_update_install as ti
import harness
from harness import APP, tmp_dir
from pixelref.core.updates import (Channel, DiscoveryResult, InstallMode, Platform, Release, ReleaseAsset, UpdateState,
                                   Version)
from pixelref.core.updates import flow, install as ins, journal as jr, recovery as rc, staging as st
from pixelref.core.updates.staging import StagingError
from pixelref.core.updates.verification import RELEASE_TRUST, VerificationError

WIN, TRUST = ti.WIN, ti.TRUST
ARCHIVE_URL = "https://github.com/AnhTuWally/pixelRef/releases/download/v1.2.0/pixelRef-v1.2.0-windows-x64.zip"
MANIFEST_URL = "https://github.com/AnhTuWally/pixelRef/releases/download/v1.2.0/manifest-v1.2.0-windows-x64.json"
INSTALLED = "1.1.0"  # the version these tests pretend is running; the fixtures' release is 1.2.0, so the real
#                     app version (which moves with every release) must not leak in.
_undo: list = []


class Harness(harness.Harness):
    """Undoes the patches Env makes even when a test fails before it reaches Env.close()."""

    def close(self) -> None:
        while _undo:
            _undo.pop()()
        super().close()


LATE = {"published_at": "2020-01-01T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z"}  # independent of the clock


def release_set(new=ti.NEW, **manifest_changes):
    data = ti.build_zip(new)
    raw = ti.manifest_bytes(data, **{**LATE, **manifest_changes})

    def opener(url):
        if url == MANIFEST_URL:
            return io.BytesIO(raw), url
        assert url == ARCHIVE_URL, url
        return io.BytesIO(data), url

    zip_asset = ReleaseAsset("pixelRef-v1.2.0-windows-x64.zip", len(data), ARCHIVE_URL)
    manifest_asset = ReleaseAsset("manifest-v1.2.0-windows-x64.json", len(raw), MANIFEST_URL)
    release = Release("v1.2.0", Version.parse("1.2.0"), False, "https://github.com/AnhTuWally/pixelRef/releases/tag/v1.2.0",
                      "Notes <b>plain</b>", "", (zip_asset, manifest_asset))
    result = DiscoveryResult(UpdateState.AVAILABLE, Version.parse("1.1.0"), release, zip_asset, manifest_asset,
                             mode=InstallMode.STANDALONE)
    return result, opener


def prepare(root, result, opener, **kw):
    args = dict(root=root, platform=WIN, installed=Version.parse("1.1.0"), channel=Channel.STABLE, trust=TRUST, opener=opener)
    args.update(kw)
    return flow.prepare_update(result, **args)


def leftovers(root):
    folder = st.staging_root(root)
    return [] if not folder.exists() else list(folder.iterdir())


def test_prepare_update_stages_and_preflights():
    root = ti.make_install()
    result, opener = release_set()
    seen = []
    staged = prepare(root, result, opener, progress=lambda a, b: seen.append((a, b)))
    assert staged.application.is_dir() and seen
    assert result.can_install
    plan = ins.plan_activation(root, staged.transaction_id, trust=TRUST, platform=WIN)
    assert plan.new_version == "1.2.0"
    assert ti.tree(root)["pixelRef.exe"] == b"old-exe"  # preparing never touches the install


def test_prepare_update_failures_change_nothing_and_leave_no_staging():
    root = ti.make_install()
    before = ti.tree(root)
    result, opener = release_set()
    other_key = ti.Ed25519PrivateKey.generate()
    cases = [
        (dict(trust=ti.TrustStore((ti.TrustedKey(other_key.public_key().public_bytes_raw()),))), VerificationError),
        (dict(trust=RELEASE_TRUST), VerificationError),
        (dict(installed=Version.parse("1.2.0")), VerificationError),
        (dict(channel=Channel.STABLE, platform=Platform("windows", "arm64")), VerificationError),
        (dict(free_space=lambda p: 0), StagingError),
        (dict(cancelled=lambda: True), StagingError),
    ]
    for kwargs, error in cases:
        try:
            prepare(root, result, opener, **kwargs)
        except error:
            pass
        else:
            raise AssertionError(kwargs)
        assert leftovers(root) == [] and ti.tree(root) == before
    # an update that is not for the file offered
    wrong = DiscoveryResult(UpdateState.AVAILABLE, result.current, result.release,
                            ReleaseAsset("pixelRef-v1.2.0-windows-x64.zip", 1, ARCHIVE_URL.replace("v1.2.0/", "v1.2.0/")),
                            ReleaseAsset("manifest-v1.2.0-windows-x64.json", 1, "https://evil.example/m.json"),
                            mode=InstallMode.STANDALONE)
    try:
        prepare(root, wrong, opener)
    except StagingError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError
    # source copies and releases without a manifest are not installable
    for bad in (DiscoveryResult(UpdateState.AVAILABLE, result.current, result.release, result.asset, None,
                                mode=InstallMode.STANDALONE),
                DiscoveryResult(UpdateState.AVAILABLE, result.current, result.release, result.asset, result.manifest_asset,
                                mode=InstallMode.SOURCE)):
        assert not bad.can_install
        try:
            prepare(root, bad, opener)
        except StagingError:
            pass
        else:
            raise AssertionError


def test_prepare_update_blocks_on_user_files_and_unfinished_updates():
    root = ti.make_install()
    (root / "_internal" / "mine.txt").write_bytes(b"x")
    result, opener = release_set()
    try:
        prepare(root, result, opener)
    except ins.InstallError as exc:
        assert "unknown file _internal/mine.txt" in str(exc)
    else:
        raise AssertionError
    assert leftovers(root) == []  # the staged copy was removed again
    root = ti.make_install()
    ti.run(root, ti.stage(root))  # leaves PENDING_HEALTH_CHECK
    try:
        prepare(root, *release_set()[:1], release_set()[1])
    except StagingError as exc:
        assert "unfinished" in str(exc)
    else:
        raise AssertionError


def test_assist_status_requires_every_condition():
    root = ti.make_install()
    bundle = helper_bundle()
    kw = dict(mode=InstallMode.STANDALONE, root=root, trust=TRUST, platform=WIN, bundle=bundle)
    assert flow.assist_status(**kw, enabled=True) == (True, "")
    for change, text in ((dict(enabled=False), "not enabled"), (dict(mode=InstallMode.SOURCE), "source"),
                         (dict(root=None), "source"), (dict(trust=ti.TrustStore()), "no release key"),
                         (dict(platform=Platform("linux", "x64")), "not supported"), (dict(bundle=None), "helper"),
                         (dict(bundle=tmp_dir()), "helper")):  # a folder without the executable is not a helper
        args = {**kw, "enabled": True, **change}
        ok, why = flow.assist_status(**args)
        assert not ok and text in why, (change, why)
    assert ins.ASSISTED_INSTALL_ENABLED is False
    assert flow.assist_status(**kw)[0] is False  # off in this build
    if os.name == "posix" and os.geteuid() != 0:
        ro = ti.make_install()
        (ro / ".pixelref-update").chmod(0o500)
        try:
            assert "read-only" in flow.assist_status(**{**kw, "root": ro}, enabled=True)[1]
        finally:
            (ro / ".pixelref-update").chmod(0o700)


class Popen:
    calls: list = []

    def __init__(self, args, **options):
        Popen.calls.append((args, options))
        self.terminated = False

    def terminate(self):
        self.terminated = True


def helper_bundle():
    bundle = tmp_dir()
    (bundle / "pixelref-updater.exe").write_bytes(b"helper")
    (bundle / "lib").mkdir()
    (bundle / "lib" / "x.dll").write_bytes(b"x")
    return bundle


def test_launch_helper_has_a_fixed_command_line():
    root = ti.make_install()
    bundle = helper_bundle()
    Popen.calls.clear()
    tx = "a" * 32
    flow.launch_helper(root, "activate", 4242, transaction=tx, platform=WIN, bundle=bundle, popen=Popen)
    args, options = Popen.calls[-1]
    folder = st.update_directory(root) / "helper" / tx
    assert args == [str(folder / "pixelref-updater.exe"), "activate", tx, "--wait-pid", "4242"]
    assert options["cwd"] == str(folder) and (folder / "lib" / "x.dll").exists()
    assert ins.derive_install_root(folder / "pixelref-updater.exe") == root.resolve()
    flow.launch_helper(root, "rollback", 7, platform=WIN, bundle=bundle, popen=Popen)
    args, _ = Popen.calls[-1]
    assert args[1:] == ["rollback", "--wait-pid", "7"]
    assert ins.derive_install_root(__import__("pathlib").Path(args[0])) == root.resolve()
    for bad in (dict(command="activate"), dict(command="activate", transaction="../x"), dict(command="rm"),
                dict(command="recover", transaction=tx), dict(command="rollback", transaction="a" * 32)):
        try:
            flow.launch_helper(root, bad.pop("command"), 1, platform=WIN, bundle=bundle, popen=Popen, **bad)
        except ins.InstallError:
            continue
        raise AssertionError(bad)
    try:
        flow.launch_helper(root, "recover", 1, platform=WIN, bundle=None, popen=Popen)
    except ins.InstallError as exc:
        assert "not part of this build" in str(exc)
    else:
        raise AssertionError


def test_startup_gate():
    flow.STARTUP.update(state="x")
    assert flow.startup_gate(None) is False and flow.STARTUP["state"] == rc.NORMAL  # source copy: untouched
    root = ti.make_install()
    assert flow.startup_gate(root) is False and flow.STARTUP["state"] == rc.NORMAL
    # a pending update: carry on, twice; the third start hands over to the recovery helper
    ti.run(root, ti.stage(root))
    Popen.calls.clear()
    bundle = helper_bundle()
    results = [flow.startup_gate(root, pid=11, platform=WIN, popen=Popen, bundle=bundle) for _ in range(3)]
    assert results == [False, False, True] and flow.STARTUP["handed_over"]
    args, _ = Popen.calls[-1]
    assert args[1:] == ["recover", "--wait-pid", "11"]
    # without a helper it starts normally and reports that recovery is manual
    root = ti.make_install()
    ti.run(root, ti.stage(root))
    for _ in range(3):
        handed = flow.startup_gate(root, pid=11, platform=WIN, popen=Popen, bundle=None)
    assert handed is False and flow.STARTUP["state"] == rc.RECOVER
    # a damaged journal or a failing helper never stops the app from starting
    (st.update_directory(root) / "journal.json").write_text("{x")
    assert flow.startup_gate(root, popen=Popen) is False and flow.STARTUP["state"] == rc.MANUAL

    def boom(*a, **k):
        raise OSError("no")

    root = ti.make_install()
    ti.run(root, ti.stage(root))
    assert [flow.startup_gate(root, platform=WIN, popen=boom, bundle=bundle) for _ in range(3)] == [False] * 3


def test_last_update_result_messages():
    root = ti.make_install()
    assert flow.last_update_result(root) is None
    ti.run(root, ti.stage(root))
    assert flow.last_update_result(root) is None  # still pending: nothing to report
    rc.recover(root)
    outcome = flow.last_update_result(root)
    assert outcome["state"] == "ROLLED_BACK" and "1.1.0 was restored" in outcome["text"]
    (st.update_directory(root) / "journal.json").write_text("{")
    assert flow.last_update_result(root)["state"] == "DAMAGED"


# ---- UI service ---------------------------------------------------------------------------------

def wait(cond, ms=30000):  # generous: it returns as soon as cond() holds, so only a failing test pays for it
    import time

    end = time.time() + ms / 1000
    while not cond() and time.time() < end:
        APP.processEvents()
        time.sleep(0.01)
    return cond()


class Env:
    def __init__(self, h, root=None, close=True):
        self.h = h
        self.root = root or ti.make_install()
        self.service = h.service("updates")
        self.result, self.opener = release_set()
        s = self.service
        s.mode, s.root, s.trust, s.platform, s.bundle, s.opener = (InstallMode.STANDALONE, self.root, TRUST, WIN,
                                                                   helper_bundle(), self.opener)
        s.result, s.state = self.result, UpdateState.AVAILABLE
        self.closes, self.quits, self.warnings, self.helpers = [], [], [], []
        s.request_close = lambda: (self.closes.append(1), close)[1]
        s.quit = lambda: self.quits.append(1)
        s.warn = lambda title, text: self.warnings.append((title, text))
        s.confirm = lambda title, text: self.confirmed.append(text) or True
        self.confirmed = []

        def launch(root, command, pid, transaction=None, platform=None):
            helper = Popen([command, str(transaction)])
            self.helpers.append((command, transaction, pid, helper))
            return helper

        s.launch = launch
        import pixelref.builtins.updates as ui_updates

        flag, version = ins.ASSISTED_INSTALL_ENABLED, ui_updates.APP_VERSION
        _undo.append(self.close)
        ui_updates.APP_VERSION = INSTALLED
        ins.ASSISTED_INSTALL_ENABLED = True
        self._saved = (flag, version, ui_updates)

    def close(self):
        flag, version, ui_updates = self._saved
        ins.ASSISTED_INSTALL_ENABLED = flag
        ui_updates.APP_VERSION = version


def test_ui_offers_install_only_when_everything_allows_it():
    h = Harness()
    try:
        env = Env(h)
        s = env.service
        assert s.can_offer_install()
        ins.ASSISTED_INSTALL_ENABLED = False
        assert not s.can_offer_install() and "not enabled" in s.assist()[1]
        assert not s.prepare() and s.prep_state == "idle"
        env.close()
        assert not s.can_offer_install()  # and the real build ships with it off
    finally:
        h.close()


def test_ui_download_verify_then_update_and_restart():
    h = Harness()
    try:
        env = Env(h)
        s = env.service
        assert s.prepare()
        assert wait(lambda: s.prep_state == "ready"), s.prep_error
        tx = s.staged.transaction_id
        assert s.restart_into_update()
        assert [(c, t, p) for c, t, p, _ in env.helpers] == [("activate", tx, os.getpid())]
        assert env.closes == [1] and env.quits == [1] and not env.warnings
        assert ti.tree(env.root)["pixelRef.exe"] == b"old-exe"  # the app itself replaced nothing
        env.close()
    finally:
        h.close()


def test_ui_cancelled_close_stops_the_helper_and_changes_nothing():
    h = Harness()
    try:
        env = Env(h, close=False)  # the user cancels the unsaved-board prompt
        s = env.service
        s.prepare()
        assert wait(lambda: s.prep_state == "ready")
        assert not s.restart_into_update()
        assert env.helpers[0][3].terminated and not env.quits
        assert s.prep_state == "ready"  # still staged: they can try again
        assert ti.tree(env.root)["pixelRef.exe"] == b"old-exe"
        env.close()
    finally:
        h.close()


def test_ui_failures_are_reported_and_leave_nothing_staged():
    h = Harness()
    try:
        env = Env(h)
        s = env.service
        s.trust = ti.TrustStore((ti.TrustedKey(ti.Ed25519PrivateKey.generate().public_key().public_bytes_raw()),))
        s.prepare()
        assert wait(lambda: s.prep_state == "failed")
        assert "signature" in s.prep_error and leftovers(env.root) == []
        assert not s.restart_into_update() and not env.helpers
        s.trust = TRUST
        s.cancel_flag = False
        cancelled = []
        s.opener = lambda url: (_ for _ in ()).throw(OSError("offline"))
        s.prepare()
        assert wait(lambda: s.prep_state == "failed") and "offline" in s.prep_error
        # a failed launch: nothing is closed
        s.opener = env.opener
        s.prepare()
        assert wait(lambda: s.prep_state == "ready")
        s.launch = lambda *a, **k: (_ for _ in ()).throw(ins.InstallError("no helper"))
        assert not s.restart_into_update() and not env.closes and env.warnings
        env.close()
    finally:
        h.close()


def test_ui_cancel_during_download():
    h = Harness()
    try:
        env = Env(h)
        s = env.service
        slow_opener = env.opener

        def opener(url):
            s.cancel_flag = True  # the user pressed Cancel while it was downloading
            return slow_opener(url)

        s.opener = opener
        s.prepare()
        assert wait(lambda: s.prep_state != "preparing")
        assert s.prep_state == "idle" and leftovers(env.root) == []
        env.close()
    finally:
        h.close()


def test_ui_startup_acknowledgment_and_notices():
    h = Harness()
    try:
        env = Env(h)
        s = env.service
        assert s.acknowledge_startup() is None  # no token: an ordinary start
        tx = ti.stage(env.root)
        journal = ti.run(env.root, tx)
        os.environ[flow.STARTUP_TOKEN_ENV] = "0" * 32
        assert s.acknowledge_startup() is False and flow.STARTUP_TOKEN_ENV not in os.environ  # popped either way
        os.environ[flow.STARTUP_TOKEN_ENV] = journal.startup_token
        assert s.acknowledge_startup() is True and flow.STARTUP_TOKEN_ENV not in os.environ
        assert jr.read_journal(st.update_directory(env.root)).state == "COMMITTED"
        toasts = []
        h.app.toast = lambda text, *a, **k: toasts.append(text)
        flow.STARTUP.update(state=rc.NORMAL)
        s.startup_notice()
        s.startup_notice()  # reported once only
        assert len(toasts) == 1 and "1.2.0 is installed" in toasts[0]
        env.close()
    finally:
        h.close()


def test_ui_failed_update_notice_and_manual_recovery_warning():
    h = Harness()
    try:
        env = Env(h)
        s = env.service
        ti.run(env.root, ti.stage(env.root))
        rc.recover(env.root)
        flow.STARTUP.update(state=rc.NORMAL)
        s.startup_notice()
        assert env.warnings and "was restored" in env.warnings[-1][1]
        env.warnings.clear()
        flow.STARTUP.update(state=rc.MANUAL)
        s.startup_notice()
        assert "recover by hand" in env.warnings[-1][1]
        flow.STARTUP.update(state=rc.NORMAL)
        env.close()
    finally:
        h.close()


def test_ui_roll_back():
    h = Harness()
    try:
        root, _ = __import__("test_update_recovery").committed_install()
        env = Env(h, root=root)
        s = env.service
        assert s.roll_back()
        assert env.confirmed and "boards, settings, plugins and scripts are not reverted" in env.confirmed[0]
        command, tx, pid, helper = env.helpers[0]
        assert command == "rollback" and tx is None and env.closes == [1] and env.quits == [1]
        # declined, cancelled close, and nothing to roll back
        env2 = Env(h, root=__import__("test_update_recovery").committed_install()[0], close=False)
        assert not env2.service.roll_back() and env2.helpers[0][3].terminated
        env2.service.confirm = lambda title, text: False
        assert not env2.service.roll_back() and len(env2.helpers) == 1
        env3 = Env(h, root=ti.make_install())
        assert not env3.service.roll_back() and "no previous version" in env3.warnings[-1][1]
        env3.close()
        env2.close()
        env.close()
    finally:
        h.close()


def _dialog():
    from PySide6.QtWidgets import QDialog

    found = [w for w in APP.topLevelWidgets() if isinstance(w, QDialog) and w.isVisible() and w.windowTitle().endswith("Update available")]
    assert len(found) == 1, found
    return found[0]


def _visible_buttons(dialog):
    from PySide6.QtWidgets import QPushButton

    return {b.text().replace("&&", "&"): not b.isHidden() for b in dialog.findChildren(QPushButton)}


def test_ui_dialog_stages():
    h = Harness()
    try:
        env = Env(h)
        s = env.service
        s._finish(env.result)
        s.manual = True
        s._finish(env.result)  # a manual check that finds an update opens the dialog
        dialog = _dialog()
        shown = _visible_buttons(dialog)
        assert shown["Download"] and not shown["Update & Restart"] and not shown["Cancel Download"]
        assert shown["Release Notes & Manual Download"] and shown["Later"]
        notes = dialog.findChildren(__import__("PySide6.QtWidgets", fromlist=["QTextBrowser"]).QTextBrowser)[0]
        assert notes.toPlainText() == "Notes <b>plain</b>"  # release notes are shown as plain text
        s.prepare()
        shown = _visible_buttons(dialog)
        assert shown["Cancel Download"] and not shown["Download"]
        assert wait(lambda: s.prep_state == "ready")
        shown = _visible_buttons(dialog)
        assert shown["Update & Restart"] and not shown["Download"] and not shown["Cancel Download"]
        dialog.close()
        # with installing switched off only the manual path is offered
        ins.ASSISTED_INSTALL_ENABLED = False
        s.prep_state, s.staged = "idle", None
        s.manual = True
        s._finish(env.result)
        dialog = _dialog()
        shown = _visible_buttons(dialog)
        assert not shown["Download"] and not shown["Update & Restart"] and shown["Release Notes & Manual Download"]
        dialog.close()
        APP.processEvents()
        env.close()
    finally:
        h.close()
