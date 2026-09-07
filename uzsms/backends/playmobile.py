"""Playmobile SMS broker backend.

Fixes the four production defects present in 1.0.1's ``uzsms/sms_utils.py``:

1. Every request passes ``timeout=sms_settings.TIMEOUT`` — a stalled broker
   can no longer pin a worker thread forever.
2. Requests go through a cached, pooled :class:`requests.Session` (see
   :func:`get_session`) instead of opening a new TCP/TLS connection per
   message.
3. All messages passed to :meth:`PlaymobileBackend.send_messages` are sent
   in a single HTTP request, as one ``messages`` array, instead of one
   request per message.
4. Each entry's wire ``message-id`` is the ``SmsMessage.message_id`` that
   already exists on the object (generated once, per instance) — never a
   hardcoded constant and never regenerated inside the backend.

Retrying a POST is unsafe in general: a request that times out or drops
mid-flight may already have been delivered by the broker, and blindly
retrying it double-sends the SMS and double-charges the operator. It is
safe here, and only here, because ``SmsMessage.message_id`` is generated
once before the first attempt and is reused verbatim across every retry of
that message (see :func:`build_payload`) — the broker recognizes a replay
carrying the same ``message-id`` as a duplicate of one delivery and
deduplicates it, rather than sending it again. That is why the retry
policy's ``allowed_methods`` includes ``POST`` here. A backend that cannot
offer that same guarantee — one that mints a fresh id per attempt, or talks
to a broker without idempotent message ids — must not retry POSTs and
should set ``MAX_RETRIES = 0``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import requests
from django.core.signals import setting_changed
from django.dispatch import receiver
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from uzsms.backends.base import BaseSmsBackend
from uzsms.conf import sms_settings
from uzsms.dto import SendResult, SmsMessage
from uzsms.exceptions import SmsProviderError, SmsTransportError

_session: requests.Session | None = None


def get_session() -> requests.Session:
    """Return a cached, pooled :class:`requests.Session` for the broker.

    The session's adapter carries ``pool_maxsize=sms_settings.POOL_MAXSIZE``
    connections and a :class:`~urllib3.util.Retry` policy built from
    ``sms_settings``. The same session (and its underlying connection pool)
    is reused across calls until :func:`reset_session` clears it.
    """
    global _session
    if _session is None:
        session = requests.Session()
        retry = Retry(
            total=sms_settings.MAX_RETRIES,
            backoff_factor=sms_settings.RETRY_BACKOFF,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"POST"}),
        )
        adapter = HTTPAdapter(pool_maxsize=sms_settings.POOL_MAXSIZE, max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        _session = session
    return _session


def reset_session() -> None:
    """Discard the cached session so the next :func:`get_session` call rebuilds it."""
    global _session
    if _session is not None:
        _session.close()
    _session = None


@receiver(setting_changed)
def _reset_session_on_setting_changed(*, setting: str, **kwargs: Any) -> None:
    if setting == "SMS_SETTINGS":
        reset_session()


def build_payload(messages: Sequence[SmsMessage]) -> dict:
    """Build the broker's documented envelope for ``messages``.

    Module-level per controller ruling R3: a future async backend must be
    able to produce a byte-identical envelope without duplicating this
    logic as a copied method body.
    """
    return {
        "messages": [
            {
                "recipient": message.phone_number,
                "message-id": message.message_id,
                "sms": {
                    "originator": sms_settings.ORIGINATOR,
                    "content": {"text": message.text},
                },
            }
            for message in messages
        ]
    }


def _parse_body(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


class PlaymobileBackend(BaseSmsBackend):
    """Sends messages to the Playmobile SMS broker over a pooled HTTP session."""

    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        if not messages:
            return []

        payload = build_payload(messages)

        try:
            response = get_session().post(
                sms_settings.URL,
                json=payload,
                timeout=sms_settings.TIMEOUT,
                auth=(sms_settings.LOGIN, sms_settings.PASSWORD),
            )
        except requests.exceptions.RequestException as exc:
            return self._fail(messages, SmsTransportError(str(exc)), exc)

        if response.status_code >= 400:
            error = SmsProviderError(
                f"Playmobile broker responded with status {response.status_code}.",
                status_code=response.status_code,
                body=_parse_body(response),
            )
            return self._fail(messages, error, None)

        body = _parse_body(response)
        return [
            SendResult(message=message, ok=True, status_code=response.status_code, raw=body)
            for message in messages
        ]

    def _fail(
        self,
        messages: Sequence[SmsMessage],
        error: SmsTransportError | SmsProviderError,
        cause: BaseException | None,
    ) -> list[SendResult]:
        status_code = getattr(error, "status_code", None)
        if self.fail_silently:
            return [
                SendResult(message=m, ok=False, status_code=status_code, error=str(error))
                for m in messages
            ]
        if cause is not None:
            raise error from cause
        raise error
