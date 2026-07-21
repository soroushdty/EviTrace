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
    _ALL_KNOWN_TOP_LEVEL_KEYS,
    _QC_DEFAULTS,
    _deep_merge,
    get_qc_config,
    load_local_config,
    load_openai_config,
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
    assert merged["adjudicator"]["strategy"] == "majority_vote"

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
        assert cfg["quality_control"]["adjudicator"]["strategy"] == "majority_vote"

    # reconciler
    def test_downstream_enable_tei_export_default_false(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["reconciler"]["enable_tei_export"] is False

    def test_downstream_enable_annotation_export_default_false(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["reconciler"]["enable_annotation_export"] is False

    def test_export_csv_defaults_to_false_when_omitted(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["export_csv"] is False

    def test_export_csv_can_be_enabled_at_top_level(self, tmp_path):
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "export_csv": True,
        })
        cfg = load_local_config(cfg_file)
        assert cfg["export_csv"] is True


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
# Unit tests: semantic_verification defaults (Requirements 9.2, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12)
# ---------------------------------------------------------------------------

class TestSemanticQCDefaults:
    """**Validates: Requirements 9.2, 9.6, 9.7, 9.8, 9.9, 9.10, 9.11, 9.12**

    The semantic_verification sub-section must be present in _QC_DEFAULTS and must
    be surfaced via load_local_config even when the caller omits quality_control
    entirely from config.yaml. The old semantic_qc key must NOT be present.
    """

    def _load(self, tmp_path, qc_override: dict | None = None) -> dict:
        data: dict = {"pdfs_path": "data/pdfs"}
        if qc_override is not None:
            data["quality_control"] = qc_override
        cfg_file = _write_config(tmp_path, data)
        return load_local_config(cfg_file)

    # --- presence ---

    def test_semantic_verification_in_qc_defaults_dict(self):
        """semantic_verification must exist directly in _QC_DEFAULTS['quality_control']."""
        assert "semantic_verification" in _QC_DEFAULTS["quality_control"]

    def test_semantic_qc_not_in_qc_defaults_dict(self):
        """semantic_qc must NOT exist in _QC_DEFAULTS['quality_control'] (deleted key)."""
        assert "semantic_qc" not in _QC_DEFAULTS["quality_control"]

    def test_semantic_verification_present_when_qc_omitted(self, tmp_path):
        """load_local_config must produce semantic_verification even when quality_control is absent."""
        cfg = self._load(tmp_path)
        assert "semantic_verification" in cfg["quality_control"]

    def test_semantic_verification_present_when_qc_empty(self, tmp_path):
        """load_local_config must produce semantic_verification when quality_control is an empty dict."""
        cfg = self._load(tmp_path, qc_override={})
        assert "semantic_verification" in cfg["quality_control"]

    # --- default values ---

    def test_semantic_verification_enabled_default_false(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_verification"]["enabled"] is False

    def test_semantic_verification_model_name_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_verification"]["model_name"] == "BAAI/bge-base-en-v1.5"

    def test_semantic_verification_similarity_threshold_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_verification"]["similarity_threshold"] == 0.85

    def test_semantic_verification_max_sentences_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_verification"]["max_sentences"] == 10000

    def test_semantic_verification_on_index_unavailable_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_verification"]["on_index_unavailable"] == "skip"

    def test_semantic_verification_extractor_agreement_enabled_default_false(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_verification"]["extractor_agreement"]["enabled"] is False

    def test_semantic_verification_extractor_agreement_len_filter_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_verification"]["extractor_agreement"]["len_filter"] == 40

    def test_semantic_verification_extractor_agreement_max_examples_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["semantic_verification"]["extractor_agreement"]["max_examples"] == 10

    # --- deep-merge: user value wins ---

    def test_semantic_verification_user_enabled_true_overrides(self, tmp_path):
        cfg = self._load(tmp_path, qc_override={"semantic_verification": {"enabled": True}})
        assert cfg["quality_control"]["semantic_verification"]["enabled"] is True

    def test_semantic_verification_partial_override_preserves_other_defaults(self, tmp_path):
        """Overriding enabled must not wipe out model_name."""
        cfg = self._load(tmp_path, qc_override={"semantic_verification": {"enabled": True}})
        assert cfg["quality_control"]["semantic_verification"]["model_name"] == "BAAI/bge-base-en-v1.5"

    def test_semantic_verification_user_threshold_overrides(self, tmp_path):
        cfg = self._load(tmp_path, qc_override={"semantic_verification": {"similarity_threshold": 0.9}})
        assert cfg["quality_control"]["semantic_verification"]["similarity_threshold"] == 0.9

    # --- no faiss/torch/sentence_transformers keys (Req 14.1) ---

    def test_semantic_verification_no_faiss_key(self):
        sv = _QC_DEFAULTS["quality_control"]["semantic_verification"]
        for key in sv:
            assert "faiss" not in key.lower()

    def test_semantic_verification_no_torch_key(self):
        sv = _QC_DEFAULTS["quality_control"]["semantic_verification"]
        for key in sv:
            assert "torch" not in key.lower()

    def test_semantic_verification_no_sentence_transformers_key(self):
        sv = _QC_DEFAULTS["quality_control"]["semantic_verification"]
        for key in sv:
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

    def test_local_metrics_extraction_coverage_ratio_threshold(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["extraction_coverage_ratio_threshold"] == 0.6

    def test_local_metrics_long_sentence_word_threshold(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["long_sentence_word_threshold"] == 120

    def test_local_metrics_long_sentence_max_fraction(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["long_sentence_max_fraction"] == 0.12

    def test_local_metrics_expected_sections_default(self, tmp_path):
        cfg = self._load(tmp_path)
        assert cfg["quality_control"]["local_metrics"]["expected_sections"] == [
            "abstract", "introduction", "methods", "results", "discussion", "conclusion"
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
        assert cfg["quality_control"]["local_metrics"]["extraction_coverage_ratio_threshold"] == 0.6

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

    def test_get_qc_config_includes_semantic_verification(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_local_config(cfg_file)
        qc = get_qc_config(cfg)
        assert "semantic_verification" in qc


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
        """class must default to 'text_processing.composite.DefaultTextProcessor'."""
        assert _QC_DEFAULTS["text_processor"]["class"] == "text_processing.composite.DefaultTextProcessor"

    def test_text_processor_sentence_tokenizer_backend_default(self):
        """sentence_tokenizer.backend must default to 'nltk_punkt'."""
        assert _QC_DEFAULTS["text_processor"]["sentence_tokenizer"]["backend"] == "nltk_punkt"

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
        assert cfg["text_processor"]["class"] == "text_processing.composite.DefaultTextProcessor"

    def test_load_qc_config_text_processor_sentence_backend_default(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_qc_config(str(cfg_file))
        assert cfg["text_processor"]["sentence_tokenizer"]["backend"] == "nltk_punkt"

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
        assert cfg["text_processor"]["sentence_tokenizer"]["backend"] == "nltk_punkt"


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

    def test_grobid_timeout_default_is_300_seconds(self, tmp_path):
        """New configs should inherit the shorter GROBID timeout default."""
        cfg_file = self._minimal_cfg_file(tmp_path)
        cfg = load_qc_config(cfg_file)
        assert cfg["quality_control"]["grobid"]["timeout"] == 300

    def test_grobid_warmup_defaults_are_present(self, tmp_path):
        """New configs should expose configurable GROBID warmup settings."""
        cfg_file = self._minimal_cfg_file(tmp_path)
        cfg = load_qc_config(cfg_file)
        warmup = cfg["quality_control"]["grobid"]["warmup"]
        assert warmup["enabled"] is True
        assert warmup["mode"] == "tiny_real_pdf"
        assert warmup["timeout"] == 300
        assert warmup["title"] == "EviTrace warmup"
        assert "Warmup document" in warmup["text"]


# ---------------------------------------------------------------------------
# Task 4.1: TextProcessor() with empty config dict does not raise
# ---------------------------------------------------------------------------

class TestTextProcessorEmptyConfig:
    """**Validates: Requirement 9**

    Instantiating ScispaCySentenceSegment() with an empty config dict ({}) must use all
    defaults without raising.  This exercises the default initialization path in
    the new text_processing.base module.
    """

    def test_text_processor_default_instantiation(self):
        """ScispaCySentenceSegment() with no arguments must not raise."""
        from text_processing.base import ScispaCySentenceSegment
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = ScispaCySentenceSegment()
        assert tp is not None

    def test_text_processor_empty_dict_instantiation(self):
        """ScispaCySentenceSegment(config={}) must not raise."""
        from text_processing.base import ScispaCySentenceSegment
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = ScispaCySentenceSegment(config={})
        assert tp is not None

    def test_text_processor_norm_backend_default(self):
        """Default config class path points to text_processing.composite.DefaultTextProcessor."""
        from utils.config_utils import _QC_DEFAULTS
        assert _QC_DEFAULTS["text_processor"]["class"] == "text_processing.composite.DefaultTextProcessor"

    def test_text_processor_word_tokenizer_backend_default(self):
        """Default word tokenizer backend in config is 'simple'."""
        from utils.config_utils import _QC_DEFAULTS
        assert _QC_DEFAULTS["text_processor"]["word_tokenizer"]["backend"] == "simple"

    def test_text_processor_tokenize_sentences_works(self):
        """tokenize_sentences() must work on a ScispaCySentenceSegment instance."""
        from text_processing.base import ScispaCySentenceSegment
        mock_spacy = MagicMock()
        mock_scispacy = MagicMock()
        mock_sent = MagicMock(); mock_sent.text = "Sentence one."
        mock_doc = MagicMock(); mock_doc.sents = [mock_sent]
        mock_nlp = MagicMock(return_value=mock_doc)
        mock_spacy.load.return_value = mock_nlp
        with patch.dict(sys.modules, {"scispacy": mock_scispacy, "spacy": mock_spacy}):
            tp = ScispaCySentenceSegment(config={})
        result = tp.tokenize_sentences("Hello World")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Task 8.4: token_budgets / cache_diagnostics top-level config keys
# ---------------------------------------------------------------------------

class TestTokenBudgetsAndCacheDiagnosticsConfig:
    """**Validates: Requirements 7.5, 8.6**

    `token_budgets` and `cache_diagnostics` are new top-level config.yaml
    sections (design.md "Config Additions"). They must be registered in
    `_ALL_KNOWN_TOP_LEVEL_KEYS` so `load_local_config` does not reject them
    as unknown keys (mirroring the existing
    `test_unknown_top_level_key_still_raises` pattern), and
    `load_openai_config` must surface the raw `token_budgets` mapping so
    that `pipeline.token_budget.load_budgets(openai_config)` -- already
    wired into `pdf_processor.py` by task 8.2 -- actually observes
    user-configured overrides instead of silently always falling back to
    documented defaults.
    """

    # --- registration ---

    def test_token_budgets_registered_as_known_top_level_key(self):
        assert "token_budgets" in _ALL_KNOWN_TOP_LEVEL_KEYS

    def test_cache_diagnostics_registered_as_known_top_level_key(self):
        assert "cache_diagnostics" in _ALL_KNOWN_TOP_LEVEL_KEYS

    # --- load_local_config must not raise on the new keys ---

    def test_token_budgets_key_does_not_raise(self, tmp_path):
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "token_budgets": {
                "extraction_chunk": 100000,
                "validation_repair": 20000,
                "synthesis": 120000,
                "cache_warmup": 10000,
            },
        })
        cfg = load_local_config(cfg_file)
        assert cfg is not None

    def test_cache_diagnostics_key_does_not_raise(self, tmp_path):
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "cache_diagnostics": {"threshold": 50},
        })
        cfg = load_local_config(cfg_file)
        assert cfg is not None

    def test_both_new_keys_together_do_not_raise(self, tmp_path):
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "token_budgets": {"extraction_chunk": 100000},
            "cache_diagnostics": {"threshold": 50},
        })
        cfg = load_local_config(cfg_file)
        assert cfg is not None

    def test_unknown_top_level_key_still_raises_alongside_new_keys(self, tmp_path):
        """Registering the new keys must not accidentally widen the unknown-key check."""
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "token_budgets": {"extraction_chunk": 100000},
            "totally_unknown_key": True,
        })
        with pytest.raises(ValueError, match="Unknown config keys"):
            load_local_config(cfg_file)

    # --- load_openai_config surfaces token_budgets for load_budgets() ---

    def test_load_openai_config_surfaces_token_budgets_section(self, tmp_path):
        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "token_budgets": {
                "extraction_chunk": 55000,
                "validation_repair": 15000,
                "synthesis": 90000,
                "cache_warmup": 8000,
            },
        })
        cfg = load_openai_config(str(cfg_file))
        assert cfg["token_budgets"] == {
            "extraction_chunk": 55000,
            "validation_repair": 15000,
            "synthesis": 90000,
            "cache_warmup": 8000,
        }

    def test_load_openai_config_token_budgets_defaults_to_empty_dict_when_absent(self, tmp_path):
        cfg_file = _write_config(tmp_path, {"pdfs_path": "data/pdfs"})
        cfg = load_openai_config(str(cfg_file))
        assert cfg["token_budgets"] == {}

    def test_load_budgets_reads_actual_yaml_overrides_via_load_openai_config(self, tmp_path):
        """End-to-end: a real config.yaml file's `token_budgets` overrides
        must actually reach `pipeline.token_budget.load_budgets()` through
        the exact dict `pdf_processor.py` passes it (the dict returned by
        `load_openai_config()`), not just fall back to documented defaults
        regardless of what is configured on disk."""
        from pipeline.token_budget import load_budgets

        cfg_file = _write_config(tmp_path, {
            "pdfs_path": "data/pdfs",
            "token_budgets": {"extraction_chunk": 42000},
        })
        openai_cfg = load_openai_config(str(cfg_file))
        budgets = load_budgets(openai_cfg)
        assert budgets["extraction_chunk"] == 42000
        # Unspecified stages still fall back to documented defaults (Req 7.6).
        assert budgets["validation_repair"] == 20000
        assert budgets["synthesis"] == 120000
        assert budgets["cache_warmup"] == 10000

    # --- repo config.yaml itself must match design.md exactly ---

    def test_repo_config_yaml_token_budgets_match_design_defaults(self):
        """`configs/config.yaml` values must exactly match design.md's
        'Config Additions' section (100000/20000/120000/10000)."""
        cfg = load_openai_config()
        assert cfg["token_budgets"] == {
            "extraction_chunk": 100000,
            "validation_repair": 20000,
            "synthesis": 120000,
            "cache_warmup": 10000,
        }

    def test_repo_config_yaml_cache_diagnostics_threshold_is_50(self):
        import yaml as _yaml
        from pathlib import Path as _Path

        repo_config_path = _Path(__file__).resolve().parents[3] / "configs" / "config.yaml"
        with open(repo_config_path, encoding="utf-8") as f:
            raw = _yaml.safe_load(f)
        assert raw["cache_diagnostics"]["threshold"] == 50
