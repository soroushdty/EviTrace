"""Tests for the configurable GROBID warmup strategy."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from utils.grobid_manager import GrobidServerManager


def _make_manager(warmup_config: dict) -> GrobidServerManager:
    return GrobidServerManager(
        {
            "quality_control": {
                "grobid": {
                    "warmup": warmup_config,
                }
            }
        }
    )


def test_warmup_disabled_skips_request():
    manager = _make_manager({"enabled": False})
    mock_requests = MagicMock()
    mock_requests.exceptions.Timeout = TimeoutError

    with patch.dict(sys.modules, {"requests": mock_requests}):
        manager._warmup_models()

    mock_requests.post.assert_not_called()


def test_warmup_synthetic_mode_uses_legacy_pdf():
    manager = _make_manager({"enabled": True, "mode": "synthetic_minimal_pdf"})
    mock_requests = MagicMock()
    mock_requests.exceptions.Timeout = TimeoutError
    mock_requests.post.return_value = MagicMock(status_code=200)

    with patch.dict(sys.modules, {"requests": mock_requests}):
        manager._warmup_models()

    warmup_file = mock_requests.post.call_args.kwargs["files"]["input"]
    payload = warmup_file[1].read()
    assert payload.startswith(b"%PDF-1.0")


def test_warmup_tiny_real_mode_generates_text_pdf():
    manager = _make_manager(
        {
            "enabled": True,
            "mode": "tiny_real_pdf",
            "title": "Warmup title",
            "text": "Warmup text",
        }
    )
    mock_requests = MagicMock()
    mock_requests.exceptions.Timeout = TimeoutError
    mock_requests.post.return_value = MagicMock(status_code=200)

    with patch.dict(sys.modules, {"requests": mock_requests}):
        manager._warmup_models()

    warmup_file = mock_requests.post.call_args.kwargs["files"]["input"]
    payload = warmup_file[1].read()
    assert payload.startswith(b"%PDF-1.4")
    assert b"Warmup title" in payload
    assert b"Warmup text" in payload
