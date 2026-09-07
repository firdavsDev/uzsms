"""Value objects for uzsms.

This module must not import ``django.conf.settings`` or read settings at
module import time, so that importing ``uzsms`` never requires
``SMS_SETTINGS`` to be configured.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4


@dataclass(frozen=True)
class SmsMessage:
    phone_number: str
    text: str
    message_id: str = field(default_factory=lambda: uuid4().hex)


@dataclass(frozen=True)
class SendResult:
    message: SmsMessage
    ok: bool
    # `from __future__ import annotations` is intentionally not used here: it
    # would let ruff (FA100) push these toward `str | None` PEP 604 syntax,
    # which is unsupported on the Python 3.9 floor this module targets.
    provider_message_id: Optional[str] = None  # noqa: FA100
    status_code: Optional[int] = None  # noqa: FA100
    raw: Any = None
    error: str = ""
