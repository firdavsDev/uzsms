"""Tests for the SmsMessage / SendResult value objects in uzsms.message."""

import dataclasses

import pytest

from uzsms.message import SendResult, SmsMessage


def test_message_id_defaults_differ_across_instances():
    first = SmsMessage(phone_number="+998901234567", text="hello")
    second = SmsMessage(phone_number="+998901234567", text="hello")

    assert first.message_id != second.message_id


def test_explicit_message_id_is_preserved_verbatim():
    message = SmsMessage(
        phone_number="+998901234567",
        text="hello",
        message_id="custom-id-123",
    )

    assert message.message_id == "custom-id-123"


def test_sms_message_is_frozen():
    message = SmsMessage(phone_number="+998901234567", text="hello")

    with pytest.raises(dataclasses.FrozenInstanceError):
        message.phone_number = "+998900000000"


def test_send_result_is_frozen():
    message = SmsMessage(phone_number="+998901234567", text="hello")
    result = SendResult(message=message, ok=True)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.ok = False


def test_send_result_defaults():
    message = SmsMessage(phone_number="+998901234567", text="hello")
    result = SendResult(message=message, ok=True)

    assert result.provider_message_id is None
    assert result.status_code is None
    assert result.raw is None
    assert result.error == ""
