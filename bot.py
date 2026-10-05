# -*- coding: utf-8 -*-
"""
YUK-FURA BOT (yakuniy, barqaror versiya)
=========================================
Furachi (haydovchi) va Yuk beruvchi (cargo owner) ni bog'lovchi Telegram bot.

O'RNATISH (Pydroid 3):
1. Pydroid 3 -> Pip -> qidiruvga "pyTelegramBotAPI" yozib O'RNATING.
2. Pastdagi TOKEN va ADMIN_ID qiymatlarini o'zingizniki bilan almashtiring.
3. Shu faylni RUN qiling.

DIQQAT: Bu bot real pulni o'zi ushlab turolmaydi - balans ichki hisob-kitob.
Real pul kelganda admin /balans_qoshish buyrug'i bilan balansni oshiradi.
"""

import re
import sqlite3
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None
import math
import datetime
import threading
import time
import sys
import os
import traceback

try:
    import telebot
    from telebot import types
except ImportError:
    print("XATOLIK: 'telebot' kutubxonasi topilmadi.")
    print("Pydroid 3 -> Pip -> 'pyTelegramBotAPI' ni o'rnating, keyin qayta ishga tushiring.")
    sys.exit(1)

# =========================== SOZLAMALAR ===========================
# Server (Railway/Render)da ishlatilsa, TOKEN va ADMIN_ID muhit o'zgaruvchisidan
# (Environment Variables) olinadi. Pydroid'da telefonda ishlatilsa, pastdagi
# standart qiymatlar ishlatiladi - shunchaki o'zgartirib qo'ying.
TOKEN = os.getenv("BOT_TOKEN", "BU_YERGA_TOKEN_QOYING")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
OWNER_FOIZ = 0.02                         # Yuk beruvchidan olinadigan foiz (yuk yolga chiqqanda)
DRIVER_FOIZ = 0.02                        # Furachidan olinadigan foiz (yetkazib bergach)
ADMIN_CARD = os.getenv("ADMIN_CARD", "9860 1606 0644 0149")

if not TOKEN.strip() or ":" not in TOKEN:
    print("=" * 60)
    print("XATOLIK: TOKEN notogri yoki kiritilmagan!")
    print("Fayl boshidagi TOKEN qatoriga @BotFather dan olgan tokenni toliq qoying.")
    print("Togri token namunasi: 123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    print("=" * 60)
    sys.exit(1)

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
DB_PATH = "yukfura.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL) and psycopg2 is not None
if os.getenv("DATABASE_URL", "").strip() and psycopg2 is None:
    print("OGOHLANTIRISH: DATABASE_URL berilgan, lekin psycopg2 ornatilmagan. "
          "requirements.txt ga psycopg2-binary qoshing. Hozircha vaqtinchalik SQLite ishlatiladi.")
db_lock = threading.Lock()


class _CursorWrap:
    """sqlite3 kursori va psycopg2 kursorini bir xil korinishda ishlatish uchun."""

    def __init__(self, raw, is_pg):
        self.raw = raw
        self.is_pg = is_pg

    def _conv(self, sql):
        return sql.replace("?", "%s") if self.is_pg else sql

    def execute(self, sql, params=()):
        self.raw.execute(self._conv(sql), params)
        return self

    def fetchone(self):
        return self.raw.fetchone()

    def fetchall(self):
        return self.raw.fetchall()

    @property
    def lastrowid(self):
        if self.is_pg:
            return None  # Postgresda INSERT...RETURNING id orqali olinadi
        return self.raw.lastrowid


class _ConnWrap:
    """db() har doim shu obyektni qaytaradi - qolgan butun kod ozgarishsiz ishlayveradi."""

    def __init__(self, raw, is_pg):
        self.raw = raw
        self.is_pg = is_pg

    def execute(self, sql, params=()):
        cur = _CursorWrap(self.raw.cursor(), self.is_pg)
        cur.execute(sql, params)
        return cur

    def cursor(self):
        return _CursorWrap(self.raw.cursor(), self.is_pg)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        try:
            self.raw.rollback()
        except Exception:
            pass

    def close(self):
        self.raw.close()

# =========================== BAZA ===========================

def db():
    if USE_PG:
        raw = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return _ConnWrap(raw, is_pg=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return _ConnWrap(conn, is_pg=False)


def init_db():
    with db_lock:
        conn = db()
        c = conn.cursor()
        pk = "SERIAL PRIMARY KEY" if USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            role TEXT,
            full_name TEXT,
            phone TEXT,
            car_brand TEXT,
            car_model TEXT,
            car_plate TEXT,
            balance REAL DEFAULT 0,
            verified INTEGER DEFAULT 0,
            verify_photo TEXT,
            rating_sum REAL DEFAULT 0,
            rating_count INTEGER DEFAULT 0,
            created_at TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS cargos (
            id """ + pk + """,
            owner_id BIGINT,
            cargo_type TEXT,
            weight TEXT,
            from_loc TEXT,
            to_loc TEXT,
            price REAL,
            lat REAL,
            lon REAL,
            to_lat REAL,
            to_lon REAL,
            status TEXT DEFAULT 'open',
            driver_id BIGINT,
            owner_fee REAL DEFAULT 0,
            owner_paid INTEGER DEFAULT 0,
            driver_fee REAL DEFAULT 0,
            driver_paid INTEGER DEFAULT 0,
            rated INTEGER DEFAULT 0,
            owner_pay_req INTEGER DEFAULT 0,
            driver_pay_req INTEGER DEFAULT 0,
            created_at TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS driver_locations (
            user_id BIGINT PRIMARY KEY,
            lat REAL,
            lon REAL,
            updated_at TEXT
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value REAL
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS help_map (
            forwarded_msg_id BIGINT PRIMARY KEY,
            user_id BIGINT
        )""")
        if USE_PG:
            c.execute("INSERT INTO settings(key, value) VALUES ('admin_balance', 0) ON CONFLICT (key) DO NOTHING")
        else:
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('admin_balance', 0)")
        conn.commit()

        # Eski bazalarda yangi ustunlar bolmasligi mumkin - xavfsiz qoshib qoyamiz.
        # Postgresda notogri ALTER tranzaksiyani "buzadi", shuning uchun har birini
        # alohida commit/rollback qilamiz.
        cargo_cols = [
            ("owner_fee", "REAL DEFAULT 0"), ("owner_paid", "INTEGER DEFAULT 0"),
            ("driver_fee", "REAL DEFAULT 0"), ("driver_paid", "INTEGER DEFAULT 0"),
            ("rated", "INTEGER DEFAULT 0"), ("to_lat", "REAL"), ("to_lon", "REAL"),
            ("owner_pay_req", "INTEGER DEFAULT 0"), ("driver_pay_req", "INTEGER DEFAULT 0"),
            ("owner_receipt", "TEXT"), ("driver_receipt", "TEXT"),
        ]
        user_cols = [
            ("verified", "INTEGER DEFAULT 0"), ("verify_photo", "TEXT"),
            ("rating_sum", "REAL DEFAULT 0"), ("rating_count", "INTEGER DEFAULT 0"),
        ]
        for table, cols in (("cargos", cargo_cols), ("users", user_cols)):
            for col, coltype in cols:
                try:
                    conn.execute("ALTER TABLE " + table + " ADD COLUMN " + col + " " + coltype)
                    conn.commit()
                except Exception:
                    conn.rollback()
        conn.close()


def get_user(user_id):
    with db_lock:
        conn = db()
        row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        return row


def save_user(user_id, **fields):
    with db_lock:
        conn = db()
        c = conn.cursor()
        existing = c.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,)).fetchone()
        if existing:
            keys = ", ".join(k + "=?" for k in fields)
            c.execute("UPDATE users SET " + keys + " WHERE user_id=?", (*fields.values(), user_id))
        else:
            fields["user_id"] = user_id
            fields.setdefault("created_at", str(datetime.datetime.now()))
            cols = ", ".join(fields.keys())
            qs = ", ".join("?" for _ in fields)
            c.execute("INSERT INTO users (" + cols + ") VALUES (" + qs + ")", tuple(fields.values()))
        conn.commit()
        conn.close()


def change_balance(user_id, delta):
    with db_lock:
        conn = db()
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (delta, user_id))
        conn.commit()
        conn.close()


def add_admin_balance(delta):
    with db_lock:
        conn = db()
        conn.execute("UPDATE settings SET value = value + ? WHERE key='admin_balance'", (delta,))
        conn.commit()
        conn.close()


def get_admin_balance():
    with db_lock:
        conn = db()
        row = conn.execute("SELECT value FROM settings WHERE key='admin_balance'").fetchone()
        conn.close()
        return row["value"] if row else 0


def add_cargo(**fields):
    with db_lock:
        conn = db()
        fields.setdefault("created_at", str(datetime.datetime.now()))
        cols = ", ".join(fields.keys())
        qs = ", ".join("?" for _ in fields)
        if USE_PG:
            cur = conn.execute(
                "INSERT INTO cargos (" + cols + ") VALUES (" + qs + ") RETURNING id",
                tuple(fields.values()),
            )
            cid = cur.fetchone()["id"]
        else:
            cur = conn.execute("INSERT INTO cargos (" + cols + ") VALUES (" + qs + ")", tuple(fields.values()))
            cid = cur.lastrowid
        conn.commit()
        conn.close()
        return cid


def get_cargo(cargo_id):
    with db_lock:
        conn = db()
        row = conn.execute("SELECT * FROM cargos WHERE id=?", (cargo_id,)).fetchone()
        conn.close()
        return row


def update_cargo(cargo_id, **fields):
    with db_lock:
        conn = db()
        keys = ", ".join(k + "=?" for k in fields)
        conn.execute("UPDATE cargos SET " + keys + " WHERE id=?", (*fields.values(), cargo_id))
        conn.commit()
        conn.close()


def open_cargos():
    with db_lock:
        conn = db()
        rows = conn.execute("SELECT * FROM cargos WHERE status='open' ORDER BY id DESC").fetchall()
        conn.close()
        return rows


def owner_cargos(owner_id):
    with db_lock:
        conn = db()
        rows = conn.execute("SELECT * FROM cargos WHERE owner_id=? ORDER BY id DESC", (owner_id,)).fetchall()
        conn.close()
        return rows


def driver_cargos(driver_id):
    with db_lock:
        conn = db()
        rows = conn.execute("SELECT * FROM cargos WHERE driver_id=? ORDER BY id DESC", (driver_id,)).fetchall()
        conn.close()
        return rows


def save_driver_location(user_id, lat, lon):
    with db_lock:
        conn = db()
        conn.execute(
            "INSERT INTO driver_locations(user_id, lat, lon, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET lat=excluded.lat, lon=excluded.lon, updated_at=excluded.updated_at",
            (user_id, lat, lon, str(datetime.datetime.now())),
        )
        conn.commit()
        conn.close()


def save_help_map(msg_id, user_id):
    with db_lock:
        conn = db()
        if USE_PG:
            conn.execute(
                "INSERT INTO help_map(forwarded_msg_id, user_id) VALUES (?,?) "
                "ON CONFLICT (forwarded_msg_id) DO UPDATE SET user_id=excluded.user_id",
                (msg_id, user_id),
            )
        else:
            conn.execute("INSERT OR REPLACE INTO help_map(forwarded_msg_id, user_id) VALUES (?,?)", (msg_id, user_id))
        conn.commit()
        conn.close()


def get_help_user(msg_id):
    with db_lock:
        conn = db()
        row = conn.execute("SELECT user_id FROM help_map WHERE forwarded_msg_id=?", (msg_id,)).fetchone()
        conn.close()
        return row["user_id"] if row else None


def unpaid_owner_cargo(owner_id):
    """Yuk beruvchining hali tolamagan (owner_fee) yuki bormi - bolsa shu qatorni qaytaradi."""
    with db_lock:
        conn = db()
        row = conn.execute(
            "SELECT * FROM cargos WHERE owner_id=? AND owner_fee>0 AND owner_paid=0 ORDER BY id LIMIT 1",
            (owner_id,),
        ).fetchone()
        conn.close()
        return row


def unpaid_driver_cargo(driver_id):
    """Furachining hali tolamagan (driver_fee) yuki bormi - bolsa shu qatorni qaytaradi."""
    with db_lock:
        conn = db()
        row = conn.execute(
            "SELECT * FROM cargos WHERE driver_id=? AND driver_fee>0 AND driver_paid=0 ORDER BY id LIMIT 1",
            (driver_id,),
        ).fetchone()
        conn.close()
        return row


# =========================== YORDAMCHI FUNKSIYALAR ===========================

user_state = {}


def step_is(m, name):
    """Foydalanuvchi shu bosqichda bolsa True. Buyruqlar (/admin, /myid ...) hech qachon
    bosqichga tushib qolmasligi uchun, / bilan boshlangan matnlar hisobga olinmaydi."""
    text = getattr(m, "text", None)
    if text and text.startswith("/"):
        return False
    return user_state.get(m.from_user.id, {}).get("step") == name



def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def main_menu(role):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if role == "driver":
        kb.add(types.KeyboardButton("Ochiq yuklar"), types.KeyboardButton("Yaqin yuklarni topish"))
        kb.add(types.KeyboardButton("Mening yuklarim"), types.KeyboardButton("Balansim"))
        kb.add(types.KeyboardButton("Profilim"), types.KeyboardButton("Yordam"))
    elif role == "owner":
        kb.add(types.KeyboardButton("Yuk qoshish"), types.KeyboardButton("Mening yuklarim"))
        kb.add(types.KeyboardButton("Balansim"), types.KeyboardButton("Profilim"))
        kb.add(types.KeyboardButton("Yordam"))
    return kb


def role_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("Men furachiman", callback_data="role_driver"),
        types.InlineKeyboardButton("Men yuk beruvchiman", callback_data="role_owner"),
    )
    return kb


def phone_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("Raqamni yuborish", request_contact=True))
    return kb


def location_keyboard(label):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton(label, request_location=True))
    return kb


def cancel_state(user_id):
    user_state.pop(user_id, None)


def is_admin(user_id):
    return user_id == ADMIN_ID


def driver_rating_text(u):
    if not u or not u["rating_count"]:
        return "hali baho yoq"
    avg = u["rating_sum"] / u["rating_count"]
    return format(avg, ".1f") + "/5 (" + str(u["rating_count"]) + " baho)"


def cargo_card_text(row):
    text = (
        "Yuk #" + str(row["id"]) + "\n"
        "Turi: " + str(row["cargo_type"]) + "\n"
        "Massasi: " + str(row["weight"]) + "\n"
        "Qayerdan: " + str(row["from_loc"]) + "\n"
        "Qayerga: " + str(row["to_loc"]) + "\n"
        "Narxi: " + format(row["price"], ",.0f") + " som\n"
        "Holati: " + str(row["status"])
    )
    try:
        if row["to_lat"] is not None and row["to_lon"] is not None and row["lat"] is not None:
            dist = haversine(row["lat"], row["lon"], row["to_lat"], row["to_lon"])
            text += "\nTaxminiy masofa: ~" + format(dist, ".1f") + " km"
    except Exception:
        pass
    return text


def parse_som(text):
    """"500000", "500 000", "500 ming", "500ming", "1 mln", "150k" kabi yozuvlarni
    somga aylantiradi. Tushunarsiz bolsa None qaytaradi."""
    if not text:
        return None
    t = text.strip().lower()
    for word in ("so'm", "soʻm", "sum", "сум", "som"):
        t = t.replace(word, "")
    t = t.strip()
    multiplier = 1
    if "mln" in t or "million" in t or "млн" in t:
        multiplier = 1_000_000
        t = re.sub(r"mln|million|млн", "", t)
    elif "ming" in t or "тыс" in t:
        multiplier = 1_000
        t = re.sub(r"ming|тыс", "", t)
    t = t.strip().replace(" ", "").replace(",", ".")
    if t.endswith("k"):
        t = t[:-1]
        multiplier = 1_000
    if not t:
        return None
    try:
        val = float(t)
    except ValueError:
        return None
    return val * multiplier


def safe_send(chat_id, text, **kwargs):
    """Xato chiqsa ham bot to'xtamasligi uchun himoyalangan xabar yuborish."""
    try:
        bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        print("send_message xatosi:", e)


def owner_pay_keyboard(cargo_row):
    kb = types.InlineKeyboardMarkup()
    if cargo_row["owner_pay_req"]:
        kb.add(types.InlineKeyboardButton("Adminga qayta eslatish", callback_data="remindowner_" + str(cargo_row["id"])))
    else:
        kb.add(types.InlineKeyboardButton("Toladim", callback_data="ownerpaid_" + str(cargo_row["id"])))
    return kb


def driver_pay_keyboard(cargo_row):
    kb = types.InlineKeyboardMarkup()
    if cargo_row["driver_pay_req"]:
        kb.add(types.InlineKeyboardButton("Adminga qayta eslatish", callback_data="reminddriver_" + str(cargo_row["id"])))
    else:
        kb.add(types.InlineKeyboardButton("Toladim", callback_data="driverpaid_" + str(cargo_row["id"])))
    return kb


def send_owner_pay_prompt(uid, cargo_row):
    if cargo_row["owner_pay_req"]:
        status = "Tolov qildim deb belgilagansiz, admin tasdiqlashini kutmoqda."
    else:
        status = "Quyidagi kartaga otkazing:\n<code>" + ADMIN_CARD + "</code>\nOtkazgach pastdagi tugmani bosing."
    safe_send(
        uid,
        "Tolanmagan xizmat haqi: Yuk #" + str(cargo_row["id"]) + ", " +
        format(cargo_row["owner_fee"], ",.0f") + " som.\n" + status,
        reply_markup=owner_pay_keyboard(cargo_row),
    )


def send_driver_pay_prompt(uid, cargo_row):
    if cargo_row["driver_pay_req"]:
        status = "Tolov qildim deb belgilagansiz, admin tasdiqlashini kutmoqda."
    else:
        status = "Quyidagi kartaga otkazing:\n<code>" + ADMIN_CARD + "</code>\nOtkazgach pastdagi tugmani bosing."
    safe_send(
        uid,
        "Tolanmagan xizmat haqi: Yuk #" + str(cargo_row["id"]) + ", " +
        format(cargo_row["driver_fee"], ",.0f") + " som.\n" + status,
        reply_markup=driver_pay_keyboard(cargo_row),
    )


def notify_admin_owner_payment(cargo_row):
    owner = get_user(cargo_row["owner_id"])
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Tasdiqlash", callback_data="confirmowner_" + str(cargo_row["id"])))
    caption = (
        "Yuk beruvchi tolov qildi deb belgiladi.\n"
        "Yuk #" + str(cargo_row["id"]) + ", " + owner["full_name"] + ", tel: " + owner["phone"] + "\n"
        "Miqdor: " + format(cargo_row["owner_fee"], ",.0f") + " som\n"
        "Kartangizda tushganini tekshirib, tasdiqlang:"
    )
    if cargo_row["owner_receipt"]:
        try:
            bot.send_photo(ADMIN_ID, cargo_row["owner_receipt"], caption=caption, reply_markup=kb)
            return
        except Exception as e:
            print("Chek rasmini yuborishda xato:", e)
    safe_send(ADMIN_ID, caption, reply_markup=kb)


def notify_admin_driver_payment(cargo_row):
    driver = get_user(cargo_row["driver_id"])
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Tasdiqlash", callback_data="confirmdriver_" + str(cargo_row["id"])))
    caption = (
        "Furachi tolov qildi deb belgiladi.\n"
        "Yuk #" + str(cargo_row["id"]) + ", " + driver["full_name"] + ", tel: " + driver["phone"] + "\n"
        "Miqdor: " + format(cargo_row["driver_fee"], ",.0f") + " som\n"
        "Kartangizda tushganini tekshirib, tasdiqlang:"
    )
    if cargo_row["driver_receipt"]:
        try:
            bot.send_photo(ADMIN_ID, cargo_row["driver_receipt"], caption=caption, reply_markup=kb)
            return
        except Exception as e:
            print("Chek rasmini yuborishda xato:", e)
    safe_send(ADMIN_ID, caption, reply_markup=kb)



# =========================== /START ===========================

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("Statistika"), types.KeyboardButton("Tasdiqlanmaganlar"))
    kb.add(types.KeyboardButton("Mening ID im"), types.KeyboardButton("Foydalanuvchi rejimi"))
    return kb


@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    cancel_state(uid)
    if is_admin(uid):
        safe_send(
            uid,
            "Salom, Admin! Bu sizning shaxsiy boshqaruv menyuingiz.\n"
            "Agar oddiy foydalanuvchi (furachi/yuk beruvchi) sifatida botni sinab "
            "kormoqchi bolsangiz, pastdagi 'Foydalanuvchi rejimi' tugmasini bosing.",
            reply_markup=admin_menu(),
        )
        return
    user = get_user(uid)
    if user and user["role"]:
        if user["role"] == "driver" and not user["verified"]:
            safe_send(uid, "Hujjatlaringiz hali admin tomonidan tasdiqlanmagan. Biroz kuting.")
            return
        safe_send(uid, "Xush kelibsiz! Asosiy menyu:", reply_markup=main_menu(user["role"]))
    else:
        safe_send(uid, "Yuk-Fura botiga xush kelibsiz!\n\nSiz kimsiz?", reply_markup=role_keyboard())


@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "Statistika")
def admin_btn_stats(message):
    admin_panel(message)


@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "Tasdiqlanmaganlar")
def admin_btn_pending(message):
    cmd_pending(message)


@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "Mening ID im")
def admin_btn_myid(message):
    cmd_myid(message)


@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "Foydalanuvchi rejimi")
def admin_btn_userswitch(message):
    uid = message.from_user.id
    user = get_user(uid)
    if user and user["role"]:
        safe_send(uid, "Foydalanuvchi menyusi (sinov uchun):", reply_markup=main_menu(user["role"]))
    else:
        safe_send(uid, "Avval rol tanlang (sinov uchun):", reply_markup=role_keyboard())


@bot.callback_query_handler(func=lambda c: c.data in ("role_driver", "role_owner"))
def cb_role(call):
    uid = call.from_user.id
    role = "driver" if call.data == "role_driver" else "owner"
    save_user(uid, role=role)
    bot.answer_callback_query(call.id)
    user_state[uid] = {"step": "d_fullname" if role == "driver" else "o_fullname", "data": {}}
    safe_send(uid, "Ism va familiyangizni kiriting:")


# =========================== RO'YXATDAN O'TISH: FURACHI ===========================

@bot.message_handler(func=lambda m: step_is(m, "d_fullname"))
def d_fullname(message):
    uid = message.from_user.id
    user_state[uid]["data"]["full_name"] = message.text.strip()
    user_state[uid]["step"] = "d_phone"
    safe_send(uid, "Telefon raqamingizni yuboring:", reply_markup=phone_keyboard())


@bot.message_handler(content_types=["contact"], func=lambda m: step_is(m, "d_phone"))
def d_phone_contact(message):
    uid = message.from_user.id
    user_state[uid]["data"]["phone"] = message.contact.phone_number
    user_state[uid]["step"] = "d_brand"
    safe_send(uid, "Fura rusmini kiriting (masalan: Isuzu, Kamaz, MAN, Howo):", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: step_is(m, "d_phone"))
def d_phone_text(message):
    uid = message.from_user.id
    user_state[uid]["data"]["phone"] = message.text.strip()
    user_state[uid]["step"] = "d_brand"
    safe_send(uid, "Fura rusmini kiriting (masalan: Isuzu, Kamaz, MAN, Howo):", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: step_is(m, "d_brand"))
def d_brand(message):
    uid = message.from_user.id
    user_state[uid]["data"]["car_brand"] = message.text.strip()
    user_state[uid]["step"] = "d_model"
    safe_send(uid, "Fura modelini kiriting:")


@bot.message_handler(func=lambda m: step_is(m, "d_model"))
def d_model(message):
    uid = message.from_user.id
    user_state[uid]["data"]["car_model"] = message.text.strip()
    user_state[uid]["step"] = "d_plate"
    safe_send(uid, "Fura davlat raqamini kiriting (masalan: 01A123BC):")


@bot.message_handler(func=lambda m: step_is(m, "d_plate"))
def d_plate(message):
    uid = message.from_user.id
    user_state[uid]["data"]["car_plate"] = message.text.strip()
    user_state[uid]["step"] = "d_photo"
    safe_send(
        uid,
        "Oxirgi qadam: tasdiqlash uchun haydovchilik guvohnomangiz (yoki pasportingiz) rasmini yuboring.\n"
        "Bu admin tomonidan tekshiriladi, shundan keyin yuk olishingiz mumkin bo'ladi.",
    )


@bot.message_handler(content_types=["photo"], func=lambda m: step_is(m, "d_photo"))
def d_photo(message):
    uid = message.from_user.id
    data = user_state[uid]["data"]
    photo_id = message.photo[-1].file_id
    save_user(
        uid,
        full_name=data["full_name"],
        phone=data["phone"],
        car_brand=data["car_brand"],
        car_model=data["car_model"],
        car_plate=data["car_plate"],
        verify_photo=photo_id,
        verified=0,
    )
    cancel_state(uid)
    safe_send(
        uid,
        "Royxatdan otdingiz! Hujjatlaringiz admin tomonidan tekshirilmoqda, biroz kuting. "
        "Tasdiqlangach sizga xabar beramiz.",
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("Tasdiqlash", callback_data="verify_" + str(uid)),
        types.InlineKeyboardButton("Rad etish", callback_data="reject_" + str(uid)),
    )
    u = get_user(uid)
    try:
        bot.send_photo(
            ADMIN_ID,
            photo_id,
            caption=(
                "Yangi furachi tasdiqlash kutmoqda:\n"
                + u["full_name"] + "\n" + u["phone"] + "\n"
                + (u["car_brand"] or "") + " " + (u["car_model"] or "") + "\n"
                + "Davlat raqami: " + (u["car_plate"] or "")
            ),
            reply_markup=kb,
        )
    except Exception as e:
        print("Admin fotosuratni olishda xato:", e)


@bot.message_handler(func=lambda m: step_is(m, "d_photo"))
def d_photo_wrong_type(message):
    safe_send(message.from_user.id, "Iltimos, hujjat rasmini rasm (photo) sifatida yuboring.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("verify_"))
def cb_verify_driver(call):
    if not is_admin(call.from_user.id):
        return
    target_uid = int(call.data.split("_")[1])
    save_user(target_uid, verified=1)
    bot.answer_callback_query(call.id, "Tasdiqlandi!")
    try:
        bot.edit_message_caption("Tasdiqlandi.", call.message.chat.id, call.message.message_id)
    except Exception as e:
        print("edit_message_caption xatosi:", e)
    safe_send(target_uid, "Hujjatlaringiz tasdiqlandi! Endi yuklarni korish va olish mumkin.", reply_markup=main_menu("driver"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("reject_"))
def cb_reject_driver(call):
    if not is_admin(call.from_user.id):
        return
    target_uid = int(call.data.split("_")[1])
    bot.answer_callback_query(call.id, "Rad etildi.")
    try:
        bot.edit_message_caption("Rad etildi.", call.message.chat.id, call.message.message_id)
    except Exception as e:
        print("edit_message_caption xatosi:", e)
    safe_send(
        target_uid,
        "Afsuski hujjatlaringiz tasdiqlanmadi. Iltimos aniqroq rasm bilan qaytadan /start orqali royxatdan oting.",
    )


# =========================== RO'YXATDAN O'TISH: YUK BERUVCHI ===========================

@bot.message_handler(func=lambda m: step_is(m, "o_fullname"))
def o_fullname(message):
    uid = message.from_user.id
    user_state[uid]["data"]["full_name"] = message.text.strip()
    user_state[uid]["step"] = "o_phone"
    safe_send(uid, "Telefon raqamingizni yuboring:", reply_markup=phone_keyboard())


@bot.message_handler(content_types=["contact"], func=lambda m: step_is(m, "o_phone"))
def o_phone_contact(message):
    uid = message.from_user.id
    data = user_state[uid]["data"]
    save_user(uid, full_name=data["full_name"], phone=message.contact.phone_number)
    cancel_state(uid)
    safe_send(uid, "Royxatdan muvaffaqiyatli otdingiz!", reply_markup=main_menu("owner"))


@bot.message_handler(func=lambda m: step_is(m, "o_phone"))
def o_phone_text(message):
    uid = message.from_user.id
    data = user_state[uid]["data"]
    save_user(uid, full_name=data["full_name"], phone=message.text.strip())
    cancel_state(uid)
    safe_send(uid, "Royxatdan muvaffaqiyatli otdingiz!", reply_markup=main_menu("owner"))


# =========================== PROFIL / BALANS ===========================

@bot.message_handler(func=lambda m: m.text == "Profilim")
def profile(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u:
        return cmd_start(message)
    if u["role"] == "driver":
        text = (
            u["full_name"] + "\n" + u["phone"] + "\n" +
            (u["car_brand"] or "") + " " + (u["car_model"] or "") + "\n" +
            "Davlat raqami: " + (u["car_plate"] or "") + "\n" +
            "Holat: " + ("Tasdiqlangan" if u["verified"] else "Tasdiqlanmagan") + "\n" +
            "Reyting: " + driver_rating_text(u)
        )
    else:
        text = u["full_name"] + "\n" + u["phone"] + "\nBalans: " + format(u["balance"], ",.0f") + " som"
    safe_send(uid, text)


@bot.message_handler(func=lambda m: m.text == "Balansim")
def balance_view(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u:
        return cmd_start(message)
    safe_send(uid, "Sizning balansingiz: " + format(u["balance"], ",.0f") + " som")


# =========================== YUK QO'SHISH (YUK BERUVCHI) ===========================

@bot.message_handler(func=lambda m: m.text == "Yuk qoshish")
def add_cargo_start(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u or u["role"] != "owner":
        return
    pending = unpaid_owner_cargo(uid)
    if pending:
        safe_send(uid, "Yangi yuk qoshishdan oldin xizmat haqini toilang:")
        send_owner_pay_prompt(uid, pending)
        return
    user_state[uid] = {"step": "c_type", "data": {}}
    safe_send(uid, "Yuk turini kiriting (masalan: qurilish materiali, oziq-ovqat):", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: step_is(m, "c_type"))
def c_type(message):
    uid = message.from_user.id
    user_state[uid]["data"]["cargo_type"] = message.text.strip()
    user_state[uid]["step"] = "c_weight"
    safe_send(uid, "Yuk massasini kiriting (masalan: 5 tonna):")


@bot.message_handler(func=lambda m: step_is(m, "c_weight"))
def c_weight(message):
    uid = message.from_user.id
    user_state[uid]["data"]["weight"] = message.text.strip()
    user_state[uid]["step"] = "c_from"
    safe_send(uid, "Qayerdan jonatiladi? (shahar/tuman nomi):")


@bot.message_handler(func=lambda m: step_is(m, "c_from"))
def c_from(message):
    uid = message.from_user.id
    user_state[uid]["data"]["from_loc"] = message.text.strip()
    user_state[uid]["step"] = "c_to"
    safe_send(uid, "Qayerga yetkazib berish kerak?")


@bot.message_handler(func=lambda m: step_is(m, "c_to"))
def c_to(message):
    uid = message.from_user.id
    user_state[uid]["data"]["to_loc"] = message.text.strip()
    user_state[uid]["step"] = "c_from_location"
    safe_send(uid, "Yukni olib ketish joyi (lokatsiya) ni yuboring:", reply_markup=location_keyboard("Joylashuvni yuborish"))


@bot.message_handler(content_types=["location"], func=lambda m: step_is(m, "c_from_location"))
def c_from_location(message):
    uid = message.from_user.id
    user_state[uid]["data"]["lat"] = message.location.latitude
    user_state[uid]["data"]["lon"] = message.location.longitude
    user_state[uid]["step"] = "c_to_location"
    safe_send(
        uid,
        "Endi yukni TUSHIRISH (yetkazish) manzilini belgilang.\n\n"
        "DIQQAT: pastdagi tugma sizning HOZIRGI turgan joyingizni yuboradi - "
        "agar yetkazish manzili boshqa joyda bolsa, undan foydalanmang!\n\n"
        "Boshqa manzilni belgilash uchun:\n"
        "1) Xabar yozish maydoni yonidagi qogoz qisqich (attach) belgisini bosing\n"
        "2) \"Location\" ni tanlang\n"
        "3) Xaritada kerakli nuqtani barmoq bilan bosib turing (uzoq bosish)\n"
        "4) Chiqqan \"Send Selected Location\" tugmasini bosing",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@bot.message_handler(content_types=["location"], func=lambda m: step_is(m, "c_to_location"))
def c_to_location(message):
    uid = message.from_user.id
    data = user_state[uid]["data"]
    data["to_lat"] = message.location.latitude
    data["to_lon"] = message.location.longitude
    dist = haversine(data["lat"], data["lon"], data["to_lat"], data["to_lon"])
    data["distance_km"] = dist
    user_state[uid]["step"] = "c_price"
    safe_send(
        uid,
        "Taxminiy masofa: ~" + format(dist, ".1f") + " km.\n\n"
        "Yuk uchun qancha tolaysiz? (masalan: 500000 yoki 500 ming):",
        reply_markup=types.ReplyKeyboardRemove(),
    )


@bot.message_handler(func=lambda m: step_is(m, "c_price"))
def c_price(message):
    uid = message.from_user.id
    price = parse_som(message.text)
    if price is None or price <= 0:
        safe_send(uid, "Tushunmadim. Masalan: 1500000 yoki 1,5 mln yoki 500 ming deb yozing.")
        return
    data = user_state[uid]["data"]
    cid = add_cargo(
        owner_id=uid,
        cargo_type=data["cargo_type"],
        weight=data["weight"],
        from_loc=data["from_loc"],
        to_loc=data["to_loc"],
        price=price,
        lat=data["lat"],
        lon=data["lon"],
        to_lat=data["to_lat"],
        to_lon=data["to_lon"],
        status="open",
    )
    cancel_state(uid)
    safe_send(uid, "Yuk elon qilindi! Yuk raqami: #" + str(cid), reply_markup=main_menu("owner"))


# =========================== OCHIQ YUKLAR / YAQIN YUKLAR (FURACHI) ===========================

def is_verified_driver(u):
    return u and u["role"] == "driver" and u["verified"]


@bot.message_handler(func=lambda m: m.text == "Ochiq yuklar")
def list_open_cargos(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not is_verified_driver(u):
        safe_send(uid, "Bu funksiya faqat tasdiqlangan furachilar uchun.")
        return
    rows = open_cargos()
    if not rows:
        safe_send(uid, "Hozircha ochiq yuklar yoq.")
        return
    for row in rows[:15]:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Olish", callback_data="take_" + str(row["id"])))
        safe_send(uid, cargo_card_text(row), reply_markup=kb)
        if row["lat"] is not None and row["lon"] is not None:
            try:
                bot.send_location(uid, row["lat"], row["lon"])
            except Exception as e:
                print("send_location xatosi:", e)


@bot.message_handler(func=lambda m: m.text == "Yaqin yuklarni topish")
def ask_driver_location(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not is_verified_driver(u):
        safe_send(uid, "Bu funksiya faqat tasdiqlangan furachilar uchun.")
        return
    safe_send(uid, "Joylashuvingizni yuboring, sizga eng yaqin yuklarni topib beraman:",
               reply_markup=location_keyboard("Joylashuvni yuborish"))


@bot.message_handler(content_types=["location"], func=lambda m: user_state.get(m.from_user.id, {}).get("step") is None)
def driver_location_update(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not is_verified_driver(u):
        return
    save_driver_location(uid, message.location.latitude, message.location.longitude)
    rows = open_cargos()
    if not rows:
        safe_send(uid, "Hozircha ochiq yuklar yoq.", reply_markup=main_menu("driver"))
        return
    scored = []
    for row in rows:
        if row["lat"] is not None and row["lon"] is not None:
            dist = haversine(message.location.latitude, message.location.longitude, row["lat"], row["lon"])
            scored.append((dist, row))
    scored.sort(key=lambda x: x[0])
    if not scored:
        safe_send(uid, "Yaqin atrofda yuk topilmadi.", reply_markup=main_menu("driver"))
        return
    safe_send(uid, "Sizga eng yaqin yuklar:", reply_markup=main_menu("driver"))
    for dist, row in scored[:5]:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Olish", callback_data="take_" + str(row["id"])))
        safe_send(uid, cargo_card_text(row) + "\nMasofa: ~" + format(dist, ".1f") + " km", reply_markup=kb)
        try:
            bot.send_location(uid, row["lat"], row["lon"])
        except Exception as e:
            print("send_location xatosi:", e)


# =========================== YUKNI OLISH / YETKAZIB BERISH ===========================

@bot.callback_query_handler(func=lambda c: c.data.startswith("take_"))
def cb_take(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["status"] != "open":
        bot.answer_callback_query(call.id, "Bu yuk allaqachon band qilingan yoki mavjud emas.", show_alert=True)
        return
    driver = get_user(uid)
    if not is_verified_driver(driver):
        bot.answer_callback_query(call.id, "Faqat tasdiqlangan furachilar yuk olishi mumkin.", show_alert=True)
        return
    pending_driver = unpaid_driver_cargo(uid)
    if pending_driver:
        bot.answer_callback_query(call.id, "Avval tolanmagan xizmat haqingizni toilang.", show_alert=True)
        send_driver_pay_prompt(uid, pending_driver)
        return

    owner_fee = round(row["price"] * OWNER_FOIZ, 0)
    update_cargo(cargo_id, status="taken", driver_id=uid, owner_fee=owner_fee, owner_paid=0)
    bot.answer_callback_query(call.id, "Yuk sizga biriktirildi!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)

    owner = get_user(row["owner_id"])
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Yetkazib berdim", callback_data="delivered_" + str(cargo_id)))
    safe_send(
        uid,
        "Siz #" + str(cargo_id) + "-yukni oldingiz.\n"
        "Yuk beruvchi: " + owner["full_name"] + ", tel: " + owner["phone"] + "\n"
        "Yetkazib bergach quyidagi tugmani bosing:",
        reply_markup=kb,
    )

    owner_kb = types.InlineKeyboardMarkup()
    owner_kb.add(types.InlineKeyboardButton("Toladim", callback_data="ownerpaid_" + str(cargo_id)))
    safe_send(
        row["owner_id"],
        "#" + str(cargo_id) + "-yukingizni furachi oldi va yolga chiqdi!\n"
        "Furachi: " + driver["full_name"] + "\nTelefon: " + driver["phone"] + "\n"
        "Fura: " + (driver["car_brand"] or "") + " " + (driver["car_model"] or "") +
        ", davlat raqami: " + (driver["car_plate"] or "") + "\n"
        "Reyting: " + driver_rating_text(driver) + "\n\n"
        "Xizmat haqi (" + format(OWNER_FOIZ * 100, ".0f") + "%): " + format(owner_fee, ",.0f") + " som.\n"
        "Quyidagi kartaga otkazing:\n<code>" + ADMIN_CARD + "</code>\n"
        "Otkazgach pastdagi tugmani bosing:",
        reply_markup=owner_kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("delivered_"))
def cb_delivered(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["status"] != "taken" or row["driver_id"] != uid:
        bot.answer_callback_query(call.id, "Bu amalni bajarib bolmaydi.", show_alert=True)
        return
    price = row["price"]
    driver_fee = round(price * DRIVER_FOIZ, 0)

    update_cargo(cargo_id, status="delivered", driver_fee=driver_fee, driver_paid=0)

    bot.answer_callback_query(call.id, "Yetkazib berish tasdiqlandi!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)

    driver_kb = types.InlineKeyboardMarkup()
    driver_kb.add(types.InlineKeyboardButton("Toladim", callback_data="driverpaid_" + str(cargo_id)))
    safe_send(
        uid,
        "#" + str(cargo_id) + "-yuk yetkazib berildi deb belgilandi.\n"
        "Xizmat haqi (" + format(DRIVER_FOIZ * 100, ".0f") + "%): " + format(driver_fee, ",.0f") + " som.\n"
        "Quyidagi kartaga otkazing:\n<code>" + ADMIN_CARD + "</code>\n"
        "Otkazgach pastdagi tugmani bosing. Toʻlamaguningizcha yangi yuk ololmaysiz:",
        reply_markup=driver_kb,
    )
    safe_send(row["owner_id"], "#" + str(cargo_id) + "-yukingiz yetkazib berildi. Furachi tasdiqladi.")

    if not row["rated"]:
        rate_kb = types.InlineKeyboardMarkup()
        rate_kb.add(*[
            types.InlineKeyboardButton(str(n) + " ★", callback_data="rate_" + str(cargo_id) + "_" + str(n))
            for n in range(1, 6)
        ])
        safe_send(row["owner_id"], "Furachiga baho bering:", reply_markup=rate_kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ownerpaid_"))
def cb_owner_paid(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["owner_id"] != uid or row["owner_paid"]:
        bot.answer_callback_query(call.id, "Bu tolov allaqachon tasdiqlangan yoki notogri.", show_alert=True)
        return
    user_state[uid] = {"step": "owner_receipt", "data": {"cargo_id": cargo_id}}
    bot.answer_callback_query(call.id, "Chek rasmini yuboring.")
    safe_send(uid, "Iltimos, tolov chekini (skrinshotini) RASM korinishida yuboring:")


@bot.callback_query_handler(func=lambda c: c.data.startswith("remindowner_"))
def cb_owner_remind(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["owner_id"] != uid or row["owner_paid"]:
        bot.answer_callback_query(call.id, "Bu tolov allaqachon tasdiqlangan yoki notogri.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "Adminga eslatma yuborildi.")
    notify_admin_owner_payment(row)


@bot.message_handler(content_types=["photo"], func=lambda m: step_is(m, "owner_receipt"))
def owner_receipt_photo(message):
    uid = message.from_user.id
    cargo_id = user_state[uid]["data"]["cargo_id"]
    cancel_state(uid)
    row = get_cargo(cargo_id)
    if not row or row["owner_id"] != uid or row["owner_paid"]:
        safe_send(uid, "Bu amalni bajarib bolmaydi.")
        return
    photo_id = message.photo[-1].file_id
    update_cargo(cargo_id, owner_pay_req=1, owner_receipt=photo_id)
    row = get_cargo(cargo_id)
    safe_send(
        uid,
        "Rahmat! Chek qabul qilindi, admin tekshirib tasdiqlaydi. "
        "Kechiksa, pastdagi 'Adminga qayta eslatish' tugmasini bosing.",
        reply_markup=owner_pay_keyboard(row),
    )
    notify_admin_owner_payment(row)


@bot.message_handler(func=lambda m: step_is(m, "owner_receipt"))
def owner_receipt_wrong_type(message):
    safe_send(message.from_user.id, "Iltimos, chek skrinshotini RASM (photo) sifatida yuboring.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("driverpaid_"))
def cb_driver_paid(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["driver_id"] != uid or row["driver_paid"]:
        bot.answer_callback_query(call.id, "Bu tolov allaqachon tasdiqlangan yoki notogri.", show_alert=True)
        return
    user_state[uid] = {"step": "driver_receipt", "data": {"cargo_id": cargo_id}}
    bot.answer_callback_query(call.id, "Chek rasmini yuboring.")
    safe_send(uid, "Iltimos, tolov chekini (skrinshotini) RASM korinishida yuboring:")


@bot.callback_query_handler(func=lambda c: c.data.startswith("reminddriver_"))
def cb_driver_remind(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["driver_id"] != uid or row["driver_paid"]:
        bot.answer_callback_query(call.id, "Bu tolov allaqachon tasdiqlangan yoki notogri.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "Adminga eslatma yuborildi.")
    notify_admin_driver_payment(row)


@bot.message_handler(content_types=["photo"], func=lambda m: step_is(m, "driver_receipt"))
def driver_receipt_photo(message):
    uid = message.from_user.id
    cargo_id = user_state[uid]["data"]["cargo_id"]
    cancel_state(uid)
    row = get_cargo(cargo_id)
    if not row or row["driver_id"] != uid or row["driver_paid"]:
        safe_send(uid, "Bu amalni bajarib bolmaydi.")
        return
    photo_id = message.photo[-1].file_id
    update_cargo(cargo_id, driver_pay_req=1, driver_receipt=photo_id)
    row = get_cargo(cargo_id)
    safe_send(
        uid,
        "Rahmat! Chek qabul qilindi, admin tekshirib tasdiqlaydi. "
        "Kechiksa, pastdagi 'Adminga qayta eslatish' tugmasini bosing.",
        reply_markup=driver_pay_keyboard(row),
    )
    notify_admin_driver_payment(row)


@bot.message_handler(func=lambda m: step_is(m, "driver_receipt"))
def driver_receipt_wrong_type(message):
    safe_send(message.from_user.id, "Iltimos, chek skrinshotini RASM (photo) sifatida yuboring.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("confirmowner_"))
def cb_confirm_owner(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Bu tugma faqat admin uchun.", show_alert=True)
        return
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row:
        bot.answer_callback_query(call.id, "Topilmadi (baza yangilangan bolishi mumkin).", show_alert=True)
        return
    if row["owner_paid"]:
        bot.answer_callback_query(call.id, "Bu tolov allaqachon tasdiqlangan.", show_alert=True)
        return
    update_cargo(cargo_id, owner_paid=1)
    add_admin_balance(row["owner_fee"])
    bot.answer_callback_query(call.id, "Tasdiqlandi!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)
    safe_send(row["owner_id"], "Tolovingiz tasdiqlandi! Endi yangi yuk qoʻsha olasiz.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("confirmdriver_"))
def cb_confirm_driver(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Bu tugma faqat admin uchun.", show_alert=True)
        return
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row:
        bot.answer_callback_query(call.id, "Topilmadi (baza yangilangan bolishi mumkin).", show_alert=True)
        return
    if row["driver_paid"]:
        bot.answer_callback_query(call.id, "Bu tolov allaqachon tasdiqlangan.", show_alert=True)
        return
    update_cargo(cargo_id, driver_paid=1)
    add_admin_balance(row["driver_fee"])
    bot.answer_callback_query(call.id, "Tasdiqlandi!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)
    safe_send(row["driver_id"], "Tolovingiz tasdiqlandi! Endi yangi yuk olishingiz mumkin.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("rate_"))
def cb_rate_driver(call):
    uid = call.from_user.id
    parts = call.data.split("_")
    cargo_id = int(parts[1])
    stars = int(parts[2])
    row = get_cargo(cargo_id)
    if not row or row["owner_id"] != uid or row["rated"]:
        bot.answer_callback_query(call.id, "Bu yukka allaqachon baho berilgan.", show_alert=True)
        return
    driver = get_user(row["driver_id"])
    with db_lock:
        conn = db()
        conn.execute(
            "UPDATE users SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE user_id=?",
            (stars, row["driver_id"]),
        )
        conn.commit()
        conn.close()
    update_cargo(cargo_id, rated=1)
    bot.answer_callback_query(call.id, "Rahmat, bahoyingiz saqlandi!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)
    safe_send(row["driver_id"], "Sizga yangi baho qoyildi: " + str(stars) + " ★")


# =========================== MENING YUKLARIM ===========================

@bot.message_handler(func=lambda m: m.text == "Mening yuklarim")
def my_cargos(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u:
        return
    if u["role"] == "owner":
        rows = owner_cargos(uid)
    else:
        rows = driver_cargos(uid)
    if not rows:
        safe_send(uid, "Hozircha yuklaringiz yoq.")
        return
    for row in rows[:15]:
        safe_send(uid, cargo_card_text(row))
    # Tolanmagan xizmat haqi bolsa - tolov tugmasini qayta korsatamiz
    if u["role"] == "owner":
        pend = unpaid_owner_cargo(uid)
        if pend:
            send_owner_pay_prompt(uid, pend)
    elif u["role"] == "driver":
        pend = unpaid_driver_cargo(uid)
        if pend:
            send_driver_pay_prompt(uid, pend)


# =========================== YORDAM ===========================

@bot.message_handler(func=lambda m: m.text == "Yordam")
def help_start(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "help_msg", "data": {}}
    safe_send(uid, "Muammoingizni yozing, men adminga yetkazaman:")


@bot.message_handler(func=lambda m: step_is(m, "help_msg"))
def help_forward(message):
    uid = message.from_user.id
    cancel_state(uid)
    u = get_user(uid)
    name = u["full_name"] if u else (getattr(message.from_user, "first_name", None) or "Nomalum")
    phone = u["phone"] if u else "-"
    try:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Javob yozish", callback_data="replyto_" + str(uid)))
        sent = bot.send_message(
            ADMIN_ID,
            "Yordam sorovi\n" + name + "\n" + phone + "\nID: " + str(uid) + "\n\n" + message.text,
            reply_markup=kb,
        )
        save_help_map(sent.message_id, uid)
        safe_send(uid, "Xabaringiz adminga yuborildi, tez orada javob beriladi.")
    except Exception as e:
        print("Adminga yuborishda xato:", e)
        safe_send(uid, "Xabar yuborishda xatolik yuz berdi, birozdan song qayta urinib koring.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("replyto_"))
def cb_reply_to(call):
    if not is_admin(call.from_user.id):
        return
    target_uid = int(call.data.split("_")[1])
    user_state[ADMIN_ID] = {"step": "admin_replying", "data": {"target": target_uid}}
    bot.answer_callback_query(call.id)
    safe_send(ADMIN_ID, "Javobingizni yozing (keyingi xabaringiz avtomatik foydalanuvchiga yuboriladi):")


@bot.message_handler(func=lambda m: step_is(m, "admin_replying"))
def admin_reply_via_state(message):
    uid = message.from_user.id
    target_uid = user_state[uid]["data"]["target"]
    cancel_state(uid)
    safe_send(target_uid, "Admin javobi:\n" + message.text)
    safe_send(uid, "Javob yuborildi.")


@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.reply_to_message is not None)
def admin_reply(message):
    target_uid = get_help_user(message.reply_to_message.message_id)
    if target_uid:
        safe_send(target_uid, "Admin javobi:\n" + message.text)
        safe_send(ADMIN_ID, "Javob yuborildi.")


# =========================== ADMIN PANELI ===========================

@bot.message_handler(commands=["myid"])
def cmd_myid(message):
    uid = message.from_user.id
    if is_admin(uid):
        safe_send(uid, "Sizning ID: " + str(uid) + "\nHolat: ADMIN (togri sozlangan)")
    else:
        safe_send(
            uid,
            "Sizning Telegram ID raqamingiz: " + str(uid) + "\n"
            "Holat: admin emas.\n\n"
            "Agar siz botning egasi bolsangiz, shu raqamni serverdagi ADMIN_ID ozgaruvchisiga yozing.",
        )


@bot.message_handler(commands=["kutilayotgan"])
def cmd_pending(message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    with db_lock:
        conn = db()
        unverified = conn.execute(
            "SELECT * FROM users WHERE role='driver' AND verified=0 AND verify_photo IS NOT NULL ORDER BY user_id"
        ).fetchall()
        owner_rows = conn.execute(
            "SELECT * FROM cargos WHERE owner_pay_req=1 AND owner_paid=0 AND owner_fee>0 ORDER BY id"
        ).fetchall()
        driver_rows = conn.execute(
            "SELECT * FROM cargos WHERE driver_pay_req=1 AND driver_paid=0 AND driver_fee>0 ORDER BY id"
        ).fetchall()
        conn.close()
    if not unverified and not owner_rows and not driver_rows:
        safe_send(uid, "Hech narsa tasdiqlashni kutmayapti. Hammasi joyida!")
        return
    if unverified:
        safe_send(uid, "=== Tasdiqlanmagan furachilar (" + str(len(unverified)) + ") ===")
    for u in unverified:
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("Tasdiqlash", callback_data="verify_" + str(u["user_id"])),
            types.InlineKeyboardButton("Rad etish", callback_data="reject_" + str(u["user_id"])),
        )
        try:
            bot.send_photo(
                uid,
                u["verify_photo"],
                caption=(
                    "Furachi: " + (u["full_name"] or "") + "\n" + (u["phone"] or "") + "\n"
                    + (u["car_brand"] or "") + " " + (u["car_model"] or "") + "\n"
                    + "Davlat raqami: " + (u["car_plate"] or "") + "\nID: " + str(u["user_id"])
                ),
                reply_markup=kb,
            )
        except Exception as e:
            print("Tasdiqlanmagan furachini qayta korsatishda xato:", e)
    if owner_rows or driver_rows:
        safe_send(uid, "=== Tolanmagan xizmat haqlari ===")
    for row in owner_rows:
        notify_admin_owner_payment(row)
    for row in driver_rows:
        notify_admin_driver_payment(row)


@bot.message_handler(commands=["admin"])
def admin_panel(message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    with db_lock:
        conn = db()
        drivers = conn.execute("SELECT COUNT(*) c FROM users WHERE role='driver'").fetchone()["c"]
        unverified = conn.execute("SELECT COUNT(*) c FROM users WHERE role='driver' AND verified=0").fetchone()["c"]
        owners = conn.execute("SELECT COUNT(*) c FROM users WHERE role='owner'").fetchone()["c"]
        cargos_total = conn.execute("SELECT COUNT(*) c FROM cargos").fetchone()["c"]
        cargos_open = conn.execute("SELECT COUNT(*) c FROM cargos WHERE status='open'").fetchone()["c"]
        cargos_delivered = conn.execute("SELECT COUNT(*) c FROM cargos WHERE status='delivered'").fetchone()["c"]
        pending_owner_pay = conn.execute(
            "SELECT COUNT(*) c FROM cargos WHERE owner_pay_req=1 AND owner_paid=0"
        ).fetchone()["c"]
        pending_driver_pay = conn.execute(
            "SELECT COUNT(*) c FROM cargos WHERE driver_pay_req=1 AND driver_paid=0"
        ).fetchone()["c"]
        conn.close()
    text = (
        "Admin panel\n\n"
        "Furachilar: " + str(drivers) + " (tasdiqlanmagan: " + str(unverified) + ")\n"
        "Yuk beruvchilar: " + str(owners) + "\n"
        "Jami yuklar: " + str(cargos_total) + " (ochiq: " + str(cargos_open) + ", yetkazilgan: " + str(cargos_delivered) + ")\n"
        "Yigilgan komissiya: " + format(get_admin_balance(), ",.0f") + " som\n"
        "Tasdiqlashni kutayotgan tolovlar: " + str(pending_owner_pay + pending_driver_pay) + "\n\n"
        "'Tasdiqlanmaganlar' tugmasini bosing - barcha kutayotgan "
        "furachi tasdiqlari va tolovlar qayta chiqadi."
    )
    safe_send(uid, text)


@bot.message_handler(commands=["balans_qoshish"])
def admin_add_balance(message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    try:
        parts = message.text.split()
        target_id = int(parts[1])
        amount = float(parts[2])
    except Exception:
        safe_send(uid, "Format: /balans_qoshish USER_ID SUMMA")
        return
    change_balance(target_id, amount)
    safe_send(uid, format(amount, ",.0f") + " som " + str(target_id) + " ga qoshildi.")
    safe_send(target_id, "Balansingizga " + format(amount, ",.0f") + " som qoshildi.")


# =========================== BARCHA BOSHQA XABARLAR (fallback) ===========================

@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not u or not u["role"]:
        return cmd_start(message)
    if user_state.get(uid, {}).get("step") is None:
        safe_send(uid, "Buyruqni tugmalar orqali tanlang.", reply_markup=main_menu(u["role"]))


# =========================== ISHGA TUSHIRISH ===========================

def run_forever():
    init_db()
    print("Baza tayyor. Bot ishga tushdi...")
    try:
        bot.send_message(ADMIN_ID, "Bot ishga tushdi. Agar bu xabarni koryapsiz - ADMIN_ID togri sozlangan.\n/admin - panel\n/kutilayotgan - tasdiq kutayotgan tolovlar")
    except Exception as e:
        print("ADMIN_ID ga xabar yuborib bolmadi (ADMIN_ID notogri yoki admin botga /start bosmagan):", e)

    # Render.com kabi platformalar "Web Service" uchun ochiq port talab qiladi.
    # Bot esa polling orqali ishlaydi (port ochmaydi), shuning uchun shu yerda
    # juda kichik bir "soxta" HTTP server ishga tushiramiz - u faqat platformaga
    # "xizmat ishlayapti" deb ko'rsatish uchun kerak, botning ishiga aloqasi yo'q.
    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _PingHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Bot ishlamoqda")

            def log_message(self, format, *args):
                pass  # konsolni chalkashtirmaslik uchun HTTP loglarni ochirib qoyamiz

        port = int(os.getenv("PORT", "8080"))

        def _run_http():
            server = HTTPServer(("0.0.0.0", port), _PingHandler)
            server.serve_forever()

        http_thread = threading.Thread(target=_run_http, daemon=True)
        http_thread.start()
        print("Yordamchi HTTP server " + str(port) + "-portda ishga tushdi.")
    except Exception as e:
        print("HTTP server ishga tushmadi (bu Pydroidda normal holat):", e)

    while True:
        try:
            bot.infinity_polling(timeout=20, long_polling_timeout=20, skip_pending=True)
        except Exception:
            print("Kutilmagan xatolik, 5 soniyadan song qayta urinilmoqda:")
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    run_forever()
