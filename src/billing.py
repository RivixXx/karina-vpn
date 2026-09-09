"""Backward-compatible imports for the canonical billing implementation."""

try:
    from .models.billing import PLANS, Order, OrderStatus, Plan
    from .repositories.billing_repository import BillingRepository
    from .services.billing_service import BillingService
except ImportError:
    from models.billing import PLANS, Order, OrderStatus, Plan
    from repositories.billing_repository import BillingRepository
    from services.billing_service import BillingService

__all__ = [
    "BillingRepository", "BillingService", "Order", "OrderStatus", "Plan", "PLANS",
]
