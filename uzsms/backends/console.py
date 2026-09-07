"""Console SMS backend.

Writes a readable rendering of each outgoing message to stdout instead of
sending it. Performs zero network I/O; useful for local development.
"""

from __future__ import annotations

from collections.abc import Sequence

from uzsms.backends.base import BaseSmsBackend
from uzsms.dto import SendResult, SmsMessage


class ConsoleBackend(BaseSmsBackend):
    """Prints each message to stdout and reports it as successfully sent."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        results = []
        for message in messages:
            print(
                "\n".join(
                    (
                        "-" * 40,
                        f"To: {message.phone_number}",
                        f"Message-ID: {message.message_id}",
                        message.text,
                        "-" * 40,
                    )
                )
            )
            results.append(SendResult(message=message, ok=True))
        return results
