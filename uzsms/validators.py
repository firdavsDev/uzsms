"""Shared validators for uzsms.

These are the single source of truth for validating phone numbers and
message text: the DRF serializer and the client both call these, rather
than duplicating the logic.
"""

from __future__ import annotations

import re

from uzsms.conf import sms_settings
from uzsms.exceptions import SmsValidationError

_UZ_PHONE_RE = re.compile(r"^998\d{9}$")


def validate_uz_phone(value: str) -> str:
    """Validate a Uzbek phone number and return the cleaned value.

    Accepts ``998`` followed by exactly nine digits, with surrounding
    whitespace stripped first.
    """
    if not isinstance(value, str):
        raise SmsValidationError("Phone number must be a string.")

    cleaned = value.strip()

    if not _UZ_PHONE_RE.match(cleaned):
        raise SmsValidationError(
            f"{value!r} is not a valid Uzbek phone number; "
            "expected '998' followed by nine digits."
        )

    return cleaned


def validate_message_text(value: str) -> str:
    """Validate outgoing SMS text and return the cleaned value.

    Rejects empty or whitespace-only text, and text longer than
    ``sms_settings.MAX_MESSAGE_LENGTH``.
    """
    if not isinstance(value, str) or not value.strip():
        raise SmsValidationError("Message text must not be empty.")

    max_length = sms_settings.MAX_MESSAGE_LENGTH
    if len(value) > max_length:
        raise SmsValidationError(
            f"Message text is {len(value)} characters, which exceeds the "
            f"maximum of {max_length}."
        )

    return value
