"""SmsClient: the package's primary public API.

Orchestrates validation, bulk log persistence, and the provider backend.
This module intentionally contains no retry, caching, or signal logic —
retries belong to the backend, and there is nothing here worth caching.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from asgiref.sync import sync_to_async

from uzsms.backends import get_async_backend, get_backend
from uzsms.backends.base import BaseAsyncSmsBackend, BaseSmsBackend
from uzsms.dto import SendResult, SmsMessage
from uzsms.exceptions import SmsBackendError
from uzsms.repository import SmsLogRecorder
from uzsms.validators import validate_message_text, validate_uz_phone


def _build_failure_results(
    messages: Sequence[SmsMessage], exc: Exception
) -> list[SendResult]:
    """Build one ``ok=False`` :class:`SendResult` per message, carrying ``exc``.

    Used to mark PENDING log rows FAILED when ``backend.send_messages``
    raises instead of returning results: without this, a raise leaves those
    rows PENDING forever, with no error recorded — the same class of defect
    (a log row that doesn't reflect the real outcome) this package was
    refactored to eliminate.

    ``SmsProviderError`` carries the broker's response body as ``.body``;
    passing it through as ``raw`` lets ``SmsLog.mark_failed`` persist it into
    ``provider_response``, since the API no longer forwards that body to
    callers. ``SmsTransportError`` (and any other exception) has no such
    attribute, so ``raw`` is correctly ``None`` for those.
    """
    raw = getattr(exc, "body", None)
    return [SendResult(message=m, ok=False, error=str(exc), raw=raw) for m in messages]


def _check_result_count(
    backend: object, messages: Sequence[SmsMessage], results: Sequence[SendResult]
) -> None:
    """Raise a clear error if ``backend`` didn't return one result per message.

    A backend that violates this contract would otherwise surface as an
    opaque ``IndexError`` in ``send()`` (or an unnamed ``ValueError`` deep in
    ``SmsLogRecorder.record_results``) — and when ``LOG_MESSAGES`` is
    disabled, ``record_results`` is never even called, so the mismatch would
    go completely undetected.
    """
    if len(results) != len(messages):
        raise SmsBackendError(
            f"{type(backend).__name__}.send_messages returned {len(results)} "
            f"result(s) for {len(messages)} message(s)."
        )


def _with_log_ids(
    logs: Sequence[object], results: Sequence[SendResult]
) -> list[SendResult]:
    """Return ``results`` with each carrying the ``pk`` of its own log row.

    ``logs`` and ``results`` correspond index-for-index (both derive from the
    same ``messages`` sequence, in order) — the same correspondence
    ``SmsLogRecorder.record_results`` relies on. Building the replacement
    list is purely in-memory (``dataclasses.replace``), so it costs no
    additional queries: every log's ``pk`` is already populated by
    ``create_pending``'s ``bulk_create``.
    """
    if not logs:
        return list(results)
    return [
        dataclasses.replace(result, log_id=log.pk) for log, result in zip(logs, results)
    ]


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

        try:
            results = self.backend.send_messages(messages)
        except Exception as exc:
            if logs:
                self.recorder.record_results(logs, _build_failure_results(messages, exc))
            raise

        _check_result_count(self.backend, messages, results)

        if logs:
            self.recorder.record_results(logs, results)

        return _with_log_ids(logs, results)


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

        try:
            results = await self.backend.send_messages(messages)
        except Exception as exc:
            if logs:
                await sync_to_async(self.recorder.record_results)(
                    logs, _build_failure_results(messages, exc)
                )
            raise

        _check_result_count(self.backend, messages, results)

        if logs:
            await sync_to_async(self.recorder.record_results)(logs, results)

        return _with_log_ids(logs, results)
