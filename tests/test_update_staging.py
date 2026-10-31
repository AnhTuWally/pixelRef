"""Verified download and safe staging (M3). Offline; the live installation is never touched."""

from __future__ import annotations

import hashlib
import io
import os
import stat
import urllib.error
import zipfile
from pathlib import Path

from harness import tmp_dir
from pixelref.core.updates import Platform
from pixelref.core.updates import staging as st
from pixelref.core.updates.verification import VerificationError, parse_manifest

WIN = Platform("windows", "x64")
URL = "https://github.com/AnhTuWally/pixelRef/releases/download/v1.2.0/pixelRef-v1.2.0-windows-x64.zip"
GOOD = {"pixelRef/pixelRef.exe": b"exe", "pixelRef/pixelref-cli.exe": b"cli", "pixelRef/_internal/base.dll": b"dll"}


def make_zip(entries: dict | None = None, extra=()) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in (GOOD if entries is None else entries).items():
            z.writestr(name, data)
        for info, data in extra:
            z.writestr(info, data)
    return buffer.getvalue()


def manifest_for(data: bytes, inventory: bool = False):
    payload = {"schema_version": 1, "release_id": "v1.2.0", "sequence": 5, "version": "1.2.0", "channel": "stable",
               "platform": "windows", "architecture": "x64", "artifact_name": "pixelRef-v1.2.0-windows-x64.zip",
               "artifact_size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "package_format": "zip",
               "published_at": "2026-10-31T00:00:00Z", "expires_at": "2027-01-31T00:00:00Z",
               "min_updater_version": "1.0.0"}
    if inventory:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            payload["files"] = [{"path": i.filename, "size": i.file_size, "sha256": hashlib.sha256(z.read(i)).hexdigest()}
                                for i in z.infolist() if not i.is_dir()]
    return parse_manifest(payload)


class Stream(io.BytesIO):
    """Fails like a dropped connection after ``fail_at`` bytes."""

    def __init__(self, data, fail_at=None):
        super().__init__(data)
        self.fail_at = fail_at

    def read(self, n=-1):
        if self.fail_at is not None and self.tell() >= self.fail_at:
            raise ConnectionResetError("connection dropped")
        return super().read(min(n, 7) if n and n > 0 else n)


def opener_for(data, fail_at=None, final=URL):
    return lambda url: (Stream(data, fail_at), final)


def snapshot(folder: Path) -> dict:
    return {str(p.relative_to(folder)): (p.read_bytes() if p.is_file() else None) for p in sorted(folder.rglob("*"))}


def live_install() -> Path:
    install = tmp_dir()
    (install / "pixelRef.exe").write_bytes(b"old")
    (install / "_internal").mkdir()
    (install / "_internal" / "old.dll").write_bytes(b"old-dll")
    (install / "user-data").mkdir()
    (install / "user-data" / "settings.ini").write_text("mine")
    return install


def stage(data, install=None, inventory=False, **kw):
    install = install or live_install()
    root = st.staging_root(install)
    manifest = manifest_for(data, inventory)
    kw.setdefault("opener", opener_for(data))
    return stage_failure_or(lambda: st.stage_update(manifest, b"{}", URL, root, platform=WIN, **kw)), install, root


def stage_failure_or(fn):
    try:
        return fn()
    except st.StagingError as exc:
        return exc
    except VerificationError as exc:
        return exc


def assert_rejected(result, install, root, text=""):
    assert isinstance(result, (st.StagingError, VerificationError)), result
    assert text in str(result), str(result)
    assert not root.exists() or list(root.iterdir()) == [], list(root.iterdir())  # transaction removed


def test_good_update_is_staged_and_live_install_untouched():
    install = live_install()
    before = snapshot(install)
    seen = []
    result, _, root = stage(make_zip(), install, inventory=True, progress=lambda a, b: seen.append((a, b)))
    assert isinstance(result, st.StagedUpdate)
    assert (result.application / "pixelRef.exe").read_bytes() == b"exe"
    assert (result.application / "_internal" / "base.dll").read_bytes() == b"dll"
    assert seen and seen[-1][0] == seen[-1][1]
    assert {k: v for k, v in snapshot(install).items() if not k.startswith(".pixelref-update")} == before
    st.validate_staged(result.directory, result.manifest, WIN)
    assert (result.directory / "signed-metadata" / "manifest.json").exists()


def test_interrupted_download_at_many_offsets_leaves_nothing():
    data = make_zip()
    for offset in (0, 1, 7, len(data) // 2, len(data) - 1):
        install = live_install()
        before = snapshot(install)
        result, _, root = stage(data, install, opener=opener_for(data, fail_at=offset))
        assert_rejected(result, install, root, "download failed")
        assert snapshot(install) == {**before, **{k: v for k, v in snapshot(install).items() if k.startswith(".pixelref-update")}}


def test_cancel_and_progress():
    data = make_zip()
    calls = []
    result, install, root = stage(data, cancelled=lambda: len(calls) > 2 or calls.append(1) or False)
    assert isinstance(result, st.Cancelled)
    assert_rejected(result, install, root)
    result, install, root = stage(data, cancelled=lambda: True)
    assert isinstance(result, st.Cancelled) and not list(root.iterdir())


def test_corrupt_wrong_size_and_oversized_downloads_rejected():
    data = make_zip()
    corrupt = bytearray(data)
    corrupt[10] ^= 0xFF
    result, install, root = stage(data, opener=opener_for(bytes(corrupt)))
    assert_rejected(result, install, root, "SHA-256")
    result, install, root = stage(data, opener=opener_for(data[:-3]))
    assert_rejected(result, install, root, "size")
    result, install, root = stage(data, opener=opener_for(data + b"x" * 50))
    assert_rejected(result, install, root, "larger than the signed size")
    result, install, root = stage(data, opener=opener_for(b"not a zip at all"))
    assert_rejected(result, install, root)


def test_download_urls_are_allowlisted_including_redirects():
    data = make_zip()
    for bad in ("http://github.com/a.zip", "https://evil.example/a.zip", "https://user:pw@github.com/a.zip",
                "file:///etc/passwd", "https://github.com.evil.example/a.zip"):
        try:
            st.check_download_url(bad)
        except st.StagingError:
            continue
        raise AssertionError(bad)
    st.check_download_url("https://objects.githubusercontent.com/x")
    result, install, root = stage(data, opener=opener_for(data, final="https://evil.example/a.zip"))
    assert_rejected(result, install, root, "not allowed")
    handler = st._CheckedRedirects()
    try:
        handler.redirect_request(urllib.request.Request(URL), None, 302, "Found", {}, "http://evil.example/x")
    except urllib.error.URLError as exc:
        assert "redirect refused" in str(exc)
    else:
        raise AssertionError("redirect not refused")


import urllib.request  # noqa: E402


def test_malicious_archives_are_rejected():
    def link(name, target):
        info = zipfile.ZipInfo(name)
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        return info, target

    cases = {
        "traversal": ({**GOOD, "pixelRef/../evil.txt": b"x"}, (), "unsafe"),
        "absolute": ({**GOOD, "/etc/passwd": b"x"}, (), "unsafe"),
        "backslash": ({**GOOD, "pixelRef\\..\\evil": b"x"}, (), "unsafe"),
        "drive": ({**GOOD, "C:/evil.txt": b"x"}, (), "unsafe"),
        "unc": ({**GOOD, "//server/share/x": b"x"}, (), "unsafe"),
        "outside top level": ({**GOOD, "other/file": b"x"}, (), "outside"),
        "reserved name": ({**GOOD, "pixelRef/nul.txt": b"x"}, (), "unsafe"),
        "user data": ({**GOOD, "pixelRef/user-data/settings.ini": b"x"}, (), "protected"),
        "portable.toml": ({**GOOD, "pixelRef/Portable.TOML": b"x"}, (), "protected"),
        "updater dir": ({**GOOD, "pixelRef/.pixelref-update/journal.json": b"x"}, (), "protected"),
        "case collision": ({**GOOD, "pixelRef/PIXELREF.EXE": b"x"}, (), "case-colliding"),
        "file and folder": ({**GOOD, "pixelRef/_internal": b"x"}, (), "share a path"),
        "symlink": (GOOD, [link("pixelRef/_internal/evil", "/etc")], "links"),
        "empty": ({}, (), "empty"),
    }
    for label, (entries, extra, text) in cases.items():
        try:
            data = make_zip(entries, extra)
        except Exception:  # noqa: BLE001  (zipfile refuses some names itself)
            continue
        result, install, root = stage(data)
        assert_rejected(result, install, root, text)
    assert not (Path(st.staging_root(live_install())).parent.parent / "evil.txt").exists()


def test_duplicate_entries_rejected():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for name, data in GOOD.items():
            z.writestr(name, data)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            z.writestr("pixelRef/pixelRef.exe", b"second")
    result, install, root = stage(buffer.getvalue())
    assert_rejected(result, install, root, "case-colliding")


def test_limits_and_layout():
    data = make_zip()
    manifest = manifest_for(data)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for kwargs in ({"max_entries": 2}, {"max_bytes": 5}):
            try:
                st.inspect_archive(z, manifest, **kwargs)
            except st.StagingError:
                continue
            raise AssertionError(kwargs)
    partial = make_zip({"pixelRef/pixelRef.exe": b"exe", "pixelRef/_internal/base.dll": b"dll"})
    result, install, root = stage(partial)
    assert_rejected(result, install, root, "missing pixelref-cli.exe")


def test_inventory_must_match_and_hashes_are_checked():
    data = make_zip()
    manifest = manifest_for(data, inventory=True)
    other = make_zip({**GOOD, "pixelRef/extra.dll": b"surprise"})
    install = live_install()
    root = st.staging_root(install)
    result = stage_failure_or(lambda: st.stage_update(
        manifest, b"{}", URL, root, platform=WIN,
        opener=opener_for(other)))
    assert isinstance(result, (st.StagingError, VerificationError))  # hash of archive differs from the signed one
    tampered = make_zip({**GOOD, "pixelRef/_internal/base.dll": b"DLL"})  # same size, different content
    forged = manifest_for(tampered, inventory=True)
    object.__setattr__(forged, "files", tuple(f if f.path != "pixelRef/_internal/base.dll" else
                                              type(f)(f.path, f.size, hashlib.sha256(b"dll").hexdigest())
                                              for f in forged.files))
    result = stage_failure_or(lambda: st.stage_update(forged, b"{}", URL, root, platform=WIN, opener=opener_for(tampered)))
    assert isinstance(result, st.StagingError) and "failed verification" in str(result)
    assert not list(root.iterdir())


def test_validate_staged_detects_later_tampering():
    data = make_zip()
    result, install, root = stage(data, inventory=True)
    st.validate_staged(result.directory, result.manifest, WIN)
    (result.application / "_internal" / "base.dll").write_bytes(b"EVL")
    for fn in (lambda: st.validate_staged(result.directory, result.manifest, WIN),):
        try:
            fn()
        except st.StagingError as exc:
            assert "changed" in str(exc)
        else:
            raise AssertionError("tampering not detected")
    other = manifest_for(make_zip({**GOOD, "pixelRef/a": b"1"}))
    try:
        st.validate_staged(result.directory, other, WIN)
    except (st.StagingError, VerificationError):
        pass
    else:
        raise AssertionError("different manifest accepted")


def test_out_of_space_and_unwritable_fail_with_clear_message():
    data = make_zip()
    result, install, root = stage(data, free_space=lambda p: 0)
    assert_rejected(result, install, root, "disk space")
    result, install, root = stage(data, free_space=lambda p: 10 ** 12)
    assert isinstance(result, st.StagedUpdate)


def test_cleanup_only_removes_transactions():
    root = tmp_dir()
    old = root / ("a" * 32)
    old.mkdir()
    (old / "f").write_text("x")
    keep = root / ("b" * 32)
    keep.mkdir()
    unknown = root / "my-notes"
    unknown.mkdir()
    (root / ("c" * 32 + ".txt")).write_text("file")
    outside = tmp_dir()
    (outside / "precious").write_text("x")
    if os.name == "posix":
        (root / ("d" * 32)).symlink_to(outside)
    assert st.clean_staging(root, keep=("b" * 32,)) == ["a" * 32]
    assert keep.exists() and unknown.exists() and (outside / "precious").exists()
    for bad in ("../x", "", "A" * 32, "a" * 31, "a" * 32 + "/.."):
        try:
            st.remove_transaction(root, bad)
        except st.StagingError:
            continue
        raise AssertionError(bad)
    assert not (root / ("a" * 32)).exists()


def test_sequence_counter_never_goes_backwards():
    install = tmp_dir()
    assert st.read_last_sequence(install) == 0
    st.record_sequence(install, 5)
    st.record_sequence(install, 3)
    assert st.read_last_sequence(install) == 5
    (st.update_directory(install) / "accepted.json").write_text("garbage")
    assert st.read_last_sequence(install) == 0
