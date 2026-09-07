"""Tests for the backend interface (BaseSmsBackend/BaseAsyncSmsBackend) and
the three offline backends (Console, LocMem, Dummy).
"""

from __future__ import annotations

import pytest

from uzsms.backends import locmem
from uzsms.backends.base import BaseAsyncSmsBackend, BaseSmsBackend
from uzsms.backends.console import ConsoleBackend
from uzsms.backends.dummy import DummyBackend
from uzsms.backends.locmem import LocMemBackend
from uzsms.conf import sms_settings
from uzsms.dto import SendResult, SmsMessage


def _messages() -> list[SmsMessage]:
    return [
        SmsMessage(phone_number="998901234567", text="first"),
        SmsMessage(phone_number="998901234568", text="second"),
    ]


def test_base_sms_backend_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseSmsBackend()


def test_base_async_sms_backend_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        BaseAsyncSmsBackend()


@pytest.mark.parametrize("backend_class", [ConsoleBackend, DummyBackend, LocMemBackend])
def test_backend_returns_one_result_per_message_in_order_all_ok(backend_class, locmem_backend):
    messages = _messages()
    backend = backend_class()
    results = backend.send_messages(messages)

    assert len(results) == len(messages)
    for message, result in zip(messages, results):
        assert result.message is message
        assert result.ok is True


def test_console_backend_prints_message_details(capsys):
    messages = _messages()
    backend = ConsoleBackend()
    backend.send_messages(messages)

    captured = capsys.readouterr()
    for message in messages:
        assert message.phone_number in captured.out
        assert message.text in captured.out


def test_console_backend_performs_no_network_io(capsys):
    # Sanity check: sending must not raise even without any network stack
    # configured (no SMS_SETTINGS URL/LOGIN/PASSWORD required at all).
    backend = ConsoleBackend()
    results = backend.send_messages(_messages())
    assert all(result.ok for result in results)


def test_locmem_backend_appends_to_module_level_outbox(locmem_backend):
    messages = _messages()
    backend = LocMemBackend()
    backend.send_messages(messages)

    assert locmem.outbox == messages


def test_locmem_outbox_is_cleared_before_each_test(locmem_backend):
    # If a previous test's messages leaked into the outbox, this would fail.
    assert locmem.outbox == []


def test_dummy_backend_discards_messages():
    backend = DummyBackend()
    results = backend.send_messages(_messages())
    assert all(result.ok for result in results)


class _RecordingBackend(BaseSmsBackend):
    """Concrete backend that records open()/close() calls for assertions."""

    def __init__(self, *, fail_silently=None):
        super().__init__(fail_silently=fail_silently)
        self.calls: list[str] = []

    def open(self):
        self.calls.append("open")

    def close(self):
        self.calls.append("close")

    def send_messages(self, messages):
        return [SendResult(message=message, ok=True) for message in messages]


def test_sync_context_manager_calls_open_on_enter_and_close_on_exit():
    backend = _RecordingBackend()
    assert backend.calls == []

    with backend as entered:
        assert entered is backend
        assert backend.calls == ["open"]

    assert backend.calls == ["open", "close"]


class _RecordingAsyncBackend(BaseAsyncSmsBackend):
    """Concrete async backend that records open()/close() calls for assertions."""

    def __init__(self, *, fail_silently=None):
        super().__init__(fail_silently=fail_silently)
        self.calls: list[str] = []

    async def open(self):
        self.calls.append("open")

    async def close(self):
        self.calls.append("close")

    async def send_messages(self, messages):
        return [SendResult(message=message, ok=True) for message in messages]


@pytest.mark.asyncio
async def test_async_context_manager_calls_open_on_enter_and_close_on_exit():
    backend = _RecordingAsyncBackend()
    assert backend.calls == []

    async with backend as entered:
        assert entered is backend
        assert backend.calls == ["open"]

    assert backend.calls == ["open", "close"]


def test_fail_silently_defaults_from_settings():
    assert DummyBackend().fail_silently == sms_settings.FAIL_SILENTLY


def test_fail_silently_explicit_override_wins():
    backend = DummyBackend(fail_silently=True)
    assert backend.fail_silently is True
