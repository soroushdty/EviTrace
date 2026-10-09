"""provenance.adapters — mapping-based adapters into provenance records.

Adapters accept plain ``Mapping`` / ``Sequence[Mapping]`` inputs only, so this
subpackage never needs to import ``pipeline`` or any other producer package.
Each adapter module appends exactly one re-export here when it is created.
"""

__all__: list[str] = []
