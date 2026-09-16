"""Skip or fail integration modules whose backend is not configured.

Locally, a missing Postgres just skips the module. In CI, CRM_IT_REQUIRED=1 turns that
skip into a failure, so a half-configured job cannot pass with modules silently skipped.
"""

from __future__ import annotations

import os

import pytest


def require(available: bool, reason: str) -> None:
    if available:
        return
    if os.environ.get("CRM_IT_REQUIRED") == "1":
        pytest.fail(f"integration backend required but not available: {reason}", pytrace=False)
    pytest.skip(reason, allow_module_level=True)
