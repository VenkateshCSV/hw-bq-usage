# hw-bq-usage

BigQuery query scan volume dashboard for Hitwicket analysts. Static site (GitHub Pages) with no backend — the UI loads pre-aggregated JSON.

## Live dashboard

Host `index.html`, `data.json`, `meta.json`, and `facts.json` from the repo root on GitHub Pages (`facts.json` is optional for the UI but needed for incremental rebuilds).

## What's included

| File | Purpose |
|------|---------|
| [index.html](index.html) | Dashboard UI (Chart.js) |
| [data.json](data.json) | Aggregated usage data (generated) |
| [facts.json](facts.json) | Merge store for incremental updates (generated) |
| [meta.json](meta.json) | Build metadata: window, last data date, merge summary |
| [build.py](build.py) | Payload/CSV → JSON build script |
| [config/](config/) | Identity maps and GCP project settings |
| [docs/payload-schema.md](docs/payload-schema.md) | BQ payload contract (no query text) |
| [sql/export_aggregated.sql](sql/export_aggregated.sql) | Reference: 40-day bootstrap export |

## Refresh data

```bash
pip install -r requirements.txt
python build.py --status                              # last data date
python build.py --init --from-bq exports/payload.json # first 40-day load
python build.py --merge --from-bq exports/new.json    # incremental
```

See [docs/refresh.md](docs/refresh.md) for the full workflow.

Expected payload size: **~100–500 KB** per export (vs ~179 MB job-level CSV with query text).

## Identity mapping

Analysts are resolved in your BQ export using the same rules as [config/idmap.json](config/idmap.json). Reference SQL is regenerated via `python scripts/render_idmap_sql.py`.

## Automation (Phase 2)

[.github/workflows/refresh-dashboard.yml](.github/workflows/refresh-dashboard.yml) is prepared for scheduled refresh once GCP credentials are added.

## Context

See [bq_dashboard_context.md](bq_dashboard_context.md) for full project handoff notes.
