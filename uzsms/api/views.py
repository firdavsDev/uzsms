"""Views for the uzsms API.

Fixes the two worst defects in 1.0.1's ``uzsms/views.py``:

1. **Open relay.** The old view hardcoded ``permission_classes =
   [AllowAny]``, so anyone on the internet could send SMS through the
   operator's broker credentials. ``SendSmsAPIView.get_permissions``
   resolves ``sms_settings.PERMISSION_CLASSES`` (default
   ``IsAuthenticated``) lazily via ``import_string``, so authentication is
   required by default and a host project can only relax it deliberately,
   through its own settings.
2. **Unserializable response.** The old view did ``return
   Response(result)`` where ``result`` was a raw ``requests.Response``
   object, which DRF cannot render — every request to it crashed. Every
   body this view returns is built by :mod:`uzsms.api.responses`, whose
   envelope contains only JSON-primitive values.

``handle_exception`` re-shapes DRF's own error responses (authentication,
permission, throttling, parsing, and validation failures) into the same
envelope, by delegating to DRF's default exception handling for status
codes and headers and then rewriting only the body.
"""

from __future__ import annotations

from typing import Any

from django.utils.module_loading import import_string
from rest_framework.exceptions import APIException
from rest_framework.exceptions import AuthenticationFailed as DRFAuthenticationFailed
from rest_framework.exceptions import NotAuthenticated as DRFNotAuthenticated
from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
from rest_framework.exceptions import Throttled as DRFThrottled
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from uzsms.api.responses import error_response, success_response
from uzsms.api.serializers import SendSmsSerializer
from uzsms.conf import sms_settings
from uzsms.exceptions import SmsProviderError, SmsTransportError, SmsValidationError
from uzsms.models import SmsLog
from uzsms.services import SmsClient

# Maps a DRF exception type to the error code this API reports for it.
# Checked in order, most specific first; anything else that reaches
# ``handle_exception`` falls back to the exception's own ``default_code``.
_DRF_ERROR_CODES: tuple[tuple[type[APIException], str], ...] = (
    (DRFThrottled, "throttled"),
    (DRFNotAuthenticated, "authentication_failed"),
    (DRFAuthenticationFailed, "authentication_failed"),
    (DRFPermissionDenied, "permission_denied"),
    (DRFValidationError, "validation_error"),
)


def _error_code_for(exc: Exception) -> str:
    for exc_type, code in _DRF_ERROR_CODES:
        if isinstance(exc, exc_type):
            return code
    if isinstance(exc, APIException):
        return str(exc.default_code)
    return "error"


class _SendSmsThrottle(ScopedRateThrottle):
    """A :class:`ScopedRateThrottle` whose rate comes from ``SMS_SETTINGS``.

    DRF's own ``ScopedRateThrottle`` reads its rate from
    ``DEFAULT_THROTTLE_RATES`` in the ``REST_FRAMEWORK`` setting, keyed by
    scope. This overrides that lookup so ``sms_settings.THROTTLE_RATE``
    stays the single place the send endpoint's throttle is configured,
    consistent with every other tunable this package exposes.
    """

    def get_rate(self) -> str:
        return sms_settings.THROTTLE_RATE


class SendSmsAPIView(APIView):
    """Sends a single SMS message.

    Requires authentication by default; see the module docstring and
    ``sms_settings.PERMISSION_CLASSES``.
    """

    serializer_class = SendSmsSerializer
    throttle_classes = (_SendSmsThrottle,)
    throttle_scope = "uzsms.send"

    def get_permissions(self) -> list[BasePermission]:
        """Resolve permission classes lazily, so tests can override the setting."""
        permission_paths = sms_settings.PERMISSION_CLASSES
        return [import_string(path)() for path in permission_paths]

    def post(self, request: Any, *args: Any, **kwargs: Any) -> Response:
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        phone_number = serializer.validated_data["phone_number"]
        text = serializer.validated_data["message"]

        client = SmsClient()
        try:
            result = client.send(phone_number, text)
        except SmsValidationError as exc:
            return error_response("validation_error", str(exc), status_code=400)
        except SmsProviderError as exc:
            return error_response(
                "provider_error",
                str(exc),
                detail={"status_code": exc.status_code, "body": exc.body},
                status_code=502,
            )
        except SmsTransportError as exc:
            return error_response("transport_error", str(exc), status_code=502)

        # ``result.log_id`` is carried straight out of ``SmsClient.send()``
        # (see ``uzsms.services._with_log_ids``) — it is the pk of exactly
        # the log row this result belongs to, no re-query needed. It is
        # ``None`` when ``sms_settings.LOG_MESSAGES`` is disabled, since no
        # log row was ever created.
        data = {
            "message_id": result.message.message_id,
            "log_id": result.log_id,
            "phone_number": result.message.phone_number,
            "status": (SmsLog.Status.SENT if result.ok else SmsLog.Status.FAILED).value,
        }
        return success_response(data, status_code=201)

    def handle_exception(self, exc: Exception) -> Response:
        """Re-shape DRF's own error responses into this API's envelope.

        Delegates to DRF's default handling for the status code and any
        headers it attaches (``WWW-Authenticate``, ``Retry-After``), and
        rewrites only the body — so auth, permission, throttling, and
        parsing failures never leak DRF's bare ``{"detail": ...}`` shape.
        """
        response = super().handle_exception(exc)
        code = _error_code_for(exc)

        if isinstance(response.data, dict) and set(response.data) == {"detail"}:
            message = str(response.data["detail"])
            detail = None
        else:
            message = "Invalid request." if code == "validation_error" else str(exc)
            detail = response.data

        enveloped = error_response(code, message, detail, status_code=response.status_code)
        for header, value in response.items():
            enveloped[header] = value
        return enveloped


send_sms_api_view = SendSmsAPIView.as_view()
