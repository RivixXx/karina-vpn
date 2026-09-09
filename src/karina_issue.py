#!/usr/bin/env python3

import argparse
import sys
from pathlib import Path

try:
    from .app_config import ConfigError, load_config
    from .integrations.subscription import SubscriptionIssuerError, issue_subscription
except ImportError:  # Direct execution from the src directory.
    from app_config import ConfigError, load_config
    from integrations.subscription import SubscriptionIssuerError, issue_subscription


DEFAULT_CONFIG = Path("/etc/karina-vpn/config.env")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="karina-issue",
        description="Create a direct HTTPS subscription QR and connection page.",
    )
    result.add_argument("subscription_url", nargs="?", help="authoritative HTTPS subscription URL")
    result.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help=argparse.SUPPRESS)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not args.subscription_url:
        parser().print_usage()
        return 1
    try:
        config = load_config(args.config)
        expected_prefix = f"{config.sub_base}/"
        if not args.subscription_url.startswith(expected_prefix):
            raise SubscriptionIssuerError("URL подписки не соответствует SUB_BASE")
        page = issue_subscription(
            args.subscription_url,
            output_dir=config.connect_dir,
            public_base=config.connect_base,
        )
    except (ConfigError, SubscriptionIssuerError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2

    print(page)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
