from django.contrib import admin

from .models import SmsLog


class SmsLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "phone_number",
        "text",
        "is_active",
    )
    list_display_links = ("id", "phone_number")
    list_editable = ("is_active",)


admin.site.register(SmsLog, SmsLogAdmin)
