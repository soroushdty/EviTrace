"""
tests/test_quality_control_config.py
=====================================
Tests for the QC config extension in pdf_extractor/utils/config_utils.py.

Covers:
  - Property 15: QC config defaults are applied when namespace is absent
  - Unit tests for the quality_control top-level key acceptance
  - Unit tests for each sub-namespace default key/value
  - Unit tests for deep-merge (user values override defaults)
  - Unit tests for get_qc_config helper
"""

from __future__ import annotations

import yaml
import pytest

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from utils.config_utils import (
    _QC_DEFAULTS,
    _deep_merge,
    get_qc_config,
    load_config,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_config(tmp_path, data: dict) -> str:
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump(data), encoding="utf-8")
    return str(cfg_file)


# ---------------------------------------------------------------------------
# Property 15: QC config defaults are applied when namespace is absent
# ---------------------------------------------------------------------------

# Feature: quality-control-module, Property 15: QC config defaults are applied when namespace is absent
@given(
    st.dictionaries(
        keys=st.just("pdfs_path"),
        values=st.just("data/pdfs"),
        min_size=1,
        max_size=1,
    )
)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_qc_config_defaults_applied(config_without_qc):
    """**Validates: Requirements 7.1, 7.3**

    For any config dict that does NOT contain a quality_control key, the
    deep-merge logic SHALL produce a quality_control sub-dict containing all
    required sub-namespaces with their correct default values.

    We test the _deep_merge helper directly (which is what load_config uses
    internally) to avoid the need for a real file on disk in a property test.
    """
    assert "quality_control" not in config_without_qc

    qc_defaults = _QC_DEFAULTS["quality_control"]
    # Simulate what load_config does: deep-merge defaults with an empty user dict
    merged = _deep_merge(qc_defaults, {})

    # All required sub-namespaces must be present
    for sub_ns in ("artifact_generator", "rater", "iaa_calculator", "adjudicator", "reconciler"):
        assert sub_ns in merged, f"Sub-namespace '{sub_ns}' missing from merged QC config"

    # artifact_generator defaults
    assert merged["artifact_generator"]["export_to_disk"] is False
    assert merged["artifact_generator"]["output_dir"] == "output/qc_artifacts"

    # rater defaults
    assert merged["rater"]["attributes"] == []

    # iaa_calculator defaults
    assert merged["iaa_calculator"]["thresholds"] == {}
    assert merged["iaa_calculator"]["agreement_metrics"] == []

    # adjudicator defaults
    assert merged["adjudicator"]["strategy"] == "placeholder"

    # reconciler defaults
    assert merged["reconciler"]["enable_tei_export"] is False
    assert merged["reconciler"]["enable_annotation_export"] is False


# ---------------------------------------------------------------------------
# Unit tests: quality_control accepted as a known top-level key
# ---------------------------------------------------------------------------

class TestQualityControlKeyAccepted:
    def test_quality_control_key_does_not_raise(self, tmp_path):
        """quality_control is a known top-level key and must not trigger ValueError."""
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "quality_control": {},
        })
        # Should not raise
        cfg = load_config(cfg_file)
        assert "quality_control" in cfg

    def test_unknown_top_level_key_still_raises(self, tmp_path):
        """Unknown top-level keys must still raise ValueError."""
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "totally_unknown_key": True,
        })
        with pytest.raises(ValueError, match="Unknown config keys"):
            load_config(cfg_file)

    def test_quality_control_absent_does_not_raise(self, tmp_path):
        """Omitting quality_control entirely must not raise."""
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_config(cfg_file)
        assert "quality_control" in cfg


# ---------------------------------------------------------------------------
# Unit tests: sub-namespace default keys and values
# ---------------------------------------------------------------------------

class TestQCSubNamespaceDefaults:
    def _load(self, tmp_path, qc_override: dict | None = None) -> dict:
        data: dict = {"pdfs_path": "data/pdfs"}
        if qc_override is not None:
            data["quality_control"] = qc_override
        cfg_file = _write_config(tmp_path, data)
        return load_config(cfg_file)

    # artifacts
    def test_artifacts_export_to_disk_default_false(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["artifact_generator"]["export_to_disk"] is False

    def test_artifacts_output_dir_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["artifact_generator"]["output_dir"] == "output/qc_artifacts"

    # rater
    def test_observer_attributes_default_empty_list(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["rater"]["attributes"] == []

    # iaa_calculator
    def test_investigator_thresholds_default_empty_dict(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["iaa_calculator"]["thresholds"] == {}

    def test_investigator_agreement_metrics_default_empty_list(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["iaa_calculator"]["agreement_metrics"] == []

    # adjudicator
    def test_adjudicator_strategy_default_placeholder(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["adjudicator"]["strategy"] == "placeholder"

    # reconciler
    def test_downstream_enable_tei_export_default_false(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["reconciler"]["enable_tei_export"] is False

    def test_downstream_enable_annotation_export_default_false(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["reconciler"]["enable_annotation_export"] is False


# ---------------------------------------------------------------------------
# Unit tests: deep merge — user-supplied values override defaults
# ---------------------------------------------------------------------------

class TestQCDeepMerge:
    def _load_with_qc(self, tmp_path, qc: dict) -> dict:
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs", "quality_control": qc})
        return load_config(cfg_file)

    def test_user_export_to_disk_true_overrides_default(self, tmp_path):
        cfg = self._load_with_qc(tmp_path, {"artifact_generator": {"export_to_disk": True}})
        assert cfg["quality_control"]["artifact_generator"]["export_to_disk"] is True

    def test_user_output_dir_overrides_default(self, tmp_path):
        cfg = self._load_with_qc(tmp_path, {"artifact_generator": {"output_dir": "custom/dir"}})
        assert cfg["quality_control"]["artifact_generator"]["output_dir"] == "custom/dir"

    def test_user_observer_attributes_overrides_default(self, tmp_path):
        cfg = self._load_with_qc(tmp_path, {"rater": {"attributes": ["word_count", "char_count"]}})
        assert cfg["quality_control"]["rater"]["attributes"] == ["word_count", "char_count"]

    def test_user_agreement_metrics_overrides_default(self, tmp_path):
        cfg = self._load_with_qc(tmp_path, {"iaa_calculator": {"agreement_metrics": ["jaccard"]}})
        assert cfg["quality_control"]["iaa_calculator"]["agreement_metrics"] == ["jaccard"]

    def test_user_adjudicator_strategy_overrides_default(self, tmp_path):
        cfg = self._load_with_qc(tmp_path, {"adjudicator": {"strategy": "majority_vote"}})
        assert cfg["quality_control"]["adjudicator"]["strategy"] == "majority_vote"

    def test_user_downstream_tei_export_overrides_default(self, tmp_path):
        cfg = self._load_with_qc(tmp_path, {"reconciler": {"enable_tei_export": True}})
        assert cfg["quality_control"]["reconciler"]["enable_tei_export"] is True

    def test_partial_override_preserves_other_defaults(self, tmp_path):
        """Overriding one artifact_generator key must not wipe out the other."""
        cfg = self._load_with_qc(tmp_path, {"artifact_generator": {"export_to_disk": True}})
        # output_dir default must still be present
        assert cfg["quality_control"]["artifact_generator"]["output_dir"] == "output/qc_artifacts"

    def test_partial_investigator_override_preserves_thresholds(self, tmp_path):
        cfg = self._load_with_qc(tmp_path, {"iaa_calculator": {"agreement_metrics": ["cosine"]}})
        assert cfg["quality_control"]["iaa_calculator"]["thresholds"] == {}


# ---------------------------------------------------------------------------
# Unit tests: get_qc_config helper
# ---------------------------------------------------------------------------

class TestGetQcConfig:
    def test_returns_quality_control_subdict(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_config(cfg_file)
        qc = get_qc_config(cfg)
        assert qc is cfg["quality_control"]

    def test_returned_dict_contains_all_sub_namespaces(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_config(cfg_file)
        qc = get_qc_config(cfg)
        for sub_ns in ("artifact_generator", "rater", "iaa_calculator", "adjudicator", "reconciler"):
            assert sub_ns in qc

    def test_returned_dict_reflects_user_overrides(self, tmp_path):
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "quality_control": {"adjudicator": {"strategy": "custom"}},
        })
        cfg = load_config(cfg_file)
        qc = get_qc_config(cfg)
        assert qc["adjudicator"]["strategy"] == "custom"


# ---------------------------------------------------------------------------
# Unit tests: semantic_qc defaults (Requirements 7.1, 7.2, 7.3, 7.4, 7.5)
# ---------------------------------------------------------------------------

class TestSemanticQCDefaults:
    """**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**

    The semantic_qc sub-section must be present in _QC_DEFAULTS and must
    be surfaced via load_config even when the caller omits quality_control
    entirely from config.yaml.
    """

    def _load(self, tmp_path, qc_override: dict | None = None) -> dict:
        data: dict = {"pdfs_path": "data/pdfs"}
        if qc_override is not None:
            data["quality_control"] = qc_override
        cfg_file = _write_config(tmp_path, data)
        return load_config(cfg_file)

    # --- presence ---

    def test_semantic_qc_in_qc_defaults_dict(self):
        """semantic_qc must exist directly in _QC_DEFAULTS['quality_control']."""
        assert "semantic_qc" in _QC_DEFAULTS["quality_control"]

    def test_semantic_qc_present_when_qc_omitted(self, tmp_path):
        """load_config must produce semantic_qc even when quality_control is absent."""
        cfg = self._load(tmp_path)
        assert "semantic_qc" in cfg["quality_control"]

    def test_semantic_qc_present_when_qc_empty(self, tmp_path):
        """load_config must produce semantic_qc when quality_control is an empty dict."""
        cfg = self._load(tmp_path, qc_override={})
        assert "semantic_qc" in cfg["quality_control"]

    # --- default values ---

    def test_semantic_qc_enabled_default_false(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_qc"]["enabled"] is False

    def test_semantic_qc_model_name_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_qc"]["model_name"] == "BAAI/bge-base-en-v1.5"

    def test_semantic_qc_query_prefix_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_qc"]["query_prefix"] == (
            "Represent this sentence for searching relevant passages: "
        )

    def test_semantic_qc_similarity_threshold_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_qc"]["similarity_threshold"] == 0.85

    def test_semantic_qc_max_sentences_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_qc"]["max_sentences"] == 10000

    # --- deep-merge: user value wins ---

    def test_semantic_qc_user_enabled_true_overrides(self, tmp_path):
        cfg = self._load(tmp_path, qc_override={"semantic_qc": {"enabled": True}})
        assert cfg["quality_control"]["semantic_qc"]["enabled"] is True

    def test_semantic_qc_partial_override_preserves_other_defaults(self, tmp_path):
        """Overriding enabled must not wipe out model_name."""
        cfg = self._load(tmp_path, qc_override={"semantic_qc": {"enabled": True}})
        assert cfg["quality_control"]["semantic_qc"]["model_name"] == "BAAI/bge-base-en-v1.5"

    def test_semantic_qc_user_threshold_overrides(self, tmp_path):
        cfg = self._load(tmp_path, qc_override={"semantic_qc": {"similarity_threshold": 0.9}})
        assert cfg["quality_control"]["semantic_qc"]["similarity_threshold"] == 0.9

    # --- no faiss/torch/sentence_transformers keys (Req 14.1) ---

    def test_semantic_qc_no_faiss_key(self):
        sqc = _QC_DEFAULTS["quality_control"]["semantic_qc"]
        for key in sqc:
            assert "faiss" not in key.lower()

    def test_semantic_qc_no_torch_key(self):
        sqc = _QC_DEFAULTS["quality_control"]["semantic_qc"]
        for key in sqc:
            assert "torch" not in key.lower()

    def test_semantic_qc_no_sentence_transformers_key(self):
        sqc = _QC_DEFAULTS["quality_control"]["semantic_qc"]
        for key in sqc:
            assert "sentence_transformers" not in key.lower()


# ---------------------------------------------------------------------------
# Unit tests: local_metrics defaults (Requirements 7.8, 7.9, 14.1, 14.2, 14.3, 14.7)
# ---------------------------------------------------------------------------

class TestLocalMetricsDefaults:
    """**Validates: Requirements 7.8, 7.9, 14.1, 14.2, 14.3, 14.7**

    The local_metrics sub-section must be present in _QC_DEFAULTS and must
    be surfaced via load_config with all required keys at their default values.
    It must contain NO keys that toggle faiss/torch/sentence_transformers.
    """

    def _load(self, tmp_path, qc_override: dict | None = None) -> dict:
        data: dict = {"pdfs_path": "data/pdfs"}
        if qc_override is not None:
            data["quality_control"] = qc_override
        cfg_file = _write_config(tmp_path, data)
        return load_config(cfg_file)

    # --- presence ---

    def test_local_metrics_in_qc_defaults_dict(self):
        """local_metrics must exist directly in _QC_DEFAULTS['quality_control']."""
        assert "local_metrics" in _QC_DEFAULTS["quality_control"]

    def test_local_metrics_present_when_qc_omitted(self, tmp_path):
        cfg = self._load(tmp_path)
        assert "local_metrics" in cfg["quality_control"]

    def test_local_metrics_present_when_qc_empty(self, tmp_path):
        cfg = self._load(tmp_path, qc_override={})
        assert "local_metrics" in cfg["quality_control"]

    # --- default values ---

    def test_local_metrics_min_chars_per_page(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["min_chars_per_page"] == 100

    def test_local_metrics_grobid_vs_native_ratio_threshold(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["grobid_vs_native_ratio_threshold"] == 0.6

    def test_local_metrics_long_sentence_word_threshold(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["long_sentence_word_threshold"] == 120

    def test_local_metrics_long_sentence_max_fraction(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["long_sentence_max_fraction"] == 0.12

    def test_local_metrics_expected_sections_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["expected_sections"] == [
            "abstract", "introduction", "methods", "results"
        ]

    def test_local_metrics_caption_table_figure_check_enabled_default_true(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["caption_table_figure_check_enabled"] is True

    def test_local_metrics_coordinate_coverage_threshold(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["coordinate_coverage_threshold"] == 0.1

    def test_local_metrics_references_in_body_threshold(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["references_in_body_threshold"] == 0.05

    def test_local_metrics_weird_char_ratio_threshold(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["weird_char_ratio_threshold"] == 0.05

    # --- deep-merge: user value wins ---

    def test_local_metrics_user_min_chars_overrides(self, tmp_path):
        cfg = self._load(tmp_path, qc_override={"local_metrics": {"min_chars_per_page": 200}})
        assert cfg["quality_control"]["local_metrics"]["min_chars_per_page"] == 200

    def test_local_metrics_partial_override_preserves_other_defaults(self, tmp_path):
        """Overriding one key must leave unrelated defaults intact."""
        cfg = self._load(tmp_path, qc_override={"local_metrics": {"min_chars_per_page": 50}})
        assert cfg["quality_control"]["local_metrics"]["grobid_vs_native_ratio_threshold"] == 0.6

    def test_local_metrics_user_expected_sections_overrides(self, tmp_path):
        cfg = self._load(tmp_path, qc_override={
            "local_metrics": {"expected_sections": ["abstract", "conclusion"]}
        })
        assert cfg["quality_control"]["local_metrics"]["expected_sections"] == [
            "abstract", "conclusion"
        ]

    # --- no faiss/torch/sentence_transformers keys (Req 14.1) ---

    def test_local_metrics_no_faiss_key(self):
        lm = _QC_DEFAULTS["quality_control"]["local_metrics"]
        for key in lm:
            assert "faiss" not in key.lower(), f"Forbidden key found: {key}"

    def test_local_metrics_no_torch_key(self):
        lm = _QC_DEFAULTS["quality_control"]["local_metrics"]
        for key in lm:
            assert "torch" not in key.lower(), f"Forbidden key found: {key}"

    def test_local_metrics_no_sentence_transformers_key(self):
        lm = _QC_DEFAULTS["quality_control"]["local_metrics"]
        for key in lm:
            assert "sentence_transformers" not in key.lower(), f"Forbidden key found: {key}"

    # --- get_qc_config surfaces local_metrics ---

    def test_get_qc_config_includes_local_metrics(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_config(cfg_file)
        qc = get_qc_config(cfg)
        assert "local_metrics" in qc

    def test_get_qc_config_includes_semantic_qc(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_config(cfg_file)
        qc = get_qc_config(cfg)
        assert "semantic_qc" in qc
