#!/usr/bin/env python3
"""Build data.json and meta.json from a BigQuery INFORMATION_SCHEMA.JOBS CSV export."""

from __future__ import annotations

import argparse
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


def load_config(config_dir: Path) -> tuple[dict, dict, dict]:
    idmap = json.loads((config_dir / "idmap.json").read_text())
    sa_names = json.loads((config_dir / "sa_names.json").read_text())
    project = json.loads((config_dir / "project.json").read_text())
    return idmap, sa_names, project


def fmt_short_date(d: datetime) -> str:
    return f"{d.strftime('%b')} {d.day}"


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


def detect_anomalies(
    people_daily: dict[str, list[list[float]]],
    dates: list[str],
    raw_dates: list[datetime],
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


def build_data(
    csv_path: Path,
    config_dir: Path,
    days: int,
    top_n: int,
) -> tuple[dict, dict, list[str]]:
    idmap, sa_names, project = load_config(config_dir)
    aliases = idmap["aliases"]
    emails = {k.lower(): v for k, v in idmap["emails"].items()}

    df = pd.read_csv(csv_path, dtype=str)
    df.columns = [c.strip().lower() for c in df.columns]

    required = {"job_id", "date", "query_requested_by", "gb_scanned"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"CSV missing columns: {sorted(missing)}")

    if "time" not in df.columns:
        df["time"] = ""
    if "user_email" not in df.columns:
        df["user_email"] = ""

    df["gb_scanned"] = pd.to_numeric(df["gb_scanned"], errors="coerce").fillna(0.0)
    df["date_parsed"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date_parsed"])

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

    # person -> day_idx -> 8-slot row
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

    anomalies = detect_anomalies(people_out, dates, raw_dates)

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

    total_gb = sum(t["gb"] for t in totals.values())
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": raw_dates[0].strftime("%Y-%m-%d"),
        "window_end": raw_dates[-1].strftime("%Y-%m-%d"),
        "window_days": len(dates),
        "row_count": int(len(df)),
        "total_tb": round(total_gb / 1024, 2),
        "people_count": len(people_out),
        "unmapped": [{"key": k, "count": v} for k, v in unmapped.most_common(10)],
        "unmapped_total": int(sum(unmapped.values())),
        "anomalies": anomalies,
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
            f"Anomaly: {a['person']} on {a['date']} scanned {a['gb']} GB "
            f"({a['ratio']}× median)"
        )

    return data, meta, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Build BigQuery usage dashboard JSON from CSV export.")
    parser.add_argument("--csv", type=Path, required=True, help="Path to BQ export CSV")
    parser.add_argument("--days", type=int, default=90, help="Rolling window in days (default: 90)")
    parser.add_argument("--output", type=Path, default=Path("data.json"), help="Output data.json path")
    parser.add_argument("--meta", type=Path, default=Path("meta.json"), help="Output meta.json path")
    parser.add_argument("--config", type=Path, default=Path("config"), help="Config directory")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Top heaviest queries to keep")
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    data, meta, warnings = build_data(args.csv, args.config, args.days, args.top_n)

    args.output.write_text(json.dumps(data, separators=(",", ":")))
    args.meta.write_text(json.dumps(meta, indent=2) + "\n")

    print(f"Wrote {args.output} ({args.output.stat().st_size // 1024} KB)")
    print(f"Wrote {args.meta}")
    print(
        f"Window: {meta['window_start']} → {meta['window_end']} "
        f"({meta['window_days']} days, {meta['total_tb']} TB, {meta['row_count']} rows)"
    )
    for w in warnings:
        print(f"WARNING: {w}", file=sys.stderr)


if __name__ == "__main__":
    main()
