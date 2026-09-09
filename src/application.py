from pathlib import Path
from typing import Callable

try:
    from .app_config import load_config
    from .integrations.subscription import issue_subscription as issue_direct_subscription
    from .integrations.xui import XUIClient
    from .services import ClientService
except ImportError:  # Direct execution from the src directory.
    from app_config import load_config
    from integrations.subscription import issue_subscription as issue_direct_subscription
    from integrations.xui import XUIClient
    from services import ClientService


DEFAULT_CONFIG_PATH = Path("/etc/karina-vpn/config.env")
DEFAULT_CONNECT_DIR = Path("/var/www/karina/connect")


def build_client_service(
    config_path=DEFAULT_CONFIG_PATH,
    *,
    issue_subscription: Callable[[str], str | None] | None = None,
    connect_dir=None,
) -> ClientService:
    config = load_config(config_path)
    connect_dir = Path(connect_dir) if connect_dir is not None else config.connect_dir
    xui = XUIClient(config)
    xui.login()
    if issue_subscription is None:
        issue_subscription = lambda sub_id: issue_direct_subscription(
            f"{config.sub_base}/{sub_id}",
            output_dir=connect_dir,
            public_base=config.connect_base,
        )
    return ClientService(
        config=config,
        xui=xui,
        issue_subscription=issue_subscription,
        connect_dir=connect_dir,
    )
