#!/usr/bin/env python3

import sys
from pathlib import Path

try:
    from .integrations.happ import SubscriptionIssuerError, issue_subscription
except ImportError:  # Direct execution from the src directory.
    from integrations.happ import SubscriptionIssuerError, issue_subscription


OUTPUT_DIR = Path("/var/www/karina/connect")
PUBLIC_BASE = "https://vpn.parsekk.ru/connect"
SUBSCRIPTION_PREFIX = "https://vpn.parsekk.ru/sub/"


def main():
    if len(sys.argv) != 2:
        print("Использование:")
        print("  karina-issue https://vpn.parsekk.ru/sub/ID")
        raise SystemExit(1)
    subscription_url = sys.argv[1].strip()
    if not subscription_url.startswith(SUBSCRIPTION_PREFIX):
        print(
            "Ошибка: ожидается HTTPS-подписка вида https://vpn.parsekk.ru/sub/...",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        page = issue_subscription(
            subscription_url, output_dir=OUTPUT_DIR, public_base=PUBLIC_BASE,
        )
    except SubscriptionIssuerError as exc:
        detail = str(exc)
        if "Crypt5" in detail:
            print("Ошибка: Crypt5-ссылка не найдена.", file=sys.stderr)
            code = 5
        elif "SUB_ID" in detail or "путь" in detail:
            print("Ошибка: некорректный SUB_ID.", file=sys.stderr)
            code = 3
        else:
            print(f"Ошибка генератора Happ: {detail}", file=sys.stderr)
            code = 4
        raise SystemExit(code) from exc
    sub_id = subscription_url.rstrip("/").rsplit("/", 1)[-1]
    print("\nКарина VPN — выдача создана\n")
    print(f"SUB_ID:      {sub_id}")
    print(f"Страница:    {page}")
    print(f"QR:          {PUBLIC_BASE}/{sub_id}.png")
    print(f"Crypt5 файл: {OUTPUT_DIR / (sub_id + '.crypt5')}")
    print("\nCrypt5:")
    print((OUTPUT_DIR / (sub_id + ".crypt5")).read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
