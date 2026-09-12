import time

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
        self.client_service = client_service
        self.get_binding = get_binding
        self.create_binding = create_binding
        self.now_provider = now_provider or (lambda: int(time.time()))

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
        return self.billing.create_order(tg_id, email, plan_id, kind=kind), True

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
        order = self.billing.get_order(order_id)
        if order is None:
            raise CustomerOrderError("Заявка не найдена")
        if order.status is OrderStatus.COMPLETED:
            return order, False
        if order.status is not OrderStatus.PENDING:
            raise BillingStateError("Заявка уже закрыта")
        plan = self.billing.plans.get(order.plan_id)
        if plan is None or not plan.enabled:
            raise UnknownPlanError("Тариф недоступен")

        link = self.get_binding(order.tg_id)
        if order.kind == "renewal":
            if not link:
                raise ReconciliationRequiredError("Telegram-привязка подписки отсутствует")
            email = link["email"]
            bundle = self.client_service.get_client_bundle(email)
            if bundle is None:
                raise ReconciliationRequiredError("Привязка указывает на отсутствующую подписку")
            if bundle.primary.expiry_time_ms == 0:
                raise CustomerOrderError("Бессрочная подписка не требует продления")
            self.client_service.extend_bundle(email, plan.days)
        else:
            email = self.generated_email(order.tg_id)
            existing_bundle = self.client_service.get_client_bundle(email)
            if link:
                if link["email"] != email or existing_bundle is None:
                    raise ReconciliationRequiredError("Состояние новой подписки требует сверки")
                # A previous attempt provisioned and bound successfully but failed
                # while recording completion. Resume without extending or rotating.
            else:
                if existing_bundle is not None:
                    raise ReconciliationRequiredError("Найдена подписка без Telegram-привязки")
                self.client_service.create_client_bundle(email, plan.days)
                try:
                    self.create_binding(order.tg_id, email)
                except Exception as exc:
                    raise ReconciliationRequiredError(
                        "Подписка создана, но Telegram-привязка не завершена"
                    ) from exc

        completed = self.billing.repository.approve_pending(order.id, self.now_provider())
        if completed is None:
            raise ReconciliationRequiredError("Подписка применена, но статус заявки не сохранён")
        return completed, True

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
