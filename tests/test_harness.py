"""Smoke tests for the test harness itself.

These assert that Django is actually configured against this project's
settings, rather than relying on pytest's exit code for an empty
collection. A future regression that causes zero tests to collect (a
typo in this file's name, a broken conftest, etc.) will now show up as
a real test failure/collection error instead of silently reporting
green.
"""

from django.apps import apps
from django.conf import settings


def test_database_is_sqlite():
    assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"


def test_sms_app_is_installed():
    app_config = apps.get_app_config("SMS")
    assert app_config is not None


def test_sms_settings_has_broker_keys():
    assert hasattr(settings, "SMS_SETTINGS")
    for key in ("SMS_URL", "SMS_LOGIN", "SMS_PASSWORD"):
        assert key in settings.SMS_SETTINGS


def test_use_tz_is_enabled():
    assert settings.USE_TZ is True
