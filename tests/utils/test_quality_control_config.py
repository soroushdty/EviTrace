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

import sys
from unittest.mock import MagicMock, patch

import yaml
import pytest

from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from utils.config_utils import (
    _QC_DEFAULTS,
    _deep_merge,
    get_qc_config,
    load_local_config,
    load_qc_config,
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
@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_qc_config_defaults_applied(config_without_qc):
    """**Validates: Requirements 7.1, 7.3**

    For any config dict that does NOT contain a quality_control key, the
    deep-merge logic SHALL produce a quality_control sub-dict containing all
    required sub-namespaces with their correct default values.

    We test the _deep_merge helper directly (which is what load_local_config uses
    internally) to avoid the need for a real file on disk in a property test.
    """
    assert "quality_control" not in config_without_qc

    qc_defaults = _QC_DEFAULTS["quality_control"]
    # Simulate what load_local_config does: deep-merge defaults with an empty user dict
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
        cfg = load_local_config(cfg_file)
        assert "quality_control" in cfg

    def test_unknown_top_level_key_still_raises(self, tmp_path):
        """Unknown top-level keys must still raise ValueError."""
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "totally_unknown_key": True,
        })
        with pytest.raises(ValueError, match="Unknown config keys"):
            load_local_config(cfg_file)

    def test_quality_control_absent_does_not_raise(self, tmp_path):
        """Omitting quality_control entirely must not raise."""
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_local_config(cfg_file)
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
        return load_local_config(cfg_file)

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
        return load_local_config(cfg_file)

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
        cfg = load_local_config(cfg_file)
        qc = get_qc_config(cfg)
        assert qc is cfg["quality_control"]

    def test_returned_dict_contains_all_sub_namespaces(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_local_config(cfg_file)
        qc = get_qc_config(cfg)
        for sub_ns in ("artifact_generator", "rater", "iaa_calculator", "adjudicator", "reconciler"):
            assert sub_ns in qc

    def test_returned_dict_reflects_user_overrides(self, tmp_path):
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "quality_control": {"adjudicator": {"strategy": "custom"}},
        })
        cfg = load_local_config(cfg_file)
        qc = get_qc_config(cfg)
        assert qc["adjudicator"]["strategy"] == "custom"


# ---------------------------------------------------------------------------
# Unit tests: semantic_qc defaults (Requirements 7.1, 7.2, 7.3, 7.4, 7.5)
# ---------------------------------------------------------------------------

class TestSemanticQCDefaults:
    """**Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.5**

    The semantic_qc sub-section must be present in _QC_DEFAULTS and must
    be surfaced via load_local_config even when the caller omits quality_control
    entirely from config.yaml.
    """

    def _load(self, tmp_path, qc_override: dict | None = None) -> dict:
        data: dict = {"pdfs_path": "data/pdfs"}
        if qc_override is not None:
            data["quality_control"] = qc_override
        cfg_file = _write_config(tmp_path, data)
        return load_local_config(cfg_file)

    # --- presence ---

    def test_semantic_qc_in_qc_defaults_dict(self):
        """semantic_qc must exist directly in _QC_DEFAULTS['quality_control']."""
        assert "semantic_qc" in _QC_DEFAULTS["quality_control"]

    def test_semantic_qc_present_when_qc_omitted(self, tmp_path):
        """load_local_config must produce semantic_qc even when quality_control is absent."""
        cfg = self._load(tmp_path)
        assert "semantic_qc" in cfg["quality_control"]

    def test_semantic_qc_present_when_qc_empty(self, tmp_path):
        """load_local_config must produce semantic_qc when quality_control is an empty dict."""
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
    be surfaced via load_local_config with all required keys at their default values.
    It must contain NO keys that toggle faiss/torch/sentence_transformers.
    """

    def _load(self, tmp_path, qc_override: dict | None = None) -> dict:
        data: dict = {"pdfs_path": "data/pdfs"}
        if qc_override is not None:
            data["quality_control"] = qc_override
        cfg_file = _write_config(tmp_path, data)
        return load_local_config(cfg_file)

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
        cfg = load_local_config(cfg_file)
        qc = get_qc_config(cfg)
        assert "local_metrics" in qc

    def test_get_qc_config_includes_semantic_qc(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_local_config(cfg_file)
        qc = get_qc_config(cfg)
        assert "semantic_qc" in qc


# ---------------------------------------------------------------------------
# Task 4.1: text_processor top-level key in _QC_DEFAULTS (Requirement 9)
# ---------------------------------------------------------------------------

class TestTextProcessorDefaults:
    """**Validates: Requirement 9**

    _QC_DEFAULTS must expose a top-level 'text_processor' key with all
    sub-keys documented in the design spec.  load_qc_config() must surface
    these defaults even when the caller omits text_processor from config.yaml.
    """

    # --- presence in _QC_DEFAULTS ---

    def test_text_processor_key_in_qc_defaults(self):
        """text_processor must be a top-level key in _QC_DEFAULTS."""
        assert "text_processor" in _QC_DEFAULTS

    def test_text_processor_class_default(self):
        """class must default to 'utils.text_processor.TextProcessor'."""
        assert _QC_DEFAULTS["text_processor"]["class"] == "utils.text_processor.TextProcessor"

    def test_text_processor_sentence_tokenizer_backend_default(self):
        """sentence_tokenizer.backend must default to 'scispacy'."""
        assert _QC_DEFAULTS["text_processor"]["sentence_tokenizer"]["backend"] == "scispacy"

    def test_text_processor_word_tokenizer_backend_default(self):
        """word_tokenizer.backend must default to 'simple'."""
        assert _QC_DEFAULTS["text_processor"]["word_tokenizer"]["backend"] == "simple"

    def test_text_processor_normalizer_backend_default(self):
        """normalizer.backend must default to 'nfkc'."""
        assert _QC_DEFAULTS["text_processor"]["normalizer"]["backend"] == "nfkc"

    # --- load_qc_config surfaces text_processor ---

    def test_load_qc_config_includes_text_processor(self, tmp_path):
        """load_qc_config must include text_processor even when absent from YAML."""
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_qc_config(str(cfg_file))
        assert "text_processor" in cfg

    def test_load_qc_config_text_processor_class_default(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_qc_config(str(cfg_file))
        assert cfg["text_processor"]["class"] == "utils.text_processor.TextProcessor"

    def test_load_qc_config_text_processor_sentence_backend_default(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_qc_config(str(cfg_file))
        assert cfg["text_processor"]["sentence_tokenizer"]["backend"] == "scispacy"

    # --- deep-merge: user value wins ---

    def test_text_processor_user_class_overrides(self, tmp_path):
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "text_processor": {"class": "my.module.MyTextProcessor"},
        })
        cfg = load_qc_config(str(cfg_file))
        assert cfg["text_processor"]["class"] == "my.module.MyTextProcessor"

    def test_text_processor_partial_override_preserves_other_defaults(self, tmp_path):
        """Overriding class must not wipe out sentence_tokenizer defaults."""
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "text_processor": {"class": "my.module.MyTextProcessor"},
        })
        cfg = load_qc_config(str(cfg_file))
        assert cfg["text_processor"]["sentence_tokenizer"]["backend"] == "scispacy"


# ---------------------------------------------------------------------------
# Task 4.1: new quality_control sub-keys in _QC_DEFAULTS (Requirement 9)
# ---------------------------------------------------------------------------

class TestQCNewSubkeyDefaults:
    """**Validates: Requirement 9**

    _QC_DEFAULTS['quality_control'] must expose scan_detection, ocr,
    text_fidelity, and section_verification sub-keys with correct defaults.
    load_qc_config() must surface them when omitted from config.yaml.
    """

    def _load_qc(self, tmp_path, extra_yaml: dict | None = None) -> dict:
        data: dict = {"pdfs_path": "data/pdfs"}
        if extra_yaml:
            data.update(extra_yaml)
        cfg_file = _write_config(tmp_path, data)
        return load_qc_config(str(cfg_file))

    # --- scan_detection ---

    def test_scan_detection_in_qc_defaults(self):
        assert "scan_detection" in _QC_DEFAULTS["quality_control"]

    def test_scan_detection_text_density_threshold_default(self):
        assert _QC_DEFAULTS["quality_control"]["scan_detection"]["text_density_threshold"] == 50

    def test_scan_detection_alpha_ratio_threshold_default(self):
        assert _QC_DEFAULTS["quality_control"]["scan_detection"]["alpha_ratio_threshold"] == 0.60

    def test_scan_detection_image_dominance_threshold_default(self):
        assert _QC_DEFAULTS["quality_control"]["scan_detection"]["image_dominance_threshold"] == 0.85

    def test_load_qc_config_scan_detection_present_when_absent(self, tmp_path):
        cfg = self._load_qc(tmp_path)
        assert "scan_detection" in cfg["quality_control"]

    def test_load_qc_config_scan_detection_text_density_threshold(self, tmp_path):
        cfg = self._load_qc(tmp_path)
        assert cfg["quality_control"]["scan_detection"]["text_density_threshold"] == 50

    def test_load_qc_config_scan_detection_alpha_ratio_threshold(self, tmp_path):
        cfg = self._load_qc(tmp_path)
        assert cfg["quality_control"]["scan_detection"]["alpha_ratio_threshold"] == 0.60

    def test_load_qc_config_scan_detection_image_dominance_threshold(self, tmp_path):
        cfg = self._load_qc(tmp_path)
        assert cfg["quality_control"]["scan_detection"]["image_dominance_threshold"] == 0.85

    # --- ocr ---

    def test_ocr_in_qc_defaults(self):
        assert "ocr" in _QC_DEFAULTS["quality_control"]

    def test_ocr_rasterization_dpi_default(self):
        assert _QC_DEFAULTS["quality_control"]["ocr"]["rasterization_dpi"] == 150

    def test_load_qc_config_ocr_rasterization_dpi(self, tmp_path):
        cfg = self._load_qc(tmp_path)
        assert cfg["quality_control"]["ocr"]["rasterization_dpi"] == 150

    # --- text_fidelity ---

    def test_text_fidelity_in_qc_defaults(self):
        assert "text_fidelity" in _QC_DEFAULTS["quality_control"]

    def test_text_fidelity_edit_distance_threshold_default(self):
        assert _QC_DEFAULTS["quality_control"]["text_fidelity"]["edit_distance_threshold"] == 0.10

    def test_load_qc_config_text_fidelity_edit_distance_threshold(self, tmp_path):
        cfg = self._load_qc(tmp_path)
        assert cfg["quality_control"]["text_fidelity"]["edit_distance_threshold"] == 0.10

    # --- section_verification ---

    def test_section_verification_in_qc_defaults(self):
        assert "section_verification" in _QC_DEFAULTS["quality_control"]

    def test_section_verification_font_size_tolerance_default(self):
        assert _QC_DEFAULTS["quality_control"]["section_verification"]["font_size_tolerance"] == 1.0

    def test_load_qc_config_section_verification_font_size_tolerance(self, tmp_path):
        cfg = self._load_qc(tmp_path)
        assert cfg["quality_control"]["section_verification"]["font_size_tolerance"] == 1.0

    # --- deep-merge for new sub-keys ---

    def test_scan_detection_user_override_text_density(self, tmp_path):
        cfg = self._load_qc(tmp_path, {"quality_control": {
            "scan_detection": {"text_density_threshold": 30}
        }})
        assert cfg["quality_control"]["scan_detection"]["text_density_threshold"] == 30

    def test_scan_detection_partial_override_preserves_alpha_ratio(self, tmp_path):
        cfg = self._load_qc(tmp_path, {"quality_control": {
            "scan_detection": {"text_density_threshold": 30}
        }})
        assert cfg["quality_control"]["scan_detection"]["alpha_ratio_threshold"] == 0.60

    def test_text_fidelity_user_override(self, tmp_path):
        cfg = self._load_qc(tmp_path, {"quality_control": {
            "text_fidelity": {"edit_distance_threshold": 0.20}
        }})
        assert cfg["quality_control"]["text_fidelity"]["edit_distance_threshold"] == 0.20

    def test_section_verification_user_override(self, tmp_path):
        cfg = self._load_qc(tmp_path, {"quality_control": {
            "section_verification": {"font_size_tolerance": 2.5}
        }})
        assert cfg["quality_control"]["section_verification"]["font_size_tolerance"] == 2.5

    def test_ocr_user_override_rasterization_dpi(self, tmp_path):
        cfg = self._load_qc(tmp_path, {"quality_control": {
            "ocr": {"rasterization_dpi": 300}
        }})
        assert cfg["quality_control"]["ocr"]["rasterization_dpi"] == 300


# ---------------------------------------------------------------------------
# Task 4.1: config defaults — existing config.yaml without new keys
# ---------------------------------------------------------------------------

class TestConfigDefaults:
    """**Validates: Requirement 9**

    Loading a config.yaml that omits all new keys must NOT
    raise and must still provide defaults for every key.
    """

    def _minimal_cfg_file(self, tmp_path) -> str:
        """Write a minimal config with none of the new keys."""
        data = {
            "pdfs_path": "data/pdfs",
            "quality_control": {
                "discard_failed_branches": False,
                "grobid": {"url": "http://localhost:8070"},
            },
        }
        return _write_config(tmp_path, data)

    def test_load_qc_config_does_not_raise_for_old_yaml(self, tmp_path):
        cfg_file = self._minimal_cfg_file(tmp_path)
        # Must not raise
        cfg = load_qc_config(cfg_file)
        assert cfg is not None

    def test_load_qc_config_provides_scan_detection_for_old_yaml(self, tmp_path):
        cfg_file = self._minimal_cfg_file(tmp_path)
        cfg = load_qc_config(cfg_file)
        assert "scan_detection" in cfg["quality_control"]

    def test_load_qc_config_provides_text_fidelity_for_old_yaml(self, tmp_path):
        cfg_file = self._minimal_cfg_file(tmp_path)
        cfg = load_qc_config(cfg_file)
        assert "text_fidelity" in cfg["quality_control"]

    def test_load_qc_config_provides_section_verification_for_old_yaml(self, tmp_path):
        cfg_file = self._minimal_cfg_file(tmp_path)
        cfg = load_qc_config(cfg_file)
        assert "section_verification" in cfg["quality_control"]

    def test_load_qc_config_provides_ocr_for_old_yaml(self, tmp_path):
        cfg_file = self._minimal_cfg_file(tmp_path)
        cfg = load_qc_config(cfg_file)
        assert "ocr" in cfg["quality_control"]

    def test_load_qc_config_provides_text_processor_for_old_yaml(self, tmp_path):
        cfg_file = self._minimal_cfg_file(tmp_path)
        cfg = load_qc_config(cfg_file)
        assert "text_processor" in cfg

    def test_grobid_config_preserved_alongside_new_defaults(self, tmp_path):
        """User-supplied grobid config must survive alongside new defaults."""
        cfg_file = self._minimal_cfg_file(tmp_path)
        cfg = load_qc_config(cfg_file)
        assert cfg["quality_control"]["grobid"]["url"] == "http://localhost:8070"


# ---------------------------------------------------------------------------
# Task 4.1: TextProcessor() with empty config dict does not raise
# ---------------------------------------------------------------------------

class TestTextProcessorEmptyConfig:
    """**Validates: Requirement 9**

    Instantiating TextProcessor() with an empty config dict ({}) must use all
    defaults without raising.  This exercises the default initialization path in
    TextProcessor.__init__ itself (not just the config layer).
    """

    def test_text_processor_default_instantiation(self):
        """TextProcessor() with no arguments must not raise."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor()
        assert tp is not None

    def test_text_processor_empty_dict_instantiation(self):
        """TextProcessor({}) must not raise."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={})
        assert tp is not None

    def test_text_processor_norm_backend_default(self):
        """Default normalizer backend is 'nfkc'."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={})
        assert tp._norm_backend == "nfkc"

    def test_text_processor_word_tokenizer_backend_default(self):
        """Default word tokenizer backend is 'simple'."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={})
        assert tp._wt_backend == "simple"

    def test_text_processor_normalize_works_after_empty_init(self):
        """normalize() must work without error after empty-config init."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={})
        result = tp.normalize("Hello   World")
        assert result == "Hello World"

    def test_text_processor_compare_works_after_empty_init(self):
        """compare() must return 1.0 for identical strings."""
        from utils.text_processor import TextProcessor
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = TextProcessor(config={})
        assert tp.compare("hello", "hello") == 1.0
