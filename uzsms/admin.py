"""Admin registration for uzsms models."""

from __future__ import annotations

from django.contrib import admin

from .models import SmsLog


@admin.register(SmsLog)
class SmsLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "phone_number",
        "text",
        "status",
        "created_at",
        "sent_at",
    )
    list_display_links = ("id", "phone_number")
    list_filter = ("status", "created_at")
    search_fields = ("phone_number", "message_id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "provider_response",
        "error",
        "created_at",
        "sent_at",
        "message_id",
    )
