"""provenance.adapters — mapping-based adapters into provenance records.

Adapters accept plain ``Mapping`` / ``Sequence[Mapping]`` inputs only, so this
subpackage never needs to import ``pipeline`` or any other producer package.
Each adapter module appends exactly one re-export here when it is created.
"""

__all__: list[str] = []
from provenance.adapters.identity_adapter import run_identity_from_identities, source_node_from_identity  # noqa: E402,F401
from provenance.adapters.evidence_adapter import evidence_node_from_item, evidence_nodes_from_items  # noqa: E402,F401
from provenance.adapters.claim_adapter import claim_nodes_from_fields  # noqa: E402,F401
from provenance.adapters.derivation_adapter import derivation_from_stage_event, derivations_from_qc_provenance  # noqa: E402,F401
