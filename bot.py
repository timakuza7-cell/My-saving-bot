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
ADMIN_ID = 123456789  # Поставьте сюда ваш настоящий Telegram ID

# Настройки Aaio
AAIO_MERCHANT_ID = "YOUR_MERCHANT_ID"
AAIO_SECRET_1 = "YOUR_SECRET_1"
PREMIUM_PRICE_RUB = 150.0

# Состояния диалогов
WAITING_START_TIME = 0
CHOOSE_CATEGORY, WAITING_NAME, CHOOSE_CURRENCY, SELECT_MODE, WAITING_PRICE, WAITING_SAVED, WAITING_DAILY, WAITING_DATE = range(1, 9)
WAITING_ADD_SUM = 0
WAITING_NEW_CURR_CODE, WAITING_NEW_CURR_SYM, WAITING_NEW_CURR_NAME = range(10, 13)

DB_FILE = "savings_bot.db"

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_users (
            user_id INTEGER PRIMARY KEY,
            reminder_time TEXT DEFAULT '21:00',
            is_premium INTEGER DEFAULT 0
        )
    """)
    
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
            currency TEXT DEFAULT 'RUB'
        )
    """)
    
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
            name TEXT,
            sort_order INTEGER DEFAULT 100
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            currency TEXT,
            pay_date TEXT
        )
    """)
    
    cursor.execute("SELECT COUNT(*) FROM categories")
    if cursor.fetchone()[0] == 0:
        default_cats = [
            ('Property', '🏠 Недвижимость'),
            ('Automotive', '🚗 Автомобиль'),
            ('Technology', '💻 Техника и гаджеты'),
            ('Travel', '✈️ Путешествия'),
            ('Investment', '📈 Инвестиции и капитал'),
            ('Other', '🎯 Персональные цели')
        ]
        cursor.executemany("INSERT INTO categories (name, display_name) VALUES (?, ?)", default_cats)
        
    cursor.execute("SELECT COUNT(*) FROM currencies")
    if cursor.fetchone()[0] == 0:
        default_currs = [
            ('RUB', '₽', 'Российский рубль', 1),
            ('USD', '$', 'Доллар США', 2),
            ('EUR', '€', 'Евро', 3),
            ('KZT', '₸', 'Казахстанский тенге', 4)
        ]
        cursor.executemany("INSERT INTO currencies (code, symbol, name, sort_order) VALUES (?, ?, ?, ?)", default_currs)

    conn.commit()
    conn.close()

# --- ФУНКЦИИ БД ---
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
    cursor.execute("SELECT code, symbol, name FROM currencies ORDER BY sort_order ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_currency_symbol(code: str) -> str:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM currencies WHERE code = ?", (code,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else code

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

def create_goal(user_id: int, data: dict) -> int:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO goals (user_id, category, name, price, saved, daily, target_date, mode, currency)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, data['category'], data['name'], data['price'], data['saved'], data.get('daily'), data.get('target_date'), data['mode'], data.get('currency', 'RUB')))
    goal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return goal_id

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
async def custom_reminder_loop(application):
    while True:
        try:
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
                text = "🔔 <b>Напоминание о формировании капитала</b>\n\nТекущие активные позиции:\n"
                for g in goals:
                    left = max(0.0, g['price'] - g['saved'])
                    if left > 0:
                        text += f"• <b>{g['name']}</b> — остаток: {format_money(left, g['currency'])}\n"
                
                keyboard = [[InlineKeyboardButton("📂 Открыть портфель активов", callback_data="back_to_list")]]
                try:
                    await application.bot.send_message(chat_id=u_id, text=text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
                except Exception as e:
                    logger.error(f"Ошибка отправки напоминания: {e}")
        except Exception as e:
            logger.error(f"Ошибка в цикле напоминаний: {e}")
        await asyncio.sleep(60)

# --- ДИАЛОГ СТАРТА ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    add_active_user(user_id)
    
    keyboard = [[InlineKeyboardButton("🚀 Создать новую цель", callback_data="start_new_goal")]]
    await update.message.reply_text(
        "👋 Добро пожаловать в профессиональную систему управления капиталом.\n\n"
        "Укажите время для ежедневных персональных уведомлений в формате ЧЧ:ММ (например, 21:00):",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_START_TIME

async def start_time_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    try:
        valid_time = datetime.strptime(update.message.text.strip(), "%H:%M")
        time_str = valid_time.strftime("%H:%M")
        update_user_reminder(user_id, time_str)
        
        kb = [[InlineKeyboardButton("🚀 Создать финансовую цель", callback_data="start_new_goal")]]
        await update.message.reply_text(
            f"✅ Параметры уведомлений обновлены. Время отправки: <b>{time_str}</b>.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Неверный формат времени. Введите значение в формате ЧЧ:ММ (например, 20:00):")
        return WAITING_START_TIME

# --- КАРТОЧКА ЦЕЛИ ---
def generate_goal_card(goal_id: int):
    g = get_goal_by_id(goal_id)
    if not g: return "⚠️ Цель не найдена.", None
    
    curr = g["currency"]
    percent = min(100, int((g['saved'] / g['price']) * 100)) if g['price'] > 0 else 0
    progress_bar = "🟩" * (percent // 10) + "⬜" * (10 - (percent // 10))
    left = max(0.0, g['price'] - g['saved'])

    info = f"🎯 <b>Цель: {g['name']}</b>\n"
    info += f"📂 Категория: {g['category']}\n"
    info += f"————————————————————\n"
    info += f"💰 Целевая стоимость: {format_money(g['price'], curr)}\n"
    info += f"💎 Накоплено: {format_money(g['saved'], curr)} ({percent}%)\n"
    info += f"📊 Прогресс: [{progress_bar}]\n"
    info += f"⏳ Остаток: {format_money(left, curr)}\n\n"

    if left > 0:
        if g["mode"] == "days":
            days = int(-(-left // g["daily"]))
            info += f"📈 Расчет: по {format_money(g['daily'], curr)} ежедневно. Срок: ~{days} дн.\n"
            info += f"📅 Дата достижения: <b>{(datetime.now() + timedelta(days=days)).strftime('%d.%m.%Y')}</b>"
        else:
            t_date = datetime.strptime(g["target_date"], "%d.%m.%Y")
            days = max(1, (t_date.date() - datetime.now().date()).days)
            info += f"📌 Дедлайн: {g['target_date']} ({days} дн.).\n"
            info += f"⚡️ Необходимый взнос: <b>{format_money(left / days, curr)}</b> в сутки"
    else:
        info += "🏆 <b>Статус: Финансовая цель успешно достигнута!</b>"

    keyboard = [
        [InlineKeyboardButton("➕ Внести средства", callback_data=f"addmoney_{g['id']}")],
        [InlineKeyboardButton("🗑 Удалить цель", callback_data=f"del_{g['id']}")],
        [InlineKeyboardButton("📂 К списку активов", callback_data="back_to_list")]
    ]
    return info, InlineKeyboardMarkup(keyboard)

async def list_goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    goals = get_user_goals(user_id)
    if not goals:
        kb = [[InlineKeyboardButton("🚀 Создать новую цель", callback_data="start_new_goal")]]
        msg = "📂 Активных финансовых целей не обнаружено."
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(kb))
        return
        
    keyboard = [[InlineKeyboardButton(f"🎯 {g['name']} — {format_money(g['price'], g['currency'])}", callback_data=f"view_{g['id']}")] for g in goals]
    keyboard.append([InlineKeyboardButton("➕ Создать новую цель", callback_data="start_new_goal")])
    
    msg = "💼 <b>Портфель финансовых целей:</b>"
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

async def goal_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("view_"):
        goal_id = int(data.split("_")[1])
        info, reply_markup = generate_goal_card(goal_id)
        await query.edit_message_text(info, parse_mode="HTML", reply_markup=reply_markup)
    elif data.startswith("addmoney_"):
        goal_id = int(data.split("_")[1])
        context.user_data["current_add_goal_id"] = goal_id
        g = get_goal_by_id(goal_id)
        kb = [[InlineKeyboardButton("❌ Отмена", callback_data=f"view_{goal_id}")]]
        await query.message.reply_text(f"💵 Введите сумму пополнения для позиции «{g['name']}»:", reply_markup=InlineKeyboardMarkup(kb))
        return WAITING_ADD_SUM
    elif data.startswith("del_"):
        goal_id = int(data.split("_")[1])
        delete_goal(goal_id)
        kb = [[InlineKeyboardButton("📂 Открыть портфель активов", callback_data="back_to_list")]]
        await query.edit_message_text("🗑 Позиция успешно удалена из портфеля.", reply_markup=InlineKeyboardMarkup(kb))

async def process_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip().replace(",", "."))
        if amount <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Некорректный формат ввода. Укажите число:")
        return WAITING_ADD_SUM
        
    goal_id = context.user_data.get("current_add_goal_id")
    if goal_id:
        add_money_to_goal(goal_id, amount)
        g = get_goal_by_id(goal_id)
        await update.message.reply_text(f"✅ Баланс пополнен на +{format_money(amount, g['currency'])}.")
        
        info, reply_markup = generate_goal_card(goal_id)
        await update.message.reply_text(info, parse_mode="HTML", reply_markup=reply_markup)
        
    return ConversationHandler.END

# --- СОЗДАНИЕ ЦЕЛИ ---
async def new_goal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id

    status = get_user_status(user_id)
    existing_goals = get_user_goals(user_id)
    
    if status['is_premium'] == 0 and len(existing_goals) >= 1:
        kb = [[InlineKeyboardButton("⭐ Оформить Premium", callback_data="buy_premium")]]
        msg = (
            "🔒 <b>Ограничение базовой версии.</b>\n\n"
            "На бесплатном тарифе доступна только 1 активная цель. "
            "Приобретите статус Premium для снятия всех ограничений системы."
        )
        if query:
            await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        else:
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(kb))
        return ConversationHandler.END

    context.user_data["new_goal"] = {}
    
    cats = get_db_categories()
    keyboard = [[InlineKeyboardButton(display, callback_data=f"cat_{name}")] for name, display in cats]
    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_creation")])
    
    text = "📂 <b>Выберите категорию инвестирования:</b>"
    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_CATEGORY

async def cat_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "cancel_creation":
        await query.edit_message_text("❌ Операция отменена.")
        return ConversationHandler.END
    context.user_data["new_goal"]["category"] = query.data.split("_")[1]
    
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cat")]]
    await query.edit_message_text("✏️ Введите наименование цели (например, «Tesla Model 3» или «Квартира в центре»):", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_NAME

async def name_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_goal"]["name"] = update.message.text.strip()
    
    currs = get_db_currencies()
    keyboard = [[InlineKeyboardButton(f"{name} ({sym})", callback_data=f"curr_{code}")] for code, sym, name in currs]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_name")])
    
    await update.message.reply_text("💱 <b>Выберите валюту расчетов (фиат или криптовалюта):</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_CURRENCY

async def currency_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["new_goal"]["currency"] = query.data.split("_")[1]
    
    keyboard = [
        [InlineKeyboardButton("📊 Расчет по сумме ежедневных взносов", callback_data="gmode_days")],
        [InlineKeyboardButton("📅 Расчет по целевой дате (дедлайну)", callback_data="gmode_date")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_curr")]
    ]
    await query.edit_message_text("⚙️ <b>Выберите режим формирования накоплений:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MODE

async def goal_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["new_goal"]["mode"] = "days" if query.data == "gmode_days" else "date"
    
    sym = get_currency_symbol(context.user_data["new_goal"]["currency"])
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_mode")]]
    await query.edit_message_text(f"💰 Укажите полную стоимость цели (в {sym}):", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_PRICE

async def goal_price_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        price = float(update.message.text.strip().replace(",", "."))
        if price <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введите числовое значение больше нуля:")
        return WAITING_PRICE
    context.user_data["new_goal"]["price"] = price
    
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_price")]]
    await update.message.reply_text("💎 Укажите объем средств, накопленный на текущий момент (при отсутствии — 0):", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_SAVED

async def goal_saved_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        saved = float(update.message.text.strip().replace(",", "."))
        if saved < 0: raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Значение не может быть отрицательным. Повторите ввод:")
        return WAITING_SAVED
    context.user_data["new_goal"]["saved"] = saved
    
    sym = get_currency_symbol(context.user_data["new_goal"]["currency"])
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_saved")]]
    if context.user_data["new_goal"]["mode"] == "days":
        await update.message.reply_text(f"📈 Планируемый объем ежедневных отчислений ({sym}):", reply_markup=InlineKeyboardMarkup(kb))
        return WAITING_DAILY
    else:
        await update.message.reply_text("📅 Укажите целевую дату в формате ДД.ММ.ГГГГ (например, 31.12.2027):", reply_markup=InlineKeyboardMarkup(kb))
        return WAITING_DATE

async def goal_daily_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        daily = float(update.message.text.strip().replace(",", "."))
        if daily <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Значение должно быть больше 0:")
        return WAITING_DAILY
    context.user_data["new_goal"]["daily"] = daily
    goal_id = create_goal(update.effective_user.id, context.user_data["new_goal"])
    
    # Сразу показываем карточку созданной цели
    info, reply_markup = generate_goal_card(goal_id)
    await update.message.reply_text("🎉 <b>Цель успешно создана и добавлена в портфель!</b>", parse_mode="HTML")
    await update.message.reply_text(info, parse_mode="HTML", reply_markup=reply_markup)
    return ConversationHandler.END

async def goal_date_entered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        target_date = datetime.strptime(text, "%d.%m.%Y")
        if target_date.date() < datetime.now().date(): raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Неверная дата. Укажите корректный день (сегодня или в будущем) в формате ДД.ММ.ГГГГ:")
        return WAITING_DATE
    context.user_data["new_goal"]["target_date"] = text
    goal_id = create_goal(update.effective_user.id, context.user_data["new_goal"])
    
    # Сразу показываем карточку созданной цели
    info, reply_markup = generate_goal_card(goal_id)
    await update.message.reply_text("🎉 <b>Цель успешно создана и добавлена в портфель!</b>", parse_mode="HTML")
    await update.message.reply_text(info, parse_mode="HTML", reply_markup=reply_markup)
    return ConversationHandler.END

# НАВИГАЦИЯ «НАЗАД»
async def back_to_cat_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await new_goal_start(update, context)

async def back_to_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cat")]]
    await query.edit_message_text("✏️ Введите наименование цели:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_NAME

async def back_to_curr_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    currs = get_db_currencies()
    keyboard = [[InlineKeyboardButton(f"{name} ({sym})", callback_data=f"curr_{code}")] for code, sym, name in currs]
    keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_name")])
    await query.edit_message_text("💱 Выберите валюту расчетов:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CHOOSE_CURRENCY

async def back_to_mode_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📊 Расчет по сумме ежедневных взносов", callback_data="gmode_days")],
        [InlineKeyboardButton("📅 Расчет по целевой дате (дедлайну)", callback_data="gmode_date")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_curr")]
    ]
    await query.edit_message_text("⚙️ Выберите режим формирования накоплений:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_MODE

async def back_to_price_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sym = get_currency_symbol(context.user_data["new_goal"]["currency"])
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_mode")]]
    await query.edit_message_text(f"💰 Укажите полную стоимость цели (в {sym}):", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_PRICE

async def back_to_saved_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_price")]]
    await query.edit_message_text("💎 Укажите объем средств, накопленный на текущий момент:", reply_markup=InlineKeyboardMarkup(kb))
    return WAITING_SAVED

# --- ДОБАВЛЕНИЕ НОВОЙ ВАЛЮТЫ ИЛИ КРИПТЫ АДМИНОМ ---
async def admin_add_currency_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен.")
        return
    await update.message.reply_text("💱 Введите код новой валюты/крипты (например: <code>USDT</code>, <code>BTC</code>, <code>GBP</code>):", parse_mode="HTML")
    return WAITING_NEW_CURR_CODE

async def admin_curr_code_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_curr"] = {"code": update.message.text.strip().upper()}
    await update.message.reply_text("符号 Введите символ валюты (например: <code>₮</code>, <code>₿</code>, <code>£</code>):", parse_mode="HTML")
    return WAITING_NEW_CURR_SYM

async def admin_curr_sym_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_curr"]["symbol"] = update.message.text.strip()
    await update.message.reply_text("📝 Введите полное название (например: <code>Tether USD</code>, <code>Bitcoin</code>):", parse_mode="HTML")
    return WAITING_NEW_CURR_NAME

async def admin_curr_name_entered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    c_data = context.user_data["new_curr"]
    
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO currencies (code, symbol, name, sort_order) VALUES (?, ?, ?, 50)",
                       (c_data["code"], c_data["symbol"], name))
        conn.commit()
        await update.message.reply_text(f"✅ Валюта/крипта <b>{name} ({c_data['symbol']})</b> успешно добавлена в систему!", parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка добавления: {e}")
    finally:
        conn.close()
    return ConversationHandler.END

# --- ОПЛАТА ---
async def buy_premium_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    order_id = f"prem_{user_id}_{int(datetime.now().timestamp())}"
    pay_url = generate_aaio_url(order_id, PREMIUM_PRICE_RUB)
    
    keyboard = [
        [InlineKeyboardButton("💳 Оплатить подписку", url=pay_url)],
        [InlineKeyboardButton("🧪 [Тест] Активировать Premium бесплатно", callback_data="give_test_premium")]
    ]
    await query.message.reply_text(
        "⭐ <b>Привилегированный статус Premium</b>\n\n"
        f"Стоимость подписки: {PREMIUM_PRICE_RUB} рублей.\n"
        "Предоставляет неограниченные лимиты на цели, расширенную мультивалютность и криптоактивы.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
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
    
    kb = [[InlineKeyboardButton("📂 Открыть портфель активов", callback_data="back_to_list")]]
    await query.edit_message_text("🎉 Статус Premium успешно активирован. Все ограничения сняты!", reply_markup=InlineKeyboardMarkup(kb))

# --- АДМИН-ПАНЕЛЬ ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещен.")
        return
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM payments")
    buyers = cursor.fetchone()[0]
    cursor.execute("SELECT currency, SUM(amount) FROM payments GROUP BY currency")
    revenue = cursor.fetchall()
    conn.close()
    
    text = "🛠 <b>Системная панель администратора</b>\n————————————————————\n"
    text += f"👥 Всего премиум-пользователей: {buyers}\n\n"
    text += "💰 Финансовый оборот:\n"
    for curr, total in revenue:
        text += f"• {total} {curr}\n"
    text += "\n⚙️ <b>Команды управления:</b>\n"
    text += "• /add_currency — добавить новую валюту или крипту\n"
    text += "• /add_cat [Код] [Имя] — добавить категорию"
    await update.message.reply_text(text, parse_mode="HTML")

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
        await update.message.reply_text(f"✅ Категория «{display_name}» добавлена.")
    except Exception:
        await update.message.reply_text("⚠️ Ошибка синтаксиса. Пример: /add_cat RealEstate 🏠 Недвижимость")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return ConversationHandler.END

async def post_init(application: Application) -> None:
    asyncio.create_task(custom_reminder_loop(application))

async def back_to_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    goals = get_user_goals(query.from_user.id)
    if not goals:
        kb = [[InlineKeyboardButton("🚀 Создать новую цель", callback_data="start_new_goal")]]
        await query.edit_message_text("📂 Активных финансовых целей не обнаружено.", reply_markup=InlineKeyboardMarkup(kb))
        return
    keyboard = [[InlineKeyboardButton(f"🎯 {g['name']} — {format_money(g['price'], g['currency'])}", callback_data=f"view_{g['id']}")] for g in goals]
    keyboard.append([InlineKeyboardButton("➕ Создать новую цель", callback_data="start_new_goal")])
    await query.edit_message_text("💼 <b>Портфель финансовых целей:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))

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

    # Админские обработчики
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CommandHandler("add_cat", admin_add_category))
    
    currency_add_handler = ConversationHandler(
        entry_points=[CommandHandler("add_currency", admin_add_currency_start)],
        states={
            WAITING_NEW_CURR_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_curr_code_entered)],
            WAITING_NEW_CURR_SYM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_curr_sym_entered)],
            WAITING_NEW_CURR_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_curr_name_entered)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True
    )
    application.add_handler(currency_add_handler)

    application.add_handler(CommandHandler("goals", list_goals))
    application.add_handler(CallbackQueryHandler(back_to_list_callback, pattern="^back_to_list$"))
    application.add_handler(CallbackQueryHandler(new_goal_start, pattern="^start_new_goal$"))
    application.add_handler(CallbackQueryHandler(buy_premium_callback, pattern="^buy_premium$"))
    application.add_handler(CallbackQueryHandler(give_test_premium_callback, pattern="^give_test_premium$"))

    new_goal_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newgoal", new_goal_start),
            CallbackQueryHandler(new_goal_start, pattern="^start_new_goal$")
        ],
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

    print("Система управления капиталом запущена.")
    application.run_polling()

if __name__ == "__main__":
    main()
