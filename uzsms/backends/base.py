"""Abstract backend interface for uzsms.

This module must not import ``django.conf.settings`` at module import
time, so that importing ``uzsms.backends.base`` never requires
``SMS_SETTINGS`` to be configured.
"""

from __future__ import annotations

import abc
from collections.abc import Sequence
from typing import TYPE_CHECKING

from uzsms.conf import sms_settings
from uzsms.dto import SendResult, SmsMessage

if TYPE_CHECKING:
    from typing_extensions import Self


class BaseSmsBackend(abc.ABC):
    """Abstract base class for synchronous SMS backends.

    Subclasses implement :meth:`send_messages`. ``open``/``close`` are
    no-ops by default; override them to acquire/release resources such
    as a pooled HTTP connection.
    """

    def __init__(self, *, fail_silently: bool | None = None) -> None:
        self.fail_silently = (
            sms_settings.FAIL_SILENTLY if fail_silently is None else fail_silently
        )

    def open(self) -> None:
        """Open any resources needed to send messages."""

    def close(self) -> None:
        """Close resources opened by :meth:`open`."""

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @abc.abstractmethod
    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        """Send ``messages`` and return one :class:`SendResult` per message, in order."""


class BaseAsyncSmsBackend(abc.ABC):
    """Abstract base class for asynchronous SMS backends.

    Mirrors :class:`BaseSmsBackend`, but with an ``async`` ``open``/``close``
    and ``send_messages``.
    """

    def __init__(self, *, fail_silently: bool | None = None) -> None:
        self.fail_silently = (
            sms_settings.FAIL_SILENTLY if fail_silently is None else fail_silently
        )

    async def open(self) -> None:
        """Open any resources needed to send messages."""

    async def close(self) -> None:
        """Close resources opened by :meth:`open`."""

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.close()

    @abc.abstractmethod
    async def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        """Send ``messages`` and return one :class:`SendResult` per message, in order."""
