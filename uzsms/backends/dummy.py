"""Dummy SMS backend.

Discards every message without sending it. Performs zero network I/O;
useful for tests or environments where SMS sending should be a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

from uzsms.backends.base import BaseSmsBackend
from uzsms.dto import SendResult, SmsMessage


class DummyBackend(BaseSmsBackend):
    """Discards all messages and reports each as successfully sent."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        return [SendResult(message=message, ok=True) for message in messages]
