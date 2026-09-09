"""Tests for the async Playmobile SMS broker backend.

Mirrors ``tests/test_backend_playmobile.py`` assertion-for-assertion, but
drives :class:`~uzsms.backends.playmobile.AsyncPlaymobileBackend` over a
mocked ``httpx.AsyncClient`` (via ``respx``) instead of ``requests``.

Also proves the optional-dependency contract: ``httpx`` is an extra, so
importing ``uzsms.backends.playmobile`` — and sending through the *sync*
``PlaymobileBackend`` — must work even when ``httpx`` cannot be imported.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import responses
import respx
from django.test import override_settings

from uzsms.backends.playmobile import (
    AsyncPlaymobileBackend,
    PlaymobileBackend,
    get_async_client,
    reset_async_client,
    reset_session,
)
from uzsms.conf import sms_settings
from uzsms.dto import SmsMessage
from uzsms.exceptions import SmsConfigurationError, SmsProviderError, SmsTransportError

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
def _playmobile_async_settings():
    with override_settings(SMS_SETTINGS=BASE_SMS_SETTINGS):
        reset_session()
        reset_async_client()
        yield
    reset_session()
    reset_async_client()


def _messages(count: int) -> list[SmsMessage]:
    return [
        SmsMessage(phone_number=f"99890123{i:04d}", text=f"hello {i}")
        for i in range(count)
    ]


@pytest.mark.asyncio
@respx.mock
async def test_sending_three_messages_issues_exactly_one_request():
    route = respx.post(sms_settings.URL).mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    messages = _messages(3)
    backend = AsyncPlaymobileBackend()

    results = await backend.send_messages(messages)

    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert len(body["messages"]) == 3
    assert len(results) == 3


@pytest.mark.asyncio
@respx.mock
async def test_each_entry_has_distinct_message_id_matching_the_sms_message():
    route = respx.post(sms_settings.URL).mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    messages = _messages(3)
    backend = AsyncPlaymobileBackend()

    await backend.send_messages(messages)

    body = json.loads(route.calls[0].request.content)
    entries = body["messages"]
    ids = [entry["message-id"] for entry in entries]
    assert len(set(ids)) == 3
    assert ids == [m.message_id for m in messages]


@pytest.mark.asyncio
@respx.mock
async def test_entries_carry_originator_recipient_and_text():
    route = respx.post(sms_settings.URL).mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    messages = _messages(2)
    backend = AsyncPlaymobileBackend()

    await backend.send_messages(messages)

    body = json.loads(route.calls[0].request.content)
    for message, entry in zip(messages, body["messages"]):
        assert entry["recipient"] == message.phone_number
        assert entry["sms"]["originator"] == sms_settings.ORIGINATOR
        assert entry["sms"]["content"]["text"] == message.text


@pytest.mark.asyncio
@respx.mock
async def test_request_carries_basic_auth_and_json_content_type():
    route = respx.post(sms_settings.URL).mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    backend = AsyncPlaymobileBackend()

    await backend.send_messages(_messages(1))

    request = route.calls[0].request
    assert request.headers["Content-Type"] == "application/json"

    auth_header = request.headers["Authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.removeprefix("Basic ")).decode()
    assert decoded == f"{sms_settings.LOGIN}:{sms_settings.PASSWORD}"


@pytest.mark.asyncio
@respx.mock
async def test_a_non_none_timeout_is_actually_passed(monkeypatch):
    respx.post(sms_settings.URL).mock(return_value=httpx.Response(200, json={"status": "ok"}))
    captured = {}
    original_request = httpx.AsyncClient.request

    async def fake_request(self, method, url, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return await original_request(self, method, url, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)

    backend = AsyncPlaymobileBackend()
    await backend.send_messages(_messages(1))

    assert captured["timeout"] is not None
    assert captured["timeout"] != httpx.USE_CLIENT_DEFAULT


@pytest.mark.asyncio
@respx.mock
async def test_200_response_yields_ok_results_with_status_and_raw_body():
    respx.post(sms_settings.URL).mock(
        return_value=httpx.Response(200, json={"status": "ok", "id": "abc"})
    )
    messages = _messages(2)
    backend = AsyncPlaymobileBackend()

    results = await backend.send_messages(messages)

    assert len(results) == 2
    for message, result in zip(messages, results):
        assert result.message is message
        assert result.ok is True
        assert result.status_code == 200
        assert result.raw == {"status": "ok", "id": "abc"}


@pytest.mark.asyncio
@respx.mock
async def test_400_response_raises_sms_provider_error_and_is_not_retried():
    route = respx.post(sms_settings.URL).mock(
        return_value=httpx.Response(400, json={"error": "bad recipient"})
    )
    backend = AsyncPlaymobileBackend()

    with pytest.raises(SmsProviderError) as exc_info:
        await backend.send_messages(_messages(1))

    assert exc_info.value.status_code == 400
    assert exc_info.value.body == {"error": "bad recipient"}
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_500_response_is_retried_up_to_max_retries_then_raises_transport_error():
    with override_settings(SMS_SETTINGS=_merged(MAX_RETRIES=2)):
        reset_async_client()
        route = respx.post(sms_settings.URL).mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        backend = AsyncPlaymobileBackend()

        with pytest.raises(SmsTransportError):
            await backend.send_messages(_messages(1))

        assert route.call_count == sms_settings.MAX_RETRIES + 1


@pytest.mark.asyncio
@respx.mock
async def test_timeout_exception_raises_sms_transport_error():
    respx.post(sms_settings.URL).mock(side_effect=httpx.TimeoutException("connection timed out"))
    backend = AsyncPlaymobileBackend()

    with pytest.raises(SmsTransportError):
        await backend.send_messages(_messages(1))


@pytest.mark.asyncio
@respx.mock
async def test_fail_silently_returns_ok_false_results_instead_of_raising():
    respx.post(sms_settings.URL).mock(
        return_value=httpx.Response(400, json={"error": "bad recipient"})
    )
    messages = _messages(2)
    backend = AsyncPlaymobileBackend(fail_silently=True)

    results = await backend.send_messages(messages)

    assert len(results) == 2
    for message, result in zip(messages, results):
        assert result.message is message
        assert result.ok is False
        assert result.error
        # The broker body must still reach the caller when fail_silently is
        # used, so it can be persisted into SmsLog.provider_response even
        # though the API no longer forwards it to callers.
        assert result.raw == {"error": "bad recipient"}


@pytest.mark.asyncio
@respx.mock
async def test_max_retries_zero_attempts_exactly_once_on_500():
    with override_settings(SMS_SETTINGS=_merged(MAX_RETRIES=0)):
        reset_async_client()
        route = respx.post(sms_settings.URL).mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        backend = AsyncPlaymobileBackend()

        with pytest.raises(SmsTransportError):
            await backend.send_messages(_messages(1))

        assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_retried_attempts_reuse_the_same_message_id_as_the_first_attempt():
    with override_settings(SMS_SETTINGS=_merged(MAX_RETRIES=2)):
        reset_async_client()
        route = respx.post(sms_settings.URL).mock(
            return_value=httpx.Response(500, json={"error": "boom"})
        )
        messages = _messages(1)
        backend = AsyncPlaymobileBackend()

        with pytest.raises(SmsTransportError):
            await backend.send_messages(messages)

        assert route.call_count == sms_settings.MAX_RETRIES + 1
        message_ids = set()
        for call in route.calls:
            body = json.loads(call.request.content)
            message_ids.add(body["messages"][0]["message-id"])

        assert message_ids == {messages[0].message_id}


@pytest.mark.asyncio
async def test_get_async_client_returns_cached_client():
    client_a = await get_async_client()
    client_b = await get_async_client()
    assert client_a is client_b


@pytest.mark.asyncio
async def test_reset_async_client_via_setting_changed_signal_produces_a_fresh_client():
    client_before = await get_async_client()

    with override_settings(SMS_SETTINGS=_merged(POOL_MAXSIZE=20)):
        client_after = await get_async_client()
        assert client_after is not client_before


def test_module_imports_without_httpx_installed():
    """A fresh interpreter with httpx blocked must still import the module."""
    script = (
        "import sys\n"
        "sys.modules['httpx'] = None\n"
        "import uzsms.backends.playmobile as pm\n"
        "assert pm.PlaymobileBackend is not None\n"
        "assert pm.AsyncPlaymobileBackend is not None\n"
        "print('OK')\n"
    )
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


@responses.activate
def test_sync_backend_still_sends_when_httpx_is_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)
    responses.add(responses.POST, sms_settings.URL, json={"status": "ok"}, status=200)

    backend = PlaymobileBackend()
    results = backend.send_messages(_messages(1))

    assert len(results) == 1
    assert results[0].ok is True


@pytest.mark.asyncio
async def test_async_backend_raises_configuration_error_when_httpx_is_absent(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)

    backend = AsyncPlaymobileBackend()

    with pytest.raises(SmsConfigurationError, match=r"django-sms-uz\[async\]"):
        await backend.send_messages(_messages(1))
