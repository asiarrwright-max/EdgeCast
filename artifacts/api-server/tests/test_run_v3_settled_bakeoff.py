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
