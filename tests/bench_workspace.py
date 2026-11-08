"""Workspace benchmark: switching between many open boards, and restoring a workspace.

``python tests/bench_workspace.py``   (BENCH_BOARDS=10 BENCH_IMAGES=2 BENCH_PX=1600x1000 BENCH_ROUNDS=3)

Compares the old behaviour (every parked board kept in memory as scene text, ``EVICT_CLEAN`` off)
with the current one (clean boards are reloaded from disk), each in its own process so peak memory
isn't shared. Reports per-switch time, the scene text the open-files list retains, resident and
peak memory, and for a restored workspace the startup time and how many scenes were fully loaded.
``BENCH_OUT=file.jsonl`` appends each run as a JSON line.
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

BOARDS = int(os.environ.get("BENCH_BOARDS", "10"))
IMAGES = int(os.environ.get("BENCH_IMAGES", "2"))
PX = tuple(int(v) for v in os.environ.get("BENCH_PX", "1600x1000").split("x"))
ROUNDS = int(os.environ.get("BENCH_ROUNDS", "3"))
CACHE = Path(os.environ.get("BENCH_CACHE", Path.home() / ".cache" / "pixelref-bench-workspace"))


def memory_mb() -> tuple[float, float]:
    """(resident, peak) in MB for this process."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return counters.WorkingSetSize / 2**20, counters.PeakWorkingSetSize / 2**20
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 if sys.platform != "darwin" else 2**20)
    try:
        resident = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20
    except OSError:
        resident = peak
    return resident, peak


def ensure_boards() -> list[str]:
    """Boards of incompressible noise images (so embedded size is realistic), made once and reused."""
    CACHE.mkdir(parents=True, exist_ok=True)
    marker = CACHE / f"boards_{BOARDS}_{IMAGES}_{PX[0]}x{PX[1]}.json"
    if marker.exists():
        paths = json.loads(marker.read_text())
        if all(Path(p).exists() for p in paths):
            return paths
    env = dict(os.environ, BENCH_CHILD="make")
    out = subprocess.run([sys.executable, __file__], env=env, capture_output=True, text=True, check=True)
    paths = json.loads(out.stdout.strip().splitlines()[-1])
    marker.write_text(json.dumps(paths))
    return paths


def make_boards() -> None:
    sys.path.insert(0, str(Path(__file__).parent))
    from harness import make
    from PySide6.QtGui import QImage

    h = make()
    paths = []
    for b in range(BOARDS):
        h.run("file.new")
        for i in range(IMAGES):
            png = CACHE / f"noise_{b}_{i}.png"
            image = QImage(PX[0], PX[1], QImage.Format_RGB32)
            ptr = image.bits()
            ptr[:] = os.urandom(image.sizeInBytes())
            image.save(str(png))
            h.create("image", f"img{i}", path=png.as_posix(), translate=[i * 400.0, 0.0])
        h.trigger("image_storage.embed_all")  # photos live inside the file, like a real board
        path = CACHE / f"board_{b}.pxref"
        assert h.save_to(path)
        paths.append(str(path.resolve()))
    print(json.dumps(paths))


def run_child(evict: bool, paths: list[str]) -> dict:
    sys.path.insert(0, str(Path(__file__).parent))
    from harness import APP, make

    import pixelref.builtins.file as file_plugin

    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    file_plugin.EVICT_CLEAN = evict
    h = make()
    files = h.service("files")
    files.restore_workspace()
    for p in paths:
        h.run("file.open_path", p, confirm=False)
        h.refresh()
    import gc

    gc.collect()
    after_open = memory_mb()[0]
    times = []
    for _ in range(ROUNDS):
        for p in paths:
            t0 = time.perf_counter()
            h.run("file.switch_to", p)
            APP.processEvents()
            # The app's real event loop deletes widgets on deleteLater(); this script has none.
            QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            times.append((time.perf_counter() - t0) * 1000)
    retained = sum(len(e.text or "") for e in files.open_entries()) / 2**20
    gc.collect()
    resident, peak = memory_mb()
    result = {"policy": "evict clean" if evict else "keep all", "boards": len(paths), "switches": len(times),
              "switch_median_ms": round(statistics.median(times), 1), "switch_p95_ms": round(sorted(times)[int(len(times) * .95)], 1),
              "retained_text_mb": round(retained, 1), "after_open_mb": round(after_open),
              "resident_mb": round(resident), "peak_mb": round(peak)}

    if os.environ.get("BENCH_OBJECTS"):
        from PySide6.QtGui import QImage, QPixmap

        gc.collect()
        objs = gc.get_objects()
        big = [o for o in objs if isinstance(o, (bytes, bytearray, str)) and len(o) > 1_000_000]
        result["live_QImage"] = sum(isinstance(o, QImage) for o in objs)
        result["live_QPixmap"] = sum(isinstance(o, QPixmap) for o in objs)
        result["big_blobs"] = len(big)
        result["big_blobs_mb"] = round(sum(len(o) for o in big) / 2**20)
        result["scene_nodes"] = len(h.session.scene.nodes)
        from PySide6.QtWidgets import QApplication as _QA

        result["widgets"] = len(_QA.allWidgets())
        result["views"] = len(h.app.views)
        from pixelref.builtins.image import source as image_source

        store = image_source.STORE
        live = {id(x) for x in h.session.services.get("image_cache").handles.values() if x is not None}
        orphans = [e for e in store.entries.values() if e[0]() is not None and id(e[0]()) not in live]
        result["store_entries"] = len(store.entries)
        result["store_mb"] = round(store.bytes / 2**20)
        result["store_orphans"] = len(orphans)
        result["store_orphan_mb"] = round(sum(e[1] for e in orphans) / 2**20)
    h.run("file.close_all")
    h.refresh()
    gc.collect()
    result["after_close_all_mb"] = round(memory_mb()[0])
    # Diagnostic: is what remains a cache that can be emptied, or memory the process just keeps?
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QPixmapCache
    from PySide6.QtWidgets import QApplication

    QPixmapCache.clear()
    cache = h.session.services.get("image_cache")
    if cache is not None:
        cache.clear()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    APP.processEvents()
    gc.collect()
    result["after_clearing_caches_mb"] = round(memory_mb()[0])

    # Restoring the workspace: only the active board may load.
    files.restore_workspace()
    for p in paths:
        h.run("file.open_path", p, confirm=False)
    h.service("recovery").resume_enabled = True
    files.save_workspace()
    h.app.request_close()
    h2 = make()
    loads = []
    real = h2.session.load_text
    h2.session.load_text = lambda *a, **k: loads.append(1) or real(*a, **k)
    t0 = time.perf_counter()
    h2.service("recovery").resume_enabled = True
    h2.service("recovery").resume()
    result["restore_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    result["restore_scene_loads"] = len(loads)
    result["restore_open_files"] = len(h2.service("files").open_files_list())
    result["restore_dormant"] = len(h2.service("files").dormant_files())
    return result


def main() -> int:
    mode = os.environ.get("BENCH_CHILD")
    if mode == "make":
        make_boards()
        return 0
    if mode == "run":
        print("RESULT " + json.dumps(run_child(os.environ["BENCH_EVICT"] == "1", json.loads(os.environ["BENCH_PATHS"]))))
        return 0
    paths = ensure_boards()
    size = sum(Path(p).stat().st_size for p in paths) / 2**20
    print(f"{len(paths)} boards x {IMAGES} images of {PX[0]}x{PX[1]} noise ({size:.0f} MB on disk), {ROUNDS} rounds\n")
    rows = []
    for evict in (False, True):
        env = dict(os.environ, BENCH_CHILD="run", BENCH_EVICT="1" if evict else "0", BENCH_PATHS=json.dumps(paths))
        out = subprocess.run([sys.executable, __file__], env=env, capture_output=True, text=True)
        line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT ")), None)
        if line is None:
            print(out.stdout[-2000:], out.stderr[-2000:])
            return 1
        rows.append(json.loads(line[7:]))
    keys = list(rows[0])
    for key in keys:
        print(f"{key:<22}" + "".join(f"{str(r[key]):>16}" for r in rows))
    if os.environ.get("BENCH_OUT"):
        with open(os.environ["BENCH_OUT"], "a", encoding="utf-8") as handle:
            for r in rows:
                handle.write(json.dumps(r) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
