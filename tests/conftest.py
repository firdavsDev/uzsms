"""Shared pytest fixtures for the uzsms test suite."""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.test import override_settings


@pytest.fixture
def locmem_backend():
    """Clear the LocMem backend's module-level outbox before and after the test."""
    from uzsms.backends import locmem

    locmem.outbox.clear()
    yield
    locmem.outbox.clear()


@pytest.fixture
def settings_with_locmem():
    """Override ``SMS_SETTINGS['BACKEND']`` to point at the LocMem backend."""
    merged = {
        **django_settings.SMS_SETTINGS,
        "BACKEND": "uzsms.backends.locmem.LocMemBackend",
    }
    with override_settings(SMS_SETTINGS=merged):
        yield
