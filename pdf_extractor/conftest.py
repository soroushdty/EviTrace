"""
conftest.py
-----------
Root-level pytest configuration.

Ensures the project root is at the front of sys.path so that
``import evi_trace.extraction`` resolves to the ``evi_trace/extraction/`` package
rather than the ``tests/evi_trace.extraction.py`` stub.
"""

import sys
from pathlib import Path

# Insert the project root at position 0 so the package takes precedence
# over the tests/ stub when both are on sys.path.
_project_root = str(Path(__file__).parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
