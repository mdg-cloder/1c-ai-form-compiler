"""Shared test helpers: the ordinary-form sample EPF and skip conditions."""

from __future__ import annotations

import importlib.util
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAMPLE = FIXTURES / "sample_ordinary.epf"
HAS_V8UNPACK = importlib.util.find_spec("v8unpack") is not None
HAS_SAMPLE = HAS_V8UNPACK and SAMPLE.is_file()
SAMPLE_REASON = ("v8unpack is not installed" if not HAS_V8UNPACK
                 else "sample tests/fixtures/sample_ordinary.epf is missing")
