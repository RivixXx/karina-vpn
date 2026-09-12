# Readiness for payment integration

The current scope is the service with **manual administrator confirmation**.
No payment provider, production deployment, commit, push or customer migration
is included in this change.

## Implemented locally

- Orders retain purchased days, amount and tariff title.
- SQLite enforces one pending/paid order per Telegram user.
- Approval saves an absolute expiry and moves the order to paid before XUI writes.
- Primary and mobile expiries are verified before completion.
- Retries reuse the saved expiry. Conflicting external changes stop for review.
- An OS lock serializes payment/referral application across processes using the
  same database. A process exit releases it without expiring a dangerous lease.
- A completed order and its customer/referral delivery tasks commit together.
- New-request and cancellation messages are also durable.
- The delivery worker starts with the bot, retries failures and is cancelled on
  shutdown. It does not automatically approve or provision orders.
- /payments and the administrator menu expose pending/paid orders, operation
  targets, error types and a safe activation retry.
- Status checks/cancellation do not authenticate to XUI.
- Payment binding cannot overwrite another user's Telegram binding.
- The legacy paid-order adapter requires the same CustomerOrderService boundary.
- A read-only database audit and Linux CI checks are included.
- SQLite backups use verified snapshots instead of copying live database files;
  committed WAL records are included and existing backups cannot be overwritten.

## Before the first deployment

1. Complete the recorded checks on the final revision.
2. Create and verify a backup using the existing backup procedure. Keep databases
   and secrets outside Git. Test restoration on a separate machine/environment.
   Pause subscription mutations for consistency across the independent XUI/bot
   databases, and require the backup directory's COMPLETE marker.
3. On a copy of the billing database, run:
   `python -m src.payment_audit --db /path/to/copied-karina.db`.
4. Resolve duplicate active requests explicitly before schema migration. Startup
   refuses the unique index if duplicates remain; it never silently cancels them.
5. Review every legacy active order against actual payments and XUI. An old
   pending/paid row cannot prove whether the old code already extended XUI before
   crashing. Do not approve such a row blindly.
6. Validate a separate test Telegram bot/XUI setup with synthetic customers:
   new purchase, renewal, tariff change/cancellation, blocked messages, referral,
   restart during activation, primary/mobile partial failure, and unavailable XUI.
7. Confirm SUPPORT_URL reaches the operator: payments are arranged manually.
8. Deploy a reviewed clean revision through the existing deployment procedure.
   Check bot/notifier state, logs and /payments after startup.

## Recovery

- Pending: check actual receipt of payment, then approve or reject.
- Paid: payment was accepted; activation is incomplete. Open the order in
  /payments and retry after resolving the reported issue. Cancellation is blocked.
- Completed: entitlement is recorded. Message/bonus failures retry independently;
  never extend again merely because a receipt was not delivered.
- Changed expiry, missing credential, inconsistent bundle, conflicting binding:
  compare saved intent to the actual account using existing read-only CLI tools.
  Repair through the existing reviewed administration/migration tools, then retry.
  Do not delete/recreate a customer or edit the order to pending to clear an error.
- Avoid concurrent manual CLI/panel changes to an account with an active payment
  intent. The application lock coordinates payment/referral workers using this DB;
  it cannot lock independent writes made directly in the external XUI panel.
- Delivery claims are retried after five minutes if the worker crashes. Normal
  failures back off from 30 seconds to one hour. /payments shows queue/error counts;
  the read-only audit reports incomplete operations and undelivered effects.
- Telegram has no message idempotency key. A crash after Telegram accepts a message
  but before the local delivery commit can duplicate a notification. Receipts
  include the order ID; subscription days are not applied again.
- Historic completed orders are not bulk-notified or retroactively processed by
  the new queue. Review legacy missing rewards/notifications separately.

## Rollback

Code rollback is not a database rollback. Keep the new tables and the backup.
Do not return to the older relative-extension payment implementation while saved
payment operations remain unresolved. Reconcile those operations first or deploy
a forward fix. Never restore an old billing DB over newer successful payments.

## Payment provider boundary

A future adapter must verify webhook authenticity, merchant, amount, currency and
the captured order identity, persist the verified provider reference atomically,
then use CustomerOrderService. It must add replay tests and refund/reconciliation
policy. No user button is evidence of payment.

## Validation

Verified locally on 2026-09-13 with Python 3.12.12 on Windows:

- Full isolated pytest suite: **507 passed** (49.33 seconds).
- Python compilation: passed.
- Syntax check of every shell script: passed (also covered by deployment tests).
- Git diff whitespace check: passed.
- Synthetic SQLite WAL snapshot, restore/open and integrity tests: passed.

Live Telegram/XUI integration, restoration of actual production backups, Linux
CI execution and production rollout have not been performed. They are separate
release gates, not implied by passing isolated tests. No commit or push performed;
avatar assets remain untracked.
