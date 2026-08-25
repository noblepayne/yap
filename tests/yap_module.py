"""Shared yap module import for tests.

Loads yap.py via importlib so test files can do:
    from yap_module import yap
"""

import importlib.util
import sys
from pathlib import Path

root = Path(__file__).parent.parent
sys.path.insert(0, str(root))

spec = importlib.util.spec_from_file_location("yap", root / "yap.py")
yap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(yap)
