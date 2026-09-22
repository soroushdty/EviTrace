import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent
_src_dir = str(_repo_root / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

# Repo root: lets tests import shared helpers as ``tests.helpers.<module>`` under
# ``--import-mode=importlib`` regardless of how pytest is invoked.
_root_dir = str(_repo_root)
if _root_dir not in sys.path:
    sys.path.append(_root_dir)
