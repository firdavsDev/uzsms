"""URLconf for the uzsms API.

Host projects include this (indirectly, via ``uzsms.urls``) under their
own prefix, e.g. ``path("sms/", include("uzsms.urls"))`` — which, with
owner decision D7's route path, puts the send endpoint at ``/sms/send/``.
"""

from __future__ import annotations

from django.urls import path

from uzsms.api.views import send_sms_api_view

app_name = "uzsms"

urlpatterns = [
    path("send/", send_sms_api_view, name="send_sms"),
]
