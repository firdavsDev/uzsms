"""Value objects for uzsms.

This module must not import ``django.conf.settings`` or read settings at
module import time, so that importing ``uzsms`` never requires
``SMS_SETTINGS`` to be configured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
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
    provider_message_id: str | None = None
    status_code: int | None = None
    raw: Any = None
    error: str = ""
