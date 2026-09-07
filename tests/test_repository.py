"""Tests for the SmsLogRecorder in uzsms.repository."""

from __future__ import annotations

import pytest
from django.test import override_settings

from uzsms.dto import SendResult, SmsMessage
from uzsms.models import SmsLog
from uzsms.repository import SmsLogRecorder


def _messages(count: int) -> list[SmsMessage]:
    return [
        SmsMessage(phone_number=f"99890123{i:04d}", text=f"hello {i}")
        for i in range(count)
    ]


@pytest.mark.django_db
def test_create_pending_issues_exactly_one_query(django_assert_num_queries):
    recorder = SmsLogRecorder(enabled=True)
    messages = _messages(10)

    with django_assert_num_queries(1):
        logs = recorder.create_pending(messages)

    assert len(logs) == 10


@pytest.mark.django_db
def test_create_pending_returns_rows_in_input_order_with_message_ids():
    recorder = SmsLogRecorder(enabled=True)
    messages = _messages(3)

    logs = recorder.create_pending(messages)

    assert [log.phone_number for log in logs] == [m.phone_number for m in messages]
    assert [log.text for log in logs] == [m.text for m in messages]
    assert [log.message_id for log in logs] == [m.message_id for m in messages]
    assert all(log.status == SmsLog.Status.PENDING for log in logs)


@pytest.mark.django_db
def test_create_pending_persists_rows_to_the_database():
    recorder = SmsLogRecorder(enabled=True)
    messages = _messages(3)

    recorder.create_pending(messages)

    assert SmsLog.objects.count() == 3


@pytest.mark.django_db
def test_create_pending_with_enabled_false_issues_zero_queries_and_returns_empty(
    django_assert_num_queries,
):
    recorder = SmsLogRecorder(enabled=False)
    messages = _messages(10)

    with django_assert_num_queries(0):
        logs = recorder.create_pending(messages)

    assert logs == []
    assert SmsLog.objects.count() == 0


@pytest.mark.django_db
def test_record_results_issues_exactly_one_query(django_assert_num_queries):
    recorder = SmsLogRecorder(enabled=True)
    messages = _messages(10)
    logs = recorder.create_pending(messages)
    results = [
        SendResult(message=m, ok=True, provider_message_id=f"pmid-{i}", raw={"i": i})
        for i, m in enumerate(messages)
    ]

    with django_assert_num_queries(1):
        recorder.record_results(logs, results)


@pytest.mark.django_db
def test_record_results_applies_mixed_outcomes_correctly():
    recorder = SmsLogRecorder(enabled=True)
    messages = _messages(2)
    logs = recorder.create_pending(messages)
    results = [
        SendResult(
            message=messages[0],
            ok=True,
            provider_message_id="pmid-0",
            raw={"status": "ok"},
        ),
        SendResult(message=messages[1], ok=False, error="provider unreachable"),
    ]

    recorder.record_results(logs, results)

    sent_log = SmsLog.objects.get(pk=logs[0].pk)
    failed_log = SmsLog.objects.get(pk=logs[1].pk)

    assert sent_log.status == SmsLog.Status.SENT
    assert sent_log.sent_at is not None
    assert sent_log.is_active is True
    assert sent_log.message_id == "pmid-0"
    assert sent_log.provider_response == {"status": "ok"}

    assert failed_log.status == SmsLog.Status.FAILED
    assert failed_log.error == "provider unreachable"
    assert failed_log.is_active is False


@pytest.mark.django_db
def test_record_results_with_empty_logs_issues_zero_queries(django_assert_num_queries):
    recorder = SmsLogRecorder(enabled=True)

    with django_assert_num_queries(0):
        recorder.record_results([], [])


@pytest.mark.django_db
def test_record_results_with_enabled_false_issues_zero_queries(django_assert_num_queries):
    messages = _messages(3)
    logs = SmsLogRecorder(enabled=True).create_pending(messages)
    results = [SendResult(message=m, ok=True) for m in messages]
    recorder = SmsLogRecorder(enabled=False)

    with django_assert_num_queries(0):
        recorder.record_results(logs, results)


@pytest.mark.django_db
def test_record_results_raises_value_error_on_mismatched_lengths():
    recorder = SmsLogRecorder(enabled=True)
    messages = _messages(2)
    logs = recorder.create_pending(messages)
    results = [SendResult(message=messages[0], ok=True)]

    with pytest.raises(ValueError):
        recorder.record_results(logs, results)


@pytest.mark.django_db
def test_default_enabled_comes_from_sms_settings_log_messages():
    from django.conf import settings as django_settings

    merged = {**django_settings.SMS_SETTINGS, "LOG_MESSAGES": False}
    with override_settings(SMS_SETTINGS=merged):
        from uzsms.conf import sms_settings

        sms_settings.reset()
        recorder = SmsLogRecorder()

        logs = recorder.create_pending(_messages(2))

    assert logs == []
