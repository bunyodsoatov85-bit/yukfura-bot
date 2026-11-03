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
import html
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
            c.execute("INSERT INTO settings(key, value) VALUES ('admin_withdrawn', 0) ON CONFLICT (key) DO NOTHING")
        else:
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('admin_balance', 0)")
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES ('admin_withdrawn', 0)")
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
            ("consent_at", "TEXT"), ("lang", "TEXT"), ("blocked", "INTEGER DEFAULT 0"),
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

CARD_DIGITS = re.sub(r"\D", "", ADMIN_CARD)

user_state = {}
lang_cache = {}
blocked_ids = set()
state_lock = threading.Lock()


def esc(x):
    """Foydalanuvchi kiritgan matnni HTML xabarga xavfsiz qoyish."""
    return html.escape(str(x if x is not None else ""), quote=False)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def ago(ts):
    """'2026-11-01 20:57:11.123' -> '5 daq. oldin' (admin uchun, ozbekcha)."""
    try:
        t = datetime.datetime.fromisoformat(str(ts))
        sec = int((datetime.datetime.now() - t).total_seconds())
    except Exception:
        return "noma'lum"
    if sec < 60:
        return "hozirgina"
    if sec < 3600:
        return str(sec // 60) + " daq. oldin"
    if sec < 86400:
        return str(sec // 3600) + " soat oldin"
    return str(sec // 86400) + " kun oldin"


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


def money(x):
    return format(x or 0, ",.0f").replace(",", " ")


# =========================== TILLAR (O'ZBEK / RUS) ===========================
# Har bir matn: (ozbekcha, ruscha). Tugma matnlari avvalgi versiya bilan bir xil
# qoldirilgan - eski klaviaturalar ham ishlayveradi.

TXT = {
    # ---- tugmalar ----
    "b_open": ("Ochiq yuklar", "Открытые грузы"),
    "b_near": ("Yaqin yuklarni topish", "Найти ближайшие грузы"),
    "b_my": ("Mening yuklarim", "Мои грузы"),
    "b_bal": ("Balansim", "Мой баланс"),
    "b_prof": ("Profilim", "Мой профиль"),
    "b_help": ("Yordam", "Помощь"),
    "b_add": ("Yuk qoshish", "Добавить груз"),
    "b_lang": ("Til / Язык", "Til / Язык"),
    "b_sendloc": ("Joylashuvni yuborish", "Отправить геопозицию"),
    "b_sendphone": ("Raqamni yuborish", "Отправить номер"),
    "b_role_driver": ("Men furachiman", "Я водитель"),
    "b_role_owner": ("Men yuk beruvchiman", "Я грузоотправитель"),
    "b_paid": ("Toladim", "Я оплатил(а)"),
    "b_remind": ("Adminga qayta eslatish", "Напомнить админу"),
    "b_take": ("Olish", "Взять"),
    "b_deliv": ("Yetkazib berdim", "Груз доставлен"),
    "b_copy": ("Karta raqamini nusxalash", "Скопировать номер карты"),
    "b_showcard": ("Karta raqamini ko'rsatish", "Показать номер карты"),
    "b_where": ("Furachi qayerda?", "Где водитель?"),
    "b_yes": ("Roziman", "Согласен(на)"),
    "b_no": ("Rozi emasman", "Не согласен(на)"),
    "b_reply": ("Javob yozish", "Ответить"),
    # ---- til / start ----
    "lang_prompt": ("Tilni tanlang / Выберите язык:", "Tilni tanlang / Выберите язык:"),
    "lang_set": ("Til o'zgartirildi: o'zbekcha.", "Язык изменён: русский."),
    "welcome_new": ("Yuk-Fura botiga xush kelibsiz!\n\nSiz kimsiz?",
                    "Добро пожаловать в Yuk-Fura бот!\n\nКто вы?"),
    "welcome_back": ("Xush kelibsiz! Asosiy menyu:", "Добро пожаловать! Главное меню:"),
    "wait_verify": ("Hujjatlaringiz hali admin tomonidan tasdiqlanmagan. Biroz kuting.",
                    "Ваши документы ещё не подтверждены администратором. Немного подождите."),
    "blocked": ("Kechirasiz, sizning hisobingiz bloklangan. Savollar bo'lsa admin bilan bog'laning.",
                "Извините, ваш аккаунт заблокирован. По вопросам свяжитесь с администратором."),
    "use_buttons": ("Buyruqni tugmalar orqali tanlang.", "Выберите действие кнопками."),
    # ---- ro'yxatdan o'tish ----
    "ask_name": ("Ism va familiyangizni kiriting:", "Введите имя и фамилию:"),
    "ask_phone": ("Telefon raqamingizni yuboring:", "Отправьте номер телефона:"),
    "ask_brand": ("Fura rusmini kiriting (masalan: Isuzu, Kamaz, MAN, Howo):",
                  "Введите марку грузовика (например: Isuzu, Kamaz, MAN, Howo):"),
    "ask_model": ("Fura modelini kiriting:", "Введите модель грузовика:"),
    "ask_plate": ("Fura davlat raqamini kiriting (masalan: 01A123BC):",
                  "Введите госномер грузовика (например: 01A123BC):"),
    "consent_text": (
        "Oxirgi qadam: shaxsingizni tasdiqlash.\n\n"
        "Shaxsiy ma'lumotlarni qayta ishlashga rozilik:\n"
        "- Maqsad: faqat haydovchi shaxsini tekshirish.\n"
        "- Guvohnoma (yoki pasport) rasmini faqat bot admini ko'radi, boshqa foydalanuvchilarga "
        "ko'rsatilmaydi va uchinchi shaxslarga berilmaydi.\n"
        "- Tekshiruv tugagach (tasdiqlash yoki rad etishdan keyin) rasmga havola botning bazasidan o'chiriladi.\n"
        "- Rozilikni istalgan vaqtda admin orqali qaytarib olishingiz mumkin.\n\n"
        "Rozimisiz?",
        "Последний шаг: подтверждение личности.\n\n"
        "Согласие на обработку персональных данных:\n"
        "- Цель: только проверка личности водителя.\n"
        "- Фото водительских прав (или паспорта) видит только администратор бота; оно не показывается "
        "другим пользователям и не передаётся третьим лицам.\n"
        "- После проверки (подтверждения или отказа) ссылка на фото удаляется из базы бота.\n"
        "- Вы можете в любой момент отозвать согласие через администратора.\n\n"
        "Вы согласны?"),
    "consent_wait": ("Iltimos, yuqoridagi 'Roziman' yoki 'Rozi emasman' tugmasini bosing.",
                     "Пожалуйста, нажмите кнопку «Согласен(на)» или «Не согласен(на)» выше."),
    "consent_stale": ("Bu so'rov eskirgan. /start bosing.", "Запрос устарел. Нажмите /start."),
    "consent_no_msg": (
        "Tushunarli. Rozilik bermasangiz, haydovchi sifatida tasdiqlanish imkoni yo'q. "
        "Fikringiz o'zgarsa, /start bosib qaytadan boshlang. Hech qanday ma'lumotingiz saqlanmadi.",
        "Понятно. Без согласия подтвердить статус водителя невозможно. Если передумаете — "
        "нажмите /start и начните заново. Никакие ваши данные не сохранены."),
    "ask_photo": ("Rahmat! Endi haydovchilik guvohnomangiz (yoki pasportingiz) rasmini yuboring.\n"
                  "Admin tekshirgach, yuk olishingiz mumkin bo'ladi.",
                  "Спасибо! Теперь отправьте фото водительских прав (или паспорта).\n"
                  "После проверки администратором вы сможете брать грузы."),
    "photo_wrong": ("Iltimos, hujjat rasmini rasm (photo) sifatida yuboring.",
                    "Пожалуйста, отправьте документ как фото (photo)."),
    "reg_done_driver": ("Ro'yxatdan o'tdingiz! Hujjatlaringiz admin tomonidan tekshirilmoqda, biroz kuting. "
                        "Tasdiqlangach sizga xabar beramiz.",
                        "Вы зарегистрированы! Документы проверяет администратор, немного подождите. "
                        "Мы сообщим после подтверждения."),
    "reg_done_owner": ("Ro'yxatdan muvaffaqiyatli o'tdingiz!", "Регистрация прошла успешно!"),
    "verified_msg": ("Hujjatlaringiz tasdiqlandi! Endi yuklarni ko'rish va olish mumkin.",
                     "Ваши документы подтверждены! Теперь вы можете смотреть и брать грузы."),
    "rejected_msg": ("Afsuski hujjatlaringiz tasdiqlanmadi. Iltimos, aniqroq rasm bilan /start orqali qaytadan "
                     "ro'yxatdan o'ting.",
                     "К сожалению, документы не подтверждены. Пожалуйста, зарегистрируйтесь заново через /start "
                     "с более чётким фото."),
    # ---- profil / balans ----
    "prof_driver": ("{name}\n{phone}\n{car}\nDavlat raqami: {plate}\nHolat: {status}\nReyting: {rating}",
                    "{name}\n{phone}\n{car}\nГосномер: {plate}\nСтатус: {status}\nРейтинг: {rating}"),
    "prof_owner": ("{name}\n{phone}", "{name}\n{phone}"),
    "st_verified": ("Tasdiqlangan", "Подтверждён"),
    "st_unverified": ("Tasdiqlanmagan", "Не подтверждён"),
    "no_rating": ("hali baho yo'q", "оценок пока нет"),
    "rating_fmt": ("{avg}/5 ({n} baho)", "{avg}/5 (оценок: {n})"),
    "bal_driver": (
        "Sizning balansingiz: {bal} so'm\n\n"
        "Yetkazilgan yuklar: {n} ta\nJami yuk summasi: {gross} so'm\n"
        "Xizmat haqi (to'langan va to'lanishi kerak): {fees} so'm\n"
        "Sof daromad: {net} so'm",
        "Ваш баланс: {bal} сум\n\n"
        "Доставлено грузов: {n}\nОбщая сумма грузов: {gross} сум\n"
        "Комиссия сервиса (оплаченная и к оплате): {fees} сум\n"
        "Чистый доход: {net} сум"),
    "bal_owner": (
        "Sizning balansingiz: {bal} so'm\n\n"
        "E'lon qilingan yuklar: {n} ta\nXizmat haqi (to'langan va to'lanishi kerak): {fees} so'm",
        "Ваш баланс: {bal} сум\n\n"
        "Размещено грузов: {n}\nКомиссия сервиса (оплаченная и к оплате): {fees} сум"),
    # ---- yuk qo'shish ----
    "pay_first_owner": ("Yangi yuk qo'shishdan oldin xizmat haqini to'lang:",
                        "Перед добавлением нового груза оплатите комиссию сервиса:"),
    "ask_ctype": ("Yuk turini kiriting (masalan: qurilish materiali, oziq-ovqat):",
                  "Введите тип груза (например: стройматериалы, продукты):"),
    "ask_cweight": ("Yuk massasini kiriting (masalan: 5 tonna):", "Введите вес груза (например: 5 тонн):"),
    "ask_cfrom": ("Qayerdan jo'natiladi? (shahar/tuman nomi):", "Откуда отправляется? (город/район):"),
    "ask_cto": ("Qayerga yetkazib berish kerak?", "Куда нужно доставить?"),
    "ask_cfromloc": ("Yukni olib ketish joyi (lokatsiya) ni yuboring:",
                     "Отправьте геопозицию места погрузки:"),
    "ask_ctoloc": (
        "Endi yukni TUSHIRISH (yetkazish) manzilini belgilang.\n\n"
        "DIQQAT: pastdagi tugma sizning HOZIRGI turgan joyingizni yuboradi - "
        "agar yetkazish manzili boshqa joyda bo'lsa, undan foydalanmang!\n\n"
        "Boshqa manzilni belgilash uchun:\n"
        "1) Xabar yozish maydoni yonidagi qog'oz qisqich (attach) belgisini bosing\n"
        "2) \"Location\" ni tanlang\n"
        "3) Xaritada kerakli nuqtani barmoq bilan bosib turing (uzoq bosish)\n"
        "4) Chiqqan \"Send Selected Location\" tugmasini bosing",
        "Теперь укажите место РАЗГРУЗКИ (доставки).\n\n"
        "ВНИМАНИЕ: кнопка ниже отправляет ваше ТЕКУЩЕЕ местоположение — "
        "если место доставки в другом месте, не используйте её!\n\n"
        "Чтобы указать другую точку:\n"
        "1) Нажмите значок скрепки (вложение) рядом с полем сообщения\n"
        "2) Выберите «Геопозиция» (Location)\n"
        "3) Долго удерживайте палец на нужной точке карты\n"
        "4) Нажмите «Send Selected Location»"),
    "ask_price": ("Taxminiy masofa: ~{dist} km.\n\nYuk uchun qancha to'laysiz? (masalan: 500000 yoki 500 ming):",
                  "Примерное расстояние: ~{dist} км.\n\nСколько вы платите за груз? (например: 500000 или 500 тыс):"),
    "price_bad": ("Tushunmadim. Masalan: 1500000 yoki 1,5 mln yoki 500 ming deb yozing.",
                  "Не понял. Напишите, например: 1500000 или 1,5 млн или 500 тыс."),
    "cargo_posted": ("Yuk e'lon qilindi! Yuk raqami: #{id}", "Груз размещён! Номер груза: #{id}"),
    # ---- ochiq yuklar ----
    "only_verified": ("Bu funksiya faqat tasdiqlangan furachilar uchun.",
                      "Эта функция только для подтверждённых водителей."),
    "no_open": ("Hozircha ochiq yuklar yo'q.", "Открытых грузов пока нет."),
    "send_loc_near": ("Joylashuvingizni yuboring, sizga eng yaqin yuklarni topib beraman:",
                      "Отправьте геопозицию — я найду ближайшие грузы:"),
    "no_near": ("Yaqin atrofda yuk topilmadi.", "Рядом грузов не найдено."),
    "near_title": ("Sizga eng yaqin yuklar:", "Ближайшие к вам грузы:"),
    "dist_line": ("\nMasofa: ~{d} km", "\nРасстояние: ~{d} км"),
    "live_saved": ("Jonli joylashuv qabul qilindi. Yo'lda ketayotganingizda u avtomatik yangilanib turadi.",
                   "Живая геопозиция принята. Во время поездки она будет обновляться автоматически."),
    # ---- yuk kartasi ----
    "card": (
        "Yuk #{id}\nTuri: {type}\nMassasi: {weight}\nQayerdan: {frm}\nQayerga: {to}\n"
        "Narxi: {price} so'm\nHolati: {status}",
        "Груз #{id}\nТип: {type}\nВес: {weight}\nОткуда: {frm}\nКуда: {to}\n"
        "Цена: {price} сум\nСтатус: {status}"),
    "card_dist": ("\nTaxminiy masofa: ~{d} km", "\nПримерное расстояние: ~{d} км"),
    "s_open": ("Ochiq", "Открыт"),
    "s_taken": ("Yo'lda", "В пути"),
    "s_delivered": ("Yetkazilgan", "Доставлен"),
    # ---- olish / yetkazish ----
    "take_gone": ("Bu yuk allaqachon band qilingan yoki mavjud emas.", "Этот груз уже занят или не существует."),
    "take_only_ver": ("Faqat tasdiqlangan furachilar yuk olishi mumkin.",
                      "Брать грузы могут только подтверждённые водители."),
    "take_pay_first": ("Avval to'lanmagan xizmat haqingizni to'lang.", "Сначала оплатите комиссию сервиса."),
    "take_ok": ("Yuk sizga biriktirildi!", "Груз закреплён за вами!"),
    "driver_took": (
        "Siz #{id}-yukni oldingiz.\nYuk beruvchi: {owner}, tel: {phone}\n"
        "Yetkazib bergach quyidagi tugmani bosing:",
        "Вы взяли груз #{id}.\nГрузоотправитель: {owner}, тел: {phone}\n"
        "После доставки нажмите кнопку ниже:"),
    "live_hint": (
        "Maslahat: yo'lda ketayotganda JONLI joylashuv (Live Location) yuboring - yuk beruvchi va admin yukingiz "
        "qayerda ekanini ko'ra oladi.\n"
        "Qanday: xabar yozish maydoni yonidagi qog'oz qisqich -> Joylashuv (Location) -> "
        "\"Jonli joylashuvni ulashish\" -> 8 soat.",
        "Совет: в пути отправьте ЖИВУЮ геопозицию (Live Location) — грузоотправитель и админ смогут видеть, "
        "где ваш груз.\n"
        "Как: скрепка рядом с полем сообщения -> Геопозиция (Location) -> "
        "«Транслировать геопозицию» -> 8 часов."),
    "owner_taken": (
        "#{id}-yukingizni furachi oldi va yo'lga chiqdi!\nFurachi: {driver}\nTelefon: {phone}\n"
        "Fura: {car}, davlat raqami: {plate}\nReyting: {rating}\n\n"
        "Xizmat haqi ({pct}%): {fee} so'm.\nQuyidagi kartaga o'tkazing:\n<code>{card}</code>\n"
        "O'tkazgach pastdagi tugmani bosing:",
        "Ваш груз #{id} взял водитель и выехал!\nВодитель: {driver}\nТелефон: {phone}\n"
        "Грузовик: {car}, госномер: {plate}\nРейтинг: {rating}\n\n"
        "Комиссия сервиса ({pct}%): {fee} сум.\nПереведите на карту:\n<code>{card}</code>\n"
        "После перевода нажмите кнопку ниже:"),
    "deliv_bad": ("Bu amalni bajarib bo'lmaydi.", "Это действие невозможно."),
    "deliv_ok": ("Yetkazib berish tasdiqlandi!", "Доставка подтверждена!"),
    "driver_delivered": (
        "#{id}-yuk yetkazib berildi deb belgilandi.\nXizmat haqi ({pct}%): {fee} so'm.\n"
        "Quyidagi kartaga o'tkazing:\n<code>{card}</code>\n"
        "O'tkazgach pastdagi tugmani bosing. To'lamaguningizcha yangi yuk ololmaysiz:",
        "Груз #{id} отмечен как доставленный.\nКомиссия сервиса ({pct}%): {fee} сум.\n"
        "Переведите на карту:\n<code>{card}</code>\n"
        "После перевода нажмите кнопку ниже. Пока не оплатите, новые грузы брать нельзя:"),
    "owner_delivered": ("#{id}-yukingiz yetkazib berildi. Furachi tasdiqladi.",
                        "Ваш груз #{id} доставлен. Водитель подтвердил."),
    "rate_ask": ("Furachiga baho bering:", "Оцените водителя:"),
    "rate_done": ("Rahmat, bahoyingiz saqlandi!", "Спасибо, ваша оценка сохранена!"),
    "rate_dup": ("Bu yukka allaqachon baho berilgan.", "Этот груз уже оценён."),
    "rate_got": ("Sizga yangi baho qo'yildi: {n} ★", "Вам поставили новую оценку: {n} ★"),
    # ---- to'lov ----
    "pay_fee_line": ("To'lanmagan xizmat haqi: Yuk #{id}, {fee} so'm.\n{status}",
                     "Неоплаченная комиссия: Груз #{id}, {fee} сум.\n{status}"),
    "pay_status_wait": ("To'lov qildim deb belgilagansiz, admin tasdiqlashini kutmoqda.",
                        "Вы отметили оплату, ожидается подтверждение администратора."),
    "pay_status_card": ("Quyidagi kartaga o'tkazing:\n<code>{card}</code>\nO'tkazgach pastdagi tugmani bosing.",
                        "Переведите на карту:\n<code>{card}</code>\nПосле перевода нажмите кнопку ниже."),
    "pay_dup": ("Bu to'lov allaqachon tasdiqlangan yoki noto'g'ri.", "Эта оплата уже подтверждена или неверна."),
    "pay_send_photo": ("Iltimos, to'lov chekini (skrinshotini) RASM ko'rinishida yuboring:",
                       "Пожалуйста, отправьте чек об оплате (скриншот) в виде ФОТО:"),
    "pay_send_photo_cb": ("Chek rasmini yuboring.", "Отправьте фото чека."),
    "pay_photo_wrong": ("Iltimos, chek skrinshotini RASM (photo) sifatida yuboring.",
                        "Пожалуйста, отправьте скриншот чека как ФОТО (photo)."),
    "pay_receipt_ok": ("Rahmat! Chek qabul qilindi, admin tekshirib tasdiqlaydi. "
                       "Kechiksa, pastdagi 'Adminga qayta eslatish' tugmasini bosing.",
                       "Спасибо! Чек принят, администратор проверит и подтвердит. "
                       "Если задержится — нажмите «Напомнить админу» ниже."),
    "pay_remind_ok": ("Adminga eslatma yuborildi.", "Напоминание админу отправлено."),
    "pay_bad_action": ("Bu amalni bajarib bo'lmaydi.", "Это действие невозможно."),
    "owner_pay_confirmed": ("To'lovingiz tasdiqlandi! Endi yangi yuk qo'sha olasiz.",
                            "Ваша оплата подтверждена! Теперь вы можете добавлять новые грузы."),
    "driver_pay_confirmed": ("To'lovingiz tasdiqlandi! Endi yangi yuk olishingiz mumkin.",
                             "Ваша оплата подтверждена! Теперь вы можете брать новые грузы."),
    "card_only": ("Karta raqami:\n<code>{card}</code>", "Номер карты:\n<code>{card}</code>"),
    # ---- mening yuklarim ----
    "no_my": ("Hozircha yuklaringiz yo'q.", "У вас пока нет грузов."),
    "where_none": ("Furachining joylashuvi hali ma'lum emas. U jonli joylashuv yuborsa, shu yerda ko'rinadi.",
                   "Местоположение водителя пока неизвестно. Когда он отправит живую геопозицию, она появится здесь."),
    "where_info": ("Furachi oxirgi marta {when} joylashuv yuborgan.",
                   "Водитель последний раз отправлял геопозицию: {when}."),
    "where_bad": ("Bu yuk uchun joylashuvni ko'rib bo'lmaydi.", "Для этого груза геопозиция недоступна."),
    # ---- yordam ----
    "help_ask": ("Muammoingizni yozing, men adminga yetkazaman:", "Опишите проблему, я передам её админу:"),
    "help_sent": ("Xabaringiz adminga yuborildi, tez orada javob beriladi.",
                  "Ваше сообщение отправлено админу, скоро ответят."),
    "help_fail": ("Xabar yuborishda xatolik yuz berdi, birozdan so'ng qayta urinib ko'ring.",
                  "Ошибка отправки, попробуйте ещё раз чуть позже."),
    "admin_says": ("Admin javobi:\n{text}", "Ответ администратора:\n{text}"),
}

MENU_KEYS = ("b_open", "b_near", "b_my", "b_bal", "b_prof", "b_help", "b_add", "b_lang")
MENU_TEXTS = set()
for _k in MENU_KEYS:
    MENU_TEXTS.update(TXT[_k])


def lang_of(uid):
    l = lang_cache.get(uid)
    if l:
        return l
    try:
        u = get_user(uid)
        if u and u["lang"] in ("uz", "ru"):
            lang_cache[uid] = u["lang"]
            return u["lang"]
    except Exception:
        pass
    return "uz"


def T(uid, key, **kw):
    pair = TXT[key]
    s = pair[1] if lang_of(uid) == "ru" else pair[0]
    return s.format(**kw) if kw else s


def is_btn(m, key):
    """Menyu tugmasi bosilganini tekshiradi (ikkala tilda). Admin uchun hech qachon True emas."""
    text = getattr(m, "text", None)
    if text is None or is_admin(m.from_user.id):
        return False
    return text in TXT[key]


def step_is(m, name):
    """Foydalanuvchi shu bosqichda bolsa True. Buyruqlar (/admin ...) va menyu tugmalari
    hech qachon bosqichga tushib qolmaydi."""
    text = getattr(m, "text", None)
    if text and (text.startswith("/") or text in MENU_TEXTS):
        return False
    return user_state.get(m.from_user.id, {}).get("step") == name


def cancel_state(user_id):
    user_state.pop(user_id, None)


def is_admin(user_id):
    return user_id == ADMIN_ID


def safe_send(chat_id, text, **kwargs):
    """Xato chiqsa ham bot to'xtamasligi uchun himoyalangan xabar yuborish."""
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        print("send_message xatosi:", e)
        # HTML belgilari tufayli xato bo'lishi mumkin - oddiy matn sifatida qayta urinamiz
        if "parse" in str(e).lower() or "entities" in str(e).lower():
            try:
                return bot.send_message(chat_id, text, parse_mode=None, **kwargs)
            except Exception as e2:
                print("send_message (oddiy matn) xatosi:", e2)
    return None


def safe_edit(call, text, kb=None):
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            print("edit_message_text xatosi:", e)


# =========================== KLAVIATURALAR ===========================

def main_menu(uid, role):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    B = lambda k: types.KeyboardButton(T(uid, k))
    if role == "driver":
        kb.add(B("b_open"), B("b_near"))
        kb.add(B("b_my"), B("b_bal"))
        kb.add(B("b_prof"), B("b_help"))
        kb.add(B("b_lang"))
    elif role == "owner":
        kb.add(B("b_add"), B("b_my"))
        kb.add(B("b_bal"), B("b_prof"))
        kb.add(B("b_help"), B("b_lang"))
    return kb


def lang_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("O'zbekcha", callback_data="lang_uz"),
        types.InlineKeyboardButton("Русский", callback_data="lang_ru"),
    )
    return kb


def role_keyboard(uid):
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(T(uid, "b_role_driver"), callback_data="role_driver"),
        types.InlineKeyboardButton(T(uid, "b_role_owner"), callback_data="role_owner"),
    )
    return kb


def phone_keyboard(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton(T(uid, "b_sendphone"), request_contact=True))
    return kb


def location_keyboard(uid):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton(T(uid, "b_sendloc"), request_location=True))
    return kb


def copy_card_row(uid):
    """Karta raqamini bir bosishda nusxalaydigan tugma (Telegram 'copy_text' tugmasi).
    Eski kutubxona versiyasida bu tugma bo'lmaydi - u holda raqamni alohida xabarda yuboruvchi tugma beriladi."""
    try:
        return types.InlineKeyboardButton(T(uid, "b_copy"), copy_text=types.CopyTextButton(text=CARD_DIGITS))
    except Exception:
        return types.InlineKeyboardButton(T(uid, "b_showcard"), callback_data="showcard")


def owner_pay_keyboard(cargo_row, uid):
    kb = types.InlineKeyboardMarkup()
    if cargo_row["owner_pay_req"]:
        kb.add(types.InlineKeyboardButton(T(uid, "b_remind"), callback_data="remindowner_" + str(cargo_row["id"])))
    else:
        kb.add(copy_card_row(uid))
        kb.add(types.InlineKeyboardButton(T(uid, "b_paid"), callback_data="ownerpaid_" + str(cargo_row["id"])))
    return kb


def driver_pay_keyboard(cargo_row, uid):
    kb = types.InlineKeyboardMarkup()
    if cargo_row["driver_pay_req"]:
        kb.add(types.InlineKeyboardButton(T(uid, "b_remind"), callback_data="reminddriver_" + str(cargo_row["id"])))
    else:
        kb.add(copy_card_row(uid))
        kb.add(types.InlineKeyboardButton(T(uid, "b_paid"), callback_data="driverpaid_" + str(cargo_row["id"])))
    return kb


# =========================== KARTALAR / MATNLAR ===========================

def driver_rating_text(u, uid):
    if not u or not u["rating_count"]:
        return T(uid, "no_rating")
    avg = u["rating_sum"] / u["rating_count"]
    return T(uid, "rating_fmt", avg=format(avg, ".1f"), n=u["rating_count"])


def status_label(uid, status):
    key = {"open": "s_open", "taken": "s_taken", "delivered": "s_delivered"}.get(status)
    return T(uid, key) if key else str(status)


def cargo_card_text(row, uid):
    text = T(
        uid, "card", id=row["id"], type=esc(row["cargo_type"]), weight=esc(row["weight"]),
        frm=esc(row["from_loc"]), to=esc(row["to_loc"]), price=money(row["price"]),
        status=status_label(uid, row["status"]),
    )
    try:
        if row["to_lat"] is not None and row["to_lon"] is not None and row["lat"] is not None:
            dist = haversine(row["lat"], row["lon"], row["to_lat"], row["to_lon"])
            text += T(uid, "card_dist", d=format(dist, ".1f"))
    except Exception:
        pass
    return text


def send_owner_pay_prompt(uid, cargo_row):
    if cargo_row["owner_pay_req"]:
        status = T(uid, "pay_status_wait")
    else:
        status = T(uid, "pay_status_card", card=ADMIN_CARD)
    safe_send(
        uid,
        T(uid, "pay_fee_line", id=cargo_row["id"], fee=money(cargo_row["owner_fee"]), status=status),
        reply_markup=owner_pay_keyboard(cargo_row, uid),
    )


def send_driver_pay_prompt(uid, cargo_row):
    if cargo_row["driver_pay_req"]:
        status = T(uid, "pay_status_wait")
    else:
        status = T(uid, "pay_status_card", card=ADMIN_CARD)
    safe_send(
        uid,
        T(uid, "pay_fee_line", id=cargo_row["id"], fee=money(cargo_row["driver_fee"]), status=status),
        reply_markup=driver_pay_keyboard(cargo_row, uid),
    )


def notify_admin_owner_payment(cargo_row):
    owner = get_user(cargo_row["owner_id"])
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Tasdiqlash", callback_data="confirmowner_" + str(cargo_row["id"])))
    caption = (
        "Yuk beruvchi to'lov qildi deb belgiladi.\n"
        "Yuk #" + str(cargo_row["id"]) + ", " + esc(owner["full_name"]) + ", tel: " + esc(owner["phone"]) + "\n"
        "Miqdor: " + money(cargo_row["owner_fee"]) + " so'm\n"
        "Kartangizga tushganini tekshirib, tasdiqlang:"
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
        "Furachi to'lov qildi deb belgiladi.\n"
        "Yuk #" + str(cargo_row["id"]) + ", " + esc(driver["full_name"]) + ", tel: " + esc(driver["phone"]) + "\n"
        "Miqdor: " + money(cargo_row["driver_fee"]) + " so'm\n"
        "Kartangizga tushganini tekshirib, tasdiqlang:"
    )
    if cargo_row["driver_receipt"]:
        try:
            bot.send_photo(ADMIN_ID, cargo_row["driver_receipt"], caption=caption, reply_markup=kb)
            return
        except Exception as e:
            print("Chek rasmini yuborishda xato:", e)
    safe_send(ADMIN_ID, caption, reply_markup=kb)


def notify_admin(text):
    """Admin uchun kichik xabarnoma (xato bo'lsa bot to'xtamaydi)."""
    safe_send(ADMIN_ID, text)


def is_complete(u):
    """Ro'yxatdan to'liq o'tgan foydalanuvchimi (rol tanlab, ism kiritib bo'lgan)."""
    return bool(u and u["role"] and u["full_name"])


TXT.update({
    "unblocked": ("Hisobingiz qayta faollashtirildi. /start ni bosing.",
                  "Ваш аккаунт снова активен. Нажмите /start."),
    "admin_msg": ("Admin xabari:\n{text}", "Сообщение от администратора:\n{text}"),
})


# =========================== BLOKLANGANLARNI USHLASH (eng birinchi) ===========================

def is_blocked(uid):
    return uid in blocked_ids and not is_admin(uid)


@bot.message_handler(func=lambda m: is_blocked(m.from_user.id),
                     content_types=["text", "photo", "location", "contact", "document", "video", "voice"])
def blocked_msg_guard(message):
    safe_send(message.from_user.id, T(message.from_user.id, "blocked"))


@bot.callback_query_handler(func=lambda c: is_blocked(c.from_user.id))
def blocked_cb_guard(call):
    try:
        bot.answer_callback_query(call.id, T(call.from_user.id, "blocked"), show_alert=True)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "showcard")
def cb_showcard(call):
    bot.answer_callback_query(call.id)
    safe_send(call.from_user.id, T(call.from_user.id, "card_only", card=CARD_DIGITS))


# =========================== ADMIN: MENYU ===========================

A_STATS = "📊 Statistika"
A_BAL = "💰 Balans"
A_PEND = "⏳ Tasdiqlanmaganlar"
A_MAP = "📍 Furachilar xaritasi"
A_USERS = "👥 Foydalanuvchilar"
A_CARGOS = "📦 Yuklar"
A_SEARCH = "🔎 Qidirish"
A_BC = "📢 Xabar yuborish"
A_ID = "🆔 Mening ID im"
A_OLD_USERMODE = "Foydalanuvchi rejimi"


def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton(A_STATS), types.KeyboardButton(A_BAL))
    kb.add(types.KeyboardButton(A_PEND), types.KeyboardButton(A_MAP))
    kb.add(types.KeyboardButton(A_USERS), types.KeyboardButton(A_CARGOS))
    kb.add(types.KeyboardButton(A_SEARCH), types.KeyboardButton(A_BC))
    kb.add(types.KeyboardButton(A_ID))
    return kb


def admin_btn(m, *names):
    return is_admin(m.from_user.id) and getattr(m, "text", None) in names


def get_setting(key):
    with db_lock:
        conn = db()
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        conn.close()
    return row["value"] if row else 0


def set_setting(key, value):
    with db_lock:
        conn = db()
        if USE_PG:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        else:
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))
        conn.commit()
        conn.close()


def q_all(sql, params=()):
    with db_lock:
        conn = db()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
    return rows


def q_one(sql, params=()):
    with db_lock:
        conn = db()
        row = conn.execute(sql, params).fetchone()
        conn.close()
    return row


def q_exec(sql, params=()):
    with db_lock:
        conn = db()
        conn.execute(sql, params)
        conn.commit()
        conn.close()


@bot.message_handler(commands=["start"], func=lambda m: is_admin(m.from_user.id))
def admin_start(message):
    cancel_state(message.from_user.id)
    safe_send(
        message.from_user.id,
        "Salom, Admin! Bu sizning shaxsiy boshqaruv paneli.\n"
        "Siz har doim admin sifatida ishlaysiz - foydalanuvchi rejimiga o'tib ketmaysiz.",
        reply_markup=admin_menu(),
    )


@bot.message_handler(func=lambda m: admin_btn(m, A_OLD_USERMODE))
def admin_old_usermode(message):
    cancel_state(message.from_user.id)
    safe_send(message.from_user.id, "Foydalanuvchi rejimi olib tashlandi. Siz doim adminsiz. Yangi menyu:",
              reply_markup=admin_menu())


@bot.message_handler(func=lambda m: admin_btn(m, A_STATS, "Statistika"))
def admin_btn_stats(message):
    cancel_state(message.from_user.id)
    admin_panel(message)


@bot.message_handler(func=lambda m: admin_btn(m, A_PEND, "Tasdiqlanmaganlar"))
def admin_btn_pending(message):
    cancel_state(message.from_user.id)
    cmd_pending(message)


@bot.message_handler(func=lambda m: admin_btn(m, A_ID, "Mening ID im"))
def admin_btn_myid(message):
    cancel_state(message.from_user.id)
    cmd_myid(message)


@bot.message_handler(func=lambda m: admin_btn(m, A_BAL))
def admin_btn_balance(message):
    cancel_state(message.from_user.id)
    text, kb = admin_balance_view()
    safe_send(message.from_user.id, text, reply_markup=kb)


@bot.message_handler(func=lambda m: admin_btn(m, A_MAP))
def admin_btn_map(message):
    cancel_state(message.from_user.id)
    text, kb = admin_drivers_map_view()
    safe_send(message.from_user.id, text, reply_markup=kb, disable_web_page_preview=True)


@bot.message_handler(func=lambda m: admin_btn(m, A_USERS))
def admin_btn_users(message):
    cancel_state(message.from_user.id)
    text, kb = admin_users_view("all", 0)
    safe_send(message.from_user.id, text, reply_markup=kb)


@bot.message_handler(func=lambda m: admin_btn(m, A_CARGOS))
def admin_btn_cargos(message):
    cancel_state(message.from_user.id)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Ochiq", callback_data="adm:cl:open:0"),
           types.InlineKeyboardButton("Yo'lda", callback_data="adm:cl:taken:0"))
    kb.add(types.InlineKeyboardButton("Yetkazilgan", callback_data="adm:cl:delivered:0"),
           types.InlineKeyboardButton("Hammasi", callback_data="adm:cl:all:0"))
    safe_send(message.from_user.id, "Qaysi yuklarni ko'rmoqchisiz?", reply_markup=kb)


@bot.message_handler(func=lambda m: admin_btn(m, A_SEARCH))
def admin_btn_search(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "admin_search", "data": {}}
    safe_send(uid, "Qidirish: ism, telefon raqam yoki Telegram ID sini yozing:")


@bot.message_handler(func=lambda m: admin_btn(m, A_BC))
def admin_btn_broadcast(message):
    cancel_state(message.from_user.id)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Hammaga", callback_data="adm:bc:all"))
    kb.add(types.InlineKeyboardButton("Furachilarga", callback_data="adm:bc:driver"),
           types.InlineKeyboardButton("Yuk beruvchilarga", callback_data="adm:bc:owner"))
    safe_send(message.from_user.id, "Xabar kimga yuborilsin?", reply_markup=kb)


# =========================== ADMIN: KO'RINISHLAR ===========================

def admin_balance_view():
    cur = get_admin_balance()
    withdrawn = get_setting("admin_withdrawn")
    pend = q_one(
        "SELECT (SELECT COALESCE(SUM(owner_fee),0) FROM cargos WHERE owner_pay_req=1 AND owner_paid=0 AND owner_fee>0) AS a, "
        "(SELECT COALESCE(SUM(driver_fee),0) FROM cargos WHERE driver_pay_req=1 AND driver_paid=0 AND driver_fee>0) AS b"
    )
    unpaid = q_one(
        "SELECT (SELECT COALESCE(SUM(owner_fee),0) FROM cargos WHERE owner_paid=0 AND owner_fee>0) AS a, "
        "(SELECT COALESCE(SUM(driver_fee),0) FROM cargos WHERE driver_paid=0 AND driver_fee>0) AS b"
    )
    text = (
        "💰 BALANS (sof foyda)\n\n"
        "Joriy balans: " + money(cur) + " so'm\n"
        "(oxirgi nolga qaytarishdan beri tasdiqlangan komissiya)\n\n"
        "Avval nolga qaytarilgan (yechib olingan): " + money(withdrawn) + " so'm\n"
        "Jami topilgan (butun vaqt): " + money(cur + withdrawn) + " so'm\n\n"
        "Tasdiq kutayotgan to'lovlar: " + money(pend["a"] + pend["b"]) + " so'm\n"
        "Hali to'lanmagan komissiya: " + money(unpaid["a"] + unpaid["b"]) + " so'm\n\n"
        "Yangilandi: " + datetime.datetime.now().strftime("%H:%M:%S")
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔄 Balansni yangilash", callback_data="adm:bal"))
    kb.add(types.InlineKeyboardButton("♻️ Balansni nolga qaytarish", callback_data="adm:balz"))
    return text, kb


def admin_users_view(kind, offset):
    where = {
        "driver": "role='driver'",
        "owner": "role='owner'",
        "blocked": "blocked=1",
        "all": "role IS NOT NULL",
    }.get(kind, "role IS NOT NULL")
    total = q_one("SELECT COUNT(*) AS c FROM users WHERE " + where + " AND user_id<>?", (ADMIN_ID,))["c"]
    rows = q_all(
        "SELECT * FROM users WHERE " + where + " AND user_id<>? ORDER BY created_at DESC LIMIT 10 OFFSET ?",
        (ADMIN_ID, offset),
    )
    names = {"driver": "Furachilar", "owner": "Yuk beruvchilar", "blocked": "Bloklanganlar", "all": "Barcha foydalanuvchilar"}
    text = names.get(kind, "Foydalanuvchilar") + ": jami " + str(total)
    if total:
        text += " (" + str(offset + 1) + "-" + str(min(offset + 10, total)) + ")"
    text += "\nBiror foydalanuvchini bosing:"
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Furachilar", callback_data="adm:ul:driver:0"),
           types.InlineKeyboardButton("Yuk beruvchilar", callback_data="adm:ul:owner:0"))
    kb.add(types.InlineKeyboardButton("Bloklanganlar", callback_data="adm:ul:blocked:0"),
           types.InlineKeyboardButton("Hammasi", callback_data="adm:ul:all:0"))
    for u in rows:
        mark = "🚚" if u["role"] == "driver" else "📦"
        if u["blocked"]:
            mark = "⛔"
        label = mark + " " + (u["full_name"] or "?") + " " + (u["phone"] or "")
        kb.add(types.InlineKeyboardButton(label[:60], callback_data="adm:uc:" + str(u["user_id"])))
    nav = []
    if offset > 0:
        nav.append(types.InlineKeyboardButton("◀ Oldingi", callback_data="adm:ul:" + kind + ":" + str(max(0, offset - 10))))
    if offset + 10 < total:
        nav.append(types.InlineKeyboardButton("Keyingi ▶", callback_data="adm:ul:" + kind + ":" + str(offset + 10)))
    if nav:
        kb.add(*nav)
    return text, kb


def admin_user_card(u):
    n_cargo = q_one("SELECT COUNT(*) AS c FROM cargos WHERE owner_id=? OR driver_id=?", (u["user_id"], u["user_id"]))["c"]
    loc = q_one("SELECT updated_at FROM driver_locations WHERE user_id=?", (u["user_id"],))
    role = {"driver": "Furachi", "owner": "Yuk beruvchi"}.get(u["role"], str(u["role"]))
    lines = [
        role + ": " + esc(u["full_name"]),
        "Telefon: " + esc(u["phone"]),
        "Telegram ID: " + str(u["user_id"]),
        "Til: " + str(u["lang"] or "-"),
    ]
    if u["role"] == "driver":
        lines.append("Fura: " + esc(u["car_brand"]) + " " + esc(u["car_model"]) + ", " + esc(u["car_plate"]))
        lines.append("Holat: " + ("tasdiqlangan" if u["verified"] else "tasdiqlanmagan"))
        lines.append("Reyting: " + (format(u["rating_sum"] / u["rating_count"], ".1f") + "/5 (" + str(u["rating_count"]) + ")"
                                    if u["rating_count"] else "yo'q"))
        lines.append("Oxirgi joylashuv: " + (ago(loc["updated_at"]) if loc else "yo'q"))
    lines.append("Yuklar soni: " + str(n_cargo))
    lines.append("Balans: " + money(u["balance"]) + " so'm")
    lines.append("Bloklangan: " + ("HA" if u["blocked"] else "yo'q"))
    lines.append("Ro'yxatdan o'tgan: " + str(u["created_at"] or "-")[:16])
    if u["consent_at"]:
        lines.append("Rozilik berilgan: " + str(u["consent_at"])[:16])
    kb = types.InlineKeyboardMarkup()
    uid = str(u["user_id"])
    kb.add(types.InlineKeyboardButton("✅ Blokdan chiqarish" if u["blocked"] else "⛔ Bloklash",
                                      callback_data="adm:ub:" + uid))
    if u["role"] == "driver" and not u["verified"]:
        kb.add(types.InlineKeyboardButton("✅ Furachini tasdiqlash", callback_data="adm:uv:" + uid))
    row = [types.InlineKeyboardButton("📦 Yuklari", callback_data="adm:uy:" + uid)]
    if u["role"] == "driver":
        row.insert(0, types.InlineKeyboardButton("📍 Lokatsiyasi", callback_data="adm:loc:" + uid))
    kb.add(*row)
    kb.add(types.InlineKeyboardButton("✉ Xabar yozish", callback_data="adm:um:" + uid),
           types.InlineKeyboardButton("🗑 O'chirish", callback_data="adm:ud:" + uid))
    return "\n".join(lines), kb


def admin_cargo_text(row):
    owner = get_user(row["owner_id"])
    driver = get_user(row["driver_id"]) if row["driver_id"] else None

    def pay(paid, req, fee):
        if not fee:
            return "-"
        return "to'langan" if paid else ("tasdiq kutmoqda" if req else "to'lanmagan")

    text = (
        "Yuk #" + str(row["id"]) + " [" + {"open": "Ochiq", "taken": "Yo'lda", "delivered": "Yetkazilgan"}.get(row["status"], str(row["status"])) + "]\n"
        + esc(row["cargo_type"]) + ", " + esc(row["weight"]) + "\n"
        + esc(row["from_loc"]) + " -> " + esc(row["to_loc"]) + "\n"
        + "Narxi: " + money(row["price"]) + " so'm\n"
        + "Egasi: " + (esc(owner["full_name"]) + " " + esc(owner["phone"]) if owner else "?") + "\n"
    )
    if driver:
        text += "Furachi: " + esc(driver["full_name"]) + " " + esc(driver["phone"]) + "\n"
    text += (
        "Egasi komissiyasi: " + pay(row["owner_paid"], row["owner_pay_req"], row["owner_fee"]) + "\n"
        "Furachi komissiyasi: " + pay(row["driver_paid"], row["driver_pay_req"], row["driver_fee"])
    )
    return text


def admin_cargo_kb(row):
    kb = types.InlineKeyboardMarkup()
    cid = str(row["id"])
    if row["status"] == "taken":
        kb.add(types.InlineKeyboardButton("📍 Furachi lokatsiyasi", callback_data="adm:loc:" + str(row["driver_id"])))
        kb.add(types.InlineKeyboardButton("↩ Qayta ochish", callback_data="adm:cr:" + cid))
    kb.add(types.InlineKeyboardButton("🗑 O'chirish", callback_data="adm:cx:" + cid))
    return kb


def admin_drivers_map_view():
    rows = q_all(
        "SELECT u.user_id, u.full_name, u.phone, d.lat, d.lon, d.updated_at FROM driver_locations d "
        "JOIN users u ON u.user_id=d.user_id WHERE u.role='driver' ORDER BY d.updated_at DESC LIMIT 20"
    )
    total_drivers = q_one("SELECT COUNT(*) AS c FROM users WHERE role='driver'")["c"]
    if not rows:
        return ("Hali birorta furachi joylashuv yubormagan.\n"
                "Furachi 'Yaqin yuklarni topish' tugmasi orqali yoki yo'lda jonli joylashuv (Live Location) "
                "yuborsa, shu yerda ko'rinadi.", None)
    kb = types.InlineKeyboardMarkup()
    lines = ["Furachilar joylashuvi (" + str(len(rows)) + "/" + str(total_drivers) + " ta joylashuv ma'lum):\n"]
    for i, r in enumerate(rows, 1):
        job = q_one("SELECT id FROM cargos WHERE driver_id=? AND status='taken' ORDER BY id DESC LIMIT 1", (r["user_id"],))
        link = "https://maps.google.com/?q=" + str(r["lat"]) + "," + str(r["lon"])
        lines.append(
            str(i) + ". " + esc(r["full_name"]) + " " + esc(r["phone"]) + "\n   "
            + ago(r["updated_at"]) + (" | yo'lda: yuk #" + str(job["id"]) if job else " | bo'sh")
            + " | <a href=\"" + link + "\">xaritada</a>"
        )
        kb.add(types.InlineKeyboardButton("📍 " + (r["full_name"] or "?")[:30], callback_data="adm:loc:" + str(r["user_id"])))
    kb.add(types.InlineKeyboardButton("🔄 Yangilash", callback_data="adm:map"))
    return "\n".join(lines), kb


def admin_send_location(chat_id, driver_id):
    row = q_one("SELECT * FROM driver_locations WHERE user_id=?", (driver_id,))
    u = get_user(driver_id)
    name = esc(u["full_name"]) if u else str(driver_id)
    if not row:
        safe_send(chat_id, name + ": joylashuv hali yuborilmagan.")
        return
    try:
        bot.send_location(chat_id, row["lat"], row["lon"])
    except Exception as e:
        print("send_location xatosi:", e)
    link = "https://maps.google.com/?q=" + str(row["lat"]) + "," + str(row["lon"])
    safe_send(chat_id, name + " - oxirgi yangilanish: " + ago(row["updated_at"]) + "\n<a href=\"" + link + "\">Xaritada ochish</a>",
              disable_web_page_preview=True)


def admin_search_results(text):
    t = text.strip()
    if t.isdigit() and len(t) >= 6 and not t.startswith("+"):
        rows = q_all("SELECT * FROM users WHERE CAST(user_id AS TEXT)=? OR phone LIKE ? LIMIT 8", (t, "%" + t + "%"))
    else:
        like = "%" + t.lower() + "%"
        rows = q_all("SELECT * FROM users WHERE LOWER(full_name) LIKE ? OR phone LIKE ? LIMIT 8", (like, "%" + t + "%"))
    return rows


# =========================== ADMIN: TUGMALAR (adm:...) ===========================

@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:"))
def cb_admin(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Bu tugma faqat admin uchun.", show_alert=True)
        return
    p = call.data.split(":")
    act = p[1]
    chat = call.message.chat.id
    try:
        if act == "bal":
            text, kb = admin_balance_view()
            safe_edit(call, text, kb)
            bot.answer_callback_query(call.id, "Yangilandi")
        elif act == "balz":
            cur = get_admin_balance()
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("Ha, nolga qaytar", callback_data="adm:balzy"),
                   types.InlineKeyboardButton("Bekor qilish", callback_data="adm:bal"))
            safe_edit(call, "Joriy balans " + money(cur) + " so'm nolga qaytariladi (u 'yechib olingan' qatoriga "
                      "o'tkaziladi, jami hisob saqlanadi).\nDavom etamizmi?", kb)
            bot.answer_callback_query(call.id)
        elif act == "balzy":
            cur = get_admin_balance()
            if cur:
                set_setting("admin_withdrawn", get_setting("admin_withdrawn") + cur)
                set_setting("admin_balance", 0)
            text, kb = admin_balance_view()
            safe_edit(call, text, kb)
            bot.answer_callback_query(call.id, "Balans nolga qaytarildi")
        elif act == "map":
            text, kb = admin_drivers_map_view()
            try:
                bot.edit_message_text(text, chat, call.message.message_id, reply_markup=kb, disable_web_page_preview=True)
            except Exception as e:
                if "not modified" not in str(e).lower():
                    print("map edit xatosi:", e)
            bot.answer_callback_query(call.id, "Yangilandi")
        elif act == "ul":
            text, kb = admin_users_view(p[2], int(p[3]))
            safe_edit(call, text, kb)
            bot.answer_callback_query(call.id)
        elif act == "uc":
            u = get_user(int(p[2]))
            if not u:
                bot.answer_callback_query(call.id, "Foydalanuvchi topilmadi.", show_alert=True)
                return
            text, kb = admin_user_card(u)
            bot.answer_callback_query(call.id)
            safe_send(chat, text, reply_markup=kb)
        elif act == "ub":
            target = int(p[2])
            u = get_user(target)
            if not u:
                bot.answer_callback_query(call.id, "Topilmadi.", show_alert=True)
                return
            if u["blocked"]:
                q_exec("UPDATE users SET blocked=0 WHERE user_id=?", (target,))
                blocked_ids.discard(target)
                safe_send(target, T(target, "unblocked"))
                bot.answer_callback_query(call.id, "Blokdan chiqarildi")
            else:
                q_exec("UPDATE users SET blocked=1 WHERE user_id=?", (target,))
                blocked_ids.add(target)
                safe_send(target, T(target, "blocked"))
                bot.answer_callback_query(call.id, "Bloklandi")
            text, kb = admin_user_card(get_user(target))
            safe_edit(call, text, kb)
        elif act == "uv":
            target = int(p[2])
            save_user(target, verified=1, verify_photo=None)
            safe_send(target, T(target, "verified_msg"), reply_markup=main_menu(target, "driver"))
            bot.answer_callback_query(call.id, "Tasdiqlandi")
            text, kb = admin_user_card(get_user(target))
            safe_edit(call, text, kb)
        elif act == "loc":
            bot.answer_callback_query(call.id)
            admin_send_location(chat, int(p[2]))
        elif act == "uy":
            target = int(p[2])
            rows = q_all("SELECT * FROM cargos WHERE owner_id=? OR driver_id=? ORDER BY id DESC LIMIT 10", (target, target))
            bot.answer_callback_query(call.id)
            if not rows:
                safe_send(chat, "Bu foydalanuvchining yuklari yo'q.")
            for r in rows:
                safe_send(chat, admin_cargo_text(r), reply_markup=admin_cargo_kb(r))
        elif act == "um":
            target = int(p[2])
            user_state[ADMIN_ID] = {"step": "admin_replying", "data": {"target": target}}
            bot.answer_callback_query(call.id)
            safe_send(chat, "Xabaringizni yozing (keyingi xabaringiz shu foydalanuvchiga yuboriladi):")
        elif act == "ud":
            target = int(p[2])
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("Ha, o'chir", callback_data="adm:udy:" + str(target)),
                   types.InlineKeyboardButton("Yo'q", callback_data="adm:uc:" + str(target)))
            bot.answer_callback_query(call.id)
            safe_send(chat, "Foydalanuvchi " + str(target) + " ro'yxatdan butunlay o'chirilsinmi? "
                      "(Uning yuklari bazada qoladi, u /start orqali qaytadan ro'yxatdan o'tishi mumkin.)", reply_markup=kb)
        elif act == "udy":
            target = int(p[2])
            q_exec("DELETE FROM users WHERE user_id=?", (target,))
            q_exec("DELETE FROM driver_locations WHERE user_id=?", (target,))
            blocked_ids.discard(target)
            lang_cache.pop(target, None)
            user_state.pop(target, None)
            bot.answer_callback_query(call.id, "O'chirildi")
            safe_edit(call, "Foydalanuvchi " + str(target) + " o'chirildi.")
        elif act == "cl":
            status, offset = p[2], int(p[3])
            if status == "all":
                rows = q_all("SELECT * FROM cargos ORDER BY id DESC LIMIT 6 OFFSET ?", (offset,))
            else:
                rows = q_all("SELECT * FROM cargos WHERE status=? ORDER BY id DESC LIMIT 6 OFFSET ?", (status, offset))
            bot.answer_callback_query(call.id)
            if not rows:
                safe_send(chat, "Yuklar yo'q.")
                return
            for r in rows:
                safe_send(chat, admin_cargo_text(r), reply_markup=admin_cargo_kb(r))
            if len(rows) == 6:
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("Keyingilari ▶", callback_data="adm:cl:" + status + ":" + str(offset + 6)))
                safe_send(chat, "Yana yuklar bor:", reply_markup=kb)
        elif act == "cx":
            cid = int(p[2])
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("Ha, o'chir", callback_data="adm:cxy:" + str(cid)),
                   types.InlineKeyboardButton("Yo'q", callback_data="adm:cn:" + str(cid)))
            try:
                bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=kb)
            except Exception:
                pass
            bot.answer_callback_query(call.id, "Yuk #" + str(cid) + " o'chirilsinmi?")
        elif act == "cn":
            row = get_cargo(int(p[2]))
            if row:
                try:
                    bot.edit_message_reply_markup(chat, call.message.message_id, reply_markup=admin_cargo_kb(row))
                except Exception:
                    pass
            bot.answer_callback_query(call.id)
        elif act == "cxy":
            cid = int(p[2])
            row = get_cargo(cid)
            if row:
                q_exec("DELETE FROM cargos WHERE id=?", (cid,))
                if row["status"] != "delivered":
                    safe_send(row["owner_id"], "Admin #" + str(cid) + "-yukni bekor qildi.")
                    if row["driver_id"]:
                        safe_send(row["driver_id"], "Admin #" + str(cid) + "-yukni bekor qildi.")
            bot.answer_callback_query(call.id, "O'chirildi")
            safe_edit(call, "Yuk #" + str(cid) + " o'chirildi.")
        elif act == "cr":
            cid = int(p[2])
            row = get_cargo(cid)
            if not row or row["status"] != "taken":
                bot.answer_callback_query(call.id, "Bu yukni qayta ochib bo'lmaydi.", show_alert=True)
                return
            if row["owner_paid"]:
                bot.answer_callback_query(call.id, "Egasi komissiyani to'lab bo'lgan - qayta ochib bo'lmaydi.", show_alert=True)
                return
            old_driver = row["driver_id"]
            update_cargo(cid, status="open", driver_id=None, owner_fee=0, owner_pay_req=0, owner_receipt=None)
            if old_driver:
                safe_send(old_driver, "Admin #" + str(cid) + "-yukni sizdan qaytarib oldi.")
            safe_send(row["owner_id"], "#" + str(cid) + "-yukingiz yana ochiq holatga qaytarildi.")
            bot.answer_callback_query(call.id, "Qayta ochildi")
            safe_edit(call, "Yuk #" + str(cid) + " qayta ochildi (ochiq).")
        elif act == "bc":
            aud = p[2]
            user_state[ADMIN_ID] = {"step": "admin_bc", "data": {"aud": aud}}
            bot.answer_callback_query(call.id)
            safe_send(chat, "Yuboriladigan xabar matnini yozing:")
        elif act == "bcgo":
            st = user_state.get(ADMIN_ID, {})
            data = st.get("data", {})
            if st.get("step") != "admin_bc_confirm" or not data.get("text"):
                bot.answer_callback_query(call.id, "Xabar topilmadi, qaytadan boshlang.", show_alert=True)
                return
            cancel_state(ADMIN_ID)
            bot.answer_callback_query(call.id, "Yuborilmoqda...")
            threading.Thread(target=run_broadcast, args=(data["aud"], data["text"]), daemon=True).start()
        elif act == "bcno":
            cancel_state(ADMIN_ID)
            bot.answer_callback_query(call.id, "Bekor qilindi")
            safe_edit(call, "Xabar yuborish bekor qilindi.")
        else:
            bot.answer_callback_query(call.id)
    except Exception:
        traceback.print_exc()
        try:
            bot.answer_callback_query(call.id, "Xatolik yuz berdi, qayta urinib ko'ring.", show_alert=True)
        except Exception:
            pass


def run_broadcast(aud, text):
    where = {"driver": "role='driver'", "owner": "role='owner'"}.get(aud, "role IS NOT NULL")
    rows = q_all("SELECT user_id FROM users WHERE " + where + " AND user_id<>? AND COALESCE(blocked,0)=0", (ADMIN_ID,))
    ok = fail = 0
    for r in rows:
        try:
            bot.send_message(r["user_id"], T(r["user_id"], "admin_msg", text=esc(text)))
            ok += 1
        except Exception:
            fail += 1
        time.sleep(0.06)
    notify_admin("Xabar yuborildi: " + str(ok) + " ta, yetib bormadi: " + str(fail) + " ta.")


@bot.message_handler(func=lambda m: step_is(m, "admin_search") and is_admin(m.from_user.id))
def admin_search_step(message):
    uid = message.from_user.id
    cancel_state(uid)
    rows = admin_search_results(message.text)
    if not rows:
        safe_send(uid, "Hech narsa topilmadi.")
        return
    kb = types.InlineKeyboardMarkup()
    for u in rows:
        kb.add(types.InlineKeyboardButton(((u["full_name"] or "?") + " " + (u["phone"] or ""))[:60],
                                          callback_data="adm:uc:" + str(u["user_id"])))
    safe_send(uid, "Topilganlar:", reply_markup=kb)


@bot.message_handler(func=lambda m: step_is(m, "admin_bc") and is_admin(m.from_user.id))
def admin_bc_step(message):
    uid = message.from_user.id
    aud = user_state[uid]["data"]["aud"]
    user_state[uid] = {"step": "admin_bc_confirm", "data": {"aud": aud, "text": message.text}}
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Yuborish", callback_data="adm:bcgo"),
           types.InlineKeyboardButton("❌ Bekor qilish", callback_data="adm:bcno"))
    names = {"all": "hammaga", "driver": "furachilarga", "owner": "yuk beruvchilarga"}
    safe_send(uid, "Quyidagi xabar " + names.get(aud, "hammaga") + " yuboriladi:\n\n" + esc(message.text), reply_markup=kb)


# =========================== /START, TIL, ROL ===========================

def show_home(uid, user):
    """Til tanlangandan keyin: to'liq ro'yxatdan o'tganga menyu, aks holda rol tanlash."""
    if is_complete(user):
        if user["role"] == "driver" and not user["verified"]:
            safe_send(uid, T(uid, "wait_verify"))
            return
        safe_send(uid, T(uid, "welcome_back"), reply_markup=main_menu(uid, user["role"]))
    else:
        safe_send(uid, T(uid, "welcome_new"), reply_markup=role_keyboard(uid))


@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    cancel_state(uid)
    user = get_user(uid)
    if not user or user["lang"] not in ("uz", "ru"):
        safe_send(uid, TXT["lang_prompt"][0], reply_markup=lang_keyboard())
        return
    show_home(uid, user)


@bot.message_handler(func=lambda m: is_btn(m, "b_lang"))
def lang_button(message):
    cancel_state(message.from_user.id)
    safe_send(message.from_user.id, TXT["lang_prompt"][0], reply_markup=lang_keyboard())


@bot.callback_query_handler(func=lambda c: c.data in ("lang_uz", "lang_ru"))
def cb_lang(call):
    uid = call.from_user.id
    if is_admin(uid):
        bot.answer_callback_query(call.id)
        return
    lang = "uz" if call.data == "lang_uz" else "ru"
    save_user(uid, lang=lang)
    lang_cache[uid] = lang
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    user = get_user(uid)
    if is_complete(user):
        safe_send(uid, T(uid, "lang_set"))
    show_home(uid, user)


@bot.callback_query_handler(func=lambda c: c.data in ("role_driver", "role_owner"))
def cb_role(call):
    uid = call.from_user.id
    if is_admin(uid):
        bot.answer_callback_query(call.id)
        return
    u = get_user(uid)
    if is_complete(u):
        bot.answer_callback_query(call.id)
        show_home(uid, u)
        return
    role = "driver" if call.data == "role_driver" else "owner"
    save_user(uid, role=role)
    bot.answer_callback_query(call.id)
    user_state[uid] = {"step": "d_fullname" if role == "driver" else "o_fullname", "data": {}}
    safe_send(uid, T(uid, "ask_name"))


# =========================== RO'YXATDAN O'TISH: FURACHI ===========================

@bot.message_handler(func=lambda m: step_is(m, "d_fullname"))
def d_fullname(message):
    uid = message.from_user.id
    user_state[uid]["data"]["full_name"] = message.text.strip()
    user_state[uid]["step"] = "d_phone"
    safe_send(uid, T(uid, "ask_phone"), reply_markup=phone_keyboard(uid))


@bot.message_handler(content_types=["contact"], func=lambda m: step_is(m, "d_phone"))
def d_phone_contact(message):
    uid = message.from_user.id
    user_state[uid]["data"]["phone"] = message.contact.phone_number
    user_state[uid]["step"] = "d_brand"
    safe_send(uid, T(uid, "ask_brand"), reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: step_is(m, "d_phone"))
def d_phone_text(message):
    uid = message.from_user.id
    user_state[uid]["data"]["phone"] = message.text.strip()
    user_state[uid]["step"] = "d_brand"
    safe_send(uid, T(uid, "ask_brand"), reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: step_is(m, "d_brand"))
def d_brand(message):
    uid = message.from_user.id
    user_state[uid]["data"]["car_brand"] = message.text.strip()
    user_state[uid]["step"] = "d_model"
    safe_send(uid, T(uid, "ask_model"))


@bot.message_handler(func=lambda m: step_is(m, "d_model"))
def d_model(message):
    uid = message.from_user.id
    user_state[uid]["data"]["car_model"] = message.text.strip()
    user_state[uid]["step"] = "d_plate"
    safe_send(uid, T(uid, "ask_plate"))


@bot.message_handler(func=lambda m: step_is(m, "d_plate"))
def d_plate(message):
    uid = message.from_user.id
    user_state[uid]["data"]["car_plate"] = message.text.strip()
    user_state[uid]["step"] = "d_consent"
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(T(uid, "b_yes"), callback_data="consent_yes"),
        types.InlineKeyboardButton(T(uid, "b_no"), callback_data="consent_no"),
    )
    safe_send(uid, T(uid, "consent_text"), reply_markup=kb)


@bot.message_handler(func=lambda m: step_is(m, "d_consent"))
def d_consent_wait(message):
    safe_send(message.from_user.id, T(message.from_user.id, "consent_wait"))


@bot.callback_query_handler(func=lambda c: c.data in ("consent_yes", "consent_no"))
def cb_consent(call):
    uid = call.from_user.id
    st = user_state.get(uid)
    if not st or st.get("step") != "d_consent":
        bot.answer_callback_query(call.id, T(uid, "consent_stale"))
        return
    bot.answer_callback_query(call.id)
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    if call.data == "consent_no":
        cancel_state(uid)
        safe_send(uid, T(uid, "consent_no_msg"))
        return
    user_state[uid]["data"]["consent_at"] = str(datetime.datetime.now())
    user_state[uid]["step"] = "d_photo"
    safe_send(uid, T(uid, "ask_photo"))


@bot.message_handler(content_types=["photo"], func=lambda m: step_is(m, "d_photo"))
def d_photo(message):
    uid = message.from_user.id
    data = user_state[uid]["data"]
    photo_id = message.photo[-1].file_id
    save_user(
        uid,
        role="driver",
        full_name=data["full_name"],
        phone=data["phone"],
        car_brand=data["car_brand"],
        car_model=data["car_model"],
        car_plate=data["car_plate"],
        verify_photo=photo_id,
        verified=0,
        consent_at=data.get("consent_at"),
    )
    cancel_state(uid)
    safe_send(uid, T(uid, "reg_done_driver"))
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
                + esc(u["full_name"]) + "\n" + esc(u["phone"]) + "\n"
                + esc(u["car_brand"]) + " " + esc(u["car_model"]) + "\n"
                + "Davlat raqami: " + esc(u["car_plate"]) + "\nID: " + str(uid)
            ),
            reply_markup=kb,
        )
    except Exception as e:
        print("Admin fotosuratni olishda xato:", e)


@bot.message_handler(func=lambda m: step_is(m, "d_photo"))
def d_photo_wrong_type(message):
    safe_send(message.from_user.id, T(message.from_user.id, "photo_wrong"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("verify_"))
def cb_verify_driver(call):
    if not is_admin(call.from_user.id):
        return
    target_uid = int(call.data.split("_")[1])
    save_user(target_uid, verified=1, verify_photo=None)
    bot.answer_callback_query(call.id, "Tasdiqlandi!")
    try:
        bot.edit_message_caption("Tasdiqlandi.", call.message.chat.id, call.message.message_id)
    except Exception as e:
        print("edit_message_caption xatosi:", e)
    safe_send(target_uid, T(target_uid, "verified_msg"), reply_markup=main_menu(target_uid, "driver"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("reject_"))
def cb_reject_driver(call):
    if not is_admin(call.from_user.id):
        return
    target_uid = int(call.data.split("_")[1])
    save_user(target_uid, role=None, verified=0, verify_photo=None)
    bot.answer_callback_query(call.id, "Rad etildi.")
    try:
        bot.edit_message_caption("Rad etildi.", call.message.chat.id, call.message.message_id)
    except Exception as e:
        print("edit_message_caption xatosi:", e)
    safe_send(target_uid, T(target_uid, "rejected_msg"))


# =========================== RO'YXATDAN O'TISH: YUK BERUVCHI ===========================

@bot.message_handler(func=lambda m: step_is(m, "o_fullname"))
def o_fullname(message):
    uid = message.from_user.id
    user_state[uid]["data"]["full_name"] = message.text.strip()
    user_state[uid]["step"] = "o_phone"
    safe_send(uid, T(uid, "ask_phone"), reply_markup=phone_keyboard(uid))


def finish_owner_registration(uid, phone):
    data = user_state[uid]["data"]
    save_user(uid, role="owner", full_name=data["full_name"], phone=phone)
    cancel_state(uid)
    safe_send(uid, T(uid, "reg_done_owner"), reply_markup=main_menu(uid, "owner"))
    notify_admin("Yangi yuk beruvchi ro'yxatdan o'tdi:\n" + esc(data["full_name"]) + "\n" + esc(phone) + "\nID: " + str(uid))


@bot.message_handler(content_types=["contact"], func=lambda m: step_is(m, "o_phone"))
def o_phone_contact(message):
    finish_owner_registration(message.from_user.id, message.contact.phone_number)


@bot.message_handler(func=lambda m: step_is(m, "o_phone"))
def o_phone_text(message):
    finish_owner_registration(message.from_user.id, message.text.strip())


# =========================== PROFIL / BALANS ===========================

@bot.message_handler(func=lambda m: is_btn(m, "b_prof"))
def profile(message):
    uid = message.from_user.id
    cancel_state(uid)
    u = get_user(uid)
    if not is_complete(u):
        return cmd_start(message)
    if u["role"] == "driver":
        text = T(
            uid, "prof_driver", name=esc(u["full_name"]), phone=esc(u["phone"]),
            car=esc(u["car_brand"]) + " " + esc(u["car_model"]), plate=esc(u["car_plate"]),
            status=T(uid, "st_verified" if u["verified"] else "st_unverified"),
            rating=driver_rating_text(u, uid),
        )
    else:
        text = T(uid, "prof_owner", name=esc(u["full_name"]), phone=esc(u["phone"]))
    safe_send(uid, text)


@bot.message_handler(func=lambda m: is_btn(m, "b_bal"))
def balance_view(message):
    uid = message.from_user.id
    cancel_state(uid)
    u = get_user(uid)
    if not is_complete(u):
        return cmd_start(message)
    if u["role"] == "driver":
        r = q_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(price),0) AS gross, COALESCE(SUM(driver_fee),0) AS fees "
            "FROM cargos WHERE driver_id=? AND status='delivered'", (uid,))
        safe_send(uid, T(uid, "bal_driver", bal=money(u["balance"]), n=r["n"], gross=money(r["gross"]),
                         fees=money(r["fees"]), net=money(r["gross"] - r["fees"])))
    else:
        r = q_one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(owner_fee),0) AS fees FROM cargos WHERE owner_id=?", (uid,))
        safe_send(uid, T(uid, "bal_owner", bal=money(u["balance"]), n=r["n"], fees=money(r["fees"])))


# =========================== YUK QO'SHISH (YUK BERUVCHI) ===========================

@bot.message_handler(func=lambda m: is_btn(m, "b_add"))
def add_cargo_start(message):
    uid = message.from_user.id
    cancel_state(uid)
    u = get_user(uid)
    if not u or u["role"] != "owner":
        return
    pending = unpaid_owner_cargo(uid)
    if pending:
        safe_send(uid, T(uid, "pay_first_owner"))
        send_owner_pay_prompt(uid, pending)
        return
    user_state[uid] = {"step": "c_type", "data": {}}
    safe_send(uid, T(uid, "ask_ctype"), reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: step_is(m, "c_type"))
def c_type(message):
    uid = message.from_user.id
    user_state[uid]["data"]["cargo_type"] = message.text.strip()
    user_state[uid]["step"] = "c_weight"
    safe_send(uid, T(uid, "ask_cweight"))


@bot.message_handler(func=lambda m: step_is(m, "c_weight"))
def c_weight(message):
    uid = message.from_user.id
    user_state[uid]["data"]["weight"] = message.text.strip()
    user_state[uid]["step"] = "c_from"
    safe_send(uid, T(uid, "ask_cfrom"))


@bot.message_handler(func=lambda m: step_is(m, "c_from"))
def c_from(message):
    uid = message.from_user.id
    user_state[uid]["data"]["from_loc"] = message.text.strip()
    user_state[uid]["step"] = "c_to"
    safe_send(uid, T(uid, "ask_cto"))


@bot.message_handler(func=lambda m: step_is(m, "c_to"))
def c_to(message):
    uid = message.from_user.id
    user_state[uid]["data"]["to_loc"] = message.text.strip()
    user_state[uid]["step"] = "c_from_location"
    safe_send(uid, T(uid, "ask_cfromloc"), reply_markup=location_keyboard(uid))


@bot.message_handler(content_types=["location"], func=lambda m: step_is(m, "c_from_location"))
def c_from_location(message):
    uid = message.from_user.id
    user_state[uid]["data"]["lat"] = message.location.latitude
    user_state[uid]["data"]["lon"] = message.location.longitude
    user_state[uid]["step"] = "c_to_location"
    safe_send(uid, T(uid, "ask_ctoloc"), reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(content_types=["location"], func=lambda m: step_is(m, "c_to_location"))
def c_to_location(message):
    uid = message.from_user.id
    data = user_state[uid]["data"]
    data["to_lat"] = message.location.latitude
    data["to_lon"] = message.location.longitude
    dist = haversine(data["lat"], data["lon"], data["to_lat"], data["to_lon"])
    data["distance_km"] = dist
    user_state[uid]["step"] = "c_price"
    safe_send(uid, T(uid, "ask_price", dist=format(dist, ".1f")), reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(func=lambda m: step_is(m, "c_price"))
def c_price(message):
    uid = message.from_user.id
    price = parse_som(message.text)
    if price is None or price <= 0:
        safe_send(uid, T(uid, "price_bad"))
        return
    data = user_state[uid]["data"]
    cid = add_cargo(
        owner_id=uid, cargo_type=data["cargo_type"], weight=data["weight"],
        from_loc=data["from_loc"], to_loc=data["to_loc"], price=price,
        lat=data["lat"], lon=data["lon"], to_lat=data["to_lat"], to_lon=data["to_lon"],
        status="open",
    )
    cancel_state(uid)
    safe_send(uid, T(uid, "cargo_posted", id=cid), reply_markup=main_menu(uid, "owner"))
    notify_admin("Yangi yuk #" + str(cid) + ": " + esc(data["cargo_type"]) + ", " + esc(data["from_loc"]) + " -> "
                 + esc(data["to_loc"]) + ", " + money(price) + " so'm")


# =========================== OCHIQ YUKLAR / YAQIN YUKLAR (FURACHI) ===========================

def is_verified_driver(u):
    return bool(u and u["role"] == "driver" and u["verified"])


def take_keyboard(uid, cargo_id):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(T(uid, "b_take"), callback_data="take_" + str(cargo_id)))
    return kb


@bot.message_handler(func=lambda m: is_btn(m, "b_open"))
def list_open_cargos(message):
    uid = message.from_user.id
    cancel_state(uid)
    if not is_verified_driver(get_user(uid)):
        safe_send(uid, T(uid, "only_verified"))
        return
    rows = open_cargos()
    if not rows:
        safe_send(uid, T(uid, "no_open"))
        return
    for row in rows[:15]:
        safe_send(uid, cargo_card_text(row, uid), reply_markup=take_keyboard(uid, row["id"]))
        if row["lat"] is not None and row["lon"] is not None:
            try:
                bot.send_location(uid, row["lat"], row["lon"])
            except Exception as e:
                print("send_location xatosi:", e)


@bot.message_handler(func=lambda m: is_btn(m, "b_near"))
def ask_driver_location(message):
    uid = message.from_user.id
    cancel_state(uid)
    if not is_verified_driver(get_user(uid)):
        safe_send(uid, T(uid, "only_verified"))
        return
    safe_send(uid, T(uid, "send_loc_near"), reply_markup=location_keyboard(uid))


@bot.message_handler(content_types=["location"], func=lambda m: user_state.get(m.from_user.id, {}).get("step") is None)
def driver_location_update(message):
    uid = message.from_user.id
    u = get_user(uid)
    if not is_verified_driver(u):
        return
    save_driver_location(uid, message.location.latitude, message.location.longitude)
    if getattr(message.location, "live_period", None):
        safe_send(uid, T(uid, "live_saved"), reply_markup=main_menu(uid, "driver"))
        return
    rows = open_cargos()
    if not rows:
        safe_send(uid, T(uid, "no_open"), reply_markup=main_menu(uid, "driver"))
        return
    scored = []
    for row in rows:
        if row["lat"] is not None and row["lon"] is not None:
            dist = haversine(message.location.latitude, message.location.longitude, row["lat"], row["lon"])
            scored.append((dist, row))
    scored.sort(key=lambda x: x[0])
    if not scored:
        safe_send(uid, T(uid, "no_near"), reply_markup=main_menu(uid, "driver"))
        return
    safe_send(uid, T(uid, "near_title"), reply_markup=main_menu(uid, "driver"))
    for dist, row in scored[:5]:
        safe_send(uid, cargo_card_text(row, uid) + T(uid, "dist_line", d=format(dist, ".1f")),
                  reply_markup=take_keyboard(uid, row["id"]))
        try:
            bot.send_location(uid, row["lat"], row["lon"])
        except Exception as e:
            print("send_location xatosi:", e)


_last_live_save = {}


@bot.edited_message_handler(content_types=["location"])
def live_location_edit(message):
    """Furachi 'Jonli joylashuv' ulashganda Telegram har safar yangilangan nuqtani shu yerga yuboradi."""
    uid = message.from_user.id
    now = time.time()
    if now - _last_live_save.get(uid, 0) < 15:
        return
    u = get_user(uid)
    if not is_verified_driver(u):
        return
    _last_live_save[uid] = now
    save_driver_location(uid, message.location.latitude, message.location.longitude)


# =========================== YUKNI OLISH / YETKAZIB BERISH ===========================

@bot.callback_query_handler(func=lambda c: c.data.startswith("take_"))
def cb_take(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["status"] != "open":
        bot.answer_callback_query(call.id, T(uid, "take_gone"), show_alert=True)
        return
    driver = get_user(uid)
    if not is_verified_driver(driver):
        bot.answer_callback_query(call.id, T(uid, "take_only_ver"), show_alert=True)
        return
    pending_driver = unpaid_driver_cargo(uid)
    if pending_driver:
        bot.answer_callback_query(call.id, T(uid, "take_pay_first"), show_alert=True)
        send_driver_pay_prompt(uid, pending_driver)
        return

    owner_fee = round(row["price"] * OWNER_FOIZ, 0)
    update_cargo(cargo_id, status="taken", driver_id=uid, owner_fee=owner_fee, owner_paid=0)
    bot.answer_callback_query(call.id, T(uid, "take_ok"))
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)

    owner = get_user(row["owner_id"])
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(T(uid, "b_deliv"), callback_data="delivered_" + str(cargo_id)))
    safe_send(uid, T(uid, "driver_took", id=cargo_id, owner=esc(owner["full_name"]), phone=esc(owner["phone"])),
              reply_markup=kb)
    safe_send(uid, T(uid, "live_hint"))

    oid = row["owner_id"]
    fresh = get_cargo(cargo_id)
    safe_send(
        oid,
        T(oid, "owner_taken", id=cargo_id, driver=esc(driver["full_name"]), phone=esc(driver["phone"]),
          car=esc(driver["car_brand"]) + " " + esc(driver["car_model"]), plate=esc(driver["car_plate"]),
          rating=driver_rating_text(driver, oid), pct=format(OWNER_FOIZ * 100, ".0f"),
          fee=money(owner_fee), card=ADMIN_CARD),
        reply_markup=owner_pay_keyboard(fresh, oid),
    )
    notify_admin("Yuk #" + str(cargo_id) + " olindi: furachi " + esc(driver["full_name"]) + " " + esc(driver["phone"])
                 + "\n" + esc(row["from_loc"]) + " -> " + esc(row["to_loc"]) + ", " + money(row["price"]) + " so'm")


@bot.callback_query_handler(func=lambda c: c.data.startswith("delivered_"))
def cb_delivered(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["status"] != "taken" or row["driver_id"] != uid:
        bot.answer_callback_query(call.id, T(uid, "deliv_bad"), show_alert=True)
        return
    driver_fee = round(row["price"] * DRIVER_FOIZ, 0)
    update_cargo(cargo_id, status="delivered", driver_fee=driver_fee, driver_paid=0)
    bot.answer_callback_query(call.id, T(uid, "deliv_ok"))
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)

    fresh = get_cargo(cargo_id)
    safe_send(
        uid,
        T(uid, "driver_delivered", id=cargo_id, pct=format(DRIVER_FOIZ * 100, ".0f"),
          fee=money(driver_fee), card=ADMIN_CARD),
        reply_markup=driver_pay_keyboard(fresh, uid),
    )
    oid = row["owner_id"]
    safe_send(oid, T(oid, "owner_delivered", id=cargo_id))
    if not row["rated"]:
        rate_kb = types.InlineKeyboardMarkup()
        rate_kb.add(*[
            types.InlineKeyboardButton(str(n) + " ★", callback_data="rate_" + str(cargo_id) + "_" + str(n))
            for n in range(1, 6)
        ])
        safe_send(oid, T(oid, "rate_ask"), reply_markup=rate_kb)
    notify_admin("Yuk #" + str(cargo_id) + " yetkazildi. Furachi komissiyasi: " + money(driver_fee) + " so'm.")


def _pay_pending_cb(call, who):
    """Ikkala tomon uchun umumiy: 'Toladim' bosilganda chek rasmini so'raydi."""
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row[who + "_id"] != uid or row[who + "_paid"]:
        bot.answer_callback_query(call.id, T(uid, "pay_dup"), show_alert=True)
        return
    user_state[uid] = {"step": who + "_receipt", "data": {"cargo_id": cargo_id}}
    bot.answer_callback_query(call.id, T(uid, "pay_send_photo_cb"))
    safe_send(uid, T(uid, "pay_send_photo"))


def _pay_remind_cb(call, who):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row[who + "_id"] != uid or row[who + "_paid"]:
        bot.answer_callback_query(call.id, T(uid, "pay_dup"), show_alert=True)
        return
    bot.answer_callback_query(call.id, T(uid, "pay_remind_ok"))
    (notify_admin_owner_payment if who == "owner" else notify_admin_driver_payment)(row)


def _receipt_photo(message, who):
    uid = message.from_user.id
    cargo_id = user_state[uid]["data"]["cargo_id"]
    cancel_state(uid)
    row = get_cargo(cargo_id)
    if not row or row[who + "_id"] != uid or row[who + "_paid"]:
        safe_send(uid, T(uid, "pay_bad_action"))
        return
    photo_id = message.photo[-1].file_id
    update_cargo(cargo_id, **{who + "_pay_req": 1, who + "_receipt": photo_id})
    row = get_cargo(cargo_id)
    kbf = owner_pay_keyboard if who == "owner" else driver_pay_keyboard
    safe_send(uid, T(uid, "pay_receipt_ok"), reply_markup=kbf(row, uid))
    (notify_admin_owner_payment if who == "owner" else notify_admin_driver_payment)(row)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ownerpaid_"))
def cb_owner_paid(call):
    _pay_pending_cb(call, "owner")


@bot.callback_query_handler(func=lambda c: c.data.startswith("remindowner_"))
def cb_owner_remind(call):
    _pay_remind_cb(call, "owner")


@bot.message_handler(content_types=["photo"], func=lambda m: step_is(m, "owner_receipt"))
def owner_receipt_photo(message):
    _receipt_photo(message, "owner")


@bot.message_handler(func=lambda m: step_is(m, "owner_receipt"))
def owner_receipt_wrong_type(message):
    safe_send(message.from_user.id, T(message.from_user.id, "pay_photo_wrong"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("driverpaid_"))
def cb_driver_paid(call):
    _pay_pending_cb(call, "driver")


@bot.callback_query_handler(func=lambda c: c.data.startswith("reminddriver_"))
def cb_driver_remind(call):
    _pay_remind_cb(call, "driver")


@bot.message_handler(content_types=["photo"], func=lambda m: step_is(m, "driver_receipt"))
def driver_receipt_photo(message):
    _receipt_photo(message, "driver")


@bot.message_handler(func=lambda m: step_is(m, "driver_receipt"))
def driver_receipt_wrong_type(message):
    safe_send(message.from_user.id, T(message.from_user.id, "pay_photo_wrong"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("confirmowner_"))
def cb_confirm_owner(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Bu tugma faqat admin uchun.", show_alert=True)
        return
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row:
        bot.answer_callback_query(call.id, "Topilmadi (yuk o'chirilgan bo'lishi mumkin).", show_alert=True)
        return
    if row["owner_paid"]:
        bot.answer_callback_query(call.id, "Bu to'lov allaqachon tasdiqlangan.", show_alert=True)
        return
    update_cargo(cargo_id, owner_paid=1)
    add_admin_balance(row["owner_fee"])
    bot.answer_callback_query(call.id, "Tasdiqlandi!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)
    safe_send(row["owner_id"], T(row["owner_id"], "owner_pay_confirmed"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("confirmdriver_"))
def cb_confirm_driver(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "Bu tugma faqat admin uchun.", show_alert=True)
        return
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row:
        bot.answer_callback_query(call.id, "Topilmadi (yuk o'chirilgan bo'lishi mumkin).", show_alert=True)
        return
    if row["driver_paid"]:
        bot.answer_callback_query(call.id, "Bu to'lov allaqachon tasdiqlangan.", show_alert=True)
        return
    update_cargo(cargo_id, driver_paid=1)
    add_admin_balance(row["driver_fee"])
    bot.answer_callback_query(call.id, "Tasdiqlandi!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)
    safe_send(row["driver_id"], T(row["driver_id"], "driver_pay_confirmed"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("rate_"))
def cb_rate_driver(call):
    uid = call.from_user.id
    parts = call.data.split("_")
    cargo_id = int(parts[1])
    stars = int(parts[2])
    row = get_cargo(cargo_id)
    if not row or row["owner_id"] != uid or row["rated"]:
        bot.answer_callback_query(call.id, T(uid, "rate_dup"), show_alert=True)
        return
    q_exec("UPDATE users SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE user_id=?",
           (stars, row["driver_id"]))
    update_cargo(cargo_id, rated=1)
    bot.answer_callback_query(call.id, T(uid, "rate_done"))
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception as e:
        print("edit_message_reply_markup xatosi:", e)
    safe_send(row["driver_id"], T(row["driver_id"], "rate_got", n=stars))


# =========================== MENING YUKLARIM ===========================

@bot.message_handler(func=lambda m: is_btn(m, "b_my"))
def my_cargos(message):
    uid = message.from_user.id
    cancel_state(uid)
    u = get_user(uid)
    if not is_complete(u):
        return cmd_start(message)
    rows = owner_cargos(uid) if u["role"] == "owner" else driver_cargos(uid)
    if not rows:
        safe_send(uid, T(uid, "no_my"))
        return
    for row in rows[:15]:
        kb = None
        if u["role"] == "owner" and row["status"] == "taken" and row["driver_id"]:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(T(uid, "b_where"), callback_data="where_" + str(row["id"])))
        safe_send(uid, cargo_card_text(row, uid), reply_markup=kb)
    if u["role"] == "owner":
        pend = unpaid_owner_cargo(uid)
        if pend:
            send_owner_pay_prompt(uid, pend)
    elif u["role"] == "driver":
        pend = unpaid_driver_cargo(uid)
        if pend:
            send_driver_pay_prompt(uid, pend)


@bot.callback_query_handler(func=lambda c: c.data.startswith("where_"))
def cb_where(call):
    uid = call.from_user.id
    cargo_id = int(call.data.split("_")[1])
    row = get_cargo(cargo_id)
    if not row or row["owner_id"] != uid or row["status"] != "taken" or not row["driver_id"]:
        bot.answer_callback_query(call.id, T(uid, "where_bad"), show_alert=True)
        return
    loc = q_one("SELECT * FROM driver_locations WHERE user_id=?", (row["driver_id"],))
    if not loc:
        bot.answer_callback_query(call.id, T(uid, "where_none"), show_alert=True)
        return
    bot.answer_callback_query(call.id)
    try:
        bot.send_location(uid, loc["lat"], loc["lon"])
    except Exception as e:
        print("send_location xatosi:", e)
    when = ago(loc["updated_at"])
    safe_send(uid, T(uid, "where_info", when=when))


# =========================== YORDAM ===========================

@bot.message_handler(func=lambda m: is_btn(m, "b_help"))
def help_start(message):
    uid = message.from_user.id
    user_state[uid] = {"step": "help_msg", "data": {}}
    safe_send(uid, T(uid, "help_ask"))


@bot.message_handler(func=lambda m: step_is(m, "help_msg"))
def help_forward(message):
    uid = message.from_user.id
    cancel_state(uid)
    u = get_user(uid)
    name = u["full_name"] if u and u["full_name"] else (getattr(message.from_user, "first_name", None) or "Noma'lum")
    phone = u["phone"] if u and u["phone"] else "-"
    try:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("Javob yozish", callback_data="replyto_" + str(uid)))
        sent = bot.send_message(
            ADMIN_ID,
            "Yordam so'rovi\n" + esc(name) + "\n" + esc(phone) + "\nID: " + str(uid) + "\n\n" + esc(message.text),
            reply_markup=kb,
        )
        save_help_map(sent.message_id, uid)
        safe_send(uid, T(uid, "help_sent"))
    except Exception as e:
        print("Adminga yuborishda xato:", e)
        safe_send(uid, T(uid, "help_fail"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("replyto_"))
def cb_reply_to(call):
    if not is_admin(call.from_user.id):
        return
    target_uid = int(call.data.split("_")[1])
    user_state[ADMIN_ID] = {"step": "admin_replying", "data": {"target": target_uid}}
    bot.answer_callback_query(call.id)
    safe_send(ADMIN_ID, "Javobingizni yozing (keyingi xabaringiz avtomatik foydalanuvchiga yuboriladi):")


@bot.message_handler(func=lambda m: step_is(m, "admin_replying") and is_admin(m.from_user.id))
def admin_reply_via_state(message):
    uid = message.from_user.id
    target_uid = user_state[uid]["data"]["target"]
    cancel_state(uid)
    safe_send(target_uid, T(target_uid, "admin_says", text=esc(message.text)))
    safe_send(uid, "Yuborildi.")


@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.reply_to_message is not None
                     and getattr(m, "text", None) is not None)
def admin_reply(message):
    target_uid = get_help_user(message.reply_to_message.message_id)
    if target_uid:
        safe_send(target_uid, T(target_uid, "admin_says", text=esc(message.text)))
        safe_send(ADMIN_ID, "Javob yuborildi.")


# =========================== ADMIN BUYRUQLARI ===========================

@bot.message_handler(commands=["myid"])
def cmd_myid(message):
    uid = message.from_user.id
    if is_admin(uid):
        safe_send(uid, "Sizning ID: " + str(uid) + "\nHolat: ADMIN (to'g'ri sozlangan)", reply_markup=admin_menu())
    else:
        safe_send(
            uid,
            "Sizning Telegram ID raqamingiz: " + str(uid) + "\n"
            "Holat: admin emas.\n\n"
            "Agar siz botning egasi bo'lsangiz, shu raqamni serverdagi ADMIN_ID o'zgaruvchisiga yozing.",
        )


@bot.message_handler(commands=["kutilayotgan"])
def cmd_pending(message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    unverified = q_all(
        "SELECT * FROM users WHERE role='driver' AND verified=0 AND verify_photo IS NOT NULL ORDER BY user_id")
    owner_rows = q_all("SELECT * FROM cargos WHERE owner_pay_req=1 AND owner_paid=0 AND owner_fee>0 ORDER BY id")
    driver_rows = q_all("SELECT * FROM cargos WHERE driver_pay_req=1 AND driver_paid=0 AND driver_fee>0 ORDER BY id")
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
                uid, u["verify_photo"],
                caption=(
                    "Furachi: " + esc(u["full_name"]) + "\n" + esc(u["phone"]) + "\n"
                    + esc(u["car_brand"]) + " " + esc(u["car_model"]) + "\n"
                    + "Davlat raqami: " + esc(u["car_plate"]) + "\nID: " + str(u["user_id"])
                ),
                reply_markup=kb,
            )
        except Exception as e:
            print("Tasdiqlanmagan furachini qayta ko'rsatishda xato:", e)
    if owner_rows or driver_rows:
        safe_send(uid, "=== To'lanmagan xizmat haqlari ===")
    for row in owner_rows:
        notify_admin_owner_payment(row)
    for row in driver_rows:
        notify_admin_driver_payment(row)


@bot.message_handler(commands=["admin"])
def admin_panel(message):
    uid = message.from_user.id
    if not is_admin(uid):
        return
    c = lambda sql, *a: q_one(sql, a)["c"]
    drivers = c("SELECT COUNT(*) c FROM users WHERE role='driver' AND user_id<>?", ADMIN_ID)
    unverified = c("SELECT COUNT(*) c FROM users WHERE role='driver' AND verified=0 AND user_id<>?", ADMIN_ID)
    owners = c("SELECT COUNT(*) c FROM users WHERE role='owner' AND user_id<>?", ADMIN_ID)
    blocked = c("SELECT COUNT(*) c FROM users WHERE blocked=1")
    total = c("SELECT COUNT(*) c FROM cargos")
    opened = c("SELECT COUNT(*) c FROM cargos WHERE status='open'")
    taken = c("SELECT COUNT(*) c FROM cargos WHERE status='taken'")
    delivered = c("SELECT COUNT(*) c FROM cargos WHERE status='delivered'")
    pend = c("SELECT COUNT(*) c FROM cargos WHERE (owner_pay_req=1 AND owner_paid=0) OR (driver_pay_req=1 AND driver_paid=0)")
    text = (
        "📊 Statistika\n\n"
        "Furachilar: " + str(drivers) + " (tasdiqlanmagan: " + str(unverified) + ")\n"
        "Yuk beruvchilar: " + str(owners) + "\n"
        "Bloklanganlar: " + str(blocked) + "\n"
        "Jami yuklar: " + str(total) + " (ochiq: " + str(opened) + ", yo'lda: " + str(taken) + ", yetkazilgan: " + str(delivered) + ")\n"
        "Joriy balans (sof foyda): " + money(get_admin_balance()) + " so'm\n"
        "Tasdiq kutayotgan to'lovlar: " + str(pend)
    )
    safe_send(uid, text, reply_markup=admin_menu())


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
    safe_send(uid, money(amount) + " so'm " + str(target_id) + " ga qo'shildi.")
    safe_send(target_id, "Balansingizga " + money(amount) + " so'm qo'shildi.")


# =========================== BARCHA BOSHQA XABARLAR (fallback) ===========================

@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(message):
    uid = message.from_user.id
    if is_admin(uid):
        if user_state.get(uid, {}).get("step") is None:
            safe_send(uid, "Menyudan tanlang:", reply_markup=admin_menu())
        return
    u = get_user(uid)
    if not is_complete(u) and user_state.get(uid, {}).get("step") is None:
        return cmd_start(message)
    if user_state.get(uid, {}).get("step") is None:
        safe_send(uid, T(uid, "use_buttons"), reply_markup=main_menu(uid, u["role"]))


# =========================== ISHGA TUSHIRISH ===========================

def run_forever():
    init_db()
    try:
        for r in q_all("SELECT user_id FROM users WHERE blocked=1"):
            blocked_ids.add(r["user_id"])
    except Exception as e:
        print("Bloklanganlar ro'yxati o'qilmadi:", e)
    print("Baza tayyor. Bot ishga tushdi...")
    try:
        bot.send_message(ADMIN_ID, "Bot ishga tushdi. Admin menyusi yangilandi.", reply_markup=admin_menu())
    except Exception as e:
        print("ADMIN_ID ga xabar yuborib bo'lmadi (ADMIN_ID noto'g'ri yoki admin botga /start bosmagan):", e)

    # Render.com kabi platformalar "Web Service" uchun ochiq port talab qiladi.
    # Bot polling orqali ishlaydi, shuning uchun kichik "soxta" HTTP server ishga tushiramiz.
    try:
        from http.server import BaseHTTPRequestHandler, HTTPServer

        class _PingHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Bot ishlamoqda")

            def log_message(self, format, *args):
                pass

        port = int(os.getenv("PORT", "8080"))

        def _run_http():
            server = HTTPServer(("0.0.0.0", port), _PingHandler)
            server.serve_forever()

        threading.Thread(target=_run_http, daemon=True).start()
        print("Yordamchi HTTP server " + str(port) + "-portda ishga tushdi.")
    except Exception as e:
        print("HTTP server ishga tushmadi:", e)

    while True:
        try:
            bot.infinity_polling(
                timeout=20, long_polling_timeout=20, skip_pending=True,
                allowed_updates=["message", "edited_message", "callback_query"],
            )
        except Exception:
            print("Kutilmagan xatolik, 5 soniyadan song qayta urinilmoqda:")
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    run_forever()
