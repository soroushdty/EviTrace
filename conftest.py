"""
conftest.py (repo root)
-----------------------
Root-level pytest configuration.

Ensures the project root (EviTrace/) is at the front of sys.path so that
all top-level packages resolve correctly during test collection:

    pdf_extractor.*   →  pdf_extractor/
    quality_control.* →  quality_control/
    pipeline.*        →  pipeline/
    utils.*           →  utils/
    agents.*          →  agents/
"""

import sys
from pathlib import Path

_project_root = str(Path(__file__).parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
