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
from uzsms.exceptions import (
    SmsBackendError,
    SmsConfigurationError,
    SmsProviderError,
    SmsTransportError,
    SmsValidationError,
)
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


class _WrongCountAsyncBackend(BaseAsyncSmsBackend):
    """A backend that violates the one-result-per-message contract."""

    async def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        return [SendResult(message=m, ok=True) for m in messages[:-1]]


class _FailSilentlyProviderAsyncBackend(BaseAsyncSmsBackend):
    """Mimics ``AsyncPlaymobileBackend(fail_silently=True)`` on a provider
    error: returns ``ok=False`` results carrying the broker's parsed body as
    ``raw``, instead of raising."""

    async def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        return [
            SendResult(
                message=m,
                ok=False,
                status_code=400,
                error="Playmobile broker responded with status 400.",
                raw={"error": "bad recipient"},
            )
            for m in messages
        ]


@pytest.fixture
def log_disabled():
    """Override SMS_SETTINGS so message logging is disabled."""
    merged = {**django_settings.SMS_SETTINGS, "LOG_MESSAGES": False}
    with override_settings(SMS_SETTINGS=merged):
        yield


@pytest.fixture
def settings_with_async_locmem():
    """Override ``SMS_SETTINGS['ASYNC_BACKEND']`` to point at the async LocMem backend."""
    merged = {
        **django_settings.SMS_SETTINGS,
        "ASYNC_BACKEND": "uzsms.backends.locmem.AsyncLocMemBackend",
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
    # ``send_bulk`` returns a *new* list of results carrying each log's pk
    # (see ``_with_log_ids``), so it is not the same object passed to
    # ``record_results`` (whose results don't have ``log_id`` set yet).
    recorder.record_results.assert_called_once()
    called_logs, called_results = recorder.record_results.call_args.args
    assert called_logs == fake_logs
    assert [r.message for r in called_results] == [r.message for r in results]
    assert [r.log_id for r in results] == [log.pk for log in fake_logs]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_init_defaults_to_get_async_backend_and_recorder(settings_with_async_locmem):
    client = AsyncSmsClient()

    assert isinstance(client.backend, AsyncLocMemBackend)
    assert isinstance(client.recorder, SmsLogRecorder)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_backend_raise_marks_pending_logs_failed_and_still_propagates(locmem_backend):
    backend = _RaisingAsyncBackend(SmsTransportError("broker unreachable"))
    client = AsyncSmsClient(backend=backend, recorder=SmsLogRecorder(enabled=True))
    messages = _messages(2)

    with pytest.raises(SmsTransportError):
        await client.send_bulk(messages)

    logs = [log async for log in SmsLog.objects.order_by("created_at")]
    assert len(logs) == 2
    assert all(log.status == SmsLog.Status.FAILED for log in logs)
    assert all(log.error == "broker unreachable" for log in logs)
    # SmsTransportError carries no broker body; provider_response must stay None.
    assert all(log.provider_response is None for log in logs)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_backend_raise_with_provider_error_persists_broker_body_in_log(
    locmem_backend,
):
    broker_body = {"error": "bad recipient", "account_id": "acct-1"}
    backend = _RaisingAsyncBackend(
        SmsProviderError("broker rejected the message", status_code=400, body=broker_body)
    )
    client = AsyncSmsClient(backend=backend, recorder=SmsLogRecorder(enabled=True))
    messages = _messages(2)

    with pytest.raises(SmsProviderError):
        await client.send_bulk(messages)

    logs = [log async for log in SmsLog.objects.order_by("created_at")]
    assert len(logs) == 2
    assert all(log.status == SmsLog.Status.FAILED for log in logs)
    assert all(log.error == "broker rejected the message" for log in logs)
    assert all(log.provider_response == broker_body for log in logs)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_fail_silently_provider_failure_persists_broker_body_in_log(locmem_backend):
    client = AsyncSmsClient(
        backend=_FailSilentlyProviderAsyncBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = _messages(2)

    results = await client.send_bulk(messages)

    assert all(r.ok is False for r in results)
    logs = [log async for log in SmsLog.objects.order_by("created_at")]
    assert len(logs) == 2
    assert all(log.status == SmsLog.Status.FAILED for log in logs)
    assert all(log.provider_response == {"error": "bad recipient"} for log in logs)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_backend_raise_with_logging_disabled_issues_zero_queries_and_propagates(
    locmem_backend, log_disabled, django_assert_num_queries
):
    backend = _RaisingAsyncBackend(SmsTransportError("broker unreachable"))
    client = AsyncSmsClient(backend=backend, recorder=SmsLogRecorder())
    messages = _messages(2)

    async with assert_num_queries_async(django_assert_num_queries, 0):
        with pytest.raises(SmsTransportError):
            await client.send_bulk(messages)

    count = await SmsLog.objects.acount()
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_backend_returning_wrong_result_count_raises_clear_error_not_index_error(
    locmem_backend,
):
    client = AsyncSmsClient(
        backend=_WrongCountAsyncBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = _messages(2)

    with pytest.raises(SmsBackendError, match="_WrongCountAsyncBackend"):
        await client.send_bulk(messages)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_backend_returning_wrong_result_count_marks_pending_logs_failed(
    locmem_backend,
):
    """MINOR 8 regression test (async side): a result-count mismatch marks
    the PENDING rows FAILED instead of leaving them PENDING forever."""
    client = AsyncSmsClient(
        backend=_WrongCountAsyncBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = _messages(2)

    with pytest.raises(SmsBackendError):
        await client.send_bulk(messages)

    logs = [log async for log in SmsLog.objects.order_by("created_at")]
    assert len(logs) == 2
    assert all(log.status == SmsLog.Status.FAILED for log in logs)
    assert all(log.error for log in logs)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_result_log_id_matches_the_actual_smslog_pk(locmem_backend):
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )

    result = await client.send(phone_number="998901234567", text="hello")

    log = await SmsLog.objects.aget()
    assert result.log_id == log.pk


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_bulk_assigns_each_result_its_own_log_id_not_transposed(locmem_backend):
    """Pins the log_id race fix: each result must carry the id of ITS OWN
    log row, not another message's — this would fail if the ids were
    transposed (e.g. reversed) or all set to the same row."""
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = _messages(5)

    results = await client.send_bulk(messages)

    log_ids = [r.log_id for r in results]
    assert len(set(log_ids)) == len(messages), "log ids must be distinct per message"

    logs_by_phone = {
        log.phone_number: log.pk async for log in SmsLog.objects.all()
    }
    for message, result in zip(messages, results):
        assert result.log_id == logs_by_phone[message.phone_number]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_bulk_with_logging_disabled_leaves_log_id_none(
    locmem_backend, log_disabled
):
    client = AsyncSmsClient(backend=AsyncLocMemBackend(), recorder=SmsLogRecorder())
    messages = _messages(3)

    results = await client.send_bulk(messages)

    assert all(r.log_id is None for r in results)
    count = await SmsLog.objects.acount()
    assert count == 0


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_send_bulk_with_log_ids_issues_exactly_two_queries(
    locmem_backend, django_assert_num_queries
):
    """Attaching log_id to each result is purely in-memory (dataclasses.replace);
    it must not add a third query."""
    client = AsyncSmsClient(
        backend=AsyncLocMemBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = _messages(10)

    async with atomic_async(), assert_num_queries_async(django_assert_num_queries, 2):
        results = await client.send_bulk(messages)

    assert all(r.log_id is not None for r in results)
