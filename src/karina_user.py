#!/usr/bin/env python3

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    from .application import build_client_service
    from .app_config import ConfigError
    from .integrations.xui import XUIClient, XUIError
    from .services import ClientService, ClientServiceError
except ImportError:  # Direct execution from the src directory.
    from application import build_client_service
    from app_config import ConfigError
    from integrations.xui import XUIClient, XUIError
    from services import ClientService, ClientServiceError

CONFIG = Path("/etc/karina-vpn/config.env")
CONNECT_DIR = Path("/var/www/karina/connect")


def die(message, code=1):
    print(f"Ошибка: {message}", file=sys.stderr)
    sys.exit(code)


def fmt_date(ms):
    if not ms:
        return "Без срока"
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y %H:%M")


def fmt_date_short(ms):
    if not ms:
        return "∞"
    return datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y")


def bytes_to_gb(value):
    if not value:
        return "∞"
    return f"{value / 1024 / 1024 / 1024:.1f} ГБ"


def bytes_to_human(value):
    if not value:
        return "0 Б"
    value = float(value)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if value < 1024 or unit == "ТБ":
            return f"{int(value)} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"


def status_text(status):
    return {"active": "🟢 Активен", "expired": "🟠 Истёк", "disabled": "🔴 Отключён"}[status]


def build_service(config_path=CONFIG):
    return build_client_service(
        config_path,
        connect_dir=CONNECT_DIR,
    )


def parse_int(value, message):
    try:
        return int(value)
    except ValueError:
        die(message)


def parse_float(value, message):
    try:
        return float(value)
    except ValueError:
        die(message)


def cmd_create(args):
    if len(args) not in (1, 2, 3, 4):
        die("использование: karina-user create ИМЯ [ДНИ=30] [HWID=2] [ТРАФИК_ГБ=0]")
    days = parse_int(args[1], "дни, HWID и трафик должны быть числами") if len(args) >= 2 else 30
    hwid = parse_int(args[2], "дни, HWID и трафик должны быть числами") if len(args) >= 3 else None
    traffic = parse_float(args[3], "дни, HWID и трафик должны быть числами") if len(args) >= 4 else 0
    result = build_service().create_client(args[0], days, hwid, traffic)
    client = result.client
    print("\n💗 Карина VPN\n")
    print(f"Пользователь: {client.email}")
    print("Статус:       Активен")
    print(f"Срок:         {fmt_date(client.expiry_time_ms)}")
    print(f"HWID:         0 / {client.device_limit}")
    print(f"Трафик:       {bytes_to_gb(client.total_traffic_bytes)}")
    print(f"Серверов:     {len(client.inbound_ids)}\n")
    if result.issue_warning:
        print("Клиент создан, но генерация Crypt5/QR завершилась ошибкой:", file=sys.stderr)
        print(result.issue_warning, file=sys.stderr)
    if result.subscription_page:
        print("Страница подключения:")
        print(result.subscription_page)


def cmd_info(args):
    if len(args) != 1:
        die("использование: karina-user info ИМЯ")
    client = build_service().get_client(args[0])
    if not client:
        die(f"клиент {args[0].strip()} не найден")
    limit = client.device_limit if client.device_limit else "∞"
    print("\n💗 Карина VPN\n")
    print(f"Пользователь: {client.email}")
    print(f"Статус:       {status_text(client.status)}")
    print(f"Истекает:     {fmt_date(client.expiry_time_ms)}")
    print(f"Устройства:   {client.device_count} / {limit}")
    print(f"Использовано: {bytes_to_human(client.used_traffic_bytes)}")
    print(f"Лимит:        {bytes_to_gb(client.total_traffic_bytes)}")
    print(f"Серверы:      {list(client.inbound_ids)}\n")
    print("Страница подключения:")
    print(client.connect_url)


def cmd_list(args):
    if args:
        die("использование: karina-user list")
    rows = build_service().list_clients()
    print("\n💗 Карина VPN — пользователи\n")
    if not rows:
        print("Пользователей нет.")
        return
    print(f"{'Имя':<18}  {'Статус':<17}  {'HWID':<9}  {'Трафик':<14}  {'Лимит':<12}  {'До':<12}")
    print("─" * 92)
    counts = {"active": 0, "expired": 0, "disabled": 0}
    for client in rows:
        counts[client.status] += 1
        status = status_text(client.status)
        devices = f"{client.device_count}/{client.device_limit or '∞'}"
        print(f"{client.email:<18}  {status:<17}  {devices:<9}  "
              f"{bytes_to_human(client.used_traffic_bytes):<14}  "
              f"{bytes_to_gb(client.total_traffic_bytes):<12}  "
              f"{fmt_date_short(client.expiry_time_ms):<12}")
    print(f"\nВсего:      {len(rows)}")
    print(f"Активных:   {counts['active']}")
    print(f"Истекло:    {counts['expired']}")
    print(f"Отключено:  {counts['disabled']}")


def cmd_expiring(args):
    if len(args) != 1:
        die("использование: karina-user expiring ДНИ")
    days = parse_int(args[0], "количество дней должно быть числом")
    rows = build_service().get_expiring(days)
    print(f"\n⏰ Подписки, заканчивающиеся за {days} дн.\n")
    if not rows:
        print("Таких подписок нет.")
        return
    for client in rows:
        print(f"{client.email:<20} {fmt_date(client.expiry_time_ms)}   "
              f"осталось {client.days_remaining:.1f} дн.")


def cmd_traffic(args):
    if len(args) != 1:
        die("использование: karina-user traffic ИМЯ")
    traffic = build_service().get_traffic(args[0])
    print(f"\n📊 Трафик {args[0].strip()}\n")
    print(f"Использовано: {bytes_to_human(traffic.used_bytes)}")
    if traffic.limit_bytes:
        print(f"Лимит:        {bytes_to_human(traffic.limit_bytes)}")
        print(f"Осталось:     {bytes_to_human(traffic.remaining_bytes)}")
        print(f"Использовано: {traffic.percent_used:.1f}%")
    else:
        print("Лимит:        Безлимит")


def cmd_set_traffic(args):
    if len(args) != 2:
        die("использование: karina-user set-traffic ИМЯ ГБ")
    gb = parse_float(args[1], "лимит трафика должен быть числом")
    client = build_service().set_traffic_limit(args[0], gb)
    if client.total_traffic_bytes:
        print(f"{client.email}: лимит установлен {gb:g} ГБ")
    else:
        print(f"{client.email}: установлен безлимитный трафик")


def cmd_set_hwid(args):
    if len(args) != 2:
        die("использование: karina-user set-hwid ИМЯ КОЛИЧЕСТВО")
    limit = parse_int(args[1], "лимит HWID должен быть числом")
    client = build_service().set_hwid_limit(args[0], limit)
    value = client.device_limit if client.device_limit else "без ограничений"
    print(f"{client.email}: лимит HWID установлен {value}")


def cmd_devices(args):
    if len(args) != 1:
        die("использование: karina-user devices ИМЯ")
    service = build_service()
    client = service.get_client(args[0])
    if not client:
        die(f"клиент {args[0].strip()} не найден")
    devices = service.get_devices(args[0])
    limit = client.device_limit if client.device_limit else "∞"
    print(f"\n📱 Устройства {client.email}")
    print(f"Использовано: {len(devices)} / {limit}\n")
    if not devices:
        print("Зарегистрированных устройств нет.")
        return
    for device in devices:
        print(f"ID:         {device.id}")
        print(f"Устройство: {device.model or 'Неизвестное устройство'}")
        print(f"ОС:         {(device.os_name or 'Неизвестная ОС') + (' ' + device.os_version if device.os_version else '')}")
        print(f"Клиент:     {device.user_agent}")
        if device.first_seen_ms:
            print(f"Первый вход: {fmt_date(device.first_seen_ms)}")
        if device.last_seen_ms:
            print(f"Последний:   {fmt_date(device.last_seen_ms)}")
        print()


def cmd_device_remove(args):
    if len(args) != 2:
        die("использование: karina-user device-remove ИМЯ ID")
    device_id = parse_int(args[1], "ID устройства должен быть числом")
    build_service().remove_device(args[0], device_id)
    print(f"{args[0].strip()}: устройство ID {device_id} удалено")


def cmd_devices_reset(args):
    if len(args) != 1:
        die("использование: karina-user devices-reset ИМЯ")
    email = args[0].strip()
    service = build_service()
    if service.get_client(email) is None:
        die(f"клиент {email} не найден")
    if input(f"Сбросить ВСЕ устройства клиента {email}? Напиши YES: ").strip() != "YES":
        print("Отменено.")
        return
    service.reset_devices(email)
    print(f"{email}: все HWID сброшены")


def cmd_extend(args):
    if len(args) != 2:
        die("использование: karina-user extend ИМЯ ДНИ")
    days = parse_int(args[1], "количество дней должно быть числом")
    client = build_service().extend_client(args[0], days)
    print(f"{client.email}: срок продлён на {days} дней до {fmt_date(client.expiry_time_ms)}")


def cmd_disable(args):
    if len(args) != 1:
        die("использование: karina-user disable ИМЯ")
    print(f"{build_service().disable_client(args[0]).email}: отключён")


def cmd_enable(args):
    if len(args) != 1:
        die("использование: karina-user enable ИМЯ")
    print(f"{build_service().enable_client(args[0]).email}: включён")


def delete_client_impl(email):
    result = build_service().delete_client(email)
    warnings = [result.file_cleanup_warning] if result.file_cleanup_warning else []
    return {"vpn_deleted": result.removed, "warnings": warnings}


def cmd_delete(args):
    if len(args) != 1:
        die("использование: karina-user delete ИМЯ")
    email = ClientService.validate_email(args[0])
    if input(f"Удалить клиента {email} полностью? Напиши YES: ").strip() != "YES":
        print("Отменено.")
        return
    result = delete_client_impl(email)
    print(f"{email}: удалён")
    for warning in result["warnings"]:
        print(f"Warning: {warning}", file=sys.stderr)


def cmd_delete_confirmed(args):
    if len(args) != 1:
        die("usage: karina-user delete-confirmed EMAIL")
    print(json.dumps(delete_client_impl(ClientService.validate_email(args[0]))))


def format_mobile_migration_plan(plan):
    lines = ["Karina VPN — Mobile migration dry-run", "", f"User: {plan.primary_email}",
             f"Primary exists: {plan.primary_exists}",
             f"Primary inbound: {','.join(map(str, plan.primary_inbound_ids)) or '-'}",
             f"Mobile credential: {plan.mobile_email}", f"Mobile exists: {plan.mobile_exists}",
             f"Mobile inbound: {','.join(map(str, plan.mobile_inbound_ids)) or '-'}",
             f"Mobile quota bytes: {plan.mobile_total_bytes if plan.mobile_total_bytes is not None else '-'}",
             f"Mobile subscription configured: {plan.mobile_subscription_url_present}", "", "Plan:"]
    flags = ((plan.needs_mobile_create, "[CREATE] mobile credential"),
             (plan.needs_mobile_quota_fix, "[FIX] mobile quota"),
             (plan.needs_expiry_sync, "[SYNC] absolute expiry"),
             (plan.needs_hwid_sync, "[SYNC] HWID policy"),
             (plan.needs_external_link_update, "[LINK+VERIFY] mobile subscription"),
             (plan.needs_primary_detach, "[DETACH] mobile inbound from primary"))
    lines.extend((f"  {text}" for needed, text in flags if needed))
    if not any(needed for needed, _ in flags):
        lines.append("  no changes required")
    if plan.already_migrated:
        lines += ["", "Status: ALREADY MIGRATED"]
    lines += [*(f"Warning: {item}" for item in plan.warnings),
              *(f"BLOCKING: {item}" for item in plan.blocking_errors)]
    return "\n".join(lines)


def cmd_migrate_mobile(args):
    if not 1 <= len(args) <= 2 or (len(args) == 2 and args[1] not in {"--dry-run", "--apply"}):
        die("usage: karina-user migrate-mobile EMAIL [--dry-run|--apply]")
    service = build_service()
    plan = service.plan_mobile_migration(args[0])
    print(format_mobile_migration_plan(plan))
    if plan.blocking_errors:
        raise ClientServiceError("migration blocked by pre-flight checks")
    if len(args) < 2 or args[1] == "--dry-run":
        print("\nNo changes performed.")
        return
    service.migrate_client_to_mobile_bundle(args[0])
    print("\nMigration applied. Run dry-run again to verify ALREADY MIGRATED.")


def usage():
    print(
        """
Карина VPN — управление пользователями

Основные команды:

  karina-user create ИМЯ [ДНИ] [HWID] [ТРАФИК_ГБ]
  karina-user info ИМЯ
  karina-user list
  karina-user expiring ДНИ

Трафик:

  karina-user traffic ИМЯ
  karina-user set-traffic ИМЯ ГБ

HWID / устройства:

  karina-user devices ИМЯ
  karina-user set-hwid ИМЯ КОЛИЧЕСТВО
  karina-user device-remove ИМЯ ID
  karina-user devices-reset ИМЯ

Управление:

  karina-user extend ИМЯ ДНИ
  karina-user disable ИМЯ
  karina-user enable ИМЯ
  karina-user delete ИМЯ

Примеры:

  karina-user create Ivan 30 2
  karina-user create Ivan 30 2 100
  karina-user list
  karina-user expiring 7
  karina-user info Ivan
  karina-user traffic Ivan
  karina-user set-traffic Ivan 100
  karina-user set-traffic Ivan 0
  karina-user set-hwid Ivan 2
  karina-user devices Ivan
  karina-user extend Ivan 30
""".strip()
    )


def main():
    if len(sys.argv) < 2:
        usage()
        return
    commands = {
        "create": cmd_create, "info": cmd_info, "list": cmd_list,
        "expiring": cmd_expiring, "traffic": cmd_traffic,
        "set-traffic": cmd_set_traffic, "set-hwid": cmd_set_hwid,
        "devices": cmd_devices, "device-remove": cmd_device_remove,
        "devices-reset": cmd_devices_reset, "extend": cmd_extend,
        "disable": cmd_disable, "enable": cmd_enable, "delete": cmd_delete,
        "delete-confirmed": cmd_delete_confirmed,
        "migrate-mobile": cmd_migrate_mobile,
    }
    handler = commands.get(sys.argv[1])
    if not handler:
        usage()
        sys.exit(1)
    try:
        handler(sys.argv[2:])
    except (ConfigError, XUIError, ClientServiceError) as exc:
        die(str(exc))


if __name__ == "__main__":
    main()
