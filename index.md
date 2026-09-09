# django-sms-uz

An SMS-sending package for Django, built for the Playmobile broker used by
Uzbek telecom operators. It provides a validated, pooled, retrying HTTP
client (sync and async), a Django model that logs every outgoing message,
an optional DRF endpoint, and an optional Celery task.

The importable package is `uzsms` (the PyPI distribution name is still
`django-sms-uz`).

> ### ⚠️ Upgrading from 1.x?
> 2.0 is a **breaking release**. Its migration history was replaced with a
> single `0001_initial`, which is **data-destroying** for existing
> installs — do not run `migrate` before reading the upgrade guide:
> [UPGRADE.md](https://github.com/firdavsDev/django-sms-uz/blob/refactor/2.0/UPGRADE.md).

## Installation

```bash
pip install django-sms-uz
```

With extras, as needed:

```bash
pip install "django-sms-uz[drf]"      # HTTP API (Django REST Framework)
pip install "django-sms-uz[async]"    # AsyncSmsClient / AsyncPlaymobileBackend (httpx)
pip install "django-sms-uz[celery]"   # uzsms.tasks.send_sms_task
```

## Quickstart

```python
INSTALLED_APPS = [
    ...,
    "uzsms",
]

SMS_SETTINGS = {
    "URL": "http://91.204.239.44/broker-api/send",
    "LOGIN": "your-login",
    "PASSWORD": "your-password",
}
```

```bash
python manage.py migrate
```

```python
from uzsms import SmsClient

client = SmsClient()
result = client.send("998901234567", "hello there")
result.ok  # True/False
```

To expose the HTTP endpoint, install the `drf` extra and include the URLs:

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    ...,
    path("sms/", include("uzsms.urls")),
]
```

This puts the send endpoint at **`/sms/send/`** — note this is a changed
path from 1.0.1's `/sms/send_sms/`.

## `SMS_SETTINGS`

`URL`, `LOGIN`, and `PASSWORD` are required. Everything else has a default:

| Key | Default | Meaning |
|---|---|---|
| `URL` | *(required)* | The broker's send endpoint URL. |
| `LOGIN` | *(required)* | Broker HTTP Basic Auth username. |
| `PASSWORD` | *(required)* | Broker HTTP Basic Auth password. |
| `BACKEND` | `uzsms.backends.playmobile.PlaymobileBackend` | Dotted path to the backend class used to send messages. |
| `ORIGINATOR` | `"3700"` | The `sms.originator` value sent in every message envelope. |
| `TIMEOUT` | `(5, 15)` | `(connect, read)` timeout in seconds. |
| `MAX_RETRIES` | `3` | Retries on a transient failure (connection error or `429`/`500`/`502`/`503`/`504`). |
| `RETRY_BACKOFF` | `0.5` | Exponential backoff factor between retries. |
| `POOL_MAXSIZE` | `10` | Max size of the pooled HTTP connection pool. |
| `LOG_MESSAGES` | `True` | Whether to persist a `SmsLog` row for every message sent. |
| `FAIL_SILENTLY` | `False` | When `True`, a failure returns `SendResult(ok=False, ...)` instead of raising. |
| `MAX_MESSAGE_LENGTH` | `918` | Maximum allowed message length in characters. |
| `PERMISSION_CLASSES` | `["rest_framework.permissions.IsAuthenticated"]` | DRF permission classes on the send endpoint. **Auth is required by default.** |
| `THROTTLE_RATE` | `"20/min"` | DRF throttle rate on the send endpoint. |

Legacy names `SMS_URL`/`SMS_LOGIN`/`SMS_PASSWORD` still work, with a
`DeprecationWarning`.

## Sync and async usage

```python
from uzsms import SmsClient, SmsMessage

client = SmsClient()
result = client.send("998901234567", "hello there")

results = client.send_bulk([
    SmsMessage(phone_number="998901234567", text="hi"),
    SmsMessage(phone_number="998907654321", text="hello"),
])
```

Async (requires the `async` extra):

```python
from uzsms import AsyncSmsClient

client = AsyncSmsClient()
result = await client.send("998901234567", "hello there")
```

## HTTP API response envelope

Every response — success or failure — has exactly the same three keys.
HTTP status codes stay meaningful (`201`/`400`/`401`/`403`/`429`/`502`).

Success (`201 Created`):

```json
{
    "success": true,
    "data": {
        "message_id": "b3f1...e2a9",
        "log_id": 42,
        "phone_number": "998901234567",
        "status": "sent"
    },
    "error": null
}
```

Failure (e.g. `400 Bad Request`):

```json
{
    "success": false,
    "data": null,
    "error": {
        "code": "validation_error",
        "message": "'12345' is not a valid Uzbek phone number; expected '998' followed by nine digits.",
        "detail": null
    }
}
```

The broker's raw response body is never forwarded to API callers — only
the upstream status code, on a `502`.

## Full documentation

The complete docs — the full settings table, the backend list and how to
write a custom one, the Celery task, the `SmsLog` model and its `status`
values, troubleshooting, and known limitations — live in the package
README:

- [README](https://github.com/firdavsDev/django-sms-uz/blob/refactor/2.0/README.md)
- [UPGRADE guide (1.x → 2.0)](https://github.com/firdavsDev/django-sms-uz/blob/refactor/2.0/UPGRADE.md)
- [CHANGELOG](https://github.com/firdavsDev/django-sms-uz/blob/refactor/2.0/CHANGELOG.md)
- [PyPI project page](https://pypi.org/project/django-sms-uz/)

### Contact

Found a bug or have an idea? Open an issue on
[GitHub](https://github.com/firdavsDev/django-sms-uz/issues).
