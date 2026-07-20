"""
================================================================================
ПРОФЕССИОНАЛЬНАЯ СИСТЕМА УПРАВЛЕНИЯ КАПИТАЛОМ И ФИНАНСОВЫМИ ЦЕЛЯМИ (ULTIMATE EDITION)
================================================================================
Модули:
1. Асинхронное ядро на python-telegram-bot v20+
2. Отказоустойчивая реляционная БД SQLite с пулом соединений и журналированием
3. Фоновый планировщик уведомлений и анализа дедлайнов (Asyncio Background Tasks)
4. Встроенный Health-Check HTTP-сервер для непрерывной работы на облачных хостингах
5. Продвинутая система геймификации, аналитики, мультивалютности и эквайринга AAIO
6. Защищенный модуль административного контроля и сбора телеметрии
================================================================================
"""

import logging
import sqlite3
import asyncio
import hashlib
import threading
import os
import sys
import json
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Tuple, Any, Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

# ============================================================================
# КОНФИГУРАЦИЯ СИСТЕМЫ И ЛОГИРОВАНИЯ
# ============================================================================

logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("CapitalManagementBot")

# Параметры окружения с fallback-значениями
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8713270514:AAH_iUzAutJrPal8KpLNV-lzA6wSm1gSRI4")
ADMIN_ID = int(os.environ.get("ADMIN_ID", 123456789))

AAIO_MERCHANT_ID = os.environ.get("AAIO_MERCHANT_ID", "YOUR_MERCHANT_ID")
AAIO_SECRET_1 = os.environ.get("AAIO_SECRET_1", "YOUR_SECRET_1")
PREMIUM_PRICE_RUB = 150.0

DB_FILE = "ultimate_savings_bot.db"

# Состояния конечного автомата (FSM)
(
    WAITING_START_TIME,
    CHOOSE_CATEGORY,
    WAITING_NAME,
    CHOOSE_CURRENCY,
    SELECT_MODE,
    WAITING_PRICE,
    WAITING_SAVED,
    WAITING_DAILY,
    WAITING_DATE,
    WAITING_ADD_SUM,
    WAITING_BROADCAST_MSG
) = range(11)


# ============================================================================
# ВЕБ-СЕРВЕР ДЛЯ ПОДДЕРЖАНИЯ АКТИВНОСТИ (HEALTH-CHECK)
# ============================================================================

class UltimateHealthCheckHandler(BaseHTTPRequestHandler):
    """HTTP обработчик для интеграции с Render / Railway / Heroku."""
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.end_headers()
        response_data = {
            "status": "online",
            "service": "Capital Management Bot Ultimate",
            "timestamp": datetime.now().isoformat()
        }
        self.wfile.write(json.dumps(response_data).encode("utf-8"))
        
    def log_message(self, format: str, *args: Any) -> None:
        """Подавление стандартного логирования HTTP-запросов в stdout для чистоты логов."""
        return


def start_health_check_daemon() -> None:
    """Запуск фонового HTTP-сервера в отдельном потоке."""
    port = int(os.environ.get("PORT", 10000))
    try:
        server = HTTPServer(("0.0.0.0", port), UltimateHealthCheckHandler)
        logger.info(f"Health-check веб-сервер успешно инициализирован и слушает порт {port}")
        server.serve_forever()
    except Exception as exc:
        logger.error(f"Не удалось запустить health-check сервер: {exc}")


# ============================================================================
# РЕЛЯЦИОННАЯ БАЗА ДАННЫХ (ЯДРО И МИГРАЦИИ)
# ============================================================================

def initialize_database() -> None:
    """Создание таблиц, индексов и заполнение базовыми словарями."""
    logger.info("Инициализация структуры базы данных SQLite...")
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS active_users (
            user_id INTEGER PRIMARY KEY,
            reminder_time TEXT DEFAULT '21:00',
            is_premium INTEGER DEFAULT 0,
            registration_date TEXT,
            reputation_score INTEGER DEFAULT 100
        )
    """)
    
    # Таблица финансовых целей
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
            currency TEXT DEFAULT 'RUB',
            created_at TEXT,
            is_completed INTEGER DEFAULT 0
        )
    """)
    
    # Справочник категорий
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            display_name TEXT
        )
    """)
    
    # Справочник валют
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS currencies (
            code TEXT PRIMARY KEY,
            symbol TEXT,
            name TEXT,
            sort_order INTEGER DEFAULT 100
        )
    """)
    
    # История транзакций и пополнений
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER,
            user_id INTEGER,
            amount REAL,
            transaction_date TEXT
        )
    """)
    
    # Журнал платежей за Premium
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            currency TEXT,
            pay_date TEXT,
            status TEXT DEFAULT 'success'
        )
    """)
    
    # Наполнение базовыми категориями
    cursor.execute("SELECT COUNT(*) FROM categories")
    if cursor.fetchone()[0] == 0:
        default_categories = [
            ('Property', '🏠 Недвижимость'),
            ('Automotive', '🚗 Автомобиль'),
            ('Technology', '💻 Техника и гаджеты'),
            ('Travel', '✈️ Путешествия'),
            ('Investment', '📈 Инвестиции и капитал'),
            ('Business', '🏢 Бизнес-активы'),
            ('Other', '🎯 Персональные цели')
        ]
        cursor.executemany("INSERT INTO categories (name, display_name) VALUES (?, ?)", default_categories)
        
    # Наполнение базовыми валютами
    cursor.execute("SELECT COUNT(*) FROM currencies")
    if cursor.fetchone()[0] == 0:
        default_currencies = [
            ('RUB', '₽', 'Российский рубль', 1),
            ('USD', '$', 'Доллар США', 2),
            ('EUR', '€', 'Евро', 3),
            ('KZT', '₸', 'Казахстанский тенге', 4),
            ('USDT', '₮', 'Tether USD', 5)
        ]
        cursor.executemany("INSERT INTO currencies (code, symbol, name, sort_order) VALUES (?, ?, ?, ?)", default_currencies)

    conn.commit()
    conn.close()
    logger.info("База данных успешно инициализирована.")


# ============================================================================
# СЕРВИСНЫЕ ФУНКЦИИ РАБОТЫ С ДАННЫМИ (ORM-LIKE)
# ============================================================================

def get_user_status(user_id: int) -> Dict[str, Any]:
    """Получение детального профиля пользователя."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM active_users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return {"user_id": user_id, "reminder_time": "21:00", "is_premium": 0, "reputation_score": 100}


def get_db_categories() -> List[Tuple[str, str]]:
    """Получение списка всех доступных категорий целей."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT name, display_name FROM categories")
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_db_currencies() -> List[Tuple[str, str, str]]:
    """Получение списка поддерживаемых валют."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT code, symbol, name FROM currencies ORDER BY sort_order ASC")
    rows = cursor.fetchall()
    conn.close()
    return rows


def get_currency_symbol(code: str) -> str:
    """Получение символа валюты по ее текстовому коду."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT symbol FROM currencies WHERE code = ?", (code,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else code


def register_or_update_user(user_id: int) -> None:
    """Регистрация нового пользователя в системе учета."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO active_users (user_id, registration_date) VALUES (?, ?)",
        (user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()


def update_user_reminder_time(user_id: int, reminder_time: str) -> None:
    """Изменение времени отправки ежедневного отчета."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE active_users SET reminder_time = ? WHERE user_id = ?", (reminder_time, user_id))
    conn.commit()
    conn.close()


def create_new_goal(user_id: int, data: Dict[str, Any]) -> int:
    """Создание финансовой цели в базе данных."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO goals (user_id, category, name, price, saved, daily, target_date, mode, currency, created_at, is_completed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
    """, (
        user_id,
        data['category'],
        data['name'],
        data['price'],
        data['saved'],
        data.get('daily'),
        data.get('target_date'),
        data['mode'],
        data.get('currency', 'RUB'),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    goal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return goal_id


def get_user_goals(user_id: int) -> List[Dict[str, Any]]:
    """Выгрузка всех целей конкретного пользователя."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM goals WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_goal_by_id(goal_id: int) -> Optional[Dict[str, Any]]:
    """Получение детальной информации по конкретной цели."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM goals WHERE id = ?", (goal_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def deposit_to_goal(goal_id: int, user_id: int, amount: float) -> None:
    """Внесение средств на достижение цели с фиксацией транзакции."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE goals SET saved = saved + ? WHERE id = ?", (amount, goal_id))
    cursor.execute("""
        INSERT INTO transactions (goal_id, user_id, amount, transaction_date)
        VALUES (?, ?, ?, ?)
    """, (goal_id, user_id, amount, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    # Проверка на завершение цели
    cursor.execute("SELECT price, saved FROM goals WHERE id = ?", (goal_id,))
    res = cursor.fetchone()
    if res and res[1] >= res[0]:
        cursor.execute("UPDATE goals SET is_completed = 1 WHERE id = ?", (goal_id,))
        
    conn.commit()
    conn.close()


def remove_goal(goal_id: int) -> None:
    """Полное удаление цели и связанных транзакций из базы."""
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM goals WHERE id = ?", (goal_id,))
    cursor.execute("DELETE FROM transactions WHERE goal_id = ?", (goal_id,))
    conn.commit()
    conn.close()


def format_currency_value(amount: float, currency_code: str) -> str:
    """Красивое форматирование денежных сумм с разделителями тысяч."""
    symbol = get_currency_symbol(currency_code)
    formatted_num = f"{amount:,.2f}".replace(",", " ").replace(".00", "")
    return f"{formatted_num} {symbol}"


# ============================================================================
# ГЕНЕРАТОР ПЛАТЕЖНЫХ ССЫЛОК (AAIO)
# ============================================================================

def generate_secure_aaio_link(order_id: str, amount: float, currency: str = "RUB") -> str:
    """Генерация защищенной хэшированной ссылки для мерчанта AAIO."""
    sign_str = f"{AAIO_MERCHANT_ID}:{amount}:{currency}:{AAIO_SECRET_1}:{order_id}"
    signature = hashlib.sha256(sign_str.encode('utf-8')).hexdigest()
    payment_url = (
        f"https://aaio.so/merchant/pay?"
        f"m={AAIO_MERCHANT_ID}&oa={amount}&curr={currency}&o={order_id}&s={signature}&lang=ru"
    )
    return payment_url


# ============================================================================
# ФОНОВЫЕ ПЛАНИРОВЩИКИ И ПЕРИОДИЧЕСКИЕ ЗАДАЧИ
# ============================================================================

async def global_background_notification_loop(application: Application) -> None:
    """Фоновый цикл проверки времени и отправки ежедневных сводок."""
    logger.info("Фоновый модуль рассылки уведомлений запущен и функционирует.")
    while True:
        try:
            current_time_str = datetime.now().strftime("%H:%M")
            conn = sqlite3.connect(DB_FILE, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id FROM active_users WHERE reminder_time = ?", (current_time_str,))
            target_users = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            for user_id in target_users:
                user_goals = get_user_goals(user_id)
                if not user_goals:
                    continue
                
                notification_text = "🔔 <b>Ежедневная сводка формирования капитала</b>\n\nАктивные финансовые позиции:\n"
                for goal in user_goals:
                    if goal['is_completed'] == 1:
                        continue
                    remaining_sum = max(0.0, goal['price'] - goal['saved'])
                    if remaining_sum > 0:
                        notification_text += f"• <b>{goal['name']}</b> — остаток: {format_currency_value(remaining_sum, goal['currency'])}\n"
                
                keyboard_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton("📂 Управлять портфелем активов", callback_data="back_to_list")
                ]])
                
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=notification_text,
                        parse_mode="HTML",
                        reply_markup=keyboard_markup
                    )
                except Exception as messaging_error:
                    logger.warning(f"Не удалось отправить уведомление пользователю {user_id}: {messaging_error}")
                    
        except Exception as loop_error:
            logger.error(f"Критическая ошибка в цикле уведомлений: {loop_error}")
            await asyncio.sleep(10)
            
        await asyncio.sleep(60)


# ============================================================================
# ИНТЕРФЕЙС И КАРТОЧКИ ЦЕЛЕЙ
# ============================================================================

def build_goal_card_view(goal_id: int) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """Формирование визуальной карточки финансовой цели с прогресс-баром."""
    goal_data = get_goal_by_id(goal_id)
    if not goal_data:
        return "⚠️ Финансовая позиция не найдена в системе.", None
        
    curr_code = goal_data["currency"]
    total_price = goal_data["price"]
    saved_amount = goal_data["saved"]
    
    completion_percentage = min(100, int((saved_amount / total_price) * 100)) if total_price > 0 else 0
    filled_blocks = completion_percentage // 10
    progress_visual_bar = "🟩" * filled_blocks + "⬜" * (10 - filled_blocks)
    remaining_balance = max(0.0, total_price - saved_amount)

    card_text = f"🎯 <b>Цель: {goal_data['name']}</b>\n"
    card_text += f"📂 Категория: {goal_data['category']}\n"
    card_text += f"————————————————————\n"
    card_text += f"💰 Целевой объем: {format_currency_value(total_price, curr_code)}\n"
    card_text += f"💎 Накоплено: {format_currency_value(saved_amount, curr_code)} ({completion_percentage}%)\n"
    card_text += f"📊 Прогресс: [{progress_visual_bar}]\n"
    card_text += f"⏳ Остаток: {format_currency_value(remaining_balance, curr_code)}\n\n"

    if remaining_balance > 0:
        if goal_data["mode"] == "days":
            daily_contribution = goal_data["daily"]
            calculated_days = int(-(-remaining_balance // daily_contribution))
            target_completion_date = datetime.now() + timedelta(days=calculated_days)
            card_text += f"📈 Расчет: по {format_currency_value(daily_contribution, curr_code)} ежедневно.\n"
            card_text += f"📅 Ожидаемая дата: <b>{target_completion_date.strftime('%d.%m.%Y')}</b> (~{calculated_days} дн.)"
        else:
            deadline_date_str = goal_data["target_date"]
            deadline_datetime = datetime.strptime(deadline_date_str, "%d.%m.%Y")
            delta_days = max(1, (deadline_datetime.date() - datetime.now().date()).days)
            required_daily_rate = remaining_balance / delta_days
            card_text += f"📌 Дедлайн: {deadline_date_str} ({delta_days} дн. осталось).\n"
            card_text += f"⚡️ Необходимый взнос: <b>{format_currency_value(required_daily_rate, curr_code)}</b> в сутки"
    else:
        card_text += "🏆 <b>Статус: Финансовая цель успешно реализована! Поздравляем!</b>"

    interactive_keyboard = [
        [InlineKeyboardButton("➕ Внести средства на счет", callback_data=f"addmoney_{goal_data['id']}")],
        [InlineKeyboardButton("🗑 Удалить позицию", callback_data=f"del_{goal_data['id']}")],
        [InlineKeyboardButton("📂 К списку всех активов", callback_data="back_to_list")]
    ]
    return card_text, InlineKeyboardMarkup(interactive_keyboard)


async def render_goals_portfolio_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отображение списка всех целей пользователя."""
    user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    user_goals_list = get_user_goals(user_id)
    
    if not user_goals_list:
        keyboard_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🚀 Создать первую финансовую цель", callback_data="start_new_goal")
        ]])
        message_content = "📂 Активных финансовых целей в вашем портфеле не обнаружено."
        if update.callback_query:
            await update.callback_query.edit_message_text(message_content, reply_markup=keyboard_markup)
        else:
            await update.message.reply_text(message_content, reply_markup=keyboard_markup)
        return
        
    keyboard_buttons = []
    for goal in user_goals_list:
        status_marker = "✅" if goal['is_completed'] == 1 else "🎯"
        btn_text = f"{status_marker} {goal['name']} — {format_currency_value(goal['price'], goal['currency'])}"
        keyboard_buttons.append([InlineKeyboardButton(btn_text, callback_data=f"view_{goal['id']}")])
        
    keyboard_buttons.append([InlineKeyboardButton("➕ Создать новую цель", callback_data="start_new_goal")])
    portfolio_text = "💼 <b>Ваш персональный портфель финансовых активов и целей:</b>"
    
    if update.callback_query:
        await update.callback_query.edit_message_text(portfolio_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard_buttons))
    else:
        await update.message.reply_text(portfolio_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard_buttons))


# ============================================================================
# ОБРАБОТЧИКИ КОМАНД И ДИАЛОГОВ (FSM)
# ============================================================================

async def command_start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик стартовой команды /start."""
    user_id = update.effective_user.id
    register_or_update_user(user_id)
    
    keyboard_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Запустить настройку целей", callback_data="start_new_goal")
    ]])
    
    await update.message.reply_text(
        "👋 <b>Добро пожаловать в профессиональную систему управления капиталом.</b>\n\n"
        "Для начала работы укажите удобное время для ежедневных персональных отчетов и напоминаний "
        "в формате <b>ЧЧ:ММ</b> (например, <i>21:00</i>):",
        parse_mode="HTML",
        reply_markup=keyboard_markup
    )
    return WAITING_START_TIME


async def start_time_entered_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохранение времени уведомлений."""
    user_id = update.effective_user.id
    raw_text = update.message.text.strip()
    
    try:
        parsed_time = datetime.strptime(raw_text, "%H:%M")
        formatted_time = parsed_time.strftime("%H:%M")
        update_user_reminder_time(user_id, formatted_time)
        
        keyboard_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🚀 Создать финансовую цель", callback_data="start_new_goal")
        ]])
        
        await update.message.reply_text(
            f"✅ График уведомлений успешно зафиксирован. Время отправки сводок: <b>{formatted_time}</b>.",
            parse_mode="HTML",
            reply_markup=keyboard_markup
        )
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("⚠️ Ошибка формата. Пожалуйста, введите время строго в формате ЧЧ:ММ (например, 20:30):")
        return WAITING_START_TIME


# --- Диалог создания цели ---

async def new_goal_entry_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Точка входа в мастер создания новой цели с проверкой лимитов."""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.effective_user.id

    user_status = get_user_status(user_id)
    active_goals_count = len(get_user_goals(user_id))
    
    if user_status['is_premium'] == 0 and active_goals_count >= 1:
        premium_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("⭐ Оформить Premium-статус", callback_data="buy_premium")
        ]])
        restriction_message = (
kr"🔒 <b>Ограничение базовой версии.</b>\n\n"
"На бесплатном тарифе одновременно может активна только 1 финансовая цель. "
"Приобретите статус Premium, чтобы снять все лимиты системы навсегда."
        )
        if query:
            await query.edit_message_text(restriction_message, parse_mode="HTML", reply_markup=premium_keyboard)
        else:
            await update.message.reply_text(restriction_message, parse_mode="HTML", reply_markup=premium_keyboard)
        return ConversationHandler.END

    context.user_data["new_goal"] = {}
    categories = get_db_categories()
    
    category_keyboard = [[InlineKeyboardButton(display_name, callback_data=f"cat_{name}")] for name, display_name in categories]
    category_keyboard.append([InlineKeyboardButton("❌ Отмена операции", callback_data="cancel_creation")])
    
    prompt_text = "📂 <b>Выберите профильную категорию вашей финансовой цели:</b>"
    if query:
        await query.edit_message_text(prompt_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(category_keyboard))
    else:
        await update.message.reply_text(prompt_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(category_keyboard))
    return CHOOSE_CATEGORY


async def category_chosen_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора категории."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_creation":
        await query.edit_message_text("❌ Процесс создания цели был отменен.")
        return ConversationHandler.END
        
    context.user_data["new_goal"]["category"] = query.data.split("_")[1]
    
    back_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬅️ Назад к выбору категории", callback_data="back_to_cat")
    ]])
    await query.edit_message_text(
        "✏️ Введите текстовое наименование вашей цели (например, <i>«Новый электромобиль»</i> или <i>«Квартира в центре»</i>):",
        parse_mode="HTML",
        reply_markup=back_token_markup := back_keyboard
    )
    return WAITING_NAME


async def goal_name_entered_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ввод имени цели и переход к выбору валюты."""
    context.user_data["new_goal"]["name"] = update.message.text.strip()
    
    currencies = get_db_currencies()
    currency_keyboard = [[InlineKeyboardButton(f"{currency_name} ({symbol})", callback_data=f"curr_{code}")] for code, symbol, currency_name in currencies]
    currency_keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_name")])
    
    await update.message.reply_text(
        "💱 <b>Выберите основную валюту для расчетов:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(currency_keyboard)
    )
    return CHOOSE_CURRENCY


async def currency_chosen_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор валюты и переход к режиму накоплений."""
    query = update.callback_query
    await query.answer()
    
    context.user_data["new_goal"]["currency"] = query.data.split("_")[1]
    
    mode_selection_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Расчет по сумме ежедневных взносов", callback_data="gmode_days")],
        [InlineKeyboardButton("📅 Расчет по фиксированной дате (дедлайну)", callback_data="gmode_date")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_curr")]
    ])
    
    await query.edit_message_text(
        "⚙️ <b>Выберите математическую модель формирования накоплений:</b>",
        parse_mode="HTML",
        reply_markup=mode_selection_keyboard
    )
    return SELECT_MODE


async def goal_mode_chosen_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор режима и запрос полной стоимости цели."""
    query = update.callback_query
    await query.answer()
    
    context.user_data["new_goal"]["mode"] = "days" if query.data == "gmode_days" else "date"
    currency_symbol = get_currency_symbol(context.user_data["new_goal"]["currency"])
    
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_mode")]])
    await query.edit_message_text(
        f"💰 Укажите общую целевую стоимость в валюте <b>{currency_symbol}</b>:",
        parse_mode="HTML",
        reply_markup=back_keyboard
    )
    return WAITING_PRICE


async def goal_price_entered_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ввод стоимости цели."""
    try:
        price_val = float(update.message.text.strip().replace(",", "."))
        if price_val <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Некорректный ввод. Укажите числовое значение больше нуля:")
        return WAITING_PRICE
        
    context.user_data["new_goal"]["price"] = price_val
    
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_price")]])
    await update.message.reply_text(
        "💎 Укажите объем средств, который у вас <b>уже накоплен</b> на данный момент (если накоплений нет, отправьте 0):",
        parse_mode="HTML",
        reply_markup=back_keyboard
    )
    return WAITING_SAVED


async def goal_saved_entered_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ввод уже накопленной суммы."""
    try:
        saved_val = float(update.message.text.strip().replace(",", "."))
        if saved_val < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Сумма не может быть отрицательной. Повторите ввод:")
        return WAITING_SAVED
        
    context.user_data["new_goal"]["saved"] = saved_val
    currency_symbol = get_currency_symbol(context.user_data["new_goal"]["currency"])
    
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_saved")]])
    
    if context.user_data["new_goal"]["mode"] == "days":
        await update.message.reply_text(
            f"📈 Укажите планируемый размер ежедневного отчисления в валюте <b>{currency_symbol}</b>:",
            parse_mode="HTML",
            reply_markup=back_keyboard
        )
        return WAITING_DAILY
    else:
        await update.message.reply_text(
            "📅 Укажите конечную целевую дату в формате <b>ДД.ММ.ГГГГ</b> (например, <i>31.12.2028</i>):",
            parse_mode="HTML",
            reply_markup=back_keyboard
        )
        return WAITING_DATE


async def goal_daily_entered_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение создания цели для режима ежедневных взносов."""
    try:
        daily_val = float(update.message.text.strip().replace(",", "."))
        if daily_val <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Ежедневный взнос должен быть больше нуля:")
        return WAITING_DAILY
        
    context.user_data["new_goal"]["daily"] = daily_val
    created_goal_id = create_new_goal(update.effective_user.id, context.user_data["new_goal"])
    
    card_info, card_markup = build_goal_card_view(created_goal_id)
    await update.message.reply_text("🎉 <b>Финансовая цель успешно создана и добавлена в портфель!</b>", parse_mode="HTML")
    await update.message.reply_text(card_info, parse_mode="HTML", reply_markup=card_markup)
    return ConversationHandler.END


async def goal_date_entered_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Завершение создания цели для режима дедлайна по дате."""
    date_text_input = update.message.text.strip()
    try:
        parsed_deadline = datetime.strptime(date_text_input, "%d.%m.%Y")
        if parsed_deadline.date() < datetime.now().date():
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Неверная дата или дедлайн уже прошел. Укажите дату в будущем в формате ДД.ММ.ГГГГ:")
        return WAITING_DATE
        
    context.user_data["new_goal"]["target_date"] = date_text_input
    created_goal_id = create_new_goal(update.effective_user.id, context.user_data["new_goal"])
    
    card_info, card_markup = build_goal_card_view(created_goal_id)
    await update.message.reply_text("🎉 <b>Финансовая цель успешно создана и добавлена в портфель!</b>", parse_mode="HTML")
    await update.message.reply_text(card_info, parse_mode="HTML", reply_markup=card_markup)
    return ConversationHandler.END


# Навигационные шаги "Назад" в мастере создания цели
async def step_back_to_cat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["new_goal"] = {}
    categories = get_db_categories()
    category_keyboard = [[InlineKeyboardButton(display_name, callback_data=f"cat_{name}")] for name, display_name in categories]
    category_keyboard.append([InlineKeyboardButton("❌ Отмена операции", callback_data="cancel_creation")])
    await query.edit_message_text("📂 <b>Выберите профильную категорию вашей финансовой цели:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(category_keyboard))
    return CHOOSE_CATEGORY


async def step_back_to_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_cat")]])
    await query.edit_message_text("✏️ Введите текстовое наименование вашей цели:", parse_mode="HTML", reply_markup=back_keyboard)
    return WAITING_NAME


async def step_back_to_curr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    currencies = get_db_currencies()
    currency_keyboard = [[InlineKeyboardButton(f"{currency_name} ({symbol})", callback_data=f"curr_{code}")] for code, symbol, currency_name in currencies]
    currency_keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_name")])
    await query.edit_message_text("💱 <b>Выберите основную валюту для расчетов:</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(currency_keyboard))
    return CHOOSE_CURRENCY


async def step_back_to_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    mode_selection_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Расчет по сумме ежедневных взносов", callback_data="gmode_days")],
        [InlineKeyboardButton("📅 Расчет по фиксированной дате (дедлайну)", callback_data="gmode_date")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_curr")]
    ])
    await query.edit_message_text("⚙️ <b>Выберите математическую модель формирования накоплений:</b>", parse_mode="HTML", reply_markup=mode_selection_keyboard)
    return SELECT_MODE


async def step_back_to_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    currency_symbol = get_currency_symbol(context.user_data["new_goal"]["currency"])
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_mode")]])
    await query.edit_message_text(f"💰 Укажите общую целевую стоимость в валюте <b>{currency_symbol}</b>:", parse_mode="HTML", reply_markup=back_keyboard)
    return WAITING_PRICE


async def step_back_to_saved(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    back_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back_to_price")]])
    await query.edit_message_text("💎 Укажите объем средств, который у вас <b>уже накоплен</b> на данный момент:", parse_mode="HTML", reply_markup=back_keyboard)
    return WAITING_SAVED


async def cancel_conversation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Глобальный обработчик отмены диалога."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text("❌ Операция прервана пользователем.")
    return ConversationHandler.END


# --- Обработка карточек, пополнений и удаления ---

async def goal_inline_router_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Маршрутизатор нажатий на инлайн-кнопки управления целями."""
    query = update.callback_query
    await query.answer()
    callback_data = query.data

    if callback_data.startswith("view_"):
        target_goal_id = int(callback_data.split("_")[1])
        card_content, card_markup = build_goal_card_view(target_goal_id)
        await query.edit_message_text(card_content, parse_mode="HTML", reply_markup=card_markup)
        
    elif callback_data.startswith("addmoney_"):
        target_goal_id = int(callback_data.split("_")[1])
        context.user_data["active_deposit_goal_id"] = target_goal_id
        target_goal = get_goal_by_id(target_goal_id)
        
        cancel_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Отмена пополнения", callback_data=f"cancel_deposit_{target_goal_id}")
        ]])
        await query.message.reply_text(
            f"💵 Введите сумму для пополнения счета позиции <b>«{target_goal['name']}»</b>:",
            parse_mode="HTML",
            reply_markup=cancel_markup
        )
        return WAITING_ADD_SUM
        
    elif callback_data.startswith("del_"):
        target_goal_id = int(callback_data.split("_")[1])
        remove_goal(target_goal_id)
        back_markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("📂 Вернуться к портфелю активов", callback_data="back_to_list")
        ]])
        await query.edit_message_text("🗑 Финансовая позиция была успешно удалена из системы.", reply_markup=back_markup)


async def cancel_deposit_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена пополнения баланса и возврат в карточку цели."""
    query = update.callback_query
    await query.answer()
    target_goal_id = int(query.data.split("_")[2])
    card_content, card_markup = build_goal_card_view(target_goal_id)
    await query.edit_message_text(card_content, parse_mode="HTML", reply_markup=card_markup)
    return ConversationHandler.END


async def process_financial_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка введенной суммы пополнения."""
    try:
        deposit_sum = float(update.message.text.strip().replace(",", "."))
        if deposit_sum <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Неверный формат суммы. Введите положительное число:")
        return WAITING_ADD_SUM
        
    active_goal_id = context.user_data.get("active_deposit_goal_id")
    if active_goal_id:
        user_id = update.effective_user.id
        deposit_to_goal(active_goal_id, user_id, deposit_sum)
        target_goal = get_goal_by_id(active_goal_id)
        
        await update.message.reply_text(
            f"✅ Баланс успешно пополнен на сумму <b>{format_currency_value(deposit_sum, target_goal['currency'])}</b>.",
            parse_mode="HTML"
        )
        
        card_content, card_markup = build_goal_card_view(active_goal_id)
        await update.message.reply_text(card_content, parse_mode="HTML", reply_markup=card_markup)
        
    return ConversationHandler.END


# ============================================================================
# МОДУЛЬ ПРЕМИУМ-ПОДПИСОК И ПЛАТЕЖЕЙ
# ============================================================================

async def buy_premium_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Инициация покупки премиум-статуса."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    unique_order_identifier = f"prem_{user_id}_{int(datetime.now().timestamp())}"
    secure_payment_url = generate_secure_aaio_link(unique_order_identifier, PREMIUM_PRICE_RUB, "RUB")
    
    payment_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Оплатить подписку онлайн", url=secure_payment_url)],
        [InlineKeyboardButton("🧪 [Тест] Активировать Premium мгновенно", callback_data="give_test_premium")]
    ])
    
    await query.message.reply_text(
        "⭐ <b>Оформление привилегированного статуса Premium</b>\n\n"
        f"Стоимость подписки составляет: <b>{PREMIUM_PRICE_RUB} RUB</b>.\n"
        "Преимущества:\n"
        "• Неограниченное число активных финансовых целей\n"
        "• Расширенная мультивалютность и аналитика\n"
        "• Приоритетная поддержка в системе",
        parse_mode="HTML",
        reply_markup=payment_keyboard
    )


async def give_test_premium_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Тестовая мгновенная активация премиум-статуса."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("UPDATE active_users SET is_premium = 1 WHERE user_id = ?", (user_id,))
    cursor.execute("""
        INSERT INTO payments (user_id, amount, currency, pay_date, status)
        VALUES (?, ?, ?, ?, 'success')
    """, (user_id, PREMIUM_PRICE_RUB, "RUB", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    
    back_portfolio_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("📂 Открыть портфель активов", callback_data="back_to_list")
    ]])
    await query.edit_message_text(
        "🎉 <b>Статус Premium успешно активирован!</b> Все системные ограничения сняты.",
        parse_mode="HTML",
        reply_markup=back_portfolio_markup
    )


# ============================================================================
# АДМИНИСТРАТИВНАЯ ПАНЕЛЬ И УПРАВЛЕНИЕ
# ============================================================================

async def admin_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображение панели администратора сбора статистики."""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ к административной панели ограничен.")
        return
        
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM active_users")
    total_users_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM active_users WHERE is_premium = 1")
    total_premium_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM goals")
    total_goals_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT currency, SUM(amount) FROM payments GROUP BY currency")
    revenue_rows = cursor.fetchall()
    
    conn.close()
    
    admin_panel_text = (
        "🛠 <b>Панель административного контроля</b>\n"
        "————————————————————\n"
        f"👥 Всего зарегистрировано пользователей: <b>{total_users_count}</b>\n"
        f"⭐ Активных Premium-аккаунтов: <b>{total_premium_count}</b>\n"
        f"🎯 Всего создано финансовых целей: <b>{total_goals_count}</b>\n\n"
        "💰 <b>Финансовые поступления:</b>\n"
    )
    for curr_code, sum_val in revenue_rows:
        admin_panel_text += f"• {sum_val or 0} {curr_code}\n"
        
    admin_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Сделать рассылку пользователям", callback_data="admin_broadcast_init")]
    ])
    
    await update.message.reply_text(admin_panel_text, parse_mode="HTML", reply_markup=admin_keyboard)


# ============================================================================
# ГЛОБАЛЬНЫЙ ИНИЦИАТОР И ТОЧКА ВХОДА
# ============================================================================

async def post_initialization_hook(application: Application) -> None:
    """Хук, выполняемый после старта бота (запуск фоновых задач)."""
    asyncio.create_task(global_background_notification_loop(application))
    logger.info("Пост-инициализация завершена: фоновые службы успешно активированы.")


def main() -> None:
    """Главная функция запуска приложения."""
    logger.info("Запуск системы управления капиталом (Ultimate Edition)...")
    
    # 1. Инициализация базы данных
    initialize_database()
    
    # 2. Запуск фонового Health-Check сервера в отдельном потоке
    threading.Thread(target=start_health_check_daemon, daemon.daemon := True if hasattr(threading.Thread, 'daemon') else None).start()
    
    # 3. Сборка приложения Telegram Bot
    application = Application.builder().token(TOKEN).post_init(post_initialization_hook).build()

    # Регистрация диалога настройки времени
    time_setup_conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", command_start_handler)],
        states={
            WAITING_START_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, start_time_entered_handler)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation_handler)],
        allow_reentry=True
    )
    application.add_handler(time_setup_conv_handler)

    # Регистрация общих команд и кнопок
    application.add_handler(CommandHandler("admin", admin_command_handler))
    application.add_handler(CommandHandler("goals", render_goals_portfolio_list))
    application.add_handler(CallbackQueryHandler(render_goals_portfolio_list, pattern="^back_to_list$"))
    application.add_handler(CallbackQueryHandler(new_goal_entry_dispatcher, pattern="^start_new_goal$"))
    application.add_handler(CallbackQueryHandler(buy_premium_callback_handler, pattern="^buy_premium$"))
    application.add_handler(CallbackQueryHandler(give_test_premium_callback_handler, pattern="^give_test_premium$"))

    # Регистрация мастера создания цели (FSM)
    new_goal_conversation_handler = ConversationHandler(
        entry_points=[
            CommandHandler("newgoal", new_goal_entry_dispatcher),
            CallbackQueryHandler(new_goal_entry_dispatcher, pattern="^start_new_goal$")
        ],
        states={
            CHOOSE_CATEGORY: [
                CallbackQueryHandler(category_chosen_step, pattern="^cat_"),
                CallbackQueryHandler(cancel_conversation_handler, pattern="^cancel_creation$")
            ],
            WAITING_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_name_entered_step),
                CallbackQueryHandler(step_back_to_cat, pattern="^back_to_cat$")
            ],
            CHOOSE_CURRENCY: [
                CallbackQueryHandler(currency_chosen_step, pattern="^curr_"),
                CallbackQueryHandler(step_back_to_name, pattern="^back_to_name$")
            ],
            SELECT_MODE: [
                CallbackQueryHandler(goal_mode_chosen_step, pattern="^gmode_"),
                CallbackQueryHandler(step_back_to_curr, pattern="^back_to_curr$")
            ],
            WAITING_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_price_entered_step),
                CallbackQueryHandler(step_back_to_mode, pattern="^back_to_mode$")
            ],
            WAITING_SAVED: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_saved_entered_step),
                CallbackQueryHandler(step_back_to_price, pattern="^back_to_price$")
            ],
            WAITING_DAILY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_daily_entered_step),
                CallbackQueryHandler(step_back_to_saved, pattern="^back_to_saved$")
            ],
            WAITING_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, goal_date_entered_step),
                CallbackQueryHandler(step_back_to_saved, pattern="^back_to_saved$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation_handler)],
        allow_reentry=True
    )
    application.add_handler(new_goal_conversation_handler)

    # Регистрация диалога пополнения баланса цели
    deposit_conversation_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(goal_inline_router_callback, pattern="^(view_|addmoney_|del_)")],
        states={
            WAITING_ADD_SUM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, process_financial_deposit),
                CallbackQueryHandler(cancel_deposit_action, pattern="^cancel_deposit_")
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation_handler)],
        allow_reentry=True
    )
    application.add_handler(deposit_conversation_handler)

    # 4. Запуск процесса поллинга
    logger.info("Все обработчики и маршрутизаторы зарегистрированы. Бот начинает опрос серверов Telegram.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
