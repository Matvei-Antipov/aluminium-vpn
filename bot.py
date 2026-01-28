import asyncio
import logging
import os
import random
import re
import string
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import asyncpg
import uvicorn
from aiocryptopay import AioCryptoPay, Networks
from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, CommandObject, StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery
from dotenv import load_dotenv
from fastapi import FastAPI
from py3xui import AsyncApi, Client
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 4000
MAX_CALLBACK_ALERT_LENGTH = 150

HTML_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9]+)(?:\s[^>]*)?>")
HTML_SELF_CLOSING_TAGS = {"br", "hr", "img"}

bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher(storage=MemoryStorage())

#NETWORK = Networks.TEST_NET
NETWORK = Networks.MAIN_NET

crypto: AioCryptoPay | None = None
db_pool: asyncpg.Pool | None = None
vpn_api: AsyncApi | None = None

CHANNEL_ID = os.getenv("CHANNEL_ID")
CHANNEL_URL = os.getenv("CHANNEL_URL")
CHANNEL_2_ID = os.getenv("CHANNEL_2_ID")
CHANNEL_2_URL = os.getenv("CHANNEL_2_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
ADMIN_USERNAME = "matvei_dev"


class AdminState(StatesGroup):
    waiting_for_new_days = State()
    waiting_for_new_refs = State()
    editing_user_id = State()
    waiting_for_search_query = State()

class SupportState(StatesGroup):
    waiting_for_question = State()
    waiting_for_answer = State()


def _strip_incomplete_html_tail(text: str) -> str:
    lt = text.rfind("<")
    gt = text.rfind(">")
    if lt > gt:
        text = text[:lt]

    amp = text.rfind("&")
    if amp != -1 and ";" not in text[amp:]:
        text = text[:amp]

    return text


def _close_unclosed_html_tags(fragment: str) -> str:
    stack: list[str] = []

    for match in HTML_TAG_RE.finditer(fragment):
        is_close, tag = match.group(1), match.group(2).lower()
        if tag in HTML_SELF_CLOSING_TAGS:
            continue

        if is_close:
            for i in range(len(stack) - 1, -1, -1):
                if stack[i] == tag:
                    stack = stack[:i]
                    break
        else:
            stack.append(tag)

    for tag in reversed(stack):
        fragment += f"</{tag}>"

    return fragment


def _truncate_html(text: str, max_length: int) -> str:
    ellipsis = "…"
    if len(text) <= max_length:
        return text

    cutoff = max_length - len(ellipsis)
    while cutoff > 0:
        fragment = text[:cutoff].rstrip()
        fragment = _strip_incomplete_html_tail(fragment)
        fragment = _close_unclosed_html_tags(fragment)

        candidate = fragment + ellipsis
        if len(candidate) <= max_length:
            return candidate

        cutoff -= 20

    return ellipsis


def truncate_text(text: str | None, max_length: int, parse_mode: str | None = None) -> str | None:
    if text is None:
        return None

    if len(text) <= max_length:
        return text

    if parse_mode == "HTML":
        return _truncate_html(text, max_length)

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


async def safe_callback_answer(
    callback: types.CallbackQuery,
    text: str | None = None,
    *,
    show_alert: bool = False,
    **kwargs,
):
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


def generate_custom_id() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(9))


async def check_sub(user_id: int) -> bool:
    channels_to_check = []
    if CHANNEL_ID:
        channels_to_check.append(CHANNEL_ID)
    if CHANNEL_2_ID:
        channels_to_check.append(CHANNEL_2_ID)

    if not channels_to_check:
        return True

    for chat_id in channels_to_check:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ["left", "kicked", "banned"]:
                return False
        except Exception as e:
            logger.error(f"⚠️ Ошибка проверки подписки для канала {chat_id}: {e}")
            continue
            
    return True


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, max=10), reraise=True)
async def add_client_via_xui_api(uuid_str: str, email: str, limit_ip: int = 1, expiry_time: int = 0) -> bool:
    if vpn_api is None:
        raise RuntimeError("vpn_api is not initialized")

    inbound_id = int(os.getenv("INBOUND_ID", "0"))
    if not inbound_id:
        raise RuntimeError("INBOUND_ID is not configured")

    await vpn_api.login()

    client = Client(
        id=uuid_str,
        email=email,
        enable=True,
        limit_ip=limit_ip,
        total_gb=0,
        expiry_time=expiry_time,
        flow="xtls-rprx-vision",
        tg_id="",
        sub_id="",
    )

    await vpn_api.client.add(inbound_id=inbound_id, clients=[client])
    logger.info("✅ Client %s added successfully via py3xui", email)
    return True


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, max=10), reraise=True)
async def update_client_via_xui_api(uuid_str: str, email: str, expiry_time: int) -> bool:
    if vpn_api is None:
        raise RuntimeError("vpn_api is not initialized")

    inbound_id = int(os.getenv("INBOUND_ID", "0"))
    if not inbound_id:
        raise RuntimeError("INBOUND_ID is not configured")

    await vpn_api.login()

    client = Client(
        id=uuid_str,
        email=email,
        enable=True,
        limit_ip=1,
        total_gb=0,
        expiry_time=expiry_time,
        flow="xtls-rprx-vision",
        tg_id="",
        sub_id="",
    )

    await vpn_api.client.update(uuid_str, inbound_id=inbound_id, client=client)
    logger.info("✅ Client %s updated successfully via py3xui", email)
    return True


async def init_db() -> None:
    global db_pool
    db_pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                uuid TEXT,
                expiry_date TIMESTAMP,
                custom_id TEXT UNIQUE,
                referrer_id BIGINT,
                referral_count INTEGER DEFAULT 0,
                last_support_time TIMESTAMP
            );
            """
        )
        try:
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS custom_id TEXT UNIQUE;")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_id BIGINT;")
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_count INTEGER DEFAULT 0;"
            )
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_support_time TIMESTAMP;")
        except Exception:
            pass


def generate_vless_link(user_uuid: str, email: str) -> str:
    ip = os.getenv("SERVER_IP")
    port = os.getenv("SERVER_PORT")
    pk = os.getenv("REALITY_PK")
    sni = os.getenv("SNI")
    sid = os.getenv("SID", "")
    return (
        f"vless://{user_uuid}@{ip}:{port}?"
        f"security=reality&encryption=none&pbk={pk}&fp=chrome&type=tcp&flow=xtls-rprx-vision&"
        f"sni={sni}&sid={sid}#{email}"
    )


async def process_referral_reward(referrer_id: int) -> None:
    logger.info(f"🎁 Начисляем награду рефереру {referrer_id}...")
    if db_pool is None:
        logger.error("❌ Нет соединения с БД")
        return

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE users SET referral_count = referral_count + 1
            WHERE user_id = $1
            RETURNING referral_count, expiry_date, uuid
            """,
            referrer_id,
        )

        if not row:
            logger.error(f"❌ Не удалось обновить счетчик для {referrer_id}. Пользователь не найден в БД?")
            return

        count = row["referral_count"]
        logger.info(f"✅ Счетчик обновлен! У пользователя {referrer_id} теперь {count} рефералов.")

        if count % 5 != 0:
            logger.info(f"ℹ️ У {referrer_id} пока {count} друзей. Награда будет на 5, 10, 15...")
            return

        logger.info(f"🎉 УРА! 5-й реферал. Выдаем подписку...")

        if row["expiry_date"] and row["expiry_date"] > datetime.now():
            new_expiry = row["expiry_date"] + timedelta(days=3)
        else:
            new_expiry = datetime.now() + timedelta(days=3)

        if not row["uuid"]:
            new_uuid = str(uuid.uuid4())
            email = f"user_{referrer_id}"
            expiry_time_ms = int(new_expiry.timestamp() * 1000)
            try:
                await add_client_via_xui_api(new_uuid, email, limit_ip=1, expiry_time=expiry_time_ms)
            except Exception as e:
                logger.error(f"❌ Ошибка X-UI при выдаче награды: {e}")
                return

            await conn.execute(
                "UPDATE users SET expiry_date=$1, uuid=$2 WHERE user_id=$3",
                new_expiry, new_uuid, referrer_id,
            )
            
            key = generate_vless_link(new_uuid, email)
            try:
                await safe_bot_send_message(
                    referrer_id,
                    f"🎉 <b>Бонус (5 друзей)!</b>\nВаш ключ (+3 дня):\n<code>{key}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass
        else:
            await conn.execute("UPDATE users SET expiry_date=$1 WHERE user_id=$2", new_expiry, referrer_id)
            
            email = f"user_{referrer_id}"
            expiry_time_ms = int(new_expiry.timestamp() * 1000)
            try:
                await update_client_via_xui_api(row["uuid"], email, expiry_time_ms)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка продления X-UI для награды: {e}")
            
            try:
                await safe_bot_send_message(
                    referrer_id,
                    "🎉 <b>Бонус (5 друзей)!</b>\nВам добавлено 3 дня VPN!",
                    parse_mode="HTML",
                )
            except Exception:
                pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    global crypto, vpn_api

    crypto = AioCryptoPay(token=os.getenv("CRYPTO_TOKEN"), network=NETWORK)

    vpn_api = AsyncApi(
        host=os.getenv("PANEL_URL", ""),
        username=os.getenv("PANEL_USERNAME", ""),
        password=os.getenv("PANEL_PASSWORD", ""),
        use_tls_verify=False,
    )

    try:
        await vpn_api.login()
    except Exception as e:
        logger.warning("⚠️ X-UI login on startup failed: %s", e)

    await init_db()

    asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    yield

    if crypto:
        await crypto.close()
    if db_pool:
        await db_pool.close()
    await bot.session.close()


app = FastAPI(lifespan=lifespan)


def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="⚡️ Купить VPN (1 мес - $1)", callback_data="buy_1_month")],
        [
            InlineKeyboardButton(text="👤 Профиль", callback_data="profile"),
            InlineKeyboardButton(text="🆘 Поддержка", callback_data="support"),
        ]
    ]
    if user_id == ADMIN_ID:
        kb.append([InlineKeyboardButton(text="🛠 Админ панель", callback_data="admin_panel")])
        
    return InlineKeyboardMarkup(inline_keyboard=kb)


def sub_kb() -> InlineKeyboardMarkup:
    buttons = []

    if CHANNEL_URL:
        buttons.append([InlineKeyboardButton(text="📢 Подписаться на Канал 1", url=CHANNEL_URL)])

    if CHANNEL_2_URL:
        buttons.append([InlineKeyboardButton(text="📢 Подписаться на Канал 2", url=CHANNEL_2_URL)])

    buttons.append([InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub_btn")])

    return InlineKeyboardMarkup(inline_keyboard=buttons)

def back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="start")]])


def admin_ticket_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Ответить", callback_data=f"ans_{user_id}")],
            [InlineKeyboardButton(text="🗑 Удалить/Закрыть", callback_data="del_msg")],
        ]
    )


@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    if db_pool is None:
        return

    user_id = message.from_user.id
    username = message.from_user.username
    logger.info(f"▶️ START от пользователя {user_id} (@{username})")

    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        
        if not user:
            logger.info(f"🆕 Регистрируем НОВОГО пользователя {user_id}...")
            custom_id = generate_custom_id()
            referrer_id: int | None = None

            if command.args and command.args.isdigit():
                potential_ref_id = int(command.args)
                if potential_ref_id != user_id:
                    ref_check = await conn.fetchval("SELECT user_id FROM users WHERE user_id = $1", potential_ref_id)
                    if ref_check:
                        referrer_id = potential_ref_id

            await conn.execute(
                "INSERT INTO users (user_id, username, custom_id, referrer_id) VALUES ($1, $2, $3, $4)",
                user_id, username, custom_id, referrer_id,
            )

            if referrer_id:
                asyncio.create_task(process_referral_reward(referrer_id))
                try:
                    await safe_bot_send_message(
                        referrer_id,
                        f"👤 <b>Новый реферал!</b>\n@{username if username else user_id}",
                        parse_mode="HTML"
                    )
                except Exception:
                    pass

    if not await check_sub(user_id):
        return await safe_message_answer(
            message,
            "🔒 <b>Доступ закрыт!</b>\nДля работы с ботом подпишитесь на наши каналы:",
            reply_markup=sub_kb(),
            parse_mode="HTML",
        )

    await safe_message_answer(
        message,
        "👋 <b>Добро пожаловать в VPN Shop!</b>",
        reply_markup=main_menu_kb(user_id),
        parse_mode="HTML",
    )


@dp.callback_query(F.data == "check_sub_btn")
async def check_sub_btn(callback: types.CallbackQuery):
    if await check_sub(callback.from_user.id):
        await callback.message.delete()
        await safe_message_answer(
            callback.message,
            "👋 <b>Спасибо! Доступ открыт.</b>",
            reply_markup=main_menu_kb(callback.from_user.id),
            parse_mode="HTML",
        )
    else:
        await safe_callback_answer(callback, "❌ Вы не подписаны!", show_alert=True)


@dp.callback_query(F.data == "start")
async def cb_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()

    if not await check_sub(callback.from_user.id):
        await safe_message_answer(callback.message, "🔒 Подписка не найдена!", reply_markup=sub_kb())
        return await safe_callback_answer(callback)

    try:
        await safe_message_edit_text(
            callback.message,
            "👋 <b>Главное меню</b>",
            reply_markup=main_menu_kb(callback.from_user.id),
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        await safe_message_answer(callback.message, "👋 Главное меню", reply_markup=main_menu_kb(callback.from_user.id))


@dp.callback_query(F.data == "profile")
async def profile_handler(callback: types.CallbackQuery):
    if db_pool is None:
        return

    if not await check_sub(callback.from_user.id):
        return await safe_message_answer(callback.message, "🔒 Подпишитесь:", reply_markup=sub_kb())

    user_id = callback.from_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)

    if not user:
        return await safe_callback_answer(callback, "Ошибка данных", show_alert=True)

    days_left = 0
    status_emoji = "❌"
    status_text = "Не активен"
    if user["expiry_date"] and user["expiry_date"] > datetime.now():
        days_left = (user["expiry_date"] - datetime.now()).days
        status_emoji = "✅"
        status_text = f"Активен ({days_left} дн.)"

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"

    text = (
        "👤 <b>Личный кабинет</b>\n\n"
        f"🆔 ID: <code>{user['custom_id']}</code>\n"
        f"📡 VPN: {status_emoji} {status_text}\n\n"
        f"👥 <b>Рефералы:</b> {user['referral_count']}\n"
        "🎁 <i>3 дня VPN за каждые 5 друзей!</i>\n\n"
        "🔗 <b>Ссылка для друзей:</b>\n"
        f"<code>{ref_link}</code>"
    )

    kb_buttons = []
    if user["expiry_date"] and user["expiry_date"] > datetime.now() and user["uuid"]:
        kb_buttons.append([InlineKeyboardButton(text="👁 Показать ключ доступа", callback_data="show_key")])
    kb_buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="start")])
    profile_kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    await safe_message_edit_text(callback.message, text, reply_markup=profile_kb, parse_mode="HTML")


@dp.callback_query(F.data == "show_key")
async def show_key_handler(callback: types.CallbackQuery):
    if db_pool is None:
        return

    if not await check_sub(callback.from_user.id):
        return await safe_message_edit_text(
            callback.message, 
            "🔒 <b>Доступ закрыт!</b>\nДля работы с ботом подпишитесь на наши каналы:", 
            reply_markup=sub_kb(),
            parse_mode="HTML"
        )

    user_id = callback.from_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT uuid, expiry_date FROM users WHERE user_id=$1", user_id)

    if not user or not user["uuid"]:
        return await safe_callback_answer(callback, "❌ У вас нет активного ключа", show_alert=True)

    if not user["expiry_date"] or user["expiry_date"] <= datetime.now():
        return await safe_callback_answer(callback, "❌ Ваша подписка истекла", show_alert=True)

    email = f"user_{user_id}"
    key = generate_vless_link(user["uuid"], email)
    
    text = get_guide_text(key)
    
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в профиль", callback_data="profile")]]
    )
    
    await safe_message_edit_text(
        callback.message, 
        text, 
        reply_markup=kb, 
        parse_mode="HTML", 
        disable_web_page_preview=True
    )


@dp.callback_query(F.data == "buy_1_month")
async def create_invoice(callback: types.CallbackQuery):
    if db_pool is None:
        return

    if not await check_sub(callback.from_user.id):
        return await safe_message_answer(callback.message, "🔒 Подпишитесь:", reply_markup=sub_kb())

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⭐️ Оплатить Звездами (85 ⭐️)", callback_data="pay_stars")], 
            
            [InlineKeyboardButton(text="💎 Оплатить Криптой ($1)", callback_data="pay_crypto")],
            
            [InlineKeyboardButton(text="🔙 Назад", callback_data="start")],
        ]
    )

    await safe_message_edit_text(
        callback.message,
        "💳 <b>Выберите способ оплаты</b>\n\n"
        "⭐️ <b>Telegram Stars:</b> Оплата картой прямо в приложении.\n"
        "💎 <b>Криптовалюта:</b> USDT, TON, BTC через CryptoPay.\n\n"
        "<i>Стоимость: 1 месяц доступа.</i>",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "pay_crypto")
async def create_crypto_invoice(callback: types.CallbackQuery):
    if db_pool is None or crypto is None:
        return

    if not await check_sub(callback.from_user.id):
        return await safe_message_answer(callback.message, "🔒 Подпишитесь:", reply_markup=sub_kb())

    try:
        invoice = await crypto.create_invoice(
            amount=1.00,
            fiat="USD",
            currency_type="fiat",
            accepted_assets="USDT,TON,BTC,ETH,TRX,USDC,LTC",
            description="VPN (30 days)",
            expires_in=600,
        )
        url = invoice.bot_invoice_url

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🔗 Выбрать валюту и оплатить", url=url)],
                [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_{invoice.invoice_id}")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="start")],
            ]
        )

        await safe_message_edit_text(
            callback.message,
            "🧾 <b>Счет на оплату</b>\n"
            "Сумма: <b>$1.00</b>\n"
            "Можно оплатить: USDT, TON, TRX, BTC, ETH...\n\n"
            "<i>Нажмите кнопку ниже, чтобы выбрать валюту.</i>",
            reply_markup=kb,
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

    except Exception as e:
        logger.error("Ошибка создания счета: %s", e)
        await safe_callback_answer(callback, f"❌ Ошибка создания счета: {e}", show_alert=True)


@dp.callback_query(F.data == "pay_stars")
async def send_stars_invoice(callback: types.CallbackQuery):
    await callback.message.delete()
    await callback.message.answer_invoice(
        title="VPN Access (30 дней)",
        description="Быстрый и безопасный VPN. Протокол VLESS Reality + Vision.",
        payload="vpn_month_sub",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Подписка 1 мес.", amount=85)],
        start_parameter="vpn_sub"
    )

@dp.pre_checkout_query()
async def pre_checkout_handler(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def success_payment_handler(message: types.Message):
    if message.successful_payment.invoice_payload != "vpn_month_sub":
        return

    user_id = message.from_user.id
    logger.info(f"💰 Получена оплата Stars от {user_id}")

    if db_pool is None:
        await message.answer("❌ Ошибка базы данных. Обратитесь к админу.")
        return

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE users 
               SET expiry_date = GREATEST(expiry_date, NOW()) + INTERVAL '30 days'
               WHERE user_id = $1
               RETURNING uuid, expiry_date""",
            user_id,
        )

        if not row:
            await message.answer("❌ Ошибка: пользователь не найден в БД.")
            return

        expiry_time_ms = int(row["expiry_date"].timestamp() * 1000)
        email = f"user_{user_id}"

        if row["uuid"]:
            try:
                await update_client_via_xui_api(row["uuid"], email, expiry_time_ms)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка обновления в X-UI: {e}")
            
            key = generate_vless_link(row["uuid"], email)
        
        else:
            new_uuid = str(uuid.uuid4())
            try:
                await add_client_via_xui_api(new_uuid, email, limit_ip=1, expiry_time=expiry_time_ms)
                
                await conn.execute("UPDATE users SET uuid = $1 WHERE user_id = $2", new_uuid, user_id)
            except Exception as e:
                logger.error(f"❌ Ошибка создания в X-UI: {e}")
                await message.answer("⚠️ Оплата прошла, но произошла ошибка активации. Напишите в поддержку.")
                return
            
            key = generate_vless_link(new_uuid, email)

        guide = get_guide_text(key)
        
      
        
        await safe_message_answer(
            message,
            guide,
            reply_markup=back_kb(),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )

@dp.callback_query(F.data.startswith("check_"))
async def check_invoice(callback: types.CallbackQuery):
    if crypto is None or db_pool is None:
        return

    invoice_id = int(callback.data.split("_")[1])

    try:
        result = await crypto.get_invoices(invoice_ids=invoice_id)
        if isinstance(result, list):
            if not result:
                return await safe_callback_answer(callback, "❌ Счет не найден", show_alert=True)
            invoice = result[0]
        else:
            invoice = result
    except Exception:
        return await safe_callback_answer(callback, "❌ Ошибка проверки", show_alert=True)

    if invoice.status == "paid":
        await safe_callback_answer(callback, "✅ Оплата получена! Генерируем ключ...", show_alert=True)

        user_id = callback.from_user.id
        async with db_pool.acquire() as conn:
            user = await conn.fetchrow("SELECT uuid FROM users WHERE user_id=$1", user_id)

           
            row = await conn.fetchrow(
                """UPDATE users 
                   SET expiry_date = GREATEST(expiry_date, NOW()) + INTERVAL '30 days'
                   WHERE user_id = $1
                   RETURNING uuid, expiry_date""",
                user_id,
            )

            if not row:
                logger.error("Не удалось обновить данные пользователя %s", user_id)
                return await safe_message_edit_text(
                    callback.message, "❌ Ошибка обновления подписки", reply_markup=back_kb()
                )

           
            if row["uuid"]:
                email = f"user_{user_id}"
                expiry_time_ms = int(row["expiry_date"].timestamp() * 1000)
                
                try:
                    await update_client_via_xui_api(row["uuid"], email, expiry_time_ms)
                except Exception as e:
                    logger.warning("⚠️ Failed to update client in X-UI: %s", e)
                
                key = generate_vless_link(row["uuid"], email)
                guide = get_guide_text(key)
                await safe_message_edit_text(
                    callback.message,
                    guide,
                    reply_markup=back_kb(),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            else:
                new_uuid = str(uuid.uuid4())
                email = f"user_{user_id}"

                expiry_time_ms = int(row["expiry_date"].timestamp() * 1000)

                try:
                    await add_client_via_xui_api(new_uuid, email, limit_ip=1, expiry_time=expiry_time_ms)
                except Exception as e:
                    logger.error("Ошибка добавления клиента в X-UI: %s", e)
                    await safe_message_edit_text(
                        callback.message, "❌ Ошибка создания VPN клиента", reply_markup=back_kb()
                    )
                    return

                await conn.execute("UPDATE users SET uuid = $1 WHERE user_id = $2", new_uuid, user_id)

                key = generate_vless_link(new_uuid, email)
                guide = get_guide_text(key)
                await safe_message_edit_text(
                    callback.message,
                    guide,
                    reply_markup=back_kb(),
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )

    elif invoice.status == "active":
        await safe_callback_answer(callback, "⏳ Оплата еще не поступила", show_alert=True)
    else:
        await safe_message_edit_text(callback.message, "❌ Счет истек.", reply_markup=back_kb())

@dp.callback_query(F.data == "support")
async def support_start(callback: types.CallbackQuery, state: FSMContext):
    if db_pool is None:
        return

    if not callback.from_user.username:
        return await safe_callback_answer(callback, "❌ Установите Username в Telegram!", show_alert=True)

    user_id = callback.from_user.id
    
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT last_support_time FROM users WHERE user_id = $1", user_id)
        
        if user and user["last_support_time"]:
           
            last_time = user["last_support_time"].replace(tzinfo=None)
           
            time_diff = datetime.utcnow() - last_time
            minutes_passed = time_diff.total_seconds() / 60
            
            if minutes_passed < 60:
                minutes_left = int(60 - minutes_passed)
                return await safe_callback_answer(
                    callback,
                    f"⏳ Писать в поддержку можно раз в час.\nПодождите еще {minutes_left} мин.",
                    show_alert=True
                )

    await safe_message_edit_text(
        callback.message,
        "📝 <b>Техническая поддержка</b>\n\n"
        "Опишите вашу проблему одним сообщением.\n"
        "<i>Следующее сообщение можно будет отправить только через час!</i>",
        reply_markup=back_kb(),
        parse_mode="HTML",
    )
    await state.set_state(SupportState.waiting_for_question)


@dp.message(StateFilter(SupportState.waiting_for_question))
async def support_receive_msg(message: types.Message, state: FSMContext):
    if db_pool is None:
        await state.clear()
        return

    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT last_support_time FROM users WHERE user_id = $1", user_id)
        if user and user["last_support_time"]:
            last_time = user["last_support_time"].replace(tzinfo=None)
            time_diff = datetime.utcnow() - last_time
            if time_diff.total_seconds() < 3600:
                await safe_message_answer(message, "⏳ Прошел меньше часа с прошлого обращения.")
                await state.clear()
                return

    if not ADMIN_ID:
        await safe_message_answer(message, "❌ Поддержка не настроена.")
        await state.clear()
        return

    ticket_text = (
        "📩 <b>Тикет</b>\n"
        f"От: @{message.from_user.username} (ID: <code>{message.from_user.id}</code>)\n\n"
        f"{message.text}"
    )

    try:
        await safe_bot_send_message(
            ADMIN_ID,
            ticket_text,
            reply_markup=admin_ticket_kb(message.from_user.id),
            parse_mode="HTML",
        )

        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET last_support_time = $1 WHERE user_id = $2",
                datetime.utcnow(),
                message.from_user.id
            )
        
        await safe_message_answer(
            message,
            "✅ <b>Отправлено!</b> Администратор ответит вам в ближайшее время.",
            reply_markup=back_kb(),
            parse_mode="HTML",
        )
    except Exception:
        await safe_message_answer(message, "❌ Ошибка отправки.")

    await state.clear()

@dp.callback_query(F.data.startswith("ans_"))
async def admin_reply_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID and callback.from_user.username != ADMIN_USERNAME:
        return await safe_callback_answer(callback, "⛔ Вы не админ!", show_alert=True)

    await state.update_data(target_id=int(callback.data.split("_")[1]))
    await safe_message_answer(callback.message, "✍️ Введите ответ:")
    await state.set_state(SupportState.waiting_for_answer)
    await safe_callback_answer(callback)


@dp.message(StateFilter(SupportState.waiting_for_answer))
async def admin_send_reply(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID and message.from_user.username != ADMIN_USERNAME:
        return

    data = await state.get_data()
    target_id = data.get("target_id")
    if not target_id:
        await state.clear()
        return

    try:
        await safe_bot_send_message(
            target_id,
            f"👨‍💻 <b>Ответ поддержки:</b>\n\n{message.text}",
            parse_mode="HTML",
        )
        await safe_message_answer(message, "✅ Отправлено!")
    except Exception:
        await safe_message_answer(message, "❌ Не удалось отправить.")

    await state.clear()


@dp.callback_query(F.data == "del_msg")
async def delete_msg(callback: types.CallbackQuery):
    await callback.message.delete()


@app.api_route("/health", methods=["GET", "HEAD"])
async def health_check():
    return {"status": "ok"}


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_open(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        return

    await state.clear()
    await state.update_data(admin_search_query=None, admin_filter_active=False)
    
    await show_user_page(callback.message, state, page=0, is_edit=True)


async def show_user_page(message_obj: types.Message, state: FSMContext, page: int, is_edit: bool = False, message_id_to_edit: int = None):
    if db_pool is None:
        return

    data = await state.get_data()
    search_query = data.get("admin_search_query")
    filter_active = data.get("admin_filter_active", False)

    where_clauses = []
    params = []
    param_counter = 1

    if filter_active:
        where_clauses.append("expiry_date > NOW()")

    if search_query:

        where_clauses.append(f"(username ILIKE ${param_counter} OR CAST(user_id AS TEXT) = ${param_counter} OR custom_id = ${param_counter})")
        params.append(search_query)
        param_counter += 1

    where_sql = " WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    async with db_pool.acquire() as conn:
       
        count_sql = f"SELECT COUNT(*) FROM users{where_sql}"
        total_users = await conn.fetchval(count_sql, *params)

        params.append(page) 
       
        select_sql = f"SELECT * FROM users{where_sql} ORDER BY user_id LIMIT 1 OFFSET ${param_counter}"
        
        user = await conn.fetchrow(select_sql, *params)

    filter_status = "🔘 Все"
    if filter_active:
        filter_status = "🟢 Активные"
    if search_query:
        filter_status += f" | 🔍 Поиск: {search_query}"

    if not user:
        text = f"🛠 <b>Админ панель</b>\nСтатус: {filter_status}\n\n🤷‍♂️ <b>Пользователей не найдено.</b>"

        buttons = []
        if search_query or filter_active:
             buttons.append([InlineKeyboardButton(text="❌ Сбросить фильтры", callback_data="admin_reset_filters")])
        buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="start")])
        
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        if is_edit:
            await safe_message_edit_text(message_obj, text, reply_markup=kb, parse_mode="HTML")
        else:
            await safe_message_answer(message_obj, text, reply_markup=kb, parse_mode="HTML")
        return

    days_left = 0
    status_text = "🔴 Не активен"
    if user["expiry_date"] and user["expiry_date"] > datetime.now():
        days_left = (user["expiry_date"] - datetime.now()).days
        status_text = f"🟢 Активен ({days_left} дн.)"
    elif user["expiry_date"]:
        status_text = "🔴 Истек"

    username_txt = f"@{user['username']}" if user['username'] else "Нет юзернейма"
    
    card_text = (
        f"🛠 <b>Админ панель</b>\n"
        f"Режим: {filter_status}\n"
        f"👤 <b>Пользователь {page + 1} из {total_users}</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"🏷 Custom ID: <code>{user['custom_id']}</code>\n"
        f"👤 Login: {username_txt}\n\n"
        f"👥 Рефералов: <b>{user['referral_count']}</b>\n"
        f"📡 VPN: {status_text}\n"
        f"🔑 UUID: <code>{user['uuid'] if user['uuid'] else 'Нет'}</code>"
    )

    buttons = []

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_page_{page - 1}"))
    if page < total_users - 1:
        nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_page_{page + 1}"))
    buttons.append(nav_row)

    buttons.append([
        InlineKeyboardButton(text="✏️ Ред. дни", callback_data=f"admin_edit_days_{user['user_id']}_{page}"),
        InlineKeyboardButton(text="✏️ Ред. рефералов", callback_data=f"admin_edit_refs_{user['user_id']}_{page}")
    ])

    filter_btn_text = "Показать только активные" if not filter_active else "Показать всех"
    buttons.append([InlineKeyboardButton(text=f"👁 {filter_btn_text}", callback_data="admin_toggle_filter")])
    
    search_btn_text = "🔍 Поиск по @username / ID" if not search_query else "❌ Сбросить поиск"
    search_callback = "admin_search_start" if not search_query else "admin_reset_filters"
    buttons.append([InlineKeyboardButton(text=search_btn_text, callback_data=search_callback)])

    buttons.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="start")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    if message_id_to_edit:
        try:
            await bot.edit_message_text(
                text=card_text,
                chat_id=message_obj.chat.id,
                message_id=message_id_to_edit,
                reply_markup=kb,
                parse_mode="HTML"
            )
        except Exception:
            await safe_message_answer(message_obj, card_text, reply_markup=kb, parse_mode="HTML")
    elif is_edit:
        await safe_message_edit_text(message_obj, card_text, reply_markup=kb, parse_mode="HTML")
    else:
        await safe_message_answer(message_obj, card_text, reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.startswith("admin_page_"))
async def admin_pagination(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    page = int(callback.data.split("_")[2])
    await show_user_page(callback.message, state, page, is_edit=True)


@dp.callback_query(F.data == "admin_toggle_filter")
async def admin_toggle_filter(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    
    data = await state.get_data()
    current_status = data.get("admin_filter_active", False)
    await state.update_data(admin_filter_active=not current_status)

    await show_user_page(callback.message, state, page=0, is_edit=True)


@dp.callback_query(F.data == "admin_reset_filters")
async def admin_reset_filters(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.update_data(admin_search_query=None) 
    await show_user_page(callback.message, state, page=0, is_edit=True)


@dp.callback_query(F.data == "admin_search_start")
async def admin_search_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    
    await safe_message_edit_text(
        callback.message,
        "🔍 <b>Поиск пользователя</b>\n\n"
        "Отправьте мне:\n"
        "• Username (например @durov)\n"
        "• Telegram ID (цифры)\n"
        "• Custom ID из бота",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]]),
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_for_search_query)


@dp.message(StateFilter(AdminState.waiting_for_search_query))
async def admin_perform_search(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
    query = message.text.strip()
    if query.startswith("@"):
        query = query[1:]
        
    await message.delete()

    await state.update_data(admin_search_query=query)
    await state.set_state(None) 
    
    await show_user_page(message, state, page=0, is_edit=False)


@dp.callback_query(F.data.startswith("admin_edit_days_"))
async def admin_edit_days_start(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    target_user_id = int(parts[3])
    page = int(parts[4])

   
    await state.update_data(editing_user_id=target_user_id, return_page=page, panel_msg_id=callback.message.message_id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data=f"admin_page_{page}")]])
    
    await safe_message_edit_text(
        callback.message,
        f"📅 <b>Редактирование дней</b>\nID: <code>{target_user_id}</code>\n\n"
        "Просто отправьте число:\n"
        "• `30` — добавить 30 дней\n"
        "• `-5` — отнять 5 дней\n"
        "• `0` — сбросить на 'сейчас'",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_for_new_days)


@dp.message(StateFilter(AdminState.waiting_for_new_days))
async def admin_save_days(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return

   
    try:
        await message.delete()
    except:
        pass

    try:
        days_to_add = int(message.text)
    except ValueError:
       
        msg = await safe_message_answer(message, "❌ Нужно целое число!")
        await asyncio.sleep(2)
        await msg.delete()
        return

    data = await state.get_data()
    target_user_id = data["editing_user_id"]
    page = data["return_page"]
    panel_msg_id = data["panel_msg_id"]

    if db_pool is None: return

    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", target_user_id)
        if user:
            current_expiry = user["expiry_date"]
            if days_to_add == 0:
                new_expiry = datetime.now()
            else:
                if not current_expiry or current_expiry < datetime.now():
                    base_date = datetime.now()
                else:
                    base_date = current_expiry
                new_expiry = base_date + timedelta(days=days_to_add)

            expiry_ms = int(new_expiry.timestamp() * 1000)
            email = f"user_{target_user_id}"

            if user["uuid"]:
                await conn.execute("UPDATE users SET expiry_date=$1 WHERE user_id=$2", new_expiry, target_user_id)
                try:
                    await update_client_via_xui_api(user["uuid"], email, expiry_ms)
                except: pass
            else:
                if days_to_add > 0:
                    new_uuid = str(uuid.uuid4())
                    try:
                        await add_client_via_xui_api(new_uuid, email, limit_ip=1, expiry_time=expiry_ms)
                        await conn.execute("UPDATE users SET expiry_date=$1, uuid=$2 WHERE user_id=$3", new_expiry, new_uuid, target_user_id)
                    except: pass
                else:
                    await conn.execute("UPDATE users SET expiry_date=$1 WHERE user_id=$2", new_expiry, target_user_id)

    await state.clear()
    

    await show_user_page(message, state, page, is_edit=False, message_id_to_edit=panel_msg_id)



@dp.callback_query(F.data.startswith("admin_edit_refs_"))
async def admin_edit_refs_start(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    target_user_id = int(parts[3])
    page = int(parts[4])

    await state.update_data(editing_user_id=target_user_id, return_page=page, panel_msg_id=callback.message.message_id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data=f"admin_page_{page}")]])
    
    await safe_message_edit_text(
        callback.message,
        f"👥 <b>Редактирование рефералов</b>\nID: <code>{target_user_id}</code>\n\n"
        "Введите новое количество:",
        reply_markup=kb,
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_for_new_refs)


@dp.message(StateFilter(AdminState.waiting_for_new_refs))
async def admin_save_refs(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return

    try:
        await message.delete()
    except:
        pass

    try:
        new_count = int(message.text)
        if new_count < 0: raise ValueError
    except ValueError:
        msg = await safe_message_answer(message, "❌ Число должно быть >= 0")
        await asyncio.sleep(2)
        await msg.delete()
        return

    data = await state.get_data()
    target_user_id = data["editing_user_id"]
    page = data["return_page"]
    panel_msg_id = data["panel_msg_id"]

    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET referral_count=$1 WHERE user_id=$2", new_count, target_user_id)

    await state.clear()
    await show_user_page(message, state, page, is_edit=False, message_id_to_edit=panel_msg_id)

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)