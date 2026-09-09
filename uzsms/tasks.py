"""Optional Celery task for uzsms.

Celery is an optional extra (``uzsms[celery]``): importing this
module must never raise just because Celery isn't installed. Only
*calling* :func:`send_sms_task` without Celery installed raises a clear,
actionable :class:`~uzsms.exceptions.SmsConfigurationError`.

This module contains no payload, HTTP, or ORM logic of its own -- it is a
thin wrapper around :class:`~uzsms.services.SmsClient`, converting its
``SendResult`` into a JSON-serializable ``dict`` since ``SendResult`` (a
frozen dataclass wrapping another dataclass) is not JSON-serializable on
its own.
"""

from __future__ import annotations

from typing import Any

from uzsms.dto import SendResult
from uzsms.exceptions import SmsConfigurationError, SmsTransportError
from uzsms.services import SmsClient

try:
    from celery import shared_task
except ImportError:

    def shared_task(*_task_args: Any, **_task_kwargs: Any):
        """Fallback stand-in for ``celery.shared_task`` when Celery is absent.

        Returns a decorator that replaces the wrapped function with one
        that raises :class:`SmsConfigurationError` when called, naming the
        ``uzsms[celery]`` extra. This keeps *importing*
        ``uzsms.tasks`` safe without Celery installed; only invoking the
        task fails, with a clear, actionable message.
        """

        def decorator(func):
            def _celery_not_installed(*args: Any, **kwargs: Any) -> Any:
                raise SmsConfigurationError(
                    "Celery is required to use uzsms.tasks.send_sms_task. "
                    "Install it with `pip install uzsms[celery]`."
                )

            _celery_not_installed.__name__ = getattr(func, "__name__", "shared_task")
            _celery_not_installed.__doc__ = func.__doc__
            _celery_not_installed.autoretry_for = _task_kwargs.get("autoretry_for", ())
            return _celery_not_installed

        return decorator


def _result_to_dict(result: SendResult) -> dict[str, Any]:
    """Convert a ``SendResult`` into a plain, JSON-serializable summary."""
    return {
        "ok": result.ok,
        "message_id": result.message.message_id,
        "log_id": result.log_id,
        "error": result.error,
    }


@shared_task(autoretry_for=(SmsTransportError,), max_retries=3)
def send_sms_task(phone_number: str, text: str) -> dict[str, Any]:
    """Send a single SMS message through the configured backend.

    Returns a JSON-serializable summary (``ok``, ``message_id``, ``log_id``,
    ``error``) rather than the ``SendResult`` itself, which is not
    JSON-serializable.
    """
    client = SmsClient()
    result = client.send(phone_number, text)
    return _result_to_dict(result)
