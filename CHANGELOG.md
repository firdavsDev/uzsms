# Changelog

All notable changes to this project are documented here.

## 2.0.0

A ground-up rewrite. See `UPGRADE.md` for the 1.x → 2.0 migration
procedure — **read it before running `migrate`**, the migration history
is data-destroying for existing installs.

**The PyPI distribution is renamed** from `django-sms-uz` to
`django-smsuz`. `pip install --upgrade` will not find 2.0; uninstall
`django-sms-uz` and install `django-smsuz` instead. The importable
package name (`uzsms`), the app label (`SMS`), the database table
(`SMS_smslog`) and the settings key (`SMS_SETTINGS`) are unaffected.
`django-sms-uz` stays on PyPI at 1.0.1 and receives no further releases.

### Added

- `SmsClient`/`AsyncSmsClient` (`uzsms/services.py`): the package's
  primary API, with `send(phone_number, text, *, message_id=None)` and
  `send_bulk(messages)`, validating every message and bulk-writing log
  rows in a constant number of queries.
- `AsyncSmsClient` and `AsyncPlaymobileBackend` for async/ASGI Django,
  behind the new `async` extra (`httpx`).
- Swappable SMS backends (`uzsms/backends/`): `PlaymobileBackend` /
  `AsyncPlaymobileBackend`, `ConsoleBackend`, `LocMemBackend` /
  `AsyncLocMemBackend`, `DummyBackend`, plus `BaseSmsBackend`/
  `BaseAsyncSmsBackend` for writing your own, selected via
  `SMS_SETTINGS["BACKEND"]`.
- `send_bulk()` for sending many messages in a single batched HTTP
  request.
- Value objects `SmsMessage` and `SendResult` (`uzsms/dto.py`), both
  frozen dataclasses; `SendResult` carries `log_id`.
- A dedicated exception hierarchy (`uzsms/exceptions.py`): `SmsError`,
  `SmsConfigurationError`, `SmsValidationError`, `SmsTransportError`,
  `SmsProviderError`, `SmsBackendError`.
- `SmsLogRecorder` (`uzsms/repository.py`), the single component
  permitted to write `SmsLog` rows, batching creates and updates.
- Configurable timeout, retry, backoff, and connection pooling
  (`SMS_SETTINGS["TIMEOUT"/"MAX_RETRIES"/"RETRY_BACKOFF"/"POOL_MAXSIZE"]`).
  Retries are safe because every message carries a stable `message_id`
  the broker deduplicates on replay.
- `SMS_SETTINGS["FAIL_SILENTLY"]`, `["MAX_MESSAGE_LENGTH"]`,
  `["PERMISSION_CLASSES"]`, `["THROTTLE_RATE"]`, and `["ASYNC_BACKEND"]`
  (the backend `AsyncSmsClient` resolves, kept separate from `["BACKEND"]`
  so `SmsClient` and `AsyncSmsClient` can both be constructed from one
  unmodified `SMS_SETTINGS` dict).
- Shared phone-number and message-text validators
  (`uzsms/validators.py`), used by both the client and the DRF
  serializer.
- A uniform HTTP API response envelope (`uzsms/api/responses.py`):
  every response, success or failure, is
  `{"success": bool, "data": object|null, "error": object|null}`, with
  meaningful HTTP status codes preserved (`201`/`400`/`401`/`403`/`429`/`502`).
  DRF's own auth/permission/throttling/parsing errors are re-shaped into
  the same envelope.
- Throttling on the send endpoint (`SMS_SETTINGS["THROTTLE_RATE"]`,
  default `20/min`).
- An optional Celery task, `uzsms.tasks.send_sms_task`
  (`django-smsuz[celery]`), with automatic retry on
  `SmsTransportError`.
- Backward-compatibility shims (`uzsms/compat.py`): `SMS_Sender`
  reproduces 1.0.1's constructor and method names, delegating to
  `SmsClient` internally, and emits a `DeprecationWarning` from every
  entry point.
- A defined public API surface: `uzsms/__init__.py` exposes
  `SmsClient`, `AsyncSmsClient`, `SmsMessage`, `SendResult`,
  `SmsLogRecorder`, `SMS_Sender`, `get_backend`, `get_async_backend`, and
  the exception hierarchy, resolved lazily so importing `uzsms` never
  requires `SMS_SETTINGS` to be configured.
- `SmsLog.status` (`pending`/`sent`/`failed`), replacing the old
  activity-only tracking.
- A CI workflow running the test suite and `ruff check` across the
  supported Python/Django matrix.

### Changed

- **Package renamed**: importable package is now `uzsms` (was the
  top-level `SMS` package). The Django app *label* stays pinned to
  `"SMS"` and the database table stays `SMS_smslog`, so existing
  `django_migrations` bookkeeping and table names are otherwise
  undisturbed by the rename itself.
- **HTTP API URL path changed**: the send route is now `send/` (was
  `send_sms/`) — with `path("sms/", include("uzsms.urls"))`, that's
  `/sms/send/` (was `/sms/send_sms/`). **Breaking.**
- **`send()` return type changed**: `SmsClient.send()` (and the
  `SMS_Sender` shim) now return `SendResult`, never a raw
  `requests.Response`.
- **Settings keys renamed**: `SMS_URL`/`SMS_LOGIN`/`SMS_PASSWORD` →
  `URL`/`LOGIN`/`PASSWORD` under `SMS_SETTINGS`. The old names still
  work (see Deprecated below).
- **`SmsLog.is_active` default changed** from `True` to `False`: a
  `pending` row must not claim delivery before it's actually sent.
- Migration history replaced by a single `0001_initial` (see the
  **Removed** section and `UPGRADE.md`).

### Fixed

Four performance defects present in 1.0.1's `SMS/sms_utils.py`, all in the
broker HTTP call:

- **No request timeout.** A stalled broker could pin a worker thread
  forever. Every request now passes `SMS_SETTINGS["TIMEOUT"]`.
- **A new TCP+TLS handshake per message, with no connection pooling.**
  Requests now go through a cached, pooled `requests.Session`
  (`SMS_SETTINGS["POOL_MAXSIZE"]`), or the async equivalent via
  `httpx.AsyncClient`.
- **One HTTP request per message instead of batching.** Every message
  passed to a single `send_bulk()`/`send_messages()` call is now sent
  as one `messages` array in a single HTTP request.
- **A hardcoded constant `message-id` sent for every message.** Each
  message's wire `message-id` is now its own `SmsMessage.message_id`
  (generated once per message), never a shared constant and never
  regenerated between retries.

Plus two correctness defects:

- **The old HTTP view crashed on every request.** `SMS/views.py` did
  `return Response(result)` where `result` was a raw `requests.Response`
  object, which DRF cannot serialize — every request to the send endpoint
  crashed. Every response body this API now returns is built by
  `uzsms/api/responses.py`, whose envelope contains only
  JSON-serializable values.
- A pending log row that raised during `send()` (rather than returning
  a normal failure result) is now marked `failed` with `error` set,
  instead of being left `pending` forever.

### Security

- **Open relay fixed.** The send endpoint was hardcoded `AllowAny`,
  letting anyone on the internet send SMS through the operator's
  broker credentials. It now requires authentication by default
  (`SMS_SETTINGS["PERMISSION_CLASSES"]`, default `IsAuthenticated`).
- **Unbounded message length fixed.** Outgoing message text is now
  validated against `SMS_SETTINGS["MAX_MESSAGE_LENGTH"]` (default 918
  characters) before being sent.
- **The broker's raw response body is no longer forwarded to API
  callers.** A provider failure (`502`) now returns only a generic
  message and the upstream status code; the broker's raw body
  (which can carry broker-internal error text, account, or routing
  details) is persisted to `SmsLog.provider_response` for operators to
  inspect, but never returned in the API response.
- **A transport failure no longer leaks the broker's hostname, port, or
  URL path.** A connection error's `str()` (e.g. from `requests`/`httpx`)
  routinely embeds that information; the send endpoint now returns a
  fixed, generic message on transport failure, matching the provider-error
  branch above. The real detail is still persisted to `SmsLog.error`.
- **A send that fails under `FAIL_SILENTLY=True` no longer reports
  success.** The backend RETURNS `SendResult(ok=False, ...)` instead of
  raising in that mode; the send endpoint now checks `result.ok` and
  returns `502`/`provider_error` instead of `201`/`success: true`.

### Deprecated

- `SMS_SETTINGS["SMS_URL"]`/`["SMS_LOGIN"]`/`["SMS_PASSWORD"]` — use
  `["URL"]`/`["LOGIN"]`/`["PASSWORD"]`. The legacy names still work and
  emit a `DeprecationWarning`.
- `uzsms.SMS_Sender` (and its `SendSmsOneContact`/`create_sms_log`
  methods) — use `uzsms.SmsClient`.
- `SmsLog.is_active` — read `SmsLog.status` instead. Scheduled for
  removal in 3.0.

### Removed

- The CodeQL GitHub Actions workflow (`.github/workflows/codeql-analysis.yml`).
- The old migration history (`0001_initial`, `0002_delete_smstoken`,
  `0003_remove_smslog_code`), replaced by a single, from-scratch
  `0001_initial`. **Data-destroying for existing installs** — see
  `UPGRADE.md`.
- `SMS/sms_utils.py` (and the rest of the top-level `SMS` Python
  package/module path). The Django app label `"SMS"` is kept for
  migration/table-name compatibility, but there is no longer a `SMS`
  Python package to import from — `from SMS.sms_utils import ...` now
  raises `ImportError`. Use `from uzsms import ...` instead (see
  `UPGRADE.md`).

### Known Limitations

- **Partial-success responses are not detected.** `PlaymobileBackend`/
  `AsyncPlaymobileBackend` map a single HTTP outcome onto every message in
  a batch: if the broker returns HTTP 200 for the whole batch but silently
  rejects individual recipients within it, those messages are recorded as
  `sent` anyway. The broker does not document a per-message failure format
  within a 200 response, so this can't currently be detected. See
  `README.md` → "Known limitations" for the full list.
