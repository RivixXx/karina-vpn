import math
from dataclasses import dataclass


DAY_MS = 86_400_000
TARGET_CLIENTS = ("Vlad__K", "Nikolay_p", "Olga_K", "Sergey_B")
EXCLUDED_CLIENTS = ("Mikhail", "Home", "Anastasia_A")
PRIMARY_HWID_LIMIT = 4
PRIMARY_INBOUND_IDS = (2, 3, 4)
MOBILE_INBOUND_IDS = (5, 10)
MIGRATION_EVENT_KEY = "migration:primary-mobile:v1"
MIGRATION_EVENT = 1


@dataclass(frozen=True)
class CohortClientReport:
    email: str
    primary_inbounds: tuple[int, ...]
    mobile_inbounds: tuple[int, ...]
    expiry_time_ms: int
    telegram_id: int | None
    device_count: int | None
    device_names: tuple[str, ...]
    actions: tuple[str, ...]
    notify: bool
    blocking_errors: tuple[str, ...]


def _device_snapshot(service, email):
    try:
        devices = service.get_devices(email)
    except Exception:
        return None, ()
    names = []
    for device in devices:
        value = (getattr(device, "model", "") or getattr(device, "os_name", "")).strip()
        if value and value not in names:
            names.append(value)
    return len(devices), tuple(names)


def inspect_client(service, email, binding_lookup, notification_sent=None):
    primary = service.get_client(email)
    plan = service.plan_mobile_migration(email)
    binding = binding_lookup(email)
    telegram_id = binding["tg_id"] if binding else None
    count, names = _device_snapshot(service, email) if primary else (None, ())
    actions = []
    if plan.needs_mobile_create:
        actions.append("create mobile credential on inbounds 5,10")
    if plan.needs_mobile_quota_fix:
        actions.append("set mobile traffic limit to 50 GiB")
    if plan.needs_expiry_sync:
        actions.append("synchronize mobile expiry with primary")
    if plan.needs_hwid_sync:
        actions.append("set mobile HWID limit to 0")
    if plan.needs_external_link_update:
        actions.append("create or repair managed karina-mobile subscription")
    if plan.needs_primary_detach:
        actions.append("detach mobile inbounds from primary")
    if primary and primary.device_limit != PRIMARY_HWID_LIMIT:
        actions.append("set primary HWID limit to 4")
    if not actions and not plan.blocking_errors:
        actions.append("no changes required")
    config_errors = []
    config = getattr(service, "config", None)
    if config is not None:
        primary_config = tuple(config.primary_inbound_ids or config.inbound_ids)
        mobile_config = tuple(config.mobile_inbound_ids or (config.mobile_inbound_id,))
        if primary_config != PRIMARY_INBOUND_IDS:
            config_errors.append("configured primary inbounds are not 2,3,4")
        if mobile_config != MOBILE_INBOUND_IDS:
            config_errors.append("configured mobile inbounds are not 5,10")
    already_notified = bool(
        telegram_id is not None and notification_sent
        and notification_sent(email, MIGRATION_EVENT_KEY, MIGRATION_EVENT)
    )
    return CohortClientReport(
        email=email,
        primary_inbounds=primary.inbound_ids if primary else (),
        mobile_inbounds=tuple(plan.mobile_inbound_ids),
        expiry_time_ms=primary.expiry_time_ms if primary else 0,
        telegram_id=telegram_id,
        device_count=count,
        device_names=names,
        actions=tuple(actions),
        notify=(telegram_id is not None and not already_notified
                and not plan.blocking_errors and not config_errors),
        blocking_errors=tuple(plan.blocking_errors) + tuple(config_errors),
    )


def build_dry_run(service, binding_lookup, notification_sent=None):
    reports = tuple(inspect_client(service, email, binding_lookup, notification_sent)
                    for email in TARGET_CLIENTS)
    return reports


def _expiry_remaining(expiry_time_ms, now_ms):
    if not expiry_time_ms:
        return "без ограничений"
    return f"{max(0, math.ceil((expiry_time_ms - now_ms) / DAY_MS))} дней"


def migration_message(primary, device_count, device_names, now_ms):
    count = "недоступно" if device_count is None else str(device_count)
    lines = [
        "✅ Ваша конфигурация Карина VPN обновлена",
        "",
        "Мы обновили набор подключений в вашей подписке.",
        "Ничего переустанавливать не нужно — обновите подписку в приложении, "
        "если новые подключения не появились автоматически.",
        "",
        f"До окончания подписки: {_expiry_remaining(primary.expiry_time_ms, now_ms)}",
        "",
        "Устройства:",
        f"Использовано устройств: {count} из {PRIMARY_HWID_LIMIT}",
    ]
    lines.extend(f"- {name}" for name in device_names)
    return "\n".join(lines)


def format_dry_run(reports):
    lines = ["Karina VPN — primary/mobile cohort migration", "Mode: DRY RUN", ""]
    for email in EXCLUDED_CLIENTS:
        lines.extend((f"{email}: EXCLUDED / untouched", ""))
    for report in reports:
        expiry = str(report.expiry_time_ms) if report.expiry_time_ms else "unlimited/missing"
        devices = "unavailable" if report.device_count is None else str(report.device_count)
        binding = str(report.telegram_id) if report.telegram_id else "not linked"
        lines.extend((
            f"{report.email}:",
            f"  Current primary inbounds: {','.join(map(str, report.primary_inbounds)) or '-'}",
            f"  Current mobile inbounds: {','.join(map(str, report.mobile_inbounds)) or '-'}",
            f"  Primary expiry (ms): {expiry}",
            f"  Telegram binding: {binding}",
            f"  Primary HWID usage: {devices} / {PRIMARY_HWID_LIMIT}",
            "  Planned actions:",
            *(f"    - {action}" for action in report.actions),
            f"  Notification after success: {'YES' if report.notify else 'NO'}",
            *(f"  BLOCKING: {error}" for error in report.blocking_errors),
            "",
        ))
    lines.append("No changes performed.")
    return "\n".join(lines)


async def apply_cohort(service, binding_lookup, sender, event_sent, mark_sent,
                       now_provider):
    results = []
    for email in TARGET_CLIENTS:
        report = inspect_client(service, email, binding_lookup)
        if report.blocking_errors:
            results.append((email, "blocked"))
            continue
        service.migrate_client_to_mobile_bundle(email)
        primary = service.get_client(email)
        if primary.device_limit != PRIMARY_HWID_LIMIT:
            service.set_hwid_limit(email, PRIMARY_HWID_LIMIT)
        verified_plan = service.plan_mobile_migration(email)
        verified_primary = service.get_client(email)
        if (verified_plan.blocking_errors or not verified_plan.already_migrated
                or verified_primary.device_limit != PRIMARY_HWID_LIMIT):
            results.append((email, "reconciliation_required"))
            continue
        binding = binding_lookup(email)
        if binding and not event_sent(email, MIGRATION_EVENT_KEY, MIGRATION_EVENT):
            count, names = _device_snapshot(service, email)
            now_ms = int(now_provider() * 1000)
            await sender(binding["tg_id"], migration_message(
                verified_primary, count, names, now_ms,
            ))
            mark_sent(email, MIGRATION_EVENT_KEY, MIGRATION_EVENT)
        results.append((email, "complete"))
    return tuple(results)
