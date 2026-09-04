#!/usr/bin/env python3
"""Read-only localization of settled V3 contract-type probability error.

Consumes the complete settled-V3 cohort CSV produced by the Accuracy Lab and writes
reproducible research-only cross-tabs. This script never mutates trades or production
state and never mixes OFFICIAL/UNCLASSIFIED into the principal RESEARCH_ONLY cohort.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

REQUIRED = {
    "actual",
    "city",
    "contract_type",
    "disagreement_bucket",
    "eligibility_class",
    "event_date",
    "event_key",
    "lead_time_bucket",
    "market_bucket",
    "market_prob",
    "model_prob",
    "partition",
    "probability_bucket",
}


def _f(value: str) -> float:
    return float(value)


def _metrics(rows: list[dict[str, str]]) -> dict[str, object]:
    if not rows:
        return {
            "n": 0,
            "event_n": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": None,
            "mean_model_prob": None,
            "realized_rate": None,
            "calibration_error_abs": None,
            "v3_brier": None,
            "kalshi_brier": None,
        }
    actual = [_f(r["actual"]) for r in rows]
    model = [_f(r["model_prob"]) for r in rows]
    market = [_f(r["market_prob"]) for r in rows]
    realized = mean(actual)
    model_mean = mean(model)
    wins = int(sum(actual))
    return {
        "n": len(rows),
        "event_n": len({r["event_key"] for r in rows}),
        "wins": wins,
        "losses": len(rows) - wins,
        "win_rate": realized,
        "mean_model_prob": model_mean,
        "realized_rate": realized,
        "calibration_error_abs": abs(model_mean - realized),
        "v3_brier": mean((p - y) ** 2 for p, y in zip(model, actual)),
        "kalshi_brier": mean((p - y) ** 2 for p, y in zip(market, actual)),
    }


def _cross(rows: list[dict[str, str]], dimension: str) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[(row["partition"], row["contract_type"], row[dimension])].append(row)
    result: list[dict[str, object]] = []
    for (partition, contract_type, value), group in sorted(grouped.items()):
        item = {
            "partition": partition,
            "contract_type": contract_type,
            "dimension": dimension,
            "value": value,
            **_metrics(group),
        }
        item["insufficient_n"] = item["n"] < 10 or item["event_n"] < 5
        result.append(item)
    return result


def build_report(rows: list[dict[str, str]]) -> dict[str, object]:
    counts = defaultdict(int)
    for row in rows:
        counts[row["eligibility_class"]] += 1

    research = [r for r in rows if r["eligibility_class"] == "RESEARCH_ONLY"]
    disagreement_20pp = [r for r in research if r["disagreement_bucket"] == "20pp+"]

    by_contract = []
    for contract_type in sorted({r["contract_type"] for r in research}):
        group = [r for r in research if r["contract_type"] == contract_type]
        by_contract.append({"contract_type": contract_type, **_metrics(group)})

    return {
        "classification": "RESEARCH_ONLY_READ_ONLY_DIAGNOSTIC",
        "protected_semantic_change_activated": False,
        "real_money_execution_enabled": False,
        "evidence_class_counts": dict(counts),
        "principal_population": _metrics(research),
        "contract_type": by_contract,
        "disagreement_20pp_plus": _metrics(disagreement_20pp),
        "cross_tabs": {
            "lead_time": _cross(research, "lead_time_bucket"),
            "city": _cross(research, "city"),
            "probability_bucket": _cross(research, "probability_bucket"),
            "market_bucket": _cross(research, "market_bucket"),
            "disagreement": _cross(research, "disagreement_bucket"),
        },
        "limitations": [
            "OFFICIAL, RESEARCH_ONLY, and UNCLASSIFIED are never pooled in principal metrics.",
            "This artifact localizes historical probability error only; it does not retune the untouched holdout.",
            "Decision-time correlated-exposure state is not present in the source export and is not inferred.",
            "Small-N cells are explicitly flagged when N<10 or event N<5.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--json-out", type=Path, default=Path("v3_contract_type_localization.json"))
    args = parser.parse_args()

    with args.input_csv.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        fields = set(reader.fieldnames or [])
        missing = sorted(REQUIRED - fields)
        if missing:
            raise SystemExit(f"INCOMPLETE_SOURCE missing fields: {', '.join(missing)}")
        rows = list(reader)

    report = build_report(rows)
    args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.json_out),
        "evidence_class_counts": report["evidence_class_counts"],
        "principal_population": report["principal_population"],
        "contract_type": report["contract_type"],
        "disagreement_20pp_plus": report["disagreement_20pp_plus"],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
