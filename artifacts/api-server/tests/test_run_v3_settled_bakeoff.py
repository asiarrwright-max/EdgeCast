from __future__ import annotations

from argparse import Namespace
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "run_v3_settled_bakeoff.py"
_SPEC = spec_from_file_location("run_v3_settled_bakeoff", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_validate_args_rejects_manifest_without_input():
    with pytest.raises(SystemExit, match="--manifest requires --input"):
        _MODULE._validate_args(Namespace(input=None, manifest=Path("manifest.json")))


def test_load_export_rejects_unsupported_suffix(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"complete_settled_v3_count": 0}', encoding="utf-8")
    input_path = tmp_path / "rows.txt"
    input_path.write_text("not-json", encoding="utf-8")
    with pytest.raises(SystemExit, match="--input must be \\.csv or \\.json"):
        _MODULE._load_export(input_path, manifest)


def test_load_export_rejects_json_without_list_rows(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"complete_settled_v3_count": 0}', encoding="utf-8")
    input_path = tmp_path / "rows.json"
    input_path.write_text('{"rows": "bad"}', encoding="utf-8")
    with pytest.raises(SystemExit, match="list-valued rows"):
        _MODULE._load_export(input_path, manifest)


def test_load_export_rejects_malformed_json_input(tmp_path: Path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"complete_settled_v3_count": 0}', encoding="utf-8")
    input_path = tmp_path / "rows.json"
    input_path.write_text("{bad json", encoding="utf-8")
    with pytest.raises(SystemExit, match="unable to read JSON input"):
        _MODULE._load_export(input_path, manifest)
