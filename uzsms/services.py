"""SmsClient: the package's primary public API.

Orchestrates validation, bulk log persistence, and the provider backend.
This module intentionally contains no retry, caching, or signal logic —
retries belong to the backend, and there is nothing here worth caching.
"""

from __future__ import annotations

from collections.abc import Sequence

from asgiref.sync import sync_to_async

from uzsms.backends import get_async_backend, get_backend
from uzsms.backends.base import BaseAsyncSmsBackend, BaseSmsBackend
from uzsms.dto import SendResult, SmsMessage
from uzsms.repository import SmsLogRecorder
from uzsms.validators import validate_message_text, validate_uz_phone


class SmsClient:
    """Ties validation, bulk logging, and the backend together.

    Validation runs over every message before anything is written to the
    database. Log rows are created and updated in bulk, so sending any
    number of messages costs a constant, small number of queries.
    """

    def __init__(
        self,
        backend: BaseSmsBackend | None = None,
        recorder: SmsLogRecorder | None = None,
    ) -> None:
        self.backend = backend if backend is not None else get_backend()
        self.recorder = recorder if recorder is not None else SmsLogRecorder()

    def send(
        self, phone_number: str, text: str, *, message_id: str | None = None
    ) -> SendResult:
        """Send a single SMS message and return its :class:`SendResult`."""
        if message_id is None:
            message = SmsMessage(phone_number=phone_number, text=text)
        else:
            message = SmsMessage(
                phone_number=phone_number, text=text, message_id=message_id
            )

        results = self.send_bulk([message])
        return results[0]

    def send_bulk(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        """Validate, log, and send ``messages``, returning results in order."""
        for message in messages:
            validate_uz_phone(message.phone_number)
            validate_message_text(message.text)

        logs = self.recorder.create_pending(messages)
        results = self.backend.send_messages(messages)

        if logs:
            self.recorder.record_results(logs, results)

        return results


class AsyncSmsClient:
    """Async counterpart to :class:`SmsClient`, for ASGI/async Django users.

    Same validation-before-write and constant-query-count contracts as
    ``SmsClient``; every ORM call goes through
    ``asgiref.sync.sync_to_async`` since Django's ORM is not async-safe.
    """

    def __init__(
        self,
        backend: BaseAsyncSmsBackend | None = None,
        recorder: SmsLogRecorder | None = None,
    ) -> None:
        self.backend = backend if backend is not None else get_async_backend()
        self.recorder = recorder if recorder is not None else SmsLogRecorder()

    async def send(
        self, phone_number: str, text: str, *, message_id: str | None = None
    ) -> SendResult:
        """Send a single SMS message and return its :class:`SendResult`."""
        if message_id is None:
            message = SmsMessage(phone_number=phone_number, text=text)
        else:
            message = SmsMessage(
                phone_number=phone_number, text=text, message_id=message_id
            )

        results = await self.send_bulk([message])
        return results[0]

    async def send_bulk(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        """Validate, log, and send ``messages``, returning results in order."""
        for message in messages:
            validate_uz_phone(message.phone_number)
            validate_message_text(message.text)

        logs = await sync_to_async(self.recorder.create_pending)(messages)
        results = await self.backend.send_messages(messages)

        if logs:
            await sync_to_async(self.recorder.record_results)(logs, results)

        return results
