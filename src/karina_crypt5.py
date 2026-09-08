#!/usr/bin/env python3

import re
import sys
import urllib.parse
import urllib.request

GENERATOR_URL = "https://crypto.happ.su/"

if len(sys.argv) != 2:
    print("Использование:")
    print("  karina-crypt5 https://vpn.parsekk.ru/sub/SUB_ID")
    sys.exit(1)

subscription_url = sys.argv[1].strip()

if not subscription_url.startswith("https://"):
    print("Ошибка: ссылка подписки должна начинаться с https://", file=sys.stderr)
    sys.exit(2)

data = urllib.parse.urlencode({
    "url": subscription_url
}).encode("utf-8")

request = urllib.request.Request(
    GENERATOR_URL,
    data=data,
    method="POST",
    headers={
        "User-Agent": "KarinaVPN/1.0",
        "Content-Type": "application/x-www-form-urlencoded",
    },
)

try:
    with urllib.request.urlopen(request, timeout=20) as response:
        html = response.read().decode("utf-8", errors="replace")
except Exception as exc:
    print(f"Ошибка обращения к генератору Happ: {exc}", file=sys.stderr)
    sys.exit(3)

match = re.search(r'href="(happ://crypt5/[^"]+)"', html)

if not match:
    print("Ошибка: Crypt5-ссылка не найдена в ответе Happ.", file=sys.stderr)
    sys.exit(4)

print(match.group(1))
