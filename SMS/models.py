from django.db import models


class SmsLog(models.Model):
    phone_number = models.CharField(max_length=35)
    text = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Log"
        verbose_name_plural = "Logs"
        # ordering = ["-id"]

    def __str__(self):
        return self.phone_number
