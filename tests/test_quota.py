#!/usr/bin/env python3
"""Quota gating must not depend on email (RESEND_API_KEY) configuration.

A deploy with a missing RESEND_API_KEY previously turned off ALL conversion
limits silently. Quotas and email are independent concerns.
"""
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("SECRET_KEY", "test-secret")


def test_guest_quota_enforced_without_resend_key(monkeypatch):
    import app as app_module
    from db import get_db_session
    from models import UsageCounter

    monkeypatch.setattr(app_module, "DISABLE_QUOTAS", False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    guest_id = f"test-guest-{uuid.uuid4()}"
    with get_db_session() as db:
        db.add(UsageCounter(
            guest_id=guest_id,
            scope="lifetime",
            conversions_count=app_module.GUEST_CONVERSION_LIMIT,
        ))
        db.commit()

    allowed, error = app_module.check_conversion_quota(None, guest_id)
    assert allowed is False
    assert error["error_code"] == "GUEST_LIMIT_EXCEEDED"


def test_disable_quotas_env_still_bypasses(monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "DISABLE_QUOTAS", True)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)

    allowed, error = app_module.check_conversion_quota(None, f"test-guest-{uuid.uuid4()}")
    assert allowed is True
    assert error is None
