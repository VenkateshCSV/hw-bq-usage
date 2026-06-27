# hw-bq-usage

BigQuery query scan volume dashboard for Hitwicket analysts. Static site (GitHub Pages) — no backend. The UI loads pre-built JSON.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python build.py --status
```

## How to refresh data

There are **two supported export formats**. Pick the one that matches what you download from BigQuery.

### Which export do I have?

| Your BQ download looks like… | Size (typical) | Build command |
|------------------------------|----------------|---------------|
| **Job-level JSONL** — one JSON object per line with `job_id`, `date`, `gb_scanned`, `query_requested_by` | ~50 MB / 40d | `--from-jobs` |
| **Aggregated payload** — one row/cell with `daily`, `hourly`, `top_jobs` arrays (from `sql/export_aggregated.sql`) | ~100–500 KB | `--from-bq` |

Both paths produce the same dashboard files and support incremental updates.

---

### First load (bootstrap)

Export from BigQuery (40 days, **no `query` column**), save under `exports/`, then:

**Job-level JSONL** (most common if you export raw jobs as JSON):

```bash
python build.py --init --from-jobs exports/payload.json
```

**Aggregated payload** (if you run `sql/export_aggregated.sql` in BQ):

```bash
python scripts/render_idmap_sql.py   # refresh idmap in SQL, if needed
# Run sql/export_aggregated.sql in BQ console → save result
python build.py --init --from-bq exports/payload.jsonl
```

### Incremental update (every few days)

```bash
python build.py --status
# Note last_data_date, e.g. 2026-06-28

# Export only new dates from BQ → exports/payload-new.json

python build.py --merge --from-jobs exports/payload-new.json
# or:  python build.py --merge --from-bq exports/payload-new.jsonl
```

Stdout reports **dates added** vs **dates overwritten**. Re-uploading the same date replaces that day's data (no double-counting).

### Deploy

```bash
git add facts.json data.json meta.json
git commit -m "Refresh BQ usage data"
git push origin main
```

GitHub Pages serves `index.html`, `data.json`, and `meta.json` from the repo root.

---

## `build.py` reference

| Command | Purpose |
|---------|---------|
| `python build.py --status` | Show `last_data_date`, window range, last refresh |
| `python build.py --init --from-jobs FILE` | First load from job-level JSONL |
| `python build.py --merge --from-jobs FILE` | Append/overwrite days from job-level JSONL |
| `python build.py --init --from-bq FILE` | First load from aggregated BQ payload |
| `python build.py --merge --from-bq FILE` | Append/overwrite days from aggregated payload |

Accepted file types: `.json`, `.jsonl`, `.ndjson`. BQ “Save results as JSON” is usually JSONL (one object per line) — that works as-is.

Legacy debug-only:

```bash
python build.py --csv exports/jobs.csv --days 40
```

Does not write `facts.json` or support `--merge`.

---

## Repo layout

| File | Purpose |
|------|---------|
| [index.html](index.html) | Dashboard UI |
| [data.json](data.json) | Aggregated data for charts (generated) |
| [facts.json](facts.json) | Merge store for incremental updates (generated) |
| [meta.json](meta.json) | `last_data_date`, window, merge summary (generated) |
| [build.py](build.py) | Build script |
| [config/idmap.json](config/idmap.json) | Alias + email → analyst name |
| [config/sa_names.json](config/sa_names.json) | Service account → bucket name |
| [docs/payload-schema.md](docs/payload-schema.md) | Aggregated payload format (`--from-bq`) |
| [docs/refresh.md](docs/refresh.md) | Detailed operator notes |
| [sql/export_aggregated.sql](sql/export_aggregated.sql) | Reference: 40-day aggregated export |
| [sql/export_incremental.sql](sql/export_incremental.sql) | Reference: incremental date range |

Regenerate SQL after idmap changes:

```bash
python scripts/render_idmap_sql.py
```

---

## Identity mapping

Each query is attributed using `query_requested_by` (and `user_email` for Looker):

| Route | `query_requested_by` | Resolved via |
|-------|---------------------|--------------|
| BQ console | `person@hitwicket.com` | [config/idmap.json](config/idmap.json) emails |
| Script via SA | short alias (e.g. `venkatesh`) | idmap aliases |
| Looker Studio | `looker_studio` | user email → idmap |
| Service accounts | `*.gserviceaccount.com` | [config/sa_names.json](config/sa_names.json) |

With `--from-jobs`, resolution happens in Python. With `--from-bq`, it should already be done in your BQ export (reference SQL does this).

Add a new analyst → edit `config/idmap.json`, re-export, rebuild.

---

## Dashboard

Shows TB scanned, estimated cost, daily usage by source (Looker / console / script / SA-cron), analyst leaderboard, drill-down, compare, hour-of-day heatmap, and top heavy queries (linked to BQ console by `job_id`).

Header badge shows **Data through …** and **Refreshed …** from `meta.json`.

---

## Automation (future)

[.github/workflows/refresh-dashboard.yml](.github/workflows/refresh-dashboard.yml) is ready for daily GitHub Actions refresh once GCP credentials are configured.

---

## More context

See [bq_dashboard_context.md](bq_dashboard_context.md) for full project handoff notes.
