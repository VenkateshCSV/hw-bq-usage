# Daily dashboard refresh (manual)

Phase 1 workflow until GitHub Actions credentials are configured.

## Prerequisites

- Python 3.10+
- `pip install -r requirements.txt`
- BigQuery access to export from `INFORMATION_SCHEMA.JOBS`

## Steps

1. **Export from BigQuery**

   Run [sql/export_jobs.sql](../sql/export_jobs.sql) in the BQ console and download CSV, or:

   ```bash
   mkdir -p exports
   bq query --use_legacy_sql=false --format=csv \
     < sql/export_jobs.sql > exports/bq-results-$(date +%Y%m%d).csv
   ```

2. **Build JSON**

   ```bash
   python build.py \
     --csv exports/bq-results-YYYYMMDD.csv \
     --days 90 \
     --output data.json \
     --meta meta.json
   ```

   Review stderr for **unmapped identity** warnings and **anomaly** alerts.

3. **Validate (optional)**

   If you have a previous `data.json` snapshot:

   ```bash
   python scripts/validate_parity.py --baseline data.json --built data.json.new
   ```

4. **Deploy**

   ```bash
   git add data.json meta.json
   git commit -m "Refresh BQ usage data"
   git push origin main
   ```

   GitHub Pages updates within ~1 minute.

## Adding a new analyst

Edit [config/idmap.json](../config/idmap.json):

- Script alias → add under `aliases`
- Console / Looker email → add under `emails`

Re-run `build.py` and check that unmapped counts are zero.

## Service accounts

New SA buckets go in [config/sa_names.json](../config/sa_names.json).

## Phase 2 (automated)

When GCP credentials are ready, enable [.github/workflows/refresh-dashboard.yml](../.github/workflows/refresh-dashboard.yml) and add repository secrets:

- `GCP_SA_KEY` — service account JSON with BigQuery job creation + INFORMATION_SCHEMA read
- `GCP_PROJECT_ID` — e.g. `hitwicketsuperstars`
