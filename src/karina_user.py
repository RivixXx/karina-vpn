#!/usr/bin/env python3

import json
import re
import secrets
import string
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    from .app_config import ConfigError, KarinaConfig, load_config
    from .integrations.xui import XUIClient, XUIError
except ImportError:  # Direct execution from the src directory.
    from app_config import ConfigError, KarinaConfig, load_config
    from integrations.xui import XUIClient, XUIError

CONFIG = Path("/etc/karina-vpn/config.env")
CONNECT_DIR = Path("/var/www/karina/connect")


def die(message, code=1):
    print(f"Ошибка: {message}", file=sys.stderr)
    sys.exit(code)


def create_api(config=None):
    config = config or load_config(CONFIG)
    api = XUIClient(config)
    api.login()
    return config, api


def normalize_email(value):
    value = value.strip()

    if not re.fullmatch(
        r"[A-Za-z0-9_.-]{2,64}",
        value,
    ):
        die(
            "имя клиента может содержать только "
            "A-Z, a-z, 0-9, _, -, . "
            "и быть длиной 2–64 символа"
        )

    return value


def random_subid(length=16):
    alphabet = string.ascii_lowercase + string.digits
    return "".join(
        secrets.choice(alphabet)
        for _ in range(length)
    )


def now_ms():
    return int(datetime.now().timestamp() * 1000)


def expiry_ms(days):
    return int(
        (
            datetime.now()
            + timedelta(days=days)
        ).timestamp()
        * 1000
    )


def fmt_date(ms):
    if not ms:
        return "Без срока"

    return datetime.fromtimestamp(
        ms / 1000
    ).strftime("%d.%m.%Y %H:%M")


def fmt_date_short(ms):
    if not ms:
        return "∞"

    return datetime.fromtimestamp(
        ms / 1000
    ).strftime("%d.%m.%Y")


def bytes_to_gb(value):
    if not value:
        return "∞"

    return f"{value / 1024 / 1024 / 1024:.1f} ГБ"


def bytes_to_human(value):
    if not value:
        return "0 Б"

    value = float(value)

    units = [
        "Б",
        "КБ",
        "МБ",
        "ГБ",
        "ТБ",
    ]

    for unit in units:
        if value < 1024 or unit == "ТБ":
            if unit == "Б":
                return f"{int(value)} {unit}"

            return f"{value:.1f} {unit}"

        value /= 1024

    return f"{value:.1f} ТБ"


def traffic_limit_bytes(gb):
    if gb <= 0:
        return 0

    return int(
        gb
        * 1024
        * 1024
        * 1024
    )


def get_status(client):
    if not client.get("enable", False):
        return "🔴 Отключён"

    expiry = client.get("expiryTime", 0)

    if expiry and expiry < now_ms():
        return "🟠 Истёк"

    return "🟢 Активен"


def issue_page(sub_id, config):
    url = f"{config.sub_base}/{sub_id}"

    proc = subprocess.run(
        [
            "/usr/local/bin/karina-issue",
            url,
        ],
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        print(
            "Клиент создан, но генерация "
            "Crypt5/QR завершилась ошибкой:",
            file=sys.stderr,
        )
        print(
            proc.stderr or proc.stdout,
            file=sys.stderr,
        )
        return None

    return f"{config.connect_base}/{sub_id}.html"


def collect_client_names(api):
    names = set()

    for inbound in api.list_inbounds():
        settings = inbound.get(
            "settings",
            {},
        )

        clients = settings.get(
            "clients",
            [],
        )

        for client in clients:
            email = client.get("email")

            if email:
                names.add(email)

    return sorted(
        names,
        key=str.lower,
    )


def get_client_full(api, email):
    obj = api.get_client(email)

    if not obj:
        return None

    client = obj.get(
        "client",
        {},
    )

    try:
        devices = api.get_hwids(email)
    except XUIError:
        devices = []

    return {
        "obj": obj,
        "client": client,
        "devices": devices,
    }


def cmd_create(args):
    if len(args) not in (1, 2, 3, 4):
        die(
            "использование: "
            "karina-user create ИМЯ "
            "[ДНИ=30] [HWID=2] [ТРАФИК_ГБ=0]"
        )

    email = normalize_email(args[0])
    config = load_config(CONFIG)

    try:
        days = (
            int(args[1])
            if len(args) >= 2
            else 30
        )

        hwid = (
            int(args[2])
            if len(args) >= 3
            else config.default_hwid_limit
        )

        traffic_gb = (
            float(args[3])
            if len(args) >= 4
            else 0
        )
    except ValueError:
        die("дни, HWID и трафик должны быть числами")

    if days <= 0:
        die("количество дней должно быть больше 0")

    if hwid < 0:
        die("HWID не может быть отрицательным")

    if traffic_gb < 0:
        die("трафик не может быть отрицательным")

    config, api = create_api(config)

    if api.get_client(email):
        die(f"клиент {email} уже существует")

    payload = {
        "client": {
            "email": email,
            "subId": random_subid(),
            "expiryTime": expiry_ms(days),
            "totalGB": traffic_limit_bytes(
                traffic_gb
            ),
            "limitIp": 0,
            "limitHwid": hwid,
            "enable": True,
            "tgId": 0,
            "flow": "",
            "security": "auto",
            "group": "",
            "comment": "",
            "reset": 0,
            "resetDay": 0,
            "resetMax": 0,
            "trafficReset": "never",
            "trafficResetDay": 1,
        },
        "inboundIds": list(config.inbound_ids),
    }

    api.create_client(payload)

    created = api.get_client(email)

    if not created:
        die("клиент не найден после создания")

    client = created["client"]
    page = issue_page(client["subId"], config)

    print()
    print("💗 Карина VPN")
    print()
    print(
        f"Пользователь: {client['email']}"
    )
    print("Статус:       Активен")
    print(
        f"Срок:         "
        f"{fmt_date(client['expiryTime'])}"
    )
    print(
        f"HWID:         "
        f"0 / {client['limitHwid']}"
    )
    print(
        f"Трафик:       "
        f"{bytes_to_gb(client['totalGB'])}"
    )
    print(
        f"Серверов:     "
        f"{len(created.get('inboundIds', []))}"
    )
    print()

    if page:
        print("Страница подключения:")
        print(page)


def cmd_info(args):
    if len(args) != 1:
        die(
            "использование: "
            "karina-user info ИМЯ"
        )

    email = normalize_email(args[0])

    config, api = create_api()

    full = get_client_full(
        api,
        email,
    )

    if not full:
        die(f"клиент {email} не найден")

    obj = full["obj"]
    client = full["client"]
    devices = full["devices"]

    used = obj.get(
        "usedTraffic",
        0,
    )

    print()
    print("💗 Карина VPN")
    print()
    print(
        f"Пользователь: {client['email']}"
    )
    print(
        f"Статус:       {get_status(client)}"
    )
    print(
        f"Истекает:     "
        f"{fmt_date(client['expiryTime'])}"
    )
    print(
        f"Устройства:   "
        f"{len(devices)} / "
        f"{client['limitHwid'] if client['limitHwid'] else '∞'}"
    )
    print(
        f"Использовано: {bytes_to_human(used)}"
    )
    print(
        f"Лимит:        "
        f"{bytes_to_gb(client['totalGB'])}"
    )
    print(
        f"Серверы:      "
        f"{obj.get('inboundIds', [])}"
    )
    print()
    print("Страница подключения:")
    print(
        f"{config.connect_base}/"
        f"{client['subId']}.html"
    )


def cmd_list(args):
    if args:
        die(
            "использование: "
            "karina-user list"
        )

    config, api = create_api()

    names = collect_client_names(api)

    rows = []

    for email in names:
        full = get_client_full(
            api,
            email,
        )

        if not full:
            continue

        obj = full["obj"]
        client = full["client"]
        devices = full["devices"]

        rows.append(
            {
                "email": email,
                "status": get_status(client),
                "devices": (
                    f"{len(devices)}/"
                    f"{client['limitHwid'] if client['limitHwid'] else '∞'}"
                ),
                "used": bytes_to_human(
                    obj.get(
                        "usedTraffic",
                        0,
                    )
                ),
                "limit": bytes_to_gb(
                    client.get(
                        "totalGB",
                        0,
                    )
                ),
                "expiry": fmt_date_short(
                    client.get(
                        "expiryTime",
                        0,
                    )
                ),
            }
        )

    print()
    print("💗 Карина VPN — пользователи")
    print()

    if not rows:
        print("Пользователей нет.")
        return

    print(
        f"{'Имя':<18}  "
        f"{'Статус':<17}  "
        f"{'HWID':<9}  "
        f"{'Трафик':<14}  "
        f"{'Лимит':<12}  "
        f"{'До':<12}"
    )

    print("─" * 92)

    active = 0
    expired = 0
    disabled = 0

    for row in rows:
        status = row["status"]

        if "Активен" in status:
            active += 1
        elif "Истёк" in status:
            expired += 1
        elif "Отключён" in status:
            disabled += 1

        print(
            f"{row['email']:<18}  "
            f"{status:<17}  "
            f"{row['devices']:<9}  "
            f"{row['used']:<14}  "
            f"{row['limit']:<12}  "
            f"{row['expiry']:<12}"
        )

    print()
    print(f"Всего:      {len(rows)}")
    print(f"Активных:   {active}")
    print(f"Истекло:    {expired}")
    print(f"Отключено:  {disabled}")


def cmd_expiring(args):
    if len(args) != 1:
        die(
            "использование: "
            "karina-user expiring ДНИ"
        )

    try:
        days = int(args[0])
    except ValueError:
        die("количество дней должно быть числом")

    if days < 0:
        die("количество дней не может быть отрицательным")

    config, api = create_api()

    names = collect_client_names(api)

    current = now_ms()
    limit = (
        current
        + days
        * 24
        * 60
        * 60
        * 1000
    )

    found = []

    for email in names:
        obj = api.get_client(email)

        if not obj:
            continue

        client = obj["client"]

        if not client.get("enable", False):
            continue

        expiry = client.get(
            "expiryTime",
            0,
        )

        if not expiry:
            continue

        if current <= expiry <= limit:
            remaining = max(
                0,
                (expiry - current)
                / 1000
                / 60
                / 60
                / 24,
            )

            found.append(
                (
                    expiry,
                    email,
                    remaining,
                )
            )

    found.sort()

    print()
    print(
        f"⏰ Подписки, заканчивающиеся "
        f"за {days} дн."
    )
    print()

    if not found:
        print("Таких подписок нет.")
        return

    for expiry, email, remaining in found:
        print(
            f"{email:<20} "
            f"{fmt_date(expiry)}   "
            f"осталось {remaining:.1f} дн."
        )


def cmd_traffic(args):
    if len(args) != 1:
        die(
            "использование: "
            "karina-user traffic ИМЯ"
        )

    email = normalize_email(args[0])

    config, api = create_api()

    obj = api.get_client(email)

    if not obj:
        die(f"клиент {email} не найден")

    client = obj["client"]

    used = int(
        obj.get(
            "usedTraffic",
            0,
        )
        or 0
    )

    total = int(
        client.get(
            "totalGB",
            0,
        )
        or 0
    )

    print()
    print(f"📊 Трафик {email}")
    print()
    print(
        f"Использовано: {bytes_to_human(used)}"
    )

    if total:
        remaining = max(
            total - used,
            0,
        )

        percent = (
            used / total * 100
            if total
            else 0
        )

        print(
            f"Лимит:        "
            f"{bytes_to_human(total)}"
        )
        print(
            f"Осталось:     "
            f"{bytes_to_human(remaining)}"
        )
        print(
            f"Использовано: "
            f"{percent:.1f}%"
        )
    else:
        print("Лимит:        Безлимит")


def cmd_set_traffic(args):
    if len(args) != 2:
        die(
            "использование: "
            "karina-user set-traffic ИМЯ ГБ"
        )

    email = normalize_email(args[0])

    try:
        gb = float(args[1])
    except ValueError:
        die("лимит трафика должен быть числом")

    if gb < 0:
        die("лимит трафика не может быть отрицательным")

    config, api = create_api()

    obj = api.get_client(email)

    if not obj:
        die(f"клиент {email} не найден")

    client = obj["client"]

    client["totalGB"] = traffic_limit_bytes(gb)

    api.update_client(
        email,
        {
            "client": client,
            "inboundIds": obj.get(
                "inboundIds",
                [],
            ),
        },
    )

    if gb == 0:
        print(
            f"{email}: установлен безлимитный трафик"
        )
    else:
        print(
            f"{email}: лимит установлен "
            f"{gb:g} ГБ"
        )


def cmd_set_hwid(args):
    if len(args) != 2:
        die(
            "использование: "
            "karina-user set-hwid ИМЯ КОЛИЧЕСТВО"
        )

    email = normalize_email(args[0])

    try:
        hwid = int(args[1])
    except ValueError:
        die("лимит HWID должен быть числом")

    if hwid < 0:
        die("лимит HWID не может быть отрицательным")

    config, api = create_api()

    obj = api.get_client(email)

    if not obj:
        die(f"клиент {email} не найден")

    client = obj["client"]

    client["limitHwid"] = hwid

    api.update_client(
        email,
        {
            "client": client,
            "inboundIds": obj.get(
                "inboundIds",
                [],
            ),
        },
    )

    print(
        f"{email}: лимит HWID установлен "
        f"{hwid if hwid else 'без ограничений'}"
    )


def cmd_devices(args):
    if len(args) != 1:
        die(
            "использование: "
            "karina-user devices ИМЯ"
        )

    email = normalize_email(args[0])

    config, api = create_api()

    obj = api.get_client(email)

    if not obj:
        die(f"клиент {email} не найден")

    devices = api.get_hwids(email)

    limit = obj["client"].get(
        "limitHwid",
        0,
    )

    print()
    print(f"📱 Устройства {email}")
    print(
        f"Использовано: "
        f"{len(devices)} / "
        f"{limit if limit else '∞'}"
    )
    print()

    if not devices:
        print(
            "Зарегистрированных устройств нет."
        )
        return

    for device in devices:
        model = (
            device.get("deviceModel")
            or "Неизвестное устройство"
        )

        os_name = (
            device.get("deviceOs")
            or "Неизвестная ОС"
        )

        os_ver = (
            device.get("osVersion")
            or ""
        )

        ua = (
            device.get("userAgent")
            or ""
        )

        print(
            f"ID:         {device.get('id')}"
        )
        print(
            f"Устройство: {model}"
        )
        print(
            f"ОС:         "
            f"{os_name} {os_ver}".rstrip()
        )
        print(
            f"Клиент:     {ua}"
        )

        if device.get("firstSeen"):
            print(
                f"Первый вход: "
                f"{fmt_date(device['firstSeen'])}"
            )

        if device.get("lastSeen"):
            print(
                f"Последний:   "
                f"{fmt_date(device['lastSeen'])}"
            )

        print()


def cmd_device_remove(args):
    if len(args) != 2:
        die(
            "использование: "
            "karina-user device-remove ИМЯ ID"
        )

    email = normalize_email(args[0])

    try:
        device_id = int(args[1])
    except ValueError:
        die("ID устройства должен быть числом")

    config, api = create_api()

    if not api.get_client(email):
        die(f"клиент {email} не найден")

    api.delete_hwid(
        email,
        device_id,
    )

    print(
        f"{email}: устройство "
        f"ID {device_id} удалено"
    )


def cmd_devices_reset(args):
    if len(args) != 1:
        die(
            "использование: "
            "karina-user devices-reset ИМЯ"
        )

    email = normalize_email(args[0])

    config, api = create_api()

    if not api.get_client(email):
        die(f"клиент {email} не найден")

    confirm = input(
        f"Сбросить ВСЕ устройства "
        f"клиента {email}? Напиши YES: "
    ).strip()

    if confirm != "YES":
        print("Отменено.")
        return

    api.reset_hwids(email)

    print(
        f"{email}: все HWID сброшены"
    )


def cmd_extend(args):
    if len(args) != 2:
        die(
            "использование: "
            "karina-user extend ИМЯ ДНИ"
        )

    email = normalize_email(args[0])

    try:
        days = int(args[1])
    except ValueError:
        die("количество дней должно быть числом")

    if days <= 0:
        die("количество дней должно быть больше 0")

    config, api = create_api()

    obj = api.get_client(email)

    if not obj:
        die(f"клиент {email} не найден")

    client = obj["client"]

    base_ms = max(
        client.get("expiryTime", 0),
        now_ms(),
    )

    client["expiryTime"] = (
        base_ms
        + days
        * 24
        * 60
        * 60
        * 1000
    )

    api.update_client(
        email,
        {
            "client": client,
            "inboundIds": obj.get(
                "inboundIds",
                [],
            ),
        },
    )

    print(
        f"{email}: срок продлён "
        f"на {days} дней до "
        f"{fmt_date(client['expiryTime'])}"
    )


def cmd_disable(args):
    if len(args) != 1:
        die(
            "использование: "
            "karina-user disable ИМЯ"
        )

    email = normalize_email(args[0])

    config, api = create_api()

    obj = api.get_client(email)

    if not obj:
        die(f"клиент {email} не найден")

    client = obj["client"]
    client["enable"] = False

    api.update_client(
        email,
        {
            "client": client,
            "inboundIds": obj.get(
                "inboundIds",
                [],
            ),
        },
    )

    print(f"{email}: отключён")


def cmd_enable(args):
    if len(args) != 1:
        die(
            "использование: "
            "karina-user enable ИМЯ"
        )

    email = normalize_email(args[0])

    config, api = create_api()

    obj = api.get_client(email)

    if not obj:
        die(f"клиент {email} не найден")

    client = obj["client"]
    client["enable"] = True

    api.update_client(
        email,
        {
            "client": client,
            "inboundIds": obj.get(
                "inboundIds",
                [],
            ),
        },
    )

    print(f"{email}: включён")


def delete_client_impl(email):
    config, api = create_api()
    obj = api.get_client(email)
    if not obj:
        die(f"клиент {email} не найден")
    sub_id = obj["client"].get("subId")
    api.delete_client(email)
    warnings = []
    if not isinstance(sub_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", sub_id):
        return {"vpn_deleted": True, "warnings": ["Invalid subId; file cleanup skipped"]}
    try:
        connect_dir = CONNECT_DIR.resolve()
        for suffix in (".html", ".png", ".crypt5"):
            path = connect_dir / f"{sub_id}{suffix}"
            try:
                resolved = path.resolve()
                if not resolved.is_relative_to(connect_dir):
                    warnings.append(f"Unsafe {suffix} path; cleanup skipped")
                    continue
                path.unlink()
            except FileNotFoundError:
                pass
            except (OSError, RuntimeError) as exc:
                warnings.append(f"File cleanup failed ({suffix}): {type(exc).__name__}")
    except (OSError, RuntimeError) as exc:
        warnings.append(f"Connect directory unavailable: {type(exc).__name__}")
    return {"vpn_deleted": True, "warnings": warnings}


def cmd_delete(args):
    if len(args) != 1:
        die("использование: karina-user delete ИМЯ")
    email = normalize_email(args[0])
    confirm = input(
        f"Удалить клиента {email} "
        f"полностью? Напиши YES: "
    ).strip()

    if confirm != "YES":
        print("Отменено.")
        return

    result = delete_client_impl(email)
    print(f"{email}: удалён")
    for warning in result["warnings"]:
        print(f"Warning: {warning}", file=sys.stderr)


def cmd_delete_confirmed(args):
    """Trusted local caller only; stdout is a machine-readable deletion result."""
    if len(args) != 1:
        die("usage: karina-user delete-confirmed EMAIL")
    result = delete_client_impl(normalize_email(args[0]))
    print(json.dumps(result))


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

    command = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "create": cmd_create,
        "info": cmd_info,
        "list": cmd_list,
        "expiring": cmd_expiring,
        "traffic": cmd_traffic,
        "set-traffic": cmd_set_traffic,
        "set-hwid": cmd_set_hwid,
        "devices": cmd_devices,
        "device-remove": cmd_device_remove,
        "devices-reset": cmd_devices_reset,
        "extend": cmd_extend,
        "disable": cmd_disable,
        "enable": cmd_enable,
        "delete": cmd_delete,
        "delete-confirmed": cmd_delete_confirmed,
    }

    handler = commands.get(command)

    if not handler:
        usage()
        sys.exit(1)

    try:
        handler(args)
    except (ConfigError, XUIError) as exc:
        die(str(exc))


if __name__ == "__main__":
    main()
