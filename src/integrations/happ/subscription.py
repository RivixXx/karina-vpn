import html
import re
import urllib.parse
import urllib.request
from pathlib import Path


GENERATOR_URL = "https://crypto.happ.su/"


class SubscriptionIssuerError(Exception):
    pass


def issue_subscription(
    subscription_url: str,
    *,
    output_dir: Path | str,
    public_base: str,
    opener=None,
    qr_writer=None,
) -> str:
    parsed = urllib.parse.urlsplit(subscription_url)
    decoded_path = urllib.parse.unquote(parsed.path)
    segments = decoded_path.split("/")
    sub_id = segments[-1] if segments else ""
    if parsed.scheme != "https" or not parsed.netloc or any(
        segment in {".", ".."} for segment in segments
    ):
        raise SubscriptionIssuerError("некорректный URL подписки")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", sub_id):
        raise SubscriptionIssuerError("некорректный SUB_ID")

    directory = Path(output_dir).resolve()
    targets = {suffix: directory / f"{sub_id}{suffix}"
               for suffix in (".crypt5", ".png", ".html")}
    if any(not path.resolve().is_relative_to(directory) for path in targets.values()):
        raise SubscriptionIssuerError("небезопасный путь результата")

    request = urllib.request.Request(
        GENERATOR_URL,
        data=urllib.parse.urlencode({"url": subscription_url}).encode("utf-8"),
        method="POST",
        headers={
            "User-Agent": "KarinaVPN/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    open_request = opener or urllib.request.urlopen
    try:
        with open_request(request, timeout=20) as response:
            body = response.read().decode("utf-8", errors="replace")
    except Exception as exc:
        raise SubscriptionIssuerError(f"ошибка генератора Happ: {type(exc).__name__}") from exc

    match = re.search(r'href="(happ://crypt5/[^"]+)"', body)
    if not match:
        raise SubscriptionIssuerError("Crypt5-ссылка не найдена")
    crypt5 = match.group(1)

    directory.mkdir(parents=True, exist_ok=True)
    targets[".crypt5"].write_text(crypt5, encoding="utf-8")
    if qr_writer is None:
        import qrcode
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(crypt5)
        qr.make(fit=True)
        qr.make_image(fill_color="black", back_color="white").save(targets[".png"])
    else:
        qr_writer(crypt5, targets[".png"])

    page = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow,noarchive">
<title>Карина VPN</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7fb; color: #202124; }}
.card {{ width: 100%; max-width: 430px; background: white; border-radius: 24px; padding: 30px 24px; box-shadow: 0 16px 50px rgba(0,0,0,.10); text-align: center; }}
.logo {{ font-size: 46px; margin-bottom: 8px; }}
h1 {{ margin: 0 0 6px; font-size: 28px; }}
.subtitle {{ margin: 0 0 24px; color: #6b7280; line-height: 1.5; }}
.qr {{ width: 230px; height: 230px; max-width: 100%; margin: 4px auto 22px; display: block; }}
.button {{ display: block; width: 100%; padding: 15px 18px; border-radius: 14px; background: #7657ff; color: white; text-decoration: none; font-weight: 700; font-size: 17px; }}
.help {{ margin-top: 18px; font-size: 14px; line-height: 1.55; color: #6b7280; }}
.support {{ margin-top: 18px; }}
.support a {{ color: #7657ff; text-decoration: none; font-weight: 600; }}
</style>
</head>
<body>
<div class="card">
    <div class="logo">💗</div>
    <h1>Карина VPN</h1>
    <p class="subtitle">Защищённое подключение к интернету</p>
    <img class="qr" src="{html.escape(sub_id)}.png" alt="QR-код Карина VPN">
    <a class="button" href="{html.escape(crypt5, quote=True)}">📱 Подключить Карина VPN</a>
    <div class="help">На телефоне нажмите кнопку выше.<br>На другом устройстве отсканируйте QR-код в приложении Happ.</div>
    <div class="support"><a href="https://t.me/rivixxx">💬 Поддержка</a></div>
</div>
</body>
</html>
"""
    targets[".html"].write_text(page, encoding="utf-8")
    return f"{public_base.rstrip('/')}/{sub_id}.html"
