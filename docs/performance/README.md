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
| `data/` | Raw results (`*.jsonl`), the exact run configurations (`matrix_*.json`), `environment.json`, generated `tables/` |

Tooling (repository root):

* `tools/bench_canvas_nav.py`: real-time navigation benchmark (boards, scenarios, frame pacing, tile/image stats).
* `tools/bench_variants.py`: runtime prototypes of the proposed strategies (monkeypatches, never imported by the app).
* `tools/summarize_nav_results.py`: JSONL → Markdown tables.

Earlier documents: `docs/perf-navigation-review.md` (measured, current) and the root
`canvas-navigation-performance.md` (stale; see audit §1.2).
