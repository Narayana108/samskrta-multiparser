"""Shared pytest setup: tests stay offline and deterministic.

Pins the vidyut data directory before anything imports ``app`` (which resolves
``DATA_DIR`` at import time) and puts the project root on ``sys.path`` so the
test modules can import ``app`` and ``postprocess_analysis`` directly. The default suite
touches no network: the Dharmamitra engine is a remote API and is never exercised here. The one
exception is opt-in — ``SAMSKRTA_LIVE_GOLDEN=1`` enables the live golden regeneration in
``test_golden_outputs.py``, which is skipped otherwise.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

os.environ.setdefault("VIDYUT_DATA_DIR", str(PROJECT_ROOT / "data-0.4.0"))

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
