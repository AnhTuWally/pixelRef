"""Health checks, crash recovery and manual rollback (M5). Failure injection; no real app, no network."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from contextlib import contextmanager

import test_update_install as ti
from harness import ROOT, tmp_dir
from pixelref.core.updates import install as ins
from pixelref.core.updates import journal as jr
from pixelref.core.updates import recovery as rc
from pixelref.core.updates import staging as st
from pixelref.core.updates.inventory import INVENTORY_NAME, load_inventory

WIN, tree, all_content = ti.WIN, ti.tree, ti.all_content


class Child:
    def __init__(self, exits=None):
        self.code = exits
        self.terminated = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def wait(self, timeout=None):
        return self.code


class AppSim:
    """A launcher that behaves like the new app: acknowledges (or not), or dies at once."""

    def __init__(self, root, behaviour="healthy", token_override=None):
        self.root, self.behaviour, self.token_override = root, behaviour, token_override
        self.launches: list[str] = []
        self.children: list[Child] = []
        self.acked = []

    def __call__(self, root, platform, token):
        self.launches.append(token)
        if token and self.behaviour == "healthy":
            self.acked.append(rc.acknowledge_health(self.root, self.token_override or token))
        child = Child(exits=1 if token and self.behaviour == "crash" else None)
        self.children.append(child)
        return child


def activate_with_health(root, tx, app, timeout=5.0, **kw):
    ticks = [0.0]

    def clock():
        return ticks[0]

    def sleep(seconds):
        ticks[0] += seconds

    return ins.run_activation(root, tx, trust=ti.TRUST, platform=WIN, enabled=True, now=ti.NOW, launcher=app,
                              alive=lambda pid: False, health_timeout=timeout, clock=clock, sleep=sleep, **kw)


def committed_install():
    """An install updated 1.1.0 -> 1.2.0 and acknowledged healthy."""
    root = ti.make_install()
    before = tree(root)
    app = AppSim(root)
    journal = activate_with_health(root, ti.stage(root), app)
    assert journal.state == "COMMITTED"
    return root, before


def test_healthy_start_commits_and_prunes():
    root = ti.make_install()
    old_backup = st.update_directory(root) / "backup" / ("e" * 32)
    old_backup.mkdir(parents=True)
    (old_backup / "x").write_text("old")
    app = AppSim(root)
    journal = activate_with_health(root, ti.stage(root), app)
    assert journal.state == "COMMITTED" and app.acked == [True] and len(app.launches) == 1
    assert st.read_last_sequence(root) == 5  # only now is the metadata sequence recorded
    assert not old_backup.exists()  # older backups are pruned
    assert (st.update_directory(root) / "backup" / journal.transaction_id / "pixelRef.exe").read_bytes() == b"old-exe"
    assert tree(root)["pixelRef.exe"] == b"new-exe"
    assert rc.begin_startup(root) == rc.NORMAL
    assert rc.recover(root).state == "COMMITTED"  # a healthy update is never rolled back
    assert tree(root)["pixelRef.exe"] == b"new-exe"


def test_unhealthy_start_rolls_back_and_relaunches_the_old_version():
    for behaviour in ("silent", "crash"):
        root = ti.make_install()
        tx = ti.stage(root)
        before, content = tree(root), all_content(root)
        app = AppSim(root, behaviour)
        journal = activate_with_health(root, tx, app, timeout=30)
        assert journal.state == "ROLLED_BACK", behaviour
        assert tree(root) == before and all_content(root) == content
        assert app.launches[1:] == [""], "the previous version is started again"
        assert app.children[0].terminated == (behaviour == "silent")
        assert st.read_last_sequence(root) == 0  # a failed update's metadata is not accepted
        assert jr.read_journal(st.update_directory(root)).failure_reason


def test_wrong_token_does_not_commit():
    root = ti.make_install()
    app = AppSim(root, token_override="0" * 32)
    journal = activate_with_health(root, ti.stage(root), app, timeout=3)
    assert app.acked == [False] and journal.state == "ROLLED_BACK"
    assert not rc.acknowledge_health(root, "")
    assert not rc.acknowledge_health(root, "x" * 32)


def test_late_acknowledgment_and_timeout_cannot_both_win():
    root = ti.make_install()
    real = ins.wait_for_health
    app = AppSim(root, "silent")

    def racing(root_, tx, child, timeout, **kw):
        token = jr.read_journal(st.update_directory(root_)).startup_token
        assert rc.acknowledge_health(root_, token)   # the app acknowledges just as the helper gives up
        return False

    ins.wait_for_health = racing
    try:
        journal = activate_with_health(root, ti.stage(root), app)
    finally:
        ins.wait_for_health = real
    assert journal.state == "COMMITTED" and tree(root)["pixelRef.exe"] == b"new-exe"
    assert app.launches == [app.launches[0]]  # no relaunch of the old version


def test_startup_attempts_are_bounded():
    root = ti.make_install()
    assert rc.begin_startup(root) == rc.NORMAL
    assert rc.begin_startup(tmp_dir()) == rc.NORMAL
    app = AppSim(root, "silent")
    ins.run_activation(
        root, ti.stage(root), trust=ti.TRUST, platform=WIN, enabled=True, now=ti.NOW, launcher=app,
        alive=lambda p: False, health_timeout=None)
    assert [rc.begin_startup(root) for _ in range(3)] == [rc.PENDING, rc.PENDING, rc.RECOVER]
    assert rc.recover(root).state == "ROLLED_BACK"
    assert rc.begin_startup(root) == rc.NORMAL
    # interrupted activation and damaged journals are not "carry on"
    update = st.update_directory(root)
    (update / "journal.json").write_text("{broken")
    assert rc.begin_startup(root) == rc.MANUAL


def test_recover_after_power_loss_at_every_point():
    probe = ti.make_install()
    points: list[str] = []
    ti.run(probe, ti.stage(probe), fault=points.append)
    for point in points:
        root = ti.make_install()
        tx = ti.stage(root)
        before, content = tree(root), all_content(root)
        try:
            ti.run(root, tx, fault=lambda p, point=point: (_ for _ in ()).throw(ti.PowerLoss(p)) if p == point else None)
        except ti.PowerLoss:
            pass
        assert rc.begin_startup(root) in (rc.RECOVER, rc.PENDING), point
        # recovery itself is interrupted once, then run again
        try:
            rc.recover(root, fault=lambda p: (_ for _ in ()).throw(ti.PowerLoss(p)) if p.startswith("rollback:") else None)
        except ti.PowerLoss:
            pass
        journal = rc.recover(root)
        assert journal.state == "ROLLED_BACK", point
        assert tree(root) == before and all_content(root) == content, point
        assert rc.recover(root).state == "ROLLED_BACK"  # idempotent


def test_recover_waits_for_the_app_and_a_damaged_journal_needs_a_person():
    root = ti.make_install()
    ti.run(root, ti.stage(root))  # pending
    before = tree(root)
    try:
        rc.recover(root, pids=[4242], alive=lambda p: True, wait_timeout=0.2)
    except ins.InstallError as exc:
        assert "still running" in str(exc)
    else:
        raise AssertionError
    assert tree(root) == before
    (st.update_directory(root) / "journal.json").write_text("{nope")
    try:
        rc.recover(root)
    except ins.InstallError as exc:
        assert "needs a person" in str(exc)
    else:
        raise AssertionError
    assert tree(root) == before


@contextmanager
def failing_renames(should_fail):
    """Windows-style file locks and vanishing drives, injected where the helper renames."""
    real = os.rename

    def rename(src, dst, *a, **k):
        error = should_fail(os.fspath(src), os.fspath(dst))
        if error:
            raise error
        return real(src, dst, *a, **k)

    os.rename = rename
    try:
        yield
    finally:
        os.rename = real


def locked(name):
    return lambda src, dst: PermissionError(13, "The process cannot access the file because it is being used by another process") \
        if os.path.basename(src) == name else None


def test_a_locked_file_during_activation_is_undone():
    for name in ("_internal", "pixelRef.exe", "pixelref-cli.exe"):  # another pixelRef holds it open
        root = ti.make_install()
        tx = ti.stage(root)
        before, content = tree(root), all_content(root)
        with failing_renames(lambda s, d, name=name: locked(name)(s, d) if "backup" in d or "extracted" in s else None):
            try:
                ti.run(root, tx)
            except ins.InstallError as exc:
                assert "undone" in str(exc)
            else:
                raise AssertionError(name)
        assert tree(root) == before and all_content(root) == content
        assert jr.read_journal(st.update_directory(root)).state == "ROLLED_BACK"


def test_failed_rollback_keeps_everything_and_is_retried_then_needs_a_person():
    root = ti.make_install()
    tx = ti.stage(root)
    content = all_content(root)
    before = tree(root)
    ti.run(root, tx)  # PENDING; the new app then refuses to start
    update = st.update_directory(root)
    with failing_renames(lambda s, d: PermissionError(13, "locked") if os.path.basename(s) == "_internal" else None):
        for attempt in range(1, rc.MAX_ROLLBACK_ATTEMPTS):
            try:
                rc.recover(root)
            except ins.InstallError:
                pass
            else:
                raise AssertionError
            journal = jr.read_journal(update)
            assert journal.state == "ROLLBACK_FAILED" and all_content(root) == content, "recovery files must be kept"
    # the lock is released: the next attempt succeeds
    assert rc.recover(root).state == "ROLLED_BACK" and tree(root) == before and all_content(root) == content

    root = ti.make_install()
    tx = ti.stage(root)
    content = all_content(root)
    ti.run(root, tx)
    with failing_renames(lambda s, d: OSError(errno.ENODEV, "No such device") if os.path.basename(s) == "_internal" else None):
        outcomes = []
        for _ in range(rc.MAX_ROLLBACK_ATTEMPTS + 2):
            try:
                rc.recover(root)
            except ins.InstallError as exc:
                outcomes.append(str(exc))
        journal = jr.read_journal(update := st.update_directory(root))
        assert journal.state == "RECOVERY_REQUIRED", journal.state       # bounded: no endless retries
        assert any("manual recovery is required" in o for o in outcomes)
        assert all_content(root) == content
        assert rc.begin_startup(root) == rc.MANUAL
    try:
        rc.recover(root)
    except ins.InstallError as exc:
        assert "cannot be done safely" in str(exc)
    else:
        raise AssertionError("RECOVERY_REQUIRED must stay until a person acts")


def test_drive_disappearing_mid_update_loses_nothing():
    root = ti.make_install()
    tx = ti.stage(root)
    content = all_content(root)
    before = tree(root)
    count = [0]

    def vanish(src, dst):
        count[0] += 1
        return OSError(errno.ENODEV, "device removed") if count[0] > 3 else None

    with failing_renames(vanish):
        try:
            ti.run(root, tx)
        except ins.InstallError as exc:
            assert "could not be undone" in str(exc) and str(st.update_directory(root)) in str(exc)
    assert all_content(root) == content          # nothing lost while the drive was gone
    assert jr.read_journal(st.update_directory(root)).state == "ROLLBACK_FAILED"
    assert rc.recover(root).state == "ROLLED_BACK"  # drive plugged back in
    assert tree(root) == before


def test_manual_rollback_restores_the_previous_release_and_can_roll_forward():
    root, original = committed_install()
    updated = tree(root)
    app = AppSim(root)
    journal = rc.rollback_previous(root, platform=WIN, enabled=True, alive=lambda p: False, launcher=app)
    assert journal.state == "COMMITTED" and journal.kind == "rollback" and journal.new_version == "1.1.0"
    assert tree(root) == original, "the previous release comes back exactly, user files untouched"
    assert str(load_inventory(root).version) == "1.1.0" and app.launches == [""]
    for name, data in ti.USER.items():
        assert tree(root)[name] == data
    again = rc.rollback_previous(root, platform=WIN, enabled=True, alive=lambda p: False, launcher=app)
    assert again.new_version == "1.2.0" and tree(root) == updated  # roll forward
    assert rc.begin_startup(root) == rc.NORMAL


def test_manual_rollback_blocks_when_unsafe_and_is_retryable_after_failure():
    kw = dict(platform=WIN, enabled=True, alive=lambda p: False, launcher=AppSim(None))

    def blocked(root, text):
        before = tree(root)
        try:
            rc.rollback_previous(root, **kw)
        except ins.InstallError as exc:
            assert text in str(exc), str(exc)
        else:
            raise AssertionError(text)
        assert tree(root) == before

    root, _ = committed_install()
    backup = next((st.update_directory(root) / "backup").iterdir())
    (backup / "_internal" / "base.dll").write_bytes(b"tampered")
    blocked(root, "damaged")
    root, _ = committed_install()
    import shutil
    shutil.rmtree(next((st.update_directory(root) / "backup").iterdir()))
    blocked(root, "backup is missing")
    root, _ = committed_install()
    (root / "_internal" / "mine.txt").write_bytes(b"added since the update")
    blocked(root, "unknown file")
    root = ti.make_install()
    blocked(root, "no committed update")
    root, _ = committed_install()
    try:
        rc.rollback_previous(root, platform=WIN, alive=lambda p: False, launcher=kw["launcher"])
    except ins.InstallError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError

    # A rollback that fails part-way is undone, and the same rollback can then be run again.
    root, original = committed_install()
    updated = tree(root)
    probe, _ = committed_install()
    points: list[str] = []
    rc.rollback_previous(probe, fault=points.append, **kw)
    assert len(points) >= 10
    for point in points:
        root, _ = committed_install()
        updated, content = tree(root), all_content(root)
        try:
            rc.rollback_previous(root, fault=lambda p, point=point: (_ for _ in ()).throw(ti.Fault(p)) if p == point else None, **kw)
        except ins.InstallError as exc:
            assert "undone" in str(exc), point
        assert tree(root) == updated and all_content(root) == content, point
        assert rc.rollback_previous(root, **kw).new_version == "1.1.0", point


def test_journal_race_guard_and_stale_lock():
    update = tmp_dir()
    with jr.journal_guard(update):
        try:
            with jr.journal_guard(update, timeout=0.1):
                raise AssertionError("second holder must wait")
        except jr.JournalError as exc:
            assert "busy" in str(exc)
    assert not (update / "journal.lock").exists()
    (update / "journal.lock").write_text("x")
    os.utime(update / "journal.lock", (0, 0))  # a crashed holder's lock is stale
    with jr.journal_guard(update):
        pass


def test_helper_cli_recover_runs_while_install_is_disabled():
    root = ti.make_install()
    tx = "a" * 32
    helper_dir = st.update_directory(root) / "helper" / tx
    helper_dir.mkdir(parents=True)
    helper = helper_dir / "helper_main.py"
    helper.write_text((ROOT / "packaging" / "updater" / "helper_main.py").read_text())
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, str(helper), "recover"], capture_output=True, text=True, env=env)
    assert done.returncode == 0 and json.loads(done.stdout)["state"] == "IDLE", done.stdout + done.stderr
    for command in ("rollback", "activate " + tx):
        done = subprocess.run([sys.executable, str(helper), *command.split()], capture_output=True, text=True, env=env)
        assert done.returncode == 3, command


def test_replace_waits_for_a_reader_to_let_go_of_the_file():
    """Windows refuses to replace a file another process has open (the helper polls the journal while the new app
    commits to it). The replace is retried briefly, and a lasting refusal still raises."""
    real, calls = os.replace, []

    def flaky(source, destination):
        calls.append(1)
        if len(calls) < 4:
            raise PermissionError(5, "Access is denied")
        real(source, destination)

    def always(source, destination):
        raise PermissionError(5, "Access is denied")

    saved = jr.RETRY_REPLACE, os.replace
    jr.RETRY_REPLACE = True
    try:
        root = tmp_dir()
        source, destination = root / "a.tmp", root / "a"
        source.write_text("new")
        os.replace = flaky
        jr.replace_file(source, destination, delay=0)
        os.replace = real
        assert destination.read_text() == "new" and len(calls) == 4
        os.replace = always
        try:
            jr.replace_file(root / "b.tmp", root / "b", attempts=3, delay=0)
            raise AssertionError("a lasting refusal must raise")
        except PermissionError:
            pass
    finally:
        jr.RETRY_REPLACE, os.replace = saved
