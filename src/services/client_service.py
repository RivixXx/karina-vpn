import json
import math
import re
import secrets
import string
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    from ..app_config import KarinaConfig
    from ..integrations.xui import XUIClient, XUIError
    from ..models import (
        ClientInfo, CreateClientResult, DeleteClientResult, DeviceInfo,
        ClientBundle, CreateClientBundleResult, ExternalLinkInput, MobileMigrationPlan,
        ExpiringClient, TrafficInfo, is_mobile_email, mobile_email_for,
    )
except ImportError:  # Direct CLI execution from the src directory.
    from app_config import KarinaConfig
    from integrations.xui import XUIClient, XUIError
    from models import (
        ClientInfo, CreateClientResult, DeleteClientResult, DeviceInfo,
        ClientBundle, CreateClientBundleResult, ExternalLinkInput, MobileMigrationPlan,
        ExpiringClient, TrafficInfo, is_mobile_email, mobile_email_for,
    )
from .errors import (
    ClientAlreadyExistsError, ClientNotFoundError, ClientServiceError,
    ReconciliationRequiredError, ValidationError,
)


class ClientService:
    _WRITABLE_CLIENT_FIELDS = frozenset({
        "email", "subId", "uuid", "password", "auth", "flow", "security",
        "totalGB", "expiryTime", "limitIp", "limitHwid", "tgId", "group",
        "comment", "enable", "reset", "resetDay", "resetMax", "trafficReset",
        "trafficResetDay", "privateKey", "publicKey", "allowedIPs",
        "preSharedKey", "reverse",
    })
    _INTEGER_CLIENT_FIELDS = frozenset({
        "totalGB", "expiryTime", "limitIp", "limitHwid", "tgId", "reset",
        "resetDay", "resetMax", "trafficResetDay",
    })
    _STRING_CLIENT_FIELDS = frozenset({
        "email", "subId", "uuid", "password", "auth", "flow", "security",
        "group", "comment", "trafficReset", "privateKey", "publicKey",
        "preSharedKey",
    })

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
            if is_mobile_email(email):
                continue
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
                    warning = "генерация QR/страницы подключения завершилась ошибкой"
            except Exception as exc:
                warning = (str(exc) or type(exc).__name__)[:500]
        return CreateClientResult(client, page, warning)

    def _create_credential(self, email, expiry, limit, total, inbound_ids):
        payload = {"client": {
            "id": str(uuid.uuid4()), "email": email, "subId": self._sub_id(),
            "expiryTime": expiry, "totalGB": total, "limitIp": 0,
            "limitHwid": limit, "enable": True, "tgId": 0, "flow": "",
            "security": "auto", "group": "", "comment": "", "reset": 0,
            "resetDay": 0, "resetMax": 0, "trafficReset": "never",
            "trafficResetDay": 1,
        }, "inboundIds": list(inbound_ids)}
        self._call(lambda: self.xui.create_client(payload), "credential creation failed")
        created = self._call(lambda: self.xui.get_client(email), "credential verification failed")
        if not created:
            raise ClientServiceError("credential missing after creation")
        return self._to_client_info(created)

    @staticmethod
    def _writable_links(obj):
        return [ExternalLinkInput(str(item.get("kind") or ""),
                                  str(item.get("value") or ""),
                                  str(item.get("remark") or ""))
                for item in obj.get("externalLinks", [])
                if item.get("kind") in {"link", "subscription"}]

    def _ensure_mobile_subscription(self, primary_email, mobile_sub_id):
        expected = f"{self.config.sub_base}/{mobile_sub_id}"
        obj = self._call(lambda: self.xui.get_client(primary_email), "primary lookup failed")
        links = self._writable_links(obj)
        managed = [link for link in links
                   if link.kind == "subscription" and link.remark == "karina-mobile"]
        if len(managed) == 1 and managed[0].value == expected:
            return
        retained = [link for link in links
                    if not (link.kind == "subscription" and link.remark == "karina-mobile")]
        replacement = retained + [ExternalLinkInput("subscription", expected, "karina-mobile")]
        self._call(lambda: self.xui.set_external_links(primary_email, replacement),
                   "external subscription update failed")
        verified = self._call(lambda: self.xui.get_client(primary_email),
                              "external subscription verification failed")
        verified_links = self._writable_links(verified)
        matches = [link for link in verified_links
                   if link.kind == "subscription" and link.remark == "karina-mobile"
                   and link.value == expected]
        if len(matches) != 1 or any(link not in verified_links for link in retained):
            raise ReconciliationRequiredError("external subscription verification failed")

    def _mobile_inbound_ids(self):
        configured = tuple(getattr(self.config, "mobile_inbound_ids", ()) or ())
        return configured or (self.config.mobile_inbound_id,)

    def _existing_bundle_is_complete(self, primary_obj, mobile_obj, requested_limit, days):
        primary = self._to_client_info(primary_obj)
        mobile = self._to_client_info(mobile_obj)
        expected_mobile = set(self._mobile_inbound_ids())
        expected_primary = set(self.config.primary_inbound_ids or self.config.inbound_ids)
        primary_raw = primary_obj.get("client", {})
        mobile_raw = mobile_obj.get("client", {})
        managed = [link for link in self._writable_links(primary_obj)
                   if link.kind == "subscription" and link.remark == "karina-mobile"]
        expected_url = f"{self.config.sub_base}/{mobile.sub_id}"
        credential_keys = ("id", "uuid", "password")
        same_credential = any(
            primary_raw.get(key) and primary_raw.get(key) == mobile_raw.get(key)
            for key in credential_keys
        )
        return (
            primary.enabled and mobile.enabled
            and set(primary.inbound_ids) == expected_primary
            and set(mobile.inbound_ids) == expected_mobile
            and primary.total_traffic_bytes == 0
            and mobile.total_traffic_bytes == self.config.mobile_traffic_bytes
            and primary.device_limit == requested_limit and mobile.device_limit == 0
            and primary.expiry_time_ms == mobile.expiry_time_ms
            and ((days is None and primary.expiry_time_ms == 0)
                 or (days is not None and primary.expiry_time_ms > 0))
            and bool(primary.sub_id and mobile.sub_id)
            and primary.sub_id != mobile.sub_id and not same_credential
            and len(managed) == 1 and managed[0].value == expected_url
        )

    def create_client_bundle(self, email, days=30, hwid_limit=None):
        email = self.validate_email(email)
        try:
            mobile_email = mobile_email_for(email)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if days is not None:
            days = self._positive_days(days)
        limit = self.config.default_hwid_limit if hwid_limit is None else self._hwid_limit(hwid_limit)
        _, primary_obj = self._raw_client(email)
        _, mobile_obj = self._raw_client(mobile_email)
        if primary_obj or mobile_obj:
            if (primary_obj and mobile_obj
                    and self._existing_bundle_is_complete(primary_obj, mobile_obj, limit, days)):
                primary, mobile = self._to_client_info(primary_obj), self._to_client_info(mobile_obj)
                try:
                    page = self.issue_subscription(primary.sub_id) if self.issue_subscription else None
                    if not page:
                        raise ClientServiceError("subscription issuer returned no verified page")
                except Exception as exc:
                    raise ReconciliationRequiredError(
                        "bundle exists but connection issuance failed"
                    ) from exc
                return CreateClientBundleResult(ClientBundle(primary, mobile), page)
            if primary_obj and mobile_obj:
                raise ReconciliationRequiredError("existing client bundle is inconsistent")
            raise ClientAlreadyExistsError(f"client {email} already exists")
        expiry = 0 if days is None else self.now_provider() + days * 86400000
        primary = mobile = None
        try:
            primary = self._create_credential(
                email, expiry, limit, 0,
                self.config.primary_inbound_ids or self.config.inbound_ids,
            )
            mobile = self._create_credential(
                mobile_email, expiry, 0, self.config.mobile_traffic_bytes,
                self._mobile_inbound_ids(),
            )
            self._ensure_mobile_subscription(email, mobile.sub_id)
        except Exception as exc:
            rollback_errors = []
            for candidate in (mobile_email if mobile else None, email if primary else None):
                if candidate:
                    try:
                        self.xui.delete_client(candidate)
                    except Exception as rollback_exc:
                        rollback_errors.append(type(rollback_exc).__name__)
            if rollback_errors:
                raise ReconciliationRequiredError("bundle rollback incomplete") from exc
            raise
        try:
            page = self.issue_subscription(primary.sub_id) if self.issue_subscription else None
            if not page:
                raise ClientServiceError("subscription issuer returned no verified page")
        except Exception as exc:
            raise ReconciliationRequiredError(
                "bundle created but connection issuance failed"
            ) from exc
        return CreateClientBundleResult(ClientBundle(primary, mobile), page)

    def get_client_bundle(self, email):
        primary = self.get_client(email)
        if primary is None:
            return None
        return ClientBundle(primary, self.get_client(mobile_email_for(email)))

    def get_mobile_subscription_url(self, email):
        """Return the authoritative direct mobile subscription URL."""
        bundle = self.get_client_bundle(email)
        if bundle is None:
            raise ClientNotFoundError(f"client {email} not found")
        if bundle.mobile is None or not bundle.mobile.sub_id:
            raise ReconciliationRequiredError("mobile subscription is unavailable")
        return f"{self.config.sub_base}/{bundle.mobile.sub_id}"

    def get_primary_inbound_names(self, email) -> tuple[str, ...]:
        primary = self.get_client(email)
        if primary is None:
            raise ClientNotFoundError(f"client {email} not found")
        wanted = set(primary.inbound_ids)
        names = []
        for inbound in self._call(self.xui.list_inbounds, "inbound lookup failed"):
            inbound_id = int(inbound.get("id", 0) or 0)
            if inbound_id in wanted:
                name = str(inbound.get("remark") or inbound.get("tag") or f"#{inbound_id}")
                names.append((inbound_id, name))
        known = {item[0] for item in names}
        names.extend((inbound_id, f"#{inbound_id}") for inbound_id in wanted - known)
        return tuple(name for _, name in sorted(names))

    def migrate_client_to_mobile_bundle(self, email):
        plan = self.plan_mobile_migration(email)
        if plan.blocking_errors:
            raise ReconciliationRequiredError("; ".join(plan.blocking_errors))
        if plan.already_migrated:
            return self.get_client_bundle(email)
        primary = self.get_client(email)
        if primary is None:
            raise ClientNotFoundError(f"client {email} not found")
        mobile_email = mobile_email_for(email)
        mobile = self.get_client(mobile_email)
        if mobile is None:
            mobile = self._create_credential(
                mobile_email, primary.expiry_time_ms, 0,
                self.config.mobile_traffic_bytes, self._mobile_inbound_ids(),
            )
        elif set(mobile.inbound_ids) != set(self._mobile_inbound_ids()):
            raise ReconciliationRequiredError("existing mobile credential is inconsistent")
        if mobile.total_traffic_bytes != self.config.mobile_traffic_bytes:
            mobile = self._update(mobile_email, lambda client: client.__setitem__(
                "totalGB", self.config.mobile_traffic_bytes
            ))
        if mobile.expiry_time_ms != primary.expiry_time_ms or mobile.device_limit != 0:
            def synchronize(client):
                client["expiryTime"] = primary.expiry_time_ms
                client["limitHwid"] = 0
            mobile = self._update(mobile_email, synchronize)
        self._ensure_mobile_subscription(email, mobile.sub_id)
        mobile_inbound_ids = self._mobile_inbound_ids()
        attached_mobile_ids = tuple(value for value in primary.inbound_ids
                                    if value in set(mobile_inbound_ids))
        if attached_mobile_ids:
            self._call(lambda: self.xui.detach_inbounds(email, attached_mobile_ids),
                       "mobile inbound detach failed")
        final = self.get_client_bundle(email)
        return final

    def plan_mobile_migration(self, email):
        blocking = []
        warnings = ("Historical mobile inbound traffic is not transferred to the mobile credential.",)
        if is_mobile_email(email):
            blocking.append("input email is an internal mobile credential")
            primary_obj = None
            mobile_email = email
        else:
            email = self.validate_email(email)
            mobile_email = mobile_email_for(email)
            _, primary_obj = self._raw_client(email)
        if primary_obj is None:
            blocking.append("primary credential does not exist")
            return MobileMigrationPlan(
                email, mobile_email, False, False, (), (), 0, None, 0, None, 0, None,
                False, False, 0, False, False, False, False, False, False,
                False, warnings, tuple(blocking),
            )
        primary = self._to_client_info(primary_obj)
        _, mobile_obj = self._raw_client(mobile_email)
        mobile = self._to_client_info(mobile_obj) if mobile_obj else None
        if mobile is not None and not mobile.sub_id:
            blocking.append("existing mobile credential has no subscription identity")
        links = self._writable_links(primary_obj)
        managed = [link for link in links
                   if link.kind == "subscription" and link.remark == "karina-mobile"]
        if len(managed) > 1:
            blocking.append("multiple managed karina-mobile external links")
        expected_primary = set(self.config.primary_inbound_ids or self.config.inbound_ids)
        expected_mobile = set(self._mobile_inbound_ids())
        if set(primary.inbound_ids) - expected_mobile != expected_primary:
            blocking.append("primary inbound set does not match configured primary inbounds")
        allowed_primary = expected_primary | expected_mobile
        if not set(primary.inbound_ids).issubset(allowed_primary):
            blocking.append("primary has unexpected inbound state")
        mobile_inbounds = mobile.inbound_ids if mobile else ()
        if mobile and set(mobile_inbounds) != expected_mobile:
            blocking.append("existing mobile credential has incompatible inbound state")
        expected_url = f"{self.config.sub_base}/{mobile.sub_id}" if mobile else None
        link_present = bool(managed)
        link_correct = bool(expected_url and len(managed) == 1 and managed[0].value == expected_url)
        needs_create = mobile is None
        needs_quota = bool(mobile and mobile.total_traffic_bytes != self.config.mobile_traffic_bytes)
        needs_expiry = bool(mobile and mobile.expiry_time_ms != primary.expiry_time_ms)
        needs_hwid = bool(mobile and mobile.device_limit != 0)
        needs_detach = bool(set(primary.inbound_ids) & expected_mobile)
        needs_link = not link_correct
        already = not blocking and not any((needs_create, needs_quota, needs_expiry,
                                             needs_hwid, needs_detach, needs_link))
        return MobileMigrationPlan(
            email, mobile_email, True, mobile is not None, primary.inbound_ids,
            mobile_inbounds, primary.expiry_time_ms,
            mobile.expiry_time_ms if mobile else None, primary.device_limit,
            mobile.device_limit if mobile else None, primary.total_traffic_bytes,
            mobile.total_traffic_bytes if mobile else None, link_present, link_correct,
            len(managed), needs_create, needs_link, needs_detach, needs_quota,
            needs_expiry, needs_hwid, already, warnings, tuple(blocking),
        )

    def _update(self, email, mutate):
        email, obj = self._require_raw(email)
        client = obj["client"]
        mutate(client)
        writable = self._normalize_client_for_update(client)
        writable["inboundIds"] = list(obj.get("inboundIds", []))
        self._call(lambda: self.xui.update_client(email, writable),
                   "ошибка обновления клиента")
        return self._to_client_info(obj, len(self._devices_for_summary(email)))

    @staticmethod
    def _normalize_allowed_ips(value):
        if value is None or value == "":
            return []
        if isinstance(value, list):
            if not all(isinstance(item, str) for item in value):
                raise ClientServiceError("invalid allowedIPs value from 3x-ui")
            return list(value)
        if not isinstance(value, str):
            raise ClientServiceError("invalid allowedIPs value from 3x-ui")
        text = value.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ClientServiceError("invalid allowedIPs value from 3x-ui") from exc
            if not isinstance(decoded, list) or not all(
                    isinstance(item, str) for item in decoded):
                raise ClientServiceError("invalid allowedIPs value from 3x-ui")
            return decoded
        return [item.strip() for item in text.split(",") if item.strip()]

    @staticmethod
    def _normalize_reverse(value):
        if value is None or value == "":
            return None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ClientServiceError("invalid reverse value from 3x-ui") from exc
        if not isinstance(value, dict):
            raise ClientServiceError("invalid reverse value from 3x-ui")
        tag = value.get("tag")
        if tag is None or tag == "":
            return None
        if not isinstance(tag, str):
            raise ClientServiceError("invalid reverse value from 3x-ui")
        return {"tag": tag}

    @staticmethod
    def _normalize_enable(value):
        if isinstance(value, bool):
            return value
        if value in (1, "1", "true", "True"):
            return True
        if value in (0, "0", "false", "False", ""):
            return False
        raise ClientServiceError("invalid enable value from 3x-ui")

    @staticmethod
    def _normalize_integer(key, value):
        if isinstance(value, bool):
            raise ClientServiceError(f"invalid {key} value from 3x-ui")
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value or 0)
            except ValueError as exc:
                raise ClientServiceError(f"invalid {key} value from 3x-ui") from exc
        raise ClientServiceError(f"invalid {key} value from 3x-ui")

    @classmethod
    def _normalize_client_for_update(cls, raw_client):
        if not isinstance(raw_client, dict):
            raise ClientServiceError("invalid client value from 3x-ui")
        result = {}
        for key in cls._WRITABLE_CLIENT_FIELDS:
            if key not in raw_client:
                continue
            value = raw_client[key]
            if key == "allowedIPs":
                value = cls._normalize_allowed_ips(value)
            elif key == "reverse":
                value = cls._normalize_reverse(value)
            elif key == "enable":
                value = cls._normalize_enable(value)
            elif key in cls._INTEGER_CLIENT_FIELDS:
                value = cls._normalize_integer(key, value)
            elif key in cls._STRING_CLIENT_FIELDS:
                value = "" if value is None else str(value)
            result[key] = value
        if not result.get("email"):
            raise ClientServiceError("client email is required")
        return result

    def extend_client(self, email, days) -> ClientInfo:
        days = self._positive_days(days)
        now = self.now_provider()
        try:
            mobile_email = mobile_email_for(email)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        _, primary_obj = self._require_raw(email)
        target_expiry = (
            max(int(primary_obj["client"].get("expiryTime", 0) or 0), now)
            + days * 86400000
        )
        mobile_exists = self.get_client(mobile_email) is not None
        if mobile_exists:
            def synchronize_mobile_expiry(client):
                client["expiryTime"] = target_expiry
                client["limitHwid"] = 0
            try:
                self._update(mobile_email, synchronize_mobile_expiry)
            except ClientServiceError as exc:
                raise ReconciliationRequiredError(
                    "mobile expiry update failed; primary was not changed"
                ) from exc
        try:
            return self._update(
                email, lambda client: client.__setitem__("expiryTime", target_expiry)
            )
        except ClientServiceError as exc:
            if mobile_exists:
                raise ReconciliationRequiredError(
                    "mobile extended but primary expiry update failed"
                ) from exc
            raise

    def extend_bundle(self, email, days) -> ClientInfo:
        if days is not None:
            return self.extend_client(email, days)
        mobile_email = mobile_email_for(email)
        self._require_raw(email)
        mobile_exists = self.get_client(mobile_email) is not None
        if mobile_exists:
            def make_mobile_unlimited(client):
                client["expiryTime"] = 0
                client["limitHwid"] = 0
            try:
                self._update(mobile_email, make_mobile_unlimited)
            except ClientServiceError as exc:
                raise ReconciliationRequiredError(
                    "mobile unlimited update failed; primary was not changed"
                ) from exc
        try:
            return self._update(email, lambda client: client.__setitem__("expiryTime", 0))
        except ClientServiceError as exc:
            if mobile_exists:
                raise ReconciliationRequiredError(
                    "mobile made unlimited but primary expiry update failed"
                ) from exc
            raise

    def set_bundle_expiry(self, email, target_expiry_ms) -> ClientInfo:
        if type(target_expiry_ms) is not int or target_expiry_ms <= 0:
            raise ValidationError("expiry must be a positive timestamp")
        mobile_email = mobile_email_for(email)
        self._require_raw(email)
        mobile_exists = self.get_client(mobile_email) is not None
        if mobile_exists:
            def synchronize_mobile(client):
                client["expiryTime"] = target_expiry_ms
                client["limitHwid"] = 0
            try:
                self._update(mobile_email, synchronize_mobile)
            except ClientServiceError as exc:
                raise ReconciliationRequiredError(
                    "mobile expiry reconciliation failed; primary was not changed"
                ) from exc
        try:
            return self._update(
                email, lambda client: client.__setitem__("expiryTime", target_expiry_ms),
            )
        except ClientServiceError as exc:
            if mobile_exists:
                raise ReconciliationRequiredError(
                    "mobile expiry reconciled but primary update failed"
                ) from exc
            raise

    def enable_client(self, email) -> ClientInfo:
        return self._set_bundle_enabled(email, True)

    def disable_client(self, email) -> ClientInfo:
        return self._set_bundle_enabled(email, False)

    def set_bundle_enabled(self, email, enabled) -> ClientInfo:
        if not isinstance(enabled, bool):
            raise ValidationError("enabled must be boolean")
        return self._set_bundle_enabled(email, enabled)

    def _set_bundle_enabled(self, email, enabled):
        mobile_email = mobile_email_for(email)
        mobile_exists = self.get_client(mobile_email) is not None
        primary = self._update(email, lambda client: client.__setitem__("enable", enabled))
        if mobile_exists:
            def synchronize_mobile_enabled(client):
                client["enable"] = enabled
                client["limitHwid"] = 0
            try:
                self._update(mobile_email, synchronize_mobile_enabled)
            except ClientServiceError as exc:
                raise ReconciliationRequiredError(
                    "primary updated but mobile state synchronization failed"
                ) from exc
        return primary

    def get_mobile_traffic(self, email) -> TrafficInfo | None:
        mobile_email = mobile_email_for(email)
        if self.get_client(mobile_email) is None:
            return None
        return self.get_traffic(mobile_email)

    def set_hwid_limit(self, email, limit) -> ClientInfo:
        limit = self._hwid_limit(limit)
        mobile_email = mobile_email_for(email)
        mobile = self.get_client(mobile_email)
        primary = self._update(email, lambda client: client.__setitem__("limitHwid", limit))
        if mobile is not None and mobile.device_limit != 0:
            try:
                self._update(mobile_email, lambda client: client.__setitem__("limitHwid", 0))
            except ClientServiceError as exc:
                raise ReconciliationRequiredError(
                    "primary HWID updated but mobile synchronization failed"
                ) from exc
        return primary

    def get_bundle_devices(self, email) -> list[DeviceInfo]:
        primary = self.get_devices(email)
        mobile_email = mobile_email_for(email)
        mobile = self.get_devices(mobile_email) if self.get_client(mobile_email) else []
        unique = {}
        for device in primary + mobile:
            unique.setdefault(self._logical_device_key(device), device)
        return list(unique.values())

    @staticmethod
    def _logical_device_key(device):
        identity = (device.model, device.os_name, device.os_version, device.user_agent)
        return identity if any(identity) else ("device-id", device.id)

    def remove_bundle_device(self, email, device_id) -> None:
        if type(device_id) is not int:
            raise ValidationError("ID устройства должен быть числом")
        credentials = [email]
        mobile_email = mobile_email_for(email)
        if self.get_client(mobile_email):
            credentials.append(mobile_email)
        by_credential = {credential: self.get_devices(credential) for credential in credentials}
        target = next((item for devices in by_credential.values() for item in devices
                       if item.id == device_id), None)
        if target is None:
            raise ValidationError("устройство не найдено")
        target_key = self._logical_device_key(target)
        matching = [(credential, item.id) for credential, devices in by_credential.items()
                    for item in devices if self._logical_device_key(item) == target_key]
        completed = False
        for credential, matching_id in matching:
            try:
                self.remove_device(credential, matching_id)
                completed = True
            except ClientServiceError as exc:
                if completed:
                    raise ReconciliationRequiredError(
                        "device removed from only part of bundle"
                    ) from exc
                raise

    def reset_bundle_devices(self, email) -> None:
        mobile_email = mobile_email_for(email)
        mobile_exists = self.get_client(mobile_email) is not None
        self.reset_devices(email)
        if mobile_exists:
            try:
                self.reset_devices(mobile_email)
            except ClientServiceError as exc:
                raise ReconciliationRequiredError(
                    "primary devices reset but mobile reset failed"
                ) from exc

    def ensure_connection(self, email) -> str:
        _, obj = self._require_raw(email)
        sub_id = str(obj.get("client", {}).get("subId") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", sub_id):
            raise ClientServiceError("authoritative SUB_ID is missing or invalid")
        if self.issue_subscription is None:
            raise ClientServiceError("subscription issuer is not configured")
        try:
            page = self.issue_subscription(sub_id)
        except Exception as exc:
            raise ClientServiceError("subscription issuance failed") from exc
        if not page:
            raise ClientServiceError("subscription verification failed")
        return page

    def reissue_bundle_connection(self, email) -> str:
        return self.ensure_connection(email)

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
            if is_mobile_email(email):
                continue
            obj = self._call(lambda email=email: self.xui.get_client(email),
                             "ошибка получения клиента")
            if not obj:
                continue
            client = obj.get("client", {})
            expiry = int(client.get("expiryTime", 0) or 0)
            if client.get("enable", False) and expiry and now <= expiry <= limit:
                result.append(ExpiringClient(
                    email, expiry, (expiry - now) / 86400000,
                    enabled=True, expiry_text=self._expiry_text(expiry),
                ))
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

    def delete_client_bundle(self, email) -> DeleteClientResult:
        email = self.validate_email(email)
        mobile_email = mobile_email_for(email)
        warnings = []
        mobile = self.get_client(mobile_email)
        if mobile:
            result = self.delete_client(mobile_email)
            if result.file_cleanup_warning:
                warnings.append(result.file_cleanup_warning)
        try:
            result = self.delete_client(email)
        except ClientServiceError as exc:
            if mobile:
                raise ReconciliationRequiredError(
                    "mobile deleted but primary deletion failed"
                ) from exc
            raise
        if result.file_cleanup_warning:
            warnings.append(result.file_cleanup_warning)
        return DeleteClientResult(email, True, "; ".join(warnings) or None)
