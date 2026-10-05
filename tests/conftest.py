"""Shared pytest setup: tests stay offline and deterministic.

Pins the vidyut data directory before anything imports ``app`` (which resolves
``DATA_DIR`` at import time) and puts the project root on ``sys.path`` so the
test modules can import ``app`` and ``postprocess_analysis`` directly. No test may touch
the network: the Dharmamitra engine is a remote API and is never exercised here.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("VIDYUT_DATA_DIR", str(PROJECT_ROOT / "data-0.4.0"))

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
