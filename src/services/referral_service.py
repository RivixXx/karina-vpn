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
        if order.status is not OrderStatus.COMPLETED:
            return None
        referral = self.repository.get_by_referred(order.tg_id)
        if referral is None:
            return None
        binding = self.get_binding(referral["referrer_tg_id"])
        if not binding:
            raise ReconciliationRequiredError("referrer subscription binding is missing")
        client_service = (self._client_service() if callable(self._client_service)
                          else self._client_service)
        bundle = client_service.get_client_bundle(binding["email"])
        if bundle is None:
            raise ReconciliationRequiredError("referrer subscription is missing")
        reward = self.repository.prepare_reward(
            order.tg_id, order.id, bundle.primary.expiry_time_ms, self.REWARD_DAYS,
        )
        if reward is None:
            return None
        if reward["applied_at"] is None:
            primary = client_service.set_bundle_expiry(
                binding["email"], reward["target_expiry_ms"],
            )
            if primary.expiry_time_ms != reward["target_expiry_ms"]:
                raise ReconciliationRequiredError("referral expiry verification failed")
            reward = self.repository.mark_applied(reward["id"])
        return reward
