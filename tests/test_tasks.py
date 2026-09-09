"""Tests for the optional Celery task in uzsms.tasks.

Celery is an optional extra (``uzsms[celery]``). These tests prove
both halves of that contract: importing ``uzsms.tasks`` never requires
Celery to be installed, and only *calling* the task without Celery raises a
clear, actionable error. The "Celery absent" cases run in a subprocess so
they exercise a genuinely fresh interpreter, mirroring the pattern used in
``tests/test_backend_playmobile_async.py`` for httpx's absence.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import override_settings

from uzsms.backends import locmem


@pytest.fixture(autouse=True)
def _locmem_settings():
    merged = {
        "URL": "https://example.com/sms/api",
        "LOGIN": "test-login",
        "PASSWORD": "test-password",
        "BACKEND": "uzsms.backends.locmem.LocMemBackend",
    }
    with override_settings(SMS_SETTINGS=merged):
        locmem.outbox.clear()
        yield
        locmem.outbox.clear()


def _run_script(script: str) -> subprocess.CompletedProcess:
    repo_root = Path(__file__).resolve().parents[1]
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        check=False,
    )


def test_module_imports_without_celery_installed():
    """A fresh interpreter with celery blocked must still import the module."""
    script = (
        "import sys\n"
        "sys.modules['celery'] = None\n"
        "import django\n"
        "import os\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')\n"
        "django.setup()\n"
        "import uzsms.tasks as tasks\n"
        "assert tasks.send_sms_task is not None\n"
        "print('OK')\n"
    )
    result = _run_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_calling_task_without_celery_raises_configuration_error_naming_the_extra():
    script = (
        "import sys\n"
        "sys.modules['celery'] = None\n"
        "import django\n"
        "import os\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')\n"
        "django.setup()\n"
        "import uzsms.tasks as tasks\n"
        "from uzsms.exceptions import SmsConfigurationError\n"
        "try:\n"
        "    tasks.send_sms_task('998901234567', 'hi')\n"
        "except SmsConfigurationError as exc:\n"
        "    assert 'uzsms[celery]' in str(exc), str(exc)\n"
        "    print('OK')\n"
        "else:\n"
        "    raise AssertionError('did not raise')\n"
    )
    result = _run_script(script)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


@pytest.mark.django_db
def test_task_sends_through_configured_backend_and_returns_json_serializable_dict():
    from uzsms.tasks import send_sms_task

    result = send_sms_task("998901234567", "hello there")

    assert result["ok"] is True
    assert result["error"] == ""
    assert len(locmem.outbox) == 1
    assert locmem.outbox[0].phone_number == "998901234567"

    # Must survive json.dumps -- SendResult itself is not JSON-serializable.
    serialized = json.dumps(result)
    assert json.loads(serialized) == result


def test_task_has_autoretry_configured_for_transport_errors():
    from uzsms.exceptions import SmsTransportError
    from uzsms.tasks import send_sms_task

    assert SmsTransportError in send_sms_task.autoretry_for
