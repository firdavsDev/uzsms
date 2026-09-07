"""Data models for uzsms."""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from .dto import SendResult


class SmsLog(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        SENT = "sent"
        FAILED = "failed"

    phone_number = models.CharField(max_length=35)
    text = models.TextField()
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    message_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    provider_response = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    is_active = models.BooleanField(
        default=False,
        help_text=(
            "DEPRECATED: mirrors status == SENT. Scheduled for removal in 3.0; "
            "read `status` instead."
        ),
    )

    class Meta:
        verbose_name = "Log"
        verbose_name_plural = "Logs"
        ordering = ("-created_at",)
        indexes = (models.Index(fields=("phone_number", "created_at")),)

    def __str__(self):
        return self.phone_number

    def mark_sent(self, result: SendResult) -> None:
        """Set fields to reflect a successful send. Does not call save()."""
        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.message_id = result.provider_message_id or ""
        self.provider_response = result.raw
        self.is_active = True

    def mark_failed(self, result: SendResult) -> None:
        """Set fields to reflect a failed send. Does not call save()."""
        self.status = self.Status.FAILED
        self.error = result.error
