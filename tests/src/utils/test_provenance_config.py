"""
tests/src/utils/test_provenance_config.py
==========================================
Tests for the ``provenance`` configuration block and run-scoped output path
(provenance-core task 1.2; requirements 1.3, 8.5).

Covers:
  - ``_PROVENANCE_DEFAULTS`` carries the documented keys and default values
  - ``provenance`` is a registered top-level key, so ``load_local_config``
    accepts a config file containing it
  - ``load_provenance_config(config)`` deep-merges a passed-in config mapping
    over the defaults without mutating either
  - the shipped ``configs/config.yaml`` loads cleanly and yields the
    documented provenance defaults
  - ``PROVENANCE_DIR`` sits under the current run folder
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from utils.config_utils import (
    _ALL_KNOWN_TOP_LEVEL_KEYS,
    _PROVENANCE_DEFAULTS,
    _load_config_yaml,
    load_local_config,
    load_provenance_config,
)
from utils.path_utils import (
    PROVENANCE_DIR,
    RUN_FOLDER_NAME,
    resolve_main_output_dir,
    resolve_run_output_path,
)

_DOCUMENTED_DEFAULTS = {
    "enabled": True,
    "output_subdir": "provenance",
    "severity_overrides": {},
    "fail_run_on_severity": None,
    "stage_contracts_strict": True,
}


def _write_config(tmp_path, data: dict) -> str:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(data), encoding="utf-8")
    return str(cfg_file)


# ---------------------------------------------------------------------------
# Defaults and key registration
# ---------------------------------------------------------------------------

def test_provenance_defaults_match_documented_values():
    assert _PROVENANCE_DEFAULTS == _DOCUMENTED_DEFAULTS


def test_provenance_registered_as_known_top_level_key():
    assert "provenance" in _ALL_KNOWN_TOP_LEVEL_KEYS


def test_load_local_config_accepts_provenance_block(tmp_path):
    path = _write_config(
        tmp_path,
        {"pdfs_path": "data/pdfs", "provenance": {"enabled": False}},
    )
    cfg = load_local_config(path)  # must not raise "Unknown config keys"
    assert cfg["pdfs_path"]


# ---------------------------------------------------------------------------
# load_provenance_config(config)
# ---------------------------------------------------------------------------

def test_load_provenance_config_without_block_returns_defaults():
    assert load_provenance_config({}) == _DOCUMENTED_DEFAULTS


def test_load_provenance_config_null_block_returns_defaults():
    assert load_provenance_config({"provenance": None}) == _DOCUMENTED_DEFAULTS


def test_load_provenance_config_user_values_override_defaults():
    config = {
        "openai": {"chunk_model": "x"},
        "provenance": {
            "enabled": False,
            "severity_overrides": {"dangling_reference": "warning"},
            "fail_run_on_severity": "error",
        },
    }
    result = load_provenance_config(config)
    assert result == {
        "enabled": False,
        "output_subdir": "provenance",
        "severity_overrides": {"dangling_reference": "warning"},
        "fail_run_on_severity": "error",
        "stage_contracts_strict": True,
    }


def test_load_provenance_config_does_not_mutate_inputs_or_defaults():
    config = {"provenance": {"severity_overrides": {"a": "info"}}}
    snapshot = copy.deepcopy(config)
    defaults_snapshot = copy.deepcopy(_PROVENANCE_DEFAULTS)

    result = load_provenance_config(config)
    result["severity_overrides"]["b"] = "critical"
    result["enabled"] = False

    assert config == snapshot
    assert _PROVENANCE_DEFAULTS == defaults_snapshot
    # A fresh call is unaffected by mutation of an earlier result.
    assert load_provenance_config({})["severity_overrides"] == {}


# ---------------------------------------------------------------------------
# Shipped configuration
# ---------------------------------------------------------------------------

def test_shipped_config_has_provenance_block_with_documented_defaults():
    raw = _load_config_yaml()
    assert raw.get("provenance") == _DOCUMENTED_DEFAULTS
    assert load_provenance_config(raw) == _DOCUMENTED_DEFAULTS


def test_shipped_config_loads_without_unknown_key_error():
    load_local_config()  # raises ValueError on any unregistered top-level key


# ---------------------------------------------------------------------------
# Run-scoped output directory
# ---------------------------------------------------------------------------

def test_provenance_dir_is_under_current_run_folder():
    run_root = (resolve_main_output_dir() / RUN_FOLDER_NAME).resolve()
    assert isinstance(PROVENANCE_DIR, Path)
    assert PROVENANCE_DIR == run_root / "provenance"
    assert PROVENANCE_DIR.parent == run_root
    assert PROVENANCE_DIR.parent.name == RUN_FOLDER_NAME


def test_provenance_dir_uses_run_output_path_helper():
    assert PROVENANCE_DIR == resolve_run_output_path("provenance")
