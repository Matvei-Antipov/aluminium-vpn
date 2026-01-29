import re
from aiogram import types
from config import bot

MAX_MESSAGE_LENGTH = 4000
MAX_CALLBACK_ALERT_LENGTH = 150
HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9]+)(?:\s[^>]*)?>")
HTML_SELF_CLOSING_TAGS = {"br", "hr", "img"}

def _strip_incomplete_html_tail(text: str) -> str:
    lt = text.rfind("<")
    gt = text.rfind(">")
    if lt > gt: text = text[:lt]
    amp = text.rfind("&")
    if amp != -1 and ";" not in text[amp:]: text = text[:amp]
    return text

def _close_unclosed_html_tags(fragment: str) -> str:
    stack: list[str] = []
    for match in HTML_TAG_RE.finditer(fragment):
        is_close, tag = match.group(1), match.group(2).lower()
        if tag in HTML_SELF_CLOSING_TAGS: continue
        if is_close:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == tag:
                    stack = stack[:i]
                    break
        else:
            stack.append(tag)
    for tag in reversed(stack): fragment += f"</{tag}>"
    return fragment

def _truncate_html(text: str, max_length: int) -> str:
    ellipsis = "…"
    if len(text) <= max_length: return text
    cutoff = max_length - len(ellipsis)
    while cutoff > 0:
        fragment = text[:cutoff].rstrip()
        fragment = _strip_incomplete_html_tail(fragment)
        fragment = _close_unclosed_html_tags(fragment)
        candidate = fragment + ellipsis
        if len(candidate) <= max_length: return candidate
        cutoff -= 20
    return ellipsis

def truncate_text(text: str | None, max_length: int, parse_mode: str | None = None) -> str | None:
    if text is None: return None
    if len(text) <= max_length: return text
    if parse_mode == "HTML": return _truncate_html(text, max_length)
    ellipsis = "…"
    return text[: max_length - len(ellipsis)].rstrip() + ellipsis

async def safe_message_answer(message: types.Message, text: str, **kwargs):
    parse_mode = kwargs.get("parse_mode")
    text = truncate_text(text, MAX_MESSAGE_LENGTH, parse_mode=parse_mode) 
    return await message.answer(text, **kwargs)

async def safe_message_edit_text(message: types.Message, text: str, **kwargs):
    parse_mode = kwargs.get("parse_mode")
    text = truncate_text(text, MAX_MESSAGE_LENGTH, parse_mode=parse_mode)  
    return await message.edit_text(text, **kwargs)

async def safe_bot_send_message(chat_id: int, text: str, **kwargs):
    parse_mode = kwargs.get("parse_mode")
    text = truncate_text(text, MAX_MESSAGE_LENGTH, parse_mode=parse_mode) 
    return await bot.send_message(chat_id, text, **kwargs)

async def safe_callback_answer(callback: types.CallbackQuery, text: str | None = None, *, show_alert: bool = False, **kwargs):
    if text is not None:
        text = truncate_text(text, MAX_CALLBACK_ALERT_LENGTH)
    return await callback.answer(text, show_alert=show_alert, **kwargs)

def get_guide_text(key: str) -> str:
    return (
        f"✅ <b>Оплата прошла успешно!</b>\n\n"
        f"Вот твой ключ доступа (нажми на скрытый текст, чтобы скопировать):\n"
        f"<tg-spoiler><code>{key}</code></tg-spoiler>\n\n"
        f"<b>Этот VPN разблокирует звонки и видео в <b>Discord</b></b>\n\n"
        f"📚 <b>ИНСТРУКЦИЯ ПОДКЛЮЧЕНИЯ:</b>\n\n"
        f"1. Нажми на заблюренный ключ выше, чтобы скопировать его.\n"
        f"2. Скачай приложение для своего устройства:\n\n"
        f"📱 <b>Android:</b>\n"
        f"<a href='https://play.google.com/store/apps/details?id=com.v2raytun.android'>Скачать v2rayTun</a>\n"
        f"<i>Зайди в приложение -> Нажми '+' -> Импорт из буфера обмена -> Нажми кнопку 'V' внизу.</i>\n\n"
        f"🍏 <b>iPhone / iPad:</b>\n"
        f"<a href='https://apps.apple.com/us/app/streisand/id6450534064'>Скачать Streisand</a>\n"
        f"<i>Открой приложение -> Оно само предложит добавить ключ -> Нажми 'Add'.</i>\n\n"
        f"💻 <b>Windows / Mac:</b>\n"
        f"<a href='https://github.com/hiddify/hiddify-next/releases'>Скачать Hiddify</a>\n"
        f"<i>Установи -> Нажми 'Новый профиль' -> 'Добавить из буфера' -> Нажми большую кнопку подключения.</i>\n\n"
        f"<b>⚠️ ВАЖНО:</b> В настройках приложения обязательно включите <b>режим TUN</b> или <b>VPN-режим</b>."
    )