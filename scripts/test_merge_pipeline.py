#!/usr/bin/env python3
"""Test init/merge pipeline with synthetic payloads."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_build(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    config = ROOT / "config"
    return subprocess.run(
        [sys.executable, str(ROOT / "build.py"), *args, "--config", str(config)],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def sample_payload(days: list[str], gb: float = 10.0) -> dict:
    daily = []
    hourly = []
    for d in days:
        daily.append({"date": d, "person": "Venkatesh", "source": 1, "gb": gb, "q": 2})
        hourly.append({"date": d, "person": "Venkatesh", "hour": 10, "gb": gb})
    return {
        "window_start": days[0],
        "window_end": days[-1],
        "daily": daily,
        "hourly": hourly,
        "top_jobs": [
            {
                "job_id": f"job-{days[-1]}",
                "date": days[-1],
                "person": "Venkatesh",
                "src": "Console",
                "gb": gb * 2,
            }
        ],
        "unmapped": [],
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        td = Path(tmp)
        p1 = td / "bootstrap.json"
        p2 = td / "increment.json"
        p3 = td / "overwrite.json"

        p1.write_text(json.dumps(sample_payload(["2026-06-01", "2026-06-02"], 10)))
        p2.write_text(json.dumps(sample_payload(["2026-06-03"], 5)))
        p3.write_text(json.dumps(sample_payload(["2026-06-02"], 99)))

        r = run_build(["--init", "--from-bq", str(p1), "--output", "data.json", "--meta", "meta.json", "--facts", "facts.json"], td)
        if r.returncode != 0:
            print(r.stderr or r.stdout)
            raise SystemExit("init failed")

        meta = json.loads((td / "meta.json").read_text())
        assert meta["window_days"] == 2
        assert meta["last_data_date"] == "2026-06-02"

        r = run_build(["--merge", "--from-bq", str(p2), "--output", "data.json", "--meta", "meta.json", "--facts", "facts.json"], td)
        if r.returncode != 0:
            print(r.stderr or r.stdout)
            raise SystemExit("merge failed")
        assert "2026-06-03" in r.stdout
        meta = json.loads((td / "meta.json").read_text())
        assert meta["window_days"] == 3
        assert meta["last_data_date"] == "2026-06-03"

        data_before = json.loads((td / "data.json").read_text())
        venk_gb_before = data_before["totals"]["Venkatesh"]["gb"]

        r = run_build(["--merge", "--from-bq", str(p3), "--output", "data.json", "--meta", "meta.json", "--facts", "facts.json"], td)
        if r.returncode != 0:
            print(r.stderr or r.stdout)
            raise SystemExit("overwrite failed")
        assert "Dates overwritten" in r.stdout
        meta = json.loads((td / "meta.json").read_text())
        assert meta["last_merge"]["dates_overwritten"] == ["2026-06-02"]

        data_after = json.loads((td / "data.json").read_text())
        venk_gb_after = data_after["totals"]["Venkatesh"]["gb"]
        assert venk_gb_after > venk_gb_before

        r = run_build(["--status", "--meta", "meta.json"], td)
        assert "last_data_date: 2026-06-03" in r.stdout

    with tempfile.TemporaryDirectory() as tmp2:
        td2 = Path(tmp2)
        payload = sample_payload(["2026-06-10"], 7)
        jsonl_path = td2 / "payload.jsonl"
        jsonl_path.write_text(
            json.dumps({"payload": json.dumps(payload)}) + "\n"
        )
        r = run_build(
            [
                "--init",
                "--from-bq",
                str(jsonl_path),
                "--output",
                "data.json",
                "--meta",
                "meta.json",
                "--facts",
                "facts.json",
            ],
            td2,
        )
        if r.returncode != 0:
            print(r.stderr or r.stdout)
            raise SystemExit("jsonl init failed")
        meta = json.loads((td2 / "meta.json").read_text())
        assert meta["last_data_date"] == "2026-06-10"

    print("Pipeline tests passed.")


if __name__ == "__main__":
    main()
