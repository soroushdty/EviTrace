"""provenance — first-class provenance subsystem for EviTrace.

A leaf package: it imports only ``utils`` and the Python standard library, and
needs no network service or model-provider credential (provenance-core 1.5).
``pipeline`` is its sole integration point; ``agents``, ``pdf_extractor``,
``quality_control`` and ``text_processing`` never import it. Both directions
are enforced by ``tests/test_dependency_directions.py``.

This is the package's public surface. Its re-exports (models, recorder, graph,
reports) are added here by the tasks that create those modules.
"""

from provenance import models as _models
from provenance.models import *  # noqa: F401,F403 -- re-exported public surface
from provenance.recorder import ProvenanceRecorder

__all__: list[str] = [*_models.__all__, "ProvenanceRecorder"]
