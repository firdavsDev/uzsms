"""Tests for the SmsLog admin registration."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from uzsms.admin import SmsLogAdmin


@pytest.fixture
def staff_user(db):
    User = get_user_model()
    return User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password",
    )


@pytest.mark.django_db
def test_changelist_renders_for_staff_user(client, staff_user):
    client.force_login(staff_user)

    url = reverse("admin:SMS_smslog_changelist")
    response = client.get(url)

    assert response.status_code == 200


def test_is_active_is_not_editable_inline():
    assert "is_active" not in getattr(SmsLogAdmin, "list_editable", ())
