# bot.py
import logging
import sqlite3
import os
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler

# --- Конфигурация ---
TOKEN = os.environ.get("BOT_TOKEN")  # <-- Добавь этот секрет в Render (Environment Variables)
ADMIN_IDS = [123456789]  # <-- Замени на свой Telegram ID
DB_PATH = "bot.db"

# --- Логгинг ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Инициализация базы ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
                        telegram_id INTEGER PRIMARY KEY,
                        name TEXT,
                        is_admin INTEGER DEFAULT 0
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        assigned_to INTEGER,
                        text TEXT,
                        difficulty INTEGER,
                        assigned_at TEXT,
                        deadline TEXT,
                        status TEXT DEFAULT 'active'
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id INTEGER,
                        user_id INTEGER,
                        report_text TEXT,
                        photo_file_id TEXT,
                        submitted_at TEXT,
                        status TEXT DEFAULT 'pending'
                    )''')
        c.execute('''CREATE TABLE IF NOT EXISTS rewards (
                        user_id INTEGER,
                        points INTEGER
                    )''')
        conn.commit()

# --- Помощники ---
def get_user_points(user_id):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("SELECT SUM(points) FROM rewards WHERE user_id=?", (user_id,))
        result = c.fetchone()[0]
        return result or 0

def is_admin(user_id):
    return user_id in ADMIN_IDS

# --- Команды ---
async def start(update: Update, context: CallbackContext):
    user = update.effective_user
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (telegram_id, name, is_admin) VALUES (?, ?, ?)",
                  (user.id, user.full_name, int(is_admin(user.id))))
        conn.commit()

    buttons = [["📋 Мои задания", "✅ Отправить отчёт"], ["🏆 Мои баллы"]]
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\nГотов к новым заданиям?",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True)
    )

# --- Обработка сообщений ---
async def handle_message(update: Update, context: CallbackContext):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "📋 Мои задания":
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT id, text, deadline FROM tasks WHERE assigned_to=? AND status='active'", (user_id,))
            task = c.fetchone()
            if task:
                await update.message.reply_text(f"📝 Задание: {task[1]}\n⏰ Срок до: {task[2]}")
            else:
                await update.message.reply_text("На данный момент для тебя нет заданий. Подожди немного — скоро появятся!")

    elif text == "🏆 Мои баллы":
        points = get_user_points(user_id)
        await update.message.reply_text(f"У тебя {points} баллов 🏅")

    elif text == "✅ Отправить отчёт":
        await update.message.reply_text("Напиши текст отчёта и прикрепи фото. Пожалуйста, отправь их как одно сообщение.")
        context.user_data["waiting_for_report"] = True

# --- Приём отчётов ---
async def handle_photo(update: Update, context: CallbackContext):
    if context.user_data.get("waiting_for_report"):
        user_id = update.effective_user.id
        photo = update.message.photo[-1].file_id
        caption = update.message.caption or "(без текста)"

        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM tasks WHERE assigned_to=? AND status='active'", (user_id,))
            task = c.fetchone()
            if task:
                now = datetime.now().isoformat()
                c.execute("INSERT INTO reports (task_id, user_id, report_text, photo_file_id, submitted_at) VALUES (?, ?, ?, ?, ?)",
                          (task[0], user_id, caption, photo, now))
                conn.commit()
                await update.message.reply_text("Отчёт отправлен! Ожидай проверки. 🕵️")
                context.user_data["waiting_for_report"] = False
            else:
                await update.message.reply_text("У тебя нет активных заданий.")

# --- Админская команда ---
async def add_task(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("Нет доступа")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text("Формат: /add_task <telegram_id> <уровень> <текст задания>")
        return

    assigned_id = int(args[0])
    level = int(args[1])
    text = " ".join(args[2:])
    now = datetime.now()
    deadline = now + timedelta(hours=6)

    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO tasks (assigned_to, text, difficulty, assigned_at, deadline) VALUES (?, ?, ?, ?, ?)",
                  (assigned_id, text, level, now.isoformat(), deadline.isoformat()))
        conn.commit()

    await update.message.reply_text("Задание назначено ✅")

# --- Фоновая проверка дедлайнов ---
def check_deadlines(context: CallbackContext):
    with sqlite3.connect(DB_PATH) as conn:
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute("SELECT id, assigned_to FROM tasks WHERE status='active' AND deadline<?", (now,))
        expired = c.fetchall()
        for task_id, user_id in expired:
            context.bot.send_message(user_id, "⏰ Время на выполнение задания истекло!")
            c.execute("UPDATE tasks SET status='expired' WHERE id=?", (task_id,))
        conn.commit()

# --- Главный запуск ---
async def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add_task", add_task))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    scheduler = BackgroundScheduler()
    scheduler.add_job(check_deadlines, "interval", minutes=1, args=[app.bot])
    scheduler.start()

    print("Бот запущен...")
    await app.run_polling()

if __name__ == '__main__':
    import asyncio

    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "already running" in str(e):
            loop = asyncio.get_event_loop()
            loop.create_task(main())
            loop.run_forever()
        else:
            raise

