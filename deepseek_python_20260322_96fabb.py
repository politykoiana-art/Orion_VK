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
token = os.environ.get("VK_TOKEN") or os.environ.get("TOKEN")
if not token:
    print("   ❌ Токен не найден ни в VK_TOKEN, ни в TOKEN")
    sys.exit(1)
print(f"   ✅ Токен получен, длина: {len(token)}")

print("4. Создаём объект Bot...")
try:
    bot = Bot(token=token)
    print("   ✅ Bot создан")
except Exception as e:
    print(f"   ❌ Ошибка при создании Bot: {e}")
    sys.exit(1)

print("5. Подключаемся к БД...")
conn = sqlite3.connect("db.db", check_same_thread=False)
cursor = conn.cursor()
db_lock = threading.Lock()
print("   ✅ БД открыта")

print("6. Создаём таблицы...")
with db_lock:
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

@bot.on.message()
async def handle_message(message):
    if not message.text:
        return

    chat_id = message.peer_id
    user_id = message.from_id
    username = f"id{user_id}"
    is_user_admin = await is_admin(chat_id, user_id)

    print(f"[{msk_now().strftime('%H:%M:%S')}] Обработка сообщения от {username}: {message.text[:100]}")

    # Всегда обновляем запись пользователя (чтобы чат был в БД для отчётов)
    now_ts = int(time.time())
    with db_lock:
        cursor.execute(
            "INSERT INTO users (id, chat_id, username, last_active) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id, chat_id) DO UPDATE SET username=excluded.username, last_active=excluded.last_active",
            (user_id, chat_id, username, now_ts)
        )
        conn.commit()

    # Выходные – удаляем сообщение не-админов
    if not is_work_time(message.date):
        print("  → Время нерабочее, удаляю сообщение.")
        if not is_user_admin:
            try:
                await message.api.messages.delete(peer_id=chat_id, cmids=[message.conversation_message_id], delete_for_all=True)
            except Exception as e:
                print(f"  → Ошибка удаления: {e}")
        return

    # Поиск ссылки ВК
    matches = re.findall(link_pattern, message.text)
    if not matches:
        print("  → Ссылка ВК не найдена, удаляю сообщение.")
        if not is_user_admin:
            try:
                await message.api.messages.delete(peer_id=chat_id, cmids=[message.conversation_message_id], delete_for_all=True)
            except Exception as e:
                print(f"  → Ошибка удаления: {e}")
        return

    link = matches[0]
    if not link.startswith("http"):
        link = "https://" + link
    activity = message.text.replace(link, "").strip() or "лайк"
    print(f"  → Найдена ссылка: {link}")

    # Недельный лимит 4 поста
    with db_lock:
        cursor.execute(
            "SELECT weekly_posts FROM users WHERE id=? AND chat_id=?",
            (user_id, chat_id)
        )
        row = cursor.fetchone()
        current_posts = row[0] if row else 0

    if current_posts >= 4:
        print(f"  → Лимит превышен ({current_posts}/4).")
        await message.answer(
            f"❗ {username}, лимит 4 задания в рабочую неделю исчерпан. Задание не создано."
        )
        if not is_user_admin:
            try:
                await message.api.messages.delete(peer_id=chat_id, cmids=[message.conversation_message_id], delete_for_all=True)
            except Exception as e:
                print(f"  → Ошибка удаления: {e}")
        return

    # Создаём задание
    with db_lock:
        cursor.execute(
            "INSERT INTO tasks (chat_id, author, author_name, link, activity, created) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, username, link, activity, now_ts)
        )
        task_id = cursor.lastrowid
        cursor.execute(
            "INSERT INTO users (id, chat_id, username, last_active, weekly_posts) "
            "VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(id, chat_id) DO UPDATE SET "
            "username=excluded.username, "
            "last_active=excluded.last_active, "
            "weekly_posts=weekly_posts+1",
            (user_id, chat_id, username, now_ts)
        )
        conn.commit()

    keyboard = Keyboard(inline=True)
    keyboard.add_callback_button(
        label="✅ Актив выполнен",
        color=KeyboardButtonColor.POSITIVE,
        payload={"cmd": "done", "task_id": task_id}
    )
    sent = await message.answer(
        f"📢 Новое задание\n\n{username}\n{link}\n{activity}",
        keyboard=keyboard
    )
    with db_lock:
        cursor.execute("UPDATE tasks SET message_id=? WHERE id=?", (sent.conversation_message_id, task_id))
        conn.commit()

    # Удаляем исходное сообщение
    if not is_user_admin:
        try:
            await message.api.messages.delete(peer_id=chat_id, cmids=[message.conversation_message_id], delete_for_all=True)
            print("  → Исходное сообщение удалено.")
        except Exception as e:
            print(f"  → Ошибка удаления исходного сообщения: {e}")

@bot.on.raw_event("message_event")
async def handle_callback(event):
    payload = event.object.payload
    if payload.get("cmd") != "done":
        return
    task_id = payload.get("task_id")
    user_id = event.object.user_id
    now_ts = int(time.time())

    with db_lock:
        cursor.execute("SELECT created, chat_id, author, message_id FROM tasks WHERE id=?", (task_id,))
        task = cursor.fetchone()
        if not task:
            await event.answer("Задание не найдено")
            return
        created, chat_id, author_id, msg_id = task

        if user_id == author_id:
            await event.answer("Нельзя выполнить своё задание")
            return
        if now_ts - created < 10:
            await event.answer("Подождите 10 секунд")
            return
        cursor.execute(
            "SELECT * FROM completions WHERE task_id=? AND user_id=? AND chat_id=?",
            (task_id, user_id, chat_id)
        )
        if cursor.fetchone():
            await event.answer("Уже отмечено")
            return

        cursor.execute(
            "INSERT INTO completions (task_id, chat_id, user_id, username, time, verified) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (task_id, chat_id, user_id, f"id{user_id}", now_ts)
        )
        cursor.execute(
            "INSERT OR REPLACE INTO users (id, chat_id, username, last_active, weekly_posts) "
            "VALUES (?, ?, ?, ?, COALESCE((SELECT weekly_posts FROM users WHERE id=? AND chat_id=?), 0))",
            (user_id, chat_id, f"id{user_id}", now_ts, user_id, chat_id)
        )
        conn.commit()

    new_keyboard = Keyboard(inline=True)
    new_keyboard.add_callback_button(
        label="✅ Выполнено",
        color=KeyboardButtonColor.SECONDARY,
        payload={"cmd": "already_done"}
    )
    try:
        await bot.api.messages.edit(
            peer_id=chat_id,
            conversation_message_id=msg_id,
            message="✅ Выполнено",
            keyboard=new_keyboard.get_keyboard()
        )
    except Exception as e:
        print(f"Ошибка при смене кнопки: {e}")

    await event.answer("Засчитано ✅")

# ---------- Планировщик ----------
def scheduler():
    weekly_reported = set()
    friday_notified = set()
    monday_notified = set()
    last_week_reset = 0

    while True:
        now_ts = int(time.time())
        now_dt = msk_now()
        day = now_dt.weekday()
        hour = now_dt.hour
        week_num = now_dt.isocalendar()[1]

        # Сброс счётчика weekly_posts в понедельник 00:00
        if day == 0 and hour == 0 and now_ts - last_week_reset > 3600:
            with db_lock:
                cursor.execute("UPDATE users SET weekly_posts = 0")
                conn.commit()
            last_week_reset = now_ts
            print("Сброшен недельный счётчик постов")

        with db_lock:
            cursor.execute("SELECT DISTINCT chat_id FROM users")
            chats = {r[0] for r in cursor.fetchall()}
            cursor.execute("SELECT DISTINCT chat_id FROM tasks")
            chats |= {r[0] for r in cursor.fetchall()}

        for chat_id in chats:
            # Уведомления о начале/конце недели
            if day == 4 and hour == 23 and (chat_id, week_num) not in friday_notified:
                try:
                    asyncio.run_coroutine_threadsafe(
                        bot.api.messages.send(peer_id=chat_id, message="🌙 Пост-чат ушел на выходные! Актив по желанию", random_id=0),
                        bot.loop
                    )
                    friday_notified.add((chat_id, week_num))
                except Exception as e:
                    print(f"Ошибка пятничного сообщения: {e}")

            if day == 0 and hour == 7 and (chat_id, week_num) not in monday_notified:
                try:
                    asyncio.run_coroutine_threadsafe(
                        bot.api.messages.send(peer_id=chat_id, message="☀️ Доброе утро, пост-чат работает в нормальном режиме", random_id=0),
                        bot.loop
                    )
                    monday_notified.add((chat_id, week_num))
                except Exception as e:
                    print(f"Ошибка понедельничного сообщения: {e}")

            # Истекшие задания (24 часа)
            with db_lock:
                cursor.execute(
                    "SELECT id, created, author, author_name, message_id, link FROM tasks WHERE chat_id=?",
                    (chat_id,)
                )
                tasks = cursor.fetchall()

            for task in tasks:
                task_id, created, author_id, author_name, msg_id, link = task
                if now_ts - created > 86400:
                    with db_lock:
                        cursor.execute(
                            "SELECT username FROM completions WHERE task_id=? AND chat_id=?",
                            (task_id, chat_id)
                        )
                        done_users = {x[0] for x in cursor.fetchall()}
                        cursor.execute(
                            "SELECT username FROM users WHERE chat_id=?",
                            (chat_id,)
                        )
                        all_users = {x[0] for x in cursor.fetchall()}

                    admins = set()
                    for u in all_users:
                        with db_lock:
                            cursor.execute("SELECT id FROM users WHERE username=? AND chat_id=?", (u, chat_id))
                            row = cursor.fetchone()
                        if row:
                            is_adm = asyncio.run_coroutine_threadsafe(
                                is_admin(chat_id, row[0]), bot.loop
                            ).result()
                            if is_adm:
                                admins.add(u)

                    not_done = (all_users - done_users) - {author_name} - admins
                    if not_done:
                        text = "❌ Не выполнили задание:\n\n" + "\n".join([f"{u}" for u in not_done if u])
                    else:
                        text = "✅ Все выполнили задание"

                    try:
                        asyncio.run_coroutine_threadsafe(
                            bot.api.messages.send(peer_id=chat_id, message=text, random_id=0),
                            bot.loop
                        )
                    except Exception as e:
                        print(f"Ошибка отчёта о невыполнивших: {e}")

                    with db_lock:
                        cursor.execute("DELETE FROM tasks WHERE id=?", (task_id,))
                        conn.commit()

            # Недельный отчёт (воскресенье 12:00)
            if day == 6 and hour == 12 and (chat_id, week_num) not in weekly_reported:
                week_ago = now_ts - 604800
                with db_lock:
                    cursor.execute(
                        "SELECT username FROM users WHERE chat_id=? AND last_active<?",
                        (chat_id, week_ago)
                    )
                    inactive = [x[0] for x in cursor.fetchall() if x[0]]
                    cursor.execute(
                        "SELECT username, COUNT(*) as c FROM completions WHERE chat_id=? "
                        "GROUP BY user_id ORDER BY c DESC LIMIT 5",
                        (chat_id,)
                    )
                    top = cursor.fetchall()

                text = "📊 **Недельный отчёт**\n\n"
                if inactive:
                    text += "❌ Неактивные:\n" + "\n".join(inactive) + "\n\n"
                else:
                    text += "✅ Все активны!\n\n"
                if top:
                    text += "🏆 **Топ по выполнениям:**\n"
                    for t in top:
                        text += f"{t[0]} — {t[1]}\n"

                try:
                    asyncio.run_coroutine_threadsafe(
                        bot.api.messages.send(peer_id=chat_id, message=text, random_id=0),
                        bot.loop
                    )
                    weekly_reported.add((chat_id, week_num))
                except Exception as e:
                    print(f"Ошибка недельного отчёта: {e}")

        time.sleep(60)

# ---------- Health-сервер ----------
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get("PORT", 8000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()

if __name__ == "__main__":
    threading.Thread(target=run_health_server, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    print("Бот запущен и готов к работе!")
    bot.run_forever()