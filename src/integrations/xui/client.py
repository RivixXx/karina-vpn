import json
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

try:
    from ...app_config import KarinaConfig
except ImportError:  # Direct CLI execution from the src directory.
    from app_config import KarinaConfig
from .errors import (
    XUIAuthError,
    XUIHTTPError,
    XUIOperationError,
    XUIResponseError,
    XUITransportError,
)


class XUIClient:
    def __init__(self, config: KarinaConfig):
        self.config = config
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.csrf = None

    def _safe_detail(self, value):
        detail = str(value)[:500]
        for credential in (self.config.xui_pass, self.config.xui_user):
            if credential:
                detail = detail.replace(credential, "[redacted]")
        return detail

    def request(self, method, path, payload=None, csrf=False):
        headers = {"User-Agent": "KarinaVPN-Automation/1.1", "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        request = urllib.request.Request(
            self.config.xui_base + path, data=body, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read(500).decode("utf-8", errors="replace")
            detail = self._safe_detail(raw or exc.reason)
            raise XUIHTTPError(f"HTTP {exc.code}: {detail}") from exc
        except Exception as exc:
            raise XUITransportError(
                f"ошибка обращения к 3x-ui ({type(exc).__name__})"
            ) from exc
        if not raw:
            return {}
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise XUIResponseError(f"3x-ui вернул не JSON: {self._safe_detail(raw)}") from exc
        if not isinstance(result, dict):
            raise XUIResponseError("3x-ui вернул неожиданный формат JSON")
        return result

    def _operation(self, result, fallback):
        if not result.get("success"):
            raise XUIOperationError(self._safe_detail(result.get("msg") or fallback))
        return result.get("obj")

    def login(self):
        csrf_result = self.request("GET", "/csrf-token")
        if not csrf_result.get("success") or not csrf_result.get("obj"):
            raise XUIAuthError("не удалось получить CSRF")
        self.csrf = csrf_result["obj"]
        result = self.request("POST", "/login", {
            "username": self.config.xui_user,
            "password": self.config.xui_pass,
            "twoFactorCode": "",
        }, csrf=True)
        if not result.get("success"):
            raise XUIAuthError(self._safe_detail(result.get("msg") or "не удалось войти в 3x-ui"))

    def get_client(self, email):
        result = self.request("GET", "/panel/api/clients/get/" + urllib.parse.quote(email, safe=""))
        return result.get("obj") if result.get("success") else None

    def list_inbounds(self):
        return self._operation(self.request("GET", "/panel/api/inbounds/list"),
                               "не удалось получить список inbound") or []

    def create_client(self, payload):
        return self._operation(self.request("POST", "/panel/api/clients/add", payload, csrf=True),
                               "не удалось создать клиента")

    def update_client(self, email, payload):
        return self._operation(self.request("POST", "/panel/api/clients/update/" +
                               urllib.parse.quote(email, safe=""), payload, csrf=True),
                               "не удалось обновить клиента")

    def delete_client(self, email):
        self._operation(self.request("POST", "/panel/api/clients/del/" +
                        urllib.parse.quote(email, safe=""), {}, csrf=True),
                        "не удалось удалить клиента")

    def get_hwids(self, email):
        return self._operation(self.request("POST", "/panel/api/clients/hwids/" +
                               urllib.parse.quote(email, safe=""), {}, csrf=True),
                               "не удалось получить список устройств") or []

    def delete_hwid(self, email, device_id):
        self._operation(self.request("DELETE", "/panel/api/clients/hwids/" +
                        urllib.parse.quote(email, safe="") + f"/{device_id}", None, csrf=True),
                        "не удалось удалить устройство")

    def reset_hwids(self, email):
        self._operation(self.request("DELETE", "/panel/api/clients/hwids/" +
                        urllib.parse.quote(email, safe=""), None, csrf=True),
                        "не удалось сбросить устройства")
