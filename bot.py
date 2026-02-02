import asyncio
import os
import random
import string
import uuid
import logging
import hmac
import hashlib
import json
import aiohttp
from datetime import datetime, timedelta

from aiocryptopay import AioCryptoPay, Networks
from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, CommandObject, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery

import config
from config import bot, dp, logger, ADMIN_ID, ADMIN_USERNAME, CHANNEL_ID, CHANNEL_2_ID
import database
import xui_api
import keyboards as kb
from states import AdminState, SupportState
from utils import (
    safe_message_answer, safe_message_edit_text, safe_bot_send_message,
    safe_callback_answer, get_guide_text
)

crypto: AioCryptoPay | None = None

def generate_custom_id() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(9))

async def check_sub(user_id: int) -> bool:
    channels_to_check = []
    if CHANNEL_ID: channels_to_check.append(CHANNEL_ID)
    if CHANNEL_2_ID: channels_to_check.append(CHANNEL_2_ID)

    if not channels_to_check: return True

    for chat_id in channels_to_check:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ["left", "kicked", "banned"]: return False
        except Exception: 
            continue
    return True

async def process_referral_reward(referrer_id: int) -> None:
    logger.info(f"🎁 Начисляем награду рефереру {referrer_id}...")
    if not database.db_pool: return

    async with database.db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET referral_count = referral_count + 1 WHERE user_id = $1 RETURNING referral_count, expiry_date, uuid",
            referrer_id,
        )
        if not row: return

        count = row["referral_count"]
        if count % 5 != 0: return

        if row["expiry_date"] and row["expiry_date"] > datetime.now():
            new_expiry = row["expiry_date"] + timedelta(days=3)
        else:
            new_expiry = datetime.now() + timedelta(days=3)

        email = f"user_{referrer_id}"
        expiry_ms = int(new_expiry.timestamp() * 1000)

        if not row["uuid"]:
            new_uuid = str(uuid.uuid4())
            await xui_api.add_client_via_xui_api(new_uuid, email, limit_ip=1, expiry_time=expiry_ms)
            await conn.execute("UPDATE users SET expiry_date=$1, uuid=$2 WHERE user_id=$3", new_expiry, new_uuid, referrer_id)
            key = xui_api.generate_vless_link(new_uuid, email)
            try:
                await safe_bot_send_message(referrer_id, f"🎉 <b>Бонус (5 друзей)!</b>\nВаш ключ (+3 дня):\n<code>{key}</code>", parse_mode="HTML")
            except: pass
        else:
            await conn.execute("UPDATE users SET expiry_date=$1 WHERE user_id=$2", new_expiry, referrer_id)
            await xui_api.update_client_via_xui_api(row["uuid"], email, expiry_ms)
            try:
                await safe_bot_send_message(referrer_id, "🎉 <b>Бонус (5 друзей)!</b>\nВам добавлено 3 дня VPN!", parse_mode="HTML")
            except: pass

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    if not database.db_pool: return
    user_id = message.from_user.id
    username = message.from_user.username

    async with database.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        if not user:
            custom_id = generate_custom_id()
            referrer_id: int | None = None
            
            if command.args:
                ref_row = await conn.fetchrow("SELECT user_id FROM users WHERE custom_id = $1", command.args)
                if ref_row:
                    found_id = ref_row["user_id"]
                    if found_id != user_id:
                        referrer_id = found_id
                elif command.args.isdigit() and int(command.args) != user_id:
                    ref_check = await conn.fetchval("SELECT user_id FROM users WHERE user_id = $1", int(command.args))
                    if ref_check: referrer_id = int(command.args)
            
            await conn.execute("INSERT INTO users (user_id, username, custom_id, referrer_id) VALUES ($1, $2, $3, $4)", user_id, username, custom_id, referrer_id)
            if referrer_id:
                asyncio.create_task(process_referral_reward(referrer_id))
                try:
                    await safe_bot_send_message(referrer_id, f"👤 <b>Новый реферал!</b>\n@{username if username else user_id}", parse_mode="HTML")
                except: pass

    if not await check_sub(user_id):
        return await safe_message_answer(message, "🔒 <b>Доступ закрыт!</b>\nДля работы с ботом подпишитесь на наши каналы:", reply_markup=kb.sub_kb(), parse_mode="HTML")

    await safe_message_answer(message, "👋 <b>Добро пожаловать в VPN Shop!</b>", reply_markup=kb.main_menu_kb(user_id), parse_mode="HTML")


@dp.callback_query(F.data == "legal_menu")
async def open_legal_menu(callback: types.CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Контакты", callback_data="legal_contacts")],
        [InlineKeyboardButton(text="💸 Политика возврата", callback_data="legal_refund")],
        [InlineKeyboardButton(text="📄 Публичная оферта", callback_data="legal_offer")],
        [InlineKeyboardButton(text="🔒 Политика конфиденциальности", callback_data="legal_privacy")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="start")]
    ])
    
    await safe_message_edit_text(
        callback.message,
        "📜 <b>Правовая информация</b>\n\n"
        "Выберите интересующий вас раздел:",
        reply_markup=kb,
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "legal_contacts")
async def show_contacts(callback: types.CallbackQuery):
    await safe_callback_answer(callback)
    text = (
        "📞 <b>Контакты</b>\n\n"
        "Служба поддержки пользователей:\n"
        f"Telegram: @{ADMIN_USERNAME}\n" 
        "Email: aluminium.vpn@gmail.com\n\n" 
        "Время работы: 10:00 - 22:00 (МСК)"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="legal_menu")]])
    await safe_message_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "legal_refund")
async def show_refund_policy(callback: types.CallbackQuery):
    await safe_callback_answer(callback)
    text = (
        "💸 <b>Политика возврата</b>\n\n"
        "1. Пользователь может потребовать возврат денежных средств за товар при условии его неисправности по вине магазина или при невыдаче товара в сроки до 48 часов.\n\n"
        "2. Возврат денежных средств осуществляется на реквизиты пользователя, с которых производилась оплата.\n\n"
        "3. Возврат и замена товаров возможны только при условии неисправности самих товаров по вине магазина. (Если пользователь передумал, не понравился товар и т.д., то возврат и замена не предусмотрены.)\n\n"
        "4. Рассмотрение заявки и возврат средств осуществляется в течение 72 часов с момента обращения пользователя в поддержку магазина.\n\n"
        "5. Срок для подачи на возврат 72 часа по истечению срока на выдачу товара.\n\n"
        "6. Возврат средств осуществляется только с помощью технической поддержки телеграмм бота."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="legal_menu")]])
    await safe_message_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "legal_offer")
async def show_public_offer(callback: types.CallbackQuery):
    await safe_callback_answer(callback)
    text = (
        "📄 <b>Публичная оферта</b>\n\n"
        "Настоящая оферта является официальным предложением сервиса AluminiumVPN заключить договор купли-продажи услуг доступа к частной сети (VPN) дистанционным способом.\n\n"
        "<b>1. Предмет договора:</b> Предоставление Пользователю ключа доступа к серверам VPN.\n"
        "<b>2. Момент заключения:</b> Оплата услуг Пользователем означает безоговорочное принятие данной оферты.\n"
        "<b>3. Обязанности:</b> Сервис обязуется предоставить рабочий ключ доступа после оплаты. Пользователь обязуется не использовать сервис для противоправных действий.\n\n"
        "<i>Полный текст оферты предоставляется по запросу.</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="legal_menu")]])
    await safe_message_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "legal_privacy")
async def show_privacy_policy(callback: types.CallbackQuery):
    await safe_callback_answer(callback)
    text = (
        "🔒 <b>Политика конфиденциальности</b>\n\n"
        "Мы уважаем вашу анонимность и придерживаемся политики отсутствия логов (No-Logs Policy).\n\n"
        "<b>1. Сбор данных:</b> Мы храним только ваш Telegram ID для активации подписки. Мы НЕ собираем ФИО, номера телефонов или данные карт.\n"
        "<b>2. Использование данных:</b> Ваш ID используется исключительно для автоматической выдачи ключей доступа и технической поддержки.\n"
        "<b>3. История посещений:</b> Мы не ведем, не храним и не передаем третьим лицам логи вашего интернет-трафика.\n"
        "<b>4. Безопасность:</b> Все соединения зашифрованы современными протоколами."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="legal_menu")]])
    await safe_message_edit_text(callback.message, text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "profile")
async def profile_handler(callback: types.CallbackQuery):
    if not database.db_pool: return
    if not await check_sub(callback.from_user.id): return await safe_message_answer(callback.message, "🔒 Подпишитесь:", reply_markup=kb.sub_kb())

    user_id = callback.from_user.id
    async with database.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)

    if not user: return await safe_callback_answer(callback, "Ошибка данных", show_alert=True)

    status_emoji = "❌"
    status_text = "Не активен"
    
    if user["expiry_date"] and user["expiry_date"] > datetime.now():
        delta = user["expiry_date"] - datetime.now()
        days_left = delta.days
        hours_left = int(delta.seconds // 3600)
        status_emoji = "✅"
        status_text = f"Активен ({days_left} дн. {hours_left} ч.)"

    bot_info = await bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user['custom_id']}"

    text = (
        "👤 <b>Личный кабинет</b>\n\n"
        f"🆔 ID: <code>{user['custom_id']}</code>\n"
        f"📡 VPN: {status_emoji} {status_text}\n\n"
        f"👥 <b>Рефералы:</b> {user['referral_count']}\n"
        "🎁 <i>3 дня VPN за каждые 5 друзей!</i>\n\n"
        "🔗 <b>Ссылка для друзей:</b>\n"
        f"<code>{ref_link}</code>"
    )
    
    buttons = []
    if user["expiry_date"] and user["expiry_date"] > datetime.now() and user["uuid"]:
        buttons.append([InlineKeyboardButton(text="👁 Показать ключ доступа", callback_data="show_key")])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="start")])
    
    await safe_message_edit_text(callback.message, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")

@dp.callback_query(F.data == "daily_bonus")
async def get_daily_bonus(callback: types.CallbackQuery):
    if not database.db_pool: return
    if not await check_sub(callback.from_user.id):
        return await safe_message_answer(callback.message, "🔒 Для бонуса нужно подписаться:", reply_markup=kb.sub_kb())

    user_id = callback.from_user.id
    async with database.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT last_bonus_claim, expiry_date, uuid FROM users WHERE user_id = $1", user_id)
        
        if user and user["last_bonus_claim"]:
            if user["last_bonus_claim"] + timedelta(days=1) > datetime.now():
                next_claim = user["last_bonus_claim"] + timedelta(days=1)
                time_left = next_claim - datetime.now()
                hours = int(time_left.total_seconds() // 3600)
                minutes = int((time_left.total_seconds() % 3600) // 60)
                return await safe_callback_answer(callback, f"⏳ Бонус доступен раз в 24 часа.\nЖдать: {hours} ч. {minutes} мин.", show_alert=True)

        chance = random.randint(1, 100)
        if chance <= 90: hours_reward = random.randint(1, 12)
        elif chance <= 99: hours_reward = random.randint(13, 24)
        else: hours_reward = random.randint(25, 72)

        if user["expiry_date"] and user["expiry_date"] > datetime.now():
            new_expiry = user["expiry_date"] + timedelta(hours=hours_reward)
        else:
            new_expiry = datetime.now() + timedelta(hours=hours_reward)
            
        expiry_ms = int(new_expiry.timestamp() * 1000)
        email = f"user_{user_id}"

        try:
            if user["uuid"]:
                await xui_api.update_client_via_xui_api(user["uuid"], email, expiry_ms)
                final_uuid = user["uuid"]
            else:
                new_uuid = str(uuid.uuid4())
                await xui_api.add_client_via_xui_api(new_uuid, email, limit_ip=1, expiry_time=expiry_ms)
                final_uuid = new_uuid
                await conn.execute("UPDATE users SET uuid=$1 WHERE user_id=$2", final_uuid, user_id)
            
            await conn.execute("UPDATE users SET expiry_date=$1, last_bonus_claim=$2 WHERE user_id=$3", new_expiry, datetime.now(), user_id)

        except Exception as e:
            logger.error(f"Bonus error: {e}")
            return await safe_callback_answer(callback, "❌ Ошибка сервера, попробуйте позже", show_alert=True)

    if hours_reward >= 24:
        days = hours_reward // 24
        hrs = hours_reward % 24
        time_text = f"{days} дн." + (f" {hrs} ч." if hrs > 0 else "")
    else:
        time_text = f"{hours_reward} час(ов)"

    key_link = xui_api.generate_vless_link(final_uuid, email)
    try: await callback.message.delete()
    except: pass

    guide_text = (
        f"🎁 <b>Вы получили бонус: {time_text}!</b>\n\n"
        f"Ваша подписка продлена.\n\n"
        f"🔑 <b>Ваш ключ доступа:</b>\n"
        f"<tg-spoiler><code>{key_link}</code></tg-spoiler>\n\n"
        f"<i>Следующий бонус через 24 часа.</i>"
    )
    await safe_message_answer(callback.message, guide_text, reply_markup=kb.back_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "check_sub_btn")
async def check_sub_btn(callback: types.CallbackQuery):
    if await check_sub(callback.from_user.id):
        await callback.message.delete()
        await safe_message_answer(callback.message, "👋 <b>Спасибо! Доступ открыт.</b>", reply_markup=kb.main_menu_kb(callback.from_user.id), parse_mode="HTML")
    else:
        await safe_callback_answer(callback, "❌ Вы не подписаны!", show_alert=True)

@dp.callback_query(F.data == "start")
async def cb_start(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    if not await check_sub(callback.from_user.id):
        return await safe_message_answer(callback.message, "🔒 Подписка не найдена!", reply_markup=kb.sub_kb())
    try:
        await safe_message_edit_text(callback.message, "👋 <b>Главное меню</b>", reply_markup=kb.main_menu_kb(callback.from_user.id), parse_mode="HTML")
    except:
        await safe_message_answer(callback.message, "👋 Главное меню", reply_markup=kb.main_menu_kb(callback.from_user.id))

@dp.message(CommandStart())
async def cmd_start(message: types.Message, command: CommandObject):
    if not database.db_pool: return
    user_id = message.from_user.id
    username = message.from_user.username

    async with database.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT user_id FROM users WHERE user_id = $1", user_id)
        if not user:
            custom_id = generate_custom_id()
            referrer_id: int | None = None
            
            if command.args:
                ref_row = await conn.fetchrow("SELECT user_id FROM users WHERE custom_id = $1", command.args)
                if ref_row:
                    found_id = ref_row["user_id"]
                    if found_id != user_id:
                        referrer_id = found_id
                elif command.args.isdigit() and int(command.args) != user_id:
                    ref_check = await conn.fetchval("SELECT user_id FROM users WHERE user_id = $1", int(command.args))
                    if ref_check: referrer_id = int(command.args)
            
            await conn.execute("INSERT INTO users (user_id, username, custom_id, referrer_id) VALUES ($1, $2, $3, $4)", user_id, username, custom_id, referrer_id)
            if referrer_id:
                asyncio.create_task(process_referral_reward(referrer_id))
                try:
                    await safe_bot_send_message(referrer_id, f"👤 <b>Новый реферал!</b>\n@{username if username else user_id}", parse_mode="HTML")
                except: pass

    if not await check_sub(user_id):
        return await safe_message_answer(message, "🔒 <b>Доступ закрыт!</b>\nДля работы с ботом подпишитесь на наши каналы:", reply_markup=kb.sub_kb(), parse_mode="HTML")

    await safe_message_answer(message, "👋 <b>Добро пожаловать в VPN Shop!</b>", reply_markup=kb.main_menu_kb(user_id), parse_mode="HTML")

@dp.callback_query(F.data == "show_key")
async def show_key_handler(callback: types.CallbackQuery):
    if not database.db_pool: return
    user_id = callback.from_user.id
    async with database.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT uuid, expiry_date FROM users WHERE user_id=$1", user_id)

    if not user or not user["uuid"]:
        return await safe_callback_answer(callback, "❌ У вас нет активного ключа", show_alert=True)
    if not user["expiry_date"] or user["expiry_date"] <= datetime.now():
        return await safe_callback_answer(callback, "❌ Ваша подписка истекла", show_alert=True)

    key = xui_api.generate_vless_link(user["uuid"], f"user_{user_id}")
    await safe_message_edit_text(callback.message, get_guide_text(key), reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад в профиль", callback_data="profile")]]), parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data == "buy_1_month")
async def create_invoice(callback: types.CallbackQuery):
    await callback.answer()

    if not await check_sub(callback.from_user.id):
        try:
            await safe_message_edit_text(
                callback.message, 
                "🔒 <b>Ошибка доступа!</b>\nДля покупки VPN необходимо подписаться на наши каналы:", 
                reply_markup=kb.sub_kb(), 
                parse_mode="HTML"
            )
        except:
            await safe_message_answer(
                callback.message, 
                "🔒 <b>Ошибка доступа!</b>\nДля покупки VPN необходимо подписаться на наши каналы:", 
                reply_markup=kb.sub_kb(), 
                parse_mode="HTML"
            )
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐️ Оплатить Звездами (100 ⭐️)", callback_data="pay_stars")], 
        [InlineKeyboardButton(text="💎 Оплатить Криптой ($1)", callback_data="pay_crypto")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="start")]
    ])
    
    text = (
        "💳 <b>Выберите способ оплаты</b>\n\n"
        "⭐️ <b>Telegram Stars:</b> Оплата картой прямо в приложении.\n"
        "💎 <b>Криптовалюта:</b> USDT, TON, BTC через CryptoPay.\n\n"
        "<i>Стоимость: 1 месяц доступа.</i>"
    )

    try:
        await safe_message_edit_text(
            callback.message, 
            text, 
            reply_markup=keyboard, 
            parse_mode="HTML"
        )
    except Exception:
        try: await callback.message.delete()
        except: pass
        
        await safe_message_answer(
            callback.message, 
            text, 
            reply_markup=keyboard, 
            parse_mode="HTML"
        )

@dp.callback_query(F.data == "pay_crypto")
async def create_crypto_invoice(callback: types.CallbackQuery):
    if not crypto: return
    try:
        invoice = await crypto.create_invoice(amount=1.00, fiat="USD", currency_type="fiat", accepted_assets="USDT,TON,BTC,LTC", description="VPN (30 days)", expires_in=600)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔗 Выбрать валюту и оплатить", url=invoice.bot_invoice_url)],
            [InlineKeyboardButton(text="🔄 Проверить оплату", callback_data=f"check_{invoice.invoice_id}")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="start")]
        ])
        await safe_message_edit_text(
            callback.message, 
            "🧾 <b>Счет на оплату</b>\n"
            "Сумма: <b>$1.00</b>\n"
            "Можно оплатить: USDT, TON, TRX, BTC, ETH...\n\n"
            "<i>Нажмите кнопку ниже, чтобы выбрать валюту.</i>", 
            reply_markup=keyboard, 
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Invoice error: {e}")
        await safe_callback_answer(callback, "❌ Ошибка создания счета", show_alert=True)

@dp.callback_query(F.data == "pay_stars")
async def send_stars_invoice(callback: types.CallbackQuery):
    await callback.message.delete()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐️ Оплатить 100 XTR", pay=True)],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="buy_1_month")]
    ])

    await callback.message.answer_invoice(
        title="VPN (30 дней)",
        description="Быстрый VPN. Протокол VLESS Reality + Vision.",
        payload="vpn_month_sub",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="1 мес.", amount=100)],
        start_parameter="vpn_sub",
        reply_markup=keyboard 
    )

@dp.pre_checkout_query()
async def pre_checkout_handler(query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def success_payment_handler(message: types.Message):
    if message.successful_payment.invoice_payload != "vpn_month_sub": return
    user_id = message.from_user.id
    if not database.db_pool: return

    async with database.db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE users SET expiry_date = GREATEST(expiry_date, NOW()) + INTERVAL '30 days', expired_notification_sent = FALSE WHERE user_id = $1 RETURNING uuid, expiry_date", 
            user_id
        )
        expiry_ms = int(row["expiry_date"].timestamp() * 1000)
        email = f"user_{user_id}"

        if row["uuid"]:
            await xui_api.update_client_via_xui_api(row["uuid"], email, expiry_ms)
            key = xui_api.generate_vless_link(row["uuid"], email)
        else:
            new_uuid = str(uuid.uuid4())
            await xui_api.add_client_via_xui_api(new_uuid, email, limit_ip=1, expiry_time=expiry_ms)
            await conn.execute("UPDATE users SET uuid = $1 WHERE user_id = $2", new_uuid, user_id)
            key = xui_api.generate_vless_link(new_uuid, email)

    await safe_message_answer(message, get_guide_text(key), reply_markup=kb.back_kb(), parse_mode="HTML", disable_web_page_preview=True)

@dp.callback_query(F.data.startswith("check_"))
async def check_invoice(callback: types.CallbackQuery):
    if not crypto: return
    inv_id = int(callback.data.split("_")[1])
    try:
        invs = await crypto.get_invoices(invoice_ids=inv_id)
        invoice = invs[0] if isinstance(invs, list) else invs
    except: return await safe_callback_answer(callback, "❌ Ошибка проверки", show_alert=True)

    if invoice.status == "paid":
        await safe_callback_answer(callback, "✅ Оплата получена! Генерируем ключ...", show_alert=True)
        user_id = callback.from_user.id
        async with database.db_pool.acquire() as conn:
             row = await conn.fetchrow(
                "UPDATE users SET expiry_date = GREATEST(expiry_date, NOW()) + INTERVAL '30 days', expired_notification_sent = FALSE WHERE user_id = $1 RETURNING uuid, expiry_date", 
                user_id
            )
             expiry_ms = int(row["expiry_date"].timestamp() * 1000)
             email = f"user_{user_id}"
             
             if row["uuid"]:
                await xui_api.update_client_via_xui_api(row["uuid"], email, expiry_ms)
                key = xui_api.generate_vless_link(row["uuid"], email)
             else:
                new_uuid = str(uuid.uuid4())
                await xui_api.add_client_via_xui_api(new_uuid, email, limit_ip=1, expiry_time=expiry_ms)
                await conn.execute("UPDATE users SET uuid = $1 WHERE user_id = $2", new_uuid, user_id)
                key = xui_api.generate_vless_link(new_uuid, email)
        
        await safe_message_edit_text(callback.message, get_guide_text(key), reply_markup=kb.back_kb(), parse_mode="HTML", disable_web_page_preview=True)
        
    elif invoice.status == "active":
        await safe_callback_answer(callback, "⏳ Оплата еще не поступила", show_alert=True)
    else:
        await safe_message_edit_text(callback.message, "❌ Счет истек.", reply_markup=kb.back_kb())


@dp.callback_query(F.data == "support")
async def support_start(callback: types.CallbackQuery, state: FSMContext):
    if not database.db_pool: return
    if not callback.from_user.username:
        return await safe_callback_answer(callback, "❌ Установите Username в Telegram!", show_alert=True)

    user_id = callback.from_user.id

    async with database.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT last_support_time FROM users WHERE user_id = $1", user_id)
        if user and user["last_support_time"]:
            last_time = user["last_support_time"].replace(tzinfo=None)
            minutes_passed = (datetime.utcnow() - last_time).total_seconds() / 60
            
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
        reply_markup=kb.back_kb(),
        parse_mode="HTML"
    )
    await state.set_state(SupportState.waiting_for_question)

@dp.message(StateFilter(SupportState.waiting_for_question))
async def support_receive_msg(message: types.Message, state: FSMContext):
    if not database.db_pool: await state.clear(); return

    user_id = message.from_user.id
    
    async with database.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT last_support_time FROM users WHERE user_id = $1", user_id)
        if user and user["last_support_time"]:
            last_time = user["last_support_time"].replace(tzinfo=None)
            if (datetime.utcnow() - last_time).total_seconds() < 3600:
                await safe_message_answer(message, "⏳ Прошел меньше часа с прошлого обращения.")
                await state.clear()
                return

    if not ADMIN_ID:
        await safe_message_answer(message, "❌ Поддержка не настроена.")
        await state.clear()
        return

    try:
        await safe_bot_send_message(
            ADMIN_ID, 
            f"📩 <b>Тикет</b>\nОт: @{message.from_user.username} (ID: <code>{message.from_user.id}</code>)\n\n{message.text}", 
            reply_markup=kb.admin_ticket_kb(message.from_user.id),
            parse_mode="HTML"
        )

        async with database.db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET last_support_time = $1 WHERE user_id = $2", datetime.utcnow(), user_id)

        await safe_message_answer(message, "✅ <b>Отправлено!</b> Администратор ответит вам в ближайшее время.", reply_markup=kb.back_kb(), parse_mode="HTML")
    except: 
        await safe_message_answer(message, "❌ Ошибка отправки.")
    
    await state.clear()

@dp.callback_query(F.data.startswith("ans_"))
async def admin_reply_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.update_data(target_id=int(callback.data.split("_")[1]))
    await safe_message_answer(callback.message, "✍️ Введите ответ:")
    await state.set_state(SupportState.waiting_for_answer)
    await safe_callback_answer(callback)

@dp.message(StateFilter(SupportState.waiting_for_answer))
async def admin_send_reply(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    data = await state.get_data()
    try:
        await safe_bot_send_message(data["target_id"], f"👨‍💻 <b>Ответ поддержки:</b>\n\n{message.text}", parse_mode="HTML")
        await safe_message_answer(message, "✅ Отправлено!")
    except: await safe_message_answer(message, "❌ Не удалось отправить.")
    await state.clear()

@dp.callback_query(F.data == "del_msg")
async def delete_msg(callback: types.CallbackQuery):
    await callback.message.delete()


@dp.callback_query(F.data == "admin_panel")
async def admin_panel_open(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👥 Управление пользователями", callback_data="admin_users_list")],
        [InlineKeyboardButton(text="📢 Создать объявление", callback_data="admin_create_announce")],
        [InlineKeyboardButton(text="🔙 В главное меню", callback_data="start")]
    ])

    await safe_message_edit_text(
        callback.message,
        "🛠 <b>Админ панель</b>\n\nВыберите действие:",
        reply_markup=kb,
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "admin_create_announce")
async def ask_announcement_text(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_panel")]
    ])
    
  
    await safe_message_edit_text(
        callback.message, 
        "✍️ <b>Введите текст объявления:</b>\n\n"
        "Вы можете использовать HTML разметку (жирный, ссылки и т.д.).\n"
        "Помните: сообщение уйдет <u>ВСЕМ</u> пользователям бота.", 
        reply_markup=kb,
        parse_mode="HTML"
    )
    
  
    await state.update_data(announce_msg_id=callback.message.message_id)
    await state.set_state(AdminState.waiting_for_announcement_text)


@dp.message(StateFilter(AdminState.waiting_for_announcement_text))
async def broadcast_announcement(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    
   
    data = await state.get_data()
    menu_msg_id = data.get("announce_msg_id")
    
  
    try:
        if menu_msg_id:
            await bot.edit_message_text(
                "⏳ <b>Рассылка запущена...</b>\nЭто может занять некоторое время.",
                chat_id=message.chat.id,
                message_id=menu_msg_id,
                parse_mode="HTML"
            )
        else:
            
            msg = await message.answer("⏳ <b>Рассылка запущена...</b>", parse_mode="HTML")
            menu_msg_id = msg.message_id
    except Exception:
        pass

    if not database.db_pool: 
        await message.answer("❌ Ошибка базы данных")
        await state.clear()
        return

    count_success = 0
    count_blocked = 0

    async with database.db_pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users")
        
    for row in users:
        user_id = row['user_id']
        try:
           
            await message.send_copy(chat_id=user_id)
            count_success += 1
        except Exception:
            count_blocked += 1
        
        await asyncio.sleep(0.05)

  
    try:
        await message.delete()
    except Exception:
        pass

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 В админ панель", callback_data="admin_panel")]
    ])
    
    result_text = (
        f"✅ <b>Объявление разослано!</b>\n\n"
        f"📨 Получили: {count_success}\n"
        f"🚫 Заблокировали бота: {count_blocked}"
    )

    try:
        await bot.edit_message_text(
            result_text,
            chat_id=message.chat.id,
            message_id=menu_msg_id,
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
       
        await message.answer(result_text, reply_markup=kb, parse_mode="HTML")
        
    await state.clear()

@dp.callback_query(F.data == "admin_users_list")
async def admin_users_list(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
   
    await state.update_data(admin_search_query=None, admin_filter_active=False)
    await show_user_page(callback.message, state, page=0, is_edit=True)

async def show_user_page(message_obj: types.Message, state: FSMContext, page: int, is_edit: bool = False, message_id_to_edit: int = None):
    if not database.db_pool: return
    data = await state.get_data()
    search_query = data.get("admin_search_query")
    filter_active = data.get("admin_filter_active", False)

    where = []
    params = []
    idx = 1

    if filter_active: 
        where.append(f"expiry_date > ${idx}")
        params.append(datetime.now())
        idx += 1

    if search_query:
        where.append(f"(username ILIKE ${idx} OR CAST(user_id AS TEXT) = ${idx} OR custom_id = ${idx})")
        params.append(search_query)
        idx += 1

    where_sql = " WHERE " + " AND ".join(where) if where else ""
    async with database.db_pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM users{where_sql}", *params)

        if total == 0:
             text = f"🛠 <b>Админ панель</b>\nСтатус: {'🔍 Поиск: ' + search_query if search_query else 'Все'}\n\n🤷‍♂️ <b>Пользователей не найдено.</b>"
             bts = []
             if search_query or filter_active: bts.append([InlineKeyboardButton(text="❌ Сбросить фильтры", callback_data="admin_reset_filters")])
             bts.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="admin_panel")])
             markup = InlineKeyboardMarkup(inline_keyboard=bts)

             if message_id_to_edit:
                try:
                    await bot.edit_message_text(text=text, chat_id=message_obj.chat.id, message_id=message_id_to_edit, reply_markup=markup, parse_mode="HTML")
                    return
                except: pass

             if is_edit: await safe_message_edit_text(message_obj, text, reply_markup=markup, parse_mode="HTML")
             else: await safe_message_answer(message_obj, text, reply_markup=markup, parse_mode="HTML")
             return

        params.append(page)
        user = await conn.fetchrow(f"SELECT user_id, custom_id, username, referral_count, expiry_date, uuid FROM users{where_sql} ORDER BY user_id LIMIT 1 OFFSET ${idx}", *params)

    status_str = "🔘 Все"
    if filter_active: status_str = "🟢 Активные"
    if search_query: status_str += f" | 🔍 {search_query}"

    status_text = "🔴 Не активен"
    if user["expiry_date"] and user["expiry_date"] > datetime.now():
        delta = user["expiry_date"] - datetime.now()
        days_left = delta.days
        hours_left = int(delta.seconds // 3600)
        status_text = f"🟢 Активен ({days_left} дн. {hours_left} ч.)"
    elif user["expiry_date"]:
        status_text = "🔴 Истек"

    username_txt = f"@{user['username']}" if user['username'] else "Нет юзернейма"
    text = (
        f"🛠 <b>Админ панель</b>\n"
        f"Режим: {status_str}\n"
        f"👤 <b>Пользователь {page + 1} из {total}</b>\n\n"
        f"🆔 ID: <code>{user['user_id']}</code>\n"
        f"🏷 Custom ID: <code>{user['custom_id']}</code>\n"
        f"👤 Login: {username_txt}\n\n"
        f"👥 Рефералов: <b>{user['referral_count']}</b>\n"
        f"📡 VPN: {status_text}\n"
        f"🔑 UUID: <code>{user['uuid'] if user['uuid'] else 'Нет'}</code>"
    )
    
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"admin_page_{page-1}"))
    if page < total - 1: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"admin_page_{page+1}"))
    
    rows = [nav, [InlineKeyboardButton(text="✏️ Ред. дни", callback_data=f"admin_edit_days_{user['user_id']}_{page}"), InlineKeyboardButton(text="✏️ Ред. рефералов", callback_data=f"admin_edit_refs_{user['user_id']}_{page}")]]
    filter_btn = "Показать только активные" if not filter_active else "Показать всех"
    rows.append([InlineKeyboardButton(text=f"👁 {filter_btn}", callback_data="admin_toggle_filter")])
    search_btn = "🔍 Поиск по @username / ID" if not search_query else "❌ Сбросить поиск"
    search_cb = "admin_search_start" if not search_query else "admin_reset_filters"
    rows.append([InlineKeyboardButton(text=search_btn, callback_data=search_cb)])
    rows.append([InlineKeyboardButton(text="🔙 Назад в меню", callback_data="admin_panel")])

    markup = InlineKeyboardMarkup(inline_keyboard=rows)

    if message_id_to_edit:
        try:
            await bot.edit_message_text(text=text, chat_id=message_obj.chat.id, message_id=message_id_to_edit, reply_markup=markup, parse_mode="HTML")
            return
        except Exception:
            pass

    if is_edit: await safe_message_edit_text(message_obj, text, reply_markup=markup, parse_mode="HTML")
    else: await safe_message_answer(message_obj, text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data.startswith("admin_page_"))
async def admin_pagination(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID: return
    await show_user_page(callback.message, state, int(callback.data.split("_")[2]), is_edit=True)

@dp.callback_query(F.data == "admin_toggle_filter")
async def admin_toggle_filter(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await state.update_data(admin_filter_active=not data.get("admin_filter_active", False))
    await show_user_page(callback.message, state, page=0, is_edit=True)

@dp.callback_query(F.data == "admin_reset_filters")
async def admin_reset_filters(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(admin_search_query=None)
    await show_user_page(callback.message, state, page=0, is_edit=True)

@dp.callback_query(F.data == "admin_search_start")
async def admin_search_start(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(search_msg_id=callback.message.message_id)
    
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
    
    query = message.text.strip().replace("@", "")
    
    try:
        await message.delete()
    except:
        pass
    
    await state.update_data(admin_search_query=query)
    await state.set_state(None)

    data = await state.get_data()
    panel_id = data.get("search_msg_id")

    await show_user_page(message, state, page=0, is_edit=False, message_id_to_edit=panel_id)

@dp.callback_query(F.data.startswith("admin_edit_days_"))
async def admin_edit_days_start(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    await state.update_data(editing_user_id=int(parts[3]), return_page=int(parts[4]), panel_msg_id=callback.message.message_id)
    await safe_message_edit_text(
        callback.message, 
        f"📅 <b>Редактирование дней</b>\nID: <code>{parts[3]}</code>\n\nПросто отправьте число:\n• `30` — добавить 30 дней\n• `-5` — отнять 5 дней\n• `0` — сбросить на 'сейчас'", 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data=f"admin_page_{parts[4]}")]]) ,
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_for_new_days)

@dp.message(StateFilter(AdminState.waiting_for_new_days))
async def admin_save_days(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try: await message.delete()
    except: pass
    try: days = int(message.text)
    except: return
    data = await state.get_data()
    uid = data["editing_user_id"]
    
    async with database.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", uid)
        
        if days == 0:
            new_d = datetime.now() - timedelta(minutes=1)
        else:
            base = user["expiry_date"] if user["expiry_date"] and user["expiry_date"] > datetime.now() else datetime.now()
            new_d = base + timedelta(days=days)

        if user["uuid"]: 
            try:
                await xui_api.update_client_via_xui_api(user["uuid"], f"user_{uid}", int(new_d.timestamp()*1000))
            except Exception as e:
                logger.error(f"X-UI Update Error: {e}")

        notification_sent = False
        
        if new_d < datetime.now():
            try:
                kb_renew = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="buy_1_month")]
                ])
                await safe_bot_send_message(
                    uid,
                    "⛔️ <b>Ваша подписка истекла!</b>\n\n"
                    "VPN отключен. Чтобы продолжить пользоваться интернетом без ограничений, пожалуйста, продлите подписку.",
                    reply_markup=kb_renew,
                    parse_mode="HTML"
                )
                notification_sent = True 
            except Exception:
                notification_sent = True 
        else:
            notification_sent = False 

        await conn.execute(
            "UPDATE users SET expiry_date=$1, expired_notification_sent=$2 WHERE user_id=$3", 
            new_d, notification_sent, uid
        )

    await state.clear()
    await show_user_page(message, state, data["return_page"], is_edit=False, message_id_to_edit=data["panel_msg_id"])

@dp.callback_query(F.data.startswith("admin_edit_refs_"))
async def admin_edit_refs_start(callback: types.CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    await state.update_data(editing_user_id=int(parts[3]), return_page=int(parts[4]), panel_msg_id=callback.message.message_id)
    await safe_message_edit_text(
        callback.message, 
        f"👥 <b>Редактирование рефералов</b>\nID: <code>{parts[3]}</code>\n\nВведите новое количество:", 
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data=f"admin_page_{parts[4]}")]]) ,
        parse_mode="HTML"
    )
    await state.set_state(AdminState.waiting_for_new_refs)

@dp.message(StateFilter(AdminState.waiting_for_new_refs))
async def admin_save_refs(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID: return
    try: await message.delete()
    except: pass
    try: refs = int(message.text)
    except: return
    data = await state.get_data()
    async with database.db_pool.acquire() as conn:
        await conn.execute("UPDATE users SET referral_count=$1 WHERE user_id=$2", refs, data["editing_user_id"])
    await state.clear()
    await show_user_page(message, state, data["return_page"], is_edit=False, message_id_to_edit=data["panel_msg_id"])

async def check_expired_subscriptions():
    """Фоновая задача: проверяет истекшие подписки и шлет уведомления."""
    while True:
        try:
            if database.db_pool:
                async with database.db_pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT user_id FROM users WHERE expiry_date < NOW() AND (expired_notification_sent IS FALSE OR expired_notification_sent IS NULL)"
                    )
                    
                    for row in rows:
                        user_id = row["user_id"]
                        
                      
                        kb_renew = InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="buy_1_month")]
                        ])
                        
                        try:
                            await safe_bot_send_message(
                                user_id,
                                "⛔️ <b>Ваша подписка истекла!</b>\n\n"
                                "VPN отключен. Чтобы продолжить пользоваться интернетом без ограничений, пожалуйста, продлите подписку.",
                                reply_markup=kb_renew,
                                parse_mode="HTML"
                            )
                            await conn.execute("UPDATE users SET expired_notification_sent = TRUE WHERE user_id = $1", user_id)
                        except Exception as e:
                            await conn.execute("UPDATE users SET expired_notification_sent = TRUE WHERE user_id = $1", user_id)

        except Exception as e:
            logger.error(f"Ошибка в чекере подписок: {e}")
        
        await asyncio.sleep(300)

async def main():
    global crypto
    crypto = AioCryptoPay(token=os.getenv("CRYPTO_TOKEN"), network=Networks.MAIN_NET)
    
    await xui_api.init_vpn_api()
    await database.init_db()

    asyncio.create_task(check_expired_subscriptions())

    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("🚀 Бот запущен (Polling)")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        if crypto: await crypto.close()
        if database.db_pool: await database.db_pool.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")