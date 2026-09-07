"""Lazy, validated settings proxy for uzsms.

This module must not import ``django.conf.settings`` values at module
import time, so that importing ``uzsms.conf`` never requires
``SMS_SETTINGS`` to be configured. Resolution happens lazily, on first
attribute access, and is cached until :meth:`SmsSettings.reset` is called.
"""

from __future__ import annotations

import warnings
from typing import Any

from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver

from uzsms.exceptions import SmsConfigurationError

DEFAULTS: dict[str, Any] = {
    "BACKEND": "uzsms.backends.playmobile.PlaymobileBackend",
    "ORIGINATOR": "3700",
    "TIMEOUT": (5, 15),
    "MAX_RETRIES": 3,
    "RETRY_BACKOFF": 0.5,
    "POOL_MAXSIZE": 10,
    "LOG_MESSAGES": True,
    "FAIL_SILENTLY": False,
    "MAX_MESSAGE_LENGTH": 918,
    "PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "THROTTLE_RATE": "20/min",
}

REQUIRED_KEYS = ("URL", "LOGIN", "PASSWORD")

# Maps the current key name to the legacy key name it replaces, for the
# handful of keys that shipped under a different spelling in 1.0.1.
LEGACY_ALIASES: dict[str, str] = {
    "URL": "SMS_URL",
    "LOGIN": "SMS_LOGIN",
    "PASSWORD": "SMS_PASSWORD",
}


class SmsSettings:
    """Lazily resolves and caches ``SMS_SETTINGS`` entries."""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)

        if name in self._cache:
            return self._cache[name]

        value = self._resolve(name)
        self._cache[name] = value
        return value

    def _resolve(self, name: str) -> Any:
        user_settings = getattr(settings, "SMS_SETTINGS", {}) or {}

        if name in REQUIRED_KEYS:
            return self._resolve_required(name, user_settings)

        if name in DEFAULTS:
            return user_settings.get(name, DEFAULTS[name])

        raise AttributeError(f"'SmsSettings' object has no attribute {name!r}")

    def _resolve_required(self, name: str, user_settings: dict[str, Any]) -> Any:
        if name in user_settings:
            return user_settings[name]

        legacy_name = LEGACY_ALIASES[name]
        if legacy_name in user_settings:
            warnings.warn(
                f"SMS_SETTINGS[{legacy_name!r}] is deprecated, use "
                f"SMS_SETTINGS[{name!r}] instead.",
                DeprecationWarning,
                stacklevel=3,
            )
            return user_settings[legacy_name]

        raise SmsConfigurationError(
            f"SMS_SETTINGS[{name!r}] is required but was not provided."
        )

    def reset(self, *args: Any, **kwargs: Any) -> None:
        self._cache.clear()


sms_settings = SmsSettings()


@receiver(setting_changed)
def _reset_sms_settings(*, setting: str, **kwargs: Any) -> None:
    if setting == "SMS_SETTINGS":
        sms_settings.reset()
