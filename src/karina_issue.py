#!/usr/bin/env python3

import html
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import qrcode

GENERATOR_URL = "https://crypto.happ.su/"
OUTPUT_DIR = Path("/var/www/karina/connect")
PUBLIC_BASE = "https://vpn.parsekk.ru/connect"

if len(sys.argv) != 2:
    print("Использование:")
    print("  karina-issue https://vpn.parsekk.ru/sub/ID")
    sys.exit(1)

subscription_url = sys.argv[1].strip()

if not subscription_url.startswith("https://vpn.parsekk.ru/sub/"):
    print(
        "Ошибка: ожидается HTTPS-подписка вида "
        "https://vpn.parsekk.ru/sub/...",
        file=sys.stderr,
    )
    sys.exit(2)

sub_id = subscription_url.rstrip("/").split("/")[-1]

if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", sub_id):
    print("Ошибка: некорректный SUB_ID.", file=sys.stderr)
    sys.exit(3)

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
        body = response.read().decode("utf-8", errors="replace")
except Exception as exc:
    print(f"Ошибка генератора Happ: {exc}", file=sys.stderr)
    sys.exit(4)

match = re.search(r'href="(happ://crypt5/[^"]+)"', body)

if not match:
    print("Ошибка: Crypt5-ссылка не найдена.", file=sys.stderr)
    sys.exit(5)

crypt5 = match.group(1)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

crypt_file = OUTPUT_DIR / f"{sub_id}.crypt5"
qr_file = OUTPUT_DIR / f"{sub_id}.png"
html_file = OUTPUT_DIR / f"{sub_id}.html"

crypt_file.write_text(crypt5, encoding="utf-8")

qr = qrcode.QRCode(
    version=None,
    error_correction=qrcode.constants.ERROR_CORRECT_M,
    box_size=10,
    border=4,
)
qr.add_data(crypt5)
qr.make(fit=True)
qr.make_image(fill_color="black", back_color="white").save(qr_file)

page = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>Карина VPN</title>
<style>
* {{
    box-sizing: border-box;
}}
body {{
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f6f7fb;
    color: #202124;
}}
.card {{
    width: 100%;
    max-width: 430px;
    background: white;
    border-radius: 24px;
    padding: 30px 24px;
    box-shadow: 0 16px 50px rgba(0,0,0,.10);
    text-align: center;
}}
.logo {{
    font-size: 46px;
    margin-bottom: 8px;
}}
h1 {{
    margin: 0 0 6px;
    font-size: 28px;
}}
.subtitle {{
    margin: 0 0 24px;
    color: #6b7280;
    line-height: 1.5;
}}
.qr {{
    width: 230px;
    height: 230px;
    max-width: 100%;
    margin: 4px auto 22px;
    display: block;
}}
.button {{
    display: block;
    width: 100%;
    padding: 15px 18px;
    border-radius: 14px;
    background: #7657ff;
    color: white;
    text-decoration: none;
    font-weight: 700;
    font-size: 17px;
}}
.help {{
    margin-top: 18px;
    font-size: 14px;
    line-height: 1.55;
    color: #6b7280;
}}
.support {{
    margin-top: 18px;
}}
.support a {{
    color: #7657ff;
    text-decoration: none;
    font-weight: 600;
}}
</style>
</head>
<body>
<div class="card">
    <div class="logo">💗</div>
    <h1>Карина VPN</h1>

    <p class="subtitle">
        Защищённое подключение к интернету
    </p>

    <img
        class="qr"
        src="{html.escape(sub_id)}.png"
        alt="QR-код Карина VPN"
    >

    <a class="button" href="{html.escape(crypt5, quote=True)}">
        📱 Подключить Карина VPN
    </a>

    <div class="help">
        На телефоне нажмите кнопку выше.<br>
        На другом устройстве отсканируйте QR-код в приложении Happ.
    </div>

    <div class="support">
        <a href="https://t.me/rivixxx">
            💬 Поддержка
        </a>
    </div>
</div>
</body>
</html>
"""

html_file.write_text(page, encoding="utf-8")

print()
print("Карина VPN — выдача создана")
print()
print(f"SUB_ID:      {sub_id}")
print(f"Страница:    {PUBLIC_BASE}/{sub_id}.html")
print(f"QR:          {PUBLIC_BASE}/{sub_id}.png")
print(f"Crypt5 файл: {crypt_file}")
print()
print("Crypt5:")
print(crypt5)
