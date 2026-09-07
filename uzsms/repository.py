"""The single component permitted to write ``SmsLog`` rows.

Isolating persistence here keeps ``uzsms``'s send-path client free of ORM
concerns, and lets it batch every log write into a constant number of
queries regardless of how many messages are being sent.
"""

from __future__ import annotations

from collections.abc import Sequence

from uzsms.conf import sms_settings
from uzsms.dto import SendResult, SmsMessage
from uzsms.models import SmsLog

UPDATE_FIELDS = (
    "status",
    "sent_at",
    "message_id",
    "provider_response",
    "error",
    "is_active",
)


class SmsLogRecorder:
    """Bulk-persists ``SmsLog`` rows. The only class allowed to write them."""

    def __init__(self, enabled: bool | None = None) -> None:
        self.enabled = sms_settings.LOG_MESSAGES if enabled is None else enabled

    def create_pending(self, messages: Sequence[SmsMessage]) -> list[SmsLog]:
        """Create one PENDING ``SmsLog`` row per message, in a single query."""
        if not self.enabled:
            return []

        logs = [
            SmsLog(
                phone_number=message.phone_number,
                text=message.text,
                message_id=message.message_id,
            )
            for message in messages
        ]
        return SmsLog.objects.bulk_create(logs)

    def record_results(
        self, logs: Sequence[SmsLog], results: Sequence[SendResult]
    ) -> None:
        """Apply send outcomes to ``logs`` in place, in a single query."""
        if len(logs) != len(results):
            raise ValueError(
                f"logs and results must have the same length "
                f"(got {len(logs)} and {len(results)})"
            )

        if not self.enabled or not logs:
            return

        for log, result in zip(logs, results):
            if result.ok:
                log.mark_sent(result)
            else:
                log.mark_failed(result)

        SmsLog.objects.bulk_update(logs, UPDATE_FIELDS)
