import html
import os
import re
import tempfile
import urllib.parse
from pathlib import Path


class SubscriptionIssuerError(Exception):
    pass


def _validated_subscription(subscription_url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(subscription_url)
    decoded_path = urllib.parse.unquote(parsed.path)
    segments = decoded_path.split("/")
    sub_id = segments[-1] if segments else ""
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or any(segment in {".", ".."} for segment in segments)):
        raise SubscriptionIssuerError("некорректный URL подписки")
    if not re.fullmatch(r"[A-Za-z0-9_-]{6,128}", sub_id):
        raise SubscriptionIssuerError("некорректный SUB_ID")
    return subscription_url, sub_id


def _temporary_path(directory: Path, sub_id: str, suffix: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        dir=directory, prefix=f".{sub_id}.", suffix=f"{suffix}.tmp",
    )
    os.close(descriptor)
    return Path(name)


def issue_subscription(
    subscription_url: str,
    *,
    output_dir: Path | str,
    public_base: str,
    qr_writer=None,
) -> str:
    """Create a direct HTTPS QR and connection page without external services."""
    subscription_url, sub_id = _validated_subscription(subscription_url)
    directory = Path(output_dir).resolve()
    targets = {suffix: directory / f"{sub_id}{suffix}" for suffix in (".png", ".html")}
    if any(not path.resolve().is_relative_to(directory) for path in targets.values()):
        raise SubscriptionIssuerError("небезопасный путь результата")

    directory.mkdir(parents=True, exist_ok=True)
    temporary = {suffix: _temporary_path(directory, sub_id, suffix) for suffix in targets}
    try:
        if qr_writer is None:
            import qrcode

            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(subscription_url)
            qr.make(fit=True)
            qr.make_image(fill_color="black", back_color="white").save(
                temporary[".png"], format="PNG",
            )
        else:
            qr_writer(subscription_url, temporary[".png"])

        temporary[".html"].write_text(
            _connection_page(subscription_url, sub_id), encoding="utf-8",
        )
        if temporary[".png"].stat().st_size == 0:
            raise SubscriptionIssuerError("QR-файл пуст")
        written_page = temporary[".html"].read_text(encoding="utf-8")
        if subscription_url not in written_page or f"{sub_id}.png" not in written_page:
            raise SubscriptionIssuerError("проверка страницы подключения не пройдена")

        for path in temporary.values():
            path.chmod(0o644)
        os.replace(temporary[".png"], targets[".png"])
        os.replace(temporary[".html"], targets[".html"])
    except SubscriptionIssuerError:
        raise
    except Exception as exc:
        raise SubscriptionIssuerError(f"ошибка выдачи подписки: {type(exc).__name__}") from exc
    finally:
        for path in temporary.values():
            path.unlink(missing_ok=True)

    return f"{public_base.rstrip('/')}/{sub_id}.html"


def _connection_page(subscription_url: str, sub_id: str) -> str:
    safe_url = html.escape(subscription_url, quote=True)
    safe_id = html.escape(sub_id, quote=True)
    return f"""<!doctype html>
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
    <img class="qr" src="{safe_id}.png" alt="QR-код Карина VPN">
    <a class="button" href="{safe_url}">📱 Подключить Карина VPN</a>
    <div class="help">На телефоне нажмите кнопку выше.<br>На другом устройстве отсканируйте QR-код в приложении.</div>
    <div class="support"><a href="https://t.me/rivixxx">💬 Поддержка</a></div>
</div>
</body>
</html>
"""
