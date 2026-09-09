"""Tests for the uzsms HTTP API (``uzsms/api/``).

Written from the specification in the Task 12 brief, BEFORE reading
``uzsms/api/views.py``, ``uzsms/api/serializers.py``, or
``uzsms/api/responses.py`` — these tests must be able to fail against a
wrong implementation, not merely describe whatever the code happens to do.

Pins two 1.0.1 defects:

1. SECURITY — the send endpoint used to be ``AllowAny``, an open SMS relay.
   It must require authentication by default.
2. CRASH — the old view returned a raw ``requests.Response`` object, which
   DRF cannot serialize. Every response body here must be JSON-renderable.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from uzsms.backends.base import BaseSmsBackend
from uzsms.dto import SendResult, SmsMessage
from uzsms.exceptions import SmsProviderError, SmsTransportError

BASE_SMS_SETTINGS = {
    "URL": "https://broker.example.com/broker-api/send",
    "LOGIN": "test-login",
    "PASSWORD": "test-password",
    "BACKEND": "uzsms.backends.locmem.LocMemBackend",
}

VALID_PHONE = "998901234567"
VALID_MESSAGE = "hello there"


class _StubOkBackend(BaseSmsBackend):
    """Always succeeds, with a deterministic, non-empty provider message id."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        return [
            SendResult(
                message=m,
                ok=True,
                provider_message_id="PMID-123",
                status_code=200,
                raw={"status": "ok"},
            )
            for m in messages
        ]


class _ProviderFailureBackend(BaseSmsBackend):
    """Always raises ``SmsProviderError``, to exercise the API's 502 path."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        raise SmsProviderError(
            "broker rejected the message", status_code=400, body={"error": "bad recipient"}
        )


class _FailSilentlyBackend(BaseSmsBackend):
    """Mimics a backend under ``FAIL_SILENTLY=True``: RETURNS an ``ok=False``
    ``SendResult`` instead of raising, exactly as ``PlaymobileBackend`` does
    when ``fail_silently`` is set."""

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


# A deliberately distinctive string that would never otherwise appear in a
# response body, used to prove the broker's raw body never reaches the API
# caller (see test_provider_failure_detail_never_contains_the_broker_body).
_BROKER_BODY_SENTINEL = "SENTINEL-BROKER-INTERNAL-TEXT-8f3ac2d1"


class _LeakyProviderFailureBackend(BaseSmsBackend):
    """Raises ``SmsProviderError`` carrying a broker body with a sentinel
    string in it, standing in for broker-internal error text/account/routing
    details that must never reach the API caller."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        raise SmsProviderError(
            "broker rejected the message",
            status_code=400,
            body={
                "error": _BROKER_BODY_SENTINEL,
                "account_id": "acct-internal-999",
            },
        )


# A sentinel standing in for the broker's hostname/port/path, exactly the
# shape a real transport exception's ``str()`` embeds (e.g.
# ``requests.exceptions.ConnectionError``'s
# ``HTTPSConnectionPool(host=..., port=...): Max retries exceeded with
# url: ...``).
_TRANSPORT_SENTINEL = "internal-broker.corp.local"


class _LeakyTransportFailureBackend(BaseSmsBackend):
    """Raises ``SmsTransportError`` whose message embeds the broker's
    hostname/port/path, standing in for what ``requests``/``httpx`` put into
    a real connection error's ``str()`` — this must never reach the API
    caller."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        raise SmsTransportError(
            f"HTTPSConnectionPool(host='{_TRANSPORT_SENTINEL}', port=8443): "
            "Max retries exceeded with url: /broker-api/send"
        )


def _settings(**overrides):
    return {**BASE_SMS_SETTINGS, **overrides}


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


def _make_user(username="tester"):
    User = get_user_model()
    return User.objects.create_user(username=username, password="pw")


# ---------------------------------------------------------------------------
# Open-relay fix (defect 1)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_anonymous_post_is_rejected_not_created():
    """Pins the open-relay fix: anonymous POST must never succeed."""
    with override_settings(SMS_SETTINGS=_settings()):
        client = APIClient()
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code in (401, 403)
    assert response.status_code != 201


@pytest.mark.django_db
def test_anonymous_response_uses_envelope_not_bare_drf_detail():
    with override_settings(SMS_SETTINGS=_settings()):
        client = APIClient()
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    body = response.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is False
    assert body["data"] is None
    assert isinstance(body["error"], dict)
    assert "code" in body["error"]
    assert "message" in body["error"]


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_authenticated_post_valid_payload_returns_201_with_full_envelope():
    from uzsms.models import SmsLog

    user = _make_user()
    with override_settings(SMS_SETTINGS=_settings(BACKEND="tests.test_api._StubOkBackend")):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is True
    assert body["error"] is None

    data = body["data"]
    # ``message_id`` is the client-generated correlation id (SmsMessage.message_id),
    # not the provider's own id — it exists before the send even happens, and is
    # what the persisted SmsLog row is keyed by. Cross-check against the DB row
    # rather than hardcoding a value, since the id is generated per-request.
    assert isinstance(data["message_id"], str) and data["message_id"]
    assert data["phone_number"] == VALID_PHONE
    assert data["status"] == "sent"
    assert data["log_id"]

    # The persisted log's ``message_id`` ends up holding the *provider's*
    # returned id (``SmsLog.mark_sent`` overwrites it), which is why
    # ``data["message_id"]`` — the client-generated correlation id — is
    # checked above instead of against the DB row.
    log = SmsLog.objects.get(id=data["log_id"])
    assert log.message_id == "PMID-123"
    assert log.phone_number == VALID_PHONE
    assert log.status == SmsLog.Status.SENT


@pytest.mark.django_db
def test_log_messages_disabled_returns_valid_envelope_with_log_id_null():
    """With LOG_MESSAGES=False, no SmsLog row is ever created, so log_id
    must be None rather than the endpoint erroring or leaking a stale id."""
    from uzsms.models import SmsLog

    user = _make_user()
    with override_settings(
        SMS_SETTINGS=_settings(
            BACKEND="tests.test_api._StubOkBackend", LOG_MESSAGES=False
        )
    ):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is True
    assert body["data"]["log_id"] is None
    assert SmsLog.objects.count() == 0

    rendered = JSONRenderer().render(response.data)
    assert rendered


# ---------------------------------------------------------------------------
# Crash fix (defect 2): every response body must be JSON-renderable
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "authenticate,backend,payload",
    [
        (False, "uzsms.backends.locmem.LocMemBackend", {"phone_number": VALID_PHONE, "message": VALID_MESSAGE}),
        (True, "tests.test_api._StubOkBackend", {"phone_number": VALID_PHONE, "message": VALID_MESSAGE}),
        (True, "uzsms.backends.locmem.LocMemBackend", {"phone_number": "not-a-phone", "message": VALID_MESSAGE}),
        (True, "tests.test_api._ProviderFailureBackend", {"phone_number": VALID_PHONE, "message": VALID_MESSAGE}),
    ],
)
def test_every_response_body_is_json_renderable(authenticate, backend, payload):
    with override_settings(SMS_SETTINGS=_settings(BACKEND=backend)):
        client = APIClient()
        if authenticate:
            client.force_authenticate(user=_make_user())
        response = client.post(reverse("uzsms:send_sms"), payload, format="json")

    # This is exactly what crashed in 1.0.1: DRF could not serialize the
    # response body (a raw ``requests.Response``). Rendering it explicitly
    # here proves the body is a plain JSON-serializable structure.
    rendered = JSONRenderer().render(response.data)
    assert rendered


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_invalid_phone_number_returns_400_validation_error():
    user = _make_user()
    with override_settings(SMS_SETTINGS=_settings()):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": "12345", "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code == 400
    body = response.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "validation_error"


@pytest.mark.django_db
def test_over_length_message_returns_400():
    user = _make_user()
    with override_settings(SMS_SETTINGS=_settings()):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": "x" * 2000},
            format="json",
        )

    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "validation_error"


def test_serializer_never_assigns_self_errors_directly():
    """Pins the 1.0.1 fix: the old ``ValidatePhoneNumber`` serializer
    assigned directly to the DRF-internal ``self._errors`` instead of going
    through the normal validation flow. Source inspection catches a literal
    reintroduction of that pattern; the functional check confirms
    ``is_valid()``/``.errors`` behave through DRF's real validation path
    (a pre-set ``self._errors`` would leave these stale or empty).
    """
    import inspect

    from uzsms.api.serializers import SendSmsSerializer

    source = inspect.getsource(SendSmsSerializer)
    assert "_errors" not in source

    serializer = SendSmsSerializer(data={"phone_number": "not-a-phone", "message": ""})
    assert serializer.is_valid() is False
    assert "phone_number" in serializer.errors
    assert "message" in serializer.errors


# ---------------------------------------------------------------------------
# Provider failure
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_provider_failure_returns_502_provider_error():
    user = _make_user()
    with override_settings(
        SMS_SETTINGS=_settings(BACKEND="tests.test_api._ProviderFailureBackend")
    ):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code == 502
    body = response.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "provider_error"


@pytest.mark.django_db
def test_fail_silently_backend_failure_returns_502_not_201():
    """CRITICAL 2 regression test.

    With ``FAIL_SILENTLY=True``, a backend returns ``SendResult(ok=False)``
    instead of raising. Before the fix, ``post()`` never inspected
    ``result.ok`` and fell through to ``success_response(..., 201)`` --
    reporting a failed send as a success. This asserts the API returns a
    502 with ``success: false`` instead.
    """
    user = _make_user()
    with override_settings(
        SMS_SETTINGS=_settings(
            BACKEND="tests.test_api._FailSilentlyBackend", FAIL_SILENTLY=True
        )
    ):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code == 502
    body = response.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "provider_error"
    assert "log_id" in body["error"]["detail"]


@pytest.mark.django_db
def test_provider_failure_detail_never_contains_the_broker_body():
    """Regression guard: the broker's raw response body (including anything
    it carries, such as account/routing details) must never reach the API
    caller — not in error.detail, and not anywhere else in the envelope.
    Asserts against the full rendered JSON text, not just the ``detail``
    key, so a future refactor that moves the body elsewhere still trips it.
    """
    user = _make_user()
    with override_settings(
        SMS_SETTINGS=_settings(BACKEND="tests.test_api._LeakyProviderFailureBackend")
    ):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code == 502
    raw_text = response.content.decode()
    assert _BROKER_BODY_SENTINEL not in raw_text
    assert "acct-internal-999" not in raw_text

    body = response.json()
    assert body["error"]["code"] == "provider_error"
    # status_code is fine to forward; the body is not.
    assert body["error"]["detail"] == {"status_code": 400}


@pytest.mark.django_db
def test_transport_failure_never_contains_the_brokers_host_port_or_path():
    """IMPORTANT 5 regression guard: the transport-error branch used to
    return ``str(exc)`` verbatim, which for a real ``requests``/``httpx``
    connection error embeds the broker's hostname, port, and URL path —
    operator infrastructure details this endpoint's caller is not entitled
    to. Mirrors ``test_provider_failure_detail_never_contains_the_broker_body``
    for the sibling ``SmsTransportError`` branch, which was missed when that
    fix was made.
    """
    user = _make_user()
    with override_settings(
        SMS_SETTINGS=_settings(BACKEND="tests.test_api._LeakyTransportFailureBackend")
    ):
        client = APIClient()
        client.force_authenticate(user=user)
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code == 502
    raw_text = response.content.decode()
    assert _TRANSPORT_SENTINEL not in raw_text
    assert "8443" not in raw_text
    assert "/broker-api/send" not in raw_text

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "transport_error"
    assert body["error"]["message"] == "The SMS provider could not be reached."


# ---------------------------------------------------------------------------
# PERMISSION_CLASSES setting is honoured
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_permission_classes_setting_override_allows_anonymous():
    with override_settings(
        SMS_SETTINGS=_settings(
            BACKEND="tests.test_api._StubOkBackend",
            PERMISSION_CLASSES=["rest_framework.permissions.AllowAny"],
        )
    ):
        client = APIClient()
        response = client.post(
            reverse("uzsms:send_sms"),
            {"phone_number": VALID_PHONE, "message": VALID_MESSAGE},
            format="json",
        )

    assert response.status_code == 201
    assert response.json()["success"] is True


# ---------------------------------------------------------------------------
# Throttling
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_throttle_rate_exceeded_returns_429_enveloped():
    user = _make_user()
    with override_settings(
        SMS_SETTINGS=_settings(
            BACKEND="tests.test_api._StubOkBackend",
            THROTTLE_RATE="1/min",
        )
    ):
        client = APIClient()
        client.force_authenticate(user=user)
        payload = {"phone_number": VALID_PHONE, "message": VALID_MESSAGE}

        first = client.post(reverse("uzsms:send_sms"), payload, format="json")
        second = client.post(reverse("uzsms:send_sms"), payload, format="json")

    assert first.status_code == 201
    assert second.status_code == 429

    body = second.json()
    assert set(body.keys()) == {"success", "data", "error"}
    assert body["success"] is False
    assert body["data"] is None
    assert isinstance(body["error"], dict)
    assert "code" in body["error"]

    rendered = JSONRenderer().render(second.data)
    assert rendered


# ---------------------------------------------------------------------------
# Route wiring
# ---------------------------------------------------------------------------


def test_send_sms_route_resolves_and_path_ends_in_send_slash():
    url = reverse("uzsms:send_sms")
    assert url.endswith("send/")


# ---------------------------------------------------------------------------
# Optional-dependency contract for the DRF extra
# ---------------------------------------------------------------------------


def test_importing_uzsms_urls_without_drf_raises_configuration_error():
    """A fresh interpreter with DRF blocked must raise SmsConfigurationError
    naming the ``uzsms[drf]`` extra, not an opaque ImportError.

    Mirrors the pattern in ``tests/test_backend_playmobile_async.py``'s
    ``test_module_imports_without_httpx_installed``.
    """
    script = (
        "import sys\n"
        "sys.modules['rest_framework'] = None\n"
        "from uzsms.exceptions import SmsConfigurationError\n"
        "try:\n"
        "    import uzsms.urls\n"
        "except SmsConfigurationError as exc:\n"
        "    assert 'uzsms[drf]' in str(exc), str(exc)\n"
        "    print('OK')\n"
        "else:\n"
        "    raise AssertionError('expected SmsConfigurationError, none was raised')\n"
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
