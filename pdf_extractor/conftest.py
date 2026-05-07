"""
conftest.py
-----------
Root-level pytest configuration.

Ensures the project root is at the front of sys.path so that
``import pdf_extractor.extraction`` resolves to ``pdf_extractor/extraction/``.
"""

import sys
from pathlib import Path

# Insert the project root (EviTrace/) so `pdf_extractor.*` imports resolve.
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
