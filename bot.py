import sys
print("1. Импортируем модули...")
import asyncio
import datetime
import re
import sqlite3
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

print("2. Импортируем vkbottle...")
try:
    from vkbottle import Bot
    from vkbottle.tools import Keyboard, KeyboardButtonColor, Callback
    print("   ✅ vkbottle импортирован")
except Exception as e:
    print(f"   ❌ Ошибка импорта vkbottle: {e}")
    sys.exit(1)

print("3. Проверяем переменные окружения...")
print("   Переменные, содержащие TOKEN:")
for key in os.environ:
    if "TOKEN" in key:
        print(f"   {key}: {os.environ[key][:20]}...")
    elif key in ("VK_TOKEN", "TOKEN"):
        print(f"   {key}: {os.environ[key][:20]}...")
    else:
        pass  # не выводим все, чтобы не перегружать логи

print("4. Получаем токен...")
token = os.environ.get("VK_TOKEN") or os.environ.get("TOKEN")
if not token:
    print("   ❌ Токен не найден ни в VK_TOKEN, ни в TOKEN")
    sys.exit(1)
print(f"   ✅ Токен получен, длина: {len(token)}")

print("5. Создаём объект Bot...")
try:
    bot = Bot(token=token)
    print("   ✅ Bot создан")
except Exception as e:
    print(f"   ❌ Ошибка при создании Bot: {e}")
    sys.exit(1)

print("6. Подключаемся к БД...")
try:
    conn = sqlite3.connect("db.db", check_same_thread=False)
    cursor = conn.cursor()
    db_lock = threading.Lock()
    print("   ✅ БД открыта")
except Exception as e:
    print(f"   ❌ Ошибка БД: {e}")
    sys.exit(1)

print("7. Создаём таблицы...")
with db_lock:
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER,
                chat_id INTEGER,
                username TEXT,
                last_active INTEGER,
                weekly_posts INTEGER DEFAULT 0,
                PRIMARY KEY(id, chat_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                author INTEGER,
                author_name TEXT,
                link TEXT,
                activity TEXT,
                created INTEGER,
                message_id INTEGER
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS completions (
                task_id INTEGER,
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                time INTEGER,
                verified INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        print("   ✅ Таблицы созданы/проверены")
    except Exception as e:
        print(f"   ❌ Ошибка при создании таблиц: {e}")
        sys.exit(1)

link_pattern = r"(?:https?://)?(?:www\.)?(?:vk\.com|vkontakte\.ru)/(?:[^\s]+)"
MSK = datetime.timezone(datetime.timedelta(hours=3))

def msk_now():
    return datetime.datetime.now(MSK)

def is_work_time(timestamp):
    dt = datetime.datetime.fromtimestamp(timestamp, tz=MSK)
    weekday = dt.weekday()
    hour = dt.hour
    if weekday == 0:
        return hour >= 7
    elif 1 <= weekday <= 3:
        return True
    elif weekday == 4:
        return hour < 23
    else:
        return False

async def is_admin(chat_id, user_id):
    try:
        members = await bot.api.messages.get_conversation_members(peer_id=chat_id)
        for member in members.items:
            if member.member_id == user_id and member.is_admin:
                return True
        return False
    except Exception:
        return False

print("8. Регистрируем обработчик сообщений...")
@bot.on.message()
async def handle_message(message):
    print("   Получено сообщение:", message.text[:100] if message.text else "пустое")
    # Здесь должен быть полный код обработки, но пока оставим заглушку,
    # чтобы убедиться, что бот запускается.
    # Позже вы замените на полный код.
    await message.answer("Бот работает!")

print("9. Регистрируем обработчик callback...")
@bot.on.raw_event("message_event")
async def handle_callback(event):
    print("   Получен callback")
    await event.answer("Обработано")

print("10. Запускаем health-сервер...")
def run_health_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

threading.Thread(target=run_health_server, daemon=True).start()
print("   ✅ Health-сервер запущен")

print("11. Запускаем планировщик...")
def scheduler():
    print("   Планировщик запущен")
    while True:
        time.sleep(60)
        # временно пусто

threading.Thread(target=scheduler, daemon=True).start()
print("   ✅ Планировщик запущен")

print("12. Запускаем бота...")
try:
    bot.run_forever()
except Exception as e:
    print(f"   ❌ Ошибка при запуске бота: {e}")
