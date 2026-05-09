"""
quality_control/concerns/__init__.py
--------------------------------------
Public API for the concern strategy package.

Exports all three strategy classes, their module-level default instances,
and the ``MissingContributionError`` exception class.

Usage::

    from quality_control.concerns import (
        TextFidelityConcern,
        SectionVerificationConcern,
        TableFigureMergeConcern,
        MissingContributionError,
        DEFAULT_TEXT_FIDELITY,
        DEFAULT_SECTION_VERIFICATION,
        DEFAULT_TABLE_FIGURE_MERGE,
    )

Design reference: .kiro/specs/architecture-migration/design.md
                  §Concern Strategy Package
Requirements: 7.3
"""

from __future__ import annotations

from quality_control.concerns.table_figure_merge import (
    MissingContributionError,
    TableFigureMergeConcern,
    DEFAULT_TABLE_FIGURE_MERGE,
)
from quality_control.concerns.text_fidelity import (
    TextFidelityConcern,
    DEFAULT_TEXT_FIDELITY,
)
from quality_control.concerns.section_verification import (
    SectionVerificationConcern,
    DEFAULT_SECTION_VERIFICATION,
)

__all__ = [
    "TextFidelityConcern",
    "DEFAULT_TEXT_FIDELITY",
    "SectionVerificationConcern",
    "DEFAULT_SECTION_VERIFICATION",
    "TableFigureMergeConcern",
    "DEFAULT_TABLE_FIGURE_MERGE",
    "MissingContributionError",
]
