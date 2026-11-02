"""Activation of a staged update (M4): ownership, journaling, fault injection. Offline, no real app."""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from harness import ROOT, tmp_dir
from pixelref.core.updates import Platform, Version
from pixelref.core.updates import install as ins
from pixelref.core.updates import journal as jr
from pixelref.core.updates import staging as st
from pixelref.core.updates.inventory import INVENTORY_NAME, InventoryError, build_inventory, load_inventory, write_inventory
from pixelref.core.updates.verification import TrustedKey, TrustStore, parse_manifest, sign_manifest, verify_release

WIN = Platform("windows", "x64")
NOW = dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc)
KEY = Ed25519PrivateKey.generate()
TRUST = TrustStore((TrustedKey(KEY.public_key().public_bytes_raw()),))
URL = "https://github.com/AnhTuWally/pixelRef/releases/download/v1.2.0/pixelRef-v1.2.0-windows-x64.zip"

OLD = {"pixelRef.exe": b"old-exe", "pixelref-cli.exe": b"old-cli", "_internal/base.dll": b"old-base",
       "_internal/old.dll": b"only-in-old", "extras.txt": b"owned single file"}
NEW = {"pixelRef.exe": b"new-exe", "pixelref-cli.exe": b"new-cli", "_internal/base.dll": b"new-base",
       "_internal/new.dll": b"only-in-new", "extras.txt": b"new extras", "newtop/file.txt": b"brand new folder"}
USER = {"portable.toml": b"[portable]\nenabled = true\n", "user-data/settings.ini": b"mine",
        "user-data/boards/a.pxref": b"board", "scripts/my.py": b"print('mine')"}


class PowerLoss(BaseException):
    """Process dies mid-update: nothing may catch it."""


class Fault(Exception):
    pass


def write_tree(folder: Path, files: dict) -> None:
    for name, data in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def tree(folder: Path, skip_update=True) -> dict:
    out = {}
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder).as_posix()
        if skip_update and rel.split("/")[0] == ".pixelref-update":
            continue
        out[rel] = None if p.is_dir() else p.read_bytes()
    return out


def all_content(root: Path) -> list[bytes]:
    """Every file's bytes anywhere under the install, minus bookkeeping: nothing may ever disappear."""
    skip = {"journal.json", "installation-id", "lock", "manifest.json", "STAGED", "package.zip"}
    return sorted(p.read_bytes() for p in root.rglob("*") if p.is_file() and p.name not in skip
                  and not p.name.endswith(".tmp"))


def make_install(old=OLD, user=USER, version="1.1.0") -> Path:
    root = tmp_dir()
    write_tree(root, old)
    write_tree(root, user)
    inv = write_inventory_for(root, old, version)
    (root / ".pixelref-update").mkdir()
    return root


def write_inventory_for(root: Path, files: dict, version: str):
    # Build the inventory from only the release's own files (the user's files sit beside them).
    folder = tmp_dir()
    write_tree(folder, files)
    data = (folder / INVENTORY_NAME)
    write_inventory(folder, version)
    (root / INVENTORY_NAME).write_bytes(data.read_bytes())


def build_zip(files: dict, version="1.2.0") -> bytes:
    folder = tmp_dir()
    write_tree(folder, files)
    write_inventory(folder, version)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                z.writestr("pixelRef/" + p.relative_to(folder).as_posix(), p.read_bytes())
    return buffer.getvalue()


def manifest_bytes(data: bytes, key=KEY, inventory=True, **changes) -> bytes:
    payload = {"schema_version": 1, "release_id": "v1.2.0", "sequence": 5, "version": "1.2.0", "channel": "stable",
               "platform": "windows", "architecture": "x64", "artifact_name": "pixelRef-v1.2.0-windows-x64.zip",
               "artifact_size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "package_format": "zip",
               "published_at": "2026-10-31T00:00:00Z", "expires_at": "2027-01-31T00:00:00Z",
               "min_updater_version": "1.0.0"}
    if inventory:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            payload["files"] = [{"path": i.filename, "size": i.file_size, "sha256": hashlib.sha256(z.read(i)).hexdigest()}
                                for i in z.infolist() if not i.is_dir()]
    payload.update(changes)
    return json.dumps(sign_manifest(payload, key)).encode()


def stage(root: Path, new=NEW, **kw) -> str:
    data = build_zip(new)
    raw = manifest_bytes(data, **kw)
    manifest = verify_release(raw, trust=TRUST, installed=Version(1, 1, 0), platform=WIN, now=NOW)
    staged = st.stage_update(manifest, raw, URL, st.staging_root(root), platform=WIN,
                             opener=lambda url: (io.BytesIO(data), URL))
    return staged.transaction_id


class Launcher:
    def __init__(self):
        self.calls = []

    def __call__(self, root, platform, token):
        self.calls.append((root, token))


def run(root, tx, **kw):
    args = dict(trust=TRUST, platform=WIN, enabled=True, now=NOW, launcher=Launcher(), alive=lambda pid: False,
                health_timeout=None)  # None: return after launching (the health protocol is tested separately)
    args.update(kw)
    return ins.run_activation(root, tx, **args)


def blocked(root, tx, text="", **kw):
    before = tree(root)
    try:
        run(root, tx, **kw)
    except (ins.InstallError, jr.JournalError) as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError("expected the update to be blocked")
    assert tree(root) == before, "a blocked update changed the installation"
    assert jr.read_journal(st.update_directory(root)) is None


def test_disabled_by_default_and_helper_refuses():
    root = make_install()
    tx = stage(root)
    assert ins.ASSISTED_INSTALL_ENABLED is False
    try:
        ins.run_activation(root, tx, trust=TRUST, platform=WIN, now=NOW)
    except ins.InstallError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError
    helper = ROOT / "packaging" / "updater" / "helper_main.py"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, str(helper), "activate", tx], capture_output=True, text=True, env=env)
    assert done.returncode == 3 and "disabled" in done.stdout
    for bad in (["activate"], ["activate", tx, "--wait-pid", "x"], ["run", tx], ["activate", tx, "--root", "/"]):
        done = subprocess.run([sys.executable, str(helper), *bad], capture_output=True, text=True, env=env)
        assert done.returncode == 2, bad


def test_successful_activation():
    root = make_install()
    tx = stage(root)
    before = tree(root)
    launcher = Launcher()
    journal = run(root, tx, launcher=launcher)
    assert journal.state == "PENDING_HEALTH_CHECK" and journal.old_version == "1.1.0" and journal.new_version == "1.2.0"
    assert launcher.calls == [(root, journal.startup_token)] and len(journal.startup_token) == 32
    now = tree(root)
    for name, data in NEW.items():
        assert now[name] == data, name
    assert INVENTORY_NAME in now and "_internal/old.dll" not in now
    for name, data in USER.items():  # the user's files are exactly as they were
        assert now[name] == data
    backup = st.update_directory(root) / "backup" / tx
    assert (backup / "_internal" / "old.dll").read_bytes() == b"only-in-old"
    assert (backup / "pixelRef.exe").read_bytes() == b"old-exe"
    assert jr.read_journal(st.update_directory(root)).state == "PENDING_HEALTH_CHECK"
    assert not (st.update_directory(root) / "lock").exists()
    assert before["scripts/my.py"] == now["scripts/my.py"]


def test_every_failure_point_is_undone_exactly():
    probe = make_install()
    points: list[str] = []
    run(probe, stage(probe), fault=points.append)
    assert len(points) >= 12 and "journal:activating" in points and "newtop:before_place" in points
    for point in points:
        root = make_install()
        tx = stage(root)
        before, content = tree(root), all_content(root)

        def fault(p, point=point):
            if p == point:
                raise Fault(p)

        try:
            run(root, tx, fault=fault)
        except ins.InstallError as exc:
            assert "undone" in str(exc), (point, str(exc))
        else:
            if point == "journal:pending_health_check":
                raise AssertionError("fault after the last step must also fail")
            raise AssertionError(point)
        assert tree(root) == before, f"installation differs after failing at {point}"
        assert all_content(root) == content, f"a file was lost failing at {point}"
        assert jr.read_journal(st.update_directory(root)).state == "ROLLED_BACK", point
        st.validate_staged(st.staging_root(root) / tx, parse_manifest(json.loads(
            (st.staging_root(root) / tx / "signed-metadata" / "manifest.json").read_text())["signed"]), WIN)


def test_power_loss_at_every_point_loses_nothing_and_can_be_rolled_back_repeatedly():
    probe = make_install()
    points: list[str] = []
    run(probe, stage(probe), fault=points.append)
    for point in points:
        root = make_install()
        tx = stage(root)
        before, content = tree(root), all_content(root)

        def fault(p, point=point):
            if p == point:
                raise PowerLoss(p)

        try:
            run(root, tx, fault=fault)
        except PowerLoss:
            pass
        assert all_content(root) == content, f"file lost at {point}"
        journal = jr.read_journal(st.update_directory(root))
        assert journal.state in ("ACTIVATING", "PENDING_HEALTH_CHECK"), point
        # Recovery by inspecting the real filesystem, interrupted once and then repeated.
        if journal.operations:
            try:
                ins.rollback(root, journal, fault=lambda p: (_ for _ in ()).throw(PowerLoss(p)) if p.startswith("rollback:") else None)
            except PowerLoss:
                pass
            journal = jr.read_journal(st.update_directory(root))
        ins.rollback(root, journal)
        assert tree(root) == before, f"not restored after power loss at {point}"
        assert all_content(root) == content


def test_ownership_conflicts_block_without_changes():
    cases = {
        "unknown file in owned folder": (lambda r: (r / "_internal" / "mine.txt").write_bytes(b"x"), "unknown file _internal/mine.txt"),
        "modified owned file": (lambda r: (r / "_internal" / "base.dll").write_bytes(b"patched!"), "modified installed file"),
        "missing owned file": (lambda r: (r / "pixelref-cli.exe").unlink(), "missing installed file"),
        "unknown file where release adds": (lambda r: ((r / "newtop").mkdir(), (r / "newtop" / "mine").write_bytes(b"x")), "overwrite unknown newtop"),
    }
    for label, (mutate, text) in cases.items():
        root = make_install()
        mutate(root)
        blocked(root, stage(root), text)
    if os.name == "posix":
        root = make_install()
        (root / "_internal" / "link").symlink_to(root / "user-data")
        blocked(root, stage(root), "link")


def test_source_checkouts_and_missing_inventory_are_never_updated():
    root = make_install()
    tx = stage(root)
    (root / ".git").mkdir()
    blocked(root, tx, "source checkout")
    root = make_install()
    tx = stage(root)
    (root / "launch_pixelRef.py").write_text("x")
    blocked(root, tx, "source checkout")
    root = make_install()
    tx = stage(root)
    (root / INVENTORY_NAME).unlink()
    blocked(root, tx, "inventory")


def test_authenticity_is_rechecked_by_the_helper():
    root = make_install()
    tx = stage(root)
    blocked(root, tx, "cannot be installed", trust=TrustStore((TrustedKey(Ed25519PrivateKey.generate().public_key().public_bytes_raw()),)))
    blocked(root, tx, "cannot be installed", trust=TrustStore())
    blocked(root, tx, "expired", now=dt.datetime(2027, 6, 1, tzinfo=dt.timezone.utc))
    root = make_install()
    tx = stage(root)
    staged = st.staging_root(root) / tx / "extracted" / "pixelRef" / "_internal" / "new.dll"
    staged.write_bytes(b"EVIL-DLL!!")
    blocked(root, tx, "changed")
    root = make_install()
    tx = stage(root, inventory=False)
    blocked(root, tx, "inventory")
    root = make_install()
    st.record_sequence(root, 9)  # replay of older metadata
    blocked(root, stage(root), "replay")


def test_transaction_id_cannot_redirect_the_update():
    root = make_install()
    stage(root)
    for bad in ("../x", "", "A" * 32, "a" * 32 + "/..", "..", str(tmp_dir())):
        blocked(root, bad, "invalid transaction id")
    blocked(root, "f" * 32, "cannot be installed")  # unknown transaction


def test_lock_and_waiting_for_the_app():
    root = make_install()
    tx = stage(root)
    update = st.update_directory(root)
    with ins.InstallLock(update, alive=lambda pid: True):
        blocked(root, tx, "already in progress", alive=lambda pid: True)
    (update / "lock").write_text(json.dumps({"pid": 99999999}))
    with ins.InstallLock(update, alive=lambda pid: False):  # a dead owner's lock is replaced
        pass
    (update / "lock").write_text("garbage")
    with ins.InstallLock(update, alive=lambda pid: False):
        pass
    blocked(root, tx, "did not close", pids=[4242], alive=lambda pid: True, wait_timeout=0.2)
    assert ins.wait_for_exit([os.getpid()], 0.1)  # never waits on itself
    run(root, tx, pids=[4242], alive=lambda pid: False)


def test_unfinished_earlier_transaction_blocks_a_new_one():
    root = make_install()
    run(root, stage(root))  # leaves PENDING_HEALTH_CHECK
    try:
        run(root, stage(root, sequence=6))
    except ins.InstallError as exc:
        assert "unfinished" in str(exc) or "cannot be installed" in str(exc) or "blocked" in str(exc)
    else:
        raise AssertionError


def test_journal_is_strict_and_cannot_name_paths():
    update = make_install() / ".pixelref-update"
    good = jr.Journal("a" * 32, "b" * 32, "1.1.0", "1.2.0", "c" * 64,
                      operations=[{"name": "_internal", "action": "replace", "status": "pending"}])
    jr.write_journal(update, good)
    assert jr.read_journal(update).operations[0]["name"] == "_internal"
    base = good.to_dict()
    forged = [
        {**base, "operations": [{"name": "../../etc", "action": "remove", "status": "pending"}]},
        {**base, "operations": [{"name": "a/b", "action": "remove", "status": "pending"}]},
        {**base, "operations": [{"name": "C:\\x", "action": "remove", "status": "pending"}]},
        {**base, "operations": [{"name": "..", "action": "remove", "status": "pending"}]},
        {**base, "operations": [{"name": "x", "action": "rm -rf", "status": "pending"}]},
        {**base, "operations": [{"name": "x", "action": "add", "status": "pending"}] * 2},
        {**base, "backup_directory": "/"}, {**base, "state": "HACKED"}, {**base, "transaction_id": "../x"},
        {**base, "manifest_digest": "x"}, {**base, "startup_attempts": -1}, {**base, "old_version": "latest"},
        [], "x",
    ]
    for data in forged:
        try:
            jr.validate(data)
        except jr.JournalError:
            continue
        raise AssertionError(data)
    (update / "journal.json").write_text("{not json")
    try:
        jr.read_journal(update)
    except jr.JournalError:
        pass
    else:
        raise AssertionError


def test_state_machine_transitions():
    j = jr.Journal("a" * 32, "b" * 32, "1.1.0", "1.2.0", "c" * 64)
    for illegal in ("COMMITTED", "ROLLED_BACK", "PENDING_HEALTH_CHECK", "IDLE"):
        try:
            j.transition(illegal)
        except jr.JournalError:
            continue
        raise AssertionError(illegal)
    j.transition("ACTIVATING")
    j.transition("PENDING_HEALTH_CHECK")
    j.transition("COMMITTED")
    try:
        j.transition("ROLLING_BACK")
    except jr.JournalError:
        pass
    else:
        raise AssertionError("COMMITTED is final")
    pre = jr.Journal("a" * 32, "b" * 32, "1.1.0", "1.2.0", "c" * 64)
    pre.transition("CANCELLED")  # before activation: no live change
    assert pre.history[-1]["state"] == "CANCELLED"


def test_helper_root_is_derived_from_its_own_location():
    root = tmp_dir()
    tx = "a" * 32
    helper = root / ".pixelref-update" / "helper" / tx
    helper.mkdir(parents=True)
    assert ins.derive_install_root(helper / "helper.exe") == root.resolve()
    for elsewhere in (root / "helper.exe", root / ".pixelref-update" / "helper.exe",
                      root / ".pixelref-update" / "helper" / "notanid" / "h.exe"):
        try:
            ins.derive_install_root(elsewhere)
        except ins.InstallError:
            continue
        raise AssertionError(elsewhere)
    bundle = tmp_dir()
    (bundle / "helper.exe").write_bytes(b"h")
    target = ins.prepare_helper(root, "b" * 32, bundle)
    assert (target / "helper.exe").exists() and target.parent == helper.parent


def test_inventory_round_trip_and_exclusions():
    root = make_install()
    inventory = load_inventory(root)
    assert str(inventory.version) == "1.1.0" and set(inventory.files) == set(OLD)
    assert not any(p.startswith(("user-data", "scripts")) or p == "portable.toml" for p in inventory.files)
    folder = tmp_dir()
    write_tree(folder, {**OLD, "user-data/x": b"1", "portable.toml": b"", ".pixelref-update/j": b"1"})
    assert {f["path"] for f in build_inventory(folder, "1.1.0")["files"]} == set(OLD)
    (root / INVENTORY_NAME).write_text(json.dumps({"schema_version": 1, "version": "1.1.0", "files": [
        {"path": "../escape", "size": 1, "sha256": "a" * 64}]}))
    try:
        load_inventory(root)
    except InventoryError:
        pass
    else:
        raise AssertionError


def test_a_failed_activation_relaunches_the_version_the_user_had():
    """The app was closed for the update; when the update is undone the user must not be left with nothing."""
    root = make_install()
    tx = stage(root)
    launcher = Launcher()

    def fault(point):
        if point == "newtop:before_place":
            raise Fault(point)

    try:
        run(root, tx, fault=fault, launcher=launcher)
        raise AssertionError("the activation should have failed")
    except ins.InstallError:
        pass
    assert launcher.calls == [(root, "")], launcher.calls  # relaunched normally: no startup token
