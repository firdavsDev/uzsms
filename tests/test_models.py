"""Tests for the SmsLog model."""

from __future__ import annotations

import pytest

from uzsms.dto import SendResult, SmsMessage
from uzsms.models import SmsLog


def _result(*, ok: bool, provider_message_id=None, raw=None, error="") -> SendResult:
    message = SmsMessage(phone_number="998901234567", text="hello")
    return SendResult(
        message=message,
        ok=ok,
        provider_message_id=provider_message_id,
        raw=raw,
        error=error,
    )


@pytest.mark.django_db
def test_new_log_defaults_to_pending_and_inactive():
    log = SmsLog.objects.create(phone_number="998901234567", text="hello")

    assert log.status == SmsLog.Status.PENDING
    assert log.is_active is False


@pytest.mark.django_db
def test_mark_sent_sets_fields_without_touching_the_database(django_assert_num_queries):
    log = SmsLog.objects.create(phone_number="998901234567", text="hello")
    result = _result(ok=True, provider_message_id="abc-123", raw={"status": "ok"})

    with django_assert_num_queries(0):
        log.mark_sent(result)

    assert log.status == SmsLog.Status.SENT
    assert log.sent_at is not None
    assert log.message_id == "abc-123"
    assert log.provider_response == {"status": "ok"}
    assert log.is_active is True


@pytest.mark.django_db
def test_mark_failed_sets_fields_and_leaves_is_active_false(django_assert_num_queries):
    log = SmsLog.objects.create(phone_number="998901234567", text="hello")
    result = _result(ok=False, error="provider unreachable")

    with django_assert_num_queries(0):
        log.mark_failed(result)

    assert log.status == SmsLog.Status.FAILED
    assert log.error == "provider unreachable"
    assert log.is_active is False
    assert log.provider_response is None


@pytest.mark.django_db
def test_mark_failed_persists_provider_response_when_raw_is_present(
    django_assert_num_queries,
):
    log = SmsLog.objects.create(phone_number="998901234567", text="hello")
    body = {"error": "bad recipient", "account_id": "acct-1"}
    result = _result(ok=False, error="broker rejected the message", raw=body)

    with django_assert_num_queries(0):
        log.mark_failed(result)

    assert log.status == SmsLog.Status.FAILED
    assert log.error == "broker rejected the message"
    assert log.provider_response == body
    assert log.is_active is False


@pytest.mark.django_db
def test_default_ordering_returns_newest_first():
    first = SmsLog.objects.create(phone_number="998901234567", text="first")
    second = SmsLog.objects.create(phone_number="998901234568", text="second")

    logs = list(SmsLog.objects.all())

    assert logs == [second, first]


@pytest.mark.django_db
def test_text_accepts_a_string_longer_than_255_characters():
    long_text = "x" * 500
    log = SmsLog.objects.create(phone_number="998901234567", text=long_text)

    log.refresh_from_db()

    assert log.text == long_text


def test_smslog_db_table_is_sms_smslog():
    assert SmsLog._meta.db_table == "SMS_smslog"


def test_smslog_app_label_is_sms():
    assert SmsLog._meta.app_label == "SMS"
