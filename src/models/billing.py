from dataclasses import dataclass
from enum import Enum


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True)
class Plan:
    id: str
    title: str
    days: int
    price_rub: int
    badge: str | None = None
    enabled: bool = True


@dataclass(frozen=True)
class Order:
    id: str
    tg_id: int
    email: str
    plan_id: str
    days: int
    amount_rub: int
    status: OrderStatus
    provider: str | None
    provider_payment_id: str | None
    created_at: int
    paid_at: int | None
    applied_at: int | None
    kind: str = "renewal"
    plan_title: str | None = None


PLANS = (
    Plan("m1", "1 месяц", 30, 199),
    Plan("m3", "3 месяца", 90, 499, "Популярный"),
    Plan("m6", "6 месяцев", 180, 899),
    Plan("y1", "1 год", 365, 1499, "Выгодно"),
)
