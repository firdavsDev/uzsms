"""Guarded entry point for the uzsms API's URLconf.

``uzsms.api`` (and, through it, Django REST Framework) is only imported
here, lazily, when a host project actually includes this URLconf. Importing
this module without DRF installed raises a clear, actionable
:class:`~uzsms.exceptions.SmsConfigurationError` instead of an opaque
``ImportError`` deep inside ``uzsms.api.views``.
"""

from __future__ import annotations

from uzsms.exceptions import SmsConfigurationError

try:
    from uzsms.api import urls as _api_urls
except ImportError as exc:
    raise SmsConfigurationError(
        "uzsms's HTTP API requires Django REST Framework, which is not "
        "installed. Install it with `pip install django-sms-uz[drf]`."
    ) from exc

app_name = _api_urls.app_name
urlpatterns = _api_urls.urlpatterns
