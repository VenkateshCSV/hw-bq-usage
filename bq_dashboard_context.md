# BigQuery Usage Dashboard — Project Context

> **Purpose of this document:** Full handoff context for an agent continuing work on this project. Covers the data source, identity resolution, dashboard architecture, current state, and known extension points.

---

## 1. What was built

A self-contained `index.html` dashboard for tracking BigQuery query scan volume across Hitwicket analysts. It is designed to be hosted on GitHub Pages with zero backend — all data is baked into the HTML at build time.

**Live metrics it shows:**
- TB scanned (primary metric) and estimated cost (secondary, $6.25/TB default)
- Daily usage chart stacked by source
- Analyst leaderboard (sortable)
- Cost concentration / Pareto chart
- Per-analyst daily drill-down
- Multi-analyst compare (line chart, up to 8)
- Top 40 heaviest individual queries

---

## 2. Source data

**File:** `bq-results-20260623-025836-1782183567631.csv`
**Rows:** 297,395
**Date range:** May 14 – June 22, 2026 (40 days)
**Total scanned:** ~399 TB (~$2,435 at $6.25/TB)

### Columns

| Column | Description |
|--------|-------------|
| `job_id` | Unique BQ job identifier |
| `date` | Date of query (`YYYY-MM-DD`) |
| `time` | Time of query |
| `user_email` | Google account that ran the job |
| `queried_by` | (duplicate/legacy — not used) |
| `requestor` | (duplicate/legacy — not used) |
| `query_requested_by` | **Primary identity signal** — see §3 |
| `gb_scanned` | GB scanned by this job (float, can be null → treat as 0) |

> **Note:** No query text, table name, slot time, or duration is available. The dashboard shows who/when/how-much, not why.

---

## 3. Identity resolution

This is the most important thing to understand about the data. Each person can appear under **up to three different labels** depending on how they ran their query:

| Route | `user_email` | `query_requested_by` | Label in dashboard |
|-------|-------------|---------------------|-------------------|
| BQ console | `person@hitwicket.com` | `person@hitwicket.com` | Person's first name |
| Script via SA | `bigquery-admin@hitwicketsuperstars.iam.gserviceaccount.com` | short alias (e.g. `venkatesh`) | Person's first name |
| Looker Studio refresh | `person@hitwicket.com` | `looker_studio` | Person's first name |

### Confirmed identity map

```python
idmap = {
    # alias → canonical name (script route)
    'venkatesh':    'Venkatesh',
    'yasho':        'Yashovardhan',
    'varanasi':     'Aditya Varanasi',
    'santoryu_zunder': 'Aditya Zunder',
    'devanshi':     'Devanshi',
    'devansh':      'Devansh',
    'devs':         'Sourav',   # temp query given to Sourav by Arjun
    'sourav':       'Sourav',
    'tanmay':       'Tanmay',
    'prasannajeet': 'Prasannajeet',
    'shibam':       'Shibam',
    'soham':        'Soham',
    'spoorthi':     'Spoorthi',
    'ankush':       'Ankush',
    'harkeerat':    'Harkeerat',   # script-only, no console activity
    'avipsa':       'Avipsa',      # script-only, no console activity
    'shugal':       'Shugal',      # script-only, no console activity

    # email → canonical name (console + looker route)
    'venkatesh@hitwicket.com':      'Venkatesh',
    'yashovardhan@hitwicket.com':   'Yashovardhan',
    'aditya.varanasi@hitwicket.com':'Aditya Varanasi',
    'aditya.zunder@hitwicket.com':  'Aditya Zunder',
    'devanshi@hitwicket.com':       'Devanshi',
    'devansh@hitwicket.com':        'Devansh',
    'sourav.s@hitwicket.com':       'Sourav',
    'tanmay@hitwicket.com':         'Tanmay',
    'prasannajeet@hitwicket.com':   'Prasannajeet',
    'shibam@hitwicket.com':         'Shibam',
    'soham@hitwicket.com':          'Soham',
    'spoorthi@hitwicket.com':       'Spoorthi',
    'ankush@hitwicket.com':         'Ankush',
    'mustafa@hitwicket.com':        'Mustafa',
    'aritra@hitwicket.com':         'Aritra',
    'arsalaan@hitwicket.com':       'Arsalaan',
    'asmi@hitwicket.com':           'Asmi',
    'fayas@hitwicket.com':          'Fayas',
    'rohit@hitwicket.com':          'Rohit',
    'kiran@hitwicket.com':          'Kiran',
    'anush@hitwicket.com':          'Anush',
    'arjun@hitwicket.com':          'Arjun',
    'keerti@hitwicket.com':         'Keerti',
    'leetanshi@hitwicket.com':      'Leetanshi',
}
```

### Service account map

Jobs where `query_requested_by` is itself a service account email are grouped as named SA buckets (not attributed to any person):

```python
SA_NAMES = {
    'bigquery-admin@hitwicketsuperstars.iam.gserviceaccount.com':               'SA: bigquery-admin',
    'firebase-measurement@system.gserviceaccount.com':                          'SA: firebase-measurement',
    'reports-sa@hitwicketsuperstars.iam.gserviceaccount.com':                   'SA: reports-sa',
    'service-352216829522@gcp-sa-bigquerydatatransfer.iam.gserviceaccount.com': 'SA: data-transfer',
    '352216829522-compute@developer.gserviceaccount.com':                       'SA: compute',
    'service-352216829522@gcp-sa-connectedsheets.iam.gserviceaccount.com':      'SA: connected-sheets',
}
```

`SA: bigquery-admin` is by far the heaviest — 16,665 jobs / ~180 TB — these are unattributed cron/scheduled jobs. The dashboard cannot tie them to a person.

### Source classification

Each row is classified into one of 4 source indices used throughout the JS:

| Index | Label | Colour | Rule |
|-------|-------|--------|------|
| 0 | Looker Studio | `#3b82c4` | `query_requested_by == 'looker_studio'` |
| 1 | BQ console | `#1d9e75` | `query_requested_by` contains `@hitwicket.com` |
| 2 | Script | `#d8643a` | short alias (no `@`, not `looker_studio`, not SA) |
| 3 | SA-Cron | `#878d98` | `query_requested_by` is a `gserviceaccount` email |

---

## 4. Data pipeline (Python)

The build script reads the raw CSV and produces an embedded JSON payload baked into `index.html`. Run to refresh for any new export that uses the same schema.

### Per-person daily array format

Each person in `DATA.people` is an array of 40 rows (one per day), each row having 8 values:

```
[looker_gb, console_gb, script_gb, sacron_gb, looker_q, console_q, script_q, sacron_q]
  index 0       1          2          3          4         5          6          7
```

### `DATA.totals` shape

```json
{
  "PersonName": {
    "gb": 12345.6,
    "q": 4567,
    "looker_q": 4000,
    "direct_q": 567
  }
}
```

### `DATA.topq` shape

Top 40 individual queries, each:
```json
{ "job": "<job_id>", "di": 12, "p": "Venkatesh", "src": "Script", "gb": 345.6 }
```
`di` is the date index into `DATA.dates`.

### `DATA.dates`

Array of 40 short date strings: `["May 14", "May 15", ..., "Jun 22"]`

---

## 5. Dashboard architecture (`index.html`)

Single self-contained file (~72 KB). No build step, no server, no imports beyond Google Fonts and Chart.js 4.4.1 from cdnjs.

### Global state object `S`

```js
S = {
  start: 0,          // date range start index
  end: 39,           // date range end index
  metric: 'tb',      // 'tb' | 'cost' | 'q'  — TB is default
  rate: 6.25,        // $/TB for cost estimation
  hideSA: false,     // hide all SA: entities
  src: Set([0,1,2,3]),// global source filter — affects ALL charts
  person: 'Venkatesh',// selected analyst for drill-down
  comparePeople: Set, // selected analysts for compare chart
  sortKey: 'tb',     // leaderboard sort column
  sortDir: -1,       // -1 = descending
}
```

### Source filter

**Single global `S.src` Set** drives all charts simultaneously. One set of pills in the controls bar — no per-section filters. Toggling a pill calls `toggleSrc(k)` → updates `S.src` → calls `renderAll(false)`.

The minimum active sources is 1 (you can't deselect all). Pill visual states:
- **OFF:** grey border `#cdd2da` + grey text `#9aa1ad`
- **ON:** source colour fill + white text (set via inline style in `buildSrcFilter()`)

### Key render functions

| Function | What it renders | Uses `S.src`? |
|----------|----------------|---------------|
| `renderKPIs()` | 4 KPI cards | ✓ |
| `renderTrend()` | Daily stacked bar | ✓ |
| `renderPareto()` | Cumulative cost chart | ✓ |
| `renderLB()` | Sortable leaderboard table | ✓ |
| `renderDrill()` | Per-analyst daily bar | ✓ |
| `renderCompare()` | Multi-analyst line chart | ✓ |
| `renderTQ()` | Heaviest queries table | ✗ (always all sources) |

`renderAll(rebuildCompare=true)` calls all of them. Pass `false` to skip rebuilding compare checkboxes (used when only source filter changed, avoids DOM jitter).

### Aggregation helpers

```js
aggPerson(p)           // respects S.src + date range → {gb, q}
aggPersonAllSrc(p)     // ignores S.src, all 4 sources → {gb, q}
aggPersonSrc(p, k)     // single source k → {gb, q}
personDailySrc(p, k)   // daily array for one source, converted via mv()
personDailyFiltered(p) // daily array summed over S.src, converted via mv()
mv(gb, q)              // converts to current metric (tb/cost/q)
```

### Chart instances

`cTrend`, `cPareto`, `cDrill`, `cCompare` — all Chart.js 4.4.1. Each is destroyed and recreated on every render call via `destroy(c)`.

---

## 6. Colours & design tokens

```
Background:    #eef0f3
Surface:       #ffffff
Primary blue:  #2350b8
Muted text:    #697080
Faint text:    #9aa1ad
Border:        #e3e6eb
Border strong: #cdd2da
Grid lines:    #eef0f3

Source colours:
  Looker Studio: #3b82c4
  BQ console:    #1d9e75
  Script:        #d8643a
  SA-Cron:       #878d98

Compare palette (10 colours):
  #2350b8 #d8643a #1d9e75 #9b59b6 #e67e22
  #c0392b #16a085 #8e44ad #2980b9 #f39c12

SA leaderboard pill:   background #f3eeff, colour #7c3aed
Human leaderboard pill: background #e8f5e9, colour #1d7a4a

Fonts: Inter (UI), IBM Plex Mono (numbers/code)
```

---

## 7. Build process (how to refresh the dashboard)

1. Export fresh data from BigQuery using the same query (same schema as §2).
2. Run the Python build script (same logic as §4) to produce `data.json`.
3. Replace `__DATA__` placeholder in `index_template.html` with the JSON string.
4. Deploy `index.html` to GitHub Pages.

One-liner once script is packaged:
```bash
python build.py new_export.csv > index.html
```

The `build.py` script does not yet exist as a standalone file — it is embedded as inline Python in the conversation. It should be extracted and parameterised if automation is needed.

---

## 8. GitHub Pages deployment

1. Create repo (e.g. `bq-usage`), commit `index.html` to root of `main` branch.
2. Repo → Settings → Pages → Source: `Deploy from a branch`, branch `main`, folder `/ (root)`.
3. Live at `https://<org>.github.io/bq-usage/` within ~1 minute.
4. To refresh: regenerate `index.html` via build script, commit, push.

---

## 9. Known limitations & caveats

- **No query text or table names** in source data — can show cost/volume but not why a query was expensive.
- **`SA: bigquery-admin` cron jobs (~180 TB)** cannot be attributed to any individual — they carry no alias.
- **Cost is an estimate** — $6.25/TB is US multi-region on-demand (2026). The 1 TiB/month free tier is not deducted. Adjust rate input for your region/edition.
- **Top 40 queries only** — the `DATA.topq` array holds only the 40 heaviest individual jobs from the export. It is not a full job log.
- **Static snapshot** — data covers May 14–Jun 22 2026 only. Refresh requires a new export + rebuild.
- **`devs` alias = Sourav** — this was a one-time temp query. If it appears in future exports it still maps to Sourav.
- **`harkeerat`, `avipsa`, `shugal`** — script-only users, no console or Looker activity, no email in the data. They are real individuals confirmed by the team.

---

## 10. Potential next features

These were discussed but not built:

| Feature | Complexity | Notes |
|---------|-----------|-------|
| `build.py` standalone script | Low | Extract inline Python from conversation, add CLI args |
| Load external `data.json` instead of inline | Low | Avoids rebuilding HTML; just swap JSON file |
| Hour-of-day heatmap | Medium | `time` column is in the CSV but not used |
| Day-of-week pattern | Low | Derivable from `date` column |
| Cumulative spend line + month-end projection | Low | Add a running-total dataset to trend chart |
| Side-by-side two-person compare | Medium | Separate view from the multi-line compare |
| Query text / table name analysis | High | Requires a different BQ export schema with `query` and `referenced_tables` fields |
| Slot time / duration analysis | High | Requires `total_slot_ms` and `total_bytes_billed` in the export |
| Automated refresh via GitHub Actions | Medium | Scheduled BQ export → build script → commit |
