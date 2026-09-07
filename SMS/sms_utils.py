import json
from dataclasses import dataclass

import requests
from django.conf import settings

from .models import SmsLog

# initializing variables
SMS_ENDPOINT = settings.SMS_SETTINGS["SMS_URL"]
SMS_LOGIN = settings.SMS_SETTINGS["SMS_LOGIN"]
SMS_PASSWORD = settings.SMS_SETTINGS["SMS_PASSWORD"]
# end of initializing variables


@dataclass
class SMS_Sender:
    number: int
    message: str

    def SendSmsOneContact(self):
        try:
            dt = {
                "messages": [
                    {
                        "recipient": self.number,
                        "message-id": "abc000000001",
                        "sms": {
                            "originator": "3700",
                            "content": {"text": self.message},
                        },
                    }
                ]
            }
            # Logging the SMS
            self.create_sms_log(phone_number=self.number, message=self.message)
            return requests.post(
                url=SMS_ENDPOINT,
                auth=(SMS_LOGIN, SMS_PASSWORD),
                headers={"content-type": "application/json"},
                data=json.dumps(dt),
            )

        except Exception as e:
            return f"Error: {e!s}"

    def create_sms_log(self, phone_number, message):
        SmsLog.objects.create(phone_number=phone_number, text=message)
