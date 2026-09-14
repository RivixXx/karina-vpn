import base64
import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from ..models import OrderStatus
    from ..services.billing_service import BillingStateError
except ImportError:
    from models import OrderStatus
    from services.billing_service import BillingStateError


LOGGER = logging.getLogger(__name__)
API_BASE = "https://api.yookassa.ru/v3"


class YooKassaError(Exception):
    pass


@dataclass(frozen=True)
class PaymentLink:
    payment_id: str
    confirmation_url: str


def _stdlib_transport(method, url, headers, body=None):
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise YooKassaError("YooKassa API request failed") from exc


class YooKassaClient:
    def __init__(self, shop_id, secret_key, *, transport=None, api_base=API_BASE):
        if not shop_id or not secret_key:
            raise YooKassaError("YooKassa credentials are missing")
        self.api_base = api_base.rstrip("/")
        self.transport = transport or _stdlib_transport
        token = base64.b64encode(f"{shop_id}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Accept": "application/json"}

    def create_payment(self, payload, idempotence_key):
        headers = {**self.headers, "Content-Type": "application/json",
                   "Idempotence-Key": idempotence_key}
        return self.transport("POST", f"{self.api_base}/payments", headers,
                              json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def get_payment(self, payment_id):
        if not payment_id or "/" in payment_id:
            raise YooKassaError("Invalid payment id")
        return self.transport("GET", f"{self.api_base}/payments/{payment_id}", self.headers)

    def cancel_payment(self, payment_id, idempotence_key):
        if not payment_id or "/" in payment_id:
            raise YooKassaError("Invalid payment id")
        headers = {**self.headers, "Content-Type": "application/json",
                   "Idempotence-Key": idempotence_key}
        return self.transport("POST", f"{self.api_base}/payments/{payment_id}/cancel",
                              headers, b"{}")


class YooKassaPaymentService:
    provider = "yookassa"

    def __init__(self, billing, client, return_url):
        if not return_url.startswith("https://"):
            raise YooKassaError("HTTPS return URL is required")
        self.billing = billing
        self.client = client
        self.return_url = return_url

    def create_payment(self, order_id, *, payment_method="sbp"):
        order = self.billing.get_order(order_id)
        if order is None or order.status is not OrderStatus.PENDING:
            raise YooKassaError("Order is not awaiting payment")
        session = self.billing.repository.get_payment_session(order.id)
        if session and session["status"] in {"pending", "waiting_for_capture"}:
            current = self.client.get_payment(session["payment_id"])
            current_status = current.get("status")
            if current_status in {"pending", "waiting_for_capture"}:
                current_method = (current.get("payment_method") or {}).get("type")
                if current_method and current_method != payment_method:
                    self.client.cancel_payment(session["payment_id"], f"cancel-{session['payment_id']}")
                    self.billing.repository.set_payment_session_status(session["payment_id"], "canceled")
                    current_status = "canceled"
                else:
                    self.billing.repository.set_payment_session_status(session["payment_id"], current_status)
                    return PaymentLink(session["payment_id"], session["confirmation_url"])
            self.billing.repository.set_payment_session_status(session["payment_id"],
                                                               current_status or "unknown")
            if current_status == "succeeded":
                raise YooKassaError("Payment is already succeeded")
            if current_status != "canceled":
                raise YooKassaError("Payment state is not safe to replace")
        attempt = (session["attempt"] + 1) if session else 1
        key = f"karina-{order.id}-{attempt}"
        payload = {
            "amount": {"value": f"{order.amount_rub:.2f}", "currency": "RUB"},
            "capture": True,
            "payment_method_data": {"type": payment_method},
            "confirmation": {"type": "redirect", "return_url": self.return_url},
            "description": f"Karina VPN, заказ {order.id}",
            "metadata": {"order_id": order.id},
        }
        payment = self.client.create_payment(payload, key)
        try:
            payment_id = payment["id"]
            confirmation_url = payment["confirmation"]["confirmation_url"]
        except (KeyError, TypeError) as exc:
            raise YooKassaError("Incomplete payment response") from exc
        if not confirmation_url.startswith("https://"):
            raise YooKassaError("Unsafe confirmation URL")
        saved = self.billing.repository.save_payment_session(
            order.id, self.provider, payment_id, confirmation_url, attempt,
        )
        return PaymentLink(saved["payment_id"], saved["confirmation_url"])

    def cancel_payment(self, order_id):
        session = self.billing.repository.get_payment_session(order_id)
        if not session or session["status"] not in {"pending", "waiting_for_capture"}:
            return False
        current = self.client.get_payment(session["payment_id"])
        if current.get("status") == "canceled":
            self.billing.repository.set_payment_session_status(session["payment_id"], "canceled")
            return False
        if current.get("status") not in {"pending", "waiting_for_capture"}:
            raise YooKassaError("Payment can no longer be canceled")
        canceled = self.client.cancel_payment(session["payment_id"], f"cancel-{session['payment_id']}")
        if canceled.get("status") != "canceled":
            raise YooKassaError("YooKassa did not cancel payment")
        self.billing.repository.set_payment_session_status(session["payment_id"], "canceled")
        return True

    def handle_webhook(self, event):
        try:
            payment_id = event["object"]["id"]
        except (KeyError, TypeError) as exc:
            raise YooKassaError("Webhook has no payment id") from exc
        payment = self.client.get_payment(payment_id)
        session = self.billing.repository.get_payment_session_by_payment_id(payment_id)
        if session is None:
            raise YooKassaError("Unknown payment")
        order = self.billing.get_order(session["order_id"])
        if order is None:
            raise YooKassaError("Unknown order")
        self._verify(payment, order, payment_id)
        if order.status is OrderStatus.COMPLETED:
            self.billing.repository.set_payment_session_status(payment_id, "succeeded")
            return False
        if order.status is OrderStatus.PENDING:
            try:
                self.billing.mark_paid(order.id, self.provider, payment_id)
            except BillingStateError:
                order = self.billing.get_order(order.id)
                if order.status not in {OrderStatus.PAID, OrderStatus.COMPLETED}:
                    raise
        elif order.status is not OrderStatus.PAID:
            raise YooKassaError("Order is closed")
        self.billing.repository.set_payment_session_status(payment_id, "succeeded")
        self.billing.repository.record_receipt_warning(order.id, payment_id)
        LOGGER.warning("YooKassa payment succeeded; NPD receipt requires operator action")
        if self.billing.get_order(order.id).status is OrderStatus.COMPLETED:
            return False
        result = self.billing.apply_paid_order(order.id)
        return result[1] if isinstance(result, tuple) else True

    @staticmethod
    def _verify(payment, order, payment_id):
        try:
            amount = Decimal(payment["amount"]["value"])
            valid = (
                payment["id"] == payment_id
                and payment["status"] == "succeeded"
                and payment["paid"] is True
                and amount == Decimal(order.amount_rub)
                and payment["amount"]["currency"] == "RUB"
                and payment["metadata"]["order_id"] == order.id
            )
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise YooKassaError("Incomplete verified payment") from exc
        if not valid:
            raise YooKassaError("Payment does not match order")
