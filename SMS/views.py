# Create your views here.
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView, Response

from .serializers import ValidatePhoneNumber
from .sms_utils import SMS_Sender


class SendSmsAPIView(APIView):
    permission_classes = [AllowAny]
    serializer_class = ValidatePhoneNumber

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone_number = serializer.validated_data["phone_number"]
        message = serializer.validated_data["message"]
        configuration = SMS_Sender(phone_number, message)

        result = configuration.SendSmsOneContact()
        return Response(result)


send_sms_api_view = SendSmsAPIView.as_view()
