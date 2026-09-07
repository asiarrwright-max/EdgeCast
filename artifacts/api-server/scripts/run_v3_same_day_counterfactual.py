"""Build a read-only same-day counterfactual report from frozen settled-V3 artifacts."""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.v3_accuracy_lab import build_same_day_counterfactual_report  # noqa: E402


_FLOAT_FIELDS = {"actual", "market_prob", "model_prob", "profit_loss", "stake"}
_INT_FIELDS = {"trade_id", "lead_time_days"}


def _coerce(row: dict[str, str]) -> dict[str, object]:
    clean: dict[str, object] = {}
    for key, value in row.items():
        if value == "":
            clean[key] = None
        elif key in _FLOAT_FIELDS:
            clean[key] = float(value)
        elif key in _INT_FIELDS:
            clean[key] = int(value)
        else:
            clean[key] = value
    return clean


def _markdown(report: dict[str, object]) -> str:
    lines = [
        "# Same-day V3 counterfactual artifact",
        "",
        "Read-only report generated from committed settled V3 artifacts.",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Exact same-day supported: `{report['lead_time_distribution']['exact_same_day_supported']}`",
        "",
    ]
    if report["status"] != "ok":
        blocker = report["blocker"]
        fallback = report["available_fallback_context"]
        lines.extend([
            "## Blocker",
            "",
            f"- Missing field(s): `{', '.join(blocker['missing_fields'])}`",
            f"- Why blocked: {blocker['why_blocked']}",
            f"- Minimum additional input: {blocker['minimum_additional_input']}",
            "",
            "## Available fallback context",
            "",
            f"- Cohort: **{fallback['cohort_label']}**",
            f"- Rows available: **{fallback['rows_available']}**",
            f"- RESEARCH_ONLY rows available: **{fallback['research_rows_available']}**",
            "",
            "This fallback uses the committed `0-1d` bucket only and must not be interpreted as an exact same-day result.",
        ])
        return "\n".join(lines) + "\n"

    lines.extend([
        "## Same-day RESEARCH_ONLY counterfactual",
        "",
        "Exact same-day rows were available in the input.",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    base_dir = Path(__file__).parent.parent / "reports" / "settled_v3_complete"
    input_path = base_dir / "settled_v3_main_cohort.csv"
    if not input_path.exists():
        payload = {
            "status": "BLOCKED_INCOMPLETE_SOURCE",
            "missing_path": str(input_path),
            "message": "Frozen settled_v3_main_cohort.csv artifact is required.",
        }
        print(json.dumps(payload, indent=2))
        raise SystemExit("BLOCKED_INCOMPLETE_SOURCE: missing settled_v3_main_cohort.csv")
    with input_path.open(newline="", encoding="utf-8") as handle:
        rows = [_coerce(row) for row in csv.DictReader(handle)]
    report = build_same_day_counterfactual_report(
        rows,
        as_of=datetime.now(timezone.utc),
    )
    (base_dir / "settled_v3_same_day_counterfactual.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (base_dir / "settled_v3_same_day_counterfactual.md").write_text(
        _markdown(report),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output_json": str(base_dir / "settled_v3_same_day_counterfactual.json"),
                "output_md": str(base_dir / "settled_v3_same_day_counterfactual.md"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
