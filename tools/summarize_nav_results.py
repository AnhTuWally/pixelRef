"""Turn bench_canvas_nav.py JSONL results into Markdown tables.

    python tools/summarize_nav_results.py docs/performance/data/baseline_gl.jsonl [more.jsonl ...]
        [--cols frames|gui|tiles|refine|memory] [--filter board=medium,scenario=pan_zoomed]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(paths):
    rows = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def g(row, path, default=None):
    cur = row
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def f(value, digits=1):
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


COLUMNS = {
    "frames": [
        ("fps", lambda r: f(g(r, "frames.fps_paint"))),
        ("present p50", lambda r: f(g(r, "frames.present_interval_ms.p50"))),
        ("p95", lambda r: f(g(r, "frames.present_interval_ms.p95"))),
        ("p99", lambda r: f(g(r, "frames.present_interval_ms.p99"))),
        ("max", lambda r: f(g(r, "frames.present_interval_ms.max"))),
        ("late/N", lambda r: f"{g(r, 'frames.intervals_gt_budget')}/{g(r, 'frames.present_interval_ms.n')}"),
        ("dropped", lambda r: f(g(r, "frames.dropped_frames"))),
        ("paint p95", lambda r: f(g(r, "frames.paint_ms.p95"))),
        ("paint max", lambda r: f(g(r, "frames.paint_ms.max"))),
        ("lat p50", lambda r: f(g(r, "input.latency_ms.p50"))),
        ("lat p95", lambda r: f(g(r, "input.latency_ms.p95"))),
    ],
    "active": [
        ("fps", lambda r: f(g(r, "frames.fps_paint"))),
        ("act p50", lambda r: f(g(r, "frames.present_interval_active_ms.p50"))),
        ("p95", lambda r: f(g(r, "frames.present_interval_active_ms.p95"))),
        ("p99", lambda r: f(g(r, "frames.present_interval_active_ms.p99"))),
        ("max", lambda r: f(g(r, "frames.present_interval_active_ms.max"))),
        ("late", lambda r: f"{g(r, 'frames.active_late')}/{g(r, 'frames.present_interval_active_ms.n')}"),
        (">2x", lambda r: f(g(r, "frames.active_gt_2x"))),
        (">4x", lambda r: f(g(r, "frames.active_gt_4x"))),
        ("gap>33", lambda r: f(g(r, "gui_thread.gaps_gt_33"))),
        ("gap max", lambda r: f(g(r, "gui_thread.tick_gap_ms.max"))),
        ("paint p95", lambda r: f(g(r, "frames.paint_ms.p95"))),
        ("paint max", lambda r: f(g(r, "frames.paint_ms.max"))),
        ("sync tiles", lambda r: f(g(r, "tiles.sync_rendered"))),
        ("blurry fr", lambda r: f(g(r, "tiles.frames_visibly_blurry"))),
        ("refine s", lambda r: f(g(r, "refine.visible_s"), 2)),
    ],
    "gui": [
        ("handler p95", lambda r: f(g(r, "input.handler_ms.p95"), 2)),
        ("handler max", lambda r: f(g(r, "input.handler_ms.max"), 2)),
        ("ev/paint", lambda r: f(g(r, "input.events_per_paint"), 2)),
        ("gap p95", lambda r: f(g(r, "gui_thread.tick_gap_ms.p95"))),
        ("gap max", lambda r: f(g(r, "gui_thread.tick_gap_ms.max"))),
        (">16", lambda r: f(g(r, "gui_thread.gaps_gt_16"))),
        (">33", lambda r: f(g(r, "gui_thread.gaps_gt_33"))),
        (">50", lambda r: f(g(r, "gui_thread.gaps_gt_50"))),
        ("blocked ms", lambda r: f(g(r, "gui_thread.blocked_ms_total"), 0)),
        ("main cpu", lambda r: f(g(r, "gui_thread.main_thread_cpu_share"), 2)),
        ("cores", lambda r: f(g(r, "gui_thread.process_cores_busy"), 2)),
    ],
    "tiles": [
        ("hit %", lambda r: f(100 * (g(r, "tiles.hit_rate") or 0), 1)),
        ("sync tiles", lambda r: f(g(r, "tiles.sync_rendered"))),
        ("sync frames", lambda r: f(g(r, "tiles.frames_with_sync_render"))),
        ("worst sync ms", lambda r: f(g(r, "tiles.worst_frame_sync_ms"))),
        ("fallback fr", lambda r: f(g(r, "tiles.frames_with_fallback"))),
        ("blurry fr", lambda r: f(g(r, "tiles.frames_visibly_blurry"))),
        ("mag p50", lambda r: f(g(r, "tiles.fallback_magnification.p50"), 2)),
        ("blank", lambda r: f(g(r, "tiles.blank"))),
        ("idle sl", lambda r: f(g(r, "tiles.idle_slices"))),
        ("idle@input", lambda r: f(g(r, "tiles.idle_slices_during_input"))),
        ("idle max ms", lambda r: f(g(r, "tiles.idle_slice_ms.max"))),
        ("inval", lambda r: f(g(r, "tiles.invalidations"))),
        ("img exact %", lambda r: f(100 * (g(r, "images.bake_exact_share") or 0), 0)),
        ("placeholder", lambda r: f(g(r, "images.bake_placeholder"))),
    ],
    "refine": [
        ("visible s", lambda r: f(g(r, "refine.visible_s"), 2)),
        ("all s", lambda r: f(g(r, "refine.all_s"), 2)),
        ("frames", lambda r: f(g(r, "refine.frames_during"))),
        ("jump 1st ms p50", lambda r: f(g(r, "jumps.first_frame_ms.p50"))),
        ("jump 1st max", lambda r: f(g(r, "jumps.first_frame_ms.max"))),
        ("jump full p50", lambda r: f(g(r, "jumps.full_quality_ms.p50"), 0)),
        ("jump full max", lambda r: f(g(r, "jumps.full_quality_ms.max"), 0)),
        ("never", lambda r: f(g(r, "jumps.never_full"))),
    ],
    "memory": [
        ("items", lambda r: f(g(r, "board.items"))),
        ("open ms", lambda r: f(g(r, "board.open_ms"))),
        ("tiles MB", lambda r: f(g(r, "memory.tile_mb_all_views"))),
        ("img store MB", lambda r: f(g(r, "memory.image_store_mb"))),
        ("mip MB", lambda r: f(g(r, "memory.mip_mb"))),
        ("rss end MB", lambda r: f(g(r, "memory.rss_mb_end"), 0)),
        ("rss peak MB", lambda r: f(g(r, "memory.rss_peak_mb"), 0)),
    ],
}


def label(row):
    c = row["config"]
    bits = [c["board"], c["scenario"], c["cache"]]
    if c.get("views", 1) > 1:
        bits.append(f"{c['views']}v{'-linked' if c.get('link') else ''}")
    if c["scenario"].startswith("pan") and c.get("pan_speed", 1200) != 1200:
        bits.append(f"{int(c['pan_speed'])}px/s")
    if c.get("input_hz", 125) != 125:
        bits.append(f"{int(c['input_hz'])}Hz")
    if c.get("variant", "baseline") != "baseline":
        bits.append(c["variant"].replace("camera_coalesce", "camco").replace("pan_coalesce", "panco")
                    .replace("minzoom_cache", "minz").replace("nosync_zoom", "nosync"))
    if c.get("set"):
        bits.append(",".join(c["set"]))
    env = row.get("env_vars") or {}
    if env.get("PIXELREF_GL") == "0":
        bits.append("sw")
    if env.get("QT_QPA_PLATFORM") == "offscreen":
        bits.append("offscreen")
    return " / ".join(bits)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--cols", default="frames")
    parser.add_argument("--filter", default="")
    args = parser.parse_args()
    rows = load(args.paths)
    for part in filter(None, args.filter.split(",")):
        key, value = part.split("=", 1)
        rows = [r for r in rows if str(r["config"].get(key)) == value]
    cols = COLUMNS[args.cols]
    print("| run | " + " | ".join(c for c, _ in cols) + " |")
    print("|---|" + "---:|" * len(cols))
    for row in rows:
        print(f"| {label(row)} | " + " | ".join(fn(row) for _, fn in cols) + " |")


if __name__ == "__main__":
    main()
