# Daily dashboard refresh

Primary workflow: **aggregated BQ payload** (no query text, ~100–500 KB). See [payload-schema.md](payload-schema.md).

Legacy job-level CSV (`sql/export_jobs.sql`) is deprecated debug-only.

## Prerequisites

- Python 3.10+
- `pip install -r requirements.txt`
- Your BQ export producing a payload matching [payload-schema.md](payload-schema.md)

Optional reference SQL (regenerate after idmap edits):

```bash
python scripts/render_idmap_sql.py
# → sql/export_aggregated.sql   (40-day bootstrap)
# → sql/export_incremental.sql  (edit since_date before each run)
```

## Check current data

```bash
python build.py --status
```

Shows `last_data_date`, window range, and last merge summary.

## First load (40-day bootstrap)

**If your export is aggregated** (one `payload` blob with `daily` / `hourly` / `top_jobs`):

1. Run `sql/export_aggregated.sql` in BQ → save as `exports/payload.jsonl`
2. `python build.py --init --from-bq exports/payload.jsonl`

**If your export is job-level JSONL** (one row per query: `job_id`, `gb_scanned`, … — like a slim CSV):

1. Save BQ results as `exports/payload.json` (JSONL is fine)
2. `python build.py --init --from-jobs exports/payload.json`

Both paths write `facts.json`, `data.json`, and `meta.json`.

## Incremental refresh (every few days)

1. `python build.py --status` → note `last_data_date`.
2. Export **new dates only** from BQ (typically day after `last_data_date` through today).
   - Reference: edit `since_date` in `sql/export_incremental.sql`, run in BQ console.
3. Merge:

   ```bash
   python build.py --merge --from-bq exports/payload-YYYYMMDD.json
   ```

4. Review stdout:
   - `Dates added` — new days appended
   - `Dates overwritten` — same-day re-upload replaced existing data
5. Commit updated `facts.json`, `data.json`, `meta.json`.

### Re-fix a day

Export that specific date (or range) and `--merge`. Overlapping dates **overwrite** prior facts for those dates.

## Validate (optional)

```bash
python scripts/validate_parity.py --baseline data.json.bak --built data.json
```

## Legacy CSV path (debug)

```bash
python build.py --csv exports/slim-export.csv --days 40
```

Does not update `facts.json` or support incremental merge.

## Adding a new analyst

Edit [config/idmap.json](../config/idmap.json), re-run `python scripts/render_idmap_sql.py` if using reference SQL, then re-export from BQ.

## Phase 2 (automated)

When GCP credentials exist, enable [.github/workflows/refresh-dashboard.yml](../.github/workflows/refresh-dashboard.yml).
