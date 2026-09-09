# Upgrading from 1.x to 2.0

## DATA LOSS WARNING — read this before running `migrate`

2.0 replaces the entire migration history with a single `0001_initial`
migration. It does **not** produce a schema-compatible upgrade path: the
new `0001_initial` creates the `SMS_smslog` table from scratch, with
columns 1.0.1's table doesn't have. Django will refuse to fake this — a
fresh `0001_initial` against an existing `SMS_smslog` table fails, because
the table already exists but its columns don't match.

**There is no in-place migration.** The `SMS_smslog` table (and, with it,
your entire sent-SMS log history) must be dropped and recreated. This is a
deliberate, accepted tradeoff by the package owner: the old migration
history (`0001_initial`, `0002_delete_smstoken`, `0003_remove_smslog_code`)
carried baggage (`SmsToken`, an unused `code` field) that wasn't worth
preserving into 2.0, and a from-scratch model was judged simpler and safer
than writing a multi-step schema migration to reshape the old table.

**If you have SMS log history you need to keep, export it before you do
anything else below.** After this procedure, it is gone.

### Upgrade procedure, in order

1. **Back up your database** (or at minimum, export the `SMS_smslog` table)
   before doing anything else.
2. Upgrade the package: `pip install --upgrade django-sms-uz` (add extras
   as needed — see `README.md`).
3. Update `INSTALLED_APPS` and any settings/imports per the table below.
4. Delete the old migration records for this app so Django doesn't think
   `0001_initial` (2.0's version) is already applied:
   ```sql
   DELETE FROM django_migrations WHERE app = 'SMS';
   ```
5. Drop the old table:
   ```sql
   DROP TABLE SMS_smslog;
   ```
6. Run migrations to create the new schema:
   ```bash
   python manage.py migrate
   ```

Your sent-SMS log history is lost as part of this procedure. This was a
deliberate decision by the package owner, made in favor of a clean,
from-scratch model over a compatibility-preserving multi-step migration.

## Before / after

| | 1.0.1 | 2.0 |
|---|---|---|
| `INSTALLED_APPS` entry | `"SMS"` | `"uzsms"` |
| Import path | `from SMS.sms_utils import SMS_Sender` | `from uzsms import SmsClient` (preferred) — the old names survive as deprecated shims, but **only under the new import path**: `from uzsms import SMS_Sender` also works, with a `DeprecationWarning`. The old `SMS.sms_utils` module path itself is gone — `from SMS.sms_utils import ...` now raises `ImportError`, because the top-level `SMS` Python package no longer exists at all (only the Django app *label* `"SMS"` was kept, for migration/table-name compatibility). |
| Sending a message | `SMS_Sender(phone, msg).SendSmsOneContact()` returning a raw `requests.Response` | `SmsClient().send(phone, msg)` returning a `SendResult` (a frozen dataclass: `ok`, `provider_message_id`, `status_code`, `raw`, `error`, `log_id`, ...) — never a `requests.Response`. |
| HTTP API authentication | Endpoint was `AllowAny` — anyone could send SMS through your broker credentials. | Endpoint requires authentication by default (`SMS_SETTINGS["PERMISSION_CLASSES"]` defaults to `IsAuthenticated`). Unauthenticated requests now get `401`/`403` instead of `201`. |
| HTTP API URL | `send_sms/` (e.g. `/sms/send_sms/`) | `send/` (e.g. `/sms/send/`). **Breaking** — update any client code, load balancer rules, or reverse-proxy config hardcoding the old path. |
| HTTP API response body | `{"...whatever requests.Response happened to serialize to, or a crash..."}` | Uniform envelope on every response: `{"success": bool, "data": {...} | null, "error": {"code", "message", "detail"} | null}`. See `README.md` → "Response envelope" for worked examples. |
| `SmsLog.is_active` | Default `True`; the field to check for delivery success. | **Deprecated**, default flipped to `False`. Read `status` (`"pending"`/`"sent"`/`"failed"`) instead — `is_active` still mirrors `status == "sent"` for now, but is scheduled for removal in 3.0. |
| Settings keys | `SMS_SETTINGS = {"SMS_URL": ..., "SMS_LOGIN": ..., "SMS_PASSWORD": ...}` | Renamed to `URL`/`LOGIN`/`PASSWORD`. **You do not have to change your settings file** — the legacy `SMS_URL`/`SMS_LOGIN`/`SMS_PASSWORD` names still work exactly as before, they just now emit a `DeprecationWarning` on each use. Rename them at your convenience. |

## New in 2.0 you may want to adopt

- `AsyncSmsClient` / `AsyncPlaymobileBackend` for async Django (`django-sms-uz[async]`).
- `send_bulk()` to send many messages in one batched HTTP request instead
  of one request per message.
- Swappable backends (`SMS_SETTINGS["BACKEND"]`) — `ConsoleBackend` and
  `LocMemBackend` for local development and tests, `DummyBackend` for a
  pure no-op.
- `uzsms.tasks.send_sms_task`, an optional Celery task with automatic
  retry on transport failures (`django-sms-uz[celery]`).
- Configurable timeout, retry, and connection pooling
  (`TIMEOUT`/`MAX_RETRIES`/`RETRY_BACKOFF`/`POOL_MAXSIZE`) — 1.0.1 had none
  of these and could hang a worker thread indefinitely on a stalled broker.

See `CHANGELOG.md` for the full list, and `README.md` for the complete
`SMS_SETTINGS` reference and usage examples.
