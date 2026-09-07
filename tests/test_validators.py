"""Tests for the shared validators in uzsms.validators."""

import pytest
from django.test import override_settings

from uzsms.exceptions import SmsValidationError


def test_validate_uz_phone_accepts_valid_number():
    from uzsms.validators import validate_uz_phone

    assert validate_uz_phone("998901234567") == "998901234567"


def test_validate_uz_phone_strips_surrounding_whitespace():
    from uzsms.validators import validate_uz_phone

    assert validate_uz_phone("  998901234567  ") == "998901234567"


@pytest.mark.parametrize(
    "value",
    [
        "99890123456",  # too short
        "9989012345678",  # too long
        "998901a34567",  # non-digit
        "997901234567",  # wrong prefix
        "",  # empty
        "   ",  # whitespace only
    ],
)
def test_validate_uz_phone_rejects_invalid_values(value):
    from uzsms.validators import validate_uz_phone

    with pytest.raises(SmsValidationError):
        validate_uz_phone(value)


@pytest.mark.parametrize("value", [None, 998901234567, ["998901234567"]])
def test_validate_uz_phone_rejects_non_string_input(value):
    from uzsms.validators import validate_uz_phone

    with pytest.raises(SmsValidationError):
        validate_uz_phone(value)


@override_settings(SMS_SETTINGS={"URL": "u", "LOGIN": "l", "PASSWORD": "p"})
def test_validate_message_text_accepts_normal_text():
    from uzsms.conf import sms_settings
    from uzsms.validators import validate_message_text

    sms_settings.reset()
    assert validate_message_text("hello world") == "hello world"


@override_settings(SMS_SETTINGS={"URL": "u", "LOGIN": "l", "PASSWORD": "p"})
@pytest.mark.parametrize("value", ["", "   "])
def test_validate_message_text_rejects_empty_or_whitespace_only(value):
    from uzsms.conf import sms_settings
    from uzsms.validators import validate_message_text

    sms_settings.reset()
    with pytest.raises(SmsValidationError):
        validate_message_text(value)


@override_settings(SMS_SETTINGS={"URL": "u", "LOGIN": "l", "PASSWORD": "p"})
def test_validate_message_text_rejects_text_longer_than_max_length():
    from uzsms.conf import sms_settings
    from uzsms.validators import validate_message_text

    sms_settings.reset()
    too_long = "x" * (sms_settings.MAX_MESSAGE_LENGTH + 1)
    with pytest.raises(SmsValidationError):
        validate_message_text(too_long)


@override_settings(
    SMS_SETTINGS={"URL": "u", "LOGIN": "l", "PASSWORD": "p", "MAX_MESSAGE_LENGTH": 10}
)
def test_validate_message_text_honours_overridden_max_message_length():
    from uzsms.conf import sms_settings
    from uzsms.validators import validate_message_text

    sms_settings.reset()
    assert validate_message_text("1234567890") == "1234567890"
    with pytest.raises(SmsValidationError):
        validate_message_text("12345678901")
