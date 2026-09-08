"""Tests for the AsyncSmsClient orchestrator in uzsms.services.

Mirrors ``tests/test_services.py``'s assertions for the sync ``SmsClient``,
against :class:`~uzsms.backends.locmem.AsyncLocMemBackend`.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from contextlib import asynccontextmanager
from unittest import mock

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from django.db import transaction
from django.test import override_settings

from uzsms.backends import locmem
from uzsms.backends.base import BaseAsyncSmsBackend
from uzsms.backends.locmem import AsyncLocMemBackend
from uzsms.dto import SendResult, SmsMessage
from uzsms.exceptions import SmsConfigurationError, SmsValidationError
from uzsms.models import SmsLog
from uzsms.repository import SmsLogRecorder
from uzsms.services import AsyncSmsClient


# ``django_assert_num_queries`` (from pytest-django) opens its connection via
# a plain synchronous call (``connection.ensure_connection()``), which trips
# Django's "SynchronousOnlyOperation" guard when called directly from a
# running event loop. Entering/exiting it through ``sync_to_async`` keeps it
# on the same thread-sensitive worker thread our ``AsyncSmsClient`` uses for
# its own ORM calls, so query capture still sees every query.
@asynccontextmanager
async def assert_num_queries_async(django_assert_num_queries, num):
    ctx = django_assert_num_queries(num)
    await sync_to_async(ctx.__enter__)()
    try:
        yield
    except BaseException:
        await sync_to_async(ctx.__exit__)(*sys.exc_info())
        raise
    else:
        await sync_to_async(ctx.__exit__)(None, None, None)


# pytest-django cannot wrap a coroutine test function in an ambient atomic
# transaction the way it does for sync tests, so ``SmsLog.objects.bulk_create``/
# ``bulk_update`` each open (and commit) their own transaction here — two
# extra queries apiece that the sync ``SmsClient`` suite never sees, because
# its ``django_db``-wrapped test is already inside a transaction, making
# those inner ``atomic(savepoint=False)`` calls no-ops. Entering one
# ourselves, before query capture starts, restores that same "already in a
# transaction" condition so the query count reflects our own code's round
# trips rather than Django's transaction bookkeeping.
@asynccontextmanager
async def atomic_async():
    ctx = transaction.atomic()
    await sync_to_async(ctx.__enter__)()
    try:
        yield
    except BaseException:
        await sync_to_async(ctx.__exit__)(*sys.exc_info())
        raise
    else:
        await sync_to_async(ctx.__exit__)(None, None, None)


def _messages(count: int) -> list[SmsMessage]:
    return [
        SmsMessage(phone_number=f"99890123{i:04d}", text=f"hello {i}")
        for i in range(count)
    ]


class _RaisingAsyncBackend(BaseAsyncSmsBackend):
    """A backend whose send_messages always raises a given exception."""

    def __init__(self, exc: Exception, *, fail_silently: bool | None = None) -> None:
        super().__init__(fail_silently=fail_silently)
        self._exc = exc

    async def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        raise self._exc


class _MixedOutcomeAsyncBackend(BaseAsyncSmsBackend):
    """A backend that fails the first message and succeeds the rest."""

    async def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        results = []
        for index, message in enumerate(messages):
            if index == 0:
                results.append(
                    SendResult(message=message, ok=False, error="provider unreachable")
                )
            else:
                results.append(
                    SendResult(message=message, ok=True, provider_message_id="pmid")
                )
        return results


@pytest.fixture
def log_disabled():
    """Override SMS_SETTINGS so message logging is disabled."""
    merged = {**django_settings.SMS_SETTINGS, "LOG_MESSAGES": False}
    with override_settings(SMS_SETTINGS=merged):
        yield


@pytest.fixture
def settings_with_async_locmem():
    """Override ``SMS_SETTINGS['BACKEND']`` to point at the async LocMem backend."""
    merged = {
        **django_settings.SMS_SETTINGS,
        "BACKEND": "uzsms.backends.locmem.AsyncLocMemBackend",
    }
    with override_settings(SMS_SETTINGS=merged):
        yield


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_validates_before_any_database_write(locmem_backend):
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )

    with pytest.raises(SmsValidationError):
        await client.send(phone_number="not-a-phone", text="hello")

    count = await SmsLog.objects.acount()
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_bulk_validates_before_any_database_write(locmem_backend):
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = [*_messages(2), SmsMessage(phone_number="bad", text="hello")]

    with pytest.raises(SmsValidationError):
        await client.send_bulk(messages)

    count = await SmsLog.objects.acount()
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_returns_a_single_send_result_not_a_list_or_string(locmem_backend):
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )

    result = await client.send(phone_number="998901234567", text="hello")

    assert isinstance(result, SendResult)
    assert not isinstance(result, list)
    assert not isinstance(result, str)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_bulk_with_fifty_messages_issues_exactly_two_queries(
    locmem_backend, django_assert_num_queries
):
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = _messages(50)

    async with atomic_async(), assert_num_queries_async(django_assert_num_queries, 2):
        results = await client.send_bulk(messages)

    assert len(results) == 50


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_bulk_returns_results_in_input_order(locmem_backend):
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = _messages(5)

    results = await client.send_bulk(messages)

    assert [r.message.phone_number for r in results] == [
        m.phone_number for m in messages
    ]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_bulk_with_logging_disabled_issues_zero_queries(
    locmem_backend, log_disabled, django_assert_num_queries
):
    client = AsyncSmsClient(backend=AsyncLocMemBackend(), recorder=SmsLogRecorder())
    messages = _messages(10)

    async with assert_num_queries_async(django_assert_num_queries, 0):
        results = await client.send_bulk(messages)

    assert len(results) == 10
    assert all(r.ok for r in results)
    count = await SmsLog.objects.acount()
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_with_logging_disabled_issues_zero_queries(
    locmem_backend, log_disabled, django_assert_num_queries
):
    client = AsyncSmsClient(backend=AsyncLocMemBackend(), recorder=SmsLogRecorder())

    async with assert_num_queries_async(django_assert_num_queries, 0):
        result = await client.send(phone_number="998901234567", text="hello")

    assert result.ok is True
    count = await SmsLog.objects.acount()
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_backend_failure_marks_the_log_row_failed_with_error(locmem_backend):
    recorder = SmsLogRecorder(enabled=True)
    client = AsyncSmsClient(backend=_MixedOutcomeAsyncBackend(), recorder=recorder)
    messages = _messages(2)

    results = await client.send_bulk(messages)

    assert results[0].ok is False
    logs = [log async for log in SmsLog.objects.order_by("created_at")]
    assert len(logs) == 2
    assert logs[0].status == SmsLog.Status.FAILED
    assert logs[0].error == "provider unreachable"
    assert logs[1].status == SmsLog.Status.SENT


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_configuration_error_propagates_even_with_fail_silently(locmem_backend):
    backend = _RaisingAsyncBackend(SmsConfigurationError("broken"), fail_silently=True)
    client = AsyncSmsClient(backend=backend, recorder=SmsLogRecorder(enabled=True))

    with pytest.raises(SmsConfigurationError):
        await client.send(phone_number="998901234567", text="hello")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_validation_error_propagates_even_with_fail_silently(locmem_backend):
    backend = AsyncLocMemBackend(fail_silently=True)
    client = AsyncSmsClient(backend=backend, recorder=SmsLogRecorder(enabled=True))

    with pytest.raises(SmsValidationError):
        await client.send(phone_number="not-a-phone", text="hello")


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_explicit_message_id_reaches_the_backend_unchanged(locmem_backend):
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )

    result = await client.send(
        phone_number="998901234567", text="hello", message_id="custom-id-123"
    )

    assert result.message.message_id == "custom-id-123"
    assert locmem.outbox[0].message_id == "custom-id-123"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_bulk_uses_the_injected_recorder_instead_of_the_default(locmem_backend):
    recorder = mock.MagicMock(spec=SmsLogRecorder)
    fake_logs = [mock.MagicMock(), mock.MagicMock()]
    recorder.create_pending.return_value = fake_logs
    client = AsyncSmsClient(backend=AsyncLocMemBackend(), recorder=recorder)
    messages = _messages(2)

    results = await client.send_bulk(messages)

    recorder.create_pending.assert_called_once_with(messages)
    recorder.record_results.assert_called_once_with(fake_logs, results)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_init_defaults_to_get_async_backend_and_recorder(settings_with_async_locmem):
    client = AsyncSmsClient()

    assert isinstance(client.backend, AsyncLocMemBackend)
    assert isinstance(client.recorder, SmsLogRecorder)
