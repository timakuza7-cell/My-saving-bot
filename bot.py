import logging
import sqlite3
import asyncio
import hashlib
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# Настройка логирования
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# --- НАСТРОЙКИ БОТА ---
TOKEN = "8713270514:AAH_iUzAutJrPal8KpLNV-lzA6wSm1gSRI4"
ADMIN_ID = 123456789  # Поставьте сюда ваш настоящий Telegram ID (числом)

# Настройки Aaio (для теста можно оставить заглушки)
AAIO_MERCHANT_ID = "YOUR_MERCHANT_ID"
AAIO_SECRET_1 = "YOUR_SECRET_1"
PREMIUM_PRICE_RUB = 150.0 # Цена подписки для генерации ссылок

# Состояния диалогов
WAITING_START_TIME, WAITING_EDIT_TIME = range(2)
(
    CHOOSE_CATEGORY,
    WAITING_NAME,
    CHOOSE_CURRENCY,
    SELECT_MODE,
    WAITING_PRICE,
    WAITING_SAVED,
    WAITING_DAILY,
    WAITING_DATE,
) = range(8)
WAITING_ADD_SUM = 0

DB_FILE = "savings_bot.db"

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_users (
            user_id INTEGER PRIMARY KEY,
            reminder_time TEXT DEFAULT '21:00',
            is_premium INTEGER DEFAULT 0
        )
    """)
    
    # Таблица целей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            category TEXT,
            name TEXT,
            price REAL,
            saved REAL,
            daily REAL,
            target_date TEXT,
            mode TEXT,
            currency TEXT DEFAULT 'KZT'
        )
    """)
    
    # ДИНАМИЧЕСКИЕ СПРАВОЧНИКИ
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            display_name TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS currencies (
            code TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT
        )
    """)
    
    # Таблица платежей для аналитики
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            currency TEXT,
            pay_date TEXT
        )
    """)
    
    # Заполняем стартовые данные, если таблица пуста
    cursor.execute("SELECT COUNT(*) FROM categories")
    if cursor.fetchone()[0] == 0:
        default_cats = [
            ('🏠 Дом', '🏠 Дом / Недвижимость'),
            ('🚗 Авто', '🚗 Авто / Транспорт'),
            ('📱 Гаджеты', '📱 Гаджеты / Техника'),
            ('✈️ Отпуск', '✈️ Путешествия'),
            ('🛍 Другое', '🛍 Другое / Покупки')
        ]
        cursor.executemany("INSERT INTO categories (name, display_name) VALUES (?, ?)", default_cats)
        
    cursor.execute("SELECT COUNT(*) FROM currencies")
    if cursor.fetchone()[0] == 0:
        default_currs = [
            ('KZT', '₸', 'Казахстанский тенге'),
            ('USD', '$', 'Доллар США'),
            ('RUB', '₽', 'Российский рубль'),
            ('EUR', '€', 'Евро')
        ]
        cursor.executemany("INSERT INTO currencies (code, symbol, name) VALUES (?, ?, ?)", default_currs)

    conn.commit()
    conn.close()

# --- ФУНКЦИИ КЛИЕНТОВ И АДМИНКИ ДЛЯ БД ---
def get_user_status(user_id: int) -> dict:
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM active_users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"user_id": user_id, "reminder_time": "21:00", "is_premium": 0}

def get_db_categories():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT name, display_name FROM categories")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_db_currencies():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT code, symbol, name FROM currencies")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_currency_symbol(code: str) -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM currencies WHERE code = ?", (code,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "₸"

def add_active_user(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO active_users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def update_user_reminder(user_id: int, reminder_time: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE active_users SET reminder_time = ? WHERE user_id = ?", (reminder_time, user_id))
    conn.commit()
    conn.close()

def create_goal(user_id: int, data: dict):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO goals (user_id, category, name, price, saved, daily, target_date, mode, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, data['category'], data['name'], data['price'], data['saved'], data.get('daily'), data.get('target_date'), data['mode'], data.get('currency', 'KZT')))
    conn.commit()
    conn.close()

def get_user_goals(user_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM goals WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_goal_by_id(goal_id: int):
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def add_money_to_goal(goal_id: int, amount: float):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE goals SET saved = saved + ? WHERE id = ?", (amount, goal_id))
    conn.commit()
    conn.close()

def delete_goal(goal_id: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    conn.commit()
    conn.close()

def format_money(amount: float, currency_code: str) -> str:
    symbol = get_currency_symbol(currency_code)
    formatted_num = f"{amount:,.2f}".replace(",", " ").replace(".00", "")
    return f"{formatted_num} {symbol}"

# --- ГЕНЕРАТОР ССЫЛОК AAIO ---
def generate_aaio_url(order_id, amount, currency="RUB"):
    sign_str = f"{AAIO_MERCHANT_ID}:{amount}:{currency}:{AAIO_SECRET_1}:{order_id}"
    sign = hashlib.sha256(sign_str.encode('utf-8')).hexdigest()
    return f"https://aaio.so/merchant/pay?m={AAIO_MERCHANT_ID}&oa={amount}&o={order_id}&s={sign}&lang=ru"

# --- ФОНОВЫЙ ЦИКЛ НАПОМИНАНИЙ ---
async def custom_reminder_loop(bot):
    while True:
        now = datetime.now()
        current_time_str = now.strftime("%H:%M")
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id FROM active_users WHERE reminder_time = ?", (current_time_str,))
        user_ids = [r[0] for r in cursor.fetchall()]
        conn.close()
        
        for u_id in user_ids:
            goals = get_user_goals(u_id)
            if not goals: continue
            text = "🔔 **Время отложить деньги в копилку!**\n\nТвой прогресс:\n"
            for g in goals:
                left = max(0.0, g['price'] - g['saved'])
                if left > 0:
                    text += f"• {g['category']} *{g['name']}* (Осталось: {format_money(left, g['currency'])})\n"
            text += "\nВнести средства: /goals 🚀"
            try:
                await bot.send_message(chat_id=u_id, text=text, parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Ошибка отправки напоминания: {e}")
        await asyncio.sleep(60)

# --- ДИАЛОГ НАСТРОЙКИ ВРЕМЕНИ И СТАРТА ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    add_active_user(user_id)
    await update.message.reply_text(
        "🎯 **Привет! Я твой умный бот-копилка.**\n\n"
        "Напиши время для ежедневных напоминаний в формате **ЧЧ:ММ** (например `21:00`):",
        parse_mode="Markdown"
    )
    return WAITING_START_TIME

async def start_time_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    try:
        valid_time = datetime.strptime(update.message.text.strip(), "%H:%M")
        time_str = valid_time.strftime("%H:%M")
        update_user_reminder(user_id, time_str)
        await update.message.reply_text(f"✅ Напоминания включены на **{time_str}**!\n\nСоздай первую цель: /newgoal", parse_mode="Markdown")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Неверный формат. Напиши время как `20:00`:")
        return WAITING_START_TIME

# --- ПРОСМОТР И ОБНОВЛЕНИЕ КАРТОЧКИ ЦЕЛИ ---
def generate_goal_card(goal_id: int):
    g = get_goal_by_id(goal_id)
    if not g: return "Цель не найдена.", None
    
    curr = g["currency"]
    percent = min(100, int((g['saved'] / g['price']) * 100)) if g['price'] > 0 else 0
    progress_bar = "🟩" * (percent // 10) + "⬜" * (10 - (percent // 10))
    left = max(0.0, g['price'] - g['saved'])

    info = f"🎯 **{g['category']} — {g['name']}**\n"
    info += f"——————————————————\n"
    info += f"💰 Цель: {format_money(g['price'], curr)}\n"
    info += f"💵 Накоплено: {format_money(g['saved'], curr)} ({percent}%)\n"
    info += f"📊 Прогресс: [{progress_bar}]\n"
    info += f"📉 Осталось: {format_money(left, curr)}\n\n"

    if left > 0:
        if g["mode"] == "days":
            days = int(-(-left // g["daily"]))
            info += f"📅 По {format_money(g['daily'], curr)}/день копить: **{days} дней**.\n Финиш: **{(datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')}**"
        else:
            t_date = datetime.strptime(g["target_date"], "%d.%m.%Y")
            days = max(1, (t_date.date() - datetime.now().date()).days)
            info += f"📅 Срок до: **{g['target_date']}** ({days} дн.).\n Нужно откладывать: **{format_money(left / days, curr)}/день**"
    else:
        info += "🎉 **Поздравляем! Цель успешно достигнута!**"

    keyboard = [
        [InlineKeyboardButton("➕ Внести средства", callback_data=f"addmoney_{g['id']}")],
        [InlineKeyboardButton("🗑 Удалить цель", callback_data=f"del_{g['id']}")],
        [InlineKeyboardButton("⬅️ Назад к списку", callback_data="back_to_list")]
    ]
    return info, InlineKeyboardMarkup(keyboard)

async def list_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    goals = get_user_goals(user_id)
    if not goals:
        await update.message.reply_text("📊 У тебя еще нет целей. Создай через /newgoal")
        return
    keyboard = [[InlineKeyboardButton(f"{g['category']} {g['name']}", callback_data=f"view_{g['id']}")] for g in goals]
    await update.message.reply_text("💼 **Твои активные копилки:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def goal_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("view_"):
        goal_id = int(data.split("_")[1])
        info, reply_markup = generate_goal_card(goal_id)
        await query.edit_message_text(info, parse_mode="Markdown", reply_markup=reply_markup)
    elif data.startswith("addmoney_"):
        goal_id = int(data.split("_")[1])
        context.user_data["current_add_goal_id"] = goal_id
        g = get_goal_by_id(goal_id)
        kb = [[InlineKeyboardButton("❌ Отмена", callback_data=f"view_{goal_id}")]]
        await query.message.reply_text(f"💰 Сколько валюты ты отложил в копилку «{g['name']}»?", reply_markup=InlineKeyboardMarkup(kb))
        return WAITING_ADD_SUM
    elif data.startswith("del_"):
        goal_id = int(data.split("_")[1])
        delete_goal(goal_id)
        await query.edit_message_text("🗑 Цель успешно удалена!")

async def process_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", "."))
        if amount <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введи корректное число:")
        return WAITING_ADD_SUM
        
    goal_id = context.user_data.get("current_add_goal_id")
    if goal_id:
        add_money_to_goal(goal_id, amount)
        g = get_goal_by_id(goal_id)
        await update.message.reply_text(f"✅ Касса пополнена на +{format_money(amount, g['currency'])}!")
        
        info, reply_markup = generate_goal_card(goal_id)
        await update.message.reply_text(info, parse_mode="Markdown", reply_markup=reply_markup)
        
    return ConversationHandler.END

# --- КОНСТРУКТОР ЦЕЛИ С КНОПКАМИ «НАЗАД» И ЛИМИТОМ ---
async def new_goal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    status = get_user_status(user_id)
    existing_goals = get_user_goals(user_id)
    
    if status['is_premium'] == 0 and len(existing_goals) >= 1:
        kb = [[InlineKeyboardButton("👑 Купить Premium", callback_data="buy_premium")]]
        await update.message.reply_text(
            "⚠️ **Лимит бесплатной версии!**\n\nВ бесплатном тарифе можно создавать только **1 цель**.\n"
            "Купите Premium, чтобы добавлять неограниченное количество целей, валют и планов!",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )
        return ConversationHandler.END

    context.user_data["new_goal"] = {}
    
    cats = get_db_categories()
    keyboard = [[InlineKeyboardButton(display, callback_data=f"cat_{name}")] for name, display in cats]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_creation")])
    
    if update.callback_query:
        await update.callback_query.edit_message_text("📁 Выбери категорию для новой цели:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text("📁 Выбери категорию для новой цели:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_CATEGORY

async def cat_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_creation":
        await query.edit_message_text("❌ Создание цели отменено.")
        return ConversationHandler.END
    context.user_data["new_goal"]["category"] = query.data.split("_")[1]
    
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cat")]]
    await query.edit_message_text(f"Категория выбрана.\n\n✍️ Введи короткое название цели (например: *iPhone 17*):", reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return WAITING_NAME

async def name_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_goal"]["name"] = update.message.text.strip()
    
    currs = get_db_currencies()
    keyboard = [[InlineKeyboardButton(f"{name} ({sym})", callback_data=f"curr_{code}")] for code, sym, name in currs]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_name")])
    
    await update.message.reply_text("💱 Выбери валюту для накопления:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_CURRENCY

async def currency_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["new_goal"]["currency"] = query.data.split("_")[1]
    
    keyboard = [
        [InlineKeyboardButton("🗓 Рассчитать срок (в днях)", callback_data="gmode_days")],
        [InlineKeyboardButton("🎯 Рассчитать взнос (к дате)", callback_data="gmode_date")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_curr")]
    ]
    await query.edit_message_text("⚙️ Выбери способ планирования:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MODE

async def goal_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["new_goal"]["mode"] = "days" if query.data == "gmode_days" else "date"
    
    sym = get_currency_symbol(context.user_data["new_goal"]["currency"])
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_mode")]]
    await query.edit_message_text(f"💰 Сколько стоит цель (в {sym})?:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_PRICE

async def goal_price_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = float(update.message.text.strip().replace(",", "."))
        if price <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введи число больше нуля:")
        return WAITING_PRICE
    context.user_data["new_goal"]["price"] = price
    
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_price")]]
    await update.message.reply_text("📉 Сколько вы уже отложили на неё прямо сейчас (если 0, напишите 0)?", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_SAVED

async def goal_saved_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        saved = float(update.message.text.strip().replace(",", "."))
        if saved < 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Число не должно быть отрицательным:")
        return WAITING_SAVED
    context.user_data["new_goal"]["saved"] = saved
    
    sym = get_currency_symbol(context.user_data["new_goal"]["currency"])
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_saved")]]
    if context.user_data["new_goal"]["mode"] == "days":
        await update.message.reply_text(f"💵 Какую сумму ({sym}) вы сможете откладывать ЕЖЕДНЕВНО?", reply_markup=InlineKeyboardMarkup(kb))
        return WAITING_DAILY
    else:
        await update.message.reply_text("📅 Введи целевую дату дедлайна в формате **ДД.ММ.ГГГГ** (например `31.12.2026`):", reply_markup=InlineKeyboardMarkup(kb))
        return WAITING_DATE

async def goal_daily_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        daily = float(update.message.text.strip().replace(",", "."))
        if daily <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Число должно быть больше 0:")
        return WAITING_DAILY
    context.user_data["new_goal"]["daily"] = daily
    create_goal(update.effective_user.id, context.user_data["new_goal"])
    await update.message.reply_text("🎉 Копилка успешно создана! Посмотреть статус целей можно в команде /goals")
    return ConversationHandler.END

async def goal_date_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        target_date = datetime.strptime(text, "%d.%m.%Y")
        if target_date.date() <= datetime.now().date(): raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Ошибка. Введи дату из будущего в формате ДД.ММ.ГГГГ:")
        return WAITING_DATE
    context.user_data["new_goal"]["target_date"] = text
    create_goal(update.effective_user.id, context.user_data["new_goal"])
    await update.message.reply_text("🎉 Копилка успешно создана! Посмотреть статус целей можно в команде /goals")
    return ConversationHandler.END

# ЛОГИКА КНОПОК «НАЗАД»
async def back_to_cat_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await new_goal_start(update, context)

async def back_to_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cat")]]
    await query.edit_message_text("✍️ Введи короткое название цели:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_NAME

async def back_to_curr_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    currs = get_db_currencies()
    keyboard = [[InlineKeyboardButton(f"{name} ({sym})", callback_data=f"curr_{code}")] for code, sym, name in currs]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_name")])
    await query.edit_message_text("💱 Выбери валюту для накопления:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_CURRENCY

async def back_to_mode_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🗓 Рассчитать срок (в днях)", callback_data="gmode_days")],
        [InlineKeyboardButton("🎯 Рассчитать взнос (к дате)", callback_data="gmode_date")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_curr")]
    ]
    await query.edit_message_text("⚙️ Выбери способ планирования:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MODE

async def back_to_price_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sym = get_currency_symbol(context.user_data["new_goal"]["currency"])
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_mode")]]
    await query.edit_message_text(f"💰 Сколько стоит цель (в {sym})?:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_PRICE

async def back_to_saved_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_price")]]
    await query.edit_message_text("📉 Сколько вы уже отложили на неё прямо сейчас?", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_SAVED

# --- ОПЛАТА ПОДПИСКИ ---
async def buy_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    order_id = f"prem_{user_id}_{int(datetime.now().timestamp())}"
    pay_url = generate_aaio_url(order_id, PREMIUM_PRICE_RUB)
    
    keyboard = [
        [InlineKeyboardButton("💳 Оплатить Карта / Крипта", url=pay_url)],
        [InlineKeyboardButton("🔄 [Тест] Выдать себе Premium бесплатно", callback_data="give_test_premium")]
    ]
    await query.message.reply_text(
        f"👑 **Покупка тарифа Premium**\n\n"
        f"Стоимость: **{PREMIUM_PRICE_RUB} рублей** (~850 ₸).\n"
        f"Вы получаете: полный безлимит на цели, доступ ко всем валютам СНГ и отключение лимитов.\n\n"
        f"Выберите способ оплаты:",
        reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )

async def give_test_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE active_users SET is_premium = 1 WHERE user_id = ?", (user_id,))
    cursor.execute("INSERT INTO payments (user_id, amount, currency, pay_date) VALUES (?, ?, ?, ?)",
                   (user_id, PREMIUM_PRICE_RUB, "RUB", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    
    await query.edit_message_text("🎉 **Тестовый статус Premium успешно активирован!** Теперь лимиты сняты, попробуйте команду /newgoal снова.")

# --- АДМИН-ПАНЕЛЬ ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ Доступ закрыт.")
        return
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM payments")
    buyers = cursor.fetchone()[0]
    cursor.execute("SELECT currency, SUM(amount) FROM payments GROUP BY currency")
    revenue = cursor.fetchall()
    conn.close()
    
    text = "📊 **АДМИНКА БОТА**\n——————————————————\n"
    text += f"👥 Купивших пользователей: {buyers} чел.\n\n"
    text += "💰 Касса:\n"
    for curr, total in revenue:
        text += f"• {total} {curr}\n"
    text += "\n🔧 **Управление:**\n"
    text += "• `/add_cat [Код] [Имя кнопки]` — добавить категорию\n"
    text += "• `/add_curr [Код] [Символ] [Имя]` — добавить валюту"
    await update.message.reply_text(text, parse_mode="Markdown")

async def admin_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        args = context.args
        name = args[0]
        display_name = " ".join(args[1:])
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO categories (name, display_name) VALUES (?, ?)", (name, display_name))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Категория `{display_name}` добавлена!")
    except Exception:
        await update.message.reply_text("Ошибка. Пример: `/add_cat cat_bike 🏍 Мотоциклы`")

async def admin_add_currency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        args = context.args
        code = args[0]
        symbol = args[1]
        name = " ".join(args[2:])
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO currencies (code, symbol, name) VALUES (?, ?, ?)", (code, symbol, name))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Валюта `{name} ({symbol})` добавлена!")
    except Exception:
        await update.message.reply_text("Ошибка. Пример: `/add_curr AED 🇦🇪 Дирхам`")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return ConversationHandler.END

async def post_init(application: Application) -> None:
    asyncio.create_task(custom_reminder_loop(application.bot))

async def back_to_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    goals = get_user_goals(query.from_user.id)
    keyboard = [[InlineKeyboardButton(f"{g['category']} {g['name']}", callback_data=f"view_{g['id']}")] for g in goals]
    await query.edit_message_text("💼 **Твои активные копилки:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

# --- ТОЧКА ВХОДА ---
def main():
    init_db()
    application = Application.builder().token(TOKEN).post_init(post_init).build()

    time_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={WAITING_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, start_time_entered)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    application.add_handler(time_handler)

    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("add_cat", admin_add_category))
    application.add_handler(CommandHandler("add_curr", admin_add_currency))

    application.add_handler(CommandHandler("goals", list_goals))
    application.add_handler(CallbackQueryHandler(back_to_list_callback, pattern="^back_to_list$"))
    application.add_handler(CallbackQueryHandler(buy_premium_callback, pattern="^buy_premium$"))
    application.add_handler(CallbackQueryHandler(give_test_premium_callback, pattern="^give_test_premium$"))

    new_goal_handler = ConversationHandler(
        entry_points=[CommandHandler("newgoal", new_goal_start)],
        states={
            CHOOSE_CATEGORY: [
                CallbackQueryHandler(cat_selected, pattern="^cat_"),
                CallbackQueryHandler(cat_selected, pattern="^cancel_creation$")
            ],
            WAITING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, name_entered),
                CallbackQueryHandler(back_to_cat_step, pattern="^back_to_cat$")
            ],
            CHOOSE_CURRENCY: [
                CallbackQueryHandler(currency_selected, pattern="^curr_"),
                CallbackQueryHandler(back_to_name_step, pattern="^back_to_name$")
            ],
            SELECT_MODE: [
                CallbackQueryHandler(goal_mode_selected, pattern="^gmode_"),
                CallbackQueryHandler(back_to_curr_step, pattern="^back_to_curr$")
            ],
            WAITING_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_price_entered),
                CallbackQueryHandler(back_to_mode_step, pattern="^back_to_mode$")
            ],
            WAITING_SAVED: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_saved_entered),
                CallbackQueryHandler(back_to_price_step, pattern="^back_to_price$")
            ],
            WAITING_DAILY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_daily_entered),
                CallbackQueryHandler(back_to_saved_step, pattern="^back_to_saved$")
            ],
            WAITING_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_date_entered),
                CallbackQueryHandler(back_to_saved_step, pattern="^back_to_saved$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    deposit_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(goal_callback_handler, pattern="^(view_|addmoney_|del_)")],
        states={WAITING_ADD_SUM: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, process_deposit),
            CallbackQueryHandler(goal_callback_handler, pattern="^view_")
        ]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )

    application.add_handler(new_goal_handler)
    application.add_handler(deposit_handler)

    print("Тестовый коммерческий бот запущен!")
    application.run_polling()

if __name__ == "__main__":
    main()
