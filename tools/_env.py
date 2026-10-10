"""Import bootstrap shared by the measurement tools in this directory.

Every tool here imports ``app`` (and sometimes the accuracy tests, which hold the scoring helpers),
so the vidyut data directory has to be pinned before ``app`` resolves ``DATA_DIR`` at import time and
the project root has to sit on ``sys.path``. Same contract as ``tests/conftest.py``; call ``setup()``
as the first statement of a tool.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def setup(with_tests: bool = False) -> Path:
    """Pin the engine data directory and put the project root (optionally ``tests/``) on the path.

    Args:
        with_tests: also expose ``tests/`` so a tool can import the scoring helpers that live in the
            accuracy test modules instead of copying them

    Returns:
        Project root, for building paths to fixtures and result documents
    """
    os.environ.setdefault("VIDYUT_DATA_DIR", str(PROJECT_ROOT / "data-0.4.0"))
    for path in (PROJECT_ROOT, PROJECT_ROOT / "tests" if with_tests else None):
        if path and str(path) not in sys.path:
            sys.path.insert(0, str(path))
    return PROJECT_ROOT
