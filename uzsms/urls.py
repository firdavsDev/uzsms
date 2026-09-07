from django.urls import path

from . import views

urlpatterns = [
    path(
        "send_sms/",
        view=views.send_sms_api_view,
        name="send_sms",
    ),
]