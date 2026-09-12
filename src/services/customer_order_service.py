import time
import sqlite3

try:
    from ..models import OrderStatus
except ImportError:
    from models import OrderStatus
from .billing_service import BillingStateError, UnknownPlanError
from .errors import ReconciliationRequiredError


class CustomerOrderError(Exception):
    pass


class CustomerOrderService:
    """Application boundary for manual order confirmation and provisioning."""

    def __init__(self, billing, client_service, get_binding, create_binding,
                 *, now_provider=None):
        self.billing = billing
        self._client_service = client_service
        self._client_loaded = False
        self.get_binding = get_binding
        self.create_binding = create_binding
        self.now_provider = now_provider or (lambda: int(time.time()))
        self.billing.apply_order = lambda order_id: self.approve(order_id)[0]

    @property
    def client_service(self):
        if not self._client_loaded and callable(self._client_service):
            self._client_service = self._client_service()
        self._client_loaded = True
        return self._client_service

    @staticmethod
    def generated_email(tg_id):
        email = f"tg_{int(tg_id)}"
        if len(email) > 64:
            raise CustomerOrderError("Не удалось создать безопасное имя клиента")
        return email

    def create_request(self, tg_id, plan_id):
        plan = self.billing.plans.get(plan_id)
        if plan is None or not plan.enabled:
            raise UnknownPlanError("Неизвестный тариф")
        existing = self.billing.repository.get_pending_for_user(tg_id)
        if existing:
            return existing, False
        link = self.get_binding(tg_id)
        email = link["email"] if link else self.generated_email(tg_id)
        kind = "renewal" if link else "new"
        try:
            return self.billing.create_order(tg_id, email, plan_id, kind=kind), True
        except sqlite3.IntegrityError:
            existing = self.billing.repository.get_pending_for_user(tg_id)
            if existing:
                return existing, False
            raise

    def replace_request(self, tg_id, order_id, plan_id):
        current = self.get_request(tg_id, order_id)
        if current.status is not OrderStatus.PENDING:
            raise CustomerOrderError("Заявка недоступна")
        plan = self.billing.plans.get(plan_id)
        if plan is None or not plan.enabled:
            raise UnknownPlanError("Неизвестный тариф")
        cancelled = self.billing.repository.cancel_order(order_id)
        if cancelled is None:
            raise CustomerOrderError("Статус заявки изменился")
        link = self.get_binding(tg_id)
        email = link["email"] if link else self.generated_email(tg_id)
        kind = "renewal" if link else "new"
        return self.billing.create_order(tg_id, email, plan_id, kind=kind)

    def get_request(self, tg_id, order_id):
        order = self.billing.get_order(order_id)
        if order is None or order.tg_id != tg_id:
            raise CustomerOrderError("Заявка недоступна")
        return order

    def approve(self, order_id):
        try:
            with self.billing.repository.operation_lock():
                return self._approve(order_id)
        except OSError as exc:
            raise CustomerOrderError("Операция занята; повторите позже") from exc

    def _approve(self, order_id):
        order = self.billing.get_order(order_id)
        if order is None:
            raise CustomerOrderError("Заявка не найдена")
        if order.status is OrderStatus.COMPLETED:
            return order, False
        if order.status not in (OrderStatus.PENDING, OrderStatus.PAID):
            raise BillingStateError("Заявка уже закрыта")
        # Apply the purchased snapshot even if the catalog changes later.
        if order.days <= 0 or order.amount_rub <= 0:
            raise CustomerOrderError("Некорректные условия заявки")
        repository = self.billing.repository
        operation = repository.get_operation(order.id)
        link = self.get_binding(order.tg_id)
        if order.kind == "renewal":
            if not link or link["email"] != order.email:
                raise ReconciliationRequiredError("Telegram-привязка подписки отсутствует")
            email = order.email
            bundle = self.client_service.get_client_bundle(email)
            if bundle is None or bundle.mobile is None:
                raise ReconciliationRequiredError("Привязка указывает на отсутствующую подписку")
            if bundle.primary.expiry_time_ms == 0:
                raise CustomerOrderError("Бессрочная подписка не требует продления")
            if operation is None:
                if bundle.mobile.expiry_time_ms != bundle.primary.expiry_time_ms:
                    raise ReconciliationRequiredError("Сроки primary/mobile требуют сверки")
                original = bundle.primary.expiry_time_ms
                target = max(original, repository.reserved_referral_expiry(order.tg_id),
                             self.now_provider() * 1000) + order.days * 86400000
                operation = repository.prepare_operation(order, email, target, original, self.now_provider())
        else:
            email = self.generated_email(order.tg_id)
            if email != order.email or (link and link["email"] != email):
                raise ReconciliationRequiredError("Привязка новой подписки требует сверки")
            if operation is None:
                if self.client_service.get_client_bundle(email) is not None or link:
                    raise ReconciliationRequiredError("Найдена подписка без сохранённой операции")
                target = self.now_provider() * 1000 + order.days * 86400000
                operation = repository.prepare_operation(order, email, target, None, self.now_provider())
        if operation is None:
            raise BillingStateError("Статус заявки изменился")
        try:
            target = operation["target_expiry_ms"]
            if operation["email"] != email:
                raise ReconciliationRequiredError("Получатель операции изменился")
            if order.kind == "new":
                self.client_service.create_client_bundle(email, order.days, target_expiry_ms=target)
                if not link:
                    self.create_binding(order.tg_id, email)
            else:
                if any(client.expiry_time_ms not in (operation["original_expiry_ms"], target)
                       for client in (bundle.primary, bundle.mobile)):
                    raise ReconciliationRequiredError("Срок подписки изменён вне операции; нужна сверка")
                self.client_service.set_bundle_expiry(email, target)
            verified = self.client_service.get_client_bundle(email)
            if (verified is None or verified.mobile is None
                    or verified.primary.expiry_time_ms != target
                    or verified.mobile.expiry_time_ms != target):
                raise ReconciliationRequiredError("Сроки primary/mobile не подтверждены")
            completed = repository.complete_operation(order.id, self.now_provider())
            if completed is None:
                raise ReconciliationRequiredError("Подписка применена, но статус заявки не сохранён")
            return completed, True
        except Exception as exc:
            repository.record_operation_error(order.id, exc)
            raise

    def reject(self, order_id):
        order = self.billing.get_order(order_id)
        if order is None:
            raise CustomerOrderError("Заявка не найдена")
        if order.status is OrderStatus.CANCELLED:
            return order, False
        if order.status is not OrderStatus.PENDING:
            raise BillingStateError("Заявка уже закрыта")
        rejected = self.billing.repository.cancel_order(order.id)
        if rejected is None:
            raise BillingStateError("Статус заявки изменился")
        return rejected, True
