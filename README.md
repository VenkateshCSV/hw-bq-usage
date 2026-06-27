# hw-bq-usage

BigQuery query scan volume dashboard for Hitwicket analysts. Static site (GitHub Pages) with no backend — the UI loads pre-aggregated JSON.

## Live dashboard

Host `index.html`, `data.json`, and `meta.json` from the repo root on GitHub Pages.

## What's included

| File | Purpose |
|------|---------|
| [index.html](index.html) | Dashboard UI (Chart.js) |
| [data.json](data.json) | Aggregated usage data (generated) |
| [meta.json](meta.json) | Build metadata: window, row count, anomalies |
| [build.py](build.py) | CSV → JSON build script |
| [config/](config/) | Identity maps and GCP project settings |
| [sql/export_jobs.sql](sql/export_jobs.sql) | BigQuery export query |

## Metrics

- TB scanned and estimated cost ($6.25/TB default)
- Daily usage by source (Looker, console, script, SA-cron)
- Analyst leaderboard, Pareto chart, drill-down, compare
- Hour-of-day heatmap and day-of-week chart
- Top 25 heaviest queries (with BigQuery console links)

## Refresh data

See [docs/refresh.md](docs/refresh.md) for the daily manual workflow:

```bash
pip install -r requirements.txt
python build.py --csv exports/your-export.csv --days 90
git add data.json meta.json && git commit -m "Refresh BQ usage data" && git push
```

## Identity mapping

Analysts are resolved from `query_requested_by` and `user_email` via [config/idmap.json](config/idmap.json). Service account buckets are in [config/sa_names.json](config/sa_names.json). The build script warns about unmapped values on each run.

## Automation (Phase 2)

[.github/workflows/refresh-dashboard.yml](.github/workflows/refresh-dashboard.yml) is prepared for scheduled refresh once `GCP_SA_KEY` and `GCP_PROJECT_ID` repository secrets are added.

## Context

See [bq_dashboard_context.md](bq_dashboard_context.md) for full project handoff notes.
