"""Tests for uzsms's top-level public API surface (uzsms/__init__.py).

Critically, importing ``uzsms`` must never require ``SMS_SETTINGS`` to be
configured and must never trip ``AppRegistryNotReady`` by reaching for
Django models at module import time. The "settings absent" case runs in a
subprocess so it exercises a genuinely fresh interpreter.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest


def test_import_uzsms_without_sms_settings_does_not_raise():
    """A fresh interpreter with no SMS_SETTINGS at all must still import uzsms."""
    script = (
        "import django\n"
        "from django.conf import settings\n"
        "settings.configure(\n"
        "    DEBUG=True,\n"
        "    DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},\n"
        "    INSTALLED_APPS=[\n"
        "        'django.contrib.contenttypes',\n"
        "        'django.contrib.auth',\n"
        "        'uzsms',\n"
        "    ],\n"
        "    USE_TZ=True,\n"
        ")\n"
        "django.setup()\n"
        "import uzsms\n"
        "print('OK')\n"
    )
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


@pytest.mark.parametrize(
    "name",
    [
        "SmsClient",
        "AsyncSmsClient",
        "SmsMessage",
        "SendResult",
        "SmsLogRecorder",
        "SmsError",
        "SmsConfigurationError",
        "SmsValidationError",
        "SmsTransportError",
        "SmsProviderError",
        "SmsBackendError",
        "get_backend",
        "get_async_backend",
        "SMS_Sender",
    ],
)
def test_every_name_in_all_is_importable_from_top_level_package(name):
    import uzsms

    assert name in uzsms.__all__
    assert getattr(uzsms, name) is not None


def test_all_exports_exactly_the_expected_names():
    import uzsms

    expected = {
        "SmsClient",
        "AsyncSmsClient",
        "SmsMessage",
        "SendResult",
        "SmsLogRecorder",
        "SmsError",
        "SmsConfigurationError",
        "SmsValidationError",
        "SmsTransportError",
        "SmsProviderError",
        "SmsBackendError",
        "get_backend",
        "get_async_backend",
        "SMS_Sender",
    }
    assert set(uzsms.__all__) == expected


def test_import_sms_top_level_module_no_longer_exists():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("SMS")
