from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class ValidatePhoneNumber(serializers.Serializer):
    phone_number = serializers.CharField()
    message = serializers.CharField()

    def validate_phone_number(self, phone_number):
        self._errors = []
        if len(phone_number) != 12:
            message = _(
                "Phone number must have 12 characters and"
                " format should be '998xxxxxxxxx'"
            )
            self._errors.append(message)

        if self._errors:
            raise serializers.ValidationError(self._errors)
        return phone_number
