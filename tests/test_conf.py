"""Tests for the lazy, validated settings proxy in uzsms.conf."""

import warnings

import pytest
from django.test import override_settings

from uzsms.exceptions import SmsConfigurationError


def test_importing_conf_does_not_require_sms_settings(monkeypatch):
    from django.conf import settings

    monkeypatch.delattr(settings, "SMS_SETTINGS", raising=False)

    import importlib

    import uzsms.conf

    importlib.reload(uzsms.conf)  # must not raise even though SMS_SETTINGS is absent

    uzsms.conf.sms_settings.reset()
    with pytest.raises(SmsConfigurationError, match="URL"):
        _ = uzsms.conf.sms_settings.URL


@override_settings(SMS_SETTINGS={})
def test_missing_required_key_raises_sms_configuration_error():
    from uzsms.conf import sms_settings

    sms_settings.reset()
    with pytest.raises(SmsConfigurationError, match="URL"):
        _ = sms_settings.URL


@pytest.mark.parametrize(
    "key,expected",
    [
        ("BACKEND", "uzsms.backends.playmobile.PlaymobileBackend"),
        ("ORIGINATOR", "3700"),
        ("TIMEOUT", (5, 15)),
        ("MAX_RETRIES", 3),
        ("RETRY_BACKOFF", 0.5),
        ("POOL_MAXSIZE", 10),
        ("LOG_MESSAGES", True),
        ("FAIL_SILENTLY", False),
        ("MAX_MESSAGE_LENGTH", 918),
        ("PERMISSION_CLASSES", ["rest_framework.permissions.IsAuthenticated"]),
        ("THROTTLE_RATE", "20/min"),
    ],
)
@override_settings(SMS_SETTINGS={"URL": "u", "LOGIN": "l", "PASSWORD": "p"})
def test_defaults(key, expected):
    from uzsms.conf import sms_settings

    sms_settings.reset()
    assert getattr(sms_settings, key) == expected


@override_settings(
    SMS_SETTINGS={
        "URL": "u",
        "LOGIN": "l",
        "PASSWORD": "p",
        "MAX_RETRIES": 7,
    }
)
def test_user_supplied_value_overrides_default():
    from uzsms.conf import sms_settings

    sms_settings.reset()
    assert sms_settings.MAX_RETRIES == 7


@override_settings(SMS_SETTINGS={"URL": "u", "LOGIN": "l", "PASSWORD": "p"})
def test_unknown_attribute_raises_attribute_error():
    from uzsms.conf import sms_settings

    sms_settings.reset()
    with pytest.raises(AttributeError):
        _ = sms_settings.NOT_A_REAL_SETTING


@override_settings(SMS_SETTINGS={"URL": "first", "LOGIN": "l", "PASSWORD": "p"})
def test_override_settings_updates_value_via_setting_changed_signal():
    from uzsms.conf import sms_settings

    assert sms_settings.URL == "first"

    with override_settings(SMS_SETTINGS={"URL": "second", "LOGIN": "l", "PASSWORD": "p"}):
        assert sms_settings.URL == "second"

    assert sms_settings.URL == "first"


def test_values_are_cached_until_reset(monkeypatch):
    from django.conf import settings as django_settings

    from uzsms.conf import sms_settings

    monkeypatch.setattr(
        django_settings,
        "SMS_SETTINGS",
        {"URL": "u", "LOGIN": "l", "PASSWORD": "p"},
        raising=False,
    )
    sms_settings.reset()
    assert sms_settings.URL == "u"

    # Bypass override_settings/setting_changed entirely: a raw attribute
    # patch must not be picked up until reset() is called explicitly.
    monkeypatch.setattr(
        django_settings,
        "SMS_SETTINGS",
        {"URL": "changed", "LOGIN": "l", "PASSWORD": "p"},
        raising=False,
    )

    assert sms_settings.URL == "u"

    sms_settings.reset()
    assert sms_settings.URL == "changed"

    sms_settings.reset()  # do not leak the patched cache into later tests


@override_settings(SMS_SETTINGS={"SMS_URL": "legacy-url", "LOGIN": "l", "PASSWORD": "p"})
def test_legacy_url_alias_used_when_new_name_absent():
    from uzsms.conf import sms_settings

    sms_settings.reset()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = sms_settings.URL

    assert value == "legacy-url"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
    assert any("URL" in str(w.message) for w in caught)


@override_settings(
    SMS_SETTINGS={
        "URL": "new-url",
        "SMS_URL": "legacy-url",
        "LOGIN": "l",
        "PASSWORD": "p",
    }
)
def test_new_name_preferred_when_both_present():
    from uzsms.conf import sms_settings

    sms_settings.reset()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = sms_settings.URL

    assert value == "new-url"
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


@override_settings(SMS_SETTINGS={"LOGIN": "l", "PASSWORD": "p"})
def test_missing_both_new_and_legacy_name_raises_naming_new_key():
    from uzsms.conf import sms_settings

    sms_settings.reset()
    with pytest.raises(SmsConfigurationError, match="URL"):
        _ = sms_settings.URL


@override_settings(SMS_SETTINGS={"URL": "u", "SMS_LOGIN": "legacy-login", "PASSWORD": "p"})
def test_legacy_login_alias_used_when_new_name_absent():
    from uzsms.conf import sms_settings

    sms_settings.reset()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = sms_settings.LOGIN

    assert value == "legacy-login"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


@override_settings(SMS_SETTINGS={"URL": "u", "LOGIN": "l", "SMS_PASSWORD": "legacy-password"})
def test_legacy_password_alias_used_when_new_name_absent():
    from uzsms.conf import sms_settings

    sms_settings.reset()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        value = sms_settings.PASSWORD

    assert value == "legacy-password"
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)
