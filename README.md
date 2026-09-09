# uzsms

[![PyPI](https://img.shields.io/pypi/v/uzsms.svg)](https://pypi.org/project/uzsms/)
[![Python versions](https://img.shields.io/pypi/pyversions/uzsms.svg)](https://pypi.org/project/uzsms/)
[![Django versions](https://img.shields.io/badge/django-4.2%20%7C%205.0%20%7C%205.1-092E20.svg)](https://pypi.org/project/uzsms/)
[![CI](https://github.com/firdavsDev/uzsms/actions/workflows/ci.yml/badge.svg)](https://github.com/firdavsDev/uzsms/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/pypi/l/uzsms.svg)](https://github.com/firdavsDev/uzsms/blob/master/LICENSE)

An SMS-sending package for Django, built for the Playmobile broker used by
Uzbek telecom operators. It provides a validated, pooled, retrying HTTP
client (sync and async), a Django model that logs every outgoing message,
an optional DRF endpoint, and an optional Celery task.

As of 2.0 the distribution and the importable package share one name:
`pip install uzsms`, `import uzsms`. 1.x was published as
`django-sms-uz`, which is now frozen at 1.0.1 and receives no further
releases.

> **Upgrading from 1.x?** Read [`UPGRADE.md`](UPGRADE.md) first. 2.0 is a
> breaking release, and its migration history is **data-destroying** for
> existing installs — do not run `migrate` before reading it.

## Installation

Base install (sync sending only, no HTTP API, no Celery task):

```bash
pip install uzsms
```

With extras, as needed:

```bash
pip install "uzsms[drf]"      # HTTP API (Django REST Framework)
pip install "uzsms[async]"    # AsyncSmsClient / AsyncPlaymobileBackend (httpx)
pip install "uzsms[celery]"   # uzsms.tasks.send_sms_task
pip install "uzsms[drf,async,celery]"  # any combination
```

For contributing to this package itself:

```bash
pip install "uzsms[dev]"
```

## Quickstart

Add the app:

```python
INSTALLED_APPS = [
    ...,
    "uzsms",
]
```

Configure the broker credentials (the only three required keys — see the
full settings table below for everything else):

```python
SMS_SETTINGS = {
    "URL": "http://91.204.239.44/broker-api/send",
    "LOGIN": "your-login",
    "PASSWORD": "your-password",
}
```

Run migrations:

```bash
python manage.py migrate
```

Send a message:

```python
from uzsms import SmsClient

client = SmsClient()
result = client.send("998901234567", "hello there")
result.ok  # True/False
```

If you also want the HTTP endpoint, install the `drf` extra and include the
URLs:

```python
# urls.py
from django.urls import include, path

urlpatterns = [
    ...,
    path("sms/", include("uzsms.urls")),
]
```

This puts the send endpoint at `/sms/send/` (see "HTTP API" below).

## `SMS_SETTINGS`

All settings live under one dict, `SMS_SETTINGS`, in your Django settings
module. `URL`, `LOGIN`, and `PASSWORD` are required (no default); everything
else falls back to the default shown below. Defaults are read from
`uzsms/conf.py`.

| Key                 | Default                                          | Meaning |
|----------------------|--------------------------------------------------|---------|
| `URL`                | *(required)*                                     | The broker's send endpoint URL. |
| `LOGIN`              | *(required)*                                     | Broker HTTP Basic Auth username. |
| `PASSWORD`           | *(required)*                                     | Broker HTTP Basic Auth password. |
| `BACKEND`            | `"uzsms.backends.playmobile.PlaymobileBackend"`  | Dotted path to the backend class `SmsClient` uses to actually send messages. |
| `ASYNC_BACKEND`      | `"uzsms.backends.playmobile.AsyncPlaymobileBackend"` | Dotted path to the backend class `AsyncSmsClient` uses. A separate setting from `BACKEND`, so a project can use `SmsClient` and `AsyncSmsClient` side by side from one unmodified `SMS_SETTINGS` dict. |
| `ORIGINATOR`         | `"3700"`                                         | The `sms.originator` value sent in every message envelope. |
| `TIMEOUT`            | `(5, 15)`                                        | `(connect, read)` timeout in seconds, passed straight to `requests`/mapped onto `httpx.Timeout`. |
| `MAX_RETRIES`        | `3`                                               | Number of retries on a transient failure (connection error or a `429`/`500`/`502`/`503`/`504` response). Safe because every message carries a stable `message_id` the broker deduplicates on retry — see the docstring in `uzsms/backends/playmobile.py`. |
| `RETRY_BACKOFF`      | `0.5`                                             | Backoff factor between retries (exponential, per `urllib3.util.Retry`/the async backend's own backoff loop). |
| `POOL_MAXSIZE`       | `10`                                              | Max size of the pooled HTTP connection pool (`requests.Session`'s adapter, or `httpx.Limits`). |
| `LOG_MESSAGES`       | `True`                                            | Whether to persist a `SmsLog` row for every message sent. |
| `FAIL_SILENTLY`      | `False`                                           | When `True`, a send failure returns a `SendResult(ok=False, ...)` instead of raising. |
| `MAX_MESSAGE_LENGTH` | `918`                                             | Maximum allowed message length in characters; longer text raises `SmsValidationError`. |
| `PERMISSION_CLASSES` | `["rest_framework.permissions.IsAuthenticated"]`  | Dotted paths to DRF permission classes applied to the send endpoint. **The endpoint requires authentication by default.** |
| `THROTTLE_RATE`      | `"20/min"`                                        | DRF throttle rate string applied to the send endpoint. |

Legacy key names `SMS_URL`, `SMS_LOGIN`, and `SMS_PASSWORD` (1.0.1's
spelling of `URL`/`LOGIN`/`PASSWORD`) still work, but emit a
`DeprecationWarning`. The new name always wins if both are present.

## Sync usage

```python
from uzsms import SmsClient

client = SmsClient()

# Single message
result = client.send("998901234567", "hello there")
# result: SendResult(message=SmsMessage(...), ok=True, provider_message_id=None,
#                     status_code=200, raw=..., error="", log_id=42)
# provider_message_id is None because no shipped backend populates it (see
# "The SmsLog model" below) — the id that matters is message.message_id,
# the one actually sent on the wire.

# Multiple messages, sent as one batched HTTP request
from uzsms import SmsMessage

results = client.send_bulk([
    SmsMessage(phone_number="998901234567", text="hi"),
    SmsMessage(phone_number="998907654321", text="hello"),
])
```

`send`/`send_bulk` validate every message (Uzbek phone format, non-empty
text under `MAX_MESSAGE_LENGTH`) before writing anything to the database,
create `SmsLog` rows in bulk (a single query for any number of messages,
unless `LOG_MESSAGES` is `False`), send through the configured backend, and
update those rows in bulk with the outcome.

## Async usage

Requires the `async` extra (`pip install "uzsms[async]"`, which
installs `httpx`):

```python
from uzsms import AsyncSmsClient

client = AsyncSmsClient()
result = await client.send("998901234567", "hello there")
results = await client.send_bulk([...])
```

`AsyncSmsClient` has the same validation-before-write and constant-query
contract as `SmsClient`; its ORM calls go through
`asgiref.sync.sync_to_async` since Django's ORM is not async-safe.

## Backends

Select the sync backend with `SMS_SETTINGS["BACKEND"]`, and the async
backend `AsyncSmsClient` uses with `SMS_SETTINGS["ASYNC_BACKEND"]` — each a
dotted path to a class implementing `uzsms.backends.base.BaseSmsBackend`
(sync) or `BaseAsyncSmsBackend` (async), respectively:

| Backend | Path | Behavior |
|---|---|---|
| Playmobile (default) | `uzsms.backends.playmobile.PlaymobileBackend` | Sends over a pooled, retrying `requests.Session`. |
| Async Playmobile | `uzsms.backends.playmobile.AsyncPlaymobileBackend` | Same broker, over a cached `httpx.AsyncClient`. Used automatically by `AsyncSmsClient`. |
| Console | `uzsms.backends.console.ConsoleBackend` | Prints each message to stdout; always reports success. No network I/O. Useful for local development. |
| LocMem | `uzsms.backends.locmem.LocMemBackend` (sync) / `AsyncLocMemBackend` (async) | Appends each message to a module-level `outbox` list (`uzsms.backends.locmem.outbox`), like Django's `django.core.mail.outbox`. No network I/O. Useful for tests. |
| Dummy | `uzsms.backends.dummy.DummyBackend` | Discards every message; always reports success. No network I/O. |

```python
SMS_SETTINGS = {
    ...,
    "BACKEND": "uzsms.backends.console.ConsoleBackend",
}
```

### Writing a custom backend

Subclass `uzsms.backends.base.BaseSmsBackend` (or `BaseAsyncSmsBackend`) and
implement `send_messages`:

```python
from collections.abc import Sequence
from uzsms.backends.base import BaseSmsBackend
from uzsms.dto import SendResult, SmsMessage

class MyBackend(BaseSmsBackend):
    def send_messages(self, messages: Sequence[SmsMessage]) -> list[SendResult]:
        # Must return exactly one SendResult per message, in the same order.
        ...
```

`get_backend()` (in `uzsms/backends/__init__.py`) resolves
`SMS_SETTINGS["BACKEND"]`, and `get_async_backend()` resolves
`SMS_SETTINGS["ASYNC_BACKEND"]`, each via
`django.utils.module_loading.import_string` and instantiates it; a backend
that doesn't subclass the expected base raises `SmsConfigurationError`.
`open()`/`close()` are no-op hooks you can override to acquire/release
resources (backends also work as a context manager via `with backend:` /
`async with backend:`).

## Celery task

Requires the `celery` extra (`pip install "uzsms[celery]"`).
Calling `uzsms.tasks.send_sms_task` without Celery installed raises
`SmsConfigurationError` naming the extra; importing the module is always
safe.

```python
from uzsms.tasks import send_sms_task

send_sms_task.delay("998901234567", "hello there")
```

The task retries automatically on `SmsTransportError` (up to 3 times) and
returns a JSON-serializable summary dict (`ok`, `message_id`, `log_id`,
`error`) rather than a `SendResult`, since `SendResult` isn't JSON
serializable on its own.

## HTTP API

Include the URLs under whatever prefix you like:

```python
path("sms/", include("uzsms.urls"))
```

This resolves to `POST /sms/send/` (route name `send_sms`). Importing
`uzsms.urls` without DRF installed raises `SmsConfigurationError` naming the
`drf` extra.

**The endpoint requires authentication by default** (`SMS_SETTINGS["PERMISSION_CLASSES"]`
defaults to `["rest_framework.permissions.IsAuthenticated"]`). This is
deliberate: 1.0.1 shipped this endpoint as `AllowAny`, an open relay that let
anyone on the internet send SMS through your broker credentials. To relax
it, set `PERMISSION_CLASSES` explicitly, e.g. `["rest_framework.permissions.AllowAny"]`.
The endpoint is also throttled (`THROTTLE_RATE`, default `"20/min"`).

Request body:

```json
{"phone_number": "998901234567", "message": "hello there"}
```

### Response envelope

Every response — success or failure — has exactly the same three top-level
keys: `success`, `data`, `error`. Exactly one of `data`/`error` is non-null.
HTTP status codes stay meaningful (`201` created, `400` validation error,
`401`/`403` unauthenticated/forbidden, `429` throttled, `502` upstream
broker failure).

**Success** (`201 Created`):

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

`message_id` here is the client-generated correlation id (`SmsMessage.message_id`)
that identifies the request, not the broker's own id — it's assigned before
the send even happens. `log_id` is the `SmsLog` row's primary key, or `null`
when `LOG_MESSAGES` is `False`. `status` is `"sent"` or `"failed"`.

**Failure** (e.g. `400 Bad Request`, invalid phone number). `message` stays
the fixed string `"Invalid request."`; the field-level errors live in
`error.detail`, keyed by field name (DRF's own validation error shape):

```json
{
    "success": false,
    "data": null,
    "error": {
        "code": "validation_error",
        "message": "Invalid request.",
        "detail": {
            "phone_number": [
                "'12345' is not a valid Uzbek phone number; expected '998' followed by nine digits."
            ]
        }
    }
}
```

A broker/upstream failure (`502 Bad Gateway`) never forwards the broker's
raw response body to the caller — only the upstream status code:

```json
{
    "success": false,
    "data": null,
    "error": {
        "code": "provider_error",
        "message": "The SMS provider rejected the request.",
        "detail": {"status_code": 400}
    }
}
```

DRF's own authentication, permission, throttling, and parsing errors are
re-shaped into this same envelope rather than DRF's bare `{"detail": ...}`
shape.

## The `SmsLog` model

Every send (unless `LOG_MESSAGES` is `False`) writes a row to `SmsLog`
(table `SMS_smslog`, app label `SMS` — both pinned for backward
compatibility with 1.0.1's migration history location, even though the
Python package is now `uzsms`):

| Field | Type | Notes |
|---|---|---|
| `phone_number` | `CharField(35)` | |
| `text` | `TextField` | |
| `status` | `CharField`, choices | `"pending"`, `"sent"`, or `"failed"` — see `SmsLog.Status`. |
| `created_at` | `DateTimeField` | Auto-set on creation, indexed. |
| `sent_at` | `DateTimeField`, nullable | Set when marked sent. |
| `message_id` | `CharField(255)` | The client-generated correlation id (`SmsMessage.message_id`) that was sent on the wire. Only overwritten if the backend supplies its own `SendResult.provider_message_id` — no shipped backend currently does. Indexed. |
| `provider_response` | `JSONField`, nullable | The broker's raw response body, when available. |
| `error` | `TextField` | Error message on failure. |
| `is_active` | `BooleanField`, default `False` | **Deprecated**, mirrors `status == "sent"`. Scheduled for removal in 3.0 — read `status` instead. |

Rows go `pending` → `sent`/`failed`. A row that raises before the backend
even returns (e.g. a raised exception rather than a returned `SendResult`)
is still marked `failed`, with `error` set, rather than left `pending`
forever.

## Troubleshooting

- **`SmsConfigurationError: SMS_SETTINGS['URL'] is required but was not provided.`**
  You haven't set `SMS_SETTINGS["URL"]` (or the legacy `SMS_URL`). Same for
  `LOGIN`/`PASSWORD`.
- **`SmsConfigurationError: ... requires Django REST Framework ...`** when
  importing `uzsms.urls`. Install the `drf` extra.
- **`SmsConfigurationError: ... requires httpx ...`** when using
  `AsyncSmsClient`/`AsyncPlaymobileBackend`. Install the `async` extra.
- **`SmsConfigurationError: Celery is required ...`** when calling
  `send_sms_task`. Install the `celery` extra.
- **401/403 from the send endpoint.** Authentication is required by
  default; either authenticate the request or relax
  `SMS_SETTINGS["PERMISSION_CLASSES"]`.
- **429 from the send endpoint.** You've exceeded `SMS_SETTINGS["THROTTLE_RATE"]`.
- **A send raises instead of returning `ok=False`.** Set
  `SMS_SETTINGS["FAIL_SILENTLY"] = True` if you'd rather inspect
  `SendResult.ok`/`.error` than catch exceptions.
- **`SmsBackendError: ... returned N result(s) for M message(s)`.** A custom
  backend's `send_messages` didn't return exactly one `SendResult` per
  message it was given — fix the backend.

## Known limitations

These are accepted, deliberate tradeoffs, not oversights:

- **Partial-success responses are not detected.** `PlaymobileBackend`/`AsyncPlaymobileBackend`
  map a single HTTP outcome onto every message in a batch: if the broker
  returns HTTP 200 for the whole batch but silently rejects individual
  recipients within it, those messages are recorded as `sent` anyway. The
  broker does not document a per-message failure format within a 200
  response, so this can't currently be detected.
- **The cached async HTTP client has no event-loop affinity check.**
  `get_async_client()` in `uzsms/backends/playmobile.py` caches one
  `httpx.AsyncClient` per process, assuming a single, persistent event
  loop. Calling it from repeated separate `asyncio.run()` invocations in
  the same process can break the cached client.
- **Resetting `SMS_SETTINGS` leaks the async client.** `reset_async_client()`
  drops the cached `httpx.AsyncClient` reference without calling
  `aclose()` on it (closing it is an async operation, and the reset runs
  from a synchronous Django signal handler) — every `SMS_SETTINGS` change
  in a long-lived async process leaks one client.

  (Upgrading from 1.x and still calling the deprecated `SMS_Sender.create_sms_log`?
  See the `create_sms_log` row in `UPGRADE.md` → "Before / after" for a
  behavior change you need to know about.)

## License

MIT. See `LICENSE`.
