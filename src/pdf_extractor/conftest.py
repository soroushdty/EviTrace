import sys
from pathlib import Path

# parent.parent of src/pdf_extractor/conftest.py → src/
_src_dir = str(Path(__file__).resolve().parent.parent)
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)
