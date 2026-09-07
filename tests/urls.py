"""URLconf used only by the test suite (e.g. to exercise the admin site)."""

from __future__ import annotations

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
]
