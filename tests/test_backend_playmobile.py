"""Tests for the Playmobile SMS broker backend.

Exercises the four production defects this backend fixes over the 1.0.1
``sms_utils.py`` implementation: missing timeout, one connection per
message, one HTTP request per message, and a hardcoded ``message-id``.
"""

from __future__ import annotations

import base64
import json

import pytest
import requests
import responses
from django.conf import settings as django_settings
from django.test import override_settings

from uzsms.backends.playmobile import PlaymobileBackend, build_payload, get_session, reset_session
from uzsms.conf import sms_settings
from uzsms.dto import SmsMessage
from uzsms.exceptions import SmsProviderError, SmsTransportError

BASE_SMS_SETTINGS = {
    "URL": "https://broker.example.com/broker-api/send",
    "LOGIN": "test-login",
    "PASSWORD": "test-password",
    "ORIGINATOR": "3700",
    "TIMEOUT": (2, 5),
    "MAX_RETRIES": 2,
    "RETRY_BACKOFF": 0,
    "POOL_MAXSIZE": 10,
    "FAIL_SILENTLY": False,
}


def _merged(**overrides):
    return {**BASE_SMS_SETTINGS, **overrides}


@pytest.fixture(autouse=True)
def _playmobile_settings():
    with override_settings(SMS_SETTINGS=BASE_SMS_SETTINGS):
        reset_session()
        yield
    reset_session()


def _messages(count: int) -> list[SmsMessage]:
    return [
        SmsMessage(phone_number=f"99890123{i:04d}", text=f"hello {i}")
        for i in range(count)
    ]


def test_build_payload_is_module_level_function():
    # Controller ruling R3: build_payload must be callable without an instance.
    messages = _messages(2)
    payload = build_payload(messages)
    assert isinstance(payload, dict)
    assert len(payload["messages"]) == 2


@responses.activate
def test_sending_three_messages_issues_exactly_one_request():
    responses.add(responses.POST, sms_settings.URL, json={"status": "ok"}, status=200)
    messages = _messages(3)
    backend = PlaymobileBackend()

    results = backend.send_messages(messages)

    assert len(responses.calls) == 1
    body = json.loads(responses.calls[0].request.body)
    assert len(body["messages"]) == 3
    assert len(results) == 3


@responses.activate
def test_each_entry_has_distinct_message_id_matching_the_sms_message():
    responses.add(responses.POST, sms_settings.URL, json={"status": "ok"}, status=200)
    messages = _messages(3)
    backend = PlaymobileBackend()

    backend.send_messages(messages)

    body = json.loads(responses.calls[0].request.body)
    entries = body["messages"]
    ids = [entry["message-id"] for entry in entries]
    assert len(set(ids)) == 3
    assert ids == [m.message_id for m in messages]


@responses.activate
def test_entries_carry_originator_recipient_and_text():
    responses.add(responses.POST, sms_settings.URL, json={"status": "ok"}, status=200)
    messages = _messages(2)
    backend = PlaymobileBackend()

    backend.send_messages(messages)

    body = json.loads(responses.calls[0].request.body)
    for message, entry in zip(messages, body["messages"]):
        assert entry["recipient"] == message.phone_number
        assert entry["sms"]["originator"] == sms_settings.ORIGINATOR
        assert entry["sms"]["content"]["text"] == message.text


@responses.activate
def test_request_carries_basic_auth_and_json_content_type():
    responses.add(responses.POST, sms_settings.URL, json={"status": "ok"}, status=200)
    backend = PlaymobileBackend()

    backend.send_messages(_messages(1))

    request = responses.calls[0].request
    assert request.headers["Content-Type"] == "application/json"

    auth_header = request.headers["Authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.removeprefix("Basic ")).decode()
    assert decoded == f"{sms_settings.LOGIN}:{sms_settings.PASSWORD}"


@responses.activate
def test_a_non_none_timeout_is_actually_passed(monkeypatch):
    responses.add(responses.POST, sms_settings.URL, json={"status": "ok"}, status=200)
    captured = {}
    original_request = requests.Session.request

    def fake_request(self, method, url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return original_request(self, method, url, **kwargs)

    monkeypatch.setattr(requests.Session, "request", fake_request)

    backend = PlaymobileBackend()
    backend.send_messages(_messages(1))

    assert captured["timeout"] is not None
    assert captured["timeout"] == sms_settings.TIMEOUT


@responses.activate
def test_200_response_yields_ok_results_with_status_and_raw_body():
    responses.add(
        responses.POST, sms_settings.URL, json={"status": "ok", "id": "abc"}, status=200
    )
    messages = _messages(2)
    backend = PlaymobileBackend()

    results = backend.send_messages(messages)

    assert len(results) == 2
    for message, result in zip(messages, results):
        assert result.message is message
        assert result.ok is True
        assert result.status_code == 200
        assert result.raw == {"status": "ok", "id": "abc"}


@responses.activate
def test_400_response_raises_sms_provider_error_and_is_not_retried():
    responses.add(
        responses.POST,
        sms_settings.URL,
        json={"error": "bad recipient"},
        status=400,
    )
    backend = PlaymobileBackend()

    with pytest.raises(SmsProviderError) as exc_info:
        backend.send_messages(_messages(1))

    assert exc_info.value.status_code == 400
    assert exc_info.value.body == {"error": "bad recipient"}
    assert len(responses.calls) == 1


@override_settings(SMS_SETTINGS=_merged(MAX_RETRIES=2))
@responses.activate
def test_500_response_is_retried_up_to_max_retries_then_raises_transport_error():
    reset_session()
    responses.add(responses.POST, sms_settings.URL, json={"error": "boom"}, status=500)
    backend = PlaymobileBackend()

    with pytest.raises(SmsTransportError):
        backend.send_messages(_messages(1))

    assert len(responses.calls) == sms_settings.MAX_RETRIES + 1


@responses.activate
def test_timeout_exception_raises_sms_transport_error():
    responses.add(
        responses.POST,
        sms_settings.URL,
        body=requests.exceptions.Timeout("connection timed out"),
    )
    backend = PlaymobileBackend()

    with pytest.raises(SmsTransportError):
        backend.send_messages(_messages(1))


@responses.activate
def test_fail_silently_returns_ok_false_results_instead_of_raising():
    responses.add(
        responses.POST, sms_settings.URL, json={"error": "bad recipient"}, status=400
    )
    messages = _messages(2)
    backend = PlaymobileBackend(fail_silently=True)

    results = backend.send_messages(messages)

    assert len(results) == 2
    for message, result in zip(messages, results):
        assert result.message is message
        assert result.ok is False
        assert result.error
        # The broker body must still reach the caller when fail_silently is
        # used, so it can be persisted into SmsLog.provider_response even
        # though the API no longer forwards it to callers.
        assert result.raw == {"error": "bad recipient"}


@override_settings(SMS_SETTINGS=_merged(MAX_RETRIES=0))
@responses.activate
def test_max_retries_zero_attempts_exactly_once_on_500():
    reset_session()
    responses.add(responses.POST, sms_settings.URL, json={"error": "boom"}, status=500)
    backend = PlaymobileBackend()

    with pytest.raises(SmsTransportError):
        backend.send_messages(_messages(1))

    assert len(responses.calls) == 1


@override_settings(SMS_SETTINGS=_merged(MAX_RETRIES=2))
@responses.activate
def test_retried_attempts_reuse_the_same_message_id_as_the_first_attempt():
    reset_session()
    responses.add(responses.POST, sms_settings.URL, json={"error": "boom"}, status=500)
    messages = _messages(1)
    backend = PlaymobileBackend()

    with pytest.raises(SmsTransportError):
        backend.send_messages(messages)

    assert len(responses.calls) == sms_settings.MAX_RETRIES + 1
    message_ids = set()
    for call in responses.calls:
        body = json.loads(call.request.body)
        message_ids.add(body["messages"][0]["message-id"])

    assert message_ids == {messages[0].message_id}


@pytest.mark.django_db
@responses.activate
def test_send_through_smsclient_persists_the_wire_message_id():
    """CRITICAL 3 regression test.

    No shipped backend ever populates ``SendResult.provider_message_id``
    (``PlaymobileBackend`` included -- see ``result.raw``, not
    ``provider_message_id``, above). Before the fix,
    ``SmsLog.mark_sent`` did ``self.message_id = result.provider_message_id
    or ""``, which unconditionally blanked out the log row's ``message_id``
    -- the very ``SmsMessage.message_id`` that was put on the wire and is
    what makes the retry policy replay-safe. This must go through a real
    backend (not a hand-built ``SendResult``), since that's exactly the gap
    that let this defect ship.
    """
    from uzsms.models import SmsLog
    from uzsms.repository import SmsLogRecorder
    from uzsms.services import SmsClient

    responses.add(responses.POST, sms_settings.URL, json={"status": "ok"}, status=200)
    client = SmsClient(backend=PlaymobileBackend(), recorder=SmsLogRecorder(enabled=True))

    result = client.send(phone_number="998901234567", text="hello")

    assert result.ok is True
    log = SmsLog.objects.get(pk=result.log_id)
    assert log.message_id == result.message.message_id
    assert log.message_id != ""


def test_get_session_returns_cached_session():
    session_a = get_session()
    session_b = get_session()
    assert session_a is session_b


def test_reset_session_via_setting_changed_signal_produces_a_fresh_session():
    session_before = get_session()

    with override_settings(SMS_SETTINGS=_merged(POOL_MAXSIZE=20)):
        session_after = get_session()
        assert session_after is not session_before

    # sanity: settings restored, sms_settings/session both usable again
    assert django_settings.SMS_SETTINGS == BASE_SMS_SETTINGS
