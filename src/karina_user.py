#!/usr/bin/env python3

import json
import re
import secrets
import string
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path

CONFIG = Path("/etc/karina-vpn/config.env")


def die(message, code=1):
    print(f"Ошибка: {message}", file=sys.stderr)
    sys.exit(code)


def load_env(path):
    if not path.exists():
        die(f"не найден {path}")

    env = {}

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")

    required = [
        "XUI_BASE",
        "XUI_USER",
        "XUI_PASS",
        "SUB_BASE",
        "CONNECT_BASE",
        "INBOUND_IDS",
    ]

    for key in required:
        if not env.get(key):
            die(f"в {path} отсутствует {key}")

    return env


ENV = load_env(CONFIG)

BASE = ENV["XUI_BASE"].rstrip("/")
SUB_BASE = ENV["SUB_BASE"].rstrip("/")
CONNECT_BASE = ENV["CONNECT_BASE"].rstrip("/")
INBOUND_IDS = [
    int(x.strip())
    for x in ENV["INBOUND_IDS"].split(",")
    if x.strip()
]
DEFAULT_HWID = int(ENV.get("DEFAULT_HWID_LIMIT", "2"))


class XUI:
    def __init__(self):
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.csrf = None

    def request(self, method, path, payload=None, csrf=False):
        url = BASE + path

        headers = {
            "User-Agent": "KarinaVPN-Automation/1.1",
            "Accept": "application/json",
        }

        body = None

        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"

        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf

        req = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method=method,
        )

        try:
            with self.opener.open(req, timeout=20) as response:
                raw = response.read().decode(
                    "utf-8",
                    errors="replace",
                )
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(
                "utf-8",
                errors="replace",
            )
            die(f"HTTP {exc.code}: {raw or exc.reason}")
        except Exception as exc:
            die(f"ошибка обращения к 3x-ui: {exc}")

        if not raw:
            return {}

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            die(f"3x-ui вернул не JSON: {raw[:500]}")

    def login(self):
        csrf_result = self.request(
            "GET",
            "/csrf-token",
        )

        if not csrf_result.get("success"):
            die(
                f"не удалось получить CSRF: "
                f"{csrf_result}"
            )

        self.csrf = csrf_result["obj"]

        login_result = self.request(
            "POST",
            "/login",
            {
                "username": ENV["XUI_USER"],
                "password": ENV["XUI_PASS"],
                "twoFactorCode": "",
            },
            csrf=True,
        )

        if not login_result.get("success"):
            die(
                login_result.get(
                    "msg",
                    "не удалось войти в 3x-ui",
                )
            )

    def get_client(self, email):
        result = self.request(
            "GET",
            "/panel/api/clients/get/"
            + urllib.parse.quote(email, safe=""),
        )

        if not result.get("success"):
            return None

        return result["obj"]

    def list_inbounds(self):
        result = self.request(
            "GET",
            "/panel/api/inbounds/list",
        )

        if not result.get("success"):
            die(
                result.get(
                    "msg",
                    "не удалось получить список inbound",
                )
            )

        return result.get("obj", [])

    def create_client(self, payload):
        result = self.request(
            "POST",
            "/panel/api/clients/add",
            payload,
            csrf=True,
        )

        if not result.get("success"):
            die(
                result.get(
                    "msg",
                    "не удалось создать клиента",
                )
            )

        return result.get("obj")

    def update_client(self, email, payload):
        result = self.request(
            "POST",
            "/panel/api/clients/update/"
            + urllib.parse.quote(email, safe=""),
            payload,
            csrf=True,
        )

        if not result.get("success"):
            die(
                result.get(
                    "msg",
                    "не удалось обновить клиента",
                )
            )

        return result.get("obj")

    def delete_client(self, email):
        result = self.request(
            "POST",
            "/panel/api/clients/del/"
            + urllib.parse.quote(email, safe=""),
            {},
            csrf=True,
        )

        if not result.get("success"):
            die(
                result.get(
                    "msg",
                    "не удалось удалить клиента",
                )
            )

    def get_hwids(self, email):
        result = self.request(
            "POST",
            "/panel/api/clients/hwids/"
            + urllib.parse.quote(email, safe=""),
            {},
            csrf=True,
        )

        if not result.get("success"):
            die(
                result.get(
                    "msg",
                    "не удалось получить список устройств",
                )
            )

        return result.get("obj", [])

    def delete_hwid(self, email, device_id):
        result = self.request(
            "DELETE",
            "/panel/api/clients/hwids/"
            + urllib.parse.quote(email, safe="")
            + f"/{device_id}",
            None,
            csrf=True,
        )

        if not result.get("success"):
            die(
                result.get(
                    "msg",
                    "не удалось удалить устройство",
                )
            )

    def reset_hwids(self, email):
        result = self.request(
            "DELETE",
            "/panel/api/clients/hwids/"
            + urllib.parse.quote(email, safe=""),
            None,
            csrf=True,
        )

        if not result.get("success"):
            die(
                result.get(
                    "msg",
                    "не удалось сбросить устройства",
                )
            )


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


def issue_page(sub_id):
    url = f"{SUB_BASE}/{sub_id}"

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

    return f"{CONNECT_BASE}/{sub_id}.html"


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
    except SystemExit:
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

    try:
        days = (
            int(args[1])
            if len(args) >= 2
            else 30
        )

        hwid = (
            int(args[2])
            if len(args) >= 3
            else DEFAULT_HWID
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

    api = XUI()
    api.login()

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
        "inboundIds": INBOUND_IDS,
    }

    api.create_client(payload)

    created = api.get_client(email)

    if not created:
        die("клиент не найден после создания")

    client = created["client"]
    page = issue_page(
        client["subId"]
    )

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

    api = XUI()
    api.login()

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
        f"{CONNECT_BASE}/"
        f"{client['subId']}.html"
    )


def cmd_list(args):
    if args:
        die(
            "использование: "
            "karina-user list"
        )

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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

    api = XUI()
    api.login()

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


def cmd_delete(args):
    if len(args) != 1:
        die(
            "использование: "
            "karina-user delete ИМЯ"
        )

    email = normalize_email(args[0])

    api = XUI()
    api.login()

    obj = api.get_client(email)

    if not obj:
        die(f"клиент {email} не найден")

    sub_id = obj["client"]["subId"]

    confirm = input(
        f"Удалить клиента {email} "
        f"полностью? Напиши YES: "
    ).strip()

    if confirm != "YES":
        print("Отменено.")
        return

    api.delete_client(email)

    connect_dir = Path(
        "/var/www/karina/connect"
    )

    for suffix in (
        ".html",
        ".png",
        ".crypt5",
    ):
        path = (
            connect_dir
            / f"{sub_id}{suffix}"
        )

        try:
            path.unlink()
        except FileNotFoundError:
            pass

    print(f"{email}: удалён")


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
    }

    handler = commands.get(command)

    if not handler:
        usage()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
