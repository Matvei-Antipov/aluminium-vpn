from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from config import ADMIN_ID, CHANNEL_URL, CHANNEL_2_URL

def main_menu_kb(user_id: int) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton(text="⚡️ Купить VPN (1 мес - $1)", callback_data="buy_1_month")],
        [InlineKeyboardButton(text="🎁 Ежедневный бонус", callback_data="daily_bonus")],
        [InlineKeyboardButton(text="📜 Правила и Оферта", callback_data="legal_menu")],
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