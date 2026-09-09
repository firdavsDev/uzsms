"""Tests for the backward-compatibility shims in uzsms.compat.

``SMS_Sender`` reproduces the 1.0.1 API surface (positional
``(number, message)`` constructor, ``.number``/``.message`` attributes,
``SendSmsOneContact()``, ``create_sms_log(phone_number, message)``) but
delegates every bit of payload/HTTP/ORM work to ``SmsClient`` -- none of
that logic is duplicated here.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from uzsms.backends import locmem
from uzsms.compat import SMS_Sender
from uzsms.dto import SendResult


@pytest.fixture(autouse=True)
def _locmem_settings():
    merged = {
        "URL": "https://example.com/sms/api",
        "LOGIN": "test-login",
        "PASSWORD": "test-password",
        "BACKEND": "uzsms.backends.locmem.LocMemBackend",
    }
    with override_settings(SMS_SETTINGS=merged):
        locmem.outbox.clear()
        yield
        locmem.outbox.clear()


@pytest.mark.django_db
def test_send_sms_one_contact_sends_through_configured_backend_and_returns_send_result():
    with pytest.warns(DeprecationWarning):
        sender = SMS_Sender("998901234567", "hi")

    with pytest.warns(DeprecationWarning):
        result = sender.SendSmsOneContact()

    assert isinstance(result, SendResult)
    assert result.ok is True
    assert len(locmem.outbox) == 1
    assert locmem.outbox[0].phone_number == "998901234567"
    assert locmem.outbox[0].text == "hi"


def test_constructor_exposes_number_and_message_attributes():
    with pytest.warns(DeprecationWarning):
        sender = SMS_Sender("998901234567", "hi")

    assert sender.number == "998901234567"
    assert sender.message == "hi"


def test_constructing_sms_sender_emits_deprecation_warning_naming_sms_client():
    with pytest.warns(DeprecationWarning, match="SmsClient"):
        SMS_Sender("998901234567", "hi")


@pytest.mark.django_db
def test_send_sms_one_contact_emits_its_own_deprecation_warning():
    with pytest.warns(DeprecationWarning):
        sender = SMS_Sender("998901234567", "hi")

    with pytest.warns(DeprecationWarning):
        sender.SendSmsOneContact()


@pytest.mark.django_db
def test_create_sms_log_emits_its_own_deprecation_warning():
    with pytest.warns(DeprecationWarning):
        sender = SMS_Sender("998901234567", "hi")

    with pytest.warns(DeprecationWarning):
        sender.create_sms_log("998901234567", "hi")
