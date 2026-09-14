"""Public legal documents, published only after the operator supplies reviewed text."""
import json
import logging
from pathlib import Path
from urllib.parse import urlparse

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

LOGGER = logging.getLogger(__name__)
LEGAL_FILE = Path("/etc/karina-vpn/legal.json")
TITLES = {"terms": "Условия оказания услуги", "refunds": "Порядок возврата",
          "privacy": "Персональные данные", "contacts": "Контакты поддержки"}


def load_legal_documents(path=LEGAL_FILE):
    try:
        data = Path(path).read_text(encoding="utf-8")
        documents = json.loads(data)
        if not isinstance(documents, dict) or documents.get("published") is not True:
            return {}
        for key in ("version", "operator", "contacts", "terms", "refunds", "privacy"):
            value = documents.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > 3000:
                raise ValueError("Missing or oversized public legal field")
        if len(documents["operator"]) > 400 or len(documents["version"]) > 80:
            raise ValueError("Oversized legal heading")
        support_url = documents.get("support_url")
        if support_url is not None:
            if not isinstance(support_url, str):
                raise ValueError("Invalid public support URL")
            parsed = urlparse(support_url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("Invalid public support URL")
        return documents
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        LOGGER.warning("Public legal documents unavailable; unpublished fallback used")
        return {}


def legal_view(section, *, documents=None, support_url=None):
    documents = documents or {}
    support_url = support_url or documents.get("support_url")
    rows = []
    if section == "home":
        text = "📄 Документы и поддержка • Карина VPN\n\nВыберите раздел."
    elif section in TITLES:
        text = TITLES[section] + " • Карина VPN\n\n"
        if documents:
            text += (f"Исполнитель: {documents['operator']}\n"
                     f"Редакция: {documents['version']}\n\n{documents[section]}")
        elif section == "contacts":
            text += ("Обращения по подключению, оплате и возвратам принимает оператор.\n"
                     "Укажите номер заявки, дату и описание проблемы. "
                     "Не отправляйте пароли, коды подтверждения или полный номер карты.\n\n"
                     "Сообщение в этот бот само по себе не отправляет обращение оператору. "
                     "Используйте контакт ниже.")
        else:
            text += ("Окончательная редакция документа ещё не опубликована. "
                     "До оплаты запросите у оператора условия, порядок возврата "
                     "и реквизиты исполнителя. Этот экран не является офертой.")
    else:
        raise ValueError("Unknown legal section")
    if support_url:
        text += f"\n\nКонтакт поддержки: {support_url}"
        rows.append([InlineKeyboardButton("✍️ Написать оператору", url=support_url)])
    else:
        text += "\n\nКонтакт оператора пока не опубликован."
    for key, title in TITLES.items():
        if key != section:
            rows.append([InlineKeyboardButton(title, callback_data=f"legal:{key}")])
    rows.append([InlineKeyboardButton("← В меню", callback_data="client_home")])
    return text, InlineKeyboardMarkup(rows)
