"""Read-only Accuracy Lab runner that rejects incomplete settled-V3 exports."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.models_v3 import V3PaperTrade  # noqa: E402
from app.services.v3_accuracy_lab import build_settled_v3_accuracy_lab_report  # noqa: E402


_FLOAT_FIELDS = {
    "ec_yes_probability", "ec_side_probability", "market_yes_probability",
    "side_market_price", "edge_pct_points", "historical_bias_adj",
    "historical_sigma", "final_bias", "final_sigma", "effective_hist_n",
    "stake", "quantity", "station_lat", "station_lon", "gross_payout",
    "profit_loss", "return_pct", "quote_age_seconds", "minutes_to_market_close",
}
_INT_FIELDS = {
    "id", "v3_snapshot_id", "lead_time_days", "fallback_level_used",
    "hist_sample_count", "v3_forward_count",
}
_BOOL_FIELDS = {"is_executable", "station_verified", "outcome_verified"}


def _coerce_export_row(row: dict) -> SimpleNamespace:
    """Restore primitive DB types lost by CSV serialization."""
    clean = {}
    for key, value in row.items():
        if value == "":
            clean[key] = None
        elif key in _FLOAT_FIELDS:
            clean[key] = float(value)
        elif key in _INT_FIELDS:
            clean[key] = int(value)
        elif key in _BOOL_FIELDS:
            clean[key] = str(value).strip().lower() in {"true", "1", "yes"}
        else:
            clean[key] = value
    return SimpleNamespace(**clean)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, help="Complete settled-V3 CSV/JSON export")
    parser.add_argument("--manifest", type=Path, help="Completeness manifest (required with --input)")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent.parent / "reports")
    return parser.parse_args()


def _load_export(path: Path, manifest_path: Path | None) -> tuple[list[SimpleNamespace], dict]:
    if manifest_path is None:
        raise SystemExit("BLOCKED_INCOMPLETE_SOURCE: --manifest is required with --input")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("complete_settled_v3_count")
    if not isinstance(expected, int) or expected < 0:
        raise SystemExit("BLOCKED_INCOMPLETE_SOURCE: manifest needs integer complete_settled_v3_count")
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            raw_rows = list(csv.DictReader(handle))
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw_rows = payload if isinstance(payload, list) else payload.get("rows", [])
    if len(raw_rows) != expected:
        raise SystemExit(
            f"BLOCKED_INCOMPLETE_SOURCE: export has {len(raw_rows)} rows; manifest declares {expected}"
        )
    return [_coerce_export_row(row) for row in raw_rows], manifest


async def _load_database() -> tuple[list[V3PaperTrade], dict]:
    settings = get_settings()
    db_url, connect_args = settings.get_async_db_url()
    engine = create_async_engine(db_url, connect_args=connect_args, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        expected = int((await session.execute(
            select(func.count(V3PaperTrade.id)).where(V3PaperTrade.status == "SETTLED")
        )).scalar_one())
        rows = list((await session.execute(
            select(V3PaperTrade).where(V3PaperTrade.status == "SETTLED")
            .order_by(V3PaperTrade.target_settlement_date, V3PaperTrade.id)
        )).scalars().all())
    await engine.dispose()
    if len(rows) != expected:
        raise SystemExit(f"BLOCKED_INCOMPLETE_SOURCE: selected {len(rows)} of {expected} settled V3 rows")
    return rows, {"source": "production_database_read_only", "complete_settled_v3_count": expected,
                  "queried_at": datetime.now(timezone.utc).isoformat()}


def _write_outputs(report: dict, manifest: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "settled_v3_accuracy_lab.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    (out_dir / "settled_v3_source_manifest.json").write_text(
        json.dumps(manifest, indent=2, default=str) + "\n", encoding="utf-8")
    cohort = report["main_cohort"]
    fields = sorted({key for row in cohort for key in row})
    with (out_dir / "settled_v3_main_cohort.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(cohort)
    holdout = [row for row in cohort if row.get("partition") == "holdout"]
    (out_dir / "settled_v3_holdout.json").write_text(
        json.dumps(holdout, indent=2, default=str) + "\n", encoding="utf-8")
    with (out_dir / "settled_v3_holdout_results.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["candidate", "holdout_n", "event_n", "wins", "losses", "brier",
                  "event_level_brier", "calibration_error_pp", "log_loss"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for candidate in report["candidate_results"]["ranked_on_holdout"]:
            metrics = candidate["holdout_metrics"]
            writer.writerow({"candidate": candidate["name"], "holdout_n": metrics["n"],
                             "event_n": metrics["event_n"], "wins": metrics["wins"],
                             "losses": metrics["losses"], "brier": metrics["brier"],
                             "event_level_brier": metrics["event_level_brier"],
                             "calibration_error_pp": metrics["mean_abs_calibration_error_pp"],
                             "log_loss": metrics["log_loss"]})


async def main() -> None:
    args = _arguments()
    rows, manifest = _load_export(args.input, args.manifest) if args.input else await _load_database()
    report = build_settled_v3_accuracy_lab_report(rows, as_of=datetime.now(timezone.utc))
    if report["frozen_population"]["total_settled_v3_rows"] != manifest["complete_settled_v3_count"]:
        raise SystemExit("BLOCKED_INCOMPLETE_SOURCE: analysis population differs from completeness manifest")
    _write_outputs(report, manifest, args.output_dir)
    print(json.dumps({"frozen_population": report["frozen_population"],
                      "baseline_by_evidence_class": report["baseline_reproduction"]["by_evidence_class"],
                      "recommendation": report["recommendation"], "output_dir": str(args.output_dir)},
                     indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
