# Canvas performance investigation

Investigation of pan/zoom performance on large, image-heavy boards. **No production code was changed.**
Start with the executive summary.

| Document | Contents |
|---|---|
| [00-executive-summary.md](00-executive-summary.md) | Answers, top five bottlenecks, three best improvements, gaps |
| [01-canvas-performance-audit.md](01-canvas-performance-audit.md) | Execution map, work per event, review of earlier findings, ranked bottlenecks |
| [02-progressive-rendering-feasibility.md](02-progressive-rendering-feasibility.md) | What the tile cache already does, prototypes, recommended architecture |
| [03-benchmark-report.md](03-benchmark-report.md) | Environment, method, validity notes, all result tables, reproduction |
| [04-optimisation-strategy.md](04-optimisation-strategy.md) | Ranked candidates, trade-offs, rejected ideas |
| [05-implementation-roadmap.md](05-implementation-roadmap.md) | Phased steps with files, tests, numeric gates, rollback |
| [06-phase1-results.md](06-phase1-results.md) | Measured results of roadmap Phase 1 (steps 1.1 to 1.3), gates met and not met |
| [07-gl-paint-investigation.md](07-gl-paint-investigation.md) | Roadmap step 1.5: where the GL paint time goes, go/no-go |
| [08-phase2-results.md](08-phase2-results.md) | Phase 2 (progressive rendering): design, final 60-run matrix, gates, validation, open issues |
| [09-big-selections-and-item-index.md](09-big-selections-and-item-index.md) | Big selections, hover, rubber band, drag: the item index fix, cached overlays, results, what is not fixed |
| `data/` | Raw results (`*.jsonl`), the exact run configurations (`matrix_*.json`), `environment.json`, generated `tables/` |

Tooling (repository root):

* `tools/bench_canvas_nav.py`: real-time navigation benchmark (boards, scenarios, frame pacing, tile/image stats).
* `tests/bench_selection.py`: big selections (pan, zoom, hover, select all, rubber band, drag), per-event times and profiles.
* `tools/bench_variants.py`: runtime prototypes of the proposed strategies (monkeypatches, never imported by the app).
* `tools/summarize_nav_results.py`: JSONL → Markdown tables.
* `tools/probe_gl_paint.py`: per-section timing of `paintEvent` (GL vs software, several views).
* `tools/validate_rhi.py`, `tools/validate_progressive.py`: pixel checks (real window; `render_frame` images for the tile cache, screen captures for the viewport path).

Earlier documents: `docs/perf-navigation-review.md` (measured, current) and the root
`canvas-navigation-performance.md` (stale; see audit §1.2).
