# BQ aggregated payload schema

The dashboard build consumes **pre-aggregated JSON** from your BigQuery export. No raw job rows and **no SQL query text**.

## Top-level object

| Field | Required | Description |
|-------|----------|-------------|
| `window_start` | recommended | First date in this export (`YYYY-MM-DD`, Asia/Kolkata) |
| `window_end` | recommended | Last date in this export |
| `daily` | yes | Per-person, per-day, per-source aggregates |
| `hourly` | yes | Per-person, per-day, per-hour GB (must include `date`) |
| `top_jobs` | yes | Heaviest jobs in the exported range (`job_id` only) |
| `unmapped` | no | Identity keys that could not be resolved |

## `daily[]`

```json
{"date": "2026-06-01", "person": "Venkatesh", "source": 0, "gb": 12.5, "q": 3}
```

| Field | Type | Notes |
|-------|------|-------|
| `date` | string | `YYYY-MM-DD` |
| `person` | string | Canonical analyst name (resolved in your BQ export) |
| `source` | int | `0` Looker, `1` Console, `2` Script, `3` SA-Cron |
| `gb` | number | GB scanned |
| `q` | int | Query count |

## `hourly[]`

```json
{"date": "2026-06-01", "person": "Venkatesh", "hour": 14, "gb": 5.0}
```

| Field | Type | Notes |
|-------|------|-------|
| `date` | string | Required for incremental merge / same-day overwrite |
| `person` | string | Canonical analyst name |
| `hour` | int | 0–23 (Asia/Kolkata) |
| `gb` | number | GB scanned in that hour |

## `top_jobs[]`

```json
{"job_id": "abc-123", "date": "2026-06-01", "person": "Venkatesh", "src": "Script", "gb": 120.0}
```

| Field | Type | Notes |
|-------|------|-------|
| `job_id` | string | BigQuery job ID — dashboard links to BQ console |
| `date` | string | Job date |
| `person` | string | Canonical analyst name |
| `src` | string | `Looker`, `Console`, `Script`, or `SA-Cron` |
| `gb` | number | GB scanned |

**Excluded:** `query`, `qtext`, or any SQL string field.

## `unmapped[]`

```json
{"key": "unknown_alias", "count": 1}
```

## File formats

`build.py --from-bq` accepts:

1. **Plain JSON** — the object above saved as `exports/payload.json`
2. **JSONL / NDJSON** — `.jsonl` or `.ndjson` (typical BQ “Save results” format):
   - One line with the full payload object, or
   - One line with a `payload` column: `{"payload": "{...}"}` or `{"payload": {...}}`
   - A `.json` file that contains one JSON object per line is also accepted
3. **BQ one-row CSV** — first cell is the JSON string

Example BQ JSONL (single line):

```json
{"payload":"{\"window_start\":\"2026-05-14\",\"daily\":[...],\"hourly\":[...],\"top_jobs\":[...]}"}
```

Usage is unchanged — point `--from-bq` at your file:

```bash
python build.py --init --from-bq exports/payload.jsonl
```

## Merge semantics

- **`--init`** — replaces all of `facts.json` with the payload
- **`--merge`** — for each `date` in the payload: delete existing facts for that date, insert payload facts; top jobs deduped by `job_id` (keep higher `gb`), global top 100 retained

See [refresh.md](refresh.md) for the operator workflow.
