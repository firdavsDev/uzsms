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

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import requests
from django.core.signals import setting_changed
from django.dispatch import receiver
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from uzsms.backends.base import BaseAsyncSmsBackend, BaseSmsBackend
from uzsms.conf import sms_settings
from uzsms.dto import SendResult, SmsMessage
from uzsms.exceptions import SmsConfigurationError, SmsProviderError, SmsTransportError

if TYPE_CHECKING:
    import httpx

_session: requests.Session | None = None
_async_client: httpx.AsyncClient | None = None

# Status codes the broker (and, for the sync backend, urllib3's Retry) treats
# as transient — safe to retry given SmsMessage.message_id's replay-dedup
# guarantee (see the module docstring).
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


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


def _import_httpx():
    """Import and return the ``httpx`` module, or raise a clear config error.

    ``httpx`` is an optional extra (``django-smsuz[async]``); this is the
    only place :class:`AsyncPlaymobileBackend` reaches for it, and it does
    so lazily, on first use, so importing this module never requires
    ``httpx`` to be installed.
    """
    try:
        import httpx
    except ImportError as exc:
        raise SmsConfigurationError(
            "httpx is required to use AsyncPlaymobileBackend. Install it "
            "with `pip install django-smsuz[async]`."
        ) from exc
    return httpx


async def get_async_client() -> httpx.AsyncClient:
    """Return a cached ``httpx.AsyncClient`` for the broker.

    Mirrors :func:`get_session`: the client's connection pool limits come
    from ``sms_settings.POOL_MAXSIZE`` and the same instance is reused
    across calls until :func:`reset_async_client` clears it.
    """
    global _async_client
    if _async_client is None:
        httpx = _import_httpx()
        limits = httpx.Limits(
            max_connections=sms_settings.POOL_MAXSIZE,
            max_keepalive_connections=sms_settings.POOL_MAXSIZE,
        )
        _async_client = httpx.AsyncClient(limits=limits)
    return _async_client


def reset_async_client() -> None:
    """Discard the cached async client so the next call rebuilds it.

    Does not ``aclose`` the discarded client: closing an ``httpx.AsyncClient``
    is an async operation and this function is invoked from a synchronous
    Django signal handler. The discarded client is left for garbage
    collection, matching this package's YAGNI stance on connection teardown
    for a backend most hosts open once per process.
    """
    global _async_client
    _async_client = None


async def _async_sleep_backoff(attempt: int) -> None:
    """Sleep between retries, mirroring urllib3's exponential backoff.

    ``attempt`` is the 1-based number of the retry about to be made.
    Tests keep this fast the same way the sync suite keeps urllib3's
    ``backoff_factor`` fast: by setting ``RETRY_BACKOFF = 0``, which makes
    every backoff a zero-second (near-instant) ``asyncio.sleep``.
    """
    backoff = sms_settings.RETRY_BACKOFF * (2 ** (attempt - 1))
    if backoff:
        await asyncio.sleep(backoff)


def _build_httpx_timeout(httpx) -> httpx.Timeout:
    """Translate ``sms_settings.TIMEOUT`` into an ``httpx.Timeout``.

    ``sms_settings.TIMEOUT`` follows requests' ``(connect, read)`` tuple
    convention (see ``DEFAULTS`` in ``uzsms.conf``); ``httpx.Timeout`` has
    no such tuple form, so a 2-tuple is mapped onto ``connect``/``read``
    (and ``write``/``pool`` reuse ``connect``/``read`` respectively).
    """
    timeout = sms_settings.TIMEOUT
    if isinstance(timeout, tuple):
        connect, read = timeout
        return httpx.Timeout(connect=connect, read=read, write=read, pool=connect)
    return httpx.Timeout(timeout)


def _build_failure(
    messages: Sequence[SmsMessage],
    error: SmsTransportError | SmsProviderError,
    cause: BaseException | None,
    *,
    fail_silently: bool,
) -> list[SendResult]:
    status_code = getattr(error, "status_code", None)
    if fail_silently:
        raw = getattr(error, "body", None)
        return [
            SendResult(
                message=m, ok=False, status_code=status_code, error=str(error), raw=raw
            )
            for m in messages
        ]
    if cause is not None:
        raise error from cause
    raise error


@receiver(setting_changed)
def _reset_session_on_setting_changed(*, setting: str, **kwargs: Any) -> None:
    if setting == "SMS_SETTINGS":
        reset_session()
        reset_async_client()


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


def _parse_body(response: Any) -> Any:
    """Parse a response body as JSON, falling back to raw text.

    Works for both ``requests.Response`` and ``httpx.Response``: both
    expose a ``.json()`` method (raising a ``ValueError`` subclass on
    failure) and a ``.text`` property, so the sync and async backends can
    share this one implementation.
    """
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
        return _build_failure(messages, error, cause, fail_silently=self.fail_silently)


class AsyncPlaymobileBackend(BaseAsyncSmsBackend):
    """Sends messages to the Playmobile SMS broker over a cached ``httpx.AsyncClient``.

    Mirrors :class:`PlaymobileBackend`: same timeout, same pool limits,
    same error mapping, and the same replay-safe retry story (see the
    module docstring) — but ``httpx`` has no built-in retry policy like
    urllib3's ``Retry``, so the bounded retry loop over
    ``_RETRYABLE_STATUS_CODES`` is implemented explicitly here, reusing
    ``sms_settings.MAX_RETRIES``/``RETRY_BACKOFF``.
    """

    async def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        if not messages:
            return []

        httpx = _import_httpx()
        payload = build_payload(messages)
        client = await get_async_client()
        auth = httpx.BasicAuth(sms_settings.LOGIN, sms_settings.PASSWORD)
        timeout = _build_httpx_timeout(httpx)

        attempt = 0
        while True:
            try:
                response = await client.post(
                    sms_settings.URL,
                    json=payload,
                    timeout=timeout,
                    auth=auth,
                )
            except httpx.RequestError as exc:
                if attempt >= sms_settings.MAX_RETRIES:
                    return self._fail(messages, SmsTransportError(str(exc)), exc)
                attempt += 1
                await _async_sleep_backoff(attempt)
                continue

            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < sms_settings.MAX_RETRIES:
                    attempt += 1
                    await _async_sleep_backoff(attempt)
                    continue
                error = SmsTransportError(
                    f"Playmobile broker responded with status {response.status_code} "
                    f"after {attempt} retries."
                )
                return self._fail(messages, error, None)

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
        return _build_failure(messages, error, cause, fail_silently=self.fail_silently)
