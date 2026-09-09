import math
import re
import secrets
import string
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    from ..app_config import KarinaConfig
    from ..integrations.xui import XUIClient, XUIError
    from ..models import (
        ClientInfo, CreateClientResult, DeleteClientResult, DeviceInfo,
        ExpiringClient, TrafficInfo,
    )
except ImportError:  # Direct CLI execution from the src directory.
    from app_config import KarinaConfig
    from integrations.xui import XUIClient, XUIError
    from models import (
        ClientInfo, CreateClientResult, DeleteClientResult, DeviceInfo,
        ExpiringClient, TrafficInfo,
    )
from .errors import (
    ClientAlreadyExistsError, ClientNotFoundError, ClientServiceError,
    ValidationError,
)


class ClientService:
    def __init__(
        self,
        config: KarinaConfig,
        xui: XUIClient,
        issue_subscription: Callable[[str], str | None] | None = None,
        now_provider: Callable[[], int] | None = None,
        connect_dir: Path | str = "/var/www/karina/connect",
    ):
        self.config = config
        self.xui = xui
        self.issue_subscription = issue_subscription
        self.now_provider = now_provider or (lambda: int(time.time() * 1000))
        self.connect_dir = Path(connect_dir)

    @staticmethod
    def validate_email(email: str) -> str:
        value = email.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,64}", value):
            raise ValidationError(
                "имя клиента может содержать только A-Z, a-z, 0-9, _, -, . "
                "и быть длиной 2–64 символа"
            )
        return value

    @staticmethod
    def _positive_days(days: int) -> int:
        if type(days) is not int or days <= 0:
            raise ValidationError("количество дней должно быть больше 0")
        return days

    @staticmethod
    def _hwid_limit(limit: int) -> int:
        if type(limit) is not int or limit < 0:
            raise ValidationError("лимит HWID не может быть отрицательным")
        return limit

    @staticmethod
    def _traffic_bytes(traffic_gb: float) -> int:
        if isinstance(traffic_gb, bool) or not isinstance(traffic_gb, (int, float)):
            raise ValidationError("лимит трафика должен быть числом")
        if not math.isfinite(traffic_gb) or traffic_gb < 0:
            raise ValidationError("лимит трафика не может быть отрицательным")
        return int(traffic_gb * 1024 * 1024 * 1024) if traffic_gb > 0 else 0

    @staticmethod
    def _sub_id(length=16):
        alphabet = string.ascii_lowercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _call(self, operation, message):
        try:
            return operation()
        except XUIError as exc:
            raise ClientServiceError(f"{message}: {exc}") from exc

    def _raw_client(self, email):
        email = self.validate_email(email)
        obj = self._call(lambda: self.xui.get_client(email), "ошибка получения клиента")
        return email, obj

    def _require_raw(self, email):
        email, obj = self._raw_client(email)
        if not obj:
            raise ClientNotFoundError(f"клиент {email} не найден")
        return email, obj

    def _status(self, client):
        if not client.get("enable", False):
            return "disabled"
        expiry = int(client.get("expiryTime", 0) or 0)
        return "expired" if expiry and expiry < self.now_provider() else "active"

    @staticmethod
    def _expiry_text(expiry):
        if not expiry:
            return "Без срока"
        return datetime.fromtimestamp(expiry / 1000).strftime("%d.%m.%Y %H:%M")

    def _to_client_info(self, obj, device_count=0):
        client = obj.get("client", {})
        expiry = int(client.get("expiryTime", 0) or 0)
        sub_id = str(client.get("subId") or "")
        return ClientInfo(
            email=str(client.get("email") or ""), status=self._status(client),
            enabled=bool(client.get("enable", False)), expiry_time_ms=expiry,
            expiry_text=self._expiry_text(expiry), device_count=device_count,
            device_limit=int(client.get("limitHwid", 0) or 0),
            total_traffic_bytes=int(client.get("totalGB", 0) or 0),
            used_traffic_bytes=int(obj.get("usedTraffic", 0) or 0),
            inbound_ids=tuple(int(value) for value in obj.get("inboundIds", [])),
            sub_id=sub_id,
            connect_url=f"{self.config.connect_base}/{sub_id}.html" if sub_id else "",
        )

    def _devices_for_summary(self, email):
        try:
            return self.xui.get_hwids(email)
        except XUIError:
            return []

    def get_client(self, email) -> ClientInfo | None:
        email, obj = self._raw_client(email)
        return self._to_client_info(obj, len(self._devices_for_summary(email))) if obj else None

    def _client_names(self):
        inbounds = self._call(self.xui.list_inbounds, "ошибка получения списка клиентов")
        return sorted({client.get("email") for inbound in inbounds
                       for client in inbound.get("settings", {}).get("clients", [])
                       if client.get("email")}, key=str.lower)

    def list_clients(self) -> list[ClientInfo]:
        result = []
        for email in self._client_names():
            client = self.get_client(email)
            if client:
                result.append(client)
        return result

    def create_client(self, email, days=30, hwid_limit=None, traffic_gb=0) -> CreateClientResult:
        email = self.validate_email(email)
        days = self._positive_days(days)
        limit = self.config.default_hwid_limit if hwid_limit is None else self._hwid_limit(hwid_limit)
        total = self._traffic_bytes(traffic_gb)
        if self._call(lambda: self.xui.get_client(email), "ошибка проверки клиента"):
            raise ClientAlreadyExistsError(f"клиент {email} уже существует")
        payload = {
            "client": {
                "email": email, "subId": self._sub_id(),
                "expiryTime": self.now_provider() + days * 86400000,
                "totalGB": total, "limitIp": 0, "limitHwid": limit,
                "enable": True, "tgId": 0, "flow": "", "security": "auto",
                "group": "", "comment": "", "reset": 0, "resetDay": 0,
                "resetMax": 0, "trafficReset": "never", "trafficResetDay": 1,
            },
            "inboundIds": list(self.config.inbound_ids),
        }
        self._call(lambda: self.xui.create_client(payload), "ошибка создания клиента")
        created = self._call(lambda: self.xui.get_client(email), "ошибка проверки созданного клиента")
        if not created:
            raise ClientServiceError("клиент не найден после создания")
        client = self._to_client_info(created)
        page = None
        warning = None
        if self.issue_subscription:
            try:
                page = self.issue_subscription(client.sub_id)
                if page is None:
                    warning = "генерация Crypt5/QR завершилась ошибкой"
            except Exception as exc:
                warning = (str(exc) or type(exc).__name__)[:500]
        return CreateClientResult(client, page, warning)

    def _update(self, email, mutate):
        email, obj = self._require_raw(email)
        client = obj["client"]
        mutate(client)
        self._call(lambda: self.xui.update_client(email, {
            "client": client, "inboundIds": obj.get("inboundIds", []),
        }), "ошибка обновления клиента")
        return self._to_client_info(obj, len(self._devices_for_summary(email)))

    def extend_client(self, email, days) -> ClientInfo:
        days = self._positive_days(days)
        now = self.now_provider()
        return self._update(email, lambda client: client.__setitem__(
            "expiryTime", max(int(client.get("expiryTime", 0) or 0), now) + days * 86400000
        ))

    def enable_client(self, email) -> ClientInfo:
        return self._update(email, lambda client: client.__setitem__("enable", True))

    def disable_client(self, email) -> ClientInfo:
        return self._update(email, lambda client: client.__setitem__("enable", False))

    def set_hwid_limit(self, email, limit) -> ClientInfo:
        limit = self._hwid_limit(limit)
        return self._update(email, lambda client: client.__setitem__("limitHwid", limit))

    def get_traffic(self, email) -> TrafficInfo:
        _, obj = self._require_raw(email)
        used = int(obj.get("usedTraffic", 0) or 0)
        limit = int(obj.get("client", {}).get("totalGB", 0) or 0)
        if not limit:
            return TrafficInfo(used, 0, None, None)
        return TrafficInfo(used, limit, max(limit - used, 0), used / limit * 100)

    def set_traffic_limit(self, email, traffic_gb) -> ClientInfo:
        total = self._traffic_bytes(traffic_gb)
        return self._update(email, lambda client: client.__setitem__("totalGB", total))

    def get_devices(self, email) -> list[DeviceInfo]:
        email, _ = self._require_raw(email)
        devices = self._call(lambda: self.xui.get_hwids(email), "ошибка получения устройств")
        return [DeviceInfo(
            id=int(item.get("id", 0) or 0),
            model=str(item.get("deviceModel") or ""),
            os_name=str(item.get("deviceOs") or ""),
            os_version=str(item.get("osVersion") or ""),
            user_agent=str(item.get("userAgent") or ""),
            first_seen_ms=int(item.get("firstSeen", 0) or 0),
            last_seen_ms=int(item.get("lastSeen", 0) or 0),
        ) for item in devices]

    def remove_device(self, email, device_id) -> None:
        email, _ = self._require_raw(email)
        if type(device_id) is not int:
            raise ValidationError("ID устройства должен быть числом")
        self._call(lambda: self.xui.delete_hwid(email, device_id), "ошибка удаления устройства")

    def reset_devices(self, email) -> None:
        email, _ = self._require_raw(email)
        self._call(lambda: self.xui.reset_hwids(email), "ошибка сброса устройств")

    def get_expiring(self, days) -> list[ExpiringClient]:
        if type(days) is not int or days < 0:
            raise ValidationError("количество дней не может быть отрицательным")
        now = self.now_provider()
        limit = now + days * 86400000
        result = []
        for email in self._client_names():
            obj = self._call(lambda email=email: self.xui.get_client(email),
                             "ошибка получения клиента")
            if not obj:
                continue
            client = obj.get("client", {})
            expiry = int(client.get("expiryTime", 0) or 0)
            if client.get("enable", False) and expiry and now <= expiry <= limit:
                result.append(ExpiringClient(email, expiry, (expiry - now) / 86400000))
        return sorted(result, key=lambda item: item.expiry_time_ms)

    def delete_client(self, email) -> DeleteClientResult:
        email, obj = self._require_raw(email)
        sub_id = obj.get("client", {}).get("subId")
        self._call(lambda: self.xui.delete_client(email), "ошибка удаления клиента")
        warnings = []
        if not isinstance(sub_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", sub_id):
            warnings.append("Invalid subId; file cleanup skipped")
        else:
            try:
                connect_dir = self.connect_dir.resolve()
                for suffix in (".html", ".png", ".crypt5"):
                    path = connect_dir / f"{sub_id}{suffix}"
                    try:
                        if not path.resolve().is_relative_to(connect_dir):
                            warnings.append(f"Unsafe {suffix} path; cleanup skipped")
                            continue
                        path.unlink()
                    except FileNotFoundError:
                        pass
                    except (OSError, RuntimeError) as exc:
                        warnings.append(f"File cleanup failed ({suffix}): {type(exc).__name__}")
            except (OSError, RuntimeError) as exc:
                warnings.append(f"Connect directory unavailable: {type(exc).__name__}")
        return DeleteClientResult(
            email=email,
            removed=True,
            file_cleanup_warning="; ".join(warnings) or None,
        )
