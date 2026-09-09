"""django-sms-uz: SMS sending API for Django.

This module defines the package's public API surface (``__all__``) and
must stay free of two things: reading ``SMS_SETTINGS`` at import time, and
importing Django models at module level. Django imports an app's
``__init__.py`` while populating the app registry, before it is ready --
an eager import of anything that reaches ``uzsms.models`` (directly, or
transitively through ``uzsms.services`` or ``uzsms.repository``) would
raise ``AppRegistryNotReady``. Names that reach the ORM are therefore
resolved lazily, on first attribute access, via a module-level
``__getattr__`` (PEP 562).
"""

from __future__ import annotations

import importlib
from typing import Any

from uzsms.backends import get_async_backend, get_backend
from uzsms.dto import SendResult, SmsMessage
from uzsms.exceptions import (
    SmsBackendError,
    SmsConfigurationError,
    SmsError,
    SmsProviderError,
    SmsTransportError,
    SmsValidationError,
)

__all__ = [
    "AsyncSmsClient",
    "SMS_Sender",
    "SendResult",
    "SmsBackendError",
    "SmsClient",
    "SmsConfigurationError",
    "SmsError",
    "SmsLogRecorder",
    "SmsMessage",
    "SmsProviderError",
    "SmsTransportError",
    "SmsValidationError",
    "get_async_backend",
    "get_backend",
]

# Names that resolve lazily, via __getattr__ below, because importing their
# home module reaches Django models (directly or transitively).
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "SmsClient": ("uzsms.services", "SmsClient"),
    "AsyncSmsClient": ("uzsms.services", "AsyncSmsClient"),
    "SmsLogRecorder": ("uzsms.repository", "SmsLogRecorder"),
    "SMS_Sender": ("uzsms.compat", "SMS_Sender"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    module = importlib.import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
