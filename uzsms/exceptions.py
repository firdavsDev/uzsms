"""Exception hierarchy for uzsms.

This module must not import ``django.conf.settings`` or read settings at
module import time, so that importing ``uzsms`` never requires
``SMS_SETTINGS`` to be configured.
"""

from django.core.exceptions import ImproperlyConfigured


class SmsError(Exception):
    """Root of the uzsms exception hierarchy."""


class SmsConfigurationError(SmsError, ImproperlyConfigured):
    """Raised when uzsms is misconfigured.

    Inherits from both ``SmsError`` and Django's ``ImproperlyConfigured``
    so that host projects catching either base still catch this.
    """


class SmsValidationError(SmsError):
    """Raised when an outgoing SMS message fails validation."""


class SmsTransportError(SmsError):
    """Raised on network failure, timeout, or exhausted retries."""


class SmsProviderError(SmsError):
    """Raised when the broker returns a non-success response."""

    def __init__(self, message, *, status_code=None, body=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body
