"""Compatibility import for the former Happ/Crypt5 issuer module."""

from ..subscription import SubscriptionIssuerError, issue_subscription

__all__ = ["SubscriptionIssuerError", "issue_subscription"]
