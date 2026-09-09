"""Backward-compatibility shims for uzsms 1.0.1's public API.

``SMS_Sender`` reproduces the original positional signature and method
names so that 1.0.1 call sites keep working, but every method here
delegates to :class:`~uzsms.services.SmsClient` -- none of the payload,
HTTP, or ORM logic that ``SmsClient`` (and its collaborators) already own
is duplicated in this module. Each entry point emits its own
``DeprecationWarning`` naming its replacement.
"""

from __future__ import annotations

import warnings
from typing import Any

from uzsms.dto import SendResult, SmsMessage
from uzsms.models import SmsLog
from uzsms.services import SmsClient


class SMS_Sender:
    """Deprecated 1.0.1-compatible shim. Use :class:`uzsms.SmsClient` instead."""

    def __init__(self, number: Any, message: str) -> None:
        warnings.warn(
            "SMS_Sender is deprecated and will be removed in a future "
            "release; use uzsms.SmsClient instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.number = number
        self.message = message

    def SendSmsOneContact(self) -> SendResult:
        """Send the configured message and return a :class:`SendResult`.

        Delegates entirely to :meth:`SmsClient.send`, which already
        creates the log row -- this method does not call
        :meth:`create_sms_log` itself, to avoid logging the same message
        twice.
        """
        warnings.warn(
            "SMS_Sender.SendSmsOneContact is deprecated; use "
            "uzsms.SmsClient.send instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return SmsClient().send(self.number, self.message)

    def create_sms_log(self, phone_number: Any, message: str) -> SmsLog | None:
        """Create a PENDING log row for ``(phone_number, message)``.

        Delegates to the same :class:`~uzsms.repository.SmsLogRecorder`
        that :class:`SmsClient` uses internally, rather than writing to
        ``SmsLog`` directly.
        """
        warnings.warn(
            "SMS_Sender.create_sms_log is deprecated; uzsms.SmsClient logs "
            "automatically when sending. Use uzsms.SmsClient.send instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        client = SmsClient()
        logs = client.recorder.create_pending(
            [SmsMessage(phone_number=phone_number, text=message)]
        )
        return logs[0] if logs else None
