"""Tests that the app-label pin survives the SMS -> uzsms package rename.

Django records applied migrations in the ``django_migrations`` table keyed
by app *label*, and derives the default table name from that label. These
assertions prove that renaming the Python package to ``uzsms`` while
pinning ``label = "SMS"`` on the AppConfig leaves the on-disk migration
state and the database table name completely undisturbed.
"""

import pytest
from django.apps import apps
from django.core.management import call_command

from uzsms.models import SmsLog


def test_app_config_name_is_uzsms():
    app_config = apps.get_app_config("SMS")
    assert app_config.name == "uzsms"


def test_smslog_db_table_is_unchanged():
    assert SmsLog._meta.db_table == "SMS_smslog"


def test_smslog_app_label_is_unchanged():
    assert SmsLog._meta.app_label == "SMS"


@pytest.mark.django_db
def test_makemigrations_reports_no_pending_changes():
    try:
        call_command("makemigrations", "SMS", "--check", "--dry-run")
    except SystemExit as exc:
        assert exc.code == 0, "makemigrations reported pending model changes"
