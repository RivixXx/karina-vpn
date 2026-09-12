"""Persistent effects are retried independently of payment provisioning."""
import asyncio
import logging

try:
    from .avatar_scheduler import AvatarApplication
except ImportError:
    from avatar_scheduler import AvatarApplication

LOGGER = logging.getLogger(__name__)
TASK_KEY = "payment_effects_task"


async def delivery_loop(application):
    while True:
        try:
            await application.bot_data["deliver_payment_effects"](application)
        except Exception:
            LOGGER.warning("Payment delivery cycle failed", exc_info=True)
        await asyncio.sleep(30)


async def stop_delivery(application):
    task = application.bot_data.pop(TASK_KEY, None)
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


class ServiceApplication(AvatarApplication):
    async def start(self):
        await super().start()
        self.bot_data[TASK_KEY] = self.create_task(delivery_loop(self), name="karina-payment-effects")

    async def stop(self):
        await stop_delivery(self)
        await super().stop()

    async def shutdown(self):
        await stop_delivery(self)
        await super().shutdown()
