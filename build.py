#!/usr/bin/env python3
"""Build data.json and meta.json from BQ aggregated payloads or legacy CSV exports."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SRC_LABELS = {0: "Looker", 1: "Console", 2: "Script", 3: "SA-Cron"}
DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULT_TOP_N = 100
ANOMALY_MULTIPLIER = 5.0
FACTS_VERSION = 1


def load_config(config_dir: Path) -> tuple[dict, dict, dict]:
    idmap = json.loads((config_dir / "idmap.json").read_text())
    sa_names = json.loads((config_dir / "sa_names.json").read_text())
    project = json.loads((config_dir / "project.json").read_text())
    return idmap, sa_names, project


def fmt_short_date(d: datetime) -> str:
    return f"{d.strftime('%b')} {d.day}"


def normalize_date(value: str) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def classify_source(qrb: str) -> int:
    qrb = (qrb or "").strip()
    if qrb == "looker_studio":
        return 0
    if "@hitwicket.com" in qrb:
        return 1
    if "gserviceaccount.com" in qrb:
        return 3
    return 2


def resolve_person(
    qrb: str,
    user_email: str,
    aliases: dict[str, str],
    emails: dict[str, str],
    sa_names: dict[str, str],
) -> str | None:
    qrb = (qrb or "").strip()
    user_email = (user_email or "").strip().lower()

    if qrb in sa_names:
        return sa_names[qrb]

    if qrb == "looker_studio":
        return emails.get(user_email)

    if "gserviceaccount.com" in qrb:
        return sa_names.get(qrb, f"SA: {qrb.split('@')[0]}")

    if "@hitwicket.com" in qrb:
        return emails.get(qrb.lower())

    person = aliases.get(qrb.lower())
    if person:
        return person

    return emails.get(user_email)


def derive_query_requested_by(df: pd.DataFrame) -> pd.Series:
    n = len(df)
    empty = pd.Series([""] * n, index=df.index)

    def col(name: str) -> pd.Series:
        if name not in df.columns:
            return empty.copy()
        return df[name].fillna("").astype(str).str.strip().replace("nan", "")

    requestor = col("requestor")
    queried_by = col("queried_by")
    user_email = col("user_email")
    derived = requestor.where(requestor != "", queried_by.where(queried_by != "", user_email))

    if "query_requested_by" in df.columns:
        qrb = col("query_requested_by")
        return qrb.where(qrb != "", derived)
    return derived


def prepare_jobs_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]

    identity_cols = {"query_requested_by", "requestor", "queried_by", "user_email"}
    if not identity_cols.intersection(df.columns):
        raise SystemExit(
            "CSV needs query_requested_by or at least one of: requestor, queried_by, user_email"
        )

    required = {"job_id", "date", "gb_scanned"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"CSV missing columns: {sorted(missing)}")

    if "time" not in df.columns:
        df["time"] = ""
    if "user_email" not in df.columns:
        df["user_email"] = ""

    df["query_requested_by"] = derive_query_requested_by(df)
    df["gb_scanned"] = pd.to_numeric(df["gb_scanned"], errors="coerce").fillna(0.0)
    df = df[df["gb_scanned"] > 0]

    df["date_parsed"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date_parsed"])
    return df


def parse_hour(time_val) -> int | None:
    if pd.isna(time_val):
        return None
    s = str(time_val).strip()
    if not s:
        return None
    for fmt in ("%H:%M:%S", "%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt).hour
        except ValueError:
            continue
    parts = s.split(":")
    if parts and parts[0].isdigit():
        h = int(parts[0])
        return h if 0 <= h <= 23 else None
    return None


def empty_day_row() -> list[float]:
    return [0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0]


def empty_facts() -> dict:
    return {"version": FACTS_VERSION, "daily": [], "hourly": [], "top_jobs": []}


def load_facts(path: Path) -> dict:
    if not path.exists():
        return empty_facts()
    facts = json.loads(path.read_text())
    if facts.get("version") != FACTS_VERSION:
        raise SystemExit(f"Unsupported facts.json version: {facts.get('version')}")
    for key in ("daily", "hourly", "top_jobs"):
        facts.setdefault(key, [])
    return facts


def save_facts(path: Path, facts: dict) -> None:
    path.write_text(json.dumps(facts, indent=2) + "\n")


def load_payload(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"Payload not found: {path}")

    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as fh:
            row = next(csv.reader(fh), None)
            if not row:
                raise SystemExit(f"Empty CSV payload: {path}")
            raw = row[0].strip()
        if raw.startswith("{"):
            data = json.loads(raw)
        else:
            raise SystemExit("CSV payload first cell must be a JSON object or payload string")
    else:
        data = json.loads(path.read_text())

    if isinstance(data, str):
        data = json.loads(data)

    if "payload" in data and isinstance(data["payload"], str):
        data = json.loads(data["payload"])

    if "daily" not in data:
        raise SystemExit("Payload missing required field: daily")

    for forbidden in ("query", "qtext"):
        if forbidden in data:
            raise SystemExit(f"Payload must not contain {forbidden!r}")

    data.setdefault("hourly", [])
    data.setdefault("top_jobs", [])
    data.setdefault("unmapped", [])
    return data


def dates_in_facts(facts: dict) -> set[str]:
    dates: set[str] = set()
    for section in ("daily", "hourly"):
        for row in facts.get(section, []):
            dates.add(normalize_date(row["date"]))
    return dates


def dates_in_payload(payload: dict) -> set[str]:
    dates: set[str] = set()
    for section in ("daily", "hourly"):
        for row in payload.get(section, []):
            dates.add(normalize_date(row["date"]))
    for row in payload.get("top_jobs", []):
        dates.add(normalize_date(row["date"]))
    return dates


def init_facts_from_payload(payload: dict, top_n: int) -> dict:
    top_jobs = sorted(payload.get("top_jobs", []), key=lambda x: float(x.get("gb", 0)), reverse=True)
    return {
        "version": FACTS_VERSION,
        "daily": list(payload.get("daily", [])),
        "hourly": list(payload.get("hourly", [])),
        "top_jobs": top_jobs[:top_n],
    }


def merge_facts(existing: dict, payload: dict, top_n: int) -> tuple[dict, list[str], list[str], list[str]]:
    payload_dates = dates_in_payload(payload)
    existing_dates = dates_in_facts(existing)
    dates_added = sorted(d for d in payload_dates if d not in existing_dates)
    dates_overwritten = sorted(d for d in payload_dates if d in existing_dates)
    dates_in_payload_sorted = sorted(payload_dates)

    for section in ("daily", "hourly"):
        existing[section] = [
            row for row in existing[section] if normalize_date(row["date"]) not in payload_dates
        ]
        existing[section].extend(payload.get(section, []))

    by_job: dict[str, dict] = {row["job_id"]: row for row in existing.get("top_jobs", [])}
    for row in payload.get("top_jobs", []):
        job_id = row["job_id"]
        if job_id not in by_job or float(row.get("gb", 0)) > float(by_job[job_id].get("gb", 0)):
            by_job[job_id] = row
    existing["top_jobs"] = sorted(by_job.values(), key=lambda x: float(x.get("gb", 0)), reverse=True)[:top_n]
    return existing, dates_added, dates_overwritten, dates_in_payload_sorted


def detect_anomalies(
    people_daily: dict[str, list[list[float]]],
    dates: list[str],
) -> list[dict]:
    alerts: list[dict] = []
    for person, rows in people_daily.items():
        daily_gb = [sum(r[k] for k in range(4)) for r in rows]
        positive = [g for g in daily_gb if g > 0]
        if len(positive) < 3:
            continue
        sorted_gb = sorted(positive)
        median = sorted_gb[len(sorted_gb) // 2]
        if median <= 0:
            continue
        threshold = median * ANOMALY_MULTIPLIER
        for i, gb in enumerate(daily_gb):
            if gb > threshold and gb >= 10:
                alerts.append(
                    {
                        "person": person,
                        "date": dates[i],
                        "gb": round(gb, 1),
                        "median_gb": round(median, 1),
                        "ratio": round(gb / median, 1),
                    }
                )
    alerts.sort(key=lambda x: x["gb"], reverse=True)
    return alerts[:20]


def facts_to_data(facts: dict, project: dict, top_n: int) -> tuple[dict, dict, list[str]]:
    all_date_strs: set[str] = dates_in_facts(facts)
    for row in facts.get("top_jobs", []):
        all_date_strs.add(normalize_date(row["date"]))

    if not all_date_strs:
        raise SystemExit("No dates in facts — upload a payload with daily/hourly data.")

    raw_dates = sorted(pd.Timestamp(d).to_pydatetime() for d in all_date_strs)
    date_index = {pd.Timestamp(d).normalize(): i for i, d in enumerate(raw_dates)}
    dates = [fmt_short_date(d) for d in raw_dates]

    people: set[str] = set()
    sa_set: set[str] = set()
    grid: dict[str, list[list[float]]] = defaultdict(lambda: [empty_day_row() for _ in raw_dates])
    hourly: dict[str, list[float]] = defaultdict(lambda: [0.0] * 24)
    dow: dict[str, list[float]] = defaultdict(lambda: [0.0] * 7)
    team_hourly = [0.0] * 24
    team_dow = [0.0] * 7

    for row in facts.get("daily", []):
        person = row["person"]
        source = int(row["source"])
        gb = float(row.get("gb", 0))
        q = int(row.get("q", 0))
        day = pd.Timestamp(normalize_date(row["date"])).normalize()
        di = date_index[day]
        people.add(person)
        if person.startswith("SA:"):
            sa_set.add(person)
        slot = grid[person][di]
        slot[source] += gb
        slot[4 + source] += q

    for row in facts.get("hourly", []):
        person = row["person"]
        hour = int(row["hour"])
        gb = float(row.get("gb", 0))
        if not 0 <= hour <= 23:
            continue
        people.add(person)
        hourly[person][hour] += gb
        team_hourly[hour] += gb

    for row in facts.get("daily", []):
        person = row["person"]
        day = pd.Timestamp(normalize_date(row["date"]))
        gb = float(row.get("gb", 0))
        dow_idx = day.dayofweek
        dow[person][dow_idx] += gb
        team_dow[dow_idx] += gb

    top_jobs: list[dict] = []
    for row in sorted(facts.get("top_jobs", []), key=lambda x: float(x.get("gb", 0)), reverse=True)[:top_n]:
        day = pd.Timestamp(normalize_date(row["date"])).normalize()
        di = date_index.get(day)
        if di is None:
            continue
        top_jobs.append(
            {
                "job": row["job_id"],
                "di": di,
                "p": row["person"],
                "src": row.get("src", "Script"),
                "gb": round(float(row.get("gb", 0)), 1),
            }
        )

    people_out = {p: grid[p] for p in sorted(people)}
    totals: dict[str, dict] = {}
    for person, rows in people_out.items():
        gb = sum(sum(r[k] for k in range(4)) for r in rows)
        q = sum(sum(r[4 + k] for k in range(4)) for r in rows)
        looker_q = sum(r[4] for r in rows)
        totals[person] = {
            "gb": round(gb, 1),
            "q": int(q),
            "looker_q": int(looker_q),
            "direct_q": int(q - looker_q),
        }

    anomalies = detect_anomalies(people_out, dates)
    total_gb = sum(t["gb"] for t in totals.values())

    data = {
        "dates": dates,
        "people": people_out,
        "totals": totals,
        "topq": top_jobs,
        "sa_list": sorted(sa_set),
        "hourly": {p: [round(v, 2) for v in hourly[p]] for p in sorted(hourly)},
        "dow": {p: [round(v, 2) for v in dow[p]] for p in sorted(dow)},
        "team_hourly": [round(v, 2) for v in team_hourly],
        "team_dow": [round(v, 2) for v in team_dow],
        "dow_labels": DOW_LABELS,
        "gcp_project_id": project.get("gcp_project_id", "hitwicketsuperstars"),
    }

    meta_base = {
        "window_start": raw_dates[0].strftime("%Y-%m-%d"),
        "window_end": raw_dates[-1].strftime("%Y-%m-%d"),
        "last_data_date": raw_dates[-1].strftime("%Y-%m-%d"),
        "window_days": len(dates),
        "total_tb": round(total_gb / 1024, 2),
        "people_count": len(people_out),
        "anomalies": anomalies,
        "source": "bq_aggregated",
    }
    warnings: list[str] = []
    if anomalies:
        a = anomalies[0]
        warnings.append(
            f"Anomaly: {a['person']} on {a['date']} scanned {a['gb']} GB ({a['ratio']}× median)"
        )
    return data, meta_base, warnings


def build_meta(
    meta_base: dict,
    payload: dict | None,
    payload_path: Path | None,
    merge_info: dict | None,
    unmapped: list[dict],
) -> dict:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        **meta_base,
        "generated_at": now,
        "last_refresh_at": now,
        "unmapped": unmapped[:10],
        "unmapped_total": int(sum(u.get("count", 0) for u in unmapped)),
    }
    if payload_path:
        meta["source_payload"] = payload_path.name
    if merge_info:
        meta["last_merge"] = merge_info
    if payload and payload.get("window_start"):
        meta["payload_window_start"] = payload["window_start"]
    if payload and payload.get("window_end"):
        meta["payload_window_end"] = payload["window_end"]
    return meta


def run_from_facts_pipeline(
    facts: dict,
    payload: dict | None,
    payload_path: Path | None,
    merge_info: dict | None,
    config_dir: Path,
    top_n: int,
) -> tuple[dict, dict, list[str]]:
    _, _, project = load_config(config_dir)
    data, meta_base, warnings = facts_to_data(facts, project, top_n)
    unmapped = payload.get("unmapped", []) if payload else []
    meta = build_meta(meta_base, payload, payload_path, merge_info, unmapped)
    if unmapped:
        top = unmapped[:3]
        warnings.append(
            "Unmapped identities in payload: "
            + ", ".join(f"{u.get('key')!r} ({u.get('count')})" for u in top)
        )
    return data, meta, warnings


def build_data_from_csv(
    csv_path: Path,
    config_dir: Path,
    days: int,
    top_n: int,
) -> tuple[dict, dict, list[str]]:
    idmap, sa_names, project = load_config(config_dir)
    aliases = idmap["aliases"]
    emails = {k.lower(): v for k, v in idmap["emails"].items()}

    df = prepare_jobs_frame(pd.read_csv(csv_path, dtype=str))
    if df.empty:
        raise SystemExit("No valid rows after parsing dates.")

    max_date = df["date_parsed"].max()
    min_date = max_date - pd.Timedelta(days=days - 1)
    df = df[(df["date_parsed"] >= min_date) & (df["date_parsed"] <= max_date)]

    all_dates = sorted(df["date_parsed"].dt.normalize().unique())
    date_index = {d: i for i, d in enumerate(all_dates)}
    raw_dates = [pd.Timestamp(d).to_pydatetime() for d in all_dates]
    dates = [fmt_short_date(d) for d in raw_dates]

    unmapped: Counter[str] = Counter()
    people: set[str] = set()
    sa_set: set[str] = set()
    grid: dict[str, list[list[float]]] = defaultdict(lambda: [empty_day_row() for _ in all_dates])
    hourly: dict[str, list[float]] = defaultdict(lambda: [0.0] * 24)
    dow: dict[str, list[float]] = defaultdict(lambda: [0.0] * 7)
    team_hourly = [0.0] * 24
    team_dow = [0.0] * 7
    top_jobs: list[dict] = []

    for row in df.itertuples(index=False):
        qrb = getattr(row, "query_requested_by", "") or ""
        user_email = getattr(row, "user_email", "") or ""
        job_id = getattr(row, "job_id", "") or ""
        gb = float(getattr(row, "gb_scanned", 0) or 0)
        day = pd.Timestamp(getattr(row, "date_parsed")).normalize()
        di = date_index[day]
        time_val = getattr(row, "time", "")

        person = resolve_person(qrb, user_email, aliases, emails, sa_names)
        if not person:
            unmapped[qrb or "(empty)"] += 1
            continue

        src = classify_source(qrb)
        people.add(person)
        if person.startswith("SA:"):
            sa_set.add(person)

        slot = grid[person][di]
        slot[src] += gb
        slot[4 + src] += 1

        hour = parse_hour(time_val)
        if hour is not None:
            hourly[person][hour] += gb
            team_hourly[hour] += gb

        dow_idx = day.dayofweek
        dow[person][dow_idx] += gb
        team_dow[dow_idx] += gb

        top_jobs.append(
            {
                "job": job_id,
                "di": di,
                "p": person,
                "src": SRC_LABELS[src],
                "gb": round(gb, 1),
            }
        )

    top_jobs.sort(key=lambda x: x["gb"], reverse=True)
    top_jobs = top_jobs[:top_n]
    people_out = {p: grid[p] for p in sorted(people)}

    totals: dict[str, dict] = {}
    for person, rows in people_out.items():
        gb = sum(sum(r[k] for k in range(4)) for r in rows)
        q = sum(sum(r[4 + k] for k in range(4)) for r in rows)
        looker_q = sum(r[4] for r in rows)
        totals[person] = {
            "gb": round(gb, 1),
            "q": int(q),
            "looker_q": int(looker_q),
            "direct_q": int(q - looker_q),
        }

    anomalies = detect_anomalies(people_out, dates)
    total_gb = sum(t["gb"] for t in totals.values())

    data = {
        "dates": dates,
        "people": people_out,
        "totals": totals,
        "topq": top_jobs,
        "sa_list": sorted(sa_set),
        "hourly": {p: [round(v, 2) for v in hourly[p]] for p in sorted(hourly)},
        "dow": {p: [round(v, 2) for v in dow[p]] for p in sorted(dow)},
        "team_hourly": [round(v, 2) for v in team_hourly],
        "team_dow": [round(v, 2) for v in team_dow],
        "dow_labels": DOW_LABELS,
        "gcp_project_id": project.get("gcp_project_id", "hitwicketsuperstars"),
    }

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {
        "generated_at": now,
        "last_refresh_at": now,
        "last_data_date": raw_dates[-1].strftime("%Y-%m-%d"),
        "window_start": raw_dates[0].strftime("%Y-%m-%d"),
        "window_end": raw_dates[-1].strftime("%Y-%m-%d"),
        "window_days": len(dates),
        "row_count": int(len(df)),
        "total_tb": round(total_gb / 1024, 2),
        "people_count": len(people_out),
        "unmapped": [{"key": k, "count": v} for k, v in unmapped.most_common(10)],
        "unmapped_total": int(sum(unmapped.values())),
        "anomalies": anomalies,
        "source": "csv_jobs",
        "source_csv": csv_path.name,
    }

    warnings: list[str] = []
    if unmapped:
        top = unmapped.most_common(3)
        warnings.append(
            "Unmapped query_requested_by values: "
            + ", ".join(f"{k!r} ({v})" for k, v in top)
        )
    if anomalies:
        a = anomalies[0]
        warnings.append(
            f"Anomaly: {a['person']} on {a['date']} scanned {a['gb']} GB ({a['ratio']}× median)"
        )
    return data, meta, warnings


def print_status(meta_path: Path) -> None:
    if not meta_path.exists():
        print("No meta.json found — run --init with your first payload.")
        raise SystemExit(0)

    meta = json.loads(meta_path.read_text())
    last = meta.get("last_data_date") or meta.get("window_end") or "—"
    start = meta.get("window_start", "—")
    end = meta.get("window_end", "—")
    days = meta.get("window_days", "—")
    refreshed = meta.get("last_refresh_at") or meta.get("generated_at") or "—"
    source = meta.get("source", "—")

    print(f"last_data_date: {last}")
    print(f"window:         {start} → {end} ({days} days)")
    print(f"last_refresh:   {refreshed}")
    print(f"source:         {source}")
    print(f"total_tb:       {meta.get('total_tb', '—')}")
    print(f"people_count:   {meta.get('people_count', '—')}")

    merge = meta.get("last_merge")
    if merge:
        print(f"last_merge:     {merge.get('payload_file', '—')}")
        if merge.get("dates_added"):
            print(f"  dates_added:       {', '.join(merge['dates_added'])}")
        if merge.get("dates_overwritten"):
            print(f"  dates_overwritten: {', '.join(merge['dates_overwritten'])}")


def write_outputs(
    data: dict,
    meta: dict,
    warnings: list[str],
    output: Path,
    meta_path: Path,
    facts: dict | None,
    facts_path: Path | None,
) -> None:
    output.write_text(json.dumps(data, separators=(",", ":")))
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    if facts is not None and facts_path is not None:
        save_facts(facts_path, facts)

    print(f"Wrote {output} ({output.stat().st_size // 1024} KB)")
    print(f"Wrote {meta_path}")
    if facts is not None and facts_path is not None:
        print(f"Wrote {facts_path}")
    print(
        f"Window: {meta['window_start']} → {meta['window_end']} "
        f"({meta['window_days']} days, {meta['total_tb']} TB)"
    )
    print(f"last_data_date: {meta.get('last_data_date')}")
    merge = meta.get("last_merge")
    if merge:
        added = merge.get("dates_added") or []
        overwritten = merge.get("dates_overwritten") or []
        print(f"Dates added:       {', '.join(added) if added else '(none)'}")
        print(f"Dates overwritten: {', '.join(overwritten) if overwritten else '(none)'}")
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build BigQuery usage dashboard JSON from aggregated payloads or CSV."
    )
    parser.add_argument("--status", action="store_true", help="Show last data date and window")
    parser.add_argument("--init", action="store_true", help="Replace facts with payload (bootstrap)")
    parser.add_argument("--merge", action="store_true", help="Merge payload into existing facts")
    parser.add_argument("--from-bq", type=Path, help="Aggregated BQ payload (.json or .csv)")
    parser.add_argument("--csv", type=Path, help="Legacy job-level CSV export (debug)")
    parser.add_argument("--days", type=int, default=40, help="Rolling window for --csv (default: 40)")
    parser.add_argument("--output", type=Path, default=Path("data.json"))
    parser.add_argument("--meta", type=Path, default=Path("meta.json"))
    parser.add_argument("--facts", type=Path, default=Path("facts.json"))
    parser.add_argument("--config", type=Path, default=Path("config"))
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    args = parser.parse_args()

    if args.status:
        print_status(args.meta)
        return

    if args.init and args.merge:
        raise SystemExit("Use only one of --init or --merge")

    if args.from_bq:
        payload = load_payload(args.from_bq)
        if args.init:
            facts = init_facts_from_payload(payload, args.top_n)
            merge_info = {
                "payload_file": args.from_bq.name,
                "dates_added": sorted(dates_in_payload(payload)),
                "dates_overwritten": [],
                "dates_in_payload": sorted(dates_in_payload(payload)),
            }
        elif args.merge:
            facts = load_facts(args.facts)
            facts, dates_added, dates_overwritten, dates_in_payload_list = merge_facts(
                facts, payload, args.top_n
            )
            merge_info = {
                "payload_file": args.from_bq.name,
                "dates_added": dates_added,
                "dates_overwritten": dates_overwritten,
                "dates_in_payload": dates_in_payload_list,
            }
        else:
            raise SystemExit("With --from-bq, specify --init (first load) or --merge (incremental)")

        data, meta, warnings = run_from_facts_pipeline(
            facts, payload, args.from_bq, merge_info, args.config, args.top_n
        )
        write_outputs(data, meta, warnings, args.output, args.meta, facts, args.facts)
        return

    if args.csv:
        data, meta, warnings = build_data_from_csv(args.csv, args.config, args.days, args.top_n)
        write_outputs(data, meta, warnings, args.output, args.meta, None, None)
        return

    parser.print_help()
    raise SystemExit(2)


if __name__ == "__main__":
    main()
