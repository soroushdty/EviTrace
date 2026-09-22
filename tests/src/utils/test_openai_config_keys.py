"""
tests/src/utils/test_openai_config_keys.py
------------------------------------------
Feature: risk-remediation, task 1.1 (Config boundary).
Validates: Requirement 5.4 (repair-attempt limit is an operator-configurable
``retry`` key) and Requirement 10.3 (evidence-budget documentation agrees with
the configured values).

Covers ``load_openai_config()``'s two new keys -- ``max_repair_attempts``
(env ``OPENAI_MAX_REPAIR_ATTEMPTS``) and ``min_evidence_coverage_ratio``
(env ``OPENAI_MIN_EVIDENCE_COVERAGE_RATIO``) -- under the env > yaml >
default rule, plus a consistency check that ``configs/config.yaml``, the
loader defaults, and ``configs/README.md`` all agree on the canonical
evidence budgets (150 items / 30000 chars per chunk).

No OPENAI_API_KEY is needed: ``load_openai_config`` only reads the env var,
it never contacts the API.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from utils.config_utils import _ALL_KNOWN_TOP_LEVEL_KEYS, load_openai_config

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONFIG_YAML = _REPO_ROOT / "configs" / "config.yaml"
_CONFIG_README = _REPO_ROOT / "configs" / "README.md"
_STEERING_CONFIG = _REPO_ROOT / ".kiro" / "steering" / "config.md"

_CANONICAL_MAX_ITEMS = 150
_CANONICAL_MAX_CHARS = 30000

_ENV_REPAIR = "OPENAI_MAX_REPAIR_ATTEMPTS"
_ENV_COVERAGE = "OPENAI_MIN_EVIDENCE_COVERAGE_RATIO"


def _write_config(tmp_path, data: dict) -> str:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(data), encoding="utf-8")
    return str(cfg_file)


@pytest.fixture
def clean_env(monkeypatch):
    """Guarantee the two new env vars are unset for default-resolution tests."""
    monkeypatch.delenv(_ENV_REPAIR, raising=False)
    monkeypatch.delenv(_ENV_COVERAGE, raising=False)
    return monkeypatch


# ---------------------------------------------------------------------------
# Requirement 5.4 / 10.3: new keys resolve to defaults when yaml + env absent
# ---------------------------------------------------------------------------

class TestNewKeyDefaults:
    def test_max_repair_attempts_default_is_2(self, tmp_path, clean_env):
        cfg = load_openai_config(_write_config(tmp_path, {"pdfs_path": "data/pdfs"}))
        assert cfg["max_repair_attempts"] == 2
        assert isinstance(cfg["max_repair_attempts"], int)

    def test_min_evidence_coverage_ratio_default_is_0_6(self, tmp_path, clean_env):
        cfg = load_openai_config(_write_config(tmp_path, {"pdfs_path": "data/pdfs"}))
        assert cfg["min_evidence_coverage_ratio"] == pytest.approx(0.6)
        assert isinstance(cfg["min_evidence_coverage_ratio"], float)

    def test_max_evidence_chars_default_stays_30000(self, tmp_path, clean_env):
        """Design: 'max_evidence_chars_per_chunk default stays 30000'."""
        cfg = load_openai_config(_write_config(tmp_path, {"pdfs_path": "data/pdfs"}))
        assert cfg["max_evidence_items_per_chunk"] == _CANONICAL_MAX_ITEMS
        assert cfg["max_evidence_chars_per_chunk"] == _CANONICAL_MAX_CHARS


# ---------------------------------------------------------------------------
# yaml beats default; env beats yaml
# ---------------------------------------------------------------------------

class TestNewKeyOverrides:
    def test_yaml_max_repair_attempts_overrides_default(self, tmp_path, clean_env):
        cfg = load_openai_config(_write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "retry": {"max_repair_attempts": 4},
        }))
        assert cfg["max_repair_attempts"] == 4

    def test_yaml_min_evidence_coverage_ratio_overrides_default(self, tmp_path, clean_env):
        cfg = load_openai_config(_write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "extraction": {"min_evidence_coverage_ratio": 0.8},
        }))
        assert cfg["min_evidence_coverage_ratio"] == pytest.approx(0.8)

    def test_env_max_repair_attempts_overrides_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv(_ENV_REPAIR, "5")
        cfg = load_openai_config(_write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "retry": {"max_repair_attempts": 4},
        }))
        assert cfg["max_repair_attempts"] == 5
        assert isinstance(cfg["max_repair_attempts"], int)

    def test_env_min_evidence_coverage_ratio_overrides_yaml(self, tmp_path, monkeypatch):
        monkeypatch.setenv(_ENV_COVERAGE, "0.75")
        cfg = load_openai_config(_write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "extraction": {"min_evidence_coverage_ratio": 0.8},
        }))
        assert cfg["min_evidence_coverage_ratio"] == pytest.approx(0.75)
        assert isinstance(cfg["min_evidence_coverage_ratio"], float)

    def test_env_max_repair_attempts_zero_is_honoured(self, tmp_path, monkeypatch):
        """'0' is a legitimate operator value (disable repair); it must not be
        treated as unset the way a falsy string would be under a bare `or`."""
        monkeypatch.setenv(_ENV_REPAIR, "0")
        cfg = load_openai_config(_write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "retry": {"max_repair_attempts": 4},
        }))
        assert cfg["max_repair_attempts"] == 0

    def test_new_keys_are_nested_not_top_level(self):
        """Steering 'Adding New Config Keys': the keys live under existing
        `retry` / `extraction` sections, so the top-level registry is unchanged."""
        assert "max_repair_attempts" not in _ALL_KNOWN_TOP_LEVEL_KEYS
        assert "min_evidence_coverage_ratio" not in _ALL_KNOWN_TOP_LEVEL_KEYS
        assert "retry" in _ALL_KNOWN_TOP_LEVEL_KEYS
        assert "extraction" in _ALL_KNOWN_TOP_LEVEL_KEYS


# ---------------------------------------------------------------------------
# Requirement 10.3: config file, loader default, and docs agree on 150 / 30000
# ---------------------------------------------------------------------------

def _doc_budget_values(text: str) -> tuple[list[int], list[int]]:
    items = [int(m) for m in re.findall(r"max_evidence_items_per_chunk:\s*(\d+)", text)]
    chars = [int(m) for m in re.findall(r"max_evidence_chars_per_chunk:\s*(\d+)", text)]
    return items, chars


class TestEvidenceBudgetConsistency:
    def test_repo_config_yaml_has_canonical_budgets(self):
        raw = yaml.safe_load(_CONFIG_YAML.read_text(encoding="utf-8"))
        assert raw["extraction"]["max_evidence_items_per_chunk"] == _CANONICAL_MAX_ITEMS
        assert raw["extraction"]["max_evidence_chars_per_chunk"] == _CANONICAL_MAX_CHARS

    def test_repo_config_yaml_has_new_keys(self):
        raw = yaml.safe_load(_CONFIG_YAML.read_text(encoding="utf-8"))
        assert raw["retry"]["max_repair_attempts"] == 2
        assert raw["extraction"]["min_evidence_coverage_ratio"] == pytest.approx(0.6)

    def test_loader_defaults_match_canonical_budgets(self, tmp_path, clean_env):
        # No `extraction` section at all -> pure loader defaults.
        cfg = load_openai_config(_write_config(tmp_path, {"pdfs_path": "data/pdfs"}))
        assert cfg["max_evidence_items_per_chunk"] == _CANONICAL_MAX_ITEMS
        assert cfg["max_evidence_chars_per_chunk"] == _CANONICAL_MAX_CHARS

    def test_configs_readme_documents_canonical_budgets(self):
        items, chars = _doc_budget_values(_CONFIG_README.read_text(encoding="utf-8"))
        assert items, "configs/README.md must document max_evidence_items_per_chunk"
        assert chars, "configs/README.md must document max_evidence_chars_per_chunk"
        assert set(items) == {_CANONICAL_MAX_ITEMS}
        assert set(chars) == {_CANONICAL_MAX_CHARS}

    def test_configs_readme_documents_new_keys(self):
        text = _CONFIG_README.read_text(encoding="utf-8")
        assert re.search(r"max_repair_attempts:\s*2\b", text)
        assert re.search(r"min_evidence_coverage_ratio:\s*0\.6\b", text)
        assert "token_budgets" in text

    def test_steering_config_documents_canonical_budgets_and_env_vars(self):
        text = _STEERING_CONFIG.read_text(encoding="utf-8")
        items, chars = _doc_budget_values(text)
        assert set(items) == {_CANONICAL_MAX_ITEMS}
        assert set(chars) == {_CANONICAL_MAX_CHARS}
        assert _ENV_REPAIR in text
        assert _ENV_COVERAGE in text

    def test_all_three_sources_agree(self, tmp_path, clean_env):
        """The single assertion the task's 'Done when' names: yaml, loader
        default, and README agree on 150 items / 30000 chars."""
        raw = yaml.safe_load(_CONFIG_YAML.read_text(encoding="utf-8"))["extraction"]
        loader = load_openai_config(_write_config(tmp_path, {"pdfs_path": "data/pdfs"}))
        readme_items, readme_chars = _doc_budget_values(_CONFIG_README.read_text(encoding="utf-8"))

        items = {raw["max_evidence_items_per_chunk"], loader["max_evidence_items_per_chunk"], *readme_items}
        chars = {raw["max_evidence_chars_per_chunk"], loader["max_evidence_chars_per_chunk"], *readme_chars}
        assert items == {_CANONICAL_MAX_ITEMS}, f"evidence item budgets disagree: {items}"
        assert chars == {_CANONICAL_MAX_CHARS}, f"evidence char budgets disagree: {chars}"
