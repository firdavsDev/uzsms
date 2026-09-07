"""In-memory SMS backend for tests.

Mirrors the idiom of Django's ``django.core.mail.outbox``: every message
sent through :class:`LocMemBackend` is appended to the module-level
``outbox`` list, so tests can import this module and inspect it directly
without holding a reference to the backend instance. Performs zero
network I/O.
"""

from __future__ import annotations

from collections.abc import Sequence

from uzsms.backends.base import BaseSmsBackend
from uzsms.dto import SendResult, SmsMessage

outbox: list[SmsMessage] = []


class LocMemBackend(BaseSmsBackend):
    """Appends each sent message to the module-level :data:`outbox`."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        results = []
        for message in messages:
            outbox.append(message)
            results.append(SendResult(message=message, ok=True))
        return results
