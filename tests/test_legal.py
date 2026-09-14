import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from src import bot
from src.legal import legal_view, load_legal_documents


def run(coroutine):
    try:
        coroutine.send(None)
    except StopIteration:
        return
    finally:
        coroutine.close()
    raise AssertionError("Unexpected I/O")


@pytest.mark.parametrize("section", ["home", "terms", "refunds", "privacy", "contacts"])
def test_legal_callbacks_available_without_membership_or_binding(monkeypatch, section):
    monkeypatch.setattr(bot, "get_link_by_tg", Mock(side_effect=AssertionError("DB lookup")))
    monkeypatch.setattr(bot, "check_required_membership", AsyncMock(side_effect=AssertionError("Gate")))
    monkeypatch.setattr(bot, "load_legal_documents", lambda: {})
    monkeypatch.setattr(bot, "SUPPORT_URL", "https://t.me/example_support")
    display = AsyncMock()
    monkeypatch.setattr(bot, "show_text", display)
    update = NS(effective_chat=NS(type="private", id=1), effective_user=NS(id=1),
                callback_query=NS(answer=AsyncMock(), data=f"legal:{section}"))
    run(bot.callbacks(update, NS(user_data={})))
    display.assert_awaited_once()


@pytest.mark.parametrize("command", ["terms", "refunds", "privacy", "support", "paysupport"])
def test_public_commands_available_to_unregistered_users(monkeypatch, command):
    monkeypatch.setattr(bot, "load_legal_documents", lambda: {})
    monkeypatch.setattr(bot, "SUPPORT_URL", "https://t.me/example_support")
    display = AsyncMock()
    monkeypatch.setattr(bot, "show_text", display)
    update = NS(effective_chat=NS(type="private"), message=NS(text=f"/{command}@example_bot"))
    run(bot.legal_command(update, NS(user_data={})))
    assert "https://t.me/example_support" in display.await_args.args[2]


def test_missing_or_invalid_documents_never_publish_draft(tmp_path):
    path = tmp_path / "legal.json"
    assert load_legal_documents(path) == {}
    for content in ('bad json', '{}', '[]', '{"published":true}',
                    '{"published":false,"operator":"Draft operator"}'):
        path.write_text(content, encoding="utf-8")
        assert load_legal_documents(path) == {}
    text, _ = legal_view("terms")
    assert "не является офертой" in text


def test_approved_documents_show_exact_text_and_operator(tmp_path):
    data = {key: f"Reviewed {key}" for key in
            ("operator", "version", "contacts", "terms", "refunds", "privacy")}
    data["published"] = True
    path = tmp_path / "legal.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_legal_documents(path)
    text, _ = legal_view("refunds", documents=loaded)
    assert data["refunds"] in text and data["operator"] in text
    data["terms"] = "x" * 3001
    path.write_text(json.dumps(data), encoding="utf-8")
    assert load_legal_documents(path) == {}


@pytest.mark.parametrize("linked", [False, True])
def test_start_reopens_screen_after_clearing_history(monkeypatch, linked):
    monkeypatch.setattr(bot, "ADMIN_TG_ID", 99)
    monkeypatch.setattr(bot, "REQUIRED_MEMBERSHIP_MODE", "disabled")
    monkeypatch.setattr(bot, "get_link_by_tg", lambda uid: {"email": "test"} if linked else None)
    monkeypatch.setattr(bot, "resolve_main_sticker", AsyncMock(return_value="s"))
    # Missing external client uses the existing stale-binding screen; no live I/O.
    monkeypatch.setattr(bot, "build_client_service", lambda: NS(get_client_bundle=lambda email: None))
    message = NS(text="/start", reply_text=AsyncMock(return_value=NS(message_id=50)))
    update = NS(effective_chat=NS(type="private", id=1), effective_user=NS(id=1),
                message=message, callback_query=None)
    tg = NS(send_message=AsyncMock(return_value=NS(message_id=51)),
            send_sticker=AsyncMock(return_value=NS(message_id=52)),
            edit_message_text=AsyncMock(side_effect=RuntimeError("Message is not modified")))
    context = NS(args=[], bot=tg, user_data={
        "karina_ui_message_id": 10, "karina_ui_kind": "compound" if linked else "text",
        "karina_ui_screen": {"screen_key": "main", "content_type": "compound",
                             "primary_message_id": 10, "message_ids": [9, 10]}})
    run(bot.start(update, context))
    tg.edit_message_text.assert_not_awaited()
    if linked:
        tg.send_message.assert_awaited_once()
    else:
        message.reply_text.assert_awaited_once()
