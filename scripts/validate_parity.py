#!/usr/bin/env python3
"""Compare totals in two data.json files for build parity checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def load_totals(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text())
    return data.get("totals", {})


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate data.json totals match a baseline.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--built", type=Path, required=True)
    parser.add_argument("--tolerance-gb", type=float, default=0.2, help="Max GB diff per person")
    args = parser.parse_args()

    base = load_totals(args.baseline)
    built = load_totals(args.built)

    all_people = sorted(set(base) | set(built))
    mismatches = []

    for person in all_people:
        b = base.get(person, {})
        n = built.get(person, {})
        for field in ("gb", "q", "looker_q", "direct_q"):
            bv = b.get(field, 0)
            nv = n.get(field, 0)
            if field == "gb":
                if abs(bv - nv) > args.tolerance_gb:
                    mismatches.append(f"{person}.{field}: baseline={bv} built={nv}")
            elif bv != nv:
                mismatches.append(f"{person}.{field}: baseline={bv} built={nv}")

    missing_base = sorted(set(built) - set(base))
    missing_built = sorted(set(base) - set(built))

    if missing_base:
        mismatches.append(f"Only in built: {missing_base[:5]}{'…' if len(missing_base)>5 else ''}")
    if missing_built:
        mismatches.append(f"Only in baseline: {missing_built[:5]}{'…' if len(missing_built)>5 else ''}")

    base_tb = sum(t.get("gb", 0) for t in base.values()) / 1024
    built_tb = sum(t.get("gb", 0) for t in built.values()) / 1024

    print(f"Baseline: {len(base)} people, {base_tb:.2f} TB")
    print(f"Built:    {len(built)} people, {built_tb:.2f} TB")

    if mismatches:
        print(f"\n{len(mismatches)} mismatch(es):", file=sys.stderr)
        for m in mismatches[:30]:
            print(f"  - {m}", file=sys.stderr)
        raise SystemExit(1)

    print("Parity check passed.")


if __name__ == "__main__":
    main()
