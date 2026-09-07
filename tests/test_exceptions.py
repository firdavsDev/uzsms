"""Tests for the uzsms exception hierarchy."""

import pytest
from django.core.exceptions import ImproperlyConfigured

from uzsms.exceptions import (
    SmsConfigurationError,
    SmsError,
    SmsProviderError,
    SmsTransportError,
    SmsValidationError,
)


def test_sms_configuration_error_is_caught_by_improperly_configured():
    try:
        raise SmsConfigurationError("bad config")
    except ImproperlyConfigured:
        pass
    else:
        raise AssertionError("SmsConfigurationError was not caught by ImproperlyConfigured")


def test_sms_configuration_error_is_caught_by_sms_error():
    try:
        raise SmsConfigurationError("bad config")
    except SmsError:
        pass
    else:
        raise AssertionError("SmsConfigurationError was not caught by SmsError")


def test_sms_validation_error_is_sms_error():
    assert issubclass(SmsValidationError, SmsError)


def test_sms_transport_error_is_sms_error():
    assert issubclass(SmsTransportError, SmsError)


def test_sms_provider_error_is_sms_error():
    assert issubclass(SmsProviderError, SmsError)


def test_sms_provider_error_exposes_status_code_and_body():
    error = SmsProviderError("x", status_code=502, body={"a": 1})

    assert error.status_code == 502
    assert error.body == {"a": 1}
    assert str(error) == "x"


def test_sms_provider_error_requires_keyword_only_arguments():
    with pytest.raises(TypeError):
        SmsProviderError("x", 502, {"a": 1})
