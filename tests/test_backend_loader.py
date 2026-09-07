"""Tests for uzsms.backends.get_backend / get_async_backend."""

from __future__ import annotations

import pytest

from uzsms.backends import get_async_backend, get_backend
from uzsms.backends.base import BaseAsyncSmsBackend
from uzsms.backends.locmem import LocMemBackend
from uzsms.exceptions import SmsConfigurationError


class _AsyncEchoBackend(BaseAsyncSmsBackend):
    """A minimal importable async backend used only to exercise the loader."""

    async def send_messages(self, messages):
        return []


def test_get_backend_defaults_to_sms_settings_backend(settings_with_locmem):
    backend = get_backend()
    assert isinstance(backend, LocMemBackend)


def test_get_backend_honours_explicit_path():
    backend = get_backend("uzsms.backends.locmem.LocMemBackend")
    assert isinstance(backend, LocMemBackend)


def test_get_backend_passes_through_kwargs():
    backend = get_backend("uzsms.backends.dummy.DummyBackend", fail_silently=True)
    assert backend.fail_silently is True


def test_get_backend_returns_fresh_instance_each_call():
    first = get_backend("uzsms.backends.dummy.DummyBackend")
    second = get_backend("uzsms.backends.dummy.DummyBackend")
    assert first is not second


def test_get_backend_unimportable_path_raises_sms_configuration_error():
    with pytest.raises(SmsConfigurationError, match="not_a_real_module"):
        get_backend("not_a_real_module.NotARealBackend")


def test_get_backend_non_backend_class_raises_sms_configuration_error():
    with pytest.raises(SmsConfigurationError, match="uzsms.dto.SmsMessage"):
        get_backend("uzsms.dto.SmsMessage")


def test_get_backend_rejects_async_only_backend():
    path = f"{__name__}._AsyncEchoBackend"
    with pytest.raises(SmsConfigurationError, match=path):
        get_backend(path)


def test_get_async_backend_honours_explicit_path():
    path = f"{__name__}._AsyncEchoBackend"
    backend = get_async_backend(path)
    assert isinstance(backend, _AsyncEchoBackend)


def test_get_async_backend_rejects_sync_only_backend():
    with pytest.raises(SmsConfigurationError, match="uzsms.backends.dummy.DummyBackend"):
        get_async_backend("uzsms.backends.dummy.DummyBackend")


def test_get_backend_caches_resolved_class(monkeypatch):
    import uzsms.backends as backends_module

    monkeypatch.setattr(backends_module, "_backend_class_cache", {})

    calls = []
    original_import_string = backends_module.import_string

    def spy(path):
        calls.append(path)
        return original_import_string(path)

    monkeypatch.setattr(backends_module, "import_string", spy)

    get_backend("uzsms.backends.dummy.DummyBackend")
    get_backend("uzsms.backends.dummy.DummyBackend")

    assert calls == ["uzsms.backends.dummy.DummyBackend"]
