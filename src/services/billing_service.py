import secrets
import time

try:
    from ..models import Order, OrderStatus, PLANS
except ImportError:
    from models import Order, OrderStatus, PLANS


class BillingError(Exception):
    pass


class UnknownPlanError(BillingError):
    pass


class OrderNotFoundError(BillingError):
    pass


class BillingAccessError(BillingError):
    pass


class BillingStateError(BillingError):
    pass


class BillingService:
    def __init__(self, repository, client_service, plans=PLANS, *, now_provider=None,
                 token_factory=None):
        self.repository = repository
        self.client_service = client_service
        self.plans = {plan.id: plan for plan in plans}
        self.now_provider = now_provider or (lambda: int(time.time()))
        self.token_factory = token_factory or (lambda: secrets.token_hex(8).upper())

    def list_plans(self):
        return [plan for plan in self.plans.values() if plan.enabled]

    def create_order(self, tg_id, email, plan_id, *, kind="renewal"):
        plan = self.plans.get(plan_id)
        if plan is None or not plan.enabled:
            raise UnknownPlanError("Неизвестный тариф")
        order = Order(
            id=f"KV-{self.token_factory()}", tg_id=tg_id, email=email,
            plan_id=plan.id, days=plan.days, amount_rub=plan.price_rub,
            status=OrderStatus.PENDING, provider=None, provider_payment_id=None,
            created_at=self.now_provider(), paid_at=None, applied_at=None,
            kind=kind, plan_title=plan.title,
        )
        return self.repository.create_order(order, plan.title)

    def get_order(self, order_id):
        return self.repository.get_order(order_id)

    def list_orders(self, tg_id):
        return self.repository.list_user_orders(tg_id)

    def cancel_order(self, order_id, tg_id):
        order = self._require(order_id)
        if order.tg_id != tg_id:
            raise BillingAccessError("Заказ принадлежит другому пользователю")
        if order.status is not OrderStatus.PENDING:
            raise BillingStateError("Отменить можно только ожидающий оплаты заказ")
        return self.repository.cancel_order(order_id)

    def mark_paid(self, order_id, provider, provider_payment_id):
        order = self._require(order_id)
        if order.status is not OrderStatus.PENDING:
            raise BillingStateError("Заказ не ожидает оплаты")
        if not provider or not provider_payment_id:
            raise BillingStateError("Payment reference is required")
        result = self.repository.mark_paid(order_id, self.now_provider(), provider, provider_payment_id)
        if result is None:
            raise BillingStateError("Order state changed")
        return result

    def mark_failed(self, order_id):
        order = self._require(order_id)
        if order.status not in {OrderStatus.PENDING, OrderStatus.PAID}:
            raise BillingStateError("Заказ нельзя пометить неуспешным")
        result = self.repository.mark_failed(order_id, order.status)
        if result is None:
            raise BillingStateError("Начатую операцию нужно восстановить, а не закрывать")
        return result

    def apply_paid_order(self, order_id):
        order = self._require(order_id)
        if order.status is OrderStatus.COMPLETED:
            return order
        if order.status is not OrderStatus.PAID:
            raise BillingStateError("Применить можно только оплаченный заказ")
        apply_order = getattr(self, "apply_order", None)
        if apply_order is None:
            raise BillingStateError("Применение оплаты требует CustomerOrderService")
        return apply_order(order_id)

    def _require(self, order_id):
        order = self.repository.get_order(order_id)
        if order is None:
            raise OrderNotFoundError("Заказ не найден")
        return order
