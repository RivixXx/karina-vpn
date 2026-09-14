import argparse
import json
import logging
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from ..app_config import load_config
    from ..application import build_client_service
    from ..repositories import BillingRepository
    from ..services import BillingService, CustomerOrderService, TelegramBindingStore
    from .yookassa import YooKassaClient, YooKassaError, YooKassaPaymentService
except ImportError:
    from app_config import load_config
    from application import build_client_service
    from repositories import BillingRepository
    from services import BillingService, CustomerOrderService, TelegramBindingStore
    from payments.yookassa import YooKassaClient, YooKassaError, YooKassaPaymentService


LOGGER = logging.getLogger(__name__)


def build_service(config_path, db_path):
    config = load_config(config_path)
    if not config.yookassa_shop_id or not config.yookassa_secret_key or not config.yookassa_return_url:
        raise YooKassaError("YooKassa is not configured")
    repository = BillingRepository(db_path)
    repository.init_schema()
    billing = BillingService(repository, build_client_service)

    def connect():
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        return db

    bindings = TelegramBindingStore(connect)
    CustomerOrderService(billing, build_client_service, bindings.get, bindings.create)
    return YooKassaPaymentService(
        billing, YooKassaClient(config.yookassa_shop_id, config.yookassa_secret_key),
        config.yookassa_return_url,
    )


def handler_factory(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "KarinaWebhook/1"

        def do_GET(self):
            if self.path == "/healthz":
                self._reply(200, {"status": "ok"})
            else:
                self._reply(404, {"error": "not_found"})

        def do_POST(self):
            if self.path != "/webhooks/yookassa":
                self._reply(404, {"error": "not_found"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 65536:
                    raise ValueError("invalid body size")
                event = json.loads(self.rfile.read(size).decode("utf-8"))
                service.handle_webhook(event)
                self._reply(200, {"status": "ok"})
            except (ValueError, UnicodeError, json.JSONDecodeError, YooKassaError):
                LOGGER.warning("Rejected YooKassa webhook", exc_info=True)
                self._reply(400, {"error": "invalid_notification"})
            except Exception:
                LOGGER.exception("YooKassa webhook processing failed")
                self._reply(500, {"error": "temporary_failure"})

        def log_message(self, format, *args):
            LOGGER.info("webhook request %s", self.requestline.split(" ", 1)[0])

        def _reply(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Karina VPN YooKassa webhook")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--config", type=Path, default=Path("/etc/karina-vpn/config.env"))
    parser.add_argument("--database", type=Path, default=Path("/opt/karina-bot/karina.db"))
    args = parser.parse_args(argv)
    if args.bind not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("webhook must bind to loopback")
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer((args.bind, args.port), handler_factory(
        build_service(args.config, args.database)))
    server.serve_forever()


if __name__ == "__main__":
    main()
