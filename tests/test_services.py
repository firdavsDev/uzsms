"""Tests for the SmsClient orchestrator in uzsms.services."""

from __future__ import annotations

from collections.abc import Sequence
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.test import override_settings

from uzsms.backends import locmem
from uzsms.backends.base import BaseSmsBackend
from uzsms.backends.locmem import LocMemBackend
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
from uzsms.services import AsyncSmsClient, SmsClient


def _messages(count: int) -> list[SmsMessage]:
    return [
        SmsMessage(phone_number=f"99890123{i:04d}", text=f"hello {i}")
        for i in range(count)
    ]


class _RaisingBackend(BaseSmsBackend):
    """A backend whose send_messages always raises a given exception."""

    def __init__(self, exc: Exception, *, fail_silently: bool | None = None) -> None:
        super().__init__(fail_silently=fail_silently)
        self._exc = exc

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        raise self._exc


class _MixedOutcomeBackend(BaseSmsBackend):
    """A backend that fails the first message and succeeds the rest."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
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


class _WrongCountBackend(BaseSmsBackend):
    """A backend that violates the one-result-per-message contract."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        return [SendResult(message=m, ok=True) for m in messages[:-1]]


class _FailSilentlyProviderBackend(BaseSmsBackend):
    """Mimics ``PlaymobileBackend(fail_silently=True)`` on a provider error:
    returns ``ok=False`` results carrying the broker's parsed body as ``raw``,
    instead of raising."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
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


@pytest.mark.django_db
def test_send_validates_before_any_database_write(locmem_backend):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))

    with pytest.raises(SmsValidationError):
        client.send(phone_number="not-a-phone", text="hello")

    assert SmsLog.objects.count() == 0


@pytest.mark.django_db
def test_send_bulk_validates_before_any_database_write(locmem_backend):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))
    messages = [*_messages(2), SmsMessage(phone_number="bad", text="hello")]

    with pytest.raises(SmsValidationError):
        client.send_bulk(messages)

    assert SmsLog.objects.count() == 0


@pytest.mark.django_db
def test_send_returns_a_single_send_result_not_a_list_or_string(locmem_backend):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))

    result = client.send(phone_number="998901234567", text="hello")

    assert isinstance(result, SendResult)
    assert not isinstance(result, list)
    assert not isinstance(result, str)


@pytest.mark.django_db
def test_send_bulk_with_fifty_messages_issues_exactly_two_queries(
    locmem_backend, django_assert_num_queries
):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))
    messages = _messages(50)

    with django_assert_num_queries(2):
        results = client.send_bulk(messages)

    assert len(results) == 50


@pytest.mark.django_db
def test_send_bulk_returns_results_in_input_order(locmem_backend):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))
    messages = _messages(5)

    results = client.send_bulk(messages)

    assert [r.message.phone_number for r in results] == [
        m.phone_number for m in messages
    ]


@pytest.mark.django_db
def test_send_bulk_with_logging_disabled_issues_zero_queries(
    locmem_backend, log_disabled, django_assert_num_queries
):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder())
    messages = _messages(10)

    with django_assert_num_queries(0):
        results = client.send_bulk(messages)

    assert len(results) == 10
    assert all(r.ok for r in results)
    assert SmsLog.objects.count() == 0


@pytest.mark.django_db
def test_send_with_logging_disabled_issues_zero_queries(
    locmem_backend, log_disabled, django_assert_num_queries
):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder())

    with django_assert_num_queries(0):
        result = client.send(phone_number="998901234567", text="hello")

    assert result.ok is True
    assert SmsLog.objects.count() == 0


@pytest.mark.django_db
def test_backend_failure_marks_the_log_row_failed_with_error(locmem_backend):
    recorder = SmsLogRecorder(enabled=True)
    client = SmsClient(backend=_MixedOutcomeBackend(), recorder=recorder)
    messages = _messages(2)

    results = client.send_bulk(messages)

    assert results[0].ok is False
    logs = list(SmsLog.objects.order_by("created_at"))
    assert len(logs) == 2
    assert logs[0].status == SmsLog.Status.FAILED
    assert logs[0].error == "provider unreachable"
    assert logs[1].status == SmsLog.Status.SENT


@pytest.mark.django_db
def test_configuration_error_propagates_even_with_fail_silently(locmem_backend):
    backend = _RaisingBackend(SmsConfigurationError("broken"), fail_silently=True)
    client = SmsClient(backend=backend, recorder=SmsLogRecorder(enabled=True))

    with pytest.raises(SmsConfigurationError):
        client.send(phone_number="998901234567", text="hello")


@pytest.mark.django_db
def test_validation_error_propagates_even_with_fail_silently(locmem_backend):
    backend = LocMemBackend(fail_silently=True)
    client = SmsClient(backend=backend, recorder=SmsLogRecorder(enabled=True))

    with pytest.raises(SmsValidationError):
        client.send(phone_number="not-a-phone", text="hello")


@pytest.mark.django_db
def test_explicit_message_id_reaches_the_backend_unchanged(locmem_backend):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))

    result = client.send(
        phone_number="998901234567", text="hello", message_id="custom-id-123"
    )

    assert result.message.message_id == "custom-id-123"
    assert locmem.outbox[0].message_id == "custom-id-123"


@pytest.mark.django_db
def test_send_bulk_uses_the_injected_recorder_instead_of_the_default(locmem_backend):
    recorder = mock.MagicMock(spec=SmsLogRecorder)
    fake_logs = [mock.MagicMock(), mock.MagicMock()]
    recorder.create_pending.return_value = fake_logs
    client = SmsClient(backend=LocMemBackend(), recorder=recorder)
    messages = _messages(2)

    results = client.send_bulk(messages)

    recorder.create_pending.assert_called_once_with(messages)
    # ``send_bulk`` returns a *new* list of results carrying each log's pk
    # (see ``_with_log_ids``), so it is not the same object passed to
    # ``record_results`` (whose results don't have ``log_id`` set yet).
    recorder.record_results.assert_called_once()
    called_logs, called_results = recorder.record_results.call_args.args
    assert called_logs == fake_logs
    assert [r.message for r in called_results] == [r.message for r in results]
    assert [r.log_id for r in results] == [log.pk for log in fake_logs]


@pytest.mark.django_db
def test_init_defaults_to_get_backend_and_recorder(settings_with_locmem):
    client = SmsClient()

    assert isinstance(client.backend, LocMemBackend)
    assert isinstance(client.recorder, SmsLogRecorder)


@pytest.mark.django_db
def test_async_sms_client_constructs_with_stock_settings_and_uses_async_backend():
    """CRITICAL 1 regression test.

    Under stock, unmodified ``SMS_SETTINGS`` (no ``BACKEND`` override at
    all), ``AsyncSmsClient()`` must construct successfully and resolve to
    the async Playmobile backend, while ``SmsClient()`` built from the same
    settings resolves to the sync one. Before the fix, ``AsyncSmsClient()``
    raised ``SmsConfigurationError`` because ``get_async_backend()`` read
    ``sms_settings.BACKEND``, which defaults to the *sync*
    ``PlaymobileBackend`` -- not a ``BaseAsyncSmsBackend`` subclass.
    """
    from uzsms.backends.playmobile import AsyncPlaymobileBackend, PlaymobileBackend

    async_client = AsyncSmsClient()
    sync_client = SmsClient()

    assert isinstance(async_client.backend, AsyncPlaymobileBackend)
    assert isinstance(sync_client.backend, PlaymobileBackend)


@pytest.mark.django_db
def test_backend_raise_marks_pending_logs_failed_and_still_propagates(locmem_backend):
    backend = _RaisingBackend(SmsTransportError("broker unreachable"))
    client = SmsClient(backend=backend, recorder=SmsLogRecorder(enabled=True))
    messages = _messages(2)

    with pytest.raises(SmsTransportError):
        client.send_bulk(messages)

    logs = list(SmsLog.objects.order_by("created_at"))
    assert len(logs) == 2
    assert all(log.status == SmsLog.Status.FAILED for log in logs)
    assert all(log.error == "broker unreachable" for log in logs)
    # SmsTransportError carries no broker body; provider_response must stay None.
    assert all(log.provider_response is None for log in logs)


@pytest.mark.django_db
def test_backend_raise_with_provider_error_persists_broker_body_in_log(locmem_backend):
    broker_body = {"error": "bad recipient", "account_id": "acct-1"}
    backend = _RaisingBackend(
        SmsProviderError("broker rejected the message", status_code=400, body=broker_body)
    )
    client = SmsClient(backend=backend, recorder=SmsLogRecorder(enabled=True))
    messages = _messages(2)

    with pytest.raises(SmsProviderError):
        client.send_bulk(messages)

    logs = list(SmsLog.objects.order_by("created_at"))
    assert len(logs) == 2
    assert all(log.status == SmsLog.Status.FAILED for log in logs)
    assert all(log.error == "broker rejected the message" for log in logs)
    assert all(log.provider_response == broker_body for log in logs)


@pytest.mark.django_db
def test_fail_silently_provider_failure_persists_broker_body_in_log(locmem_backend):
    client = SmsClient(
        backend=_FailSilentlyProviderBackend(), recorder=SmsLogRecorder(enabled=True)
    )
    messages = _messages(2)

    results = client.send_bulk(messages)

    assert all(r.ok is False for r in results)
    logs = list(SmsLog.objects.order_by("created_at"))
    assert len(logs) == 2
    assert all(log.status == SmsLog.Status.FAILED for log in logs)
    assert all(log.provider_response == {"error": "bad recipient"} for log in logs)


@pytest.mark.django_db
def test_backend_raise_with_logging_disabled_issues_zero_queries_and_propagates(
    locmem_backend, log_disabled, django_assert_num_queries
):
    backend = _RaisingBackend(SmsTransportError("broker unreachable"))
    client = SmsClient(backend=backend, recorder=SmsLogRecorder())
    messages = _messages(2)

    with django_assert_num_queries(0), pytest.raises(SmsTransportError):
        client.send_bulk(messages)

    assert SmsLog.objects.count() == 0


@pytest.mark.django_db
def test_backend_returning_wrong_result_count_raises_clear_error_not_index_error(
    locmem_backend,
):
    client = SmsClient(backend=_WrongCountBackend(), recorder=SmsLogRecorder(enabled=True))
    messages = _messages(2)

    with pytest.raises(SmsBackendError, match="_WrongCountBackend"):
        client.send_bulk(messages)


@pytest.mark.django_db
def test_backend_returning_wrong_result_count_marks_pending_logs_failed(
    locmem_backend,
):
    """MINOR 8 regression test: ``_check_result_count`` must run inside the
    guarded region, so a result-count mismatch marks the PENDING rows FAILED
    (like any other backend failure) instead of leaving them PENDING
    forever."""
    client = SmsClient(backend=_WrongCountBackend(), recorder=SmsLogRecorder(enabled=True))
    messages = _messages(2)

    with pytest.raises(SmsBackendError):
        client.send_bulk(messages)

    logs = list(SmsLog.objects.order_by("created_at"))
    assert len(logs) == 2
    assert all(log.status == SmsLog.Status.FAILED for log in logs)
    assert all(log.error for log in logs)


@pytest.mark.django_db
def test_send_result_log_id_matches_the_actual_smslog_pk(locmem_backend):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))

    result = client.send(phone_number="998901234567", text="hello")

    log = SmsLog.objects.get()
    assert result.log_id == log.pk


@pytest.mark.django_db
def test_send_bulk_assigns_each_result_its_own_log_id_not_transposed(locmem_backend):
    """Pins the log_id race fix: each result must carry the id of ITS OWN
    log row, not another message's — this would fail if the ids were
    transposed (e.g. reversed) or all set to the same row."""
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))
    messages = _messages(5)

    results = client.send_bulk(messages)

    log_ids = [r.log_id for r in results]
    assert len(set(log_ids)) == len(messages), "log ids must be distinct per message"

    # phone_number is distinct per message in ``_messages``, so it is a
    # reliable independent key to verify each result's log_id against.
    logs_by_phone = {log.phone_number: log.pk for log in SmsLog.objects.all()}
    for message, result in zip(messages, results):
        assert result.log_id == logs_by_phone[message.phone_number]


@pytest.mark.django_db
def test_send_bulk_with_logging_disabled_leaves_log_id_none(
    locmem_backend, log_disabled
):
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder())
    messages = _messages(3)

    results = client.send_bulk(messages)

    assert all(r.log_id is None for r in results)
    assert SmsLog.objects.count() == 0


@pytest.mark.django_db
def test_send_bulk_with_log_ids_issues_exactly_two_queries(
    locmem_backend, django_assert_num_queries
):
    """Attaching log_id to each result is purely in-memory (dataclasses.replace);
    it must not add a third query."""
    client = SmsClient(backend=LocMemBackend(), recorder=SmsLogRecorder(enabled=True))
    messages = _messages(10)

    with django_assert_num_queries(2):
        results = client.send_bulk(messages)

    assert all(r.log_id is not None for r in results)
