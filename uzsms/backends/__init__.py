"""Backend loader for uzsms.

Resolves a dotted backend path (``sms_settings.BACKEND`` for
:func:`get_backend`, ``sms_settings.ASYNC_BACKEND`` for
:func:`get_async_backend`, when no explicit ``path`` is given) to a backend
class via :func:`django.utils.module_loading.import_string`, and
instantiates it. The resolved class is cached per ``(path, base)`` pair so
repeated calls avoid re-importing; each call still returns a fresh
*instance*, since backends may hold open connections.
"""

from __future__ import annotations

from typing import Any

from django.utils.module_loading import import_string

from uzsms.backends.base import BaseAsyncSmsBackend, BaseSmsBackend
from uzsms.conf import sms_settings
from uzsms.exceptions import SmsConfigurationError

_backend_class_cache: dict[tuple[str, type], type] = {}


def _resolve_backend_class(path: str, base: type) -> type:
    cache_key = (path, base)
    if cache_key in _backend_class_cache:
        return _backend_class_cache[cache_key]

    try:
        backend_class = import_string(path)
    except ImportError as exc:
        raise SmsConfigurationError(f"Could not import SMS backend {path!r}: {exc}") from exc

    if not (isinstance(backend_class, type) and issubclass(backend_class, base)):
        raise SmsConfigurationError(
            f"SMS backend {path!r} does not resolve to a subclass of {base.__name__}."
        )

    _backend_class_cache[cache_key] = backend_class
    return backend_class


def get_backend(path: str | None = None, **kwargs: Any) -> BaseSmsBackend:
    """Resolve and instantiate a synchronous SMS backend.

    Defaults to ``sms_settings.BACKEND`` when ``path`` is ``None``.
    """
    resolved_path = sms_settings.BACKEND if path is None else path
    backend_class = _resolve_backend_class(resolved_path, BaseSmsBackend)
    return backend_class(**kwargs)


def get_async_backend(path: str | None = None, **kwargs: Any) -> BaseAsyncSmsBackend:
    """Resolve and instantiate an asynchronous SMS backend.

    Defaults to ``sms_settings.ASYNC_BACKEND`` when ``path`` is ``None`` --
    a separate setting from ``sms_settings.BACKEND`` (which defaults to the
    *sync* Playmobile backend), so ``SmsClient`` and ``AsyncSmsClient`` can
    both be constructed from one unmodified ``SMS_SETTINGS`` dict.
    """
    resolved_path = sms_settings.ASYNC_BACKEND if path is None else path
    backend_class = _resolve_backend_class(resolved_path, BaseAsyncSmsBackend)
    return backend_class(**kwargs)
