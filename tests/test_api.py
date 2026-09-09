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
from uzsms.exceptions import SmsProviderError

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
    naming the ``django-sms-uz[drf]`` extra, not an opaque ImportError.

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
        "    assert 'django-sms-uz[drf]' in str(exc), str(exc)\n"
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
