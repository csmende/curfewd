"""Pytest / unittest bootstrap exposing the repository root as the `timekpr` package."""

import os
import sys
import types

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

if "timekpr" not in sys.modules:
    pkg = types.ModuleType("timekpr")
    pkg.__path__ = [REPO_ROOT]
    pkg.__package__ = "timekpr"
    sys.modules["timekpr"] = pkg
