from .errors import ReconciliationRequiredError
try:
    from ..models import OrderStatus
except ImportError:
    from models import OrderStatus


class ReferralService:
    REWARD_DAYS = 3

    def __init__(self, repository, client_service, get_binding):
        self.repository = repository
        self._client_service = client_service
        self.get_binding = get_binding

    def profile(self, tg_id):
        return self.repository.get_or_create_profile(tg_id)

    def attribute(self, code, user):
        return self.repository.attribute(
            code, user.id, getattr(user, "username", ""), getattr(user, "first_name", ""),
        )

    def qualify_after_first_successful_payment(self, order):
        with self.repository.operation_lock():
            return self._qualify(order)

    def _qualify(self, order):
        if order.status is not OrderStatus.COMPLETED:
            return None
        first = self.repository.first_completed_order(order.tg_id)
        if first is not None and first != order.id:
            return None
        referral = self.repository.get_by_referred(order.tg_id)
        if referral is None:
            return None
        if self.repository.payment_in_progress(referral["referrer_tg_id"]):
            raise ReconciliationRequiredError("referrer payment is still being applied")
        binding = self.get_binding(referral["referrer_tg_id"])
        if not binding:
            raise ReconciliationRequiredError("referrer subscription binding is missing")
        client_service = (self._client_service() if callable(self._client_service)
                          else self._client_service)
        bundle = client_service.get_client_bundle(binding["email"])
        if bundle is None:
            raise ReconciliationRequiredError("referrer subscription is missing")
        if bundle.mobile is None:
            raise ReconciliationRequiredError("referrer mobile subscription is missing")
        current_expiry = bundle.primary.expiry_time_ms
        reward = self.repository.prepare_reward(
            order.tg_id, order.id, bundle.primary.expiry_time_ms, self.REWARD_DAYS,
        )
        if reward is None:
            return None
        if reward["applied_at"] is None:
            # Retrying an old reward must not shorten a subsequently renewed or
            # unlimited subscription. A saved target is an entitlement floor.
            if bundle.primary.expiry_time_ms == 0:
                if bundle.mobile.expiry_time_ms != 0:
                    raise ReconciliationRequiredError("unlimited bundle expiry is inconsistent")
                return dict(self.repository.mark_applied(reward["id"]), current_expiry_ms=0)
            target = max(bundle.primary.expiry_time_ms, reward["target_expiry_ms"])
            primary = client_service.set_bundle_expiry(
                binding["email"], target,
            )
            verified = client_service.get_client_bundle(binding["email"])
            if (primary.expiry_time_ms != target or verified is None or verified.mobile is None
                    or verified.primary.expiry_time_ms != target or verified.mobile.expiry_time_ms != target):
                raise ReconciliationRequiredError("referral expiry verification failed")
            reward = self.repository.mark_applied(reward["id"])
            current_expiry = target
        return dict(reward, current_expiry_ms=current_expiry)
