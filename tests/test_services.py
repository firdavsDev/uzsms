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
from uzsms.exceptions import SmsConfigurationError, SmsValidationError
from uzsms.models import SmsLog
from uzsms.repository import SmsLogRecorder
from uzsms.services import SmsClient


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
    recorder.record_results.assert_called_once_with(fake_logs, results)


@pytest.mark.django_db
def test_init_defaults_to_get_backend_and_recorder(settings_with_locmem):
    client = SmsClient()

    assert isinstance(client.backend, LocMemBackend)
    assert isinstance(client.recorder, SmsLogRecorder)
