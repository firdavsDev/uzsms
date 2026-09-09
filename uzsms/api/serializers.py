"""Serializers for the uzsms API.

``SendSmsSerializer`` delegates all validation to :mod:`uzsms.validators` —
the single source of truth for phone number and message text rules —
rather than reimplementing it. This replaces 1.0.1's ``ValidatePhoneNumber``
serializer, which reimplemented a weak ``len(phone_number) != 12`` check of
its own and assigned directly to the DRF-internal ``self._errors``.
"""

from __future__ import annotations

from rest_framework import serializers

from uzsms.exceptions import SmsValidationError
from uzsms.validators import validate_message_text, validate_uz_phone


class SendSmsSerializer(serializers.Serializer):
    """Validates the payload for :class:`uzsms.api.views.SendSmsAPIView`."""

    phone_number = serializers.CharField()
    message = serializers.CharField()

    def validate_phone_number(self, value: str) -> str:
        try:
            return validate_uz_phone(value)
        except SmsValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate_message(self, value: str) -> str:
        try:
            return validate_message_text(value)
        except SmsValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
