# -----------------------------
import os
import json
import threading
import re
import logging
import calendar
import shutil
import secrets
import base64
import time
import csv
import xml.etree.ElementTree as ET
import random
import concurrent.futures
from uuid import uuid4
from markupsafe import escape
from datetime import datetime, timedelta, timezone
from pathlib import Path  
from urllib.parse import parse_qs
from functools import wraps 
# -----------------------------------------------------------
from flask import (
    Flask, render_template, request, redirect, url_for, session, 
    jsonify, make_response, Response, flash, current_app
)
from flask_session import Session
from flask_babel import Babel
import babel.dates
import babel.numbers

from flask import send_from_directory
from flask_mail import Mail, Message  
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, Text, cast, Integer
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as SQLSession
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from translate import Translator
from concurrent.futures import ThreadPoolExecutor
from playwright.sync_api import sync_playwright

import pandas as pd
import numpy as np
import openpyxl, xlsxwriter

# -----------------------------
# Models & Logic Imports (Bulletproof Sync)
# -----------------------------

from database import (
    db, PasswordResetToken, Company, Employee, Customer, Payment, PaymentLink, Invoice, InvoiceItem,
    Product, Category, Supplier, SupplierPurchase, Transaction, User, OwnerUser, EmployeeData, HoursData, TaxCredit, Tofes101Submission, Form161Report, Form126Report, Form102Report, FormH102Report, FormB102Report, ShiftState, Timesheet, TimeEntry, Task,
    OWNER_COMPANY_ID
)
from data import get_employees, add_employee

# -----------------------------------------------------------
#  1. Load Environment & Init Flask
# -----------------------------------------------------------

from dotenv import load_dotenv
load_dotenv()
app = Flask(__name__, static_folder="static")

IS_RENDER = "RENDER" in os.environ
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# -----------------------------------------------------------
#  2. הגדרת נתיבי תיקיות (Paths) - Local & Render Safe
# -----------------------------------------------------------

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

# נקודת החיבור הרשמית של הדיסק הקבוע ב-Render
PERSISTENT_BASE = "/data" if (IS_RENDER and os.path.exists("/data")) else BASE_DIR

if IS_RENDER:
    CUSTOMERS_DIR     = os.path.join(PERSISTENT_BASE, "customers")
    SUPPLIERS_DIR     = os.path.join(PERSISTENT_BASE, "suppliers")
    COMPANY_DIR       = os.path.join(PERSISTENT_BASE, "companies")
    ITEMS_DIR         = os.path.join(PERSISTENT_BASE, "items")
    TRANSACTIONS_DIR  = os.path.join(PERSISTENT_BASE, "uploads", "transactions")
    CATEGORIES_DIR    = os.path.join(PERSISTENT_BASE, "categories")
    CANCELLATIONS_DIR = os.path.join(PERSISTENT_BASE, "cancel_reasons")
    EMPLOYEES_DIR     = os.path.join(PERSISTENT_BASE, "employees")
else:
    CUSTOMERS_DIR     = os.path.join(BASE_DIR, "customers")
    SUPPLIERS_DIR     = os.path.join(BASE_DIR, "suppliers")
    ITEMS_DIR         = os.path.join(BASE_DIR, "static", "items")
    TRANSACTIONS_DIR  = os.path.join(BASE_DIR, "static", "transactions")
    CATEGORIES_DIR    = os.path.join(BASE_DIR, "static", "categories")
    COMPANY_DIR       = os.path.join(BASE_DIR, "companies")
    EMPLOYEES_DIR     = os.path.join(BASE_DIR, "static", "employees")
    CANCELLATIONS_DIR = os.path.join(BASE_DIR, "cancel_reasons")

# -----------------------------------------------------------
#  Render: יצירת כל התיקיות על הדיסק הקבוע
# -----------------------------------------------------------

def safe_create(path):
    try:
        os.makedirs(path, exist_ok=True)
        print(f"✔ Folder ready: {path}")
    except Exception as e:
        print(f"❌ Could not create folder {path}: {e}")

ALL_DIRS = [
    ITEMS_DIR,
    TRANSACTIONS_DIR,
    UPLOAD_FOLDER,
    CATEGORIES_DIR,
    CUSTOMERS_DIR,
    EMPLOYEES_DIR,
    SUPPLIERS_DIR,
    COMPANY_DIR,
    CANCELLATIONS_DIR,
    app.instance_path,
]

for d in ALL_DIRS:
    safe_create(d)

# -----------------------------------------------------------
#  3. Security & Session Config
# -----------------------------------------------------------

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

if IS_RENDER and not os.environ.get("SECRET_KEY"):
    raise RuntimeError("SECRET_KEY is missing on Render")

if IS_RENDER and not os.path.exists("/data"):
    print("⚠️ WARNING: /data is not mounted. hours.json / uploads will be LOST on deploy!")

app.secret_key = os.environ.get("SECRET_KEY") or "local_dev_key_only"
jwt_key = os.environ.get("JWT_SECRET_KEY") or "local_jwt_key_only"

app.config.update(
    JWT_SECRET_KEY=jwt_key,
    SESSION_PERMANENT=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    SESSION_COOKIE_SECURE=IS_RENDER,
    REMEMBER_COOKIE_SECURE=IS_RENDER,
    SESSION_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',

    SESSION_TYPE="sqlalchemy",
    SESSION_SQLALCHEMY=db,
    SESSION_SQLALCHEMY_TABLE="flask_sessions",
    SESSION_CLEANUP_N_REQUESTS=500,

    ITEMS_DIR=ITEMS_DIR,
    TRANSACTIONS_DIR=TRANSACTIONS_DIR,
    UPLOAD_FOLDER=UPLOAD_FOLDER,
    CATEGORIES_DIR=CATEGORIES_DIR,
    CUSTOMERS_DIR=CUSTOMERS_DIR,
    EMPLOYEES_DIR=EMPLOYEES_DIR,
    SUPPLIERS_DIR=SUPPLIERS_DIR,
    COMPANY_DIR=COMPANY_DIR,
    CANCELLATIONS_DIR=CANCELLATIONS_DIR 
)

# -----------------------------
#  Database Config (Postgres / SQLite)
# -----------------------------

db_choice = os.getenv("DB_CHOICE", "sqlite").lower()

if db_choice == "postgres":
    uri = os.getenv("POSTGRES_URI")
    if not uri:
        raise RuntimeError("POSTGRES_URI is missing but DB_CHOICE=postgres")
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
else:
    sqlite_path = os.getenv(
        "SQLITE_URI",
        f"sqlite:///{os.path.join(app.instance_path, 'data.db')}"
    )
    app.config["SQLALCHEMY_DATABASE_URI"] = sqlite_path

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

print("=== USING DATABASE ===")
print(app.config["SQLALCHEMY_DATABASE_URI"])


# -----------------------------
#  Mail Configuration
# -----------------------------

app.config.update(
    MAIL_SERVER=os.getenv("MAIL_SERVER", "smtp.gmail.com"),
    MAIL_PORT=int(os.getenv("MAIL_PORT", 587)),
    MAIL_USE_TLS=os.getenv("MAIL_USE_TLS", "true").lower() == "true",
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_DEFAULT_SENDER=os.getenv("MAIL_DEFAULT_SENDER")
)

# -----------------------------
#  Init Extensions
# -----------------------------

db.init_app(app)
Session(app)

migrate = Migrate(app, db)
mail = Mail(app)
jwt = JWTManager(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

babel = Babel(app)

# -----------------------------
#  Owner Credentials (from ENV)
# -----------------------------

OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")

# -----------------------------
#  n8n connection key (set N8N_API_KEY in .env locally and in Render > Environment)
# -----------------------------
N8N_API_KEY = os.environ.get("N8N_API_KEY", "")
if not N8N_API_KEY:
    print("⚠ N8N_API_KEY is not set: n8n requests will be rejected")


# -----------------------------
#  Constants used by invoice code
# -----------------------------
SELF_CUSTOMER_NAME = "חשבונית עצמית / חברה"
SPECIAL_PRODUCT_IDS = ['rent', 'stocks', 'dividend', 'unspecified']
NON_STOCK_PRODUCT_IDS = SPECIAL_PRODUCT_IDS + ['deleted']


# -----------------------------
#  Password helpers (do not depend on a method inside the User model)
# -----------------------------
def _set_user_password(user, raw_password):
    user.password_hash = generate_password_hash(raw_password)


def _check_user_password(user, raw_password):
    try:
        return bool(user.password_hash) and check_password_hash(user.password_hash, raw_password)
    except Exception:
        return False


# -----------------------------
#  Customer / database helpers
# -----------------------------
def sync_sequence(model):
    """Postgres: keep the id counter ahead of MAX(id). Does nothing on SQLite."""
    if db.engine.dialect.name != "postgresql":
        return
    t = model.__tablename__
    try:
        db.session.execute(db.text(
            f"SELECT setval(pg_get_serial_sequence('{t}','id'), "
            f"COALESCE((SELECT MAX(id) FROM {t}),0)+1, false)"))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"⚠ sequence sync failed for {t}: {e}")


def _next_local_id(company_id):
    m = db.session.query(db.func.max(Customer.local_id)).filter(
        Customer.company_id == company_id).scalar()
    return (m or 0) + 1


def create_customer(company_id, customer_name, email, **extra):
    """The ONLY way to create a customer row. Never sets id (the DB assigns it).
    Do not pass role= or is_active= in extra; they are set here."""
    c = Customer(
        company_id=company_id,
        local_id=_next_local_id(company_id),
        customer_name=customer_name,
        email=email,
        role='customer',
        is_active=True,
        **extra
    )
    db.session.add(c)
    db.session.flush()          
    return c


# -----------------------------
#  DB Create All + owner rows
# -----------------------------
def initialize_database():
    with app.app_context():
        db.create_all()

        try:
            owner_company = db.session.get(Company, OWNER_COMPANY_ID)
            if not owner_company:
                owner_company = Company(
                    id=OWNER_COMPANY_ID,            
                    name="לרקוד על הגג",
                    email=OWNER_USERNAME,
                    address="פרפר 15",
                    city="אילת , ישראל",
                    postal_code="88000",
                    company_id_number="123456789",
                    deduction_file="987654321",
                    phone="08-9996666",
                    translations_json="{}"
                )
                db.session.add(owner_company)
                db.session.flush()

            owner_user = User.query.filter_by(
                email=OWNER_USERNAME,
                company_id=OWNER_COMPANY_ID
            ).first()

            if not owner_user and OWNER_USERNAME and OWNER_PASSWORD:
                owner_user = User(                  
                    email=OWNER_USERNAME,
                    username="Owner",
                    company_id=OWNER_COMPANY_ID,
                    role="owner",
                    is_active=True,
                    is_approved=True
                )
                _set_user_password(owner_user, OWNER_PASSWORD)     
                db.session.add(owner_user)

            db.session.commit()

        except Exception as e:
            # two workers starting at the same time: the other one already created the rows
            db.session.rollback()
            print(f"⚠ Owner rows were created by another worker or failed: {e}")

        # Only the company table has a fixed id (the owner company, id=1),
        # so only its counter needs aligning. User and Customer ids are always automatic.
        sync_sequence(Company)
        sync_sequence(User)

        db.session.close()


initialize_database()

# ------------------------------------------------------
#   App From Web To Translations (Global i1n SaaS Core)
# ------------------------------------------------------

def get_lang():
    try:
        cookie_lang = request.cookies.get("lang")
        if cookie_lang:
            return cookie_lang.lower().strip()
    except:
        pass
    return "he"


def get_country():
    try:
        cookie_country = request.cookies.get("country")
        if cookie_country:
            return cookie_country.upper().strip()
    except:
        pass
    return "IL"


def py_i18n(key):
    lang = get_lang()
    path = os.path.join(BASE_DIR, "static", f"{lang}.json")

    try:
        if not os.path.exists(path):
            return key
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data.get(key, key)
    except Exception as e:
        print(f"Static translation file lookup error: {e}")
        return key


@app.route("/set_language/<lang>")
def set_language(lang):
    resp = make_response(redirect(request.referrer or url_for("home")))
    resp.set_cookie("lang", lang, max_age=60*60*24*365, path="/")
    return resp


@app.before_request
def global_language_sync_engine():
    active_cookie_lang = get_lang()
    
    session['language'] = active_cookie_lang
    session['lang'] = active_cookie_lang
    
    current_app.jinja_env.globals.update(
        language=active_cookie_lang,
        lang=active_cookie_lang,
        _lang=active_cookie_lang
    )


# ------------------------------------------------------
# שכבה 2: מנוע הדאטהבייס הדינמי (GoogleTranslator בריצה מקבילה ב-Threads)
# ------------------------------------------------------

import concurrent.futures
from translate import Translator 

def generate_translations(text, source_lang="he"):
    if not text or not str(text).strip():
        return {}

    text_str = str(text).strip()
    src = "he" if str(source_lang).lower().strip() in ["he", "iw", "auto"] else str(source_lang).lower().strip()

    languages = [
        "he","en","fr","es","de","ru","ar","zh-CN","ja","hi","pt","it","nl","sv",
        "tr","ko","pl","uk","fa","ro","cs","el","th","vi","bn","id","ms","tl",
        "hu","bg"
    ]
    result = {}

    def translate_single(lang):
        if lang == src:
            return lang, text_str
            
        try:
            translator = Translator(from_lang=src, to_lang=lang)
            translated = translator.translate(text_str)
            
            if translated and "MYMEMORY" not in str(translated):
                translated_clean = str(translated).strip()
                return lang, translated_clean
            
            return lang, text_str
        except Exception:
            return lang, text_str

    # הרצה מקבילית טהורה של המתרגם המקורי שלך על כל 30 השפות
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        translations = list(executor.map(translate_single, languages))

    for lang, translated_text in translations:
        result[lang] = translated_text

    # אם אנגלית נשארה בעברית או שהיא ריקה, נבצע לה פנייה ייעודית ונקייה
    en_val = result.get("en", "")
    if not en_val or any(c in en_val for c in "אבגדהוזחטיכלמנסעפצקרשת"):
        try:
            # פנייה מפורשת ומבודדת רק עבור אנגלית כדי להבטיח אותיות לטיניות
            single_translator = Translator(from_lang=src, to_lang="en")
            direct_en = single_translator.translate(text_str)
            if direct_en and "MYMEMORY" not in str(direct_en):
                result["en"] = str(direct_en).strip()
        except Exception:
            pass

    return result

# ----------------------
# FORMAT HELPERS
# ----------------------

def format_percent(value):
    try:
        return f"{float(value):.2f}%"
    except:
        return value

def format_phone(value):
    try:
        digits = re.sub(r"\D", "", str(value))
        if len(digits) == 10:
            return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
        if len(digits) == 9:
            return f"{digits[:2]}-{digits[2:5]}-{digits[5:]}"
        return value
    except:
        return value

def format_iban(value):
    try:
        clean = re.sub(r"\s+", "", value)
        return " ".join(clean[i:i+4] for i in range(0, len(clean), 4))
    except:
        return value

def format_vat(value):
    try:
        digits = re.sub(r"\D", "", str(value))
        if len(digits) == 9:
            return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
        return value
    except:
        return value

def format_round(value, decimals=2):
    try:
        return round(float(value), decimals)
    except:
        return value

# ----------------------
# DATE FORMAT (GLOBAL)
# ----------------------

def format_lang_date(date_value):
    if not date_value:
        return ""
    
    # 1. המרת סטרינג לאובייקט datetime אם צריך
    if isinstance(date_value, str):
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d"):
            try:
                date_value = datetime.strptime(date_value, fmt)
                break
            except ValueError:
                continue
        else:
            return date_value # אם לא הצליח להמיר, מחזיר את המקור

    # 2. זיהוי שפה
    lang = get_lang() 

    # 3. החלת פורמט לפי מדינה/שפה
    try:
        # פורמט סין, יפן, קוריאה (שנה-חודש-יום)
        if lang in ["zh", "ja", "ko"]:
            return date_value.strftime("%Y-%m-%d")
        
        # פורמט ארה"ב (חודש-יום-שנה)
        if lang == "en":
            return date_value.strftime("%m-%d-%Y")
        
        # פורמט ישראל ואירופה (יום-חודש-שנה)
        return date_value.strftime("%d-%m-%Y")
    except Exception as e:
        return str(date_value)

# ----------------------
# LOCALE & CURRENCY LOGIC
# ----------------------

def get_currency():
    cookie_currency = request.cookies.get("currency")
    if cookie_currency:
        return cookie_currency
    lang = get_lang()
    fallback_map = {
        "he": "ILS", "en": "USD", "fr": "EUR", "de": "EUR", "es": "EUR",
        "it": "EUR", "nl": "EUR", "pt": "EUR", "el": "EUR", "ro": "RON",
        "ru": "RUB", "tr": "TRY", "ar": "SAR", "zh": "CNY", "ja": "JPY",
        "hi": "INR", "ko": "KRW", "pl": "PLN", "uk": "UAH", "fa": "IRR",
        "cs": "CZK", "sv": "SEK", "th": "THB", "vi": "VND",
        "bn": "BDT", "id": "IDR", "ms": "MYR", "tl": "PHP", "hu": "HUF", "bg": "BGN"
    }
    return fallback_map.get(lang, "USD")

def get_locale():
    lang = get_lang()
    locale_map = {
        "he": "he_IL", "en": "en_US", "fr": "fr_FR", "de": "de_DE", "es": "es_ES",
        "it": "it_IT", "nl": "nl_NL", "pt": "pt_PT", "el": "el_GR", "ro": "ro_RO",
        "ru": "ru_RU", "tr": "tr_TR", "ar": "ar_SA", "zh": "zh_CN", "ja": "ja_JP",
        "hi": "hi_IN", "ko": "ko_KR", "pl": "pl_PL", "uk": "uk_UA", "fa": "fa_IR",
        "cs": "cs_CZ", "sv": "sv_SE", "th": "th_TH", "vi": "vi_VN",
        "bn": "bn_BD", "id": "id_ID", "ms": "ms_MY", "tl": "tl_PH", "hu": "hu_HU", "bg": "bg_BG"
    }
    return locale_map.get(lang, "en_US")

# ----------------------
#  SMART NUMBER & CURRENCY FORMATTERS
# ----------------------

# 1. קודם כל מגדירים את הפונקציות
def format_number_only(value):
    try:
        # זה יחזיר ১.৪১১,২০ בבנגלדש
        return babel.numbers.format_decimal(value, locale=get_locale())
    except:
        return "{:,.2f}".format(float(value))

def get_currency_symbol(code):
    symbols = {
        "ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥",
        "CNY": "¥", "RUB": "₽", "TRY": "₺", "SAR": "﷼", "INR": "₹",
        "KRW": "₩", "PLN": "zł", "UAH": "₴", "IRR": "﷼", "CZK": "Kč",
        "SEK": "kr", "THB": "฿", "VND": "₫", "HUF": "Ft", "BGN": "лв",
        "RON": "lei", "BDT": "৳", "IDR": "Rp", "MYR": "RM", "PHP": "₱"
    }
    return symbols.get(code, code)

def format_currency_custom(value, currency_code=None):
    # אם לא נשלח מטבע → אל תשתמש בשפה
    if currency_code is None:
        return format_number_only(value)

    symbol = get_currency_symbol(currency_code)
    formatted_num = format_number_only(value)
    return f"{formatted_num} {symbol}"





# ----------------------
# Getting Format All Percentage Currency : Form 
# ----------------------

def sanitize_input(value, is_percentage=False, allow_nan=True):
    try:
        # Handle None or empty values gracefully
        if not value:
            return 0.0 if allow_nan else "N/A"

        # Remove unwanted characters like ',' and '%', and trim spaces
        if isinstance(value, str):
            value = value.replace(",", "").replace("%", "").strip()

        # Convert the sanitized string to a float
        numeric_value = float(value)

        # Handle percentage-specific logic
        if is_percentage:
            # Convert decimal to percentage if it's between 0 and 1
            if 0 <= numeric_value <= 1:
                return numeric_value * 100
            return numeric_value  # Already a percentage

        return numeric_value  # Return sanitized float for non-percentage values

    except (ValueError, TypeError):
        # Gracefully handle invalid inputs
        return 0.0 if allow_nan else "N/A"

# ----------------------
# Getting Clean Number Value Format Helper  
# ----------------------

def clean_number(val, default=0.0):
    try:
        return clean_float(val)
    except Exception:
        return default


def clean_float(value):
    if value is None or value == "":
        return 0.0
    
    if isinstance(value, (float, int)):
        return float(value)

    s = str(value).strip()
    
    s = re.sub(r'[^\d,.\-]', '', s)
    
    if not s:
        return 0.0

    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'): # פורמט אירופאי 1.500,50
            s = s.replace('.', '').replace(',', '.')
        else: # פורמט אנגלי 1,500.50
            s = s.replace(',', '')
    
    elif ',' in s:
        if len(s.split(',')[1]) <= 2:
            s = s.replace(',', '.')
        else:
            s = s.replace(',', '')

    try:
        return float(s)
    except ValueError:
        return 0.0

# ------------------------------------------------------------------
#  מנוע עזרי תאריכים מבודד לשעון נוכחות (Clock-In display Isolation Helpers)
# ------------------------------------------------------------------

def format_date_for_display(date_str):
    """המרת YYYY-MM-DD ל- DD/MM/YYYY לצורך תצוגה חלקה"""
    try:
        return datetime.strptime(date_str, '%Y-%m-%d').strftime('%d/%m/%Y')
    except Exception:
        return date_str  

def format_date_for_input(date_str):
    """המרת DD/MM/YYYY ל- YYYY-MM-DD לצורך Inputs ב-HTML"""
    try:
        return datetime.strptime(date_str, '%d/%m/%Y').strftime('%Y-%m-%d')
    except Exception:
        return date_str

# ------------------------------------------------------------------
#  חישוב ימי החודש המדויק חסין קריסות (מיושר ב-100% לימי השבוע הראסמיים!)
# ------------------------------------------------------------------
def get_days_in_month(year, month):
    num_days = calendar.monthrange(int(year), int(month))[1]
    hebrew_days = ['ראשון', 'שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת']
    days_data = []

    for day in range(1, num_days + 1):
        date_obj = datetime(int(year), int(month), day)
        
        #  : קובע את ימי השבוע במדויק ללא שום סטיות!
        # בפייתון: ראשון=6, שני=0, שלישי=1... הנוסחה הזו הופכת את ראשון ל-0, שני ל-1 פלס!
        hebrew_day = hebrew_days[date_obj.isoweekday() % 7]
        formatted_date = date_obj.strftime('%d-%m-%Y')

        days_data.append({
            "day": hebrew_day,
            "date": formatted_date
        })

    return days_data

#  clock_format_date כדי שלא ידרוס את פילטר השפות הראשי של המערכת 
@app.template_filter('clock_format_date')
@login_required
def clock_format_date(value, fmt='%d/%m/%Y'):
    try:
        if isinstance(value, (datetime, date)):
            return value.strftime(fmt)
        return datetime.strptime(str(value), '%Y-%m-%d').strftime(fmt)
    except Exception:
        return value  

# ------------------------------------------------------------------
#  API Endpoint: עדכון חודש ושנה דינמי מהפרונט-אנד (POST)
# ------------------------------------------------------------------
@app.route('/update_month_year', methods=['POST'])
@login_required
def update_month_year():
    try:
        data = request.get_json() or {}
        year = data.get("employeeYear")
        month = data.get("employeeMonth")

        if not year or not month:
            return jsonify(success=False, message="Missing parameters"), 400

        session['employeeMonth'] = month
        session['employeeYear'] = year

        # שולף את רשימת הימים המיושרת והמתוקנת
        days_data = get_days_in_month(year, month)
        
        return jsonify(success=True, days_data=days_data)
    except Exception as e:
        print(f"❌ Error inside update_month_year API: {e}")
        return jsonify(success=False, message=str(e)), 500





# ----------------------
# REGISTER FILTERS
# ----------------------

app.jinja_env.filters["currency"] = format_currency_custom
app.jinja_env.filters["number"] = format_number_only
app.jinja_env.filters["lang_date"] = format_lang_date
app.jinja_env.filters["percent"] = format_percent
app.jinja_env.filters["phone"] = format_phone
app.jinja_env.filters["iban"] = format_iban
app.jinja_env.filters["vat"] = format_vat
app.jinja_env.filters["round"] = format_round

# ----------------------
# GLOBAL CONTEXT
# ----------------------

@app.context_processor
def inject_globals():
    return {
        "lang": get_lang(),
        "currency": get_currency(),
        "format_lang_date": format_lang_date,
        "format_number": format_number_only,
        "format_currency": format_currency_custom,
        "time": time
    }





# ---------------------------------------------------------
# Flask-Login: User Loader
# ---------------------------------------------------------

@login_manager.user_loader
def load_user(user_id):
    try:
        # the virtual owner always has user_id = 0
        if str(user_id) == "0":
            return OwnerUser(OWNER_USERNAME)
        return db.session.get(User, int(user_id))
    except Exception:
        return None


# ---------------------------------------------------------
#  Small helpers
# ---------------------------------------------------------

def _to_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _active_role():
    """Role of the logged in user: database role first, then the session."""
    try:
        if current_user.is_authenticated:
            db_role = (getattr(current_user, 'role', '') or '').strip().lower()
            if db_role:
                return db_role
    except Exception:
        pass
    return (session.get('role') or '').strip().lower()


def _forbidden_page():
    msg = escape(py_i18n('auth.no_permission'))
    return (
        f'<div dir="rtl" style="font-family:Arial;padding:40px">'
        f'<h1>{msg}</h1>'
        f'<p><a href="{url_for("login")}">התחברות מחדש</a></p></div>'
    )


# ---------------------------------------------------------
#  Owner detection
#  The owner is: someone who logged in with the owner password (session flag),
#  the virtual OwnerUser, or a DB user with role "owner" inside the owner company.
#  The e-mail address alone NEVER makes a user an owner.
# ---------------------------------------------------------

def is_owner():
    try:
        if session.get('owner_access') is True:
            return True

        if not current_user.is_authenticated:
            return False

        real_user = current_user._get_current_object()
        if isinstance(real_user, OwnerUser):
            return True

        return (
            (getattr(real_user, 'role', '') or '').strip().lower() == 'owner'
            and getattr(real_user, 'company_id', None) == OWNER_COMPANY_ID
        )
    except Exception:
        return False


# ---------------------------------------------------------
#  Decorators (Owner & Tenant Secure)
# ---------------------------------------------------------

def OWNER_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_owner():
            flash(py_i18n('auth.owner_only'), 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        if not current_user or not current_user.is_authenticated:
            flash(py_i18n("auth.login_required"), "danger")
            return redirect(url_for('login'))

        if not getattr(current_user, 'is_active', False) or getattr(current_user, 'is_approved', None) is False:
            flash(py_i18n("login.access_expired_or_inactive"), "danger")
            return redirect(url_for('login'))

        # access expiry applies to EVERY role (manager, employee, customer)
        if hasattr(current_user, 'has_valid_access') and not current_user.has_valid_access():
            flash(py_i18n("login.access_expired_or_inactive"), "danger")
            return redirect(url_for('login'))

        return f(*args, **kwargs)
    return decorated_function


def manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        if _active_role() != 'manager':
            flash(py_i18n("auth.manager_only"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def employee_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        if _active_role() != 'employee':
            flash(py_i18n("auth.employee_only"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def customer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        if _active_role() != 'customer':
            flash(py_i18n("auth.customer_only"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def customer_self_only(f):
    """A customer may open ONLY his own customer id. Owner and manager are not limited."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        role = _active_role()

        # a manager works with all customers of his own company
        if role == 'manager':
            return f(*args, **kwargs)

        if role == 'customer':
            own_id = _to_int(session.get('customer_id'))
            if own_id is None:
                own_id = _to_int(getattr(current_user, 'customer_id', None))

            # every place where a customer id can arrive: URL path, query string, form
            sources = (kwargs, request.view_args or {}, request.args, request.form)
            provided = [s.get('customer_id') for s in sources]
            provided = [p for p in provided if p not in (None, '')]

            # deny when there is no own id, no id was sent, or ANY sent id is not his own
            if own_id is None or not provided or any(_to_int(p) != own_id for p in provided):
                flash(py_i18n("auth.customer_self_only"), "danger")
                return redirect(url_for('unauthorized'))

        return f(*args, **kwargs)
    return decorated_function


def customer_or_manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        if _active_role() not in ['customer', 'manager', 'employee']:
            flash(py_i18n("auth.no_permission"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------
# Unauthorized Redirect (with a loop guard)
# ---------------------------------------------------------

@app.route('/unauthorized')
def unauthorized():
    try:
        role = _active_role()

        # If this page was already reached a moment ago, the page we redirected to
        # sent the user back here. Stop redirecting, otherwise: endless loop.
        now = time.time()
        looping = (now - float(session.get('_unauth_ts', 0) or 0)) < 3
        session['_unauth_ts'] = now

        if not looping:
            if is_owner() or role == 'manager':
                return redirect(url_for('invoice'))
            if role == 'employee':
                return redirect(url_for('clock_in_out'))
            if role == 'customer':
                return redirect(url_for('customer_dashboard_router'))

        return _forbidden_page(), 403
    except Exception as e:
        print(f"❌ Error in unauthorized navigation redirect handler: {e}")
        return _forbidden_page(), 403


# =============================================================
#  AUTH + USERS (Multi-Tenant Secure) - one block, no duplicates
#  login, logout, register, clients, update_access, reset password,
#  delete_selected_users, update_role
# =============================================================

# -----------------------------
# Login Route (Multi-Tenant Secure Version)
# -----------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    try:
        if request.method == 'GET':
            session.clear()
            return render_template('login.html')

        email         = request.form.get('email', '').strip()
        company_email = request.form.get('company_email', '').strip()
        login_email   = email if email else company_email
        password      = request.form.get('password', '').strip()
        login_email_l = login_email.lower()

        db.session.expire_all()

        # ---------------- OWNER LOGIN ----------------
        if (OWNER_USERNAME and OWNER_PASSWORD
                and login_email_l == OWNER_USERNAME.strip().lower()
                and secrets.compare_digest(password, OWNER_PASSWORD)):
            for key in list(session.keys()):
                if not key.startswith('_'):
                    session.pop(key, None)

            login_user(OwnerUser(OWNER_USERNAME), force=True)

            session['owner_access']  = True
            session['user_id']       = 0
            session['role']          = 'owner'
            session['user_role']     = 'owner'
            session['company_id']    = OWNER_COMPANY_ID
            session['company_name']  = "לרקוד על הגג"
            session['company_email'] = OWNER_USERNAME
            session['customer_id']   = None

            try:
                owner_db_record = User.query.filter_by(
                    email=OWNER_USERNAME, company_id=OWNER_COMPANY_ID).first()
                if owner_db_record:
                    owner_db_record.last_login = datetime.utcnow()
                    db.session.commit()
            except Exception:
                db.session.rollback()

            db.session.close()
            print("✔ LOGIN OK: owner")
            flash(py_i18n('login.owner_success'), 'success')
            return redirect(url_for('invoice'))

        # ---------------- USER LOGIN ----------------
        user = None

        if company_email:
            target_company = Company.query.filter(
                db.func.lower(Company.email) == company_email.lower()).first()
            if target_company:
                for potential_user in User.query.filter(
                        db.func.lower(User.email) == login_email_l,
                        User.company_id == target_company.id).all():
                    if _check_user_password(potential_user, password):
                        user = potential_user
                        break

        if not user:
            for potential_user in User.query.filter(
                    db.func.lower(User.email) == login_email_l).all():
                if _check_user_password(potential_user, password):
                    user = potential_user
                    break

        if not user:
            matches = User.query.filter(db.func.lower(User.email) == login_email_l).all()
            print(f"⛔ LOGIN FAILED: typed_email='{login_email_l}' company_email_field='{company_email}' "
                  f"users_with_this_email={len(matches)} "
                  f"[(id, role, company, has_password, active, approved)] = "
                  f"{[(u.id, u.role, u.company_id, bool((u.password_hash or '').strip()), u.is_active, u.is_approved) for u in matches]}")
            return _login_error_page("אימייל או סיסמה שגויים")

        if not user.is_active or (user.is_approved is False):
            print(f"⛔ LOGIN BLOCKED: user {user.id} is_active={user.is_active} is_approved={user.is_approved}")
            return _login_error_page("החשבון אינו פעיל או אינו מאושר")

        if user.access_expires_at and user.access_expires_at < datetime.utcnow():
            print(f"⛔ LOGIN BLOCKED: user {user.id} access expired at {user.access_expires_at}")
            return _login_error_page("תוקף הגישה פג")

        for key in list(session.keys()):
            if not key.startswith('_'):
                session.pop(key, None)

        login_user(user, force=True)

        role = (user.role or '').strip().lower() or 'manager'

        session['user_id']    = user.id
        session['role']       = role
        session['user_role']  = role
        session['company_id'] = user.company_id

        if user.company_id:
            company_obj = db.session.get(Company, user.company_id)
            if company_obj:
                session['company_name']  = company_obj.name
                session['company_email'] = company_obj.email

        if role == 'customer':
            cust_id = user.customer_id
            if not cust_id:
                c = Customer.query.filter_by(
                    company_id=user.company_id, email=user.email).first()
                cust_id = c.id if c else None
            session['customer_id'] = cust_id
        else:
            session['customer_id'] = None

        print(f"✔ LOGIN OK: user={user.id} role={role} company={user.company_id}")
        user.last_login = datetime.utcnow()
        db.session.commit()

        if role == 'manager':
            db.session.close()
            flash(py_i18n('login.manager_success'), 'success')
            return redirect(url_for('company'))

        if role == 'employee':
            employee_profile = EmployeeData.query.filter_by(
                employee_id=user.id, company_id=user.company_id).first()
            if not employee_profile:
                employee_profile = EmployeeData.query.filter_by(
                    email=user.email, company_id=user.company_id).first()

            session['employee_id']   = employee_profile.id if employee_profile else user.id
            session['employee_name'] = employee_profile.employee_name if employee_profile else user.username

            db.session.close()
            flash(py_i18n('login.success'), 'success')
            return redirect(url_for('employee_dashboard'))

        if role == 'customer':
            db.session.close()
            flash(py_i18n('login.success'), 'success')
            return redirect(url_for('customer_dashboard_router'))

        db.session.close()
        flash(py_i18n('login.success'), 'success')
        return redirect(url_for('invoice'))

    except Exception:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        return redirect(url_for('login'))


# -----------------------------
# Logout
# -----------------------------

@app.route('/logout', methods=['POST'])
def logout():
    try:
        logout_user()
        session.clear()
        db.session.close()
        flash(py_i18n("auth.logout_success"), "info")
    except Exception as e:
        print(f"⚠️ Warning inside logout session clear: {e}")

    return redirect(url_for('login'))


# -----------------------------
# Register helpers
# -----------------------------

def _login_fresh(user, **extra):
    """New session for this user. Order matters: clear FIRST, then login_user
    (a clear() after login_user logs the user out on the next request)."""
    session.clear()
    login_user(user, force=True)

    role = (user.role or '').strip().lower() or 'manager'
    session['user_id']     = user.id
    session['role']        = role
    session['user_role']   = role
    session['company_id']  = user.company_id
    session['customer_id'] = None

    company_obj = db.session.get(Company, user.company_id) if user.company_id else None
    if company_obj:
        session['company_name']  = company_obj.name
        session['company_email'] = company_obj.email

    session.update(extra)


def _translate_new_company(co):
    try:
        translate_company_in_background(
            company_id=co.id, name=co.name,
            id_number=co.company_id_number or "", deduction_file=co.deduction_file or "",
            address=co.address or "", city=co.city or "",
            postal_code=co.postal_code or "", phone=co.phone or "",
            email=co.email or "", logo="", source_lang=get_lang())
    except Exception as e:
        print(f"⚠ Company translation start failed: {e}")


def _translate_new_customer(c):
    try:
        translate_customer_in_background(
            customer_id=c.id, company_id=c.company_id,
            name=c.customer_name, address=c.address, city=c.city,
            message=c.message, source_lang=get_lang())
    except Exception as e:
        print(f"⚠ Customer translation start failed: {e}")


# -----------------------------
# Register (Multi-Tenant Secure Engine)
# -----------------------------

@app.route('/register', methods=['POST'])
def register():
    try:
        username      = request.form.get('username', '').strip()
        email         = request.form.get('email', '').strip().lower()
        password      = request.form.get('password', '').strip()
        role          = (request.form.get('role') or 'manager').strip().lower()
        company_email = request.form.get('company_email', '').strip()

        if not email or not password:
            flash(py_i18n('auth.register_missing_fields'), 'warning')
            return redirect(url_for('login'))

        if OWNER_USERNAME and email == OWNER_USERNAME.strip().lower():
            flash(py_i18n('auth.register_email_exists'), 'warning')
            return redirect(url_for('login'))

        # =====================================================
        # 1) MANAGER - opens a brand new company
        # =====================================================
        if role == 'manager':
            if User.query.filter_by(email=email, role='manager').first():
                flash(py_i18n('auth.register_email_exists'), 'warning')
                return redirect(url_for('login'))

            if Company.query.filter_by(email=email).first():
                flash("חומת אש: כתובת אימייל זו כבר רשומה במערכת כעסק פעיל!", "danger")
                return redirect(url_for('login'))

            company_name = username if username else email
            if Company.query.filter_by(name=company_name).first():
                flash(py_i18n("auth.company_name_taken").format(name=company_name), "danger")
                return redirect(url_for('login'))

            new_company = Company(name=company_name, email=email, translations_json="{}")
            db.session.add(new_company)
            db.session.flush()

            user = User(
                email=email,
                username=(username or email)[:80],
                role='manager',
                company_id=new_company.id,
                is_active=True,
                is_approved=True,
                access_expires_at=datetime.utcnow() + timedelta(days=30)
            )
            _set_user_password(user, password)
            db.session.add(user)
            db.session.flush()

            # the new company is a customer of the owner company
            platform_customer = create_customer(
                OWNER_COMPANY_ID, company_name, email,
                date=datetime.today().strftime('%d/%m/%Y'))

            db.session.commit()

            _translate_new_company(new_company)
            _translate_new_customer(platform_customer)

            _login_fresh(user)

            flash(py_i18n("auth.register_manager_success"), "success")
            return redirect(url_for('invoice'))

        # =====================================================
        # 2) CUSTOMER - customer of an existing company
        # =====================================================
        elif role == 'customer':
            if not company_email:
                flash(py_i18n("auth.company_email_required"), "danger")
                return redirect(url_for('login'))

            company = Company.query.filter(
                db.func.lower(Company.email) == company_email.lower()).first()
            if not company:
                flash(py_i18n("auth.company_not_found"), "danger")
                return redirect(url_for('login'))

            if company.id == OWNER_COMPANY_ID:
                flash("רישום לקוחות ישירות לחברת הניהול חסום! אנא הזן אימייל של חברה עצמאית", "danger")
                return redirect(url_for('login'))

            existing_user_in_company = User.query.filter_by(
                email=email, company_id=company.id).first()

            # ---- A) the user already exists (created by the manager) and has no password yet ----
            if existing_user_in_company:
                if not (existing_user_in_company.password_hash or '').strip():
                    _set_user_password(existing_user_in_company, password)
                    existing_user_in_company.username = (
                        username or existing_user_in_company.username or email)[:80]
                    existing_user_in_company.is_active = True
                    existing_user_in_company.is_approved = True
                    existing_user_in_company.role = 'customer'

                    customer = Customer.query.filter_by(
                        company_id=company.id, email=email).first()
                    if not customer:
                        customer = create_customer(
                            company.id, username or email, email,
                            date=datetime.today().strftime('%d/%m/%Y'), id_number="")
                    else:
                        customer.is_active = True
                        if username:
                            customer.customer_name = username
                    existing_user_in_company.customer_id = customer.id

                    db.session.commit()
                    _translate_new_customer(customer)

                    _login_fresh(existing_user_in_company,
                                 customer_id=customer.id,
                                 customer_name=customer.customer_name,
                                 customer_email=existing_user_in_company.email)

                    flash("חשבונך אומת והסיסמה הוגדרה בהצלחה!", "success")
                    return redirect(url_for('customer_dashboard_router'))

                flash("הנך כבר רשום כמשתמש בחברה זו, אנא התחבר", "warning")
                return redirect(url_for('login'))

            # ---- B) a brand new user ----
            pre_created_customer = Customer.query.filter_by(
                email=email, company_id=company.id).first()

            if not pre_created_customer:
                if Customer.query.filter_by(
                        customer_name=username, company_id=company.id).first():
                    flash("שם לקוח זה כבר תפוס בחברה זו, אנא בחר שם אחר", "warning")
                    return redirect(url_for('login'))

            user = User(
                email=email,
                username=(username or email)[:80],
                role='customer',
                company_id=company.id,
                is_active=True,
                is_approved=True,
                access_expires_at=None
            )
            _set_user_password(user, password)
            db.session.add(user)
            db.session.flush()

            new_customer = pre_created_customer
            if new_customer:
                new_customer.customer_name = username or new_customer.customer_name or email
                new_customer.is_active = True
            else:
                new_customer = create_customer(
                    company.id, username or email, email,
                    date=datetime.today().strftime('%d/%m/%Y'), id_number="")
            user.customer_id = new_customer.id
            db.session.commit()

            _translate_new_customer(new_customer)

            _login_fresh(user,
                         customer_id=new_customer.id,
                         customer_name=new_customer.customer_name,
                         customer_email=user.email)

            flash(py_i18n("auth.register_customer_success"), "success")
            return redirect(url_for('customer_dashboard_router'))

        # =====================================================
        # 3) EMPLOYEE - employee of an existing company
        # =====================================================
        elif role == 'employee':
            link_company_id = request.args.get('comp')

            if link_company_id:
                assigned_company_id = _to_int(link_company_id)
                if not assigned_company_id:
                    flash(py_i18n("auth.company_not_found"), "danger")
                    return redirect(url_for('login'))
            elif current_user.is_authenticated and session.get('company_id'):
                assigned_company_id = session.get('company_id')
            elif company_email:
                searched_company = Company.query.filter(
                    db.func.lower(Company.email) == company_email.lower()).first()
                if not searched_company:
                    flash(py_i18n("auth.company_not_found"), "danger")
                    return redirect(url_for('login'))
                assigned_company_id = searched_company.id
            else:
                flash("רישום עובד מחייב קישור לחברה פעילה או אימייל חברה!", "danger")
                return redirect(url_for('login'))

            if assigned_company_id == OWNER_COMPANY_ID:
                flash("רישום עובדים לחברת הניהול חסום!", "danger")
                return redirect(url_for('login'))

            if User.query.filter_by(email=email, company_id=assigned_company_id).first():
                flash(py_i18n('auth.register_email_exists'), 'warning')
                return redirect(url_for('login'))

            emp_company = db.session.get(Company, assigned_company_id)
            if not emp_company:
                flash(py_i18n("auth.company_not_found"), "danger")
                return redirect(url_for('login'))

            user = User(
                email=email,
                username=(username or email)[:80],
                role='employee',
                company_id=assigned_company_id,
                is_active=True,
                is_approved=True,
                access_expires_at=datetime.utcnow() + timedelta(days=30)
            )
            _set_user_password(user, password)
            db.session.add(user)
            db.session.commit()

            # a manager who adds an employee stays logged in as the manager
            if current_user.is_authenticated and session.get('role') == 'manager':
                flash(f"העובד {user.username} נרשם בהצלחה תחת החברה שלך!", "success")
                return redirect(url_for('invoice'))

            _login_fresh(user)

            flash(py_i18n("auth.register_employee_success"), "success")
            return redirect(url_for('invoice'))

        flash(py_i18n("auth.register_invalid_role"), "danger")
        return redirect(url_for('login'))

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("Registration error", "danger")
        return redirect(url_for('login'))


# -----------------------------------------------------------
#  Page Clients / Users Management (Multi-Tenant Secure Unified View)
# -----------------------------------------------------------
ALLOWED_ROLES = ('manager', 'customer', 'employee')


def _customer_for_user(user):
    """The customer row of a login user (never matched by id == id)."""
    if user is None:
        return None
    c = None
    if getattr(user, 'customer_id', None):
        c = Customer.query.filter_by(id=user.customer_id, company_id=user.company_id).first()
    if not c and user.email:
        c = Customer.query.filter(
            Customer.company_id == user.company_id,
            db.func.lower(Customer.email) == user.email.lower()).first()
    return c


def _manager_for_owner_customer(c):
    """The manager user behind a customer row of the owner company."""
    if not c.email:
        return None
    matches = User.query.filter(
        db.func.lower(User.email) == c.email.lower(),
        User.role == 'manager',
        User.company_id != OWNER_COMPANY_ID).all()
    if len(matches) > 1:
        for m in matches:
            comp = db.session.get(Company, m.company_id)
            if comp and comp.name == c.customer_name:
                return m
    return matches[0] if matches else None


def _find_user_for_admin(email, company_id, owner_mode, active_company_id):
    """Returns (user, error). error is None, 'not_found' or 'ambiguous'."""
    email = (email or '').strip().lower()
    if not email:
        return None, 'not_found'
    q = User.query.filter(db.func.lower(User.email) == email)
    if owner_mode:
        if company_id and str(company_id).isdigit():
            q = q.filter(User.company_id == int(company_id))
        users = q.all()
        if len(users) > 1:
            return None, 'ambiguous'
        return (users[0], None) if users else (None, 'not_found')
    u = q.filter(User.company_id == active_company_id).first()
    return (u, None) if u else (None, 'not_found')


def _drop_customer_rows(rows):
    """Delete customer rows. Rows that already have invoices are only deactivated.
    Returns the folders to remove AFTER a successful commit."""
    folders = []
    for c in rows:
        if c.invoices:
            c.is_active = False
        else:
            if c.local_id:
                folders.append(ensure_customer_folder(c.company_id, c.local_id))
            db.session.delete(c)
    return folders


def _access_info(u, now_dt):
    """(seconds_left, expired)"""
    if u and u.access_expires_at:
        delta = (u.access_expires_at - now_dt).total_seconds()
        return max(0, int(delta)), delta <= 0
    return None, False


def _existing_columns():
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(db.engine)
    return {t: {c['name'] for c in insp.get_columns(t)} for t in insp.get_table_names()}


@app.route('/clients', methods=['GET'])
@login_required
def clients():
    try:
        language = get_lang()
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)

        user_role = (getattr(current_user, 'role', '') or '').lower() or (session.get('role') or '').lower()
        owner_mode = (user_role == 'owner') or (session.get('owner_access') is True)
        active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        view_clients = []
        company_obj = None

        # ============================ OWNER ============================
        if owner_mode:
            owner_customers = (
                Customer.query
                .filter(Customer.company_id == OWNER_COMPANY_ID,
                        Customer.customer_name != SELF_CUSTOMER_NAME)
                .order_by(Customer.id.desc())
                .all()
            )

            processed_user_ids = set()

            for c in owner_customers:
                u = _manager_for_owner_customer(c)

                created_at = u.created_at if u else None
                last_login = u.last_login if u else None
                seconds_left = None
                status_label = "employee.customer_role"
                role_label = 'customer'
                display_company_name = "חברת OWNER"

                if u:
                    processed_user_ids.add(u.id)
                    role_label = u.role
                    status_label = "employee.status_active" if u.is_active else "employee.status_blocked"
                    seconds_left, expired = _access_info(u, now_dt)
                    if expired:
                        status_label = "employee.status_expired"
                    comp_info = db.session.get(Company, u.company_id) if u.company_id else None
                    if comp_info:
                        display_company_name = comp_info.name

                emp_profile = None
                if u and c.email:
                    emp_profile = EmployeeData.query.filter_by(
                        email=c.email, company_id=u.company_id).first()

                try:
                    trans_cust = load_customer_translated(c, language, company_id=OWNER_COMPANY_ID) or {}
                    translated_name = trans_cust.get("name")
                except Exception:
                    translated_name = None

                display_name = (
                    translated_name or
                    (emp_profile.employee_name if emp_profile else None) or
                    (u.username if u else None) or
                    c.customer_name or c.email or "No Name"
                )

                role_check = (role_label or '').lower()
                view_clients.append({
                    'type': 'manager' if role_check == 'manager' else ('employee' if role_check == 'employee' else 'customer'),
                    'id': u.id if u else f"c{c.id}",
                    'username': display_name,
                    'email': c.email or (u.email if u else None) or "ללא אימייל",
                    'role': role_label,
                    'company_name': display_company_name,
                    'created_at': created_at,
                    'last_login': last_login,
                    'status_label': status_label,
                    'seconds_left': seconds_left
                })

            managers_and_employees = (
                User.query
                .filter(User.company_id == OWNER_COMPANY_ID,
                        User.role.in_(['manager', 'employee', 'Manager', 'Employee']))
                .order_by(User.id.desc())
                .all()
            )

            for m in managers_and_employees:
                if m.id in processed_user_ids:
                    continue

                seconds_left, expired = _access_info(m, now_dt)

                if (m.role or '').lower() == 'employee':
                    display_company_name = "עובד פלטפורמה - חברה 1"
                    status_type = 'employee'
                    display_status_label = "employee.company_employee" if m.is_active else "employee.status_blocked"
                else:
                    display_company_name = "לקוח של הפלטפורמה"
                    status_type = 'manager'
                    display_status_label = "employee.status_active" if m.is_active else "employee.status_blocked"

                comp_info = db.session.get(Company, m.company_id) if m.company_id else None
                if comp_info:
                    display_company_name = (
                        load_company_translated(comp_info, language).get("name") or comp_info.name)

                if expired:
                    display_status_label = "employee.status_expired"

                emp_profile_m = EmployeeData.query.filter_by(
                    email=m.email, company_id=m.company_id).first() if m.email else None

                try:
                    trans_emp = load_employee_translated(
                        emp_profile_m, language, company_id=m.company_id) if emp_profile_m else {}
                    translated_name_m = trans_emp.get("name")
                except Exception:
                    translated_name_m = None

                display_name_m = (
                    translated_name_m or
                    (emp_profile_m.employee_name if emp_profile_m else None) or
                    m.username or m.email or "No Name"
                )

                view_clients.append({
                    'type': status_type,
                    'id': m.id,
                    'username': display_name_m,
                    'email': m.email or "ללא אימייל",
                    'role': m.role,
                    'company_name': display_company_name,
                    'created_at': m.created_at,
                    'last_login': m.last_login,
                    'status_label': display_status_label,
                    'seconds_left': seconds_left
                })

            company_obj = db.session.get(Company, OWNER_COMPANY_ID)

        # ============================ MANAGER ============================
        elif user_role == 'manager':
            if not active_company_id:
                flash(py_i18n("auth.no_company_assigned"), "danger")
                return redirect(url_for('invoice'))

            company_obj = db.session.get(Company, active_company_id)
            if not company_obj:
                flash("חברה לא נמצאה במערכת", "danger")
                return redirect(url_for('login'))

            company_records = (
                Customer.query
                .filter(Customer.company_id == active_company_id,
                        Customer.customer_name != SELF_CUSTOMER_NAME)
                .order_by(Customer.id.desc())
                .all()
            )

            processed_emails = set()

            for c in company_records:
                u = User.query.filter(
                    db.func.lower(User.email) == c.email.lower(),
                    User.company_id == active_company_id).first() if c.email else None

                created_at = c.date if hasattr(c, 'date') else (u.created_at if u else None)
                last_login = u.last_login if u else None

                if c.email:
                    processed_emails.add(c.email)

                is_emp = (u and (u.role or '').lower() == 'employee') or (getattr(c, 'role', 'customer') == 'employee')
                display_type = 'employee' if is_emp else 'customer'

                emp_profile = EmployeeData.query.filter_by(
                    email=c.email, company_id=active_company_id).first() if c.email else None

                try:
                    trans_cust = load_customer_translated(c, language, company_id=active_company_id) or {}
                    translated_name = trans_cust.get("name")
                except Exception:
                    translated_name = None

                display_name = (
                    translated_name or
                    (emp_profile.employee_name if emp_profile else None) or
                    (u.username if u else None) or
                    c.customer_name or c.email or "No Name"
                )

                seconds_left = None
                if u:
                    status_label = "employee.status_active" if u.is_active else "employee.status_blocked"
                    seconds_left, expired = _access_info(u, now_dt)
                    if expired:
                        status_label = "employee.status_expired"
                else:
                    status_label = "employee.employee_role" if is_emp else "employee.customer_role"

                view_clients.append({
                    'type': display_type,
                    'id': u.id if u else f"c{c.id}",
                    'username': display_name,
                    'email': c.email or "ללא אימייל",
                    'role': display_type,
                    'company_name': company_obj.name,
                    'created_at': created_at,
                    'last_login': last_login,
                    'status_label': status_label,
                    'seconds_left': seconds_left
                })

            company_employees_users = User.query.filter_by(
                company_id=active_company_id, role='employee').all()

            for emp_user in company_employees_users:
                if emp_user.email in processed_emails:
                    continue

                seconds_left, expired = _access_info(emp_user, now_dt)

                emp_profile = EmployeeData.query.filter_by(
                    email=emp_user.email, company_id=active_company_id).first()

                try:
                    trans_emp = load_employee_translated(
                        emp_profile, language, company_id=active_company_id) if emp_profile else {}
                    translated_name_e = trans_emp.get("name")
                except Exception:
                    translated_name_e = None

                display_name = translated_name_e or (
                    emp_profile.employee_name if emp_profile
                    else (emp_user.username or emp_user.email or "עובד חדש"))

                status_label = "employee.status_active" if emp_user.is_active else "employee.status_blocked"
                if expired:
                    status_label = "employee.status_expired"

                view_clients.append({
                    'type': 'employee',
                    'id': emp_user.id,
                    'username': display_name,
                    'email': emp_user.email or "ללא אימייל",
                    'role': 'employee',
                    'company_name': company_obj.name,
                    'created_at': emp_user.created_at,
                    'last_login': emp_user.last_login,
                    'status_label': status_label,
                    'seconds_left': seconds_left
                })
        else:
            return redirect(url_for('unauthorized'))

        # ---------------- i18n lists for the page ----------------
        if owner_mode:
            all_employees_db = EmployeeData.query.all()
            all_customers_db = Customer.query.filter(
                Customer.customer_name != SELF_CUSTOMER_NAME).all()
        else:
            all_employees_db = EmployeeData.query.filter_by(company_id=active_company_id).all()
            all_customers_db = Customer.query.filter(
                Customer.company_id == active_company_id,
                Customer.customer_name != SELF_CUSTOMER_NAME).all()

        employee_i18n_list = {}
        for emp in all_employees_db:
            try:
                trans_emp = load_employee_translated(emp, language, company_id=emp.company_id) or {}
            except Exception:
                trans_emp = {}
            key_emp = str(emp.local_id) if getattr(emp, "local_id", None) else f"user_{emp.user_id or emp.id}"
            employee_i18n_list[key_emp] = {"name": trans_emp.get("name") or emp.employee_name or ""}

        customer_i18n_list = {}
        for cust in all_customers_db:
            try:
                trans_cust = load_customer_translated(cust, language, company_id=cust.company_id) or {}
            except Exception:
                trans_cust = {}
            key_cust = f"{cust.company_id}_{cust.local_id}" if getattr(cust, "local_id", None) else f"user_{cust.id}"
            customer_i18n_list[key_cust] = {"name": trans_cust.get("name") or cust.customer_name or ""}

        for u_item in view_clients:
            for k in ('created_at', 'last_login'):
                v = u_item[k]
                u_item[k] = v.strftime('%d/%m/%Y %H:%M') if (v and hasattr(v, 'strftime')) else str(v or '')

        html = render_template(
            'clients.html',
            users=view_clients,
            is_owner=owner_mode,
            employee_i18n_list=employee_i18n_list,
            customer_i18n_list=customer_i18n_list,
            company=load_company_translated(company_obj, language) if company_obj else {},
            company_db=company_obj,
            language=language
        )
        db.session.close()
        return html

    except Exception:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        flash("שגיאה בטעינת דף הלקוחות והעובדים", "danger")
        return redirect(url_for('invoice'))


# -----------------------------
# Update Access (Owner / Super Admin only)
# -----------------------------

@app.route('/update_access', methods=['POST'])
@login_required
def update_access():
    try:
        email      = request.form.get('email', '').strip()
        status     = request.form.get('status', '').strip().lower()
        duration   = request.form.get('duration', '').strip()
        company_id = request.form.get('company_id')

        current_lang = request.form.get('lang') or request.args.get('lang') or get_lang()

        user_role = (getattr(current_user, 'role', '') or '').lower() or (session.get('role') or '').lower()
        owner_mode = (user_role == 'owner') or (session.get('owner_access') is True)
        active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        db.session.expire_all()

        user, err = _find_user_for_admin(email, company_id, owner_mode, active_company_id)

        if err == 'ambiguous':
            flash("האימייל קיים בכמה חברות, יש לבחור חברה", "warning")
            return redirect(url_for('clients', lang=current_lang))
        if not user:
            flash(py_i18n("client.not_found"), "danger")
            return redirect(url_for('clients', lang=current_lang))

        target_role = (user.role or '').strip().lower()

        # nobody can change the owner account
        if target_role == 'owner':
            flash(py_i18n("auth.no_permission"), "danger")
            return redirect(url_for('clients', lang=current_lang))

        # a manager can touch only employees / customers of the same company
        if not owner_mode:
            if target_role in ['manager', 'owner'] or user.company_id != active_company_id:
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients', lang=current_lang))

        # ---- status ----
        is_status_active = status in ['active', 'approved', '1', 'true', 'מאושר', 'פעיל']
        user.is_active   = is_status_active
        user.is_approved = is_status_active

        # ---- access duration (empty = no limit) ----
        if duration and duration.isdigit():
            user.access_expires_at = (
                datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=int(duration)))
        else:
            user.access_expires_at = None

        search_company_id = user.company_id if user.company_id else active_company_id
        now = datetime.now()
        current_year = now.strftime('%Y')
        current_month = now.strftime('%m')

        # ---- customer row sync (never matched by id) ----
        if target_role == 'manager':
            customer = Customer.query.filter(
                Customer.company_id == OWNER_COMPANY_ID,
                db.func.lower(Customer.email) == (user.email or '').lower()).first()
        else:
            customer = _customer_for_user(user)

        if customer:
            customer.is_active = user.is_active
            if not customer.customer_name:
                customer.customer_name = user.username or user.email or "משתמש ללא שם"

        # ---- employee row sync ----
        employee_profile = EmployeeData.query.filter_by(
            email=user.email, company_id=search_company_id).first()
        if employee_profile and hasattr(employee_profile, 'is_active'):
            employee_profile.is_active = user.is_active

        db.session.commit()

        # ---- disk sync ----
        try:
            if target_role == 'employee' and employee_profile:
                target_folder, json_file_path, _ = get_company_clock_paths(
                    search_company_id, employee_profile.id, current_year, current_month)
                os.makedirs(target_folder, exist_ok=True)

                existing_data = {}
                if os.path.isfile(json_file_path):
                    try:
                        with open(json_file_path, "r", encoding="utf-8") as f:
                            existing_data = json.load(f)
                    except Exception:
                        existing_data = {}

                existing_data.setdefault("hours_table", {"work_day_entries": [], "tax": {}})
                existing_data["is_active"] = user.is_active
                existing_data["email"] = user.email
                existing_data["employee_name"] = employee_profile.employee_name

                with open(json_file_path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=2)

            elif target_role == 'customer' and customer and customer.local_id:
                folder = ensure_customer_folder(search_company_id, customer.local_id)
                os.makedirs(folder, exist_ok=True)
                file_path = os.path.join(folder, "customer.json")

                existing_data = {}
                if os.path.isfile(file_path):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            existing_data = json.load(f)
                    except Exception:
                        existing_data = {}

                for k in ("name", "address", "city", "message"):
                    existing_data.setdefault(k, {})
                existing_data["is_active"] = user.is_active

                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=4)

        except Exception as file_err:
            print(f"⚠️ Disk sync skipped: {file_err}")

        db.session.close()
        flash(py_i18n("client.access_updated"), "success")
        return redirect(url_for('clients', lang=current_lang))

    except Exception:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        flash("שגיאה חמורה בעדכון הרשאות הגישה", "danger")
        return redirect(url_for('clients', lang=get_lang()))


# -----------------------------
# Create Reset Token (Helper)
# -----------------------------

def create_reset_token(user, hours=1):
    try:
        PasswordResetToken.query.filter_by(user_id=user.id).delete(synchronize_session='fetch')

        token = secrets.token_urlsafe(32)
        entry = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=datetime.utcnow() + timedelta(hours=hours)
        )
        db.session.add(entry)
        db.session.commit()
        return token

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        raise


# -----------------------------
#   Step 1: send a password link (e-mail, or shown on screen when e-mail is not configured)
# -----------------------------

@app.route('/send-reset-link', methods=['POST'])
@login_required
def send_reset_link():
    try:
        email = request.form.get('email', '').strip()
        current_lang = request.form.get('lang') or request.args.get('lang') or get_lang()

        owner_mode = is_owner()
        active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not email:
            flash("כתובת אימייל חסרה", "danger")
            return redirect(url_for('clients', lang=current_lang))

        user, err = _find_user_for_admin(
            email, request.form.get('company_id'), owner_mode, active_company_id)

        if err == 'ambiguous':
            flash("האימייל קיים בכמה חברות, יש לבחור חברה", "warning")
            return redirect(url_for('clients', lang=current_lang))
        if not user:
            flash(py_i18n("client.not_found"), "danger")
            return redirect(url_for('clients', lang=current_lang))

        if not owner_mode and (user.role or '').lower() in ['manager', 'owner']:
            flash(py_i18n("auth.no_permission"), "danger")
            return redirect(url_for('unauthorized'))

        token = create_reset_token(user, hours=72)

        base_url = os.getenv('BASE_URL') or request.host_url.rstrip('/')
        reset_url = f"{base_url}/set-password?token={token}"

        if not _mail_ready():
            return _notice_page(f"שליחת אימייל אינה מוגדרת. קישור לקביעת סיסמה: {reset_url}",
                                ok=True, show_back=False)

        try:
            msg = Message(
                py_i18n("reset.email_subject"),
                recipients=[user.email],
                body=f"{py_i18n('reset.email_body')}\n{reset_url}"
            )
            mail.send(msg)
        except Exception as mail_err:
            print(f"⚠ Reset e-mail failed for {user.email}: {mail_err}")
            return _notice_page(f"שליחת האימייל נכשלה. קישור לקביעת סיסמה: {reset_url}",
                                ok=False, show_back=False)

        flash(py_i18n("reset.link_sent"), "success")
        return redirect(url_for('clients', lang=current_lang))

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה במערכת שליחת המייל", "danger")
        return redirect(url_for('clients', lang=get_lang()))


# -----------------------------
#   Step 2: set the password (token page)
# -----------------------------

def _login_error_page(message):
    return (
        f'<div dir="rtl" style="font-family:Arial;padding:40px;font-size:20px">'
        f'<div style="padding:20px;border-radius:8px;background:#fde2e2">{escape(message)}</div>'
        f'<p><a href="{url_for("login")}">חזרה לכניסה</a></p></div>'
    ), 401


def _set_password_page(token, error=""):
    err_html = (f'<div style="padding:12px;border-radius:6px;background:#fde2e2;margin-bottom:16px">'
                f'{escape(error)}</div>') if error else ''
    return f"""<!doctype html>
<html dir="rtl" lang="he"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>קביעת סיסמה</title></head>
<body style="font-family:Arial;background:#f5f5f5;margin:0">
  <div style="max-width:420px;margin:60px auto;background:#fff;padding:30px;border-radius:10px">
    <h2>קביעת סיסמה</h2>
    {err_html}
    <form method="post" action="{url_for('set_password')}"
          onsubmit="this.querySelector('button').disabled=true">
      <input type="hidden" name="token" value="{escape(token)}">
      <label>סיסמה חדשה (לפחות 8 תווים)</label>
      <input type="password" name="password" minlength="8" required autocomplete="new-password"
             style="width:100%;padding:10px;margin:8px 0 16px;box-sizing:border-box">
      <label>אימות סיסמה</label>
      <input type="password" name="password2" minlength="8" required autocomplete="new-password"
             style="width:100%;padding:10px;margin:8px 0 20px;box-sizing:border-box">
      <button type="submit" style="width:100%;padding:12px;font-size:16px">שמור סיסמה</button>
    </form>
  </div>
</body></html>"""


@app.route('/set-password', methods=['GET', 'POST'])
def set_password():
    try:
        # the token is read from the form, the address, or the session (whatever arrives)
        token = (request.form.get('token') or request.args.get('token')
                 or session.get('_reset_token') or '').strip()
        if not token:
            return _login_error_page("קישור לא תקין")

        entry = PasswordResetToken.query.filter_by(token=token).first()
        if not entry or entry.expires_at < datetime.utcnow():
            session.pop('_reset_token', None)
            return _login_error_page("הקישור פג תוקף או כבר נוצל. יש לבקש קישור חדש")

        user = db.session.get(User, entry.user_id)
        if not user:
            return _login_error_page("המשתמש לא נמצא")

        if request.method == 'GET':
            session['_reset_token'] = token
            return _set_password_page(token)

        p1 = (request.form.get('password') or '').strip()
        p2 = (request.form.get('password2') or '').strip()

        if len(p1) < 8:
            return _set_password_page(token, "הסיסמה חייבת להכיל לפחות 8 תווים")
        if p1 != p2:
            return _set_password_page(token, "הסיסמאות אינן זהות")

        first_time = not (user.password_hash or '').strip()
        _set_user_password(user, p1)

        # only an invited user (no password yet) is activated by this link
        if first_time:
            user.is_active = True
            user.is_approved = True
            customer_row = _customer_for_user(user)
            if customer_row:
                customer_row.is_active = True

        PasswordResetToken.query.filter_by(user_id=user.id).delete()
        db.session.commit()

        print(f"✔ PASSWORD SET: user={user.id} email={user.email} role={user.role} company={user.company_id}")

        session.clear()      # any open session (for example the owner) is closed
        flash(py_i18n("reset.password_updated"), "success")
        return redirect(url_for('login'))

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return _login_error_page("שגיאה בקביעת הסיסמה")


# -----------------------------
# Delete selected users (Owner only)
# -----------------------------

@app.route('/delete_selected_users', methods=['POST'])
@login_required
def delete_selected_users():
    import shutil
    try:
        if not is_owner():
            flash(py_i18n("auth.no_permission"), "danger")
            return redirect(url_for('unauthorized'))

        ids = request.form.getlist('delete_ids')
        if not ids:
            flash(py_i18n("client.delete_none_selected"), "warning")
            return redirect(url_for('clients'))

        existing = _existing_columns()

        def has(table, column=None):
            return table in existing and (column is None or column in existing[table])

        def delete_where(table, column, value):
            if has(table, column):
                db.session.execute(
                    db.text(f"DELETE FROM {table} WHERE {column} = :v"), {"v": value})

        def delete_where_company(table, column, value, company_id):
            if has(table, column) and has(table, 'company_id'):
                db.session.execute(
                    db.text(f"DELETE FROM {table} WHERE {column} = :v AND company_id = :c"),
                    {"v": value, "c": company_id})

        deleted_count = 0
        folders_to_remove = []
        db.session.expire_all()

        for raw in ids:
            raw = (raw or '').strip()

            # ---- customer without a login user: "c<customer id>" ----
            if raw.startswith('c') and raw[1:].isdigit():
                cust = db.session.get(Customer, int(raw[1:]))
                if (not cust or cust.company_id != OWNER_COMPANY_ID
                        or cust.customer_name == SELF_CUSTOMER_NAME):
                    continue
                if cust.invoices:
                    flash("ללקוח יש חשבוניות ולכן לא ניתן למחוק אותו", "warning")
                    continue
                if cust.local_id:
                    folders_to_remove.append(ensure_customer_folder(cust.company_id, cust.local_id))
                delete_where('customer', 'id', cust.id)
                deleted_count += 1
                continue

            # ---- login user ----
            if not raw.isdigit():
                continue
            user = db.session.get(User, int(raw))
            if not user:
                continue

            email_clean = (user.email or "").strip()
            role_clean = (user.role or "").strip().lower()

            if email_clean == OWNER_USERNAME or role_clean == "owner":
                flash("אי אפשר למחוק את משתמש הבעלים!", "danger")
                continue
            if user.company_id == OWNER_COMPANY_ID:
                flash("אי אפשר למחוק משתמש ששייך לחברת הבעלים!", "danger")
                continue

            uid = user.id
            comp_id = user.company_id
            own_customer = _customer_for_user(user)

            # ================= manager = the whole company is deleted =================
            if role_clean == 'manager' and comp_id:
                for c in Customer.query.filter_by(company_id=comp_id).all():
                    if c.local_id:
                        folders_to_remove.append(ensure_customer_folder(comp_id, c.local_id))
                folders_to_remove.append(ensure_company_folder(comp_id))
                folders_to_remove.append(os.path.join(app.config["EMPLOYEES_DIR"], f"company_{comp_id}"))

                # the company as a customer of the owner company
                if email_clean:
                    for pc in Customer.query.filter(
                            Customer.company_id == OWNER_COMPANY_ID,
                            db.func.lower(Customer.email) == email_clean.lower()).all():
                        if pc.invoices:
                            pc.is_active = False      # owner invoices point to it: keep, just deactivate
                        else:
                            if pc.local_id:
                                folders_to_remove.append(ensure_customer_folder(OWNER_COMPANY_ID, pc.local_id))
                            delete_where('customer', 'id', pc.id)

                for t in ('transactions', 'payments'):
                    delete_where(t, 'company_id', comp_id)

                if has('invoice_items', 'invoice_id') and has('invoices', 'company_id'):
                    db.session.execute(db.text(
                        "DELETE FROM invoice_items WHERE invoice_id IN "
                        "(SELECT id FROM invoices WHERE company_id = :c)"), {"c": comp_id})
                delete_where('invoice_items', 'company_id', comp_id)

                for t in ('invoices', 'products', 'categories', 'suppliers',
                          'shift_states', 'timesheets', 'employee_data'):
                    delete_where(t, 'company_id', comp_id)

                if has('password_reset_tokens', 'user_id') and has('users', 'company_id'):
                    db.session.execute(db.text(
                        "DELETE FROM password_reset_tokens WHERE user_id IN "
                        "(SELECT id FROM users WHERE company_id = :c)"), {"c": comp_id})

                delete_where('users', 'company_id', comp_id)
                delete_where('customer', 'company_id', comp_id)
                delete_where('company', 'id', comp_id)

            # ================= employee / customer = only his own rows =================
            else:
                emp_rows = EmployeeData.query.filter(
                    EmployeeData.company_id == comp_id,
                    db.or_(EmployeeData.user_id == uid, EmployeeData.email == user.email)).all()
                for emp in emp_rows:
                    for emp_key in {emp.id, uid}:
                        delete_where_company('shift_states', 'employee_id', emp_key, comp_id)
                        delete_where_company('timesheets', 'employee_id', emp_key, comp_id)
                    delete_where('employee_data', 'id', emp.id)

                if own_customer:
                    if own_customer.invoices:
                        own_customer.is_active = False
                    else:
                        if own_customer.local_id:
                            folders_to_remove.append(
                                ensure_customer_folder(own_customer.company_id, own_customer.local_id))
                        delete_where('customer', 'id', own_customer.id)

                delete_where('password_reset_tokens', 'user_id', uid)
                delete_where('users', 'id', uid)

            deleted_count += 1

        # one transaction: everything or nothing
        db.session.commit()

        # folders are removed ONLY after the database commit succeeded
        for folder in folders_to_remove:
            try:
                if folder and os.path.exists(folder):
                    shutil.rmtree(folder, ignore_errors=True)
            except Exception as e:
                print(f"⚠ Failed to remove folder {folder}: {e}")

        db.session.close()

        if deleted_count > 0:
            flash(py_i18n("client.deleted_count").format(count=deleted_count), "success")
        else:
            flash(py_i18n("auth.no_permission"), "danger")

        return redirect(url_for('clients'))

    except Exception:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        flash("שגיאה במהלך מחיקת המשתמשים", "danger")
        return redirect(url_for('clients'))


# -----------------------------
# Update role (Owner only - Secure Unified)
# -----------------------------

@app.route('/update_role', methods=['POST'])
@login_required
def update_role():
    import shutil
    try:
        email      = request.form.get('email', '').strip()
        new_role   = request.form.get('role', '').strip().lower()
        company_id = request.form.get('company_id')

        current_lang = request.form.get('lang') or request.args.get('lang') or get_lang()

        # only real roles are allowed (no "owner")
        if not email or new_role not in ALLOWED_ROLES:
            flash(py_i18n("client.role_invalid_data"), "danger")
            return redirect(url_for('clients', lang=current_lang))

        user_role = (getattr(current_user, 'role', '') or '').lower() or (session.get('role') or '').lower()
        owner_mode = (user_role == 'owner') or (session.get('owner_access') is True)
        active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        db.session.expire_all()

        user, err = _find_user_for_admin(email, company_id, owner_mode, active_company_id)

        if err == 'ambiguous':
            flash("האימייל קיים בכמה חברות, יש לבחור חברה", "warning")
            return redirect(url_for('clients', lang=current_lang))
        if not user:
            flash(py_i18n("client.not_found"), "danger")
            return redirect(url_for('clients', lang=current_lang))

        old_role = (user.role or '').lower()
        if old_role == 'owner':
            flash(py_i18n("auth.no_permission"), "danger")
            return redirect(url_for('clients', lang=current_lang))

        if not owner_mode:
            if (old_role in ['manager', 'owner'] or user.company_id != active_company_id
                    or new_role in ['manager', 'owner']):
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients', lang=current_lang))

        old_company_id = user.company_id
        now = datetime.now()
        current_year = now.strftime('%Y')
        current_month = now.strftime('%m')
        today_str = datetime.today().strftime('%d/%m/%Y')
        folders_to_remove = []
        after_commit = []          # translations are started only after a successful commit

        if old_role != new_role:

            # ============================ MANAGER ============================
            if new_role == 'manager':
                company_name = user.username or user.email

                if Company.query.filter(
                        (Company.name == company_name) | (Company.email == user.email)).first():
                    flash("חברה עם שם או אימייל זה כבר קיימת במערכת", "danger")
                    return redirect(url_for('clients', lang=current_lang))

                new_company = Company(name=company_name, email=user.email, translations_json="{}")
                db.session.add(new_company)
                db.session.flush()

                # old customer rows of this person in the old company
                folders_to_remove += _drop_customer_rows(
                    Customer.query.filter_by(email=user.email, company_id=old_company_id).all())

                # old employee rows and their clock data
                for o_e in EmployeeData.query.filter_by(
                        email=user.email, company_id=old_company_id).all():
                    ShiftState.query.filter_by(employee_id=o_e.id, company_id=old_company_id).delete()
                    Timesheet.query.filter_by(employee_id=o_e.id, company_id=old_company_id).delete()
                    db.session.delete(o_e)

                user.role = 'manager'
                user.company_id = new_company.id
                user.customer_id = None
                user.is_active = True
                user.is_approved = True

                after_commit.append(lambda co=new_company: _translate_new_company(co))

                # the new company is also a customer of the owner company (same as /register)
                if not Customer.query.filter(
                        Customer.company_id == OWNER_COMPANY_ID,
                        db.func.lower(Customer.email) == user.email.lower()).first():
                    platform_cust = create_customer(
                        OWNER_COMPANY_ID, company_name, user.email, date=today_str)
                    after_commit.append(lambda c=platform_cust: _translate_new_customer(c))

            # ============================ CUSTOMER ============================
            elif new_role == 'customer':
                if owner_mode and company_id and str(company_id).isdigit():
                    if not db.session.get(Company, int(company_id)):
                        flash(py_i18n("auth.company_not_found"), "danger")
                        return redirect(url_for('clients', lang=current_lang))
                    user.company_id = int(company_id)

                if not user.company_id:
                    flash(py_i18n("auth.no_company_assigned"), "danger")
                    return redirect(url_for('clients', lang=current_lang))

                emp_prof = EmployeeData.query.filter_by(
                    email=user.email, company_id=old_company_id).first()
                if emp_prof:
                    ShiftState.query.filter_by(employee_id=emp_prof.id, company_id=old_company_id).delete()
                    Timesheet.query.filter_by(employee_id=emp_prof.id, company_id=old_company_id).delete()
                    db.session.delete(emp_prof)

                user.role = 'customer'

                active_comp  = db.session.get(Company, user.company_id)
                comp_address = getattr(active_comp, 'address', '') or ""
                comp_city    = getattr(active_comp, 'city', '') or ""

                customer = Customer.query.filter(
                    Customer.company_id == user.company_id,
                    db.func.lower(Customer.email) == user.email.lower()).first()

                if not customer:
                    customer = create_customer(
                        user.company_id, user.username or user.email or "לקוח חדש", user.email,
                        address=comp_address, city=comp_city, date=today_str)
                    customer.is_active = user.is_active
                else:
                    customer.role = 'customer'
                    customer.is_active = user.is_active
                    if not customer.customer_name:
                        customer.customer_name = user.username or user.email
                    if not customer.address:
                        customer.address = comp_address
                    if not customer.city:
                        customer.city = comp_city

                user.customer_id = customer.id
                db.session.flush()

                after_commit.append(lambda c=customer: _translate_new_customer(c))

            # ============================ EMPLOYEE ============================
            elif new_role == 'employee':
                if owner_mode and company_id and str(company_id).isdigit():
                    if not db.session.get(Company, int(company_id)):
                        flash(py_i18n("auth.company_not_found"), "danger")
                        return redirect(url_for('clients', lang=current_lang))
                    user.company_id = int(company_id)

                user.role = 'employee'
                user.is_active = True
                user.is_approved = True

                folders_to_remove += _drop_customer_rows(
                    Customer.query.filter_by(email=user.email, company_id=user.company_id).all())
                user.customer_id = None

                employee_profile = EmployeeData.query.filter_by(
                    email=user.email, company_id=user.company_id).first()
                if not employee_profile:
                    employee_profile = EmployeeData()

                    for column in employee_profile.__table__.columns:
                        if not column.nullable and column.default is None and not column.primary_key:
                            if isinstance(column.type, (db.Integer, db.Float)):
                                setattr(employee_profile, column.name, 0.0)
                            elif isinstance(column.type, db.Boolean):
                                setattr(employee_profile, column.name, False)
                            else:
                                setattr(employee_profile, column.name, '')

                    employee_profile.company_id = user.company_id
                    employee_profile.employee_name = user.username or user.email or "עובד חדש"
                    employee_profile.email = user.email
                    employee_profile.id_number = ""
                    employee_profile.user_id = user.id
                    employee_profile.employee_id = user.id

                    db.session.add(employee_profile)
                    db.session.flush()

                try:
                    clock_key = employee_profile.id_number if employee_profile.id_number else str(user.id)
                    target_folder, json_file_path, _ = get_company_clock_paths(
                        user.company_id, clock_key, current_year, current_month)
                    os.makedirs(target_folder, exist_ok=True)

                    with open(json_file_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "hours_table": {"work_day_entries": [], "tax": {}},
                            "is_active": True,
                            "email": user.email,
                            "employee_name": employee_profile.employee_name
                        }, f, ensure_ascii=False, indent=2)
                except Exception as file_err:
                    print(f"⚠️ Disk sync skipped: {file_err}")

        db.session.commit()

        for fn in after_commit:
            fn()

        # old customer folders are removed only after the commit succeeded
        for folder in folders_to_remove:
            try:
                if folder and os.path.exists(folder):
                    shutil.rmtree(folder, ignore_errors=True)
            except Exception as e:
                print(f"⚠️ Folder cleanup skipped: {e}")

        db.session.close()
        flash(py_i18n("client.role_updated"), "success")
        return redirect(url_for('clients', lang=current_lang))

    except Exception:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        flash("שגיאה חמורה בעדכון תפקיד המשתמש", "danger")
        return redirect(url_for('clients', lang=get_lang()))


# -----------------------------------------------------------
# Customer Dashboard Router (Multi‑Tenant Safe)
# -----------------------------------------------------------

@app.route('/customer_dashboard_router')
@login_required
def customer_dashboard_router():
    try:
        role = _active_role()

        if is_owner() or role == 'manager':
            return redirect(url_for('invoice'))

        if role == 'customer':
            return redirect(url_for('customer_dashboard_view'))

        if role == 'employee':
            return redirect(url_for('invoice'))

        return redirect(url_for('unauthorized'))

    except Exception as e:
        print(f"❌ Navigation router failed: {e}")
        return redirect(url_for('login'))


# -----------------------------------------------------------
# Customer Dashboard View (Filtered Invoice View)
# -----------------------------------------------------------

@app.route('/customer_dashboard_view')
@login_required
def customer_dashboard_view():
    try:
        role = _active_role()
        is_owner_user = is_owner()

        if role not in ['customer', 'manager'] and not is_owner_user:
            return redirect(url_for('unauthorized'))

        active_company_id = _to_int(session.get('company_id'))

        # ---------- find the active customer (customers only) ----------
        customer = None
        if role == 'customer':
            if not active_company_id:
                return _forbidden_page(), 403

            candidate_ids = []
            for raw in (session.get('customer_id'), getattr(current_user, 'customer_id', None)):
                cid = _to_int(raw)
                if cid and cid not in candidate_ids:
                    candidate_ids.append(cid)

            for cid in candidate_ids:
                customer = Customer.query.filter_by(
                    id=cid, company_id=active_company_id).first()
                if customer:
                    break

            # last resort: by e-mail inside the same company
            if not customer and getattr(current_user, 'email', None):
                customer = Customer.query.filter(
                    Customer.company_id == active_company_id,
                    db.func.lower(Customer.email) == current_user.email.lower()).first()

            # a customer account without an active customer row has no access.
            # The 403 page is shown here directly (a redirect to /unauthorized loops back to this page)
            if not customer or customer.is_active is False:
                print(f"⚠️ Security: customer user {getattr(current_user, 'id', None)} "
                      f"has no valid customer row in company {active_company_id}")
                return _forbidden_page(), 403

            session['customer_id'] = customer.id    # repair the session

        active_customer_id = customer.id if customer else None

        # ---------- the company ----------
        target_company_id = customer.company_id if customer else active_company_id
        safe_target_company_id = int(target_company_id) if target_company_id else OWNER_COMPANY_ID

        company_obj = db.session.get(Company, safe_target_company_id)

        language = get_lang()
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        # technical customer for self invoices
        self_customer = Customer.query.filter_by(
            company_id=safe_target_company_id,
            customer_name=SELF_CUSTOMER_NAME
        ).first()

        # Self Billing mode: owner or manager (they have no customer row here)
        is_self_billing = (not customer) and (role == 'manager' or is_owner_user)

        # ---------- invoices ----------
        if is_self_billing:
            # self invoice = NULL (old), 0 (old local), or the id of the technical customer
            self_ids = [0]
            if self_customer:
                self_ids.append(self_customer.id)

            invoices = (
                Invoice.query
                .filter(
                    Invoice.company_id == safe_target_company_id,
                    db.or_(Invoice.customer_id.is_(None), Invoice.customer_id.in_(self_ids))
                )
                .order_by(cast(Invoice.invoice_number, Integer).desc(), Invoice.id.desc())
                .all()
            )
        else:
            invoices = (
                Invoice.query
                .filter_by(company_id=safe_target_company_id, customer_id=active_customer_id)
                .order_by(cast(Invoice.invoice_number, Integer).desc(), Invoice.id.desc())
                .all()
            )

        current_active_inv = invoices[0] if invoices else None

        # default VAT rate
        last_invoice = (
            Invoice.query
            .filter_by(company_id=safe_target_company_id)
            .order_by(cast(Invoice.invoice_number, Integer).desc())
            .first()
        )
        default_vat_rate = float(last_invoice.vat_rate) if last_invoice and last_invoice.vat_rate is not None else 0.0

        # ---------- JSON for the active customer ----------
        if is_self_billing:
            customer_json = {
                "id": "0",
                "local_id": None,
                "customer_name": f"★ {company_translated.get('name') or (company_obj.name if company_obj else 'החברה שלי')}",
                "address": (company_translated.get('address') or company_obj.address or "") if company_obj else "",
                "city": (company_translated.get('city') or company_obj.city or "") if company_obj else "",
                "postal_code": (company_obj.postal_code or "") if company_obj else "",
                "id_number": (company_obj.company_id_number or "") if company_obj else "",
                "email": (company_obj.email or "") if company_obj else "",
                "phone": (company_obj.phone or "") if company_obj else "",
                "message": ""
            }
            context_customer_param = "0"
        else:
            trans_active = load_customer_translated(
                customer, language, company_id=str(safe_target_company_id)) or {}
            customer_json = {
                "id": customer.id,
                "local_id": customer.local_id,
                "customer_name": trans_active.get("name") or customer.customer_name or customer.email or "לקוח",
                "address": trans_active.get("address") or customer.address or "",
                "city": trans_active.get("city") or customer.city or "",
                "postal_code": customer.postal_code or "",
                "id_number": customer.id_number or "",
                "email": customer.email or "",
                "phone": customer.phone or "",
                "message": trans_active.get("message") or customer.message or ""
            }
            # same key rule as /api/customer (CUSTOMER_LOOKUP_FIELD)
            context_customer_param = str(_customer_key(customer))

        # ---------- customers list: ONLY for owner / manager ----------
        all_customers_json = []
        if role != 'customer':
            all_customers = Customer.query.filter(
                Customer.company_id == safe_target_company_id,
                Customer.customer_name != SELF_CUSTOMER_NAME
            ).all()

            for c in all_customers:
                trans_c = load_customer_translated(
                    c, language, company_id=str(safe_target_company_id)) or {}
                all_customers_json.append({
                    "id": c.id,
                    "local_id": c.local_id,
                    "customer_name": trans_c.get("name") or c.customer_name or "",
                    "id_number": c.id_number or "",
                    "address": trans_c.get("address") or c.address or "",
                    "city": trans_c.get("city") or c.city or "",
                    "postal_code": c.postal_code or "",
                    "email": c.email or "",
                    "phone": c.phone or "",
                    "message": trans_c.get("message") or c.message or ""
                })

        # ---------- translated cancellation reason ----------
        cancellation_reason_trans = ""
        if current_active_inv and getattr(current_active_inv, 'status', '') == 'canceled':
            cancel_file_data = load_cancellation_file(
                str(safe_target_company_id), current_active_inv.id) or {}
            reason_dict = cancel_file_data.get("cancellation_reason", {})
            cancellation_reason_trans = (
                reason_dict.get(language)
                or reason_dict.get("he")
                or getattr(current_active_inv, 'cancellation_reason', '')
                or "ביטול כללי"
            )

        # ---------- currency ----------
        invoice_original_currency = (
            (getattr(current_active_inv, 'currency', None) if current_active_inv else None)
            or (getattr(company_obj, 'currency', None) if company_obj else None)
            or 'ILS'
        )
        current_currency = request.cookies.get('currency') or session.get('currency') or invoice_original_currency

        # ---------- invoice date (an empty date must not crash the page) ----------
        inv_date = getattr(current_active_inv, 'invoice_date', None) if current_active_inv else None
        invoice_date_str = (inv_date.strftime('%d-%m-%Y') if inv_date
                            else datetime.today().strftime('%d-%m-%Y'))

        # ---------- template context ----------
        base_ctx = base_invoice_context(customer_id=context_customer_param)

        base_ctx.update({
            "invoices": invoices,
            "invoice": current_active_inv,
            "invoice_id": current_active_inv.id if current_active_inv else None,
            "allocation_number": current_active_inv.allocation_number if current_active_inv else None,
            "invoice_number": current_active_inv.invoice_number if current_active_inv else 1,
            "invoice_date": invoice_date_str,

            "customer": customer_json,
            "customer_json": customer_json,
            "all_customers_json": all_customers_json,

            "items": [],
            "loadedPayments": [],
            "sub_total": float(current_active_inv.sub_total or 0.0) if current_active_inv else 0.0,

            "vat_rate": default_vat_rate,
            "vat_amount": float(current_active_inv.vat_amount or 0.0) if current_active_inv else 0.0,
            "grand_total": float(current_active_inv.grand_total or 0.0) if current_active_inv else 0.0,
            "discount_total": float(getattr(current_active_inv, 'discount_total', 0.0) or 0.0) if current_active_inv else 0.0,
            "invoice_status": current_active_inv.status if current_active_inv else "active",
            "translated_reason": cancellation_reason_trans,

            "is_customer_view": True,

            "company": company_translated,
            "company_db": company_obj,
            "language": language,

            "invoice_original_currency": invoice_original_currency,
            "current_currency": current_currency
        })

        return render_template('invoice.html', **base_ctx)

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return _forbidden_page(), 403

# -----------------------------------------------------------
# Employee Dashboard Router (Multi‑Tenant Safe)
# -----------------------------------------------------------

@app.route('/employee-dashboard')
@login_required
def employee_dashboard():
    try:
        language = get_lang()
        
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        active_company_id_session = getattr(current_user, 'company_id', None) or session.get('company_id')

        # המרה מפורשת ל-int עבור PostgreSQL ב-Render למניעת קריסות טיפוסים
        safe_company_id = int(active_company_id_session) if active_company_id_session else None

        if is_owner() or session.get('owner_access') or user_role in ['owner', 'manager']:
            return redirect(url_for('clock_in_out'))

        if user_role == 'employee':
            # שליפה מאובטחת מטבלת השכר המאוחדת EmployeeData מול פוסטגרס
            current_employee_profile = EmployeeData.query.filter(
                (EmployeeData.user_id == int(current_user.id)) | (EmployeeData.email == str(current_user.email).strip())
            ).filter_by(company_id=safe_company_id).first() if safe_company_id else None

            if current_employee_profile:
                # אבטחת סינכרון: שמירת המזהה המקומי בפורמט אחיד שמונע קפיצות או נתקים בטבלאות המשמרות (Shift States)
                safe_local_id = current_employee_profile.local_id if current_employee_profile.local_id is not None else current_employee_profile.id
                
                # הגדרת זמני ברירת מחדל נוכחיים (הפיקס המנצח שמונע טבלאות שכר ריקות!)
                today = datetime.today()
                default_month_str = f"{today.month:02d}"
                default_year_str = str(today.year)
                month_result_str = f"{default_month_str}/{default_year_str}"

                # נעילת כל המפתחות הנדרשים בסשן של העובד לצורך שליפת 120 שדות השכר ב-GET
                session.update({
                    'employee_id': str(safe_local_id),
                    'selected_month': default_month_str,
                    'selected_year': default_year_str,
                    'month_result': month_result_str,
                    'employee_global_id': current_employee_profile.id
                })
            
            return redirect(url_for('clock_in_out'))

        return redirect(url_for('unauthorized'))

    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error inside employee_dashboard alignment router: {e}")
        return redirect(url_for('unauthorized'))

# =============================================================
#  COMPANY + CUSTOMER folders and translations (one block, no duplicates)
#
#  Company  : <COMPANY_DIR>/<company id>/<company id>.json
#  Customer : <CUSTOMERS_DIR>/<company id>_<local id>/customer.json
#  Customer id 0 = the company itself (self invoice), it has no folder.
# =============================================================

_company_file_lock  = threading.Lock()            # one writer at a time
_customer_file_lock = threading.Lock()
_translate_gate     = threading.BoundedSemaphore(2)   # at most 2 translations at once

CUSTOMER_TEXT_FIELDS = ("name", "address", "city", "message")


# ---------------------------------------------------------
#  Shared translation helpers
# ---------------------------------------------------------

def _is_bad_translation(val):
    if not isinstance(val, str) or not val.strip():
        return True
    return any(m in val for m in ("Error 500", "That’s an error", "Please try again later"))


def _build_translation_dict(new_trans, original_text, src_lang, old_trans=None):
    """Merge a fresh translation with the saved one, never keeping stale text."""
    original_text = (original_text or "").strip()
    if not original_text:
        return {src_lang: "", "he": ""}

    # keep old translations ONLY if the source text did not change
    result = {}
    if isinstance(old_trans, dict) and old_trans.get(src_lang) == original_text:
        result = dict(old_trans)

    if isinstance(new_trans, dict):
        for lang, val in new_trans.items():
            if not _is_bad_translation(val):
                result[lang] = val

    if src_lang in ("he", "iw"):
        result["he"] = original_text
        result["iw"] = original_text
    else:
        result[src_lang] = original_text
        if result.get("iw"):
            result["he"] = result["iw"]
    return result


def _atomic_json_write(file_path, data, lock):
    """A reader never sees a half-written JSON."""
    with lock:
        tmp_path = f"{file_path}.{threading.get_ident()}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp_path, file_path)


# ---------------------------------------------------------
#  COMPANY
# ---------------------------------------------------------

def company_base_dir():
    # Config key is COMPANY_DIR (persistent disk on Render)
    return current_app.config.get("COMPANY_DIR") or os.path.join(current_app.root_path, "companies")


def ensure_company_folder(company_id):
    # A company is identified ONLY by its real id (no local_id for companies)
    return os.path.join(company_base_dir(), str(int(company_id)))


def translate_company_in_background(
    company_id, name, id_number, deduction_file, address, city, postal_code, phone, email, logo, source_lang="he"
):
    app_instance = current_app._get_current_object()

    def background_worker():
        with app_instance.app_context():
            with _translate_gate:
                run_company_translation(
                    str(company_id).strip(), name, id_number, deduction_file,
                    address, city, postal_code, phone, email, logo, source_lang
                )

    threading.Thread(target=background_worker, daemon=True).start()


def run_company_translation(
    company_id, name, id_number, deduction_file, address, city, postal_code, phone, email, logo, source_lang="he"
):
    try:
        old = load_company_file(company_id) or {}

        texts = {"name": str(name or "").strip(),
                 "address": str(address or "").strip(),
                 "city": str(city or "").strip()}

        merged = {}
        for field, text in texts.items():
            old_field = old.get(field) if isinstance(old.get(field), dict) else {}
            if not text:
                merged[field] = _build_translation_dict(None, "", source_lang, old_field)
                continue
            trans = generate_translations(text, source_lang=source_lang)
            merged[field] = _build_translation_dict(trans, text, source_lang, old_field)
            time.sleep(0.3)

        save_company_file(
            company_id, merged["name"], id_number or "", deduction_file or "",
            merged["address"], merged["city"],
            postal_code or "", phone or "", email or "", logo or ""
        )
    except Exception as e:
        print(f"⚠ Company translation failed for {company_id}: {e}")


def save_company_file(
    company_id, name_trans, id_number, deduction_file, address_trans, city_trans, postal_code, phone, email, logo
):
    folder = ensure_company_folder(company_id)
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, f"{int(company_id)}.json")

    data = {
        "name": name_trans,
        "company_id_number": id_number,
        "deduction_file": deduction_file,
        "address": address_trans,
        "city": city_trans,
        "postal_code": postal_code,
        "phone": phone,
        "email": email,
        "logo": logo
    }
    _atomic_json_write(file_path, data, _company_file_lock)

    print(f"✔ Company JSON saved: {file_path}")
    return file_path


def load_company_file(company_id):
    if not company_id:
        return None
    try:
        folder = ensure_company_folder(company_id)
    except (ValueError, TypeError):
        return None
    file_path = os.path.join(folder, f"{int(company_id)}.json")

    if not os.path.isfile(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠ load_company_file error: {e}")
        return None


def load_company_translated(company, language):
    empty = {
        "name": "", "company_id_number": "", "deduction_file": "",
        "address": "", "city": "", "postal_code": "", "phone": "", "email": "", "logo": ""
    }
    if not company:
        return empty

    special_mappings = {"zh": "zh-CN", "zh-cn": "zh-CN", "en": "en", "he": "he", "iw": "he"}
    lookup_lang = special_mappings.get(str(language).lower().strip(), language)

    # The database is the source of truth. Only name/address/city come from the JSON file.
    result = {
        "name": company.name or "",
        "company_id_number": company.company_id_number or "",
        "deduction_file": company.deduction_file or "",
        "address": company.address or "",
        "city": company.city or "",
        "postal_code": company.postal_code or "",
        "phone": company.phone or "",
        "email": company.email or "",
        "logo": company.logo or ""
    }

    data = load_company_file(company.id)
    if not data:
        return result

    for key in ("name", "address", "city"):
        field_data = data.get(key)
        if isinstance(field_data, dict):
            result[key] = (
                field_data.get(lookup_lang) or field_data.get(language)
                or field_data.get("he") or field_data.get("iw") or result[key]
            )
    return result


# ---------------------------------------------------------
#  CUSTOMER  (local_id is unique only inside one company)
# ---------------------------------------------------------

def customer_base_dir():
    # CUSTOMERS_DIR = persistent disk on Render (/data/customers), local: ./customers
    return current_app.config.get("CUSTOMERS_DIR") or os.path.join(current_app.root_path, "customers")


def ensure_customer_folder(company_id, local_id):
    # the folder name has BOTH numbers, so customer 1 of company 2 never meets customer 1 of company 3
    return os.path.join(customer_base_dir(), f"{int(company_id)}_{int(local_id)}")


def translate_customer_in_background(customer_id, company_id, name, address, city, message, source_lang="he"):
    app_instance = current_app._get_current_object()

    def background_worker():
        with app_instance.app_context():
            try:
                safe_cust_id = int(customer_id)
                safe_comp_id = int(company_id)
            except (ValueError, TypeError):
                return

            customer = Customer.query.filter_by(id=safe_cust_id, company_id=safe_comp_id).first()
            if not customer or not customer.local_id:
                print(f"⚠ translate_customer_in_background: customer {safe_cust_id} not found")
                return

            with _translate_gate:
                run_customer_translation(
                    str(safe_comp_id), str(customer.local_id), name, address, city, message, source_lang)

    threading.Thread(target=background_worker, daemon=True).start()


def run_customer_translation(company_id, local_id, name, address, city, message, source_lang="he"):
    try:
        texts = {
            "name": str(name or "").strip(),
            "address": str(address or "").strip(),
            "city": str(city or "").strip(),
            "message": str(message or "").strip(),
        }
        old = load_customer_file(company_id, local_id) or {}

        merged = {}
        for field in CUSTOMER_TEXT_FIELDS:
            old_field = old.get(field) if isinstance(old.get(field), dict) else {}
            text = texts[field]
            if not text:
                merged[field] = _build_translation_dict(None, "", source_lang, old_field)
                continue
            trans = generate_translations(text, source_lang=source_lang)
            merged[field] = _build_translation_dict(trans, text, source_lang, old_field)
            time.sleep(0.3)

        save_customer_file(
            company_id, local_id,
            merged["name"], merged["address"], merged["city"], merged["message"],
            source=texts
        )
    except Exception as e:
        print(f"⚠ Translation failed for company {company_id}, local_id {local_id}: {e}")


def save_customer_file(company_id, local_id, name_trans, address_trans, city_trans, message_trans, source=None):
    folder = ensure_customer_folder(company_id, local_id)
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "customer.json")

    data = {
        "name": name_trans,
        "address": address_trans,
        "city": city_trans,
        "message": message_trans
    }
    if source is not None:
        data["_source"] = source      # the original texts these translations were made from

    _atomic_json_write(file_path, data, _customer_file_lock)

    print(f"✔ Customer JSON saved: {file_path}")
    return file_path


def load_customer_file(company_id, local_id):
    if not company_id or not local_id:
        return None
    try:
        folder = ensure_customer_folder(company_id, local_id)
    except (ValueError, TypeError):
        return None

    file_path = os.path.join(folder, "customer.json")
    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("⚠ load_customer_file error:", e)
        return None


def load_customer_translated(customer, language, company_id=None):
    empty = {"name": "", "address": "", "city": "", "message": "", "postal_code": ""}
    if customer is None:
        return empty

    special_mappings = {"zh": "zh-CN", "zh-cn": "zh-CN", "en": "en", "he": "he", "iw": "he"}
    lookup_lang = special_mappings.get(str(language).lower().strip(), language)

    if not company_id:
        company_id = getattr(customer, "company_id", None) or session.get("company_id")

    local_id = getattr(customer, "local_id", None)

    # the database is the source of truth
    fallback_data = {
        "name": getattr(customer, 'customer_name', '') or "",
        "address": getattr(customer, 'address', '') or "",
        "city": getattr(customer, 'city', '') or "",
        "message": getattr(customer, 'message', '') or "",
        "postal_code": getattr(customer, 'postal_code', '') or ""
    }

    if not company_id or not local_id:
        return fallback_data

    data = load_customer_file(company_id, local_id)
    if not data:
        return fallback_data

    source = data.get("_source") if isinstance(data.get("_source"), dict) else None

    def get_val(field_key):
        default_val = fallback_data[field_key]

        # The translation was made from an older text (the customer was edited, or the folder
        # belonged to a deleted customer): show the database text until it is translated again.
        if source is not None and (source.get(field_key) or "").strip() != (default_val or "").strip():
            return default_val

        field_data = data.get(field_key)
        if not isinstance(field_data, dict):
            return default_val

        return (field_data.get(lookup_lang) or field_data.get(language)
                or field_data.get("he") or field_data.get("iw") or default_val)

    return {
        "name": get_val("name"),
        "address": get_val("address"),
        "city": get_val("city"),
        "message": get_val("message"),
        "postal_code": fallback_data["postal_code"]
    }

# -----------------------------------------------------------
# Employee Translation Background Tasks (LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_employee_folder(company_id, local_id):
    base_dir = current_app.config.get("EMPLOYEES_DIR", "static/employees")
    # אבטחת המרה כפויה ל-str של שני המזהים למניעת בעיות נתיבים בלינוקס
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    folder = os.path.join(base_dir, f"company_{safe_company}", safe_local)
    return folder


def translate_employee_in_background(employee_id, company_id, name, address, city, message, mobile_phone, id_number, postal_code):
    # שומרים את אובייקט האפליקציה האקטיבי לפני פתיחת הטרד
    app_instance = current_app._get_current_object()

    def background_worker():
        # הזרקת הקונטקסט בצורה קשיחה לתוך הטרד למניעת קריסות Context ברנדר
        with app_instance.app_context():
            try:
                safe_emp_id = int(employee_id)
                safe_comp_id = int(company_id)
            except (ValueError, TypeError):
                return

            employee = Employee.query.filter_by(id=safe_emp_id, company_id=safe_comp_id).first()
            if not employee:
                print(f"⚠ translate_employee_in_background: employee {safe_emp_id} not found")
                return

            local_id = employee.local_id
            if not local_id:
                return

            # הרצת התרגום בתוך הקונטקסט המאובטח
            run_employee_translation(
                str(safe_comp_id), str(local_id), name, address, city, 
                mobile_phone, id_number, postal_code, message
            )

    thread = threading.Thread(target=background_worker)
    thread.daemon = True
    thread.start()


def run_employee_translation(company_id, local_id, name, address, city, mobile_phone, id_number, postal_code, message):
    try:
        # אבטחת מחרוזות נקיות לתוך מנוע התרגום הראשי
        name_trans    = generate_translations(str(name or "").strip())
        address_trans = generate_translations(str(address or "").strip())
        city_trans    = generate_translations(str(city or "").strip())
        mobile_phone_trans = generate_translations(str(mobile_phone or "").strip())
        id_number_trans    = generate_translations(str(id_number or "").strip())
        postal_code_trans  = generate_translations(str(postal_code or "").strip())
        message_trans      = generate_translations(str(message or "").strip())

        def clean_translation_dict(trans_dict, original_text):
            if not trans_dict:
                return {}
            for lang, val in list(trans_dict.items()):
                if val and ("Error 500" in val or "That’s an error" in val or "Please try again later" in val):
                    trans_dict[lang] = original_text
            return trans_dict

        name_clean    = clean_translation_dict(name_trans, name or "")
        address_clean = clean_translation_dict(address_trans, address or "")
        city_clean    = clean_translation_dict(city_trans, city or "")
        mobile_phone_clean = clean_translation_dict(mobile_phone_trans, mobile_phone or "")
        id_number_clean    = clean_translation_dict(id_number_trans, id_number or "")
        postal_code_clean  = clean_translation_dict(postal_code_trans, postal_code or "")
        message_clean      = clean_translation_dict(message_trans, message or "")

        save_employee_file(
            str(company_id), str(local_id), name_clean, address_clean, city_clean, 
            mobile_phone_clean, id_number_clean, postal_code_clean, message_clean
        )

    except Exception as e:
        print(f"⚠ Translation failed for company {company_id}, local_id {local_id}: {e}")


def save_employee_file(company_id, local_id, name_trans, address_trans, city_trans, mobile_phone_trans, id_number_trans, postal_code_trans, message_trans):
    folder = ensure_employee_folder(company_id, local_id)
    
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "employee.json")

    data = {
        "name": name_trans,
        "address": address_trans,
        "city": city_trans,
        "mobile_phone": mobile_phone_trans,
        "id_number": id_number_trans,
        "postal_code": postal_code_trans,
        "message": message_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✔ Airtight Employee JSON document created at: {file_path}")
    return file_path


def load_employee_file(company_id, local_id):
    if not company_id or not local_id:
        return None

    # שימוש ישיר בפונקציה המאובטחת למניעת נתקי סנכרון
    folder = ensure_employee_folder(company_id, local_id)
    file_path = os.path.join(folder, "employee.json")

    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("⚠ load_employee_file error:", e)
        return None


def load_employee_translated(employee, language, company_id=None):
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(str(language).lower().strip(), language)

    if not company_id:
        company_id = getattr(employee, "company_id", None) or session.get("company_id")

    local_id = getattr(employee, "local_id", None)

    fallback_data = {
        "name": getattr(employee, 'employee_name', '') or "",
        "address": getattr(employee, 'address', '') or "",
        "city": getattr(employee, 'city', '') or "",
        "mobile_phone": getattr(employee, 'mobile_phone', '') or getattr(employee, 'phone', '') or "",
        "id_number": getattr(employee, 'id_number', '') or "",
        "message": getattr(employee, 'message', '') or "",
        "postal_code": getattr(employee, 'postal_code', '') or ""
    }

    if not company_id or not local_id:
        return fallback_data

    # המרה כפויה ל-str כדי להבטיח קריאה מדויקת ללא נפילה לפאלבק
    data = load_employee_file(str(company_id), str(local_id))

    if not data:
        return fallback_data

    def get_val(field_key, default_val):
        field_data = data.get(field_key, {})
        if not isinstance(field_data, dict):
            return default_val or ""
        return field_data.get(lookup_lang) or field_data.get("he") or default_val or ""

    return {
        "name": get_val("name", fallback_data["name"]),
        "address": get_val("address", fallback_data["address"]),
        "city": get_val("city", fallback_data["city"]),
        "mobile_phone": get_val("mobile_phone", fallback_data["mobile_phone"]),
        "id_number": get_val("id_number", fallback_data["id_number"]),
        "message": get_val("message", fallback_data["message"]),
        "postal_code": get_val("postal_code", fallback_data["postal_code"])
    }


# -----------------------------------------------------------
#  Supplier Translation Background Tasks (LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_supplier_folder(company_id, local_id):
    base_dir = current_app.config.get("SUPPLIERS_DIR", "static/suppliers")
    # אבטחת המרה כפויה ל-str של שני המזהים למניעת בעיות נתיבים בלינוקס
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    folder = os.path.join(base_dir, f"{safe_company}_{safe_local}")
    return folder


def translate_supplier_in_background(supplier_id, company_id, name, address, city, postal_code, notes):
    # שומרים את אובייקט האפליקציה האקטיבי של Flask לפני פתיחת הטרד
    app_instance = current_app._get_current_object()

    def background_worker():
        # מזריקים את הקונטקסט בצורה קשיחה לתוך הטרד למניעת קריסות Context ברנדר
        with app_instance.app_context():
            try:
                safe_sup_id = int(supplier_id)
                safe_comp_id = int(company_id)
            except (ValueError, TypeError):
                return

            supplier = Supplier.query.filter_by(id=safe_sup_id, company_id=safe_comp_id).first()
            if not supplier:
                print(f"⚠ translate_supplier_in_background: supplier {safe_sup_id} not found")
                return

            local_id = supplier.local_id
            if not local_id:
                return

            # הרצת התרגום בתוך הקונטקסט המאובטח והפיכת מזהים ל-str
            run_supplier_translation(
                str(safe_comp_id), str(local_id), name, address, city, postal_code, notes
            )

    thread = threading.Thread(target=background_worker)
    thread.daemon = True
    thread.start()


def run_supplier_translation(company_id, local_id, name, address, city, postal_code, notes):
    try:
        # אבטחת מחרוזות נקיות לתוך מנוע התרגום הראשי
        name_trans    = generate_translations(str(name or "").strip())
        address_trans = generate_translations(str(address or "").strip())
        city_trans    = generate_translations(str(city or "").strip())
        notes_trans   = generate_translations(str(notes or "").strip())

        def clean_translation_dict(trans_dict, original_text):
            if not trans_dict:
                return {}
            for lang, val in list(trans_dict.items()):
                if val and ("Error 500" in val or "That’s an error" in val or "Please try again later" in val):
                    trans_dict[lang] = original_text
            return trans_dict

        name_clean    = clean_translation_dict(name_trans, name or "")
        address_clean = clean_translation_dict(address_trans, address or "")
        city_clean    = clean_translation_dict(city_trans, city or "")
        notes_clean   = clean_translation_dict(notes_trans, notes or "")

        save_supplier_file(
            str(company_id), 
            str(local_id), 
            name_clean, 
            address_clean, 
            city_clean, 
            postal_code or "", 
            notes_clean
        )

    except Exception as e:
        print(f"⚠ Translation failed for company {company_id}, local_id {local_id}: {e}")


def save_supplier_file(company_id, local_id, name_trans, address_trans, city_trans, postal_code, notes_trans):
    # המרה ל-str מתבצעת אוטומטית בפנים
    folder = ensure_supplier_folder(company_id, local_id)
    
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "supplier.json")

    data = {
        "name": name_trans,
        "address": address_trans,
        "city": city_trans,
        "postal_code": postal_code, 
        "notes": notes_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✔ Airtight Supplier JSON document created at: {file_path}")
    return file_path


def load_supplier_file(company_id, local_id):
    if not company_id or not local_id:
        return None

    # תיקון קריטי: שימוש ב-ensure_supplier_folder במקום בנייה ידנית כדי למנוע נתק בסנכרון!
    folder = ensure_supplier_folder(company_id, local_id)
    file_path = os.path.join(folder, "supplier.json")

    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("⚠ load_supplier_file error:", e)
        return None


def load_supplier_translated(supplier, language, company_id=None):
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(str(language).lower().strip(), language)

    if not company_id:
        company_id = getattr(supplier, "company_id", None) or session.get("company_id")

    local_id = getattr(supplier, "local_id", None)

    fallback_data = {
        "name": supplier.supplier_name or "",
        "address": supplier.address or "",
        "city": supplier.city or "",
        "postal_code": supplier.postal_code or "",
        "notes": supplier.notes or ""
    }

    if not company_id or not local_id:
        return fallback_data

    # המרה מפורשת ל-str לפני השליחה לקובץ
    data = load_supplier_file(str(company_id), str(local_id))

    if not data:
        return fallback_data

    def get_val(field_key, default_val):
        field_data = data.get(field_key, {})
        if not isinstance(field_data, dict):
            return field_data if field_data else default_val or ""
        return field_data.get(lookup_lang) or field_data.get("he") or default_val or ""

    return {
        "name": get_val("name", fallback_data["name"]),
        "address": get_val("address", fallback_data["address"]),
        "city": get_val("city", fallback_data["city"]),
        "postal_code": get_val("postal_code", fallback_data["postal_code"]),
        "notes": get_val("notes", fallback_data["notes"])
    }


# -----------------------------------------------------------
#  Products - Items Helper - Save & Load JSON Translations (FULL COMPATIBLE LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_product_folder(company_id, local_id):
    base_dir = current_app.config.get("ITEMS_DIR", "static/items")
    # אבטחת המרה כפויה ל-str של שני המזהים למניעת בעיות נתיבים בלינוקס
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    folder = os.path.join(base_dir, f"{safe_company}_{safe_local}")
    os.makedirs(folder, exist_ok=True)
    return folder


def load_item_file(company_id_or_obj, local_id=None, company_id=None):
    final_company_id = None
    final_local_id = None

    if hasattr(company_id_or_obj, 'local_id') and hasattr(company_id_or_obj, 'company_id'):
        final_company_id = company_id_or_obj.company_id
        final_local_id = company_id_or_obj.local_id
    elif isinstance(company_id_or_obj, (int, str)) and local_id is not None:
        final_company_id = str(company_id_or_obj).strip()
        final_local_id = str(local_id).strip()
    elif isinstance(company_id_or_obj, (int, str)) and local_id is None and company_id is None:
        try:
            safe_prod_id = int(company_id_or_obj)
            product_obj = Product.query.filter_by(id=safe_prod_id).first()
            if product_obj:
                final_company_id = product_obj.company_id
                final_local_id = product_obj.local_id
        except (ValueError, TypeError):
            return None

    if not final_company_id or not final_local_id:
        return None

    # שימוש ב-ensure_product_folder למניעת נתקי סנכרון
    folder = ensure_product_folder(final_company_id, final_local_id)
    file_path = os.path.join(folder, "product.json")

    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("⚠ load_item_file error:", e)
        return None


def save_item_file(
    company_id,
    local_id,
    name_trans,
    desc_trans,
    price,
    income_category,
    cost_price=0.0,
    stock_out=0,
    sku=None,
    batches=None,
    supplier_name_trans=None  
):
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    folder = ensure_product_folder(safe_company, safe_local)
    file_path = os.path.join(folder, "product.json")

    data = {
        "id": int(safe_local),
        "sku": sku if sku else safe_local,
        "price": float(price or 0.0),
        "cost_price": float(cost_price or 0.0),
        "income_category": income_category or "service",
        "name": name_trans,
        "description": desc_trans,
        "supplier_name": supplier_name_trans if supplier_name_trans else {"he": "מלאי פתיחה / כללי"}
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return file_path


def _get_next_inventory_index(inv_folder):
    existing = []
    if os.path.isdir(inv_folder):
        for fname in os.listdir(inv_folder):
            if fname.startswith("inventory_transactions_") and fname.endswith(".json"):
                try:
                    idx = int(fname.replace("inventory_transactions_", "").replace(".json", ""))
                    existing.append(idx)
                except:
                    pass
    return (max(existing) + 1) if existing else 1


def _load_inventory_batches(company_id, local_id):
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    folder = ensure_product_folder(safe_company, safe_local)
    inv_folder = os.path.join(folder, "inventory_transactions")
    batches = []

    legacy_path = os.path.join(folder, "inventory_transactions.json")
    if os.path.exists(legacy_path):
        try:
            with open(legacy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    batches.extend(data)
        except:
            pass

    if os.path.isdir(inv_folder):
        for fname in sorted(os.listdir(inv_folder)):
            if fname.startswith("inventory_transactions_") and fname.endswith(".json"):
                fpath = os.path.join(inv_folder, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        tx = json.load(f)
                        if isinstance(tx, dict):
                            batches.append(tx)
                except:
                    pass

    return batches


def save_inventory_transaction(
    company_id,
    local_id,
    received_date,
    stock_in,
    cost_price,
    supplier_id=None,
    supplier_name=None
):
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    folder = ensure_product_folder(safe_company, safe_local)
    inv_folder = os.path.join(folder, "inventory_transactions")
    os.makedirs(inv_folder, exist_ok=True)

    next_idx = _get_next_inventory_index(inv_folder)
    file_path = os.path.join(inv_folder, f"inventory_transactions_{next_idx}.json")

    product_obj = Product.query.filter_by(local_id=int(safe_local), company_id=int(safe_company)).first()
    raw_name = product_obj.name if product_obj else ""
    raw_desc = product_obj.description if product_obj else ""

    if supplier_id and not supplier_name:
        db_supplier = Supplier.query.filter_by(id=int(supplier_id), company_id=int(safe_company)).first()
        if db_supplier:
            supplier_name = db_supplier.supplier_name

    try:
        clean_date = datetime.strptime(received_date, '%d-%m-%Y').strftime('%Y-%m-%d')
    except:
        try:
            clean_date = datetime.strptime(received_date, '%d/%m/%Y').strftime('%Y-%m-%d')
        except:
            clean_date = received_date

    new_transaction = {
        "product_local_id": int(safe_local),
        "received_date": clean_date,
        "stock_in": int(stock_in),
        "cost_price": float(cost_price),
        "supplier_id": int(supplier_id) if supplier_id else None,
        "supplier_name": supplier_name or 'מלאי פתיחה / כללי', 
        "name": raw_name,
        "description": raw_desc
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(new_transaction, f, ensure_ascii=False, indent=4)
        
    return file_path


def load_item_translated(product, language, company_id=None):
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(str(language).lower().strip(), language)

    if not company_id:
        company_id = getattr(product, "company_id", None) or session.get("company_id")
    local_id = getattr(product, "local_id", None)

    fallback_data = {
        "name": product.name or "",
        "description": product.description or "",
        "supplier_name": "מלאי פתיחה / כללי",
        "sku": product.sku or str(local_id),
        "price": float(product.price or 0.0),
        "income_category": product.income_category or "product",
        "cost_price": float(product.cost_price or 0.0),
        "stock_in": int(product.quantity or 0),
        "stock_out": 0,
        "batches": []
    }

    if not company_id or not local_id:
        return fallback_data

    # המרה כפויה ל-str כדי למנוע לצמיתות נפילה לפאלבק
    prod_data = load_item_file(str(company_id).strip(), str(local_id).strip())
    
    if not prod_data:
        return fallback_data

    batches = _load_inventory_batches(str(company_id).strip(), str(local_id).strip())
    total_stock_in = sum(int(b.get("stock_in", 0)) for b in batches) if batches else 0

    target_id_str = str(local_id).strip()
    actual_out = db.session.query(func.sum(InvoiceItem.quantity)) \
        .join(Invoice).filter(
            InvoiceItem.product_id == db.cast(target_id_str, db.String),
            InvoiceItem.income_category == 'product',
            Invoice.company_id == int(company_id),
            Invoice.status != "canceled"
        ).scalar() or 0

    def get_val(field_key, default_val):
        field_data = prod_data.get(field_key)
        if not isinstance(field_data, dict):
            return str(field_data) if field_data else default_val or ""
        return field_data.get(lookup_lang) or field_data.get("he") or default_val or ""

    return {
        "name": get_val("name", fallback_data["name"]),
        "description": get_val("description", fallback_data["description"]),
        "supplier_name": get_val("supplier_name", fallback_data["supplier_name"]),
        "sku": str(prod_data.get("sku", product.sku or str(local_id))),
        "price": float(prod_data.get("price", product.price or 0.0)),
        "income_category": prod_data.get("income_category", product.income_category or "product"),
        "cost_price": float(prod_data.get("cost_price", product.cost_price or 0.0)),
        "stock_in": int(total_stock_in),
        "stock_out": int(actual_out),
        "batches": batches
    }


def translate_product_in_background(
    product_id,
    company_id,
    name,
    description,
    price,
    income_category,
    sku=None,
    supplier_name=None,  
    **kwargs
):
    # שומרים את אובייקט האפליקציה האקטיבי לפני פתיחת הטרד
    app_instance = current_app._get_current_object()

    def background_worker():
        # הזרקת הקונטקסט בצורה קשיחה לתוך הטרד למניעת קריסות ברנדר
        with app_instance.app_context():
            try:
                safe_prod_id = int(product_id)
                safe_comp_id = int(company_id)
            except (ValueError, TypeError):
                return

            product = Product.query.filter_by(id=safe_prod_id, company_id=safe_comp_id).first()
            if not product:
                return

            local_id = product.local_id
            folder = ensure_product_folder(str(safe_comp_id), str(local_id))
            file_path = os.path.join(folder, "product.json")
            
            if os.path.isfile(file_path):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        existing_data = json.load(f)
                        if isinstance(existing_data.get("name"), dict) and len(existing_data["name"]) > 1:
                            if existing_data["name"].get("de") != existing_data["name"].get("he"):
                                return
                except:
                    pass

            final_sku = sku if sku else product.sku
            current_supplier_id = getattr(product, 'supplier_id', None)

            # הרצת תרגום ישירה בתוך הקונטקסט המבוטח
            run_product_translation(
                str(safe_comp_id),
                str(local_id),
                name,
                description,
                price,
                income_category,
                product.cost_price or 0.0,
                product.quantity or 0,
                0,
                current_supplier_id,                 
                supplier_name or "מלאי פתיחה / כללי", 
                product.received_date,
                final_sku
            )

    thread = threading.Thread(target=background_worker)
    thread.daemon = True
    thread.start()


def run_product_translation(
    company_id,
    local_id,
    name,
    description,
    price,
    income_category,
    cost_price=0.0,
    stock_in=0,
    stock_out=0,
    supplier_id=None,
    supplier_name=None,
    received_date=None,
    sku=None
):
    try:
        # אבטחת מחרוזות נקיות לחלוטין לתוך מנוע התרגום הראשי
        name_trans = generate_translations(str(name or "").strip())
        desc_trans = generate_translations(str(description or "").strip())
        supplier_trans = generate_translations(str(supplier_name or "מלאי פתיחה / כללי").strip())

        def clean_translation_dict(trans_dict, original_text):
            if not isinstance(trans_dict, dict):
                return {"he": original_text}
            for lang in list(trans_dict.keys()):
                val = trans_dict.get(lang)
                if not val or any(err in str(val) for err in ["Error 500", "That’s an error", "undefined", "null"]):
                    trans_dict[lang] = original_text
            if "he" not in trans_dict:
                trans_dict["he"] = original_text
            return trans_dict

        name_clean = clean_translation_dict(name_trans, name or "")
        desc_clean = clean_translation_dict(desc_trans, description or "")
        supplier_clean = clean_translation_dict(supplier_trans, supplier_name or "מלאי פתיחה / כללי")

        batches = _load_inventory_batches(str(company_id).strip(), str(local_id).strip())
        if not batches and stock_in > 0:
            try:
                if isinstance(received_date, str):
                    clean_date = received_date
                else:
                    clean_date = datetime.today().strftime('%Y-%m-%d')
            except:
                clean_date = datetime.today().strftime('%Y-%m-%d')

            batches = [{
                "product_local_id": int(local_id),
                "received_date": clean_date,
                "stock_in": int(stock_in or 0),
                "cost_price": float(cost_price or 0.0),
                "supplier_id": int(supplier_id) if supplier_id else None,
                "supplier_name": supplier_name or 'מלאי פתיחה / כללי',
                "name": name or "",
                "description": description or ""
            }]

        save_item_file(
            company_id=str(company_id).strip(),
            local_id=str(local_id).strip(),
            name_trans=name_clean,
            desc_trans=desc_clean,
            price=float(price or 0.0),
            income_category=income_category or "service",
            cost_price=float(cost_price or 0.0),
            stock_out=int(stock_out or 0),
            sku=sku,
            batches=batches,
            supplier_name_trans=supplier_clean
        )
        print(f"✔ Product and Supplier translation saved successfully for local_id {local_id}")
    except Exception as e:
        print(f"⚠ Product translation failed: {e}")


# -----------------------------------------------------------
# Transactions Helper - Save & Load JSON Translations (FULL COMPATIBLE LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_transaction_folder(company_id, local_id):
    base_dir = current_app.config.get("TRANSACTIONS_DIR", "static/transactions")
    # אבטחת המרה כפויה ל-str של שני המזהים למניעת בעיות נתיבים בלינוקס
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    folder = os.path.join(base_dir, f"{safe_company}_{safe_local}")
    os.makedirs(folder, exist_ok=True)
    return folder


def translate_transaction_in_background(
    transaction_id,
    company_id,
    description,
    amount,
    type_trans,
    category_id,
    currency_code=None,
    cost_price=0.0,
    income_category='service'
):
    # שומרים את אובייקט האפליקציה האקטיבי של Flask לפני פתיחת הטרד
    app_instance = current_app._get_current_object()

    def background_worker():
        # מזריקים את הקונטקסט בצורה קשיחה לתוך הטרד למניעת קריסות Context ברנדר
        with app_instance.app_context():
            try:
                safe_trans_id = int(transaction_id)
                safe_comp_id = int(company_id)
            except (ValueError, TypeError):
                return

            transaction = Transaction.query.filter_by(id=safe_trans_id, company_id=safe_comp_id).first()
            if not transaction:
                print(f"⚠ translate_transaction_in_background: transaction {safe_trans_id} not found")
                return

            local_id = transaction.local_id
            if not local_id:
                return

            # הרצת התרגום בתוך הקונטקסט המאובטח והפיכת מזהים ל-str
            run_transaction_translation(
                str(safe_comp_id),
                str(local_id),
                description,
                amount,
                type_trans,
                category_id,
                currency_code,
                cost_price,
                income_category
            )

    thread = threading.Thread(target=background_worker)
    thread.daemon = True
    thread.start()


def run_transaction_translation(
    company_id,
    local_id,
    description,
    amount,
    type_trans,
    category_id,
    currency_code=None,
    cost_price=0.0,
    income_category='service'
):
    try:
        # אבטחת מחרוזות נקיות לתוך מנוע התרגום הראשי
        desc_trans = generate_translations(str(description or "").strip())

        save_transaction_file(
            str(company_id).strip(),
            str(local_id).strip(),
            desc_trans,
            float(amount or 0.0),
            type_trans,
            category_id,
            currency_code,
            float(cost_price or 0.0),
            income_category or 'service'
        )

        print(f"✔ Transaction translation saved for company {company_id}, local_id {local_id}")

    except Exception as e:
        print(f"⚠ Transaction translation failed for company {company_id}, local_id {local_id}: {e}")


def save_transaction_file(
    company_id,
    local_id,
    desc_trans,
    amount,
    type_trans,
    category_id,
    currency_code=None,
    cost_price=0.0,
    income_category='service'
):
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    # המרה ל-str מתבצעת אוטומטית בפנים
    folder = ensure_transaction_folder(safe_company, safe_local)
    file_path = os.path.join(folder, "transaction.json")

    try:
        final_id_value = int(safe_local)
    except (ValueError, TypeError):
        final_id_value = safe_local

    data = {
        "id": final_id_value,
        "amount": float(amount or 0.0),
        "type": type_trans,
        "category_id": category_id,
        "currency": currency_code,
        "cost_price": float(cost_price or 0.0),
        "income_category": income_category or 'service',
        "description": desc_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return file_path


def load_transaction_file(company_id_or_obj, local_id=None):
    final_company_id = None
    final_local_id = None

    if hasattr(company_id_or_obj, 'local_id'):
        final_company_id = getattr(company_id_or_obj, 'company_id', None)
        final_local_id = company_id_or_obj.local_id
    elif company_id_or_obj and local_id:
        final_company_id = company_id_or_obj
        final_local_id = local_id
    elif company_id_or_obj and not local_id:
        try:
            safe_id = int(company_id_or_obj)
            transaction_obj = Transaction.query.filter_by(id=safe_id).first()
            if transaction_obj:
                final_company_id = transaction_obj.company_id
                final_local_id = transaction_obj.local_id
        except (ValueError, TypeError):
            return None

    if not final_company_id or not final_local_id:
        return None

    # תיקון קריטי: שימוש ב-ensure_transaction_folder במקום בנייה ידנית כדי למנוע נתק בסנכרון!
    folder = ensure_transaction_folder(final_company_id, final_local_id)
    file_path = os.path.join(folder, "transaction.json")

    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("⚠ load_transaction_file error:", e)
        return None


def load_transaction_translated(transaction, language, company_id=None):
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(str(language).lower().strip(), language)

    fallback_data = {
        "description": transaction.description or "",
        "amount": transaction.amount or 0.0,
        "type": getattr(transaction, 'type', '') or getattr(transaction, 'type_trans', ''),
        "category_id": transaction.category_id or None,
        "currency": getattr(transaction, 'currency', None) or getattr(transaction, 'currency_code', None),
        "cost_price": getattr(transaction, 'cost_price', 0.0) or 0.0,
        "income_category": getattr(transaction, 'income_category', 'service') or "service"
    }

    if not company_id:
        company_id = getattr(transaction, "company_id", None) or session.get("company_id")

    local_id = getattr(transaction, "local_id", None)

    if not company_id or not local_id:
        return fallback_data

    # המרה מפורשת ל-str של המזהים לפני השליחה לקובץ
    data = load_transaction_file(transaction)

    if not data:
        return fallback_data

    field_data = data.get("description", {})
    if not isinstance(field_data, dict):
        field_data = {}

    return {
        "description": (
            field_data.get(lookup_lang)
            or field_data.get("he")
            or transaction.description
            or ""
        ),
        "amount": data.get("amount", transaction.amount),
        "type": data.get("type", getattr(transaction, 'type_trans', '') or getattr(transaction, 'type', '')),
        "category_id": data.get("category_id", transaction.category_id),
        "currency": data.get("currency", getattr(transaction, 'currency_code', None) or getattr(transaction, 'currency', None)),
        "cost_price": data.get("cost_price", transaction.cost_price),
        "income_category": data.get("income_category", transaction.income_category)
    }


# -----------------------------------------------------------
# Categories Helper - Save & Load JSON Translations (FULL COMPATIBLE LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_category_folder(company_id, local_id):
    base_dir = current_app.config.get("CATEGORIES_DIR", "static/categories")
    # אבטחת המרה כפויה ל-str של שני המזהים למניעת בעיות נתיבים בלינוקס
    safe_company = str(company_id).strip()
    safe_local = str(local_id).strip()
    
    folder = os.path.join(base_dir, f"{safe_company}_{safe_local}")
    return folder


def translate_category_in_background(cat_id, company_id, raw_name_text):
    # שומרים את אובייקט האפליקציה האקטיבי של Flask לפני פתיחת הטרד
    app_instance = current_app._get_current_object()

    def background_worker():
        # מזריקים את הקונטקסט בצורה קשיחה לתוך הטרד למניעת קריסות Context ברנדר
        with app_instance.app_context():
            try:
                safe_cat_id = int(cat_id)
                safe_comp_id = int(company_id)
            except (ValueError, TypeError):
                return

            category = Category.query.filter_by(id=safe_cat_id, company_id=safe_comp_id).first()
            if not category:
                print(f"⚠ translate_category_in_background: category {safe_cat_id} not found")
                return

            local_id = category.local_id
            if not local_id:
                return

            # הרצת התרגום בתוך הקונטקסט המאובטח והפיכת מזהים ל-str
            run_category_translation(str(safe_comp_id), str(local_id), raw_name_text)

    thread = threading.Thread(target=background_worker)
    thread.daemon = True
    thread.start()


def run_category_translation(company_id, local_id, raw_name_text):
    try:
        # אבטחת מחרוזות נקיות לתוך מנוע התרגום הראשי
        name_trans = generate_translations(str(raw_name_text or "").strip())

        def clean_translation_dict(trans_dict, original_text):
            if not trans_dict:
                return {}
            for lang, val in list(trans_dict.items()):
                if val and ("Error 500" in val or "That’s an error" in val or "Please try again later" in val):
                    trans_dict[lang] = original_text
            return trans_dict

        name_clean = clean_translation_dict(name_trans, raw_name_text or "")

        save_category_file(str(company_id).strip(), str(local_id).strip(), name_clean)
        print(f"✔ Category translation saved for company {company_id}, local_id {local_id}")
    except Exception as e:
        print(f"⚠ Category translation failed for company {company_id}, local_id {local_id}: {e}")


def save_category_file(company_id, local_id, name_trans):
    # המרה ל-str מתבצעת אוטומטית בפנים
    folder = ensure_category_folder(company_id, local_id)
    
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "category.json")

    data = {
        "name": name_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return file_path


def load_category_file(company_id_or_obj, local_id=None):
    final_company_id = None
    final_local_id = None

    if hasattr(company_id_or_obj, 'local_id'):
        final_company_id = getattr(company_id_or_obj, 'company_id', None)
        final_local_id = company_id_or_obj.local_id
    elif company_id_or_obj and local_id:
        final_company_id = company_id_or_obj
        final_local_id = local_id
    elif company_id_or_obj and not local_id:
        try:
            safe_id = int(company_id_or_obj)
            cat_obj = Category.query.filter_by(id=safe_id).first()
            if cat_obj:
                final_company_id = cat_obj.company_id
                final_local_id = cat_obj.local_id
        except (ValueError, TypeError):
            return None

    if not final_company_id or not final_local_id:
        return None

    # תיקון קריטי: שימוש ב-ensure_category_folder במקום בנייה ידנית כדי למנוע נתק בסנכרון!
    folder = ensure_category_folder(final_company_id, final_local_id)
    file_path = os.path.join(folder, "category.json")

    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("⚠ load_category_file error:", e)
        return None


def load_category_translated(category, language, company_id=None):
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(str(language).lower().strip(), language)

    if not company_id:
        company_id = getattr(category, "company_id", None) or session.get("company_id")

    local_id = getattr(category, "local_id", None)

    fallback_data = {
        "name": getattr(category, "name", "") or ""
    }

    if not company_id or not local_id:
        return fallback_data

    # המרה מפורשת ל-str של המזהים לפני השליחה לקובץ
    data = load_category_file(str(company_id).strip(), str(local_id).strip())

    if not data:
        return fallback_data

    field_data = data.get("name", {})
    if not isinstance(field_data, dict):
        return fallback_data

    return {
        "name": (
            field_data.get(lookup_lang)
            or field_data.get("he")
            or getattr(category, "name", "")
            or ""
        )
    }


# -----------------------------------------------------------
# Invoice Cancellation Translation Background Tasks (AIRTIGHT)
# -----------------------------------------------------------

def ensure_cancellation_folder(company_id, invoice_id):
    base_dir = current_app.config.get("CANCELLATIONS_DIR", "static/cancellations")
    # אבטחת המרה כפויה ל-str של שני המזהים למניעת בעיות נתיבים בלינוקס
    safe_company = str(company_id).strip()
    safe_invoice = str(invoice_id).strip()
    
    folder = os.path.join(base_dir, f"{safe_company}_{safe_invoice}")
    return folder


def translate_cancellation_in_background(invoice_id, company_id, text_to_translate):
    # שומרים את אובייקט האפליקציה האקטיבי של Flask לפני פתיחת הטרד
    app_instance = current_app._get_current_object()

    def background_worker():
        # מזריקים את הקונטקסט בצורה קשיחה לתוך הטרד למניעת קריסות Context ברנדר
        with app_instance.app_context():
            try:
                safe_inv_id = int(invoice_id)
                safe_comp_id = int(company_id)
            except (ValueError, TypeError):
                return

            # הרצת התרגום בתוך הקונטקסט המאובטח והפיכת מזהים ל-str
            run_cancellation_translation(str(safe_comp_id), str(safe_inv_id), text_to_translate)

    thread = threading.Thread(target=background_worker)
    thread.daemon = True
    thread.start()


def run_cancellation_translation(company_id, invoice_id):
    try:
        # אבטחת מחרוזות נקיות לתוך מנוע התרגום הראשי
        reason_trans = generate_translations(str(text_to_translate or "").strip())

        def clean_translation_dict(trans_dict, original_text):
            if not trans_dict:
                return {}
            for lang, val in list(trans_dict.items()):
                if val and ("Error 500" in val or "That’s an error" in val or "Please try again later" in val):
                    trans_dict[lang] = original_text
            return trans_dict

        reason_clean = clean_translation_dict(reason_trans, text_to_translate or "")

        save_cancellation_file(str(company_id).strip(), str(invoice_id).strip(), reason_clean)

    except Exception as e:
        print(f"⚠ Cancellation Translation failed for company {company_id}, invoice {invoice_id}: {e}")


def save_cancellation_file(company_id, invoice_id, reason_trans):
    # המרה ל-str מתבצעת אוטומטית בפנים
    folder = ensure_cancellation_folder(company_id, invoice_id)
    
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "cancellation.json")

    data = {
        "cancellation_reason": reason_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✔ Airtight Cancellation JSON document created at: {file_path}")
    return file_path


def load_cancellation_file(company_id, invoice_id):
    if not company_id or not invoice_id:
        return None

    # תיקון קריטי: שימוש ב-ensure_cancellation_folder במקום בנייה ידנית כדי למנוע נתק בסנכרון!
    folder = ensure_cancellation_folder(company_id, invoice_id)
    file_path = os.path.join(folder, "cancellation.json")

    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("⚠ load_cancellation_file error:", e)
        return None


def load_cancellation_translated(invoice, language, company_id=None):
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(str(language).lower().strip(), language)

    if not company_id:
        company_id = getattr(invoice, "company_id", None) or session.get("company_id")

    invoice_id = getattr(invoice, "id", None)

    fallback_data = {
        "cancellation_reason": getattr(invoice, 'cancellation_reason', '') or ""
    }

    if not company_id or not invoice_id:
        return fallback_data["cancellation_reason"]

    # המרה מפורשת ל-str של המזהים לפני השליחה לקובץ
    data = load_cancellation_file(str(company_id).strip(), str(invoice_id).strip())

    if not data:
        return fallback_data["cancellation_reason"]

    field_data = data.get("cancellation_reason", {})
    if not isinstance(field_data, dict):
        return fallback_data["cancellation_reason"]

    return field_data.get(lookup_lang) or field_data.get("he") or fallback_data["cancellation_reason"]


# -----------------------------------------------------------
#  Uploaded Attachments - Save File (Multi-Tenant Secure)
# -----------------------------------------------------------

def get_transaction_upload_path(filename, company_id):
    # שימוש ב-safe_company למניעת קריסות נתיבים וטיפוסים מול Postgres ברנדר
    safe_company = str(company_id).strip()
    
    # שליפת הנתיב הרשמי מתוך ה-Config המותאם ל-Local/Render
    base_dir = current_app.config.get("TRANSACTIONS_DIR") or TRANSACTIONS_DIR
    
    company_dir = os.path.join(base_dir, f"company_{safe_company}")
    os.makedirs(company_dir, exist_ok=True)

    # יצירת שם קובץ ייחודי מבוסס זמן למניעת דריסת קבצים
    unique_filename = f"{int(time.time())}_{secure_filename(filename)}"

    absolute_path = os.path.join(company_dir, unique_filename)
    
    # זה מונע לצמיתות את הבאג של uploads/uploads ומיישר קו עם ה-HTML המקורי שלך
    relative_path = os.path.join("transactions", f"company_{safe_company}", unique_filename)

    # החזרת נתיבים עם סלאשים תקינים התואמים לכל מערכת הפעלה (לינוקס/ווינדוס)
    return os.path.normpath(absolute_path), os.path.normpath(relative_path).replace("\\", "/")



@app.route('/static/uploads/transactions/company_<string:company_id>/<string:filename>')
@app.route('/uploads/transactions/company_<string:company_id>/<string:filename>')
@app.route('/static/uploads/uploads/transactions/company_<string:company_id>/<string:filename>')
def serve_render_transaction_file(company_id, filename):
    # משיכת נתיב האב הפיזי של הטרנזקציות (ברנדר זה /data/uploads/transactions)
    base_dir = app.config.get("TRANSACTIONS_DIR") or TRANSACTIONS_DIR
    
    target_dir = os.path.join(base_dir, f"company_{str(company_id).strip()}")
    
    return send_from_directory(target_dir, filename, as_attachment=False)





# -----------------------------------------------------------
#  Company Views & Management (Multi-Tenant Secure)
# -----------------------------------------------------------

def _company_notice(message, ok=False):
    """A message that is ALWAYS visible (it does not depend on flash being shown in the template)."""
    bg = "#d4edda" if ok else "#fde2e2"
    return f"""
    <div dir="rtl" style="font-family:Arial;padding:40px;max-width:720px">
      <div style="padding:20px;border-radius:8px;background:{bg};font-size:20px">{escape(message)}</div>
      <p style="margin-top:24px">
        <button onclick="history.back()" style="padding:10px 20px;font-size:16px">חזרה לטופס</button>
        <a href="{url_for('company')}" style="margin-right:20px;font-size:16px">לדף החברה</a>
      </p>
    </div>
    """


@app.route('/company', methods=['GET', 'POST'])
@login_required
def company():
    try:
        language = get_lang()

        is_owner_user = is_owner()
        if not is_owner_user and _active_role() == 'customer':
            return redirect(url_for('customer_dashboard_router'))

        # ---------- find the company this user works for ----------
        company_obj = None
        if is_owner_user:
            company_obj = db.session.get(Company, OWNER_COMPANY_ID)
        else:
            for cid in (getattr(current_user, 'company_id', None), session.get('company_id')):
                cid = _to_int(cid)
                if cid:
                    company_obj = db.session.get(Company, cid)
                    if company_obj:
                        break
            if not company_obj:
                for mail_value in (session.get('company_email'), getattr(current_user, 'email', None)):
                    if mail_value:
                        company_obj = Company.query.filter(
                            db.func.lower(Company.email) == mail_value.lower()).first()
                        if company_obj:
                            break

        # -------- GET --------
        if request.method == 'GET':
            return render_template(
                'company.html',
                company=load_company_translated(company_obj, language),
                company_db=company_obj
            )

        # -------- POST --------
        mode = "UPDATE" if company_obj else "CREATE"
        print(f"📝 /company POST: mode={mode} company_id={company_obj.id if company_obj else None} "
              f"fields_received={sorted(request.form.keys())}")

        def field(name, max_len, current=''):
            # a field the browser did NOT send keeps its current value
            if name not in request.form:
                return (current or '')
            return _clip(request.form.get(name), max_len)

        cur = company_obj

        form_name = field('name', 255, cur.name if cur else '')
        if not form_name:
            print("⛔ COMPANY NOT SAVED: empty company name")
            return _company_notice("שם חברה הוא שדה חובה")

        email_value = field('email', 255, cur.email if cur else '').lower()
        if not email_value and cur:
            email_value = (cur.email or '')          # the e-mail is the login identity: never wiped
        if email_value and '@' not in email_value:
            print(f"⛔ COMPANY NOT SAVED: bad e-mail '{email_value}'")
            return _company_notice("כתובת האימייל אינה תקינה")

        if 'logo' in request.form:
            logo_value = (request.form.get('logo') or '').strip()
        else:
            logo_value = (cur.logo if cur else '') or ''

        company_data = {
            "name": form_name,
            "company_id_number": field('company_id_number', 100, cur.company_id_number if cur else ''),
            "deduction_file": field('deduction_file', 100, cur.deduction_file if cur else ''),
            "address": field('address', 255, cur.address if cur else ''),
            "city": field('city', 100, cur.city if cur else ''),
            "postal_code": field('postal_code', 100, cur.postal_code if cur else ''),
            "phone": field('phone', 100, cur.phone if cur else ''),
            "email": email_value,
            "logo": logo_value
        }

        # ---------- duplicate checks: ONLY for a value that really changed ----------
        other_id = cur.id if cur else 0

        name_changed = (not cur) or (company_data["name"].strip().lower() != (cur.name or '').strip().lower())
        if name_changed:
            clash = Company.query.filter(
                Company.id != other_id,
                db.func.lower(Company.name) == company_data["name"].lower()).first()
            if clash:
                print(f"⛔ COMPANY NOT SAVED: the name '{company_data['name']}' already belongs to company id={clash.id} "
                      f"(this company id={other_id})")
                return _company_notice("חברה אחרת עם שם זה כבר קיימת במערכת")

        email_changed = (not cur) or (company_data["email"] != (cur.email or '').lower())
        if company_data["email"] and email_changed:
            clash = Company.query.filter(
                Company.id != other_id,
                db.func.lower(Company.email) == company_data["email"]).first()
            if clash:
                print(f"⛔ COMPANY NOT SAVED: the e-mail '{company_data['email']}' already belongs to company id={clash.id} "
                      f"(this company id={other_id})")
                return _company_notice("חברה אחרת עם אימייל זה כבר קיימת במערכת")

        # ---------- UPDATE or CREATE ----------
        if company_obj:
            old_email = company_obj.email
            for key, value in company_data.items():
                setattr(company_obj, key, value)

            # if the company e-mail changed, the manager login e-mail follows it
            if (not is_owner_user and company_obj.email
                    and (old_email or '').lower() != company_obj.email.lower()):
                me = db.session.get(User, current_user.id) if getattr(current_user, 'id', None) else None
                if me and (me.email or '').lower() == (old_email or '').lower():
                    me.email = company_obj.email

            db.session.commit()
            msg = ("נתוני חברת הבעלים עודכנו בהצלחה!" if is_owner_user
                   else "נתוני החברה עודכנו בהצלחה!")
        elif is_owner_user:
            company_obj = Company(id=OWNER_COMPANY_ID, translations_json="{}", **company_data)
            db.session.add(company_obj)
            db.session.commit()
            sync_sequence(Company)
            msg = "נתוני חברת הבעלים נוצרו בהצלחה!"
        else:
            # no company exists for this user yet: create one, the database assigns the id
            company_obj = Company(translations_json="{}", **company_data)
            db.session.add(company_obj)
            db.session.flush()
            current_user.company_id = company_obj.id
            db.session.commit()
            msg = "חברה חדשה נוצרה בהצלחה!"

        print(f"✔ COMPANY SAVED ({mode}): id={company_obj.id} name='{company_obj.name}' "
              f"address='{company_obj.address}' city='{company_obj.city}' phone='{company_obj.phone}'")

        # keep the session in step with the saved data
        session['company_id'] = company_obj.id
        session['company_name'] = company_obj.name
        session['company_email'] = company_obj.email

        translate_company_in_background(
            company_id=str(company_obj.id),
            name=company_obj.name,
            id_number=company_obj.company_id_number,
            deduction_file=company_obj.deduction_file,
            address=company_obj.address,
            city=company_obj.city,
            postal_code=company_obj.postal_code,
            phone=company_obj.phone,
            email=company_obj.email,
            logo=company_obj.logo,
            source_lang=language
        )

        flash(msg, "success")
        return redirect(url_for('company'))

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"⛔ COMPANY NOT SAVED: exception {type(e).__name__}: {e}")
        return _company_notice(f"שגיאה בשמירת נתוני החברה ({type(e).__name__})")
        
# ----------------------
#  Clear Company Results Form
# ----------------------

@app.route('/clear_company_results', methods=['POST'])
@login_required
def clear_company_results():
    try:
        language = get_lang()

        is_owner_user = bool(session.get('owner_access') or getattr(current_user, 'role', '') == 'owner')
        company_id = OWNER_COMPANY_ID if is_owner_user else current_user.company_id

        if not company_id:
            flash("No active company.", "warning")
            return redirect(url_for('company'))

        # same helper as everywhere else (COMPANY_DIR, real company id)
        file_path = os.path.join(ensure_company_folder(company_id), f"{company_id}.json")
        file_deleted = False
        if os.path.exists(file_path):
            os.remove(file_path)
            file_deleted = True

        company_obj = db.session.get(Company, company_id)
        if company_obj:
            if company_id == OWNER_COMPANY_ID:
                company_obj.name = "לרקוד על הגג"
                company_obj.email = OWNER_USERNAME
                company_obj.address = "פרפר 15"
                company_obj.city = "אילת , ישראל"
                company_obj.postal_code = "88000"
                company_obj.company_id_number = "123456789"
                company_obj.deduction_file = "987654321"
                company_obj.phone = "08-9996666"
                company_obj.logo = ""
            else:
                # email is NOT cleared: it is the company's login identity
                company_obj.company_id_number = ""
                company_obj.deduction_file = ""
                company_obj.address = ""
                company_obj.city = ""
                company_obj.postal_code = ""
                company_obj.phone = ""
                company_obj.logo = ""
            db.session.commit()

            translate_company_in_background(
                company_id=str(company_obj.id),
                name=company_obj.name,
                id_number=company_obj.company_id_number,
                deduction_file=company_obj.deduction_file,
                address=company_obj.address,
                city=company_obj.city,
                postal_code=company_obj.postal_code,
                phone=company_obj.phone,
                email=company_obj.email,
                logo=company_obj.logo,
                source_lang=language
            )

        flash("נתוני וקובץ התרגומים של החברה נמחקו ואופסו בהצלחה." if file_deleted
              else "נתוני החברה אופסו בהצלחה.", "success")
        return redirect(url_for('company'))

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה במחיקת קובץ התרגומים.", "danger")
        return redirect(url_for('company'))
       

# --------------------
# Conected To Tax Office To Ger Recive Data Permission Invoice Number Data
# ----------------------

IRS_API_URL = "https://api.misim.gov.il/invoices"  # כתובת לדוגמה, בפועל תקבל מהרשות

def send_invoice_to_tax_authority(invoice_data):
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer YOUR_API_TOKEN"  # טוקן שתקבל מרשות המיסים
    }
    response = requests.post(IRS_API_URL, headers=headers, data=json.dumps(invoice_data))
    
    if response.status_code == 200:
        result = response.json()
        allocation_number = result.get("allocation_number")
        return allocation_number
    else:
        raise Exception(f"Tax API error: {response.status_code} {response.text}")



# =====================================================================
#  INVOICES (Multi-Tenant Secure) - one block, no duplicates
# =====================================================================

# ---------------------------------------------------------------------
#  Who is looking at the page
# ---------------------------------------------------------------------

def _active_company_id():
    """Real company id of the logged in user (owner = company 1). None if there is none."""
    if is_owner():
        return OWNER_COMPANY_ID
    cid = getattr(current_user, 'company_id', None) or session.get('company_id')
    try:
        return int(cid) if cid else None
    except (TypeError, ValueError):
        return None


def _is_customer_viewer():
    return (not is_owner()) and _active_role() == 'customer'


def _own_customer_id():
    own = _to_int(session.get('customer_id'))
    if own is None:
        own = _to_int(getattr(current_user, 'customer_id', None))
    return own


def _staff_guard():
    """Invoice pages are for owner / manager / employee. A customer goes to his own dashboard."""
    if _is_customer_viewer():
        return redirect(url_for('customer_dashboard_router'))
    return None


# ---------------------------------------------------------------------
#  Send to the tax authority (JSON)
# ---------------------------------------------------------------------

@app.route("/send_invoice", methods=["POST"])
@login_required
def send_invoice():
    try:
        if _is_customer_viewer():
            return jsonify({"status": "error", "message": "Forbidden"}), 403

        data = request.get_json(silent=True) or {}
        invoice_id = _to_int(data.get("invoice_id"))
        company_id = _active_company_id()

        if not company_id:
            return jsonify({"status": "error", "message": "No active company"}), 400
        if invoice_id is None:
            return jsonify({"status": "error", "message": "Invalid invoice id"}), 400

        invoice_obj = db.session.get(Invoice, invoice_id)
        if not invoice_obj or invoice_obj.company_id != company_id:
            return jsonify({"status": "error", "message": "Invoice not found"}), 404

        inv_payload = get_invoice_data(invoice_id, company_id=company_id)
        if not inv_payload:
            return jsonify({"status": "error", "message": "Invoice not found"}), 404

        allocation_number = send_invoice_to_tax_authority(inv_payload)

        invoice_obj = db.session.get(Invoice, invoice_id)
        if not invoice_obj or invoice_obj.company_id != company_id:
            return jsonify({"status": "error", "message": "Database sync failed for allocation"}), 500

        invoice_obj.allocation_number = allocation_number
        db.session.commit()

        return jsonify({"status": "success", "allocation_number": allocation_number})

    except Exception:
        db.session.rollback()
        app.logger.exception("send_invoice failed")
        return jsonify({"status": "error", "message": "Server error"}), 500


# ---------------------------------------------------------------------
#  Next invoice number (numeric max: "9" must not be bigger than "10")
# ---------------------------------------------------------------------

def get_next_invoice_number(company_id=None):
    if company_id is None:
        company_id = _active_company_id()
    if not company_id:
        raise ValueError("get_next_invoice_number: missing company_id")
    company_id = int(company_id)

    try:
        result = (
            db.session.query(db.func.max(cast(Invoice.invoice_number, Integer)))
            .filter(Invoice.company_id == company_id)
            .scalar()
        )
        return int(result or 0) + 1
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed calculating next invoice number")
        raise


# ---------------------------------------------------------------------
#  Self-invoice helpers (customer 0 = the company itself)
# ---------------------------------------------------------------------

def _is_self_customer(customer_id, customer_obj=None):
    """Self invoice: empty / 0, or the technical customer row of the company."""
    if customer_id in (None, 0, "0", ""):
        return True
    return bool(customer_obj and customer_obj.customer_name == SELF_CUSTOMER_NAME)


def _self_customer_json(company_obj, company_translated):
    """The active company as a 'customer' of a self invoice (id is always "0" for the JS)."""
    if not company_obj:
        return {}
    return {
        "id": "0",
        "local_id": None,
        "customer_name": f"★ {company_translated.get('name') or company_obj.name or ''}",
        "id_number": company_translated.get('company_id_number') or company_obj.company_id_number or "",
        "address": company_translated.get('address') or company_obj.address or "",
        "city": company_translated.get('city') or company_obj.city or "",
        "postal_code": company_obj.postal_code or "",
        "phone": company_obj.phone or "",
        "email": company_obj.email or "",
        "message": ""
    }


def _get_or_create_self_customer(company_id):
    """
    The technical customer row that represents the company itself (self invoice).
    It gets local_id = 0, so the real customers of every company start from 1.
    The id is assigned by the database (never forced).
    """
    company_obj = db.session.get(Company, company_id)
    company_email = ((company_obj.email if company_obj else None) or "").strip().lower()

    self_customer = Customer.query.filter_by(
        company_id=company_id,
        customer_name=SELF_CUSTOMER_NAME
    ).first()

    if self_customer:
        if company_email and self_customer.email != company_email:
            self_customer.email = company_email
            db.session.flush()
        return self_customer

    self_customer = Customer(
        company_id=company_id,
        local_id=0,
        customer_name=SELF_CUSTOMER_NAME,
        email=company_email,
        phone=(company_obj.phone if company_obj and company_obj.phone else "000"),
        id_number=(getattr(company_obj, 'company_id_number', None) or ""),
        address=(company_obj.address if company_obj else None),
        city=(company_obj.city if company_obj else None),
        postal_code=(company_obj.postal_code if company_obj else None),
        role='customer',
        is_active=True
    )
    db.session.add(self_customer)
    db.session.flush()
    return self_customer


# ---------------------------------------------------------------------
#  Shared data for the invoice page
# ---------------------------------------------------------------------

def base_invoice_context(customer_id=None):
    language = get_lang()
    company_id = _active_company_id()
    customer_viewer = _is_customer_viewer()

    # a customer account always sees only himself
    if customer_viewer:
        own_id = _own_customer_id()
        own_c = Customer.query.filter_by(id=own_id, company_id=company_id).first() \
            if (own_id and company_id) else None
        customer_id = _customer_key(own_c) if own_c else None

    company_obj = db.session.get(Company, company_id) if company_id else None
    company_translated = load_company_translated(company_obj, language) if company_obj else {}

    products_list_for_js = []
    all_customers_json = []

    # products (with cost prices) and the customers list: staff only
    if company_id and not customer_viewer:
        for p in Product.query.filter_by(company_id=company_id).all():
            translated_core = load_item_translated(p, language, company_id)
            products_list_for_js.append({
                "id": p.id,
                "local_id": p.local_id if p.local_id is not None else p.id,
                "sku": p.sku if p.sku else (str(p.local_id) if p.local_id else str(p.id)),
                "name": translated_core.get("name") or p.name or "",
                "description": translated_core.get("description") or p.description or "",
                "price": float(p.price or 0),
                "cost_price": float(p.cost_price or 0),
                "quantity": int(getattr(p, 'quantity', 0) or 0),
                "income_category": translated_core.get("income_category") or p.income_category or "service"
            })

        for c in Customer.query.filter(
                Customer.company_id == company_id,
                Customer.customer_name != SELF_CUSTOMER_NAME).all():
            trans = load_customer_translated(c, language, company_id) or {}
            all_customers_json.append({
                "id": c.id,
                "local_id": c.local_id,
                "customer_name": trans.get("name") or c.customer_name or "",
                "id_number": c.id_number or "",
                "address": trans.get("address") or c.address or "",
                "city": trans.get("city") or c.city or "",
                "postal_code": c.postal_code or "",
                "email": c.email or "",
                "phone": c.phone or "",
                "message": trans.get("message") or c.message or ""
            })

    # ----------------- ACTIVE CUSTOMER -----------------
    customer_data = None
    if customer_id is not None and company_id:
        check_c = _find_customer(company_id, customer_id) if str(customer_id) != "0" else None

        if _is_self_customer(customer_id, check_c):
            customer_data = _self_customer_json(company_obj, company_translated)
        elif check_c:
            trans = load_customer_translated(check_c, language, company_id) or {}
            customer_data = {
                "id": check_c.id,
                "local_id": check_c.local_id,
                "customer_name": trans.get("name") or check_c.customer_name or "",
                "address": trans.get("address") or check_c.address or "",
                "city": trans.get("city") or check_c.city or "",
                "postal_code": check_c.postal_code or "",
                "id_number": check_c.id_number or "",
                "phone": check_c.phone or "",
                "email": check_c.email or "",
                "message": trans.get("message") or check_c.message or ""
            }

    invoice_original_currency = getattr(company_obj, 'currency', 'ILS') if company_obj else 'ILS'
    current_currency = request.cookies.get('currency') or session.get('currency') or invoice_original_currency

    return {
        "products": products_list_for_js,
        "customer": customer_data,
        "all_customers_json": all_customers_json,
        "company": company_translated,
        "company_db": company_obj,
        "vat_options": list(range(0, 21)),
        "next_invoice_num": get_next_invoice_number(company_id=company_id) if company_id else 1,
        "invoice_original_currency": invoice_original_currency,
        "current_currency": current_currency
    }


# ---------------------------------------------------------------------
#  Product helpers
# ---------------------------------------------------------------------

def _find_product(company_id, pid, prefer_local=False):
    """Find a product by id or local_id (numeric only, so Postgres never compares INTEGER to text)."""
    if pid is None or not str(pid).isdigit():
        return None
    pid = int(pid)

    order = ['local_id', 'id'] if prefer_local else ['id', 'local_id']
    for field in order:
        prod = Product.query.filter_by(company_id=company_id, **{field: pid}).first()
        if prod:
            return prod
    return None


def _prod_key(prod):
    return prod.local_id if prod.local_id is not None else prod.id


# ---------------------------------------------------------------------
#  Full data of one invoice (view / edit)
# ---------------------------------------------------------------------

def invoice_context(invoice_id=None):
    invoice = None

    try:
        language = get_lang()
        company_id = _active_company_id()
        customer_viewer = _is_customer_viewer()

        # ----------------- LOAD INVOICE -----------------
        if invoice_id:
            try:
                invoice = db.session.get(Invoice, int(invoice_id))
            except (ValueError, TypeError):
                invoice = None
            if invoice and invoice.company_id != company_id:
                invoice = None
            # a customer may open only his own invoices
            if invoice and customer_viewer:
                own_id = _own_customer_id()
                if own_id is None or _to_int(invoice.customer_id) != own_id:
                    invoice = None

        target_company_id = int(invoice.company_id) if invoice else company_id

        # ----------------- COMPANY -----------------
        company_obj = db.session.get(Company, target_company_id) if target_company_id else None
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        # ----------------- CUSTOMER JSON -----------------
        customer_json = {}
        c_obj = None

        if invoice:
            if invoice.customer_id not in (None, 0, "0", ""):
                cid = _to_int(invoice.customer_id)
                if cid is not None:
                    c_obj = Customer.query.filter_by(id=cid, company_id=target_company_id).first()

            if _is_self_customer(invoice.customer_id, c_obj):
                customer_json = _self_customer_json(company_obj, company_translated)
            elif c_obj:
                trans = load_customer_translated(c_obj, language, target_company_id) or {}
                customer_json = {
                    "id": c_obj.id,
                    "local_id": c_obj.local_id,
                    "customer_name": trans.get("name") or c_obj.customer_name or "",
                    "address": trans.get("address") or c_obj.address or "",
                    "city": trans.get("city") or c_obj.city or "",
                    "postal_code": c_obj.postal_code or "",
                    "id_number": c_obj.id_number or "",
                    "phone": c_obj.phone or "",
                    "email": c_obj.email or "",
                    "message": trans.get("message") or c_obj.message or ""
                }

        # ----------------- ITEMS JSON (no cost prices for a customer) -----------------
        items_json = []
        if invoice:
            for item in InvoiceItem.query.filter_by(invoice_id=invoice.id).all():
                target_p = _find_product(target_company_id, item.product_id, prefer_local=True)

                row_sku = str(item.product_id)
                row_name_trans = item.description or ""

                if target_p:
                    translated_p_data = load_item_translated(target_p, language, target_company_id)
                    item_file_data = load_item_file(target_company_id, _prod_key(target_p)) or {}
                    row_sku = target_p.sku if target_p.sku else item_file_data.get("sku", str(_prod_key(target_p)))
                    row_name_trans = translated_p_data.get("name") or target_p.name or ""

                items_json.append({
                    "product_id": item.product_id,
                    "sku": row_sku,
                    "name": row_name_trans,
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "discount": float(item.discount or 0),
                    "total_price": float(item.total_price or 0),
                    "cost_price": 0.0 if customer_viewer else float(getattr(item, 'cost_price_at_time', 0.0) or 0.0)
                })

        # ----------------- ALL CUSTOMERS + PRODUCTS (staff only) -----------------
        all_customers_json = []
        products_json = []

        if not customer_viewer and target_company_id:
            for c in Customer.query.filter(
                    Customer.company_id == target_company_id,
                    Customer.customer_name != SELF_CUSTOMER_NAME
            ).order_by(Customer.customer_name).all():
                trans = load_customer_translated(c, language, target_company_id) or {}
                all_customers_json.append({
                    "id": c.id,
                    "local_id": c.local_id,
                    "customer_name": trans.get("name") or c.customer_name or "",
                    "id_number": c.id_number or "",
                    "address": trans.get("address") or c.address or "",
                    "city": trans.get("city") or c.city or "",
                    "postal_code": c.postal_code or "",
                    "email": c.email or "",
                    "phone": c.phone or "",
                    "message": trans.get("message") or c.message or ""
                })

            for p in Product.query.filter_by(company_id=target_company_id).all():
                item_file = load_item_file(target_company_id, _prod_key(p)) or {}
                translated_core = load_item_translated(p, language, target_company_id)

                p_name = translated_core.get("name") or p.name or ""
                i_cat = translated_core.get("income_category") or p.income_category or "product"
                target_pid_str = str(_prod_key(p)).strip()

                total_sold = db.session.query(db.func.sum(InvoiceItem.quantity)) \
                    .join(Invoice) \
                    .filter(
                        InvoiceItem.product_id == target_pid_str,
                        InvoiceItem.income_category == 'product',
                        Invoice.company_id == target_company_id,
                        Invoice.status != "canceled"
                    ).scalar() or 0

                stock_in_json = item_file.get("stock_in")
                stock_in = int(stock_in_json) if stock_in_json is not None else (
                    int(getattr(p, 'quantity', 0) or 0) + int(total_sold)
                )

                products_json.append({
                    "id": p.id,
                    "local_id": p.local_id,
                    "sku": p.sku if p.sku else item_file.get("sku", str(_prod_key(p))),
                    "name": p_name,
                    "price": float(p.price or 0),
                    "cost_price": float(p.cost_price or 0),
                    "quantity": stock_in - int(total_sold),
                    "income_category": i_cat
                })

        # ----------------- PAYMENTS JSON -----------------
        payments_json = []
        if invoice:
            for p in Payment.query.filter_by(invoice_id=invoice.id).all():
                payments_json.append({
                    "payment_date": p.payment_date.strftime('%Y-%m-%d') if p.payment_date else "",
                    "payment_method": p.payment_method,
                    "payment_amount": float(p.payment_amount or 0),
                    "bank": p.bank or "",
                    "branch": p.branch or "",
                    "account_number": p.account_number or ""
                })

        # ----------------- TOTALS -----------------
        sub_total = float(invoice.sub_total or 0) if invoice else 0.0
        vat_amount = float(invoice.vat_amount or 0) if invoice else 0.0
        grand_total = float(invoice.grand_total or 0) if invoice else 0.0
        discount_total = float(getattr(invoice, 'discount_total', 0) or 0) if invoice else 0.0
        vat_rate = float(invoice.vat_rate) if invoice and invoice.vat_rate is not None else 0.0

        # ----------------- CANCELLATION -----------------
        cancellation_reason_trans = ""
        if invoice and getattr(invoice, 'status', '') == 'canceled':
            cancel_file_data = load_cancellation_file(str(target_company_id), invoice.id) or {}
            reason_dict = cancel_file_data.get("cancellation_reason", {})
            cancellation_reason_trans = (
                reason_dict.get(language)
                or reason_dict.get("he")
                or getattr(invoice, 'cancellation_reason', '')
                or "ביטול כללי"
            )

        # ----------------- BASE CONTEXT -----------------
        visual_customer_id = None
        if invoice:
            if customer_json and str(customer_json.get("id")) == "0":
                visual_customer_id = "0"
            elif c_obj:
                visual_customer_id = str(_customer_key(c_obj))     # same key as the dropdown
            else:
                visual_customer_id = str(invoice.customer_id) if invoice.customer_id is not None else "0"

        base_ctx = base_invoice_context(customer_id=visual_customer_id)

        customer_final = customer_json or base_ctx.get("customer") or {}
        app_currency = "ILS" if language in ["he", "ar"] else "USD"

        transaction_obj = None
        if invoice and not customer_viewer:
            transaction_obj = Transaction.query.filter_by(
                invoice_id=invoice.id, company_id=target_company_id).first()

        base_ctx.update({
            "invoice": invoice,
            "invoice_id": invoice.id if invoice else None,
            "allocation_number": invoice.allocation_number if invoice else None,
            "invoice_number": invoice.invoice_number if invoice else get_next_invoice_number(company_id=target_company_id),

            "invoice_date": (
                invoice.invoice_date.strftime('%Y-%m-%d')
                if invoice and invoice.invoice_date else datetime.today().strftime('%Y-%m-%d')
            ),

            "invoice_original_currency": getattr(invoice, 'currency', 'ILS') if invoice else 'ILS',
            "current_app_currency": app_currency,

            "customer_json": customer_final,
            "customer": customer_final,
            "all_customers_json": all_customers_json,

            "items": items_json,
            "products": products_json,
            "loadedPayments": payments_json,

            "sub_total": sub_total,
            "vat_rate": vat_rate,
            "vat_amount": vat_amount,
            "grand_total": grand_total,
            "discount_total": discount_total,

            "invoice_status": invoice.status if invoice else "active",
            "translated_reason": cancellation_reason_trans,
            "company": company_translated,
            "company_db": company_obj,
            "transaction": transaction_obj
        })

        return base_ctx

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ CRITICAL ERROR in invoice_context: {e}")

        company_id = _active_company_id()
        customer_viewer = _is_customer_viewer()
        company_obj = db.session.get(Company, company_id) if company_id else None

        language = get_lang()
        company_translated = load_company_translated(company_obj, language) if company_obj else {}
        app_currency = "ILS" if language in ["he", "ar"] else "USD"

        products_backup = []
        if company_id and not customer_viewer:
            try:
                for p in Product.query.filter_by(company_id=company_id).all():
                    products_backup.append({
                        "id": p.id,
                        "local_id": p.local_id,
                        "sku": p.sku or str(p.local_id),
                        "name": p.name,
                        "price": float(p.price or 0),
                        "cost_price": float(p.cost_price or 0),
                        "quantity": int(getattr(p, 'quantity', 0) or 0),
                        "income_category": p.income_category or "service"
                    })
            except Exception:
                pass

        backup_reason = ""
        try:
            if invoice:
                backup_reason = load_cancellation_translated(invoice, language, company_id)
        except Exception:
            if invoice:
                backup_reason = getattr(invoice, 'cancellation_reason', '') or ""

        fallback_invoice_num = 1
        try:
            if company_id:
                fallback_invoice_num = get_next_invoice_number(company_id=company_id)
        except Exception:
            pass

        return {
            "error": "invoice_context failed",
            "invoice": None,
            "invoice_id": None,
            "company": company_translated,
            "company_db": company_obj,
            "customer": {},
            "customer_json": {},
            "all_customers_json": [],
            "products": products_backup,
            "items": [],
            "loadedPayments": [],
            "sub_total": 0.0,
            "vat_amount": 0.0,
            "grand_total": 0.0,
            "discount_total": 0.0,
            "vat_rate": 0.0,
            "vat_options": list(range(0, 21)),
            "next_invoice_num": fallback_invoice_num,
            "invoice_number": fallback_invoice_num,
            "invoice_date": datetime.today().strftime('%Y-%m-%d'),
            "invoice_original_currency": "ILS",
            "current_app_currency": app_currency,
            "current_currency": request.cookies.get('currency') or session.get('currency') or "ILS",
            "invoice_status": "active",
            "translated_reason": backup_reason
        }


# ---------------------------------------------------------------------
#  Save helpers
# ---------------------------------------------------------------------

def generate_allocation_number():
    timestamp = int(time.time())
    rand = random.randint(1000, 9999)
    return f"{timestamp}{rand}"


def _row_total(qty, unit_price, disc):
    """Discount: percent if < 100, otherwise a fixed amount (same as the original)."""
    base = qty * unit_price
    discount_value = base * (disc / 100) if disc < 100 else disc
    return base - discount_value


def _parse_payment_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _next_transaction_local_id(company_id):
    max_local = db.session.query(db.func.max(Transaction.local_id)) \
        .filter(Transaction.company_id == company_id).scalar()
    return (max_local or 0) + 1


def _write_item_file(company_id, prod, item_file, i_cat, new_stock_out, batches=None):
    save_item_file(
        company_id=company_id,
        local_id=_prod_key(prod),
        name_trans=item_file.get("name", {"he": prod.name}),
        desc_trans=item_file.get("description", {"he": prod.description}),
        price=prod.price,
        income_category=i_cat,
        cost_price=prod.cost_price,
        stock_out=new_stock_out,
        sku=prod.sku,
        batches=item_file.get("batches", []) if batches is None else batches,
        supplier_name_trans=item_file.get("supplier_name", {"he": "מלאי פתיחה / כללי"})
    )


class _ItemFileBuffer:
    """
    Collects the stock changes of the item JSON files in memory.
    The files are written ONLY after the database commit succeeded,
    so a failed save never leaves the files and the database out of sync.
    """
    def __init__(self, company_id, batches_loader=None):
        self.company_id = company_id
        self.batches_loader = batches_loader
        self._entries = {}

    def entry(self, prod):
        key = _prod_key(prod)
        e = self._entries.get(key)
        if e is None:
            item_file = load_item_file(self.company_id, key) or {}
            e = {
                "prod": prod,
                "item_file": item_file,
                "i_cat": item_file.get("income_category", getattr(prod, 'income_category', 'service')),
                "stock_out": int(item_file.get("stock_out", 0)),
                "touched": False
            }
            self._entries[key] = e
        return e

    def add_stock_out(self, prod, delta):
        e = self.entry(prod)
        e["stock_out"] = max(0, e["stock_out"] + int(delta))
        e["touched"] = True

    def flush(self):
        for key, e in self._entries.items():
            if not e["touched"]:
                continue
            try:
                batches = self.batches_loader(self.company_id, key) if self.batches_loader else None
                _write_item_file(self.company_id, e["prod"], e["item_file"],
                                 e["i_cat"], e["stock_out"], batches=batches)
            except Exception as ex:
                print(f"⚠ Item file write failed for product {key}: {ex}")


def _restore_old_stock(company_id, invoice, buf):
    """Edit / cancel: puts the quantities of the old items back into stock."""
    for old_item in InvoiceItem.query.filter_by(invoice_id=invoice.id).all():
        if old_item.product_id in NON_STOCK_PRODUCT_IDS:
            continue

        prod = _find_product(company_id, old_item.product_id, prefer_local=True)
        if not prod:
            continue

        if buf.entry(prod)["i_cat"] == 'product':
            prod.quantity += old_item.quantity
            buf.add_stock_out(prod, -int(old_item.quantity))


def _add_items(company_id, invoice_id, form, buf):
    """Adds the invoice items, lowers stock, returns the total cost of goods."""
    total_cost = 0.0

    for item_json in form.getlist('items[]'):
        try:
            item_data = json.loads(item_json)
        except Exception:
            continue

        p_id_val = item_data.get('product_id')
        qty = clean_float(item_data.get('quantity'))
        u_price = clean_float(item_data.get('price'))
        disc = clean_float(item_data.get('discount', 0))
        row_total = _row_total(qty, u_price, disc)
        item_description = (item_data.get('description', '') or '').strip()

        # special rows (rent, stocks...) have no stock
        if p_id_val in SPECIAL_PRODUCT_IDS:
            db.session.add(InvoiceItem(
                invoice_id=invoice_id,
                product_id=p_id_val,
                description=item_description or p_id_val,
                quantity=qty,
                unit_price=u_price,
                discount=disc,
                total_price=row_total,
                cost_price_at_time=0.0,
                income_category=p_id_val
            ))
            continue

        prod = _find_product(company_id, p_id_val, prefer_local=False)
        if not prod:
            continue

        entry = buf.entry(prod)
        item_file = entry["item_file"]
        i_cat = entry["i_cat"]
        c_price = float(item_file.get("cost_price", prod.cost_price or 0.0))

        if not item_description:
            item_description = item_file.get("name", {}).get("he", prod.name)

        if i_cat == 'product':
            prod.quantity -= qty
            buf.add_stock_out(prod, int(qty))

        total_cost += qty * c_price

        db.session.add(InvoiceItem(
            invoice_id=invoice_id,
            product_id=str(_prod_key(prod)),
            description=item_description,
            quantity=qty,
            unit_price=u_price,
            discount=disc,
            total_price=row_total,
            cost_price_at_time=c_price,
            income_category=i_cat
        ))

    return total_cost


def _add_payments(company_id, invoice_id, form):
    amounts = form.getlist('payment_amount[]')
    payment_dates = form.getlist('payment_date[]')
    methods = form.getlist('payment_method[]')
    banks = form.getlist('bank[]')
    branches = form.getlist('branch[]')
    accounts = form.getlist('account_number[]')

    for i in range(len(amounts)):
        amt = clean_float(amounts[i])
        if amt <= 0:
            continue

        p_date = _parse_payment_date(payment_dates[i] if i < len(payment_dates) else "")

        db.session.add(Payment(
            company_id=company_id,
            invoice_id=invoice_id,
            payment_date=p_date or datetime.today().date(),
            payment_method=methods[i] if i < len(methods) else "",
            payment_amount=amt,
            bank=banks[i] if i < len(banks) else "",
            branch=branches[i] if i < len(branches) else "",
            account_number=accounts[i] if i < len(accounts) else ""
        ))


# ---------------------------------------------------------------------
#  Save invoice (create / edit)
# ---------------------------------------------------------------------

@app.route('/invoice/save', methods=['POST'])
@login_required
def save_invoice():
    guard = _staff_guard()
    if guard:
        return guard

    invoice_id = (request.form.get("invoice_id") or "").strip()
    customer_id = (request.form.get("customer_id") or "").strip()

    company_id = _active_company_id()
    if not company_id:
        flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
        return redirect(url_for('invoice'))

    if not customer_id:
        flash("שגיאה: יש לבחור לקוח חוקי על מנת לשמור את המסמך", "error")
        return redirect(url_for('invoice'))

    sub_total = clean_float(request.form.get('sub_total'))
    vat_amount = clean_float(request.form.get('vat_amount'))
    grand_total = clean_float(request.form.get('grand_total'))

    vat_rate_raw = request.form.get('vat_rate_select')
    vat_rate = clean_float(vat_rate_raw) if vat_rate_raw not in [None, "", "null"] else 0.0

    buf = _ItemFileBuffer(company_id)

    try:
        # ----------------- CUSTOMER -----------------
        # "0" = self invoice = the technical customer row of the company (same on SQLite and Postgres)
        if customer_id == "0":
            customer = _get_or_create_self_customer(company_id)
        else:
            if _to_int(customer_id) is None:
                flash("שגיאה: מזהה לקוח אינו חוקי", "error")
                return redirect(url_for('invoice'))
            customer = _find_customer(company_id, customer_id)

        if not customer:
            flash("שגיאה: אין לך הרשאה לגשת לנתוני לקוח זה", "error")
            return redirect(url_for('invoice'))

        db_customer_id = customer.id      # always the real row id of the customer table

        # =============================================================
        # 1. EDIT AN EXISTING INVOICE
        # =============================================================
        if invoice_id:
            invoice_id_int = _to_int(invoice_id)
            if invoice_id_int is None:
                flash("שגיאה: מזהה חשבונית אינו חוקי", "error")
                return redirect(url_for('invoice'))

            invoice = db.session.get(Invoice, invoice_id_int)
            if not invoice or invoice.company_id != company_id:
                flash("החשבונית המבוקשת לעריכה אינה קיימת במערכת שלך", "error")
                return redirect(url_for('invoice'))

            if invoice.status in ["canceled", "מבוטלת"]:
                flash("שגיאה חמורה: לא ניתן לערוך או לשמור חשבונית מבוטלת.", "error")
                return redirect(url_for('invoice_view', invoice_id=invoice.id))

            _restore_old_stock(company_id, invoice, buf)

            invoice.customer_id = db_customer_id
            invoice.sub_total = sub_total
            invoice.vat_amount = vat_amount
            invoice.grand_total = grand_total
            invoice.vat_rate = vat_rate
            invoice.status = "active"

            if not invoice.allocation_number:
                invoice.allocation_number = generate_allocation_number()

            InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()
            Payment.query.filter_by(invoice_id=invoice.id).delete()
            db.session.flush()

            total_invoice_cost = _add_items(company_id, invoice.id, request.form, buf)
            _add_payments(company_id, invoice.id, request.form)

            # transactions.invoice_id is a FK to invoice.id (the primary key)
            existing_trans = Transaction.query.filter_by(
                invoice_id=invoice.id, company_id=company_id).first()

            if existing_trans:
                existing_trans.date = invoice.invoice_date
                existing_trans.description = f"חשבונית #{invoice.invoice_number}"
                existing_trans.amount = sub_total
                existing_trans.customer_id = db_customer_id
                existing_trans.cost_price_at_time = total_invoice_cost
            else:
                db.session.add(Transaction(
                    company_id=company_id,
                    local_id=_next_transaction_local_id(company_id),
                    date=invoice.invoice_date,
                    description=f"חשבונית #{invoice.invoice_number}",
                    amount=sub_total,
                    type='income',
                    category_id=None,
                    invoice_id=invoice.id,
                    customer_id=db_customer_id,
                    cost_price_at_time=total_invoice_cost,
                    quantity=1
                ))

            db.session.commit()
            buf.flush()      # item files are written only after a successful commit
            flash('החשבונית עודכנה בהצלחה והמלאי סונכרן', 'success')
            return redirect(url_for('invoice_view', invoice_id=invoice.id))

        # =============================================================
        # 2. CREATE A NEW INVOICE
        # =============================================================
        invoice_number = get_next_invoice_number(company_id=company_id)

        if Invoice.query.filter_by(company_id=company_id, invoice_number=invoice_number).first():
            flash(f"שגיאה: חשבונית מספר {invoice_number} כבר שמורה ומאובטחת במערכת.", "error")
            return redirect(url_for('invoice'))

        new_invoice = Invoice(
            company_id=company_id,
            invoice_number=invoice_number,
            invoice_date=datetime.today().date(),
            customer_id=db_customer_id,
            sub_total=sub_total,
            vat_amount=vat_amount,
            grand_total=grand_total,
            vat_rate=vat_rate,
            status="active",
            allocation_number=generate_allocation_number()
        )
        db.session.add(new_invoice)
        db.session.flush()

        total_invoice_cost = _add_items(company_id, new_invoice.id, request.form, buf)
        _add_payments(company_id, new_invoice.id, request.form)
        db.session.flush()

        db.session.add(Transaction(
            company_id=company_id,
            local_id=_next_transaction_local_id(company_id),
            date=new_invoice.invoice_date,
            description=f"חשבונית #{new_invoice.invoice_number}",
            amount=sub_total,
            type='income',
            category_id=None,
            invoice_id=new_invoice.id,
            customer_id=db_customer_id,
            cost_price_at_time=total_invoice_cost,
            quantity=1
        ))

        db.session.commit()
        buf.flush()
        flash('החשבונית הופקה בהצלחה והמלאי עודכן', 'success')
        return redirect(url_for('invoice_view', invoice_id=new_invoice.id))

    except Exception as e:
        db.session.rollback()          # the item files were never touched, so nothing else to undo
        app.logger.exception("save_invoice failed: %s", e)
        flash("שגיאה בשמירת החשבונית. הנתונים לא נשמרו. נסה שוב או פנה לתמיכה.", "error")
        return redirect(url_for('invoice'))


# ---------------------------------------------------------------------
#  Invoice form (create / edit)
# ---------------------------------------------------------------------

@app.route('/invoice/create', methods=['GET'])
@login_required
def invoice():
    guard = _staff_guard()
    if guard:
        return guard

    ctx = invoice_context(request.args.get('invoice_id'))
    return render_template('invoice.html', **ctx)


# ---------------------------------------------------------------------
#  View a signed / closed invoice (a customer sees only his own)
# ---------------------------------------------------------------------

@app.route('/invoice/<int:invoice_id>', methods=['GET'])
@login_required
def invoice_view(invoice_id):
    ctx = invoice_context(invoice_id)

    if not ctx.get("company"):
        cid = _active_company_id()
        company_obj = db.session.get(Company, cid) if cid else None
        ctx["company"] = load_company_translated(company_obj, get_lang())

    if not ctx.get("invoice"):
        flash("חשבונית לא נמצאה או שאין לך הרשאת גישה אליה", "danger")
        if _is_customer_viewer():
            return redirect(url_for('customer_dashboard_router'))
        return redirect(url_for('invoice_data'))

    if _is_customer_viewer():
        ctx["is_customer_view"] = True

    return render_template('invoice.html', **ctx)


# ---------------------------------------------------------------------
#  New clean document
# ---------------------------------------------------------------------

@app.route("/invoice/new", methods=["GET", "POST"])
@login_required
def new_invoice():
    return redirect(url_for('invoice'))


# ---------------------------------------------------------------------
#  Cancel an invoice (stock back, transaction zeroed)
# ---------------------------------------------------------------------

@app.route('/invoice/<int:invoice_id>/cancel', methods=['POST'])
@login_required
def cancel_invoice(invoice_id):
    guard = _staff_guard()
    if guard:
        return guard

    company_id = _active_company_id()
    invoice = db.session.get(Invoice, invoice_id)

    if not company_id or not invoice or invoice.company_id != company_id:
        flash("המסמך המבוקש אינו קיים במערכת שלך", "error")
        return redirect(url_for('invoice_data'))

    if invoice.status in ["canceled", "מבוטלת"]:
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    current_lang = get_lang()
    user_typed_reason = request.form.get("cancel_reason", "").strip()
    default_text = "General Cancellation" if current_lang != "he" else "ביטול כללי"
    invoice_reason = user_typed_reason or default_text

    try:
        # A) stock back (the item files are written after the commit)
        buf = _ItemFileBuffer(company_id, batches_loader=_load_inventory_batches)
        _restore_old_stock(company_id, invoice, buf)

        # B) the financial transaction is zeroed, not deleted.
        #    transactions.invoice_id = invoice.id (same as save_invoice)
        trans = Transaction.query.filter_by(invoice_id=invoice.id, company_id=company_id).first()
        if trans:
            trans.amount = 0.0
            trans.cost_price_at_time = 0.0
            if current_lang != "he":
                trans.description = f"Invoice #{invoice.invoice_number} (Canceled: {invoice_reason})"
            else:
                trans.description = f"חשבונית #{invoice.invoice_number} (מבוטלת: {invoice_reason})"

        # C) status
        invoice.status = "canceled"
        invoice.cancellation_reason = invoice_reason

        db.session.commit()
        buf.flush()

        # files and translation only after a successful commit
        initial_dict = {current_lang: invoice_reason}
        if current_lang == "he":
            initial_dict["en"] = "Cancellation pending translation..."
        else:
            initial_dict["he"] = "ביטול ממתין לתרגום..."
        save_cancellation_file(company_id, invoice.id, initial_dict)

        if user_typed_reason:
            try:
                translate_cancellation_in_background(
                    invoice_id=invoice.id,
                    company_id=company_id,
                    text_to_translate=user_typed_reason
                )
            except Exception as e:
                print(f"⚠ Threading failure: {e}")

        flash("החשבונית בוטלה בהצלחה ותנועות המס עודכנו", "success")
        return redirect(url_for('invoice_view', invoice_id=invoice.id))

    except Exception:
        db.session.rollback()
        app.logger.exception("cancel_invoice failed")
        flash("שגיאה בביטול החשבונית. לא בוצע שינוי.", "error")
        return redirect(url_for('invoice_view', invoice_id=invoice_id))


# ---------------------------------------------------------------------
#  All invoices page
# ---------------------------------------------------------------------

@app.route('/invoices')
@login_required
def invoice_data():
    guard = _staff_guard()
    if guard:
        return guard

    language = get_lang()
    search = request.args.get("q", "").strip().lower()
    selected_month = request.args.get("month", "")
    selected_year = request.args.get("year", "")
    selected_status = request.args.get("status", "all")

    if not selected_year:
        selected_year = str(datetime.today().year)
    if not selected_month:
        selected_month = datetime.today().strftime('%m')

    company_id = _active_company_id()
    if not company_id:
        flash("שגיאה: אין חברה פעילה", "danger")
        return redirect(url_for('invoice'))

    business_categories = {}
    try:
        company_cat_dir = os.path.join(app.config["UPLOAD_FOLDER"], "categories", f"company_{company_id}") if "UPLOAD_FOLDER" in app.config else ""
        if not os.path.exists(company_cat_dir):
            base_dir = app.config.get("CATEGORIES_DIR")
            company_cat_dir = os.path.join(base_dir, f"company_{company_id}") if base_dir else ""

        if company_cat_dir and os.path.exists(company_cat_dir):
            for cat_id in os.listdir(company_cat_dir):
                if cat_id.isdigit():
                    data = load_category_file(int(cat_id))
                    if data and "name" in data:
                        business_categories[str(cat_id)] = data["name"].get(language) or data["name"].get("he") or cat_id
    except Exception as e:
        print(f"⚠️ Warning: category loading failed: {e}")

    company_obj = db.session.get(Company, company_id)
    company_translated = load_company_translated(company_obj, language) if company_obj else {}

    invoices = Invoice.query.filter_by(company_id=company_id).options(
        db.joinedload(Invoice.customer),
        db.joinedload(Invoice.items)
    ).all()

    all_products_sku_map = {}
    try:
        for p in Product.query.filter_by(company_id=company_id).all():
            if p.sku:
                all_products_sku_map[str(p.local_id)] = p.sku.lower()
                all_products_sku_map[str(p.id)] = p.sku.lower()
    except Exception as e:
        print(f"⚠️ Warning: product sku preload failed: {e}")

    def safe_int_invoice(inv_obj):
        num_str = str(inv_obj.invoice_number or "0").strip()
        return int(num_str) if num_str.isdigit() else 0

    invoices.sort(key=safe_int_invoice, reverse=True)

    filtered_invoices = []
    customer_i18n_list = {}
    total_profit = 0.0
    is_numeric_search = search.isdigit()

    self_name = f"★ {company_translated.get('name') or (company_obj.name if company_obj else '')} "

    for inv in invoices:
        if not inv.invoice_date:
            continue

        trans_name = ""
        db_name = ""

        if _is_self_customer(inv.customer_id, inv.customer):
            # self invoice: NULL / 0 (old rows) or the technical customer row
            trans_name = db_name = self_name.lower()
            customer_i18n_list["0"] = {"name": self_name}
            if inv.customer:
                customer_i18n_list[inv.customer.id] = {"name": self_name}
        elif inv.customer:
            cid = inv.customer.id
            if cid not in customer_i18n_list:
                customer_i18n_list[cid] = load_customer_translated(inv.customer, language, company_id=company_id)

            trans_name = (customer_i18n_list[cid].get('name', '') or "").lower()
            db_name = (inv.customer.customer_name or "").lower()

        match_status = (selected_status == "all" or inv.status == selected_status)
        if not search:
            match_search = True
        else:
            if is_numeric_search:
                match_search = (str(inv.invoice_number) == search)
            else:
                match_search = (search in db_name or search in trans_name or search in str(inv.invoice_date))

            if not match_search:
                for item in inv.items:
                    item_sku = all_products_sku_map.get(str(item.product_id), "")
                    item_desc = str(item.description or "").lower()
                    if search in item_sku or search in item_desc:
                        match_search = True
                        break

        inv_year = str(inv.invoice_date.year)
        inv_month = inv.invoice_date.strftime('%m')

        if match_status and match_search and (not selected_month or inv_month == selected_month) and (not selected_year or inv_year == selected_year):
            if inv.status == "canceled":
                row_profit = 0.0
            else:
                total_item_cost = 0.0
                for item in inv.items:
                    total_item_cost += float(item.quantity or 0.0) * float(item.cost_price_at_time or 0.0)

                row_profit = float(inv.sub_total or 0.0) - total_item_cost
                total_profit += row_profit

            inv.profit = row_profit
            filtered_invoices.append(inv)

    active_invoices = [inv for inv in filtered_invoices if inv.status != "canceled"]

    invoice_original_currency = getattr(company_obj, 'currency', None) or "ILS"
    current_currency = request.cookies.get('currency') or session.get('currency') or invoice_original_currency

    from flask import make_response

    response = make_response(render_template(
        'invoice_data.html',
        invoices=filtered_invoices,
        total_amount=sum(float(inv.grand_total or 0.0) for inv in active_invoices),
        total_profit=total_profit,
        total_count=len(active_invoices),
        search=search,
        selected_month=selected_month,
        selected_year=selected_year,
        selected_status=selected_status,
        months=["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"],
        years=[str(y) for y in range(2024, datetime.today().year + 3)],
        customer_i18n_list=customer_i18n_list,
        business_categories=business_categories,
        language=language,
        company=company_translated,
        company_db=company_obj,
        invoice_original_currency=invoice_original_currency,
        current_currency=current_currency
    ))

    response.set_cookie('currency', current_currency)
    return response


# ---------------------------------------------------------------------
#  E-mail an invoice (PDF) to the customer
#  The page is rendered HERE (inside the request, where session and login exist).
#  The background thread only builds the PDF and sends the mail.
# ---------------------------------------------------------------------

@app.route('/send_invoice_email/<int:invoice_id>')
@login_required
def send_invoice_email(invoice_id):
    guard = _staff_guard()
    if guard:
        return guard

    company_id = _active_company_id()
    invoice = db.session.get(Invoice, invoice_id)
    if not company_id or not invoice or invoice.company_id != company_id:
        flash("המסמך המבוקש אינו קים במערכת שלך", "danger")
        return redirect(url_for('invoice_data'))

    customer = invoice.customer
    if not customer or not customer.email:
        flash("ללקוח לא מוגדר אימייל! אנא עדכן את כרטיס הלקוח תחילה.", "danger")
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    try:
        language = getattr(customer, 'language', None) or 'he'
        company_obj = db.session.get(Company, company_id)

        ctx = invoice_context(invoice_id)
        ctx["company"] = load_company_translated(company_obj, language)
        ctx["language"] = language
        html_content = render_template("invoice.html", **ctx, is_pdf=True)
    except Exception:
        app.logger.exception("send_invoice_email: render failed")
        flash("שגיאה בהפקת החשבונית לשליחה", "danger")
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    send_invoice_email_in_background(invoice_id, company_id, html_content)

    flash("בקשת השליחה התקבלה! החשבונית מופקת ונשלחת ללקוח ברקע.", "success")
    return redirect(url_for('invoice_view', invoice_id=invoice_id))


def send_invoice_email_in_background(invoice_id, company_id, html_content):
    thread = threading.Thread(
        target=run_invoice_email_task,
        args=(invoice_id, company_id, html_content),
        daemon=True
    )
    thread.start()


def run_invoice_email_task(invoice_id, company_id, html_content):
    try:
        with app.app_context():
            invoice = db.session.get(Invoice, invoice_id)
            if not invoice or invoice.company_id != company_id or not invoice.customer or not invoice.customer.email:
                print(f"❌ Security Block: Mail task aborted for Invoice ID {invoice_id}")
                return

            customer = invoice.customer

            with sync_playwright() as p:
                browser = p.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])
                page = browser.new_page()
                page.set_content(html_content, wait_until="networkidle")

                pdf_data = page.pdf(
                    format="A4", print_background=True, scale=1.0,
                    margin={"top": "20px", "right": "20px", "bottom": "20px", "left": "20px"}
                )
                browser.close()

            email_body = f"""
שלום {customer.customer_name}

להלן מצורפת חשבונית מספר {invoice.invoice_number}
לתאריך {invoice.invoice_date.strftime('%d-%m-%Y')}

תודה על שירותך
"""
            msg = Message(
                subject=f"חשבונית מס {invoice.invoice_number} - {customer.customer_name}",
                recipients=[customer.email, "edahan887@gmail.com"],
                body=email_body
            )
            msg.attach(
                filename=f"invoice_{invoice.invoice_number}.pdf",
                content_type="application/pdf",
                data=pdf_data
            )

            mail.send(msg)
            print(f"✔ Email sent for Invoice #{invoice.invoice_number} under company {company_id}")

    except Exception as e:
        print(f"❌ Email sending failed for invoice {invoice_id}: {e}")


# --------------------
#  Payment Callback Invoice Form Data (Multi-Tenant Secure Webhook Engine)
# ----------------------

@app.route('/api/payments/callback', methods=['POST'])
@login_required
def payment_callback():
    data = request.get_json() or {}
    status = data.get("status")
    invoice_id = data.get("internal_invoice_id")
    customer_id = data.get("internal_customer_id")
    transaction_id = data.get("transaction_id")

    if not invoice_id:
        return "Missing invoice ID", 400

    invoice = db.session.get(Invoice, int(invoice_id))
    if not invoice:
        return "Invoice not found", 404

    if status == "success":
        if invoice.is_paid:
            return "Already processed", 200

        invoice.is_paid = True
        invoice.payment_transaction_id = transaction_id
        
        # תיקון קריטי: שמירת תאריך בלבד (date) ללא שעות כדי למנוע קריסת PostgreSQL
        invoice.payment_date = datetime.utcnow().date()

        # תיקון קריטי: המרה מפורשת של customer_id ל-int למניעת קריסת הדרייבר
        safe_customer_id = int(customer_id) if str(customer_id).isdigit() else customer_id

        customer = Customer.query.filter_by(
            id=safe_customer_id, 
            company_id=int(invoice.company_id) 
        ).first()

        if customer:
            customer.is_active = True

        db.session.commit()
        print(f"✔ Payment confirmed for Invoice #{invoice.invoice_number} (Co: {invoice.company_id})")

    return "OK", 200


# ------------------------------------------------------------------
#  ייצר קישור תשלום זמני ישירות מהמסך Create Payment Link
# ------------------------------------------------------------------

@app.route('/payment/create_link', methods=['POST'])
@login_required
def create_payment_link():
    try:
        data = request.get_json() or {}
        amount_raw = data.get('amount')
        amount_val = float(amount_raw) if amount_raw else 0.0

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None)

        if not active_company_id:
            active_company_id = 1

        # המרה כפויה ל-int בשביל אבטחת השמירה ב-Render PostgreSQL
        active_company_id = int(active_company_id)

        # הפיקס הנכון: מייצרים את השורה הזמנית בטבלת Payment ולא ב-Transaction!
        # בזכות זה היא לא תופיע בחיים ביומן הראשי שלך ולא תייצר שורות אדומות כפולות!
        new_payment = Payment()
        new_payment.company_id = active_company_id
        new_payment.invoice_id = None  # זמני, מונע קריסות ForeignKey ברנדר
        new_payment.payment_date = datetime.today().date()
        new_payment.payment_method = 'credit'
        new_payment.payment_amount = amount_val

        # שומרים את סימון ההמתנה בשדה ה-bank בשביל הטיימר (Long Polling)
        new_payment.bank = "pending_credit_payment"
        new_payment.branch = ""
        new_payment.account_number = ""

        db.session.add(new_payment)
        db.session.commit()
        
        secure_url = url_for('public_payment_gateway', token=str(new_payment.id), _external=True)
        
        return jsonify({
            "status": "success",
            "url": secure_url,
            "payment_id": new_payment.id
        }), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


# ------------------------------------------------------------------
#  הצגת הנתונים (GET) וחיוב קארדקום  (POST) Create Payment 
# ------------------------------------------------------------------

@app.route('/pay/<string:token>', methods=['GET', 'POST'], strict_slashes=False)
def public_payment_gateway(token=None):
    try:
        if not token:
            token = request.args.get('token')
        if not token:
            return "<h1>Missing payment token parameter</h1>", 400

        language = get_lang()

        # הפיקס המושלם: מושכים את הרשומה מטבלת Payment במקום Transaction כדי למנוע כפילויות ביומן הראשי!
        link_obj = None
        if token.isdigit():
            link_obj = db.session.get(Payment, int(token))

        if not link_obj:
            return "<h1>Link not found or has been expired</h1>", 404

        comp_id = getattr(link_obj, 'company_id', 1)
        
        # אבטחת מזהה חברה כמספר שלם קשיח עבור שאילתות ב-Render PostgreSQL
        if comp_id:
            comp_id = int(comp_id)

        display_amount = float(getattr(link_obj, 'payment_amount', 0.0))
        inv_num = getattr(link_obj, 'invoice_id', None)

        if request.method == 'GET':
            customer_name = ""
            customer_address = ""
            customer_city = ""
            
            if inv_num:
                invoice_record = db.session.get(Invoice, int(inv_num))
                if invoice_record and invoice_record.customer_id:
                    # המרה מפורשת ל-int בשביל אבטחת השאילתה בשרת
                    safe_customer_id = int(invoice_record.customer_id)
                    
                    customer_obj = Customer.query.filter_by(id=safe_customer_id, company_id=comp_id).first()
                    if customer_obj:
                        translated_customer = load_customer_translated(customer_obj, language, company_id=comp_id)
                        customer_name = translated_customer.get("name", customer_obj.customer_name)
                        customer_address = translated_customer.get("address", customer_obj.address)
                        customer_city = translated_customer.get("city", customer_obj.city)

            company_obj = db.session.get(Company, comp_id) if 'Company' in globals() else None
            translated_company = load_company_translated(company_obj, language) if company_obj else {}

            return render_template(
                'public_payment_gateway.html',
                link=link_obj,
                amount=display_amount,
                description_key="payment.invoice_desc", 
                invoice_id=inv_num,
                customer_name=customer_name,         
                customer_address=customer_address,   
                customer_city=customer_city,         
                company=translated_company,          
                company_db=company_obj,
                language=language,
                token=token
            )

        # בקשת POST: הלקוח לחץ על תשלום - שולחים חיוב אמיתי ומוצפן לחברת הסליקה
        elif request.method == 'POST':
            post_data = request.get_json() or {}
            
            card_number = post_data.get('card_number')
            card_expiry = post_data.get('card_expiry')  # פורמט MMYY
            card_cvv = post_data.get('card_cvv')
            holder_id = post_data.get('holder_id')
            card_holder_name = post_data.get('card_holder_name', 'External Client')

            company_obj = db.session.get(Company, comp_id)
            terminal_number = getattr(company_obj, 'cardcom_terminal', 'TEST_TERMINAL_12345') 
            api_name = getattr(company_obj, 'cardcom_api_name', 'TEST_USER')

            cardcom_payload = {
                "TerminalNumber": terminal_number,
                "ApiName": api_name,
                "ReturnValue": str(link_obj.id),
                "Operation": "1", 
                "SumToCharge": f"{display_amount:.2f}",
                "CardNumber": card_number,
                "CardValidity": card_expiry,
                "CVV": card_cvv,
                "IdNum": holder_id,
                "CardOwnerName": card_holder_name,
                "InvoiceNo": str(inv_num) if inv_num else "0"
            }

            try:
                gateway_url = "https://cardcom.co.il"
                
                merchant_gateway_provider = getattr(company_obj, 'gateway_provider', 'cardcom').lower()
                if merchant_gateway_provider == 'meshulam':
                    gateway_url = "https://meshulam.co.il"
                elif merchant_gateway_provider == 'yaad':
                    gateway_url = "https://yaad.net"
                elif merchant_gateway_provider == 'stripe':
                    gateway_url = "https://stripe.com"

                if terminal_number == 'TEST_TERMINAL_12345':
                    cardcom_response_data = {"ResponseCode": "0", "Description": "Success", "TicketNumber": "CC178263"}
                else:
                    # הזרקה מקומית מאובטחת של ספריות הרשת למניעת קריסות NameError
                    import requests
                    from urllib.parse import parse_qs

                    cardcom_net_call = requests.post(gateway_url, data=cardcom_payload, timeout=15)
                    cardcom_response_data = {k: v[0] for k, v in parse_qs(cardcom_net_call.text).items()}

                if cardcom_response_data.get("ResponseCode") == "0":
                    ticket_number = cardcom_response_data.get("TicketNumber", "APPROVED")
                    
                    # עדכון שדות האישור בטבלת Payment בלבד 
                    link_obj.bank = ticket_number
                    link_obj.branch = ticket_number
                    link_obj.account_number = "APPROVED"
                        
                    db.session.commit()

                    return jsonify({
                        "status": "success",
                        "message": "Payment cleared successfully",
                        "reference_number": ticket_number
                    }), 200
                else:
                    err_desc = cardcom_response_data.get("Description", "Transaction declined by credit card issuer.")
                    return jsonify({"status": "error", "message": err_desc}), 400

            except Exception as net_err:
                print(f"❌ Payment API Connection Timeout/Error: {net_err}")
                return jsonify({"status": "error", "message": "Failed to communicate with credit card clearing gateway."}), 502

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return "An internal server error occurred", 500


# ------------------------------------------------------------------
# ג': הראוט השקט (Long Polling API) - בודק את סטטוס העסקה ב-DB כל 3 שניות
# ------------------------------------------------------------------

@app.route('/api/payment/check_status/<int:payment_id>', methods=['GET'])
def check_payment_status(payment_id):
    try:
        payment = db.session.get(Payment, payment_id)
        if not payment:
            return jsonify({"status": "not_found"}), 404

        # בודקים את שדה ה-bank שבו שמרנו את מספר האישור (ticket_number)
        is_paid = payment.bank and payment.bank != "pending_credit_payment"
        
        if is_paid:
            return jsonify({
                "status": "paid",
                "reference": payment.bank, # מספר האישור של קארדקום או פייפאל שיושב ב-bank
                "date": payment.payment_date.strftime('%Y-%m-%d') if hasattr(payment.payment_date, 'strftime') else datetime.utcnow().strftime('%Y-%m-%d')
            }), 200
        else:
            return jsonify({"status": "pending"}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500



# --------------------
#  Manage Products Invoice Form Data
# ----------------------

@app.route('/products_manage', methods=['GET', 'POST'])
@login_required
def manage_products():
    language = get_lang()
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    # המרה כפויה ל-int בשביל אבטחת השאילתות והפרדת Multi-Company ב-Render
    if company_id:
        company_id = int(company_id)

    if request.method == 'POST':
        db.session.expire_on_commit = False
        product_id_raw = (request.form.get("id") or "").strip()
        product_id = int(product_id_raw) if product_id_raw.isdigit() else None
        name = (request.form.get('name') or '').strip()
        description = (request.form.get('description') or '').strip()
        user_sku = (request.form.get('sku') or '').strip()
        income_category = request.form.get("income_category", "product")

        received_date_raw = (request.form.get('received_date') or '').strip()
        if not received_date_raw:
            db.session.expire_on_commit = True
            flash('תאריך הוא שדה חובה', 'error')
            return redirect(url_for('manage_products'))

        formatted_date = None
        possible_formats = ['%d-%m-%Y', '%m-%d-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d']
        for fmt in possible_formats:
            try:
                date_obj = datetime.strptime(received_date_raw, fmt)
                formatted_date = date_obj.strftime('%Y-%m-%d')
                break
            except ValueError:
                continue

        if not formatted_date:
            formatted_date = received_date_raw.replace("-", "/")

        def to_float(v, d=0.0):
            try: return float(v)
            except: return d

        def to_int(v, d=0):
            try: return int(v)
            except: return d

        price = to_float(request.form.get('price', 0))
        cost_price = to_float(request.form.get('cost_price', 0))
        stock_in = to_int(request.form.get('stock_in', 0))
        supplier_id_raw = (request.form.get('supplier_id') or '').strip()
        supplier_id = int(supplier_id_raw) if supplier_id_raw.isdigit() else None

        product = None
        if product_id:
            product = Product.query.filter_by(id=product_id, company_id=company_id).first()
        if not product and user_sku:
            product = Product.query.filter_by(sku=user_sku, company_id=company_id).first()
        if not product and name:
            product = Product.query.filter_by(name=name, company_id=company_id).first()

        sp_supplier = None
        if supplier_id:
            sp_supplier = Supplier.query.filter_by(id=supplier_id, company_id=company_id).first()

        batches = []
        old_file = {}
        total_sold = 0
        if product:
            product_id = product.id
            target_local_id = product.local_id if product.local_id is not None else product.id
            old_file = load_item_file(company_id, target_local_id) or {}
            batches = _load_inventory_batches(company_id, target_local_id)

            if not batches and old_file.get("stock_in"):
                old_stock_in = int(old_file.get("stock_in", 0))
                fallback_supplier_name = None
                old_supplier_id = old_file.get("supplier_id")
                if old_supplier_id:
                    old_sup_obj = Supplier.query.filter_by(id=int(old_supplier_id), company_id=company_id).first()
                    if old_sup_obj:
                        fallback_supplier_name = old_sup_obj.supplier_name

                batches.append({
                    "product_local_id": target_local_id,
                    "received_date": old_file.get("received_date") or product.received_date or formatted_date,
                    "stock_in": old_stock_in,
                    "cost_price": float(old_file.get("cost_price", product.cost_price or 0.0)),
                    "supplier_id": int(old_supplier_id) if old_supplier_id else None,
                    "supplier_name": fallback_supplier_name
                })

            total_sold = db.session.query(db.func.sum(InvoiceItem.quantity)) \
                .join(Invoice) \
                .filter(
                    cast(InvoiceItem.product_id, Integer) == int(target_local_id),
                    InvoiceItem.income_category == 'product',
                    Invoice.company_id == company_id,
                    Invoice.status != "canceled"
                ).scalar() or 0

        stock_out = int(total_sold)
        if product and stock_in > 0:
            save_inventory_transaction(
                company_id=company_id,
                local_id=product.local_id if product.local_id is not None else product.id,
                received_date=formatted_date,
                stock_in=int(stock_in),
                cost_price=float(cost_price),
                supplier_id=sp_supplier.id if sp_supplier else None,
                supplier_name=sp_supplier.supplier_name if sp_supplier else ""
            )
            batches = _load_inventory_batches(company_id, product.local_id if product.local_id is not None else product.id)
            real_stock_in = sum(int(b.get("stock_in", 0)) for b in batches)
        else:
            if product:
                real_stock_in = sum(int(b.get("stock_in", 0)) for b in batches)
            else:
                real_stock_in = int(stock_in)

        current_stock = real_stock_in - stock_out

        if product:
            product.name = name
            product.price = price
            product.description = description
            product.sku = user_sku if user_sku else product.sku
            product.cost_price = cost_price
            product.quantity = current_stock
            product.income_category = income_category
            product.received_date = formatted_date
            db.session.commit()
            product_id = product.id
            resolved_sku = product.sku
            next_local_id = product.local_id if product.local_id is not None else product.id
        else:
            max_local = db.session.query(db.func.max(db.cast(Product.local_id, db.Integer))) \
                .filter(Product.company_id == company_id).scalar()
            next_local_id = (max_local or 0) + 1

            product = Product(
                company_id=company_id,
                local_id=next_local_id,
                sku=user_sku if user_sku else None,
                name=name,
                price=price,
                description=description,
                cost_price=cost_price,
                quantity=current_stock,
                income_category=income_category,
                received_date=formatted_date
            )
            db.session.add(product)
            db.session.commit()
            resolved_sku = user_sku if user_sku else product.sku
            product_id = product.id

            save_inventory_transaction(
                company_id=company_id,
                local_id=next_local_id,
                received_date=formatted_date,
                stock_in=int(stock_in),
                cost_price=float(cost_price),
                supplier_id=sp_supplier.id if sp_supplier else None,
                supplier_name=sp_supplier.supplier_name if sp_supplier else ""
            )

        translate_product_in_background(
            product_id=product_id,
            company_id=company_id,
            name=name,
            description=description,
            price=price,
            income_category=income_category,
            sku=resolved_sku, 
            supplier_name=sp_supplier.supplier_name if sp_supplier else ""
        )

        if sp_supplier and stock_in > 0:
            try:
                sp = SupplierPurchase(
                    supplier_id=sp_supplier.id,
                    product_id=product_id,
                    quantity=stock_in,
                    cost_price=cost_price,
                    total=stock_in * cost_price,
                    date=formatted_date,
                    reference="רכישת מלאי",
                    notes="נוסף דרך דף מוצרים"
                )
                db.session.add(sp)
                db.session.commit()
            except:
                db.session.rollback()

        db.session.expire_on_commit = True
        flash('הנתונים עודכנו בהצלחה, המלאי נשמר ותהליך התרגום רץ ברקע!', 'success')
        return redirect(url_for('manage_products'))

    # ========================   GET   ============================

    search = (request.args.get("q") or "").strip().lower()
    today_str = datetime.today().strftime('%Y-%m-%d')
    all_products = Product.query.filter_by(company_id=company_id).order_by(Product.id.desc()).all()

    filtered_objects = []
    if search:
        is_numeric = search.isdigit()
        for p in all_products:
            item_file = load_item_file(company_id, p.local_id if p.local_id is not None else p.id) or {}
            names_dict = item_file.get("name", {"he": p.name or ""})
            descs_dict = item_file.get("description", {"he": p.description or ""})
            
            raw_sup_name = item_file.get("supplier_name", {}).get("he") or ""
            if raw_sup_name == "מלאי פתיחה / כללי":
                raw_sup_name = ""
            sups_dict = item_file.get("supplier_name", {"he": raw_sup_name})

            all_names_text = " ".join([str(v).lower() for v in names_dict.values()])
            all_descs_text = " ".join([str(v).lower() for v in descs_dict.values()])
            all_sups_text = " ".join([str(v).lower() for v in sups_dict.values()])

            match = False
            if is_numeric and str(p.local_id) == search:
                match = True
            elif p.sku and search in str(p.sku).lower():
                match = True
            elif search in all_names_text or search in all_descs_text or search in all_sups_text:
                match = True

            if match:
                filtered_objects.append(p)
    else:
        filtered_objects = all_products

    suppliers_db_map = {s.id: s.supplier_name for s in Supplier.query.filter_by(company_id=company_id).all()}

    item_i18n_list = {}
    for p in filtered_objects:
        p_local_id = p.local_id if p.local_id is not None else p.id
        
        # אבטחת קריאה בטוחה לפונקציית התרגום שמבצעת שאילתות פנימיות
        translated_data = load_item_translated(p, language, company_id)
        batches_list_val = _load_inventory_batches(company_id, p_local_id)
        
        supplier_id_val = None
        if batches_list_val and isinstance(batches_list_val, list) and len(batches_list_val) > 0:
            supplier_id_val = batches_list_val[-1].get("supplier_id")

        current_date_val = p.received_date
        if current_date_val and "/" in current_date_val:
            try:
                current_date_val = datetime.strptime(current_date_val, '%d/%m/%Y').strftime('%Y-%m-%d')
            except:
                pass

        resolved_supplier_name = translated_data.get("supplier_name") or ""
        if not resolved_supplier_name or resolved_supplier_name == "מלאי פתיחה / כללי":
            resolved_supplier_name = suppliers_db_map.get(supplier_id_val, "")

        item_i18n_list[p.id] = {
            "id": p.id,
            "local_id": p.local_id,
            "sku": translated_data.get("sku"),
            "name": translated_data.get("name"),                  
            "description": translated_data.get("description"),    
            "supplier_name": resolved_supplier_name, 
            "income_category": translated_data.get("income_category"),
            "price": translated_data.get("price"),
            "cost_price": float(p.cost_price or 0.0),
            "quantity": int(p.quantity or 0),
            "supplier_id": supplier_id_val,
            "received_date": current_date_val,
            "batches": batches_list_val
        }

    item_i18n = item_i18n_list[filtered_objects[0].id] if filtered_objects and len(filtered_objects) > 0 else None

    products_json = []
    for p in filtered_objects:
        p_info = item_i18n_list.get(p.id, {})
        item_file = load_item_file(company_id, p.local_id if p.local_id is not None else p.id) or {}
        raw_batches = p_info.get("batches") or []
        clean_batches = []
        total_stock_in_calc = 0
        
        for b in raw_batches:
            b_copy = b.copy()
            b_date_raw = b_copy.get("received_date", "")
            if b_date_raw and "/" in b_date_raw:
                try:
                    b_copy["received_date"] = datetime.strptime(b_date_raw, '%d/%m/%Y').strftime('%Y-%m-%d')
                except:
                    pass
            total_stock_in_calc += int(b_copy.get("stock_in", 0))
            b_copy["supplier_name"] = p_info.get("supplier_name")
            clean_batches.append(b_copy)

        calculated_stock_out = total_stock_in_calc - int(p.quantity or 0)
        final_sku_code = p.sku if p.sku else item_file.get("sku", str(p.local_id if p.local_id is not None else p.id))

        products_json.append({
            "id": p.id,
            "local_id": p.local_id,
            "sku": str(final_sku_code).strip(),
            "name": p_info.get("name"),
            "description": p_info.get("description"),
            "price": float(p.price or 0.0),
            "cost_price": float(p.cost_price or 0.0),
            "income_category": item_file.get("income_category", p.income_category),
            "received_date": p_info.get("received_date"),
            "stock_in": int(total_stock_in_calc),
            "stock_out": int(calculated_stock_out),
            "supplier_id": p_info.get("supplier_id"),
            "supplier_name": p_info.get("supplier_name"), 
            "batches": clean_batches
        })

    all_suppliers = Supplier.query.filter_by(company_id=company_id).order_by(Supplier.supplier_name).all()
    suppliers_translated = []
    for s in all_suppliers:
        s_name_flat = s.supplier_name 
        found_translated_name = None
        for p_id, p_info in item_i18n_list.items():
            if p_info.get("supplier_id") == s.id and p_info.get("supplier_name"):
                found_translated_name = p_info.get("supplier_name")
                break

        if found_translated_name:
            s_name_flat = found_translated_name
        else:
            try:
                base_dir = app.config.get("SUPPLIERS_DIR")
                if base_dir:
                    s_file = os.path.join(base_dir, f"{company_id}_{s.id}", "supplier.json")
                    if os.path.isfile(s_file):
                        with open(s_file, "r", encoding="utf-8") as sf:
                            s_data = json.load(sf)
                            s_name_flat = s_data.get("name", {}).get(language) or s_data.get("name", {}).get("he") or s.supplier_name
            except:
                pass
        suppliers_translated.append({"id": s.id, "supplier_name": s_name_flat})

    company_obj = db.session.get(Company, company_id)

    with db.session.no_autoflush:
        return render_template(
            'products_manage.html',
            products=filtered_objects,
            products_json=products_json,
            search=search,
            item_i18n=item_i18n,
            item_i18n_list=item_i18n_list,
            current_lang=language,
            suppliers=suppliers_translated, 
            today=today_str,
            company=load_company_translated(company_obj, language),
            company_db=company_obj
        )


# --------------------
#  Products List Selected Combobox Data
# ----------------------

@app.route("/api/products_list", methods=['GET'])
@login_required
def products_list():
    language = get_lang()
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    # המרה כפויה ל-int בשביל אבטחת השאילתה והפרדת Multi-Company ב-Render
    if company_id:
        company_id = int(company_id)

    products = Product.query.filter_by(company_id=company_id).order_by(Product.id.asc()).all()
    result = []

    for p in products:
        p_local_lookup = p.local_id if p.local_id is not None else p.id
        
        translated = load_item_translated(p, language, company_id)

        raw_names = translated.get("name")
        raw_descs = translated.get("description")

        if isinstance(raw_names, dict):
            p_name = raw_names.get(language) or raw_names.get("he") or p.name or ""
        else:
            p_name = str(raw_names) if raw_names else p.name or ""

        if isinstance(raw_descs, dict):
            p_desc = raw_descs.get(language) or raw_descs.get("he") or p.description or ""
        else:
            p_desc = str(raw_descs) if raw_descs else p.description or ""

        prod_data = load_item_file(company_id, p_local_lookup)
        if prod_data and isinstance(prod_data.get("supplier_name"), dict):
            supplier_name = prod_data["supplier_name"].get(lookup_lang) or prod_data["supplier_name"].get("he") or "מלאי פתיחה / כללי"
        else:
            supplier_name = translated.get("supplier_name") or "מלאי פתיחה / כללי"

        stock_in = translated.get("stock_in", 0)
        total_sold = translated.get("stock_out", 0)
        actual_quantity = int(stock_in) - int(total_sold)

        income_category = translated.get("income_category", "product")
        received_date = translated.get("received_date")

        raw_batches = translated.get("batches") or []
        clean_batches = []
        supplier_id = None
        
        for b in raw_batches:
            b_copy = b.copy()
            if b_copy.get("supplier_id"):
                supplier_id = int(b_copy["supplier_id"])
            
            b_date_raw = b_copy.get("received_date", "")
            if b_date_raw:
                b_copy["received_date"] = format_lang_date(b_date_raw)
            
            b_copy["supplier_name"] = supplier_name
            clean_batches.append(b_copy)

        api_date = ""
        if received_date:
            api_date = format_lang_date(received_date)

        result.append({
            "id": p.id,
            "local_id": p.local_id,  
            "sku": str(translated.get("sku", p_local_lookup)),  
            "name": p_name,               
            "description": p_desc,        
            "price": float(translated.get("price") or p.price or 0.0),
            "cost_price": float(translated.get("cost_price") or p.cost_price or 0.0),
            "quantity": int(actual_quantity),
            "stock_in": int(stock_in),
            "stock_out": int(total_sold),
            "supplier_id": supplier_id,
            "supplier_name": supplier_name, 
            "income_category": income_category,
            "received_date": api_date,
            "batches": clean_batches
        })

    return jsonify(result)


# ----------------------
#  ADD Product  (from_n8n)
# ----------------------

@app.route('/api/product/add_from_n8n', methods=['POST'])
def api_add_product_from_n8n():
    try:
        # 1. אימות מול n8n
        auth_header = request.headers.get("X-API-KEY")
        if auth_header != N8N_API_KEY:
            return jsonify({"error": "Unauthorized API Access"}), 401

        data = request.get_json() or {}

        # 2. שדות חובה
        company_id_raw = data.get("company_id")
        name = (data.get("name") or "").strip()
        sku = (data.get("sku") or "").strip()
        received_date_raw = (data.get("received_date") or "").strip()

        if not company_id_raw or not name or not received_date_raw:
            return jsonify({"error": "Missing required fields"}), 400

        safe_company_id = int(company_id_raw)

        # 3. בדיקת כפילויות מוצר
        existing = Product.query.filter(
            Product.company_id == safe_company_id,
            (Product.sku == sku) | (Product.name == name)
        ).first()

        if existing:
            return jsonify({
                "status": "exists",
                "product_id": existing.id,
                "local_id": existing.local_id
            }), 200

        # 4. המרת תאריך
        try:
            date_obj = datetime.strptime(received_date_raw, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%Y-%m-%d')
        except:
            formatted_date = received_date_raw

        # 5. חישוב local_id
        max_local = db.session.query(db.func.max(db.cast(Product.local_id, db.Integer))) \
            .filter(Product.company_id == safe_company_id).scalar()
        next_local_id = (max_local or 0) + 1

        # 6. יצירת מוצר חדש
        new_product = Product(
            company_id=safe_company_id,
            local_id=next_local_id,
            sku=sku,
            name=name,
            description=data.get("description", ""),
            price=float(data.get("price", 0)),
            cost_price=float(data.get("cost_price", 0)),
            quantity=int(data.get("quantity", 0)),
            income_category="product",
            received_date=formatted_date
        )

        db.session.add(new_product)
        db.session.commit()

        # 7. תרגום ברקע
        translate_product_in_background(
            product_id=new_product.id,
            company_id=safe_company_id,
            name=new_product.name,
            description=new_product.description,
            price=new_product.price,
            income_category=new_product.income_category,
            sku=new_product.sku,
            supplier_name=data.get("supplier_name", "")
        )

        return jsonify({
            "status": "success",
            "product_id": new_product.id,
            "local_id": new_product.local_id
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


# ----------------------
#  ADD Product TO Inventory FROM INVOICE AI EMAIL(from_n8n)
# ----------------------

@app.route('/api/inventory/add_from_n8n', methods=['POST'])
def api_add_inventory_from_n8n():
    try:
        # 1. אימות מול n8n
        auth_header = request.headers.get("X-API-KEY")
        if auth_header != N8N_API_KEY:
            return jsonify({"error": "Unauthorized API Access"}), 401

        data = request.get_json() or {}

        company_id_raw = data.get("company_id")
        product_local_id_raw = data.get("product_local_id")
        stock_in = int(data.get("stock_in", 0))
        cost_price = float(data.get("cost_price", 0))
        received_date_raw = (data.get("received_date") or "").strip()

        if not company_id_raw or not product_local_id_raw or stock_in <= 0:
            return jsonify({"error": "Missing required fields"}), 400

        safe_company_id = int(company_id_raw)
        safe_local_id = int(product_local_id_raw)

        # 2. המרת תאריך
        try:
            date_obj = datetime.strptime(received_date_raw, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%Y-%m-%d')
        except:
            formatted_date = received_date_raw

        # 3. שמירת טרנזקציית מלאי
        save_inventory_transaction(
            company_id=safe_company_id,
            local_id=safe_local_id,
            received_date=formatted_date,
            stock_in=stock_in,
            cost_price=cost_price,
            supplier_id=data.get("supplier_id"),
            supplier_name=data.get("supplier_name", "")
        )

        return jsonify({
            "status": "success",
            "message": "Inventory updated successfully"
        }), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ----------------------
#   Delete All Product  (Multi-Tenant Secure)
# ----------------------

def delete_product_folder(product_id, company_id, local_id=None):

    effective_local_id = local_id if local_id is not None else product_id
    base_dir = app.config["ITEMS_DIR"]
    
    # אבטחת מחרוזות נקיות לחלוטין למניעת נתקי סנכרון ושמות תיקייה שגויים בלינוקס
    safe_company = str(company_id).strip()
    safe_local = str(effective_local_id).strip()
    
    folder_path = os.path.join(base_dir, f"{safe_company}_{safe_local}")

    if folder_path and os.path.exists(folder_path):
        try:
            # שחרור וניקוי אגרסיבי של כל הקבצים הפנימיים לפני מחיקת התיקייה הכללית
            for root, dirs, files in os.walk(folder_path, topdown=False):
                for name in files:
                    file_path = os.path.join(root, name)
                    try:
                        os.chmod(file_path, 0o777)  # שבירת הגנות ווינדוס ונעילות קבצים
                        os.remove(file_path)
                    except:
                        pass
                for name in dirs:
                    dir_path = os.path.join(root, name)
                    try:
                        os.chmod(dir_path, 0o777)
                        os.rmdir(dir_path)
                    except:
                        pass

            # שינוי הרשאות לתיקיית האב ומחיקתה הסופית מהעולם
            os.chmod(folder_path, 0o777)
            shutil.rmtree(folder_path, ignore_errors=True)
            
            # וידוא סופי במידה ומערכת ההפעלה עדיין משאירה עקבות בדיסק
            if os.path.exists(folder_path):
                shutil.rmtree(folder_path)
                
            print(f"✔ Product folder wiped and shredded successfully: {folder_path}")
        except Exception as e:
            print(f"❌ Critical: Could not remove product folder directory node: {e}")



@app.route('/delete_selected_products', methods=['POST'])
@login_required
def delete_selected_products():
    selected_payloads = request.form.getlist('delete_products')

    if not selected_payloads:
        flash(py_i18n("products.delete_none_selected"), "warning")
        return redirect(url_for('manage_products'))

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    # המרה כפויה ל-int בשביל אבטחת השאילתות והפרדת Multi-Company ב-Render
    if company_id:
        company_id = int(company_id)

    try:
        product_ids_to_full_delete = []
        
        for payload in selected_payloads:
            if "_" in str(payload):
                parts = payload.split("_")
                p_identifier = int(parts[0])      
                raw_payload_date = parts[1]       
                batch_qty = int(parts[2])          

                product = Product.query.filter(
                    (Product.id == p_identifier) | (Product.local_id == p_identifier),
                    Product.company_id == company_id
                ).first()
                
                if not product:
                    continue

                effective_local_id = product.local_id if product.local_id is not None else product.id
                folder = ensure_product_folder(company_id, effective_local_id)
                inv_folder = os.path.join(folder, "inventory_transactions")

                batch_date_clean = None
                possible_formats = ['%d-%m-%Y', '%m-%d-%Y', '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%Y/%m/%d']
                
                for fmt in possible_formats:
                    try:
                        parsed_dt = datetime.strptime(raw_payload_date, fmt)
                        batch_date_clean = parsed_dt.strftime('%Y-%m-%d')
                        break
                    except ValueError:
                        continue
                
                if not batch_date_clean:
                    batch_date_clean = raw_payload_date.replace("-", "/")

                product_file_path = os.path.join(folder, "product.json")
                current_batches = []
                prod_data = {}
                
                if os.path.isfile(product_file_path):
                    try:
                        with open(product_file_path, "r", encoding="utf-8") as f:
                            prod_data = json.load(f)
                            current_batches = prod_data.get("batches") or []
                    except:
                        pass

                if not current_batches:
                    current_batches = _load_inventory_batches(company_id, effective_local_id)

                updated_batches = []
                batch_removed = False
                target_supplier_id = None

                for b in current_batches:
                    b_date_raw = str(b.get("received_date", "")).strip()
                    
                    b_date_clean = None
                    for fmt in possible_formats:
                        try:
                            parsed_b_dt = datetime.strptime(b_date_raw, fmt)
                            b_date_clean = parsed_b_dt.strftime('%Y-%m-%d')
                            break
                        except ValueError:
                            continue
                    if not b_date_clean:
                        b_date_clean = b_date_raw.replace("-", "/")

                    if not batch_removed and (b_date_clean == batch_date_clean or b_date_raw == raw_payload_date) and int(b.get("stock_in", 0)) == batch_qty:
                        batch_removed = True
                        target_supplier_id = b.get("supplier_id")
                        continue
                    updated_batches.append(b)

                if batch_removed:
                    if target_supplier_id:
                        try:
                            db.session.query(SupplierPurchase).filter(
                                SupplierPurchase.supplier_id == int(target_supplier_id),
                                SupplierPurchase.product_id == product.id,
                                SupplierPurchase.quantity == batch_qty
                            ).delete(synchronize_session=False)
                        except:
                            pass

                    # מחיקה פיזית של קובץ האצווה הפנימי הספציפי מהדיסק
                    if os.path.isdir(inv_folder):
                        for fname in os.listdir(inv_folder):
                            if fname.startswith("inventory_transactions_") and fname.endswith(".json"):
                                fpath = os.path.join(inv_folder, fname)
                                try:
                                    with open(fpath, "r", encoding="utf-8") as f:
                                        tx = json.load(f)
                                    tx_date_raw = str(tx.get("received_date", "")).strip()
                                    tx_local_id = tx.get("product_local_id")
                                    
                                    tx_date_clean = None
                                    for fmt in possible_formats:
                                        try:
                                            parsed_tx_dt = datetime.strptime(tx_date_raw, fmt)
                                            tx_date_clean = parsed_tx_dt.strftime('%Y-%m-%d')
                                            break
                                        except ValueError:
                                            continue
                                    if not tx_date_clean:
                                        tx_date_clean = tx_date_raw.replace("-", "/")
                                    
                                    if isinstance(tx, dict) and str(tx_local_id) == str(effective_local_id) and (tx_date_clean == batch_date_clean or tx_date_raw == raw_payload_date) and int(tx.get("stock_in", 0)) == batch_qty:
                                        os.remove(fpath)
                                        print(f"✔ File Deleted Successfully: {fpath}")
                                        break
                                except:
                                    pass

                    # שכתוב קובץ ה-product.json הראשי 
                    if updated_batches:
                        total_sold = db.session.query(func.sum(InvoiceItem.quantity)) \
                            .join(Invoice).filter(
                                InvoiceItem.product_id == str(effective_local_id),
                                InvoiceItem.income_category == 'product',
                                Invoice.company_id == company_id,
                                Invoice.status != "canceled"
                            ).scalar() or 0

                        new_stock_in = sum(int(b.get("stock_in", 0)) for b in updated_batches)
                        product.quantity = new_stock_in - int(total_sold)

                        next_latest_batch = updated_batches[-1]
                        product.cost_price = float(next_latest_batch.get("cost_price", product.cost_price or 0.0))
                        product.received_date = next_latest_batch.get("received_date", product.received_date)

                        db.session.flush()

                        if prod_data:
                            try:
                                prod_data["stock_in"] = new_stock_in
                                prod_data["cost_price"] = product.cost_price
                                prod_data["batches"] = updated_batches
                                with open(product_file_path, "w", encoding="utf-8") as f:
                                    json.dump(prod_data, f, ensure_ascii=False, indent=4)
                            except:
                                pass
                        continue
                    else:
                        product_ids_to_full_delete.append(product.id)
            else:
                product_ids_to_full_delete.append(int(payload))

        if product_ids_to_full_delete:
            product_ids_to_full_delete = list(set(product_ids_to_full_delete))
            products_to_delete = Product.query.filter(
                Product.id.in_(product_ids_to_full_delete),
                Product.company_id == company_id
            ).all()

            deleted_meta = [(p.id, p.local_id) for p in products_to_delete]
            all_invoice_ids = [inv.id for inv in Invoice.query.filter_by(company_id=company_id).all()]

            if all_invoice_ids and deleted_meta:
                for pid, lid in deleted_meta:
                    effective_local_id = lid if lid is not None else pid
                    db.session.query(InvoiceItem).filter(
                        InvoiceItem.invoice_id.in_(all_invoice_ids),
                        InvoiceItem.product_id == str(effective_local_id),
                        InvoiceItem.income_category == 'product'
                    ).update({InvoiceItem.product_id: "deleted"}, synchronize_session=False)
                    db.session.flush()

            for pid, lid in deleted_meta:
                delete_product_folder(product_id=pid, company_id=company_id, local_id=lid)

            Product.query.filter(
                Product.id.in_(product_ids_to_full_delete),
                Product.company_id == company_id
            ).delete(synchronize_session=False)

        db.session.commit()
        flash(py_i18n("products.delete_success").format(count=len(selected_payloads)), "success")
        return redirect(url_for('manage_products'))

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Product Deletion Fault Triggered: {e}")
        flash(py_i18n("products.delete_error").format(error=str(e)), "danger")
        return redirect(url_for('manage_products'))


# =============================================================
#  CUSTOMERS (Multi-Tenant Secure) - one block, no duplicates
# =============================================================

# "id" = global customer id, "local_id" = 1,2,3 numbering inside the company.
# Must match the value your invoice dropdown sends to /api/customer/<n>
CUSTOMER_LOOKUP_FIELD = "id"


def _find_customer(company_id, raw_value):
    """Find a customer inside one company, using the same key as /api/customer."""
    try:
        value = int(raw_value)
    except (ValueError, TypeError):
        return None
    field = Customer.local_id if CUSTOMER_LOOKUP_FIELD == "local_id" else Customer.id
    return Customer.query.filter(field == value, Customer.company_id == company_id).first()


def _clip(value, max_len):
    return (value or '').strip()[:max_len]


def _blocked(message, category="warning", reason=""):
    """A refused save: show the message AND print the exact reason in the terminal."""
    print(f"⛔ CUSTOMER NOT SAVED: {reason or message}")
    flash(message, category)
    return redirect(url_for('customer'))


# -------------------------------------------------------------
#  Invitation by e-mail (the customer sets his own password)
# -------------------------------------------------------------

def _mail_ready():
    """True when the mail server settings exist (so the invitation e-mail can really be sent)."""
    server = app.config.get('MAIL_SERVER')
    return bool(server and server != 'localhost' and app.config.get('MAIL_USERNAME'))


def send_invitation_email(user, hours=72):
    """Creates a set-password link and e-mails it in the background. Returns the link."""
    token = create_reset_token(user, hours=hours)
    base_url = os.getenv('BASE_URL') or request.host_url.rstrip('/')
    link = f"{base_url}/set-password?token={token}"

    company = db.session.get(Company, user.company_id) if user.company_id else None
    company_name = company.name if company else ""
    to_email = user.email
    app_obj = current_app._get_current_object()

    def _send():
        with app_obj.app_context():
            try:
                msg = Message(
                    "הזמנה להצטרפות למערכת",
                    recipients=[to_email],
                    body=(f"שלום,\n\nנפתח עבורך חשבון במערכת"
                          f"{(' עבור ' + company_name) if company_name else ''}.\n"
                          f"לקביעת סיסמה והתחברות לחץ על הקישור (תקף ל-{hours} שעות):\n{link}\n")
                )
                mail.send(msg)
                print(f"✔ Invitation e-mail sent to {to_email}")
            except Exception as e:
                print(f"⚠ Invitation e-mail failed for {to_email}: {e}")

    threading.Thread(target=_send, daemon=True).start()

    if not IS_RENDER:
        print(f"INVITE LINK (local only): {link}")    # for testing on your own computer
    return link


def _invite_page(email, link, mail_ready):
    """Page for the owner: the invitation link, in case the e-mail does not arrive."""
    mail_line = ("נשלח אליו גם אימייל עם הקישור." if mail_ready
                 else "שליחת אימייל אינה מוגדרת בשרת, לכן יש להעביר לו את הקישור ידנית (וואטסאפ, SMS).")
    return f"""
    <div dir="rtl" style="font-family:Arial;padding:40px;font-size:18px;max-width:800px">
      <h2>החברה נוצרה בהצלחה</h2>
      <p>אימייל: <b>{escape(email)}</b></p>
      <p>{mail_line}</p>
      <p>קישור לקביעת סיסמה (תקף 72 שעות, לשימוש הלקוח בלבד):</p>
      <input id="lnk" value="{escape(link)}" readonly
             style="width:100%;padding:10px;font-size:15px;direction:ltr">
      <p><button onclick="navigator.clipboard.writeText(document.getElementById('lnk').value);
                          this.innerText='הועתק'"
                 style="padding:10px 20px;font-size:16px">העתק קישור</button></p>
      <p><a href="{url_for('customer')}">חזרה לדף הלקוחות</a></p>
    </div>
    """


# -------------------------------------------------------------
#  /customer  (GET = page, POST = save)
# -------------------------------------------------------------

@app.route('/customer', methods=['GET', 'POST'])
@login_required
def customer():
    is_owner_user = bool(session.get('owner_access') or getattr(current_user, 'role', '') == 'owner')

    # customer accounts have their own dashboard
    if not is_owner_user and (session.get('role') or '').lower() == 'customer':
        return redirect(url_for('customer_dashboard_router'))

    active_company_id = OWNER_COMPANY_ID if is_owner_user else current_user.company_id
    safe_company_id = int(active_company_id)

    # =========================== POST ===========================
    if request.method == 'POST':
        # ---- read the form (text is cut to the database column sizes) ----
        customer_id     = (request.form.get('customer_id') or '').strip()
        id_number       = _clip(request.form.get('id_number'), 100)
        customer_name   = _clip(request.form.get('customer_name'), 100)
        email_input     = _clip(request.form.get('email'), 100).lower()
        address         = _clip(request.form.get('address'), 100)
        city            = _clip(request.form.get('city'), 100)
        postal_code     = _clip(request.form.get('postal_code'), 100)
        phone           = _clip(request.form.get('phone'), 20)
        contract_status = _clip(request.form.get('contract_status'), 20)
        message         = (request.form.get('message') or '').strip()

        # date: if it is missing, use today
        date_str = (request.form.get('date') or '').strip()
        if date_str:
            try:
                formatted_date = datetime.strptime(date_str, '%Y-%m-%d').strftime('%d/%m/%Y')
            except Exception:
                formatted_date = date_str[:10]
        else:
            formatted_date = datetime.today().strftime('%d/%m/%Y')

        print(f"📝 /customer POST: id='{customer_id}' name='{customer_name}' "
              f"email='{email_input}' company={safe_company_id}")

        # Self invoice mode: nothing to create here
        if customer_id == '0' or (not customer_name and not email_input):
            return _blocked('מצב חשבונית עצמית: לא נוצר לקוח', 'info',
                            reason=f"self-invoice mode (customer_id='{customer_id}', no name and no email)")

        customer_name = customer_name or email_input      # a name is always required in the DB

        try:
            # ------------------ UPDATE EXISTING CUSTOMER ------------------
            customer_obj = _find_customer(safe_company_id, customer_id) if customer_id else None

            if customer_id and not customer_obj:
                # stale id in the form: treat it as a NEW customer instead of dropping the save
                print(f"ℹ customer_id '{customer_id}' not found in company {safe_company_id}: creating a new customer")

            if customer_obj:
                if customer_obj.customer_name == SELF_CUSTOMER_NAME:
                    return _blocked("לא ניתן לערוך את לקוח החשבונית העצמית",
                                    reason="tried to edit the self-invoice customer")

                dup_filter = (Customer.customer_name == customer_name)
                if email_input:
                    dup_filter = dup_filter | (Customer.email == email_input)
                if Customer.query.filter(
                        Customer.company_id == safe_company_id,
                        Customer.id != customer_obj.id, dup_filter).first():
                    return _blocked("לקוח אחר עם אימייל זה או שם זה כבר קיים במערכת שלך!",
                                    reason=f"duplicate name/email on update ('{customer_name}' / '{email_input}')")

                if id_number and Customer.query.filter(
                        Customer.company_id == safe_company_id,
                        Customer.id != customer_obj.id,
                        Customer.id_number == id_number).first():
                    return _blocked("קיים כבר לקוח אחר עם מספר זהות/ח.פ זה במערכת שלך", "danger",
                                    reason=f"duplicate id_number on update ('{id_number}')")

                old_name, old_address = customer_obj.customer_name, customer_obj.address
                old_city, old_message = customer_obj.city, customer_obj.message

                customer_obj.date            = formatted_date
                customer_obj.customer_name   = customer_name
                customer_obj.id_number       = id_number
                customer_obj.address         = address
                customer_obj.city            = city
                customer_obj.postal_code     = postal_code
                customer_obj.phone           = phone
                customer_obj.email           = email_input
                customer_obj.contract_status = contract_status
                customer_obj.message         = message

                db.session.commit()

                if customer_obj.local_id:
                    folder = ensure_customer_folder(safe_company_id, customer_obj.local_id)
                    os.makedirs(folder, exist_ok=True)
                    file_path = os.path.join(folder, "customer.json")

                    existing_data = {}
                    if os.path.isfile(file_path):
                        try:
                            with open(file_path, "r", encoding="utf-8") as f:
                                content = f.read().strip()
                                if content:
                                    existing_data = json.loads(content)
                        except Exception:
                            existing_data = {}

                    src_lang = get_lang()
                    src_keys = ["he", "iw"] if src_lang in ("he", "iw") else [src_lang]
                    for k in src_keys:
                        existing_data.setdefault("name", {})[k] = customer_name
                        existing_data.setdefault("address", {})[k] = address
                        existing_data.setdefault("city", {})[k] = city
                        existing_data.setdefault("message", {})[k] = message

                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(existing_data, f, ensure_ascii=False, indent=4)

                    if (old_name != customer_name or old_address != address or
                            old_city != city or old_message != message):
                        translate_customer_in_background(
                            customer_id=customer_obj.id, company_id=str(safe_company_id),
                            name=customer_name, address=address, city=city,
                            message=message, source_lang=src_lang)

                flash('הנתונים עודכנו בהצלחה!', 'success')
                return redirect(url_for('customer'))

            # ------------------ CREATE NEW CUSTOMER ------------------
            if customer_name == SELF_CUSTOMER_NAME:
                return _blocked("שם זה שמור למערכת, אנא בחר שם אחר",
                                reason="name equals the reserved self-invoice name")

            dup_filter = (Customer.customer_name == customer_name)
            if email_input:
                dup_filter = dup_filter | (Customer.email == email_input)
            if Customer.query.filter(Customer.company_id == safe_company_id, dup_filter).first():
                return _blocked("לקוח עם אימייל זה או שם זה כבר קיים במערכת שלך!",
                                reason=f"duplicate customer in company {safe_company_id} "
                                       f"('{customer_name}' / '{email_input}')")

            if id_number and Customer.query.filter_by(
                    id_number=id_number, company_id=safe_company_id).first():
                return _blocked("קיים כבר לקוח עם מספר זהות/ח.פ זה במערכת שלך", "danger",
                                reason=f"duplicate id_number '{id_number}'")

            user_obj = None
            new_company = None

            if safe_company_id == OWNER_COMPANY_ID and email_input:
                # owner + e-mail = a brand new independent company with a manager login (no password yet)
                if Company.query.filter(
                        (Company.name == customer_name) | (Company.email == email_input)).first():
                    return _blocked("חומת אש: חברה עם שם זה או אימייל זה כבר רשומה במערכת כעסק עצמאי!", "danger",
                                    reason=f"company name/email already exists ('{customer_name}' / '{email_input}')")

                if id_number and Company.query.filter_by(company_id_number=id_number).first():
                    return _blocked("חומת אש: מספר ח.פ / תעודת זהות זו כבר משויכת לחברה קיימת במערכת!", "danger",
                                    reason=f"company id_number already exists '{id_number}'")

                if User.query.filter_by(email=email_input, role='manager').first():
                    return _blocked("חומת אש: כתובת אימייל זו כבר משויכת למנהל קיים במערכת!", "danger",
                                    reason=f"a manager with e-mail '{email_input}' already exists")

                new_company = Company(
                    name=customer_name, email=email_input, company_id_number=id_number,
                    address=address, city=city, postal_code=postal_code, phone=phone,
                    translations_json="{}")
                db.session.add(new_company)
                db.session.flush()

                user_obj = User(
                    email=email_input, username=customer_name[:80],
                    company_id=new_company.id, role='manager',
                    is_active=True, is_approved=True, password_hash=None)
                user_obj.access_expires_at = datetime.utcnow() + timedelta(days=30)   # 30 free days
                db.session.add(user_obj)
                db.session.flush()

            elif safe_company_id != OWNER_COMPANY_ID and email_input:
                # a login account only if the customer has an e-mail (no password yet)
                user_obj = User.query.filter_by(
                    email=email_input, company_id=safe_company_id).first()
                if not user_obj:
                    user_obj = User(
                        email=email_input, username=(customer_name or email_input)[:80],
                        company_id=safe_company_id, role='customer',
                        is_active=True, is_approved=True, password_hash=None)
                    db.session.add(user_obj)
                    db.session.flush()

            new_customer = create_customer(
                safe_company_id, customer_name, email_input,
                date=formatted_date, id_number=id_number, address=address, city=city,
                postal_code=postal_code, phone=phone,
                contract_status=contract_status, message=message)

            if user_obj is not None and user_obj.role == 'customer':
                user_obj.customer_id = new_customer.id

            db.session.commit()
            print(f"✔ CUSTOMER SAVED: id={new_customer.id} local_id={new_customer.local_id} "
                  f"company={safe_company_id}")

            try:
                ensure_customer_folder(safe_company_id, new_customer.local_id)
            except Exception as e:
                print(f"⚠ Customer folder helper failed: {e}")

            if new_company is not None:
                try:
                    translate_company_in_background(
                        company_id=str(new_company.id), name=new_company.name,
                        id_number=new_company.company_id_number, deduction_file="",
                        address=new_company.address, city=new_company.city,
                        postal_code=new_company.postal_code or "", phone=new_company.phone or "",
                        email=email_input, logo="", source_lang=get_lang())
                except Exception as e:
                    print(f"⚠️ Company translation start failed: {e}")

            translate_customer_in_background(
                customer_id=int(new_customer.id), company_id=str(safe_company_id),
                name=new_customer.customer_name, address=new_customer.address,
                city=new_customer.city, message=new_customer.message, source_lang=get_lang())

            # ---- invitation: the customer gets an e-mail and sets his own password ----
            invite_link = None
            invite_failed = False
            if user_obj is not None and not (user_obj.password_hash or '').strip():
                try:
                    invite_link = send_invitation_email(user_obj)
                except Exception as e:
                    invite_failed = True
                    print(f"⚠ Invitation failed: {e}")

            # the owner sees the link when a company was created, or when e-mail is not configured
            if invite_link and (new_company is not None or not _mail_ready()):
                return _invite_page(email_input, invite_link, _mail_ready())

            if invite_link:
                flash(f'הלקוח נוסף, ונשלחה הזמנה עם קישור לקביעת סיסמה אל {email_input}', 'success')
            elif invite_failed:
                flash('הלקוח נוסף, אבל יצירת ההזמנה נכשלה. אפשר לשלוח שוב מדף הלקוחות', 'warning')
            else:
                flash('הלקוח נוסף בהצלחה וסונכרן!', 'success')
            return redirect(url_for('customer'))

        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            return _blocked(f"שגיאה בשמירת הלקוח ({type(e).__name__})", "danger",
                            reason=f"exception {type(e).__name__}: {e}")

    # =========================== GET ===========================
    try:
        language  = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')

        rows_all = Customer.query.filter_by(company_id=safe_company_id).all()

        # every customer-user must be linked to a real customer row
        known_ids = {c.id for c in rows_all}
        by_email = {c.email: c for c in rows_all if c.email}
        changed = False
        for pu in User.query.filter_by(company_id=safe_company_id, role='customer').all():
            if pu.customer_id in known_ids:
                continue
            match = by_email.get(pu.email)
            if match:
                pu.customer_id = match.id
            else:
                c_new = create_customer(
                    safe_company_id, pu.username or pu.email, pu.email,
                    date=datetime.today().strftime('%d/%m/%Y'))
                pu.customer_id = c_new.id
                known_ids.add(c_new.id)
                by_email[pu.email] = c_new
            changed = True
        if changed:
            db.session.commit()

        all_customers = Customer.query.filter(
            Customer.company_id == safe_company_id,
            Customer.customer_name != SELF_CUSTOMER_NAME
        ).order_by(Customer.customer_name).all()

        raw_customer_arg = request.args.get('customer_id')
        customer_id = str(raw_customer_arg).strip() if raw_customer_arg else None

        selected_customer = None
        customer_i18n = {}

        if customer_id and customer_id not in ['', '0']:
            selected_customer = _find_customer(safe_company_id, customer_id)
            if selected_customer:
                customer_i18n = load_customer_translated(
                    selected_customer, language, company_id=str(safe_company_id)) or {}

        input_date_val = today_str
        if selected_customer and selected_customer.date:
            input_date_val = selected_customer.date
            if "/" in input_date_val:
                try:
                    input_date_val = datetime.strptime(
                        input_date_val, '%d/%m/%Y').strftime('%Y-%m-%d')
                except Exception:
                    pass

        company_obj = db.session.get(Company, safe_company_id)
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        customer_i18n_list = _build_customer_i18n_list(
            all_customers, company_obj, company_translated, language, safe_company_id)

        return render_template(
            'customer.html',
            customer=selected_customer,
            all_customers=all_customers,
            customer_i18n=customer_i18n,
            customer_i18n_list=customer_i18n_list,
            today=today_str,
            input_date_val=input_date_val,
            company=company_translated,
            company_db=company_obj
        )

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בטעינת נתוני הלקוחות", "danger")
        return redirect(url_for('dashboard'))


# -------------------------------------------------------------
#  Shared helpers for the customer pages
# -------------------------------------------------------------

def _staff_company_id():
    """Company id for owner / manager / employee pages.
    Returns (company_id, redirect_response). A customer account is sent to its own dashboard."""
    owner_mode = is_owner()
    if not owner_mode and _active_role() == 'customer':
        return None, redirect(url_for('customer_dashboard_router'))
    company_id = OWNER_COMPANY_ID if owner_mode else getattr(current_user, 'company_id', None)
    if not company_id:
        return None, redirect(url_for('unauthorized'))
    return int(company_id), None


def _customer_key(c):
    """The key the page uses to pick a customer. Same rule as /api/customer."""
    if CUSTOMER_LOOKUP_FIELD == "local_id" and c.local_id is not None:
        return c.local_id
    return c.id


def _build_customer_i18n_list(customers, company_obj, company_translated,
                              language, company_id, with_id_number=True):
    """All keys are strings (a mix of numbers and text breaks JSON sorting)."""
    result = {}

    # entry "0" = the company itself (self invoice)
    if company_obj:
        self_name = f"★ {company_translated.get('name') or company_obj.name} "
        self_payload = {
            "name": self_name,
            "address": company_translated.get("address") or company_obj.address or "",
            "city": company_translated.get("city") or company_obj.city or "",
            "message": "",
            "postal_code": company_obj.postal_code or ""
        }
        if with_id_number:
            self_payload["id_number"] = company_obj.company_id_number or ""
        result["0"] = self_payload
        result[self_name] = self_payload

    for c in customers:
        try:
            trans = load_customer_translated(c, language, company_id=str(company_id)) or {}
        except Exception:
            trans = {}

        payload = {
            "name": trans.get("name") or c.customer_name or "",
            "address": trans.get("address") or c.address or "",
            "city": trans.get("city") or c.city or "",
            "message": trans.get("message") or c.message or "",
            "postal_code": trans.get("postal_code") or c.postal_code or ""
        }
        if with_id_number:
            payload["id_number"] = c.id_number or ""

        result[str(_customer_key(c))] = payload          # the id always wins
        if c.customer_name:
            result.setdefault(c.customer_name, payload)  # a name never overwrites an id
    return result


# -------------------------------------------------------------
#  /api/customer/<id>
# -------------------------------------------------------------

@app.route('/api/customer/<int:customer_id>')
@login_required
def api_get_customer(customer_id):
    try:
        language = get_lang()

        owner_mode = is_owner()
        role = _active_role()
        active_company_id = OWNER_COMPANY_ID if owner_mode else getattr(current_user, 'company_id', None)
        if not active_company_id:
            return jsonify({"error": "Forbidden"}), 403

        safe_company_id = int(active_company_id)
        safe_customer_id = int(customer_id)

        # ---------- 0 = the company itself (self invoice) ----------
        if safe_customer_id == 0:
            if role == 'customer':
                return jsonify({"error": "Forbidden"}), 403

            company_obj = db.session.get(Company, safe_company_id)
            if not company_obj:
                return jsonify({"error": "Company not found"}), 404

            company_translated = load_company_translated(company_obj, language)
            return jsonify({
                "id": 0,
                "local_id": None,
                "customer_name": f"★ {company_translated.get('name') or company_obj.name} ",
                "address": company_translated.get('address') or company_obj.address or "",
                "city": company_translated.get('city') or company_obj.city or "",
                "postal_code": company_obj.postal_code or "",
                "id_number": company_obj.company_id_number or "",
                "phone": company_obj.phone or "",
                "email": company_obj.email or "",
                "message": "",
                "date": datetime.today().strftime('%Y-%m-%d'),
                "status": "active"
            })

        # ---------- one key only, always inside the active company ----------
        field = Customer.local_id if CUSTOMER_LOOKUP_FIELD == "local_id" else Customer.id
        c = Customer.query.filter(
            field == safe_customer_id, Customer.company_id == safe_company_id).first()

        if not c:
            return jsonify({"error": "Customer not found"}), 404

        # a customer account may read only its own record
        if role == 'customer':
            own_id = _to_int(session.get('customer_id'))
            if own_id is None:
                own_id = _to_int(getattr(current_user, 'customer_id', None))
            if own_id is None or c.id != own_id:
                return jsonify({"error": "Forbidden"}), 403

        formatted_date_for_picker = ""
        if c.date:
            try:
                formatted_date_for_picker = datetime.strptime(
                    c.date, '%d/%m/%Y').strftime('%Y-%m-%d')
            except Exception:
                formatted_date_for_picker = c.date

        trans = load_customer_translated(c, language, company_id=str(safe_company_id)) or {}

        return jsonify({
            "id": c.id,
            "local_id": c.local_id,
            "customer_name": trans.get("name") or c.customer_name or "",
            "address": trans.get("address") or c.address or "",
            "city": trans.get("city") or c.city or "",
            "postal_code": trans.get("postal_code") or c.postal_code or "",
            "id_number": c.id_number or "",
            "phone": c.phone or "",
            "email": c.email or "",
            "message": trans.get("message") or c.message or "",
            "date": formatted_date_for_picker,
            "status": "active"
        })

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Server error"}), 500


# -------------------------------------------------------------
#  Search customers
# -------------------------------------------------------------

@app.route('/search_customer', methods=['GET', 'POST'])
@login_required
def search_customer():
    try:
        language = get_lang()

        safe_company_id, redirect_response = _staff_company_id()
        if redirect_response:
            return redirect_response

        search_name = (request.form.get('search_name') if request.method == 'POST'
                       else request.args.get('search_name'))

        today_str = datetime.today().strftime('%Y-%m-%d')
        input_date_val = today_str
        customer = None

        # the self-invoice row is shown as the "★ company" entry, not as a normal customer
        all_customers = Customer.query.filter(
            Customer.company_id == safe_company_id,
            Customer.customer_name != SELF_CUSTOMER_NAME
        ).order_by(Customer.customer_name).all()

        if search_name and search_name.strip():
            term = search_name.strip()
            safe_term = term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            search_results = Customer.query.filter(
                Customer.company_id == safe_company_id,
                Customer.customer_name != SELF_CUSTOMER_NAME,
                Customer.customer_name.ilike(f'%{safe_term}%', escape='\\')
            ).order_by(Customer.customer_name).all()

            if search_results:
                customer = search_results[0]
                if customer.date:
                    input_date_val = customer.date
                    if "/" in input_date_val:
                        try:
                            input_date_val = datetime.strptime(
                                input_date_val, "%d/%m/%Y").strftime("%Y-%m-%d")
                        except Exception:
                            input_date_val = today_str
        else:
            search_results = all_customers

        customer_i18n = {}
        if customer:
            trans = load_customer_translated(
                customer, language, company_id=str(safe_company_id)) or {}
            customer_i18n = {
                "name": trans.get("name") or customer.customer_name or "",
                "address": trans.get("address") or customer.address or "",
                "city": trans.get("city") or customer.city or "",
                "message": trans.get("message") or customer.message or ""
            }

        company_obj = db.session.get(Company, safe_company_id)
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        customer_i18n_list = _build_customer_i18n_list(
            all_customers, company_obj, company_translated, language, safe_company_id)

        return render_template(
            'customer.html',
            customers=search_results,
            all_customers=all_customers,
            customer=customer,
            customer_i18n=customer_i18n,
            customer_i18n_list=customer_i18n_list,
            today=today_str,
            input_date_val=input_date_val,
            language=language,
            company=company_translated,
            company_db=company_obj
        )

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return redirect(url_for('customer'))


# -------------------------------------------------------------
#  Clear customer search
# -------------------------------------------------------------

@app.route('/clear_search_results_customer', methods=['POST'])
@login_required
def clear_search_results_customer():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')

        safe_company_id, redirect_response = _staff_company_id()
        if redirect_response:
            return redirect_response

        all_customers = Customer.query.filter(
            Customer.company_id == safe_company_id,
            Customer.customer_name != SELF_CUSTOMER_NAME
        ).order_by(Customer.customer_name).all()

        company_obj = db.session.get(Company, safe_company_id)
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        customer_i18n_list = _build_customer_i18n_list(
            all_customers, company_obj, company_translated, language, safe_company_id)

        return render_template(
            'customer.html',
            customers=[],
            all_customers=all_customers,
            customer=None,
            customer_i18n={},
            customer_i18n_list=customer_i18n_list,
            today=today_str,
            input_date_val=today_str,
            company=company_translated,
            company_db=company_obj
        )

    except Exception:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return redirect(url_for('customer'))


# ---------------------------------------------------------------------------
#   Build All Employees Form employee.html (Multi-Tenant Secure)
# ---------------------------------------------------------------------------

@app.route('/employee', methods=['GET', 'POST'])
@login_required
def employee():
    today_str = datetime.today().strftime('%Y-%m-%d')
    language  = get_lang()

    # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות מיושרת לפי ה-index) -----------------
    user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
    if session.get('owner_access') or user_role == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

    # Render: אם החברה ריקה (New DB), נותן חברה 1 כברירת מחדל ומונע חסימה קטלנית 
    if not active_company_id:
        active_company_id = 1
    # ---------------------------------------------------------------------------

    if request.method == 'POST':
        date_str = request.form.get('date')
        if not date_str:
            flash('תאריך הוא שדה חובה', 'error')
            return redirect(url_for('employee'))

        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%d/%m/%Y')
        except:
            formatted_date = date_str

        employee_id   = request.form.get('employee_id')
        id_number     = request.form.get('id_number', '').strip()
        employee_name = request.form.get('employee_name', '').strip()
        email_input   = request.form.get('email', '').strip().lower()

        # ------------------ UPDATE CUSTOMER (טבלת Employee + EmployeeData) ------------------
        if employee_id and str(employee_id).strip() not in ['', '0']:
            try:
                # Render: המרה קשיחה ל-int כדי שפוסטגרס לא יקרוס על השוואת VARCHAR ל-Integer 
                employee_id_int = int(employee_id)
                
                employee_obj = Employee.query.filter_by(
                    id=employee_id_int,
                    company_id=active_company_id
                ).first()
            except (ValueError, TypeError):
                employee_obj = None

            if employee_obj:
                employee_obj.date            = formatted_date
                employee_obj.employee_name   = employee_name
                employee_obj.id_number       = id_number
                employee_obj.address         = request.form.get('address')
                employee_obj.city            = request.form.get('city')
                employee_obj.postal_code     = request.form.get('postal_code')
                employee_obj.mobile_phone    = request.form.get('phone')
                employee_obj.email           = email_input
                employee_obj.contract_status = request.form.get('contract_status')
                employee_obj.message         = request.form.get('message')

                # מוודא שהטבלה קיימת ב-PostgreSQL לפני ביצוע שאילתת ההצלבה של השכר
                db.create_all()

                #  סנכרון עדכון  EmployeeData (מאובטח באמצעות ה-int המומר)
                salary_emp = EmployeeData.query.filter_by(id=employee_obj.id, company_id=active_company_id).first()
                if salary_emp:
                    salary_emp.employee_name = employee_name
                    salary_emp.id_number     = id_number
                    salary_emp.address       = request.form.get('address')
                    salary_emp.city          = request.form.get('city')
                    salary_emp.postal_code   = request.form.get('postal_code')
                    salary_emp.mobile_phone  = request.form.get('phone') # מבוצר הרמטית בעדכון!
                    salary_emp.email         = email_input

                db.session.commit()

                #  עדכון -Contact Form
                session['shared_employee_name'] = employee_obj.employee_name
                session['shared_email'] = employee_obj.email
                session['shared_address'] = employee_obj.address
                session['shared_phone'] = request.form.get('phone')

                translate_employee_in_background(
                    employee_id=employee_obj.id,
                    company_id=active_company_id,
                    name=employee_obj.employee_name,
                    address=employee_obj.address,
                    city=employee_obj.city,
                    message=employee_obj.message,
                    mobile_phone=request.form.get('phone'),
                    id_number=employee_obj.id_number,
                    postal_code=employee_obj.postal_code
                )

                flash('הנתונים עודכנו בהצלחה!', 'success')

        # ------------------ CREATE NEW EMPLOYEE ( Employee + EmployeeData) ------------------
        else:
            # הגנה: מוודא שהטבלאות קיימות ב-PostgreSQL לפני ביצוע שאילתת הבדיקה
            db.create_all()

            duplicate = Employee.query.filter(
                (Employee.company_id == active_company_id) &
                ((Employee.email == email_input) | (Employee.employee_name == employee_name))
            ).first()

            if duplicate:
                flash("לקוח עם אימייל זה או שם זה כבר קיים בחברה שלך", "warning")
                return redirect(url_for('employee'))

            if id_number and Employee.query.filter_by(
                id_number=id_number,
                company_id=active_company_id
            ).first():
                flash("קיים כבר לקוח עם מספר זהות זה במערכת שלך", "error")
                return redirect(url_for('employee'))

            existing_user = User.query.filter_by(
                email=email_input,
                company_id=active_company_id
            ).first()

            if existing_user:
                user_obj = existing_user
            else:
                user_obj = User(
                    email=email_input,
                    username=employee_name or email_input,
                    company_id=active_company_id,
                    role='employee',
                    is_active=True
                )
                db.session.add(user_obj)
                db.session.commit()

            last_employee = Employee.query.filter_by(company_id=active_company_id)\
                .order_by(Employee.local_id.desc()).first()
            
            # המרה תקנית של מזהה ה-local_id למניעת שיבושי מספרים בשרת
            next_local_id = 1 if not last_employee or not last_employee.local_id else (int(last_employee.local_id) + 1)

            new_employee = Employee(
                id=user_obj.id,
                company_id=active_company_id,
                local_id=next_local_id,   
                date=formatted_date,
                employee_name=employee_name,
                id_number=id_number,
                address=request.form.get('address'),
                city=request.form.get('city'),
                postal_code=request.form.get('postal_code'),
                mobile_phone=request.form.get('phone'),
                email=email_input,
                contract_status=request.form.get('contract_status'),
                message=request.form.get('message'),
                role='employee',
                is_active=True
            )
            db.session.add(new_employee)
            db.session.flush() # מאלץ יצירה קשיחה של רשומת האבא ב-PostgreSQL

            #   שמירה מבוצרת לטבלת EmployeeData 
            new_salary_emp = EmployeeData(
                id=user_obj.id,
                company_id=active_company_id,
                local_id=next_local_id,
                employee_id=user_obj.id, # ממלא את חוק החובה הפיננסי של ה-Not Null
                employee_name=employee_name,
                id_number=id_number,
                address=request.form.get('address'),
                city=request.form.get('city'),
                postal_code=request.form.get('postal_code'),
                mobile_phone=request.form.get('phone'), 
                email=email_input,
                date=today_str,
                
                total_hours=0.0,
                totalHours=0.0,
                basic_salary=0.0,
                gross_salary=0.0,
                net_payment=0.0,
                net_value=0.0,
                income_tax=0.0,
                total_deductions=0.0,
                bank_number="",
                branch_number="",
                account_number="",
                employee_number="",
                tax_point_child=0.0, 
                work_percent=100.0,
                tax_credit_points=2.25
            )
            db.session.add(new_salary_emp)

            db.session.commit() 

            session['shared_employee_name'] = new_employee.employee_name
            session['shared_email'] = new_employee.email
            session['shared_address'] = new_employee.address
            session['shared_phone'] = request.form.get('phone')

            try:
                folder = ensure_employee_folder(active_company_id, next_local_id)
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                print(f"⚠ Failed to create employee folder: {e}")

            translate_employee_in_background(
                employee_id=new_employee.id,
                company_id=active_company_id,
                name=new_employee.employee_name,
                address=new_employee.address,
                city=new_employee.city,
                mobile_phone=request.form.get('phone'),
                id_number=new_employee.id_number,
                postal_code=new_employee.postal_code,
                message=new_employee.message
            )

            flash('הלקוח נוסף בהצלחה וסונכרן!', 'success')

        return redirect(url_for('employee'))

    # ------------------ GET REQUEST ------------------
    db.create_all() # מוודא שהטבלה קיימת בטעינה הראשונית של הדף בענן
    
    all_employees = Employee.query.filter_by(
        company_id=active_company_id
    ).order_by(Employee.employee_name).all()

    pending_users = User.query.filter_by(
        company_id=active_company_id,
        role='employee'
    ).all()

    existing_employee_emails = {c.email for c in all_employees}

    pending_employees_list = []
    for pu in pending_users:
        if pu.email not in existing_employee_emails:
            pending_employees_list.append(
                Employee(
                    id=pu.id,
                    company_id=active_company_id,
                    local_id=None,   
                    employee_name=pu.username or pu.email,
                    email=pu.email,
                    date=today_str
                )
            )

    all_employees_combined = all_employees + pending_employees_list

    employee_id = request.args.get('employee_id')

    selected_employee = None
    employee_i18n     = {}

    if employee_id and str(employee_id).strip() not in ['', '0']:
        try:
            employee_id_int = int(employee_id)
            c_obj = Employee.query.filter_by(
                id=employee_id_int,
                company_id=active_company_id
            ).first()
        except (ValueError, TypeError):
            c_obj = None

        if not c_obj:
            c_obj = next(
                (c for c in pending_employees_list if str(c.id) == str(employee_id)),
                None
            )

        if c_obj:
            selected_employee = c_obj
            employee_i18n = load_employee_translated(
                c_obj,
                language,
                company_id=active_company_id
            ) or {}

            for k in ["name", "address", "city", "mobile_phone", "id_number", "postal_code", "message"]:
                if employee_i18n.get(k) is None:
                    employee_i18n[k] = ""

    input_date_val = today_str
    if selected_employee and selected_employee.date:
        input_date_val = selected_employee.date
        if "/" in input_date_val:
            try:
                d_obj = datetime.strptime(input_date_val, '%d/%m/%Y')
                input_date_val = d_obj.strftime('%Y-%m-%d')
            except:
                pass

    if selected_employee:
        for attr in ["id_number", "postal_code", "city", "address", "message"]:
            if getattr(selected_employee, attr, None) is None:
                setattr(selected_employee, attr, "")

    # ------------------ GET REQUEST CONTINUATION ------------------
    employee_i18n_list = {}
    for c in all_employees_combined:
        trans = load_employee_translated(c, language, company_id=active_company_id) or {}
        
        final_data = {
            "name": trans.get("name") or c.employee_name or "",
            "address": trans.get("address") or getattr(c, "address", "") or "",
            "city": trans.get("city") or getattr(c, "city", "") or "",
            "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or "",
            "mobile_phone": trans.get("mobile_phone") or getattr(c, "phone", "") or ""
        }
        
        for k, v in final_data.items():
            if v is None:
                final_data[k] = ""
                
        key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
        employee_i18n_list[key] = final_data

    company_obj = db.session.get(Company, active_company_id) if active_company_id else  ""

    return render_template(
        'employee.html',
        employee=selected_employee,
        all_employees=all_employees_combined,
        employee_i18n=employee_i18n,
        employee_i18n_list=employee_i18n_list,
        today=today_str,
        input_date_val=input_date_val,
        company=load_company_translated(company_obj, language) if company_obj else {},
        company_db=company_obj
    )


# -----------------------------------------------------------
#  Secure Individual Employee API Endpoint (Multi-Tenant)
# -----------------------------------------------------------

@app.route('/api/employee/<int:employee_id>')
@login_required
def api_get_employee(employee_id):
    try:
        language = get_lang()

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות מיושרת לפי ה-index) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            active_company_id = 1
        # ---------------------------------------------------------------------------

        db.create_all()

        c = Employee.query.filter_by(
            local_id=employee_id,
            company_id=active_company_id
        ).first()

        if not c:
            c = Employee.query.filter_by(
                id=employee_id,
                company_id=active_company_id
            ).first()

        if not c:
            u = db.session.get(User, employee_id)

            if u and u.company_id == active_company_id and u.role == 'employee':
                return jsonify({
                    "id": u.id,          
                    "local_id": None,    
                    "employee_name": u.username or u.email,
                    "address": "",
                    "city": "",
                    "postal_code": "",
                    "id_number": "",
                    "phone": "",
                    "email": u.email,
                    "message": "",
                    "date": "",
                    "status": "pending"
                })

            return jsonify({"error": "Employee not found"}), 404

        formatted_date_for_picker = ""
        if c.date:
            try:
                temp_date = datetime.strptime(str(c.date).strip(), '%d/%m/%Y')
                formatted_date_for_picker = temp_date.strftime('%Y-%m-%d')
            except:
                try:
                    temp_date = datetime.strptime(str(c.date).strip(), '%Y-%m-%d')
                    formatted_date_for_picker = temp_date.strftime('%Y-%m-%d')
                except:
                    formatted_date_for_picker = str(c.date)

        trans = load_employee_translated(c, language, company_id=active_company_id) or {}

        return jsonify({
            "id": c.id,               
            "local_id": c.local_id,   
            "employee_name": trans.get("name") or c.employee_name or "",
            "address": trans.get("address") or c.address or "",
            "city": trans.get("city") or c.city or "",            
            "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or "",            
            "id_number": c.id_number or "",
            "phone": getattr(c, "mobile_phone", None) or getattr(c, "phone", None) or "", 
            "email": c.email or "",
            "message": trans.get("message") or c.message or "",
            "date": formatted_date_for_picker,
            "status": "active"
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# Search Employees Engine (employee.html) 
# ---------------------------------------------------------------------------

@app.route('/search_employee_data', methods=['GET', 'POST'])
@login_required
def search_employee_data():
    try:
        language = get_lang()  

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות מיושרת לפי ה-index) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            active_company_id = 1
            
        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------------------

        db.create_all()

        search_name = request.form.get('search_name') if request.method == 'POST' else request.args.get('search_name')

        search_results = []
        employee = None
        
        today_str = datetime.today().strftime('%Y-%m-%d')
        input_date_val = today_str

        # Render: שימוש ב-safe_company_id המומר למספר שלם למניעת קריסות SQL
        all_employees = Employee.query.filter_by(
            company_id=safe_company_id
        ).order_by(Employee.employee_name).all()

        if search_name:
            search_name = search_name.strip()

            search_results = Employee.query.filter(
                Employee.company_id == safe_company_id,
                Employee.employee_name.ilike(f'%{search_name}%')
            ).all()

            if search_results:
                employee = search_results[0]

                if employee.date:
                    input_date_val = employee.date
                    try:
                        if "/" in input_date_val:
                            d = datetime.strptime(input_date_val, "%d/%m/%Y")
                            input_date_val = d.strftime("%Y-%m-%d")
                    except:
                        input_date_val = today_str
        else:
            search_results = all_employees

        employee_i18n = {}
        if employee:
            trans = load_employee_translated(employee, language, company_id=safe_company_id) or {}
            #  תיקון קריסת המשתנה c לשימוש ב-employee המקורי והחוקי של הבלוק!
            employee_i18n = {
                "name": trans.get("name") or employee.employee_name or "",
                "address": trans.get("address") or employee.address or "",
                "city": trans.get("city") or employee.city or "",
                "message": trans.get("message") or employee.message or "",
                "mobile_phone": trans.get("mobile_phone") or getattr(employee, "mobile_phone", None) or getattr(employee, "phone", "") or "",
                "postal_code": trans.get("postal_code") or getattr(employee, "postal_code", "") or ""
            }

        employee_i18n_list = {}
        for c in all_employees:
            trans = load_employee_translated(c, language, company_id=safe_company_id) or {}
            
            payload = {
                "name": trans.get("name") or c.employee_name or "",
                "address": trans.get("address") or c.address or "",
                "city": trans.get("city") or c.city or "",
                "message": trans.get("message") or c.message or "",                
                "mobile_phone": trans.get("mobile_phone") or getattr(c, "mobile_phone", None) or getattr(c, "phone", "") or "",
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or ""
            }
            
            key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
            employee_i18n_list[key] = payload

        company_obj = db.session.get(Company, safe_company_id) if safe_company_id else None

        return render_template(
            'employee.html',
            employees=search_results,               
            all_employees=all_employees,            
            employee=employee,                      
            employee_i18n=employee_i18n,            
            employee_i18n_list=employee_i18n_list,  
            today=today_str,
            input_date_val=input_date_val,
            language=language,                      
            company=load_company_translated(company_obj, language) if company_obj else {},
            company_db=company_obj
        )

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        return redirect(url_for('employee'))

# ---------------------------------------------------------------------------
# Clear Employee Search (employee.html)
# ---------------------------------------------------------------------------

@app.route('/clear_employee_data', methods=['POST'])
@login_required
def clear_employee_data():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות מיושרת לפי ה-index) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            active_company_id = 1

        active_company_id = int(active_company_id)
        # ---------------------------------------------------------------------------

        db.create_all()

        all_employees = Employee.query.filter_by(
            company_id=active_company_id
        ).order_by(Employee.employee_name).all()

        employee_i18n_list = {}
        for c in all_employees:
            try:
                trans = load_employee_translated(c, language, company_id=active_company_id) or {}
            except Exception:
                trans = {}

            key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
            
            employee_i18n_list[key] = {
                "name": trans.get("name") or c.employee_name or "",
                "address": trans.get("address") or getattr(c, "address", "") or "",
                "city": trans.get("city") or getattr(c, "city", "") or "",
                "message": trans.get("message") or getattr(c, "message", "") or "",                
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or "",                
                "id_number": getattr(c, "id_number", "") or "",
                "mobile_phone": trans.get("mobile_phone") or getattr(c, "mobile_phone", None) or getattr(c, "phone", "") or ""
            }

        company_obj = db.session.get(Company, active_company_id) if active_company_id else None

        return render_template(
            'employee.html',
            employees=all_employees,          
            all_employees=all_employees,      
            employee=None,                    
            employee_i18n={},                 
            employee_i18n_list=employee_i18n_list,
            today=today_str,
            input_date_val=today_str,
            company=load_company_translated(company_obj, language) if company_obj else {},
            company_db=company_obj
        )

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error in clear_search_results_employee view loop: {e}")
        return redirect(url_for('employee'))


# ----------------------
#   Build All Suppliers Form (Multi-Tenant Secure)
# ----------------------

@app.route('/supplier', methods=['GET', 'POST'])
@login_required
def supplier():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)

        # ------------------ POST ------------------
        if request.method == 'POST':

            date_str = request.form.get('date')
            if not date_str:
                flash('תאריך הוא שדה חובה', 'error')
                return redirect(url_for('supplier'))

            try:
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                formatted_date = date_obj.strftime('%d/%m/%Y')
            except Exception:
                formatted_date = date_str

            raw_supplier_id = request.form.get('supplier_id')
            supplier_id = int(raw_supplier_id) if raw_supplier_id and str(raw_supplier_id).isdigit() else None
            
            supplier_number = request.form.get('supplier_number', '').strip()
            supplier_name = request.form.get('supplier_name', '').strip()
            email_input = request.form.get('email', '').strip().lower()

            # ------------------ UPDATE SUPPLIER ------------------
            if supplier_id:
                supplier_obj = Supplier.query.filter_by(id=supplier_id, company_id=safe_company_id).first()
                if supplier_obj:
                    supplier_obj.date = formatted_date
                    supplier_obj.supplier_name = supplier_name
                    supplier_obj.supplier_number = supplier_number
                    supplier_obj.address = request.form.get('address')
                    supplier_obj.city = request.form.get('city')
                    supplier_obj.postal_code = request.form.get('postal_code')
                    supplier_obj.phone = request.form.get('phone')
                    supplier_obj.email = email_input
                    supplier_obj.payment_terms = request.form.get('payment_terms')
                    supplier_obj.notes = request.form.get('notes')

                    db.session.commit()

                    # הפעלת תרגום מאובטחת עם המרת מזהים קשיחה כמחרוזות לדיסק של Render
                    translate_supplier_in_background(
                        supplier_id=supplier_obj.id,
                        company_id=str(safe_company_id),
                        name=supplier_obj.supplier_name,
                        address=supplier_obj.address,
                        city=supplier_obj.city,
                        postal_code=supplier_obj.postal_code,
                        notes=supplier_obj.notes
                    )

                    flash('נתוני הספק עודכנו בהצלחה!', 'success')

            # ------------------ CREATE NEW SUPPLIER ------------------
            else:
                duplicate = Supplier.query.filter(
                    (Supplier.company_id == safe_company_id) &
                    ((Supplier.email == email_input) | (Supplier.supplier_name == supplier_name))
                ).first()

                if duplicate:
                    flash("ספק עם אימייל זה או שם זה כבר קיים בחברה שלך", "warning")
                    return redirect(url_for('supplier'))

                if supplier_number and Supplier.query.filter_by(
                    supplier_number=supplier_number, 
                    company_id=safe_company_id
                ).first():
                    flash("קיים כבר ספק עם מספר ספק זה במערכת שלך", "error")
                    return redirect(url_for('supplier'))

                last_supplier = Supplier.query.filter_by(company_id=safe_company_id)\
                    .order_by(Supplier.local_id.desc()).first()
                next_local_id = 1 if not last_supplier else last_supplier.local_id + 1

                new_supplier = Supplier(
                    company_id=safe_company_id,
                    local_id=next_local_id,   
                    date=formatted_date,
                    supplier_name=supplier_name,
                    supplier_number=supplier_number,
                    address=request.form.get('address'),
                    city=request.form.get('city'),
                    postal_code=request.form.get('postal_code'),
                    phone=request.form.get('phone'),
                    email=email_input,
                    payment_terms=request.form.get('payment_terms'),
                    notes=request.form.get('notes'),
                    role='supplier',
                    is_active=True
                )

                db.session.add(new_supplier)
                db.session.commit()

                #תיקון קריטי: שימוש בפונקציית התשתית הרשמית והמאובטחת ליצירת התיקייה בדיסק הקבוע
                try:
                    ensure_supplier_folder(str(safe_company_id), str(next_local_id))
                except Exception as e:
                    print(f"⚠ Failed to create supplier folder via layout helper: {e}")

                translate_supplier_in_background(
                    supplier_id=new_supplier.id,
                    company_id=str(safe_company_id),
                    name=new_supplier.supplier_name,
                    address=new_supplier.address,
                    city=new_supplier.city,
                    postal_code=new_supplier.postal_code,
                    notes=new_supplier.notes
                )

                flash('הספק נוסף בהצלחה!', 'success')

            return redirect(url_for('supplier'))

        # ------------------ GET ------------------
        all_suppliers = Supplier.query.filter_by(company_id=safe_company_id).order_by(Supplier.supplier_name).all()
        raw_supplier_arg = request.args.get('supplier_id')
        selected_supplier_id = int(raw_supplier_arg) if raw_supplier_arg and str(raw_supplier_arg).isdigit() else None

        selected_supplier = None
        supplier_i18n = {}

        if selected_supplier_id:
            s_obj = Supplier.query.filter_by(id=selected_supplier_id, company_id=safe_company_id).first()
            if s_obj:
                selected_supplier = s_obj
                supplier_i18n = load_supplier_translated(s_obj, language, company_id=safe_company_id) or {}

        input_date_val = today_str
        if selected_supplier and selected_supplier.date:
            input_date_val = selected_supplier.date
            if "/" in input_date_val:
                try:
                    d_obj = datetime.strptime(input_date_val, '%d/%m/%Y')
                    input_date_val = d_obj.strftime('%Y-%m-%d')
                except Exception:
                    pass

        supplier_i18n_list = {}
        for s in all_suppliers:
            trans = load_supplier_translated(s, language, company_id=safe_company_id) or {"name": s.supplier_name}
            key = s.local_id if getattr(s, "local_id", None) is not None else s.id
            supplier_i18n_list[key] = trans

        company_obj = db.session.get(Company, safe_company_id) if safe_company_id else None

        return render_template(
            'supplier.html',
            supplier=selected_supplier,
            all_suppliers=all_suppliers,
            supplier_i18n=supplier_i18n,
            supplier_i18n_list=supplier_i18n_list,
            today=today_str,
            input_date_val=input_date_val,
            company=load_company_translated(company_obj, language),
            company_db=company_obj
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Critical error inside unified multi-tenant supplier execution: {e}")
        return redirect(url_for('dashboard'))


# -----------------------------------------------------------
#  Secure Individual Supplier API Endpoint (Multi-Tenant)
# -----------------------------------------------------------

@app.route('/api/supplier/<int:supplier_id>')
@login_required
def api_get_supplier(supplier_id):
    try:
        language = get_lang()

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)
        safe_supplier_id = int(supplier_id)

        s = Supplier.query.filter(
            ((Supplier.id == safe_supplier_id) | (Supplier.local_id == safe_supplier_id)),
            Supplier.company_id == safe_company_id
        ).first()

        if not s:
            u = db.session.get(User, safe_supplier_id)

            if u and u.company_id == safe_company_id and u.role == 'supplier':
                return jsonify({
                    "id": u.id,          
                    "local_id": None,    
                    "supplier_name": u.username or u.email,
                    "supplier_number": "",
                    "address": "",
                    "city": "",
                    "postal_code": "",
                    "phone": "",
                    "email": u.email,
                    "payment_terms": "",
                    "notes": "",
                    "date": "",
                    "status": "pending"
                })

            return jsonify({"error": "Supplier not found"}), 404

        formatted_date_for_picker = ""
        if s.date:
            try:
                temp_date = datetime.strptime(s.date, '%d/%m/%Y')
                formatted_date_for_picker = temp_date.strftime('%Y-%m-%d')
            except:
                formatted_date_for_picker = s.date

        #תיקון קריטי: העברת מזהה חברה כמחרוזת נקייה כדי להבטיח שליפה ללא נתקים מדיסק ה-Render
        trans = load_supplier_translated(s, language, company_id=str(safe_company_id)) or {}

        return jsonify({
            "id": s.id,               
            "local_id": s.local_id,   
            "supplier_name": trans.get("name", s.supplier_name or ""),
            "supplier_number": s.supplier_number or "",
            "address": trans.get("address", s.address or ""),
            "city": trans.get("city", s.city or ""),
            "postal_code": trans.get("postal_code", s.postal_code or ""),
            "phone": s.phone or "",
            "email": s.email or "",
            "payment_terms": s.payment_terms or "",
            "notes": trans.get("notes", s.notes or ""),
            "date": formatted_date_for_picker,
            "status": "active"
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------
#  N8N NEW FOR GREAT AUTO Supplier API Endpoint (Multi-Tenant)
# -----------------------------------------------------------

@app.route('/api/supplier/add_from_n8n', methods=['POST'])
def api_add_supplier_from_n8n():
    try:
        # 1. בדיקת מפתח אבטחה מול n8n
        auth_header = request.headers.get("X-API-KEY")
        if auth_header != N8N_API_KEY:
            return jsonify({"error": "Unauthorized API Access"}), 401

        data = request.get_json() or {}
        
        supplier_name = data.get("supplier_name", "").strip()
        if not supplier_name:
            return jsonify({"error": "Supplier name is required"}), 400

        # קביעת החברה האקטיבית והמרה בטוחה ל-int עבור Postgres ברנדר
        raw_company_id = data.get("company_id", OWNER_COMPANY_ID)
        safe_company_id = int(raw_company_id)

        email_input = data.get("email", "").strip().lower()
        supplier_number = data.get("supplier_number", "").strip()

        # 2. בדיקת כפילויות בדיוק כמו בראוט הרגיל שלך מול Postgres
        duplicate = Supplier.query.filter(
            (Supplier.company_id == safe_company_id) &
            ((Supplier.email == email_input) | (Supplier.supplier_name == supplier_name))
        ).first()
        if duplicate:
            return jsonify({"error": "Supplier with this email or name already exists in this company"}), 400

        if supplier_number and Supplier.query.filter_by(
            supplier_number=supplier_number, 
            company_id=safe_company_id
        ).first():
            return jsonify({"error": "Supplier with this number already exists"}), 400

        # 3. חישוב ה-local_id הבא בתור לחברה הזו
        last_supplier = Supplier.query.filter_by(company_id=safe_company_id)\
            .order_by(Supplier.local_id.desc()).first()
        next_local_id = 1 if not last_supplier else last_supplier.local_id + 1

        # עיבוד תאריך (n8n ישלח תאריך או שנשתמש בהיום)
        date_str = str(data.get("date", datetime.today().strftime('%Y-%m-%d'))).strip()
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%d/%m/%Y')
        except Exception:
            formatted_date = date_str

        # 4. יצירת אובייקט הספק החדש עם כל השדות התואמים
        new_supplier = Supplier(
            company_id=safe_company_id,
            local_id=next_local_id,   
            date=formatted_date,
            supplier_name=supplier_name,
            supplier_number=supplier_number,
            address=data.get("address", ""),
            city=data.get("city", ""),
            postal_code=data.get("postal_code", ""),
            phone=data.get("phone", ""),
            email=email_input,
            payment_terms=data.get("payment_terms", ""),
            notes=data.get("notes", "Added automatically via n8n from email invoice"),
            role='supplier',
            is_active=True
        )

        db.session.add(new_supplier)
        db.session.commit()

        # 5.תיקון קריטי: יצירת תיקיית הספק בדיסק הקבוע של Render באמצעות ה-Helper הרשמי
        try:
            ensure_supplier_folder(str(safe_company_id), str(next_local_id))
        except Exception as e:
            print(f"⚠ n8n flow - Failed to create supplier folder via layout helper: {e}")

        # 6. הפעלת מנגנון התרגום ברקע של המערכת שלך (העברת מזהה חברה כמחרוזת נקייה)
        try:
            translate_supplier_in_background(
                supplier_id=new_supplier.id,
                company_id=str(safe_company_id),
                name=new_supplier.supplier_name,
                address=new_supplier.address,
                city=new_supplier.city,
                postal_code=new_supplier.postal_code,
                notes=new_supplier.notes
            )
        except Exception as e:
            print(f"⚠ n8n flow - Translation trigger failed: {e}")

        return jsonify({
            "status": "success",
            "message": "Supplier created and integrated successfully",
            "supplier_id": new_supplier.id,
            "local_id": new_supplier.local_id
        }), 201

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------
#  Search Suppliers Engine (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/search_supplier', methods=['GET', 'POST'])
@login_required
def search_supplier():
    try:
        language = get_lang()

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)

        search_name = request.form.get('search_supplier') if request.method == 'POST' else request.args.get('search_supplier')

        search_results = []
        supplier = None
        
        today_str = datetime.today().strftime('%Y-%m-%d')
        input_date_val = today_str

        if search_name:
            search_name = search_name.strip()

            search_results = Supplier.query.filter(
                Supplier.company_id == safe_company_id,
                Supplier.supplier_name.ilike(f'%{search_name}%')
            ).all()

            if search_results:
                supplier = search_results[0]

                if supplier.date:
                    input_date_val = supplier.date
                    try:
                        if "/" in input_date_val:
                            d = datetime.strptime(input_date_val, "%d/%m/%Y")
                            input_date_val = d.strftime("%Y-%m-%d")
                    except:
                        input_date_val = today_str

        all_suppliers = Supplier.query.filter_by(
            company_id=safe_company_id
        ).order_by(Supplier.supplier_name).all()

        supplier_i18n = {}

        if supplier:
            #תיקון קריטי: העברת מזהה חברה כמחרוזת נקייה לסנכרון הרמטי מול דיסק ה-Render
            trans = load_supplier_translated(supplier, language, company_id=str(safe_company_id)) or {}
            supplier_i18n = {
                "name": trans.get("name", supplier.supplier_name),
                "address": trans.get("address", supplier.address),
                "city": trans.get("city", supplier.city),
                "postal_code": trans.get("postal_code", supplier.postal_code),
                "notes": trans.get("notes", supplier.notes)
            }

        supplier_i18n_list = {}
        for s in all_suppliers:
            #תיקון קריטי זהה עבור לולאת כלל הספקים במסך
            trans = load_supplier_translated(s, language, company_id=str(safe_company_id)) or {}
            key = s.local_id if getattr(s, "local_id", None) is not None else s.id
            supplier_i18n_list[key] = {
                "name": trans.get("name", s.supplier_name),
                "address": trans.get("address", s.address),
                "city": trans.get("city", s.city),
                "postal_code": trans.get("postal_code", s.postal_code),
                "notes": trans.get("notes", s.notes)
            }

        company_obj = db.session.get(Company, safe_company_id)

        return render_template(
            'supplier.html',
            suppliers=search_results,
            all_suppliers=all_suppliers,
            supplier=supplier,
            supplier_i18n=supplier_i18n,
            supplier_i18n_list=supplier_i18n_list,
            today=today_str,
            input_date_val=input_date_val,
            company=load_company_translated(company_obj, language),
            company_db=company_obj
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error inside secure search_supplier execution: {e}")
        return redirect(url_for('supplier'))


# -----------------------------------------------------------
#  Clear Supplier Search (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/clear_search_results_supplier', methods=['POST'])
@login_required
def clear_search_results_supplier():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)

        all_suppliers = Supplier.query.filter_by(
            company_id=safe_company_id
        ).order_by(Supplier.supplier_name).all()

        supplier_i18n_list = {}

        for s in all_suppliers:
            try:
                #תיקון קריטי: העברת מזהה חברה כמחרוזת נקייה לסנכרון הרמטי מול דיסק ה-Render
                trans = load_supplier_translated(s, language, company_id=str(safe_company_id)) or {}
            except Exception:
                trans = {}

            key = s.local_id if getattr(s, "local_id", None) is not None else s.id
            supplier_i18n_list[key] = {
                "name": trans.get("name", s.supplier_name),
                "address": trans.get("address", s.address),
                "city": trans.get("city", s.city),
                "postal_code": trans.get("postal_code", s.postal_code),
                "notes": trans.get("notes", s.notes)
            }

        company_obj = db.session.get(Company, safe_company_id) if safe_company_id else None

        return render_template(
            'supplier.html',
            suppliers=[],                                 
            all_suppliers=all_suppliers,                  
            supplier=None,                                
            supplier_i18n={},                             
            supplier_i18n_list=supplier_i18n_list,        
            today=today_str,
            input_date_val=today_str,
            company=load_company_translated(company_obj, language),
            company_db=company_obj 
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error inside secure clear_search_results_supplier execution: {e}")
        return redirect(url_for('supplier')) # תיקון ה-Redirect לעמוד הספקים במקום עמוד החשבוניות


# ----------------------
# All Transaction Route (Multi-Tenant Secure)
# ----------------------

@app.route('/transactions')
@login_required
def transactions():
    try:
        language = get_lang()

        search = request.args.get("q", "").strip().lower()

        # FIX: month missing = first visit (current month); empty / "all" = the whole year
        today_dt = datetime.today()
        raw_month = request.args.get("month")
        if raw_month is None:
            selected_month = today_dt.strftime('%m')
            all_months = False
        elif raw_month.strip().lower() in ('', 'all', '0', '00'):
            selected_month = ''
            all_months = True
        elif raw_month.strip().isdigit() and 1 <= int(raw_month.strip()) <= 12:
            selected_month = raw_month.strip().zfill(2)
            all_months = False
        else:
            selected_month = today_dt.strftime('%m')
            all_months = False

        selected_year = (request.args.get("year", "") or "").strip() or str(today_dt.year)

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        safe_company_id = int(active_company_id)

        db.session.expire_all()

        all_transactions = (
            Transaction.query
            .filter_by(company_id=safe_company_id)
            .filter(
                Transaction.type.in_(['income', 'expense']),
                ~Transaction.description.in_([
                    "pending_credit_payment",
                    "pending_credit_payment_checkout",
                    "תשלום אשראי",
                    "sent_checkout",
                    "pending_credit_payment_checkout"
                ])
            )
            .order_by(Transaction.date.desc())
            .all()
        )
        today_str = datetime.today().strftime('%Y-%m-%d')

        business_categories = {}
        try:
            db_categories = Category.query.filter_by(company_id=safe_company_id).all()
            for cat in db_categories:
                data = load_category_file(cat)

                if data and "name" in data:
                    name_obj = data["name"]
                    translated_name = (
                        name_obj.get(language)
                        or name_obj.get("he")
                        or getattr(cat, 'name', 'Unknown')
                    )
                else:
                    translated_name = getattr(cat, 'name', 'Unknown')

                if getattr(cat, 'local_id', None) is not None:
                    business_categories[str(cat.local_id)] = translated_name
                if getattr(cat, 'id', None) is not None:
                    business_categories[str(cat.id)] = translated_name

        except Exception as e:
            print(f"⚠️ Warning: Safe database-driven category layout mapping bypassed: {e}")

        company_obj = db.session.get(Company, safe_company_id)
        translated_company = load_company_translated(company_obj, language) if company_obj else {}

        customer_i18n_list = {}
        if company_obj:
            self_name = f"★ {translated_company.get('name', company_obj.name)} "
            customer_i18n_list["0"] = {"name": self_name}

        all_customers = Customer.query.filter_by(company_id=safe_company_id).all()
        for c in all_customers:
            trans_c = load_customer_translated(c, language, company_id=safe_company_id) or {}
            customer_i18n_list[c.id] = {"name": trans_c.get("name") or c.customer_name or ""}
            if c.local_id is not None:
                customer_i18n_list[c.local_id] = {"name": trans_c.get("name") or c.customer_name or ""}

        filtered_transactions = []
        trans_i18n_list = {}
        costs_at_time = {}
        vat_amounts_list = {}

        # FIX: "1,234.50" is a number too
        search_num = search.replace(",", "")
        is_numeric_search = search_num.replace(".", "", 1).isdigit()
        search_float = float(search_num) if is_numeric_search else 0.0

        # FIX: t.amount is changed below for cancelled invoices; never let that reach the database
        with db.session.no_autoflush:
            for t in all_transactions:
                if not t.date:
                    continue

                trans_file = load_transaction_file(t)
                desc_obj = trans_file.get("description", {}) if trans_file else {}

                if desc_obj.get(language):
                    translated_desc = desc_obj.get(language)
                elif desc_obj.get("he"):
                    translated_desc = desc_obj.get("he")
                elif getattr(t, 'invoice_id', None) is not None:
                    try:
                        safe_invoice_num = int(t.invoice_id)
                    except (ValueError, TypeError):
                        safe_invoice_num = t.invoice_id

                    invoice_core = Invoice.query.filter_by(
                        invoice_number=safe_invoice_num,
                        company_id=safe_company_id
                    ).first()
                    if invoice_core:
                        translated_desc = f"חשבונית #{invoice_core.invoice_number}"
                    else:
                        translated_desc = f"חשבונית #{t.invoice_id}"
                else:
                    translated_desc = t.description or ""

                # payroll cost rows keep the real description of the employee
                if t.description and "עלות שכר מעביד" in str(t.description):
                    translated_desc = t.description

                t_month = t.date.strftime('%m')
                t_year = str(t.date.year)

                # FIX: all months = the whole selected year
                match_month = all_months or t_month == selected_month
                match_year = t_year == selected_year

                if not search:
                    match_search = True
                else:
                    raw_desc = (t.description or "").lower()
                    trans_desc_lower = translated_desc.lower()

                    cat_id_str = str(t.category_id) if t.category_id else None
                    if cat_id_str and cat_id_str in business_categories:
                        cat_name = business_categories[cat_id_str].lower()
                    else:
                        if t.description and "עלות שכר מעביד" in str(t.description):
                            cat_name = "שכר עבודה משכורות"
                        else:
                            cat_name = (t.category or "general").lower()

                    cust_mapped = customer_i18n_list.get(t.customer_id) or customer_i18n_list.get(str(t.customer_id)) or {}
                    cust_mapped_name = (cust_mapped.get("name") or "").lower()

                    amount_str = str(t.amount)
                    date_str = t.date.strftime("%d/%m/%Y")

                    if is_numeric_search:
                        # financial comparison to the cent (finds payroll amounts exactly)
                        match_search = abs(float(t.amount or 0.0) - search_float) < 0.005
                    else:
                        match_search = (
                            search in raw_desc
                            or search in trans_desc_lower
                            or search in cat_name
                            or search in amount_str
                            or search in date_str
                            or search in cust_mapped_name
                        )

                if match_month and match_year and match_search:
                    filtered_transactions.append(t)
                    trans_i18n_list[t.id] = translated_desc

                    current_amount = float(t.amount or 0.0)
                    current_cost_price = float(getattr(t, 'cost_price_at_time', 0.0) or 0.0)

                    if getattr(t, 'invoice_id', None) is not None:
                        try:
                            safe_inv_num = int(t.invoice_id)
                        except (ValueError, TypeError):
                            safe_inv_num = t.invoice_id

                        invoice_obj = Invoice.query.filter_by(
                            invoice_number=safe_inv_num,
                            company_id=safe_company_id
                        ).first()

                        if invoice_obj and invoice_obj.status == "canceled":
                            current_amount = 0.0
                            current_cost_price = 0.0
                            vat_amounts_list[t.id] = 0.0
                        elif invoice_obj and getattr(invoice_obj, 'vat_amount', None) is not None:
                            vat_amounts_list[t.id] = float(invoice_obj.vat_amount)
                        else:
                            vat_amounts_list[t.id] = float(getattr(t, 'vat_amount', 0.0) or 0.0)
                    else:
                        vat_amounts_list[t.id] = float(getattr(t, 'vat_amount', 0.0) or 0.0)

                    costs_at_time[t.id] = current_cost_price
                    t.amount = current_amount

        months_list = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        years_list = [str(y) for y in range(2024, 2031)]

        db.session.close()

        return render_template(
            'transactions.html',
            transactions=filtered_transactions,
            trans_i18n_list=trans_i18n_list,
            costs_at_time=costs_at_time,
            vat_amounts_list=vat_amounts_list,
            business_categories=business_categories,
            search=search,
            selected_month=selected_month,      
            all_months=all_months,
            selected_year=selected_year,
            months=months_list,
            years=years_list,
            today=today_str,
            company=translated_company,
            company_db=company_obj,
            customer_i18n_list=customer_i18n_list
        )

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return f"Error: {e}", 500

# -----------------------------------------------------------
# Add Transaction Form (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/transaction/add', methods=['POST'])
@login_required
def add_transaction():
    try:
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        active_company_id = int(active_company_id)          # numbers for queries
        company_str = str(active_company_id)                # text only for paths / translation

        date_str = request.form.get('date', '').strip()
        trans_date = None

        if date_str:
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d"):
                try:
                    trans_date = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
        if not trans_date:
            trans_date = datetime.today().date()

        raw_amount = clean_float(request.form.get('amount', '0'))
        raw_vat = clean_float(request.form.get('vat_amount', '0'))

        description = request.form.get('description', '')

        # FIX: type must be valid
        trans_type = (request.form.get('type') or '').strip()
        if trans_type not in ('income', 'expense'):
            flash('סוג תנועה לא תקין (הכנסה / הוצאה)', 'danger')
            return redirect(url_for('transactions'))

        raw_invoice_id = request.form.get('invoice_id')
        future_invoice_id = int(raw_invoice_id) if raw_invoice_id and str(raw_invoice_id).isdigit() else None

        # FIX: tell the user instead of silently ignoring
        if future_invoice_id:
            flash('תנועות הקשורות לחשבונית נוצרות אוטומטית ממערכת החשבוניות', 'warning')
            return redirect(url_for('transactions'))

        if trans_type == 'expense' and ("חשבונית" in description or "אשראי" in description or "סליקה" in description):
            flash('תנועה שמכילה "חשבונית", "אשראי" או "סליקה" נוצרת אוטומטית ולא ידנית', 'warning')
            return redirect(url_for('transactions'))

        raw_cat = request.form.get('category')
        category_id = int(raw_cat) if raw_cat and str(raw_cat).isdigit() else None

        cost_price = 0.0
        income_cat = 'service'

        raw_customer_id = request.form.get('customer_id')
        final_customer_id = "0" if str(raw_customer_id).strip() == "0" else (int(raw_customer_id) if raw_customer_id and str(raw_customer_id).isdigit() else None)

        if trans_type == 'income':
            p_id = (request.form.get('product_id') or '').strip()
            if p_id:
                if p_id in ['rent', 'stocks', 'dividend', 'unspecified']:
                    income_cat = p_id
                    cost_price = 0.0
                elif p_id.isdigit():                          # FIX: number lookups only for numbers
                    product = Product.query.filter_by(local_id=int(p_id), company_id=active_company_id).first()
                    if not product:
                        product = Product.query.filter_by(id=int(p_id), company_id=active_company_id).first()

                    if product:
                        item_file = load_item_file(product.id, company_id=active_company_id) or {}
                        income_cat = item_file.get("income_category", getattr(product, 'income_category', 'service'))

                        if income_cat == 'product':
                            cost_price = float(item_file.get("cost_price", product.cost_price or 0.0))

        # ---------------------------------------------------------------------------
        # ATTACHMENTS to the permanent disk of RENDER
        # ---------------------------------------------------------------------------
        relative_db_pointer = None
        if 'attachment' in request.files:
            file = request.files['attachment']
            if file and file.filename != '':
                absolute_save_target, relative_db_pointer = get_transaction_upload_path(file.filename, company_str)

                file.save(absolute_save_target)

                if relative_db_pointer and not relative_db_pointer.startswith("uploads/"):
                    clean_pointer = relative_db_pointer.lstrip("/")
                    if not clean_pointer.startswith("uploads/"):
                        relative_db_pointer = f"uploads/{clean_pointer}"
        # ---------------------------------------------------------------------------

        max_local = db.session.query(db.func.max(Transaction.local_id))\
            .filter(Transaction.company_id == active_company_id).scalar()

        next_local_id = (max_local or 0) + 1

        final_description = description
        if not future_invoice_id and "אשראי" in description:
            final_description = "pending_credit_payment_checkout"

        new_trans = Transaction(
            company_id=active_company_id,
            local_id=next_local_id,
            date=trans_date,
            description=final_description,
            amount=raw_amount,
            vat_amount=raw_vat,
            type=trans_type,
            category_id=category_id,
            invoice_id=future_invoice_id,
            attachment_path=relative_db_pointer,
            cost_price_at_time=cost_price,
            customer_id=str(final_customer_id) if final_customer_id is not None else None,
            quantity=1
        )

        if hasattr(new_trans, 'attachment'):
            new_trans.attachment = relative_db_pointer

        db.session.add(new_trans)
        db.session.commit()

        current_curr = get_currency() if 'get_currency' in globals() else 'ILS'
        try:
            translate_transaction_in_background(
                transaction_id=new_trans.id,
                company_id=company_str,
                description=final_description,
                amount=raw_amount,
                type_trans=trans_type,
                category_id=category_id,
                currency_code=current_curr,
                cost_price=cost_price,
                income_category=income_cat
            )
        except Exception:
            pass

        db.session.close()
        flash('התנועה נוספה בהצלחה!', 'success')

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash(f'שגיאה בשמירה: {e}', 'danger')

    return redirect(url_for('transactions'))

# -----------------------------------------------------------
#  Transactions List API (Multi-Tenant Secure JSON Endpoint)
# -----------------------------------------------------------
 
@app.route('/api/transactions_list', methods=['GET'])
@login_required
def transactions_list():
    try:
        language = get_lang()

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id
        
        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)
        
        db.session.expire_all()

        transactions = (
            Transaction.query
            .filter_by(company_id=safe_company_id)
            .filter(
                Transaction.type.in_(['income', 'expense']),
                ~Transaction.description.in_([
                    "pending_credit_payment", 
                    "pending_credit_payment_checkout", 
                    "תשלום אשראי", 
                    "sent_checkout"
                ])
            )
            .order_by(Transaction.date.desc())
            .all()
        )
        
        cat_map = {}
        try:
            db_categories = Category.query.filter_by(company_id=safe_company_id).all()
            for cat in db_categories:
                # תיקון קריטי: העברת אובייקט הקטגוריה השלם לפונקציית הטעינה המאובטחת
                data = load_category_file(cat)

                if data and "name" in data:
                    names = data.get("name", {})
                    translated_name = (
                        names.get(language) or 
                        names.get('he') or 
                        getattr(cat, 'name', 'Unknown') or 
                        f"Cat {getattr(cat, 'local_id', cat.id)}"
                    )
                else:
                    translated_name = getattr(cat, 'name', 'Unknown') or f"Cat {getattr(cat, 'local_id', cat.id)}"
                
                if getattr(cat, 'local_id', None) is not None:
                    cat_map[str(cat.local_id)] = translated_name
                if getattr(cat, 'id', None) is not None:
                    cat_map[str(cat.id)] = translated_name

        except Exception:
            pass

        company_obj = db.session.get(Company, safe_company_id)
        translated_company = load_company_translated(company_obj, language) if company_obj else {}
        
        customer_map = {}
        if company_obj:
            customer_map["0"] = f"★ {translated_company.get('name', company_obj.name)} "

        all_customers = Customer.query.filter_by(company_id=safe_company_id).all()
        for c in all_customers:
            trans_c = load_customer_translated(c, language, company_id=safe_company_id) or {}
            c_name = trans_c.get("name") or c.customer_name or f"Customer {c.local_id}"
            customer_map[str(c.id)] = c_name
            if c.local_id is not None:
                customer_map[str(c.local_id)] = c_name

        result = []

        for t in transactions:
            # שליפה מאובטחת ומסונכרנת מהדיסק הקבוע דרך אובייקט התנועה המלא
            trans_file = load_transaction_file(t)
            
            if trans_file and "description" in trans_file:
                desc_dict = trans_file["description"]
            else:
                desc_dict = {"he": t.description or ""}
            
            if isinstance(desc_dict, dict):
                if desc_dict.get(language):
                    p_desc = desc_dict.get(language)
                elif desc_dict.get("he"):
                    p_desc = desc_dict.get("he")
                elif getattr(t, 'invoice_id', None) is not None:
                    try:
                        safe_invoice_num = int(t.invoice_id)
                    except (ValueError, TypeError):
                        safe_invoice_num = t.invoice_id

                    invoice_core = Invoice.query.filter_by(
                        invoice_number=safe_invoice_num,
                        company_id=safe_company_id
                    ).first()
                    p_desc = f"חשבונית #{invoice_core.invoice_number}" if invoice_core else f"חשבונית #{t.invoice_id}"
                else:
                    p_desc = t.description or ""
            else:
                p_desc = t.description or ""
            
            # שדרוג ממוקד ל-API: אם זו הוצאת שכר מעביד, אנחנו מקבעים את התיאור האמיתי של העובד
            if t.description and "עלות שכר מעביד" in str(t.description):
                p_desc = t.description

            cat_id_str = str(t.category_id) if t.category_id else None
            
            if cat_id_str and cat_id_str in cat_map:
                translated_cat = cat_map[cat_id_str]
            else:
                # הפיקס המנצח: מספקים שם תצוגה מובנה לקטגוריית שכר עבודה ב-JSON כדי שהפרונט-אנד יציג אותה חלק
                if t.description and "עלות שכר מעביד" in str(t.description):
                    translated_cat = "שכר עבודה משכורות"
                else:
                    translated_cat = getattr(t, 'category', 'General') or 'General'
            
            current_amount = float(t.amount or 0.0)
            current_cost_price = float(getattr(t, 'cost_price_at_time', 0.0) or 0.0)
            vat_amount_val = getattr(t, 'vat_amount', 0.0)
            final_vat = float(vat_amount_val if vat_amount_val is not None else 0.0)

            if getattr(t, 'invoice_id', None) is not None:
                try:
                    safe_inv_num = int(t.invoice_id)
                except (ValueError, TypeError):
                    safe_inv_num = t.invoice_id

                invoice_obj = Invoice.query.filter_by(
                    invoice_number=safe_inv_num, 
                    company_id=safe_company_id
                ).first()
                
                if invoice_obj and invoice_obj.status == "canceled":
                    current_amount = 0.0      
                    final_vat = 0.0           
                    current_cost_price = 0.0  
                elif invoice_obj and getattr(invoice_obj, 'vat_amount', None) is not None:
                    final_vat = float(invoice_obj.vat_amount)

            t_cust_id_str = str(t.customer_id) if t.customer_id else None
            resolved_cust_name = customer_map.get(t_cust_id_str, "") if t_cust_id_str else ""

            # מוודאים שערך ה-attachment ב-JSON פונה תמיד עם קידומת /static/uploads/ תקינה
            raw_path = t.attachment_path or ""
            safe_attachment_url = ""
            if raw_path:
                # מנקים שאריות כפולות ומיישרים קו עם הראוטינג של הדף
                clean_path = raw_path.replace("static/", "").replace("uploads/", "").lstrip("/")
                safe_attachment_url = f"/static/uploads/{clean_path}"

            result.append({
                "id": t.id,
                "local_id": t.local_id,
                "date": t.date.strftime('%Y-%m-%d') if t.date else "",
                "description": p_desc,
                "amount": current_amount, 
                "vat_amount": final_vat,
                "type": t.type,
                "category_id": t.category_id,
                "category_display": translated_cat,
                # הזרקת הכתובת המתוקנת שמאלצת את הדפדפן לפתוח את התמונה במקום להוריד אותה!
                "attachment": safe_attachment_url,
                "invoice_id": t.invoice_id,
                "customer_id": t.customer_id,
                "customer_name": resolved_cust_name,
                "cost_price": current_cost_price  
            })
            
        db.session.close()
        return jsonify(result)

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------
#  N8N NEW FOR GREAT AUTO Transaction API Endpoint 
# -----------------------------------------------------------

@app.route('/api/transaction/add_from_n8n', methods=['POST'])
def api_add_transaction_from_n8n():
    try:
        # 1. security: the key must be set on the server AND match
        auth_header = request.headers.get("X-API-KEY") or ""
        expected_key = N8N_API_KEY or ""
        if not expected_key or not hmac.compare_digest(auth_header, expected_key):
            return jsonify({"error": "Unauthorized API Access"}), 401

        # 2. JSON extracted from the invoice by the AI in n8n
        data = request.get_json(silent=True) or {}

        if "amount" not in data:
            return jsonify({"error": "Amount is required"}), 400

        try:
            active_company_id = int(data.get("company_id") or OWNER_COMPANY_ID)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid company_id"}), 400

        # 3. date
        date_str = str(data.get("date", "")).strip()
        trans_date = None
        if date_str:
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    trans_date = datetime.strptime(date_str, fmt).date()
                    break
                except ValueError:
                    continue
        if not trans_date:
            trans_date = datetime.today().date()

        final_description = str(data.get("description") or "הוצאה אוטומטית מחשבונית מייל via n8n").strip()
        trans_type = 'expense'

        raw_invoice_id = data.get('invoice_id')
        future_invoice_id = int(raw_invoice_id) if raw_invoice_id and str(raw_invoice_id).isdigit() else None

        if future_invoice_id:
            return jsonify({"status": "ignored", "message": "Transaction ignored to prevent duplication with invoice system"}), 200

        if "חשבונית" in final_description or "אשראי" in final_description or "סליקה" in final_description:
            return jsonify({"status": "ignored", "message": "Transaction containing sensitive billing keywords ignored"}), 200

        # payroll rows are created only from the hours save
        if "עלות שכר מעביד" in final_description:
            return jsonify({"status": "ignored", "message": "Payroll cost rows are created by the hours save"}), 200

        # 4. numbers
        raw_amount = clean_float(data.get("amount", 0.0))
        raw_vat = clean_float(data.get("vat_amount", 0.0))
        cost_price = clean_float(data.get("cost_price", 0.0))

        if raw_amount <= 0:
            return jsonify({"error": "Amount must be greater than 0"}), 400

        # a retry from n8n must not create the same expense twice
        duplicate = Transaction.query.filter(
            Transaction.company_id == active_company_id,
            Transaction.date == trans_date,
            Transaction.type == trans_type,
            Transaction.description == final_description,
            db.func.round(Transaction.amount, 2) == round(raw_amount, 2)
        ).first()
        if duplicate:
            return jsonify({"status": "duplicate", "transaction_id": duplicate.id,
                            "message": "An identical transaction already exists"}), 200

        max_local = db.session.query(db.func.max(Transaction.local_id))\
            .filter(Transaction.company_id == active_company_id).scalar()
        next_local_id = (max_local or 0) + 1

        raw_cat = data.get("category_id")
        category_id = int(raw_cat) if raw_cat and str(raw_cat).isdigit() else None

        raw_customer_id = data.get("customer_id")
        final_customer_id = "0" if str(raw_customer_id).strip() == "0" else (int(raw_customer_id) if raw_customer_id and str(raw_customer_id).isdigit() else None)

        attachment_path = data.get("attachment_path", "") or ""
        if attachment_path and not attachment_path.startswith("http") and not attachment_path.startswith("uploads/"):
            clean_attach = attachment_path.lstrip("/")
            if not clean_attach.startswith("uploads/"):
                attachment_path = f"uploads/{clean_attach}"

        new_trans = Transaction(
            company_id=active_company_id,
            local_id=next_local_id,
            date=trans_date,
            description=final_description,
            amount=raw_amount,
            vat_amount=raw_vat,
            type=trans_type,
            category_id=category_id,
            invoice_id=future_invoice_id,
            attachment_path=attachment_path,
            cost_price_at_time=cost_price,
            customer_id=str(final_customer_id) if final_customer_id is not None else None,
            quantity=1
        )

        db.session.add(new_trans)
        db.session.commit()

        current_curr = get_currency() if 'get_currency' in globals() else 'ILS'
        try:
            translate_transaction_in_background(
                transaction_id=new_trans.id,
                company_id=str(active_company_id),
                description=final_description,
                amount=raw_amount,
                type_trans=trans_type,
                category_id=category_id,
                currency_code=current_curr,
                cost_price=cost_price,
                income_category='service'
            )
        except Exception as e:
            print(f"⚠️ n8n transaction flow - Translation trigger bypassed: {e}")

        transaction_id, local_id = new_trans.id, new_trans.local_id
        db.session.close()

        return jsonify({
            "status": "success",
            "message": "Transaction recorded successfully from n8n",
            "transaction_id": transaction_id,
            "local_id": local_id
        }), 201

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# -----------------------------------------------------------
#  Delete Transaction Endpoint (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/transaction/delete/<int:id>', methods=['POST'])
@login_required
def delete_transaction(id):
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

    # המרה בטוחה לטיפוסים התואמים ל-Postgres ברנדר
    safe_company_id = int(active_company_id)
    safe_trans_id = int(id)

    db.session.expire_all()

    trans = Transaction.query.filter_by(id=safe_trans_id, company_id=safe_company_id).first()
    
    if trans:
        # הגנה חשבונאית אטומית: חוסם מחיקה אך ורק אם התנועה היא פיזית תנועת הכנסה מסוג חשבונית השייכת לחברה!
        if getattr(trans, 'invoice_id', None) and trans.type == 'income' and "חשבונית" in (trans.description or ""):
            invoice_exists = Invoice.query.filter_by(
                invoice_number=trans.invoice_id, 
                company_id=safe_company_id
            ).first()
            
            if invoice_exists:
                flash('לא ניתן למחוק תנועה הקשורה לחשבונית. יש למחוק או לבטל את החשבונית עצמה.', 'danger')
                return redirect(request.referrer or url_for('transactions'))

        #  הפיקס הארכיטקטוני המנצח: מחיקה פיזית ומאובטחת של קובץ הנספח מתוך הדיסק הקבוע /data
        if trans.attachment_path:
            try:
                # שליפת נתיב בסיס הדיסק הרשמי מתוך ה-Config (מצביע ל-/data/uploads/transactions ברנדר)
                # אם ה-Config ריק, מבוצע פאלבק לנתיב הלינוקס הרשמי
                base_dir = app.config.get("TRANSACTIONS_DIR") or (PERSISTENT_BASE + "/uploads/transactions" if "RENDER" in os.environ else os.path.join(app.root_path, "static", "uploads", "transactions"))
                
                # מנקים את נתיב ה-DB מהקידומות המיותרות כדי לחלץ את שם הקובץ והתיקייה הנקיים
                clean_path = str(trans.attachment_path).replace("static/", "").replace("uploads/transactions/", "").replace(f"company_{safe_company_id}/", "").lstrip("/")
                
                # בניית הנתיב האבסולוטי האמיתי שבו הקובץ יושב על הדיסק הקבוע של החברה
                file_path = os.path.join(base_dir, f"company_{safe_company_id}", clean_path)
                file_path = os.path.normpath(file_path)
                
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    os.remove(file_path)
                    print(f"✔ File removed physically from permanent disk: {file_path}")
            except Exception as file_del_err:
                print(f"⚠️ Warning: Attachment physical file deletion bypassed: {file_del_err}")

        current_local_id = trans.local_id

        db.session.delete(trans)
        db.session.commit()

        #  הגנה מחמירה ומאובטחת על מחיקת תיקיית ה-JSON של התנועה בדיסק הקבוע
        if current_local_id:
            try:
                # שימוש בתשתית הרשמית שמחזירה את תיקיית התנועה
                trans_folder_path = ensure_transaction_folder(str(safe_company_id), str(current_local_id))

                # שומר על בידוד מוחלט (Sandbox): מוודאים שהנתיב מכיל את מזהה החברה ומזהה התנועה במפורש
                # זה מונע לצמיתות מצב שבו פקודת shutil.rmtree תמחק בטעות תיקיית שורש של חברה אחרת!
                if trans_folder_path and os.path.exists(trans_folder_path) and os.path.isdir(trans_folder_path):
                    if f"company_{safe_company_id}" in trans_folder_path and str(current_local_id) in trans_folder_path:
                        import shutil
                        shutil.rmtree(trans_folder_path)
                        print(f"✔ Transaction JSON folder cleaned from disk: {trans_folder_path}")
            except Exception as folder_del_err:
                print(f"⚠️ Warning: Transaction JSON disk directory cleanup bypassed: {folder_del_err}")
        
        db.session.close()
        flash('התנועה וכל הקבצים הקשורים אליה נמחקו בהצלחה', 'success')
    else:
        flash('התנועה לא נמצאה במערכת שלך', 'warning')
        
    return redirect(request.referrer or url_for('transactions'))


# -----------------------------------------------------------------------------
#  Main Categories Management View
# -----------------------------------------------------------------------------

@app.route('/categories', methods=['GET', 'POST'])
@login_required
def categories():
    try:
        language = get_lang()

        # קביעת מזהה החברה האקטיבית (Multi-Company Protection)
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id
        
        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)

        # שליפת כל הקטגוריות השייכות לחברה הנוכחית
        db_categories = Category.query.filter_by(company_id=safe_company_id).all()
        all_categories = []
        
        for cat in db_categories:
            #תיקון קריטי: מעבירים את אובייקט הקטגוריה השלם לפונקציה המאובטחת החדשה
            data = load_category_file(cat)

            if data and "name" in data:
                names_dict = data.get("name", {})
                translated_name = (
                    names_dict.get(language) or 
                    names_dict.get('he') or 
                    getattr(cat, 'name', 'Unknown') or "Unknown"
                )
            else:
                translated_name = getattr(cat, 'name', 'Unknown') or "Unknown"
                
            # החזרת שני המזהים (הגלובלי והמקומי) כדי למנוע נתונים שבורים ב-JS
            all_categories.append({
                "id": str(cat.id), 
                "local_id": getattr(cat, 'local_id', cat.id),
                "name": translated_name
            })

        # מיון אלפביתי של הקטגוריות לפי השם המתורגם
        all_categories.sort(key=lambda x: x['name'])

        company_obj = db.session.get(Company, safe_company_id)

        return render_template(
            'categories.html', 
            categories=all_categories, 
            language=language,
            company=load_company_translated(company_obj, language),
            company_db=company_obj 
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error encountered inside custom categories view router: {e}")
        return redirect(url_for('dashboard'))


@app.route('/category/add', methods=['POST'])
@login_required
def add_custom_category():
    category_name = request.form.get("new_category", "").strip()
    if not category_name:
        return redirect(url_for('categories'))

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

    # המרה בטוחה לטיפוס מספר שלם למניעת קריסות שאילתת ה-Max ב-Postgres
    safe_company_id = int(active_company_id)

    # חישוב ה-local_id הרץ הבא עבור קטגוריות החברה הנוכחית
    max_local = db.session.query(db.func.max(Category.local_id))\
        .filter(Category.company_id == safe_company_id).scalar()
    
    next_local_id = (max_local or 0) + 1

    # יצירת הקטגוריה החדשה בדאטהבייס
    new_cat = Category(
        name=category_name,
        company_id=safe_company_id,
        local_id=next_local_id 
    )
    db.session.add(new_cat)
    db.session.commit()

    #תיקון קריטי: יצירת התיקייה הפיזית עם המרת מחרוזת קשיחה לדיסק של Render
    try:
        ensure_category_folder(str(safe_company_id), str(next_local_id))
    except Exception as folder_err:
        print(f"⚠️ Non-critical category folder setup warning: {folder_err}")

    # הפעלת תהליך התרגום ברקע
    try:
        translate_category_in_background(
            cat_id=new_cat.id,
            company_id=str(safe_company_id),
            raw_name_text=category_name
        )
    except Exception as translate_err:
        print(f"⚠️ Non-critical background category translation trigger failed: {translate_err}")

    flash('הקטגוריה נוספה בהצלחה! תהליך התרגום רץ ברקע.', 'success')
    return redirect(url_for('categories'))


@app.route('/category/delete/<int:cat_id>', methods=['POST'])
@login_required
def delete_custom_category(cat_id):
    try:
        # קביעת מזהה החברה האקטיבית (Multi-Company Protection)
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוסים התואמים ל-Postgres ברנדר
        safe_company_id = int(active_company_id)
        safe_cat_id = int(cat_id)

        # שליפת הקטגוריה תוך וידוא קשיח שהיא שייכת לחברה הנוכחית
        cat = Category.query.filter_by(id=safe_cat_id, company_id=safe_company_id).first()
        
        if cat:
            current_local_id = cat.local_id

            # הגנה חשבונאית: ניתוק תנועות קיימות המשויכות לקטגוריה זו כדי למנוע קריסות של דפים ודוחות
            # אנחנו מעדכנים את ה-category_id ל-None (Null) עבור כל התנועות של החברה שקשורות לקטגוריה הנמחקת
            db.session.query(Transaction).filter(
                Transaction.category_id == cat.id,
                Transaction.company_id == safe_company_id
            ).update({Transaction.category_id: None}, synchronize_session=False)

            # מחיקת השורה של הקטגוריה מטבלת Category בדאטהבייס
            db.session.delete(cat)
            db.session.commit()

            #תיקון קריטי: מחיקה פיזית של תיקיית קבצי ה-JSON של הקטגוריה מהדיסק הקבוע של Render
            if current_local_id:
                try:
                    # שימוש בפונקציית העזר הרשמית שלנו הממירה אוטומטית ל-str
                    cat_path = ensure_category_folder(str(safe_company_id), str(current_local_id))
                    
                    if cat_path and os.path.exists(cat_path) and os.path.isdir(cat_path):
                        import shutil
                        shutil.rmtree(cat_path)
                        print(f"✔ Successfully purged translation folder for category local_id {current_local_id}")
                except Exception as e:
                    print(f"⚠️ Warning: Could not purge file system nodes for category folder: {e}")
                    
            flash('הקטגוריה נמחקה בהצלחה', 'success')
        else:
            flash('הקטגוריה המבוקשת אינה קיימת במערכת שלך', 'warning')
            
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error encountered during custom category deletion routine: {e}")
        flash('שגיאה בתהליך מחיקת הקטגוריה', 'danger')
        
    return redirect(url_for('categories'))


# -----------------------------------------------------------
#  Categories List API (Multi-Tenant Secure JSON Endpoint)
# -----------------------------------------------------------

@app.route('/api/categories_list')
@login_required
def categories_list():
    try:
        lang = get_lang()

        # קביעת מזהה החברה האקטיבית (Multi-Company Protection)
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id
        
        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)
        
        # שליפת כל הקטגוריות של החברה הנוכחית
        db_categories = Category.query.filter_by(company_id=safe_company_id).all()
        result = []

        for cat in db_categories:
            #תיקון קריטי: מעבירים את אובייקט הקטגוריה השלם לפונקציה המאובטחת החדשה שלנו
            data = load_category_file(cat)

            if data and "name" in data:
                names_dict = data.get("name", {})
                translated_name = (
                    names_dict.get(lang) or 
                    names_dict.get('he') or 
                    getattr(cat, 'name', 'Unknown') or "Unknown"
                )
            else:
                translated_name = getattr(cat, 'name', 'Unknown') or "Unknown"
                
            # החזרת מערך שלם המכיל את המזהה הגלובלי, המקומי והשם המתורגם
            result.append({
                "id": cat.id,          
                "local_id": getattr(cat, 'local_id', cat.id),
                "name": translated_name
            })
                    
        return jsonify(result)

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ API Categories List Fault Triggered: {e}")
        # במקרה של תקלה זמנית, מחזירים מערך ריק בסטטוס תקין כדי שה-JS בדף לא יישבר לחלוטין
        return jsonify([])
        

# -----------------------------------------------------------
#  Supplier Payment All Option Sync & Api Live Run (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/payment')
@login_required
def payment():
    try:
        language = get_lang()

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)

        all_suppliers = Supplier.query.filter_by(company_id=safe_company_id).order_by(Supplier.supplier_name).all()

        company_obj = db.session.get(Company, safe_company_id) if safe_company_id else None
        translated_company = load_company_translated(company_obj, language) if company_obj else {}

        return render_template(
            "payment.html",
            all_suppliers=all_suppliers,
            company=translated_company,
            company_db=company_obj 
        )
    except Exception as e:
        print(f"❌ Error in payment page view: {e}")
        return redirect(url_for('customer_dashboard_router'))


@app.route('/api/payment_page_submit', methods=['POST'])
@login_required
def payment_page_submit():
    try:
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)

        data = request.get_json() or {}

        supplier_data = data.get("supplier_payment", {})
        invoice_data = data.get("invoice_payment", {})
        authority_data = data.get("authority_payment", {})

        if supplier_data:
            req_supplier_id = supplier_data.get("supplier_id")
            try:
                safe_supplier_id = int(req_supplier_id)
            except (ValueError, TypeError):
                return jsonify({"status": "error", "message": "Invalid vendor profile token structure"}), 400
            
            checked_supplier = Supplier.query.filter_by(id=safe_supplier_id, company_id=safe_company_id).first()
            if not checked_supplier:
                return jsonify({"status": "error", "message": "Unauthorized or missing vendor profile token"}), 403

            save_supplier_payment(
                supplier_id=checked_supplier.id,
                supplier_name=supplier_data.get("supplier_name"),
                supplier_number=supplier_data.get("supplier_number"),
                amount=supplier_data.get("supplier_amount"),
                description=supplier_data.get("supplier_description"),
                reference=supplier_data.get("supplier_reference"),
                company_id=safe_company_id
            )

        if invoice_data:
            req_invoice_num = invoice_data.get("invoice_number")
            try:
                safe_invoice_num = int(req_invoice_num)
            except (ValueError, TypeError):
                safe_invoice_num = req_invoice_num # פאלבק למחרוזת אם מספר החשבונית הוא טקסטואלי
            
            checked_invoice = Invoice.query.filter_by(invoice_number=safe_invoice_num, company_id=safe_company_id).first()
            if not checked_invoice:
                return jsonify({"status": "error", "message": "Unauthorized or missing document sequence mapping reference"}), 403

            save_invoice_payment(
                invoice_number=checked_invoice.invoice_number,
                customer=invoice_data.get("invoice_customer"),
                amount=invoice_data.get("invoice_amount"),
                description=invoice_data.get("invoice_description"),
                company_id=safe_company_id
            )

        if authority_data:
            authority_data["company_id"] = safe_company_id
            save_authority_payment(authority_data)

        return jsonify({
            "status": "ok",
            "message": "Payment page submitted successfully"
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        print("❌ ERROR in payment_page_submit:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# -----------------------------------------------------------
#  Add Purchase & Api Live Run (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/api/purchase', methods=['POST'])
@login_required
def add_purchase():
    try:
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)

        data = request.json or {}

        # המרת מזהים בצורה בטוחה ל-int למניעת Data Type Mismatch
        try:
            supplier_id = int(data.get("supplier_id"))
            product_id = int(data.get("product_id"))
        except (ValueError, TypeError):
            return jsonify({"status": "error", "message": "Invalid parameter format for supplier or product tokens"}), 400

        quantity = float(data.get("quantity", 0) or 0)
        cost_price = float(data.get("cost_price", 0) or 0)
        reference = data.get("reference", "")
        notes = data.get("notes", "")
        date = datetime.today().strftime("%Y-%m-%d")   

        checked_supplier = Supplier.query.filter_by(id=supplier_id, company_id=safe_company_id).first()
        product = Product.query.filter_by(id=product_id, company_id=safe_company_id).first()

        if not checked_supplier or not product:
            return jsonify({"status": "error", "message": "Unauthorized or invalid inventory parameters selection"}), 403

        total = quantity * cost_price

        purchase = SupplierPurchase(
            company_id=safe_company_id,   
            supplier_id=checked_supplier.id,
            product_id=product.id,
            quantity=quantity,
            cost_price=cost_price,
            total=total,
            reference=reference,
            notes=notes,
            date=date
        )
        db.session.add(purchase)

        # עדכון שדות המלאי והעלות בבסיס הנתונים
        product.quantity += quantity
        product.cost_price = cost_price

        db.session.commit()

        #תיקון אטומי: סנכרון תנועת המלאי והאצוֹת לקובצי ה-JSON בדיסק הקבוע של Render
        try:
            # 1. רישום קובץ עסקת מלאי חדשה בתיקיית inventory_transactions של הפריט
            save_inventory_transaction(
                company_id=str(safe_company_id),
                local_id=str(product.local_id),
                received_date=date,
                stock_in=int(quantity),
                cost_price=cost_price,
                supplier_id=checked_supplier.id,
                supplier_name=checked_supplier.supplier_name
            )
            
            # 2. הרצת עדכון וסנכרון תרגום הפריט המלא ברקע (מעדכן את קובץ product.json)
            translate_product_in_background(
                product_id=product.id,
                company_id=str(safe_company_id),
                name=product.name,
                description=product.description,
                price=product.price,
                income_category=product.income_category,
                sku=product.sku,
                supplier_name=checked_supplier.supplier_name
            )
        except Exception as json_err:
            print(f"⚠️ Warning: Purchase flow database updated but JSON sync on persistent disk bypassed: {json_err}")

        db.session.close()
        return jsonify({"status": "success"})
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ API Add Purchase Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# -----------------------------------------------------------
#  Suppliers Dropdown API (Multi-Tenant Secure JSON Endpoint)
# -----------------------------------------------------------

@app.route('/api/suppliers_list')
@login_required
def suppliers_list():
    try:
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # המרה בטוחה לטיפוס מספר שלם למניעת קריסות SQL ב-Postgres ברנדר
        safe_company_id = int(active_company_id)

        # שליפת כל הספקים של החברה הנוכחית ממוינים אלפביתית
        suppliers = Supplier.query.filter_by(company_id=safe_company_id).order_by(Supplier.supplier_name).all()

        return jsonify([
            {
                "id": s.id, 
                "supplier_name": s.supplier_name
            }
            for s in suppliers
        ])
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ API Suppliers List Error: {e}")
        return jsonify({"error": str(e)}), 500



# ----------------------
# Profit And Loss (Multi-Tenant Secure Analytics Engine)
# ----------------------

@app.route('/profit')
@login_required
def profit():
    try:
        language = get_lang()
        search = request.args.get("q", "").strip().lower()

        # FIX: month missing = first visit (current month); empty / "all" = the whole year
        today_dt = datetime.today()
        raw_month = request.args.get("month")
        if raw_month is None:
            selected_month = today_dt.strftime('%m')
            all_months = False
        elif raw_month.strip().lower() in ('', 'all', '0', '00'):
            selected_month = ''
            all_months = True
        elif raw_month.strip().isdigit() and 1 <= int(raw_month.strip()) <= 12:
            selected_month = raw_month.strip().zfill(2)
            all_months = False
        else:
            selected_month = today_dt.strftime('%m')
            all_months = False

        selected_year = (request.args.get("year", "") or "").strip() or str(today_dt.year)

        # FIX: one helper for every date check below
        def in_period(d):
            if not d:
                return False
            if str(d.year) != selected_year:
                return False
            return all_months or d.strftime('%m') == selected_month

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None)

        if not active_company_id:
            return "<h1>Access Denied: Unverified Workspace</h1>", 401

        safe_company_id = int(active_company_id)

        db.session.expire_all()

        all_customers = Customer.query.filter_by(company_id=safe_company_id).options(
            db.joinedload(Customer.invoices).joinedload(Invoice.items)
        ).all()

        all_transactions = (
            Transaction.query
            .filter_by(company_id=safe_company_id)
            .filter(
                Transaction.type.in_(['income', 'expense']),
                ~Transaction.description.in_([
                    "pending_credit_payment",
                    "pending_credit_payment_checkout",
                    "sent_checkout"
                ])
            )
            .all()
        )

        all_products_dict = {}
        for p in Product.query.filter_by(company_id=safe_company_id).all():
            if p.local_id is not None:
                all_products_dict[str(p.local_id)] = p
            all_products_dict[str(p.id)] = p

        business_categories = {}
        try:
            db_categories = Category.query.filter_by(company_id=safe_company_id).all()
            for cat in db_categories:
                data = load_category_file(cat)
                if data and "name" in data:
                    name_obj = data["name"]
                    translated_name = (
                        name_obj.get(language) or
                        name_obj.get("he") or
                        getattr(cat, 'name', 'Unknown') or
                        f"Cat {getattr(cat, 'local_id', cat.id)}"
                    )
                else:
                    translated_name = getattr(cat, 'name', 'Unknown') or f"Cat {getattr(cat, 'local_id', cat.id)}"

                if getattr(cat, 'local_id', None) is not None:
                    business_categories[str(cat.local_id)] = translated_name
                if getattr(cat, 'id', None) is not None:
                    business_categories[str(cat.id)] = translated_name
        except Exception as e:
            print(f"⚠️ Warning: Safe database-driven category loading skipped in Profit: {e}")

        total_revenue = 0.0
        total_expenses = 0.0
        total_cogs = 0.0
        total_manual_income = 0.0

        total_invoice_vat = 0.0
        total_expense_vat = 0.0

        customer_totals = {}
        customer_i18n_list = {}
        product_i18n_list = {}
        filtered_customers = []

        manual_incomes_list = []
        expenses_list = []
        trans_i18n_list = {}

        company_obj = db.session.get(Company, safe_company_id)
        translated_company = load_company_translated(company_obj, language) if company_obj else {}

        # ------------------ PROCESS INVOICES ------------------
        for customer in all_customers:
            customer_i18n_list[customer.id] = load_customer_translated(customer, language, company_id=safe_company_id)
            cust_revenue = 0.0

            for inv in customer.invoices:
                if inv.status == "canceled":
                    continue

                # FIX: month check through the helper (all months = whole year)
                if in_period(inv.invoice_date):
                    cust_revenue += float(inv.sub_total or 0.0)
                    total_invoice_vat += float(inv.vat_amount or 0.0)

                    for item in inv.items:
                        prod_obj = all_products_dict.get(str(item.product_id)) if item.product_id else None

                        if prod_obj and str(item.product_id) not in product_i18n_list:
                            product_i18n_list[str(item.product_id)] = load_item_translated(prod_obj, language)

                        item_cost = float(getattr(item, 'cost_price_at_time', 0.0) or 0.0)

                        if item_cost == 0.0 and item.product_id:
                            if prod_obj and getattr(prod_obj, 'income_category', 'service') == 'product':
                                item_cost = float(prod_obj.cost_price or 0.0)

                        total_cogs += (item_cost * float(item.quantity or 0.0))

            trans_name = (customer_i18n_list[customer.id].get('name', '') or "").lower()
            match_search = not search or (search in customer.customer_name.lower() or search in trans_name)

            if match_search and (cust_revenue > 0.0 or not search):
                customer_totals[customer.id] = cust_revenue
                total_revenue += cust_revenue
                filtered_customers.append(customer)

        # self-report / internal passive income (customer id 0)
        self_invoices = Invoice.query.filter_by(company_id=safe_company_id, customer_id=0).all()
        self_revenue = 0.0

        for inv in self_invoices:
            if inv.status == "canceled":
                continue

            # FIX: same helper
            if in_period(inv.invoice_date):
                self_revenue += float(inv.sub_total or 0.0)
                total_invoice_vat += float(inv.vat_amount or 0.0)

                for item in inv.items:
                    total_cogs += 0.0

        if self_revenue > 0.0 or not search:
            self_title = f"★ {translated_company.get('name', company_obj.name if company_obj else '')} "
            customer_totals["0"] = self_revenue
            total_revenue += self_revenue
            customer_i18n_list["0"] = {"name": self_title}

            period_self_invoices = [inv for inv in self_invoices if in_period(inv.invoice_date)]   # FIX

            class VirtualSelfCustomer:
                id = "0"
                customer_name = self_title
                invoices = period_self_invoices

            if self_revenue > 0.0 or (not search and self_invoices):
                filtered_customers.append(VirtualSelfCustomer())

        # ============ item names for the HTML tables ============
        item_names_map = {}
        for customer in all_customers:
            for inv in customer.invoices:
                if inv.status == "canceled":
                    continue
                for item in inv.items:
                    prod_obj = None
                    if item.product_id and str(item.product_id).isdigit():
                        safe_item_pid = int(item.product_id)
                        prod_obj = Product.query.filter_by(local_id=safe_item_pid, company_id=safe_company_id).first()
                        if not prod_obj:
                            prod_obj = Product.query.filter_by(id=safe_item_pid, company_id=safe_company_id).first()

                    if prod_obj:
                        trans_p = load_item_translated(prod_obj, language) or {}
                        p_name_dict = trans_p.get('name') or {}
                        if isinstance(p_name_dict, dict):
                            item_names_map[item.id] = p_name_dict.get(language) or p_name_dict.get("he") or prod_obj.name
                        else:
                            item_names_map[item.id] = str(p_name_dict) if p_name_dict else prod_obj.name
                    else:
                        item_names_map[item.id] = item.description or "---"

        for inv in self_invoices:
            if inv.status == "canceled":
                continue
            for item in inv.items:
                item_names_map[item.id] = item.description or "---"

        # ------------------ PROCESS TRANSACTIONS ------------------
        # (includes the salary cost rows: type 'expense', dated the 1st of each month)
        for trans in all_transactions:
            # FIX: same helper
            if in_period(trans.date):
                t_file = load_transaction_file(trans)
                desc_obj = t_file.get("description", {}) if t_file else {}
                trans_i18n_list[trans.id] = (
                    desc_obj.get(language) or
                    desc_obj.get("he") or
                    trans.description
                )

                current_trans_vat = 0.0
                if getattr(trans, 'invoice_id', None) is not None:
                    try:
                        safe_invoice_num = int(trans.invoice_id)
                    except (ValueError, TypeError):
                        safe_invoice_num = trans.invoice_id

                    invoice_obj = Invoice.query.filter_by(invoice_number=safe_invoice_num, company_id=safe_company_id).first()
                    if invoice_obj and invoice_obj.status != "canceled" and getattr(invoice_obj, 'vat_amount', None) is not None:
                        current_trans_vat = float(invoice_obj.vat_amount)
                    else:
                        current_trans_vat = 0.0 if (invoice_obj and invoice_obj.status == "canceled") else float(getattr(trans, 'vat_amount', 0.0) or 0.0)
                else:
                    vat_val = getattr(trans, 'vat_amount', 0.0)
                    current_trans_vat = float(vat_val if vat_val is not None else 0.0)

                if trans.type == 'expense':
                    total_expenses += float(trans.amount or 0.0)
                    total_expense_vat += current_trans_vat
                    expenses_list.append(trans)

                elif trans.type == 'income':
                    if not trans.invoice_id:
                        total_manual_income += float(trans.amount or 0.0)
                        total_revenue += float(trans.amount or 0.0)
                        total_cogs += float(getattr(trans, 'cost_price_at_time', 0.0) or 0.0)
                        total_invoice_vat += current_trans_vat
                        manual_incomes_list.append(trans)

        net_profit = total_revenue - total_expenses - total_cogs
        total_vat_to_pay = total_invoice_vat - total_expense_vat

        months_list = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
        years_list = [str(y) for y in range(2024, 2031)]

        db.session.close()

        return render_template(
            'profit.html',
            all_customers=filtered_customers,
            customer_totals=customer_totals,
            total_revenue=total_revenue,
            total_expenses=total_expenses,
            total_manual_income=total_manual_income,
            manual_incomes_list=manual_incomes_list,
            expenses_list=expenses_list,
            trans_i18n_list=trans_i18n_list,
            business_categories=business_categories,
            total_cogs=total_cogs,
            net_profit=net_profit,
            total_invoice_vat=total_invoice_vat,
            total_expense_vat=total_expense_vat,
            total_vat_to_pay=total_vat_to_pay,
            customer_i18n_list=customer_i18n_list,
            product_i18n_list=product_i18n_list,
            item_names_map=item_names_map,
            selected_month=selected_month,      
            all_months=all_months,              
            selected_year=selected_year,
            search=search,
            months=months_list,
            years=years_list,
            language=language,
            company=translated_company,
            company_db=company_obj
        )

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return f"An internal monitoring error occurred: {str(e)}", 500




# ==================================================================
#  מנוע שעון נוכחות מולטי-חברה CSV Files (Multi-Tenant Employee Clock In/Out  Hours File)
# ==================================================================

def get_company_clock_paths(company_id, employee_id, year, month):
    try:
        base_dir = app.config.get("EMPLOYEES_DIR", "static/employees")
        month_str = str(month).zfill(2)
        year_str = str(year)
        
        final_emp_id = str(employee_id).strip()
        
        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(company_id) if company_id else None
        
        # הגנה מפני קריסות ValueError במקרה ומגיע מזהה טקסט חופשי, ובדיקה חסינת נפילות מול המודל החדש
        if employee_id and str(employee_id).strip().isdigit() and safe_company_id:
            emp_id_int = int(str(employee_id).strip())
            
            #  שאילתות מוגנות במספרים שלמים נקיים לעבודה חלקה בענן
            emp_row = EmployeeData.query.filter_by(id=emp_id_int, company_id=safe_company_id).first()
            if not emp_row:
                emp_row = EmployeeData.query.filter_by(local_id=emp_id_int, company_id=safe_company_id).first()
                
            if emp_row and emp_row.local_id:
                final_emp_id = str(emp_row.local_id) 

        # קיבוע נתיב מבודד לחלוטין בדיסק לכל מעסיק בנפרד (תואם לשיטת החשבוניות שלכם)
        target_folder = os.path.join(
            base_dir, 
            f"company_{safe_company_id}",
            f"employee_{final_emp_id}",  
            year_str, 
            month_str
        )
        
        json_path = os.path.join(target_folder, 'clock_hours_data.json')
        csv_path = os.path.join(target_folder, 'hours_report.csv')
        
        return target_folder, json_path, csv_path

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        # פאלבק בטוח למניעת תקיעת השרת
        fallback_folder = os.path.join(app.config.get("EMPLOYEES_DIR", "static/employees"), f"company_{company_id}", f"employee_{employee_id}", str(year), str(month).zfill(2))
        return fallback_folder, os.path.join(fallback_folder, 'clock_hours_data.json'), os.path.join(fallback_folder, 'hours_report.csv')


def load_clock_hours(company_id, employee_id, year, month):
    if not company_id or not employee_id:
        return {}
        
    company_folder, json_file_path, _ = get_company_clock_paths(company_id, employee_id, year, month)
    
    if not os.path.isfile(json_file_path): 
        return {}
        
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, Exception):
        return {}

def save_clock_hours(company_id, employee_id, year, month, data):
    try:
        company_folder, json_file_path, _ = get_company_clock_paths(company_id, employee_id, year, month)
        
        os.makedirs(company_folder, exist_ok=True)
        
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
                
        print(f"✔ Clock hours JSON locked safely at: {json_file_path}")
    except Exception as e:
        print(f"⚠️ Failed to save company clock hours JSON: {e}")


def entry_exists_in_csv(company_id, employee_id, year, month, month_key):
    empty_structure = {
        'employee_id': '',
        'employee_name': '',
        'month': '',
        'section': ''
    }
  
    if not company_id or not employee_id:
        return empty_structure

    try:
        _, _, csv_file_path = get_company_clock_paths(company_id, employee_id, year, month)
    except Exception:
        return empty_structure
  
    if not os.path.isfile(csv_file_path):
        return empty_structure

    try:
        with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            # תוקן: המרה בטוחה של המפתח לבדיקה חסינת טיפוסים (String Comparison Match)
            search_id = str(employee_id).strip()
            for row in reader:
                # בודק את כל הוואריאציות האפשריות של כותרת העמודה למניעת זיופי קריאה
                if str(row.get('employee_id', row.get('employeeId', ''))).strip() == search_id:
                    return row
    except Exception:
        pass

    return empty_structure

def timesheet_entry_exists(employee_id, selected_year, selected_month):
    try:
        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return False

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות SQL ב-PostgreSQL
        safe_active_company_id = int(active_company_id)

        # תוקן הרמטית: מציאת ה-ID המאובטח של העובד בטבלת EmployeeData למניעת הצלבות מידע בין חברות
        if str(employee_id).isdigit():
            #  המרת הפרמטרים ל-int נקי לעבודה חלקה בענן ללא קריסות טיפוסים
            emp_profile = EmployeeData.query.filter(
                (EmployeeData.local_id == int(employee_id)) | (EmployeeData.id == int(employee_id))
            ).filter_by(company_id=safe_active_company_id).first()
            final_db_id = emp_profile.id if emp_profile else int(employee_id)
        else:
            emp_profile = EmployeeData.query.filter_by(employee_id=str(employee_id), company_id=safe_active_company_id).first()
            final_db_id = emp_profile.id if emp_profile else employee_id

        month_filter = f"{selected_year}-{str(selected_month).zfill(2)}-%"
      
        # Render: הבטחה שגם מזהה ה-final_db_id יעבור כמספר שלם נקי לשאילתה
        try:
            final_db_id_int = int(final_db_id)
            return Timesheet.query.filter(
                Timesheet.company_id == safe_active_company_id,
                Timesheet.employee_id == final_db_id_int,
                Timesheet.date.like(month_filter)
            ).first() is not None
        except (ValueError, TypeError):
            return False

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        return False


def calculate_hours(start_time_str, end_time_str):
    if not start_time_str or not end_time_str:
        return 0.0
        
    start_str_clean = str(start_time_str).strip()
    end_str_clean = str(end_time_str).strip()
    
    if start_str_clean in ["00:00:00", "00:00", "None", ""] or end_str_clean in ["00:00:00", "00:00", "None", ""]:
        return 0.0

    def extract_time_part(t_str):
        t_clean = str(t_str).strip()
        
        if "T" in t_clean:
            try:
                t_clean = t_clean.split("T")[1]
            except:
                pass
                
        if " " in t_clean:
            t_clean = t_clean.split()[-1]
            
        if "-" in t_clean and ":" not in t_clean:
            return "00:00"
            
        return t_clean[:5]

    start_clean = extract_time_part(start_str_clean)
    end_clean = extract_time_part(end_str_clean)

    if start_clean == "00:00" or end_clean == "00:00":
        return 0.0

    fmt = "%H:%M"
    try:
        start = datetime.strptime(start_clean, fmt)
        end = datetime.strptime(end_clean, fmt)
        
        if end < start:
            diff = (end + timedelta(days=1)) - start
        else:
            diff = end - start
            
        return round(diff.total_seconds() / 3600, 2)
    except Exception:
        return 0.0

# ----------------------
# Employee Helper Get Clock In Out Days
# ----------------------

def get_clockinout_days(year, month, employee_id, company_id):
    num_days = calendar.monthrange(int(year), int(month))[1]
    clockinout_data = []

    try:
        # שליפת נתיבי קבצי ה-CSV לפי ה-ID המאובטח והחברה הפעילה
        _, _, csv_file_path = get_company_clock_paths(company_id, employee_id, year, month)
    except Exception as e:
        print(f"⚠ Failed to resolve company clock paths: {e}")
        csv_file_path = ""

    csv_hours_map = {}
    
    if csv_file_path and os.path.isfile(csv_file_path):
        try:
            with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_date_str = row.get('Date', '').strip() # פורמט YYYY-MM-DD
                    if row_date_str:
                        if row_date_str not in csv_hours_map:
                            csv_hours_map[row_date_str] = {'START': [], 'END': [], 'Task': '', 'Duration': '0.0'}
                        
                        row_type = row.get('Type', '').strip() # 'START' or 'END'
                        row_time = row.get('Time', '').strip() # 'HH:MM:SS'
                        
                        if row_time:
                            t_clean = row_time
                            if "T" in t_clean:
                                t_clean = t_clean.split("T")[-1]
                            if "-" in t_clean:
                                time_parts = t_clean.split()
                                t_clean = time_parts[-1] if time_parts else "00:00:00"
                            
                            if ":" in t_clean and "-" not in t_clean:
                                t_clean = t_clean.strip()
                                if row_type in ['START', 'END']:
                                    csv_hours_map[row_date_str][row_type].append(t_clean)
                        
                        if row.get('Task'):
                            csv_hours_map[row_date_str]['Task'] = row.get('Task')
                        if row.get('Duration') and row.get('Duration') != '0.0' and row.get('Duration') != '0' and row.get('Duration') != 0:
                            csv_hours_map[row_date_str]['Duration'] = row.get('Duration')
        except Exception as e:
            print(f"⚠ Error reading CSV inside get_clockinout_days: {e}")

    for day in range(1, num_days + 1):
        date_obj = datetime(int(year), int(month), day)
        formatted_date = date_obj.strftime('%Y-%m-%d') # משמש לשליפה מהמפה בדיסק
        day_index = date_obj.isoweekday() % 7

        # --- תוקן: המרת פורמט התאריך למבנה ישראלי DD/MM/YYYY בדיוק כמו שה-Front-end מצפה לקבל! ---
        frontend_date_format = date_obj.strftime('%d/%m/%Y')

        day_data = csv_hours_map.get(formatted_date, {'START': [], 'END': [], 'Task': '', 'Duration': '0.0'})
        
        start_time_raw = min(day_data['START']) if day_data['START'] else ''
        end_time_raw = max(day_data['END']) if day_data['END'] else ''

        start_time = "00:00"
        if start_time_raw and start_time_raw not in ["00:00:00", "00:00", "None", ""] and "-" not in str(start_time_raw):
            start_time = start_time_raw[:5]

        end_time = "00:00"
        if end_time_raw and end_time_raw not in ["00:00:00", "00:00", "None", ""] and "-" not in str(end_time_raw):
            end_time = end_time_raw[:5]

        total_hours = 0.0

        if start_time and end_time and start_time != "00:00" and end_time != "00:00":
            try:
                t_start = datetime.strptime(start_time, '%H:%M')
                t_end = datetime.strptime(end_time, '%H:%M')
                if t_end > t_start:
                    duration = t_end - t_start
                    total_hours = round(duration.total_seconds() / 3600.0, 2)
            except:
                pass
        
        if day_data['Duration'] != '0.0' and day_data['Duration'] != '0' and day_data['Duration'] != 0:
            try:
                total_hours = float(day_data['Duration'])
            except:
                pass

        clockinout_data.append({
            "day_index": day_index,
            "date": frontend_date_format,  # תוקן: מחזיר DD/MM/YYYY חסין שבירות ב-JS
            "start_time": start_time,   
            "end_time": end_time,       
            "totalHours": str(total_hours), 
            "task": day_data['Task']    
        })

    return clockinout_data


# ----------------------
# API: Read Employee ID Current User id 
# ----------------------

@app.route('/api/current_user_info', methods=['GET'])
@login_required
def current_user_info():
    try:
        raw_role = getattr(current_user, 'role', 'employee') or 'employee'
        clean_role = str(raw_role).strip().lower()

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות) -----------------
        if session.get('owner_access') or clean_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            db.session.close()
            return jsonify({"error": "No active company found"}), 401

        # ברירת מחדל לזיהוי המשתמש
        resolved_employee_id = session.get("employee_id") or current_user.id
        final_frontend_id = resolved_employee_id

        # תוקן: שליפת הפרופיל האמיתי של העובד כדי להחזיר את ה-local_id שלו ל-Front-end
        if clean_role == 'employee':
            # אם זה עובד רגיל, המזהה שלו בדאטהבייס הוא current_user.id (מקושר ל-User.id)
            # או שאנחנו מחפשים לפי ה-email שלו בטבלת EmployeeData החדשה
            employee_profile = EmployeeData.query.filter(
                (EmployeeData.id == current_user.id) | (EmployeeData.email == current_user.email)
            ).filter_by(company_id=active_company_id).first()
            
            if employee_profile and employee_profile.local_id:
                # ה-JS מצפה לקבל את ה-local_id (המספר הרץ של החברה 1, 2, 3...)
                final_frontend_id = employee_profile.local_id
        else:
            # אם זה מנהל/אדמין, נבדוק אם נבחר עובד ספציפי בקומבו-בוקס ונשמר בסשן
            if str(resolved_employee_id).isdigit():
                employee_profile = EmployeeData.query.filter(
                    (EmployeeData.local_id == int(resolved_employee_id)) | (EmployeeData.id == int(resolved_employee_id))
                ).filter_by(company_id=active_company_id).first()
                if employee_profile and employee_profile.local_id:
                    final_frontend_id = employee_profile.local_id

        db.session.close() 

        # החזרת המידע המיושר והמאובטח ביותר ל-DOM של השעון וה-Timesheet
        return jsonify({
            "employee_id": final_frontend_id, 
            "role": clean_role,
            "company_id": active_company_id
        }), 200
        
    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Error inside current_user_info API node: {e}")
        return jsonify({"error": str(e)}), 500


# ----------------------
# Hours File Load Handling
# ----------------------

def load_hours(company_id, local_id):
    if not company_id or not local_id:
        return {}
    base_dir = app.config.get("EMPLOYEES_DIR") or EMPLOYEES_DIR
    folder = os.path.join(base_dir, f"company_{company_id}", str(local_id))
    file_path = os.path.join(folder, "hours.json")
    if not os.path.exists(file_path):
        return {}
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_hours(company_id, local_id, data):
    if not company_id or not local_id:
        return
    base_dir = app.config.get("EMPLOYEES_DIR") or EMPLOYEES_DIR
    folder = os.path.join(base_dir, f"company_{company_id}", str(local_id))
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "hours.json")
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ----------------------
# Check If Employee ID Months Years Exists On Data   
# ----------------------

def get_form_data_from_csv(company_id, employee_id, year, month, month_key):
    base_dir = app.config.get("EMPLOYEES_DIR", "static/employees")
    folder = os.path.join(base_dir, f"company_{company_id}", str(employee_id))
    csv_path = os.path.join(folder, "hours_data.csv")

    if not os.path.exists(csv_path):
        return {}

    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('month') == month_key:
                    return row
    except Exception:
        pass

    return {
        'employee_id': '', 'employee_name': '', 'month': '', 'section': '',
        'total_salary_pension_funds': '', 'amount_tax_credit_points_monthly': '',
        'income_tax_before_credit': '', 'final_city_tax_benefit': '',
        'monthly_city_tax_tops': '', 'date_of_birth': '', 'basic_salary': '',
        'city_value_percentage': '', 'additional_payments': '', 'above_ceiling_value': '',
        'above_ceiling_fund': '', 'above_ceiling_compensation': '', 'tax_level_precente': '',
        'tax_credit_points': '', 'net_value': '', 'gross_salary': '', 'gross_taxable': '',
        'pension_fund': '', 'compensation': '', 'study_fund': '', 'disability': '',
        'miscellaneous': '', 'national_insurance': '', 'salary_tax': '',
        'total_employer_contributions': '', 'employee_pension_fund': '',
        'self_employed_pension_fund': '', 'study_fund_deductions': '',
        'miscellaneous_deductions': '', 'national_insurance_deductions': '',
        'health_insurance_deductions': '', 'income_tax': '', 'advance_payment_salary': '',
        'total_deductions': '', 'total_salary_cost': '', 'total_missing_hours': '',
        'total_work_days': '', 'totals_lunch_value': '', 'net_payment': '', 'cars_value': '',
        'final_extra_hours_weekend': '', 'final_extra_hours_regular': '',
        'food_break_unpaid_salary': '', 'hours125_regular_salary': '',
        'hours150_regular_salary': '', 'hours150_holidays_saturday_salary': '',
        'hours175_holidays_saturday_salary': '', 'hours200_holidays_saturday_salary': '',
        'sick_days_salary': '', 'sick_days_entitlement': '', 'vacation_days_salary': '',
        'vacation_days_entitlement': '', 'sick_days_salary_yearly': '',
        'vacation_days_salary_yearly': '', 'sick_days_balance_yearly': '',
        'vacation_balance_yearly': '', 'gross_taxable_yearly': '',
        'employee_pension_fund_yearly': '', 'self_employed_pension_fund_yearly': '',
        'study_fund_deductions_yearly': '', 'miscellaneous_deductions_yearly': '',
        'national_insurance_deductions_yearly': '', 'health_insurance_deductions_yearly': '',
        'income_tax_yearly': '', 'amount_tax_credit_points_monthly_yearly': '',
        'final_city_tax_benefit_yearly': '', 'pension_fund_yearly': '',
        'compensation_yearly': '', 'study_fund_yearly': '', 'disability_yearly': '',
        'miscellaneous_yearly': '', 'national_insurance_yearly': '', 'salary_tax_yearly': '',
        'total_employer_contributions_yearly': '', 'total_salary_cost_yearly': ''
    }


# ----------------------
#  Clear and Build Employee_details
# ----------------------

NUMERIC_ZERO_FIELDS = [
    "hourly_rate", "total_work_days", "totals_lunch_value", "total_missing_hours",
    "mobile_value", "clothing_value", "lunch_value", "cars_value", "advance_payment_salary",
    "monthly_city_tax_tops", "city_value_percentage", "final_city_tax_benefit",
    "hours150_holidays_saturday_salary", "hours175_holidays_saturday_salary",
    "hours200_holidays_saturday_salary", "tax_point_child"
]

# PROFILE fields = never wiped (listed only for reference, the code below wipes by MONTH_RESULT_FIELDS):
# address, city, postal_code, phone, id_number, date_of_birth, start_date, hourly_rate,
# thirteenth_salary, lunch_value, mobile_value, clothing_value, work_percent, contract_status,
# tax_point_child, monthly_city_tax_tops, tax_credit_points, city_value_percentage, cars_value,
# sick_days_entitlement, vacation_days_entitlement

# Calculated MONTHLY results = wiped when the selected month has no saved data
MONTH_RESULT_FIELDS = {
    # hours / days
    "sick_days_salary", "vacation_days_salary",
    "final_extra_hours_regular", "final_extra_hours_weekend",
    "total_work_days", "total_missing_hours",
    # salary table
    "basic_salary", "totals_lunch_value", "additional_payments",
    "net_value", "gross_salary",
    "above_ceiling_value", "above_ceiling_fund", "above_ceiling_compensation",
    "gross_taxable",
    "food_break_unpaid_salary",
    "hours125_regular_salary", "hours150_regular_salary",
    "hours150_holidays_saturday_salary", "hours175_holidays_saturday_salary",
    "hours200_holidays_saturday_salary",
    "advance_payment_salary",
    # employer contributions
    "pension_fund", "compensation", "study_fund", "disability", "miscellaneous",
    "national_insurance", "salary_tax", "total_employer_contributions",
    "total_salary_cost", "total_salary_pension_funds",
    # employee deductions / tax table
    "employee_pension_fund", "self_employed_pension_fund",
    "study_fund_deductions", "miscellaneous_deductions",
    "national_insurance_deductions", "health_insurance_deductions",
    "income_tax", "income_tax_before_credit", "tax_level_precente",
    "amount_tax_credit_points_monthly", "final_city_tax_benefit",
    "total_deductions", "net_payment",
}

def is_month_result_field(key):
    k = str(key)
    return (k in MONTH_RESULT_FIELDS
            or k.endswith('_yearly')     # all yearly totals and balances
            or k.endswith('_monthly')    # monthly totals row of the hours table
            or k.endswith('_paid')       # paid totals row of the hours table
            or k == 'food_break_unpaid'
            or k == 'final_total_extra_hours_weekend_monthly')




def build_employee_details(company_id, employee_id, month, year):
    """Plain dict. Returns None if the employee is not found in this company."""
    try:
        emp_id = int(employee_id)
    except (TypeError, ValueError):
        return None

    date_key = f"{month}/{year}"
    month_key = f"{year}-{month}"

    month_row = EmployeeData.query.filter_by(
        company_id=company_id, local_id=emp_id, date=date_key).first()
    employee = month_row or EmployeeData.query.filter_by(
        company_id=company_id, local_id=emp_id).order_by(EmployeeData.id.desc()).first()
    if not employee:
        return None

    data = {col: getattr(employee, col) for col in EmployeeData.__table__.columns.keys()}

    # latest row is ANOTHER month -> keep the profile, blank only the calculated results
    if not month_row:
        for k in list(data.keys()):
            if is_month_result_field(k):
                data[k] = 0 if k in NUMERIC_ZERO_FIELDS else ""

    all_hours = load_hours(company_id, emp_id) or {}
    data["hours_table"] = (all_hours.get(month_key) or {}).get("hours_table", {}) or {}
    data["existing_record_found"] = True
    return data


# ----------------------
#  Shared helpers Index: Form Page(module level)
# ----------------------

def get_active_company_id():
    user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
    if session.get('owner_access') or user_role == 'owner':
        cid = OWNER_COMPANY_ID
    else:
        cid = getattr(current_user, 'company_id', None) or session.get('company_id')
    return cid or 1


def resolve_employee(company_id, ident, date_key=None):
    """local_id first (monthly row if date_key), then latest profile row, then PK."""
    try:
        ident = int(ident)
    except (TypeError, ValueError):
        return None

    emp = None
    if date_key:
        emp = EmployeeData.query.filter_by(
            company_id=company_id, local_id=ident, date=date_key).first()
    if not emp:
        emp = EmployeeData.query.filter_by(
            company_id=company_id, local_id=ident
        ).order_by(EmployeeData.id.desc()).first()
    if not emp:
        emp = EmployeeData.query.filter_by(company_id=company_id, id=ident).first()
    return emp


# ----------------------
# Protect Main Page , Index: Form Page
# ----------------------

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    db.create_all()

    if session.get('role') == 'employee' or getattr(current_user, 'role', '').lower() == 'employee':
        return redirect(url_for('employee_dashboard'))

    active_company_id = get_active_company_id()

    months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני',
              'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
    years = list(range(2020, 2041))

    employees = EmployeeData.query.filter_by(company_id=active_company_id) \
        .order_by(EmployeeData.employee_name).all()

    today = datetime.today()
    default_month = f"{today.month:02d}"
    default_year = str(today.year)

    session.setdefault('selected_employee_id', '')
    session.setdefault('owner_employee_id', '')
    session.setdefault('selected_month', default_month)
    session.setdefault('selected_year', default_year)

    # ===========================
    # POST: save_all_data (unchanged)
    # ===========================
    if request.method == 'POST' and request.form.get('form_type') == 'save_all_data':
        try:
            hours_table_json = request.form.get('hours_table_data')
            if not hours_table_json:
                flash("שגיאה: נתוני השעות לא התקבלו", "danger")
                return redirect(url_for('index'))

            payload = json.loads(hours_table_json)

            employee_id = str(payload.get('employee_id', '')).strip()
            employee_name = (payload.get('employee_name') or '').strip()
            month = str(payload.get('month', default_month)).zfill(2)
            year = str(payload.get('year', default_year)).strip()

            hours_table = payload.get('hours_table', {}) or {}
            work_day_entries = hours_table.get('work_day_entries', []) or []
            monthly_totals = hours_table.get('monthly_totals', {}) or {}
            paid_totals = hours_table.get('paid_totals', {}) or {}
            tax_data = hours_table.get('tax', {}) or {}

            if not employee_id or not month or not year:
                flash("נא לבחור עובד, חודש ושנה", "warning")
                return redirect(url_for('index'))

            employee = resolve_employee(active_company_id, employee_id)
            if not employee:
                flash("העובד לא נמצא בחברה זו", "danger")
                return redirect(url_for('index'))

            employee_local_id = employee.local_id
            final_name = employee_name or employee.employee_name or ""
            date_key = f"{month}/{year}"
            month_key = f"{year}-{month}"

            all_hours = load_hours(active_company_id, employee_local_id) or {}

            try:
                yearly_totals = compute_yearly_totals(
                    all_hours=all_hours,
                    employee_id=str(employee_local_id),
                    year=year,
                    current_month=month,
                    current_tax=tax_data
                ) or {}
            except Exception as yearly_err:
                print(f"compute_yearly_totals failed: {yearly_err}")
                yearly_totals = {}
            tax_data.update(yearly_totals)

            all_hours["employee_name"] = final_name
            all_hours[month_key] = {
                "hours_table": {
                    "work_day_entries": work_day_entries,
                    "monthly_totals": monthly_totals,
                    "paid_totals": paid_totals,
                    "tax": tax_data
                }
            }
            save_hours(active_company_id, employee_local_id, all_hours)

            db_payload = {
                'company_id': active_company_id,
                'local_id': employee_local_id,
                'employee_id': employee_local_id,
                'employee_name': final_name,
                'employeeMonth': month,
                'employeeYear': year,
                'date': date_key
            }
            db_payload.update(monthly_totals)
            db_payload.update(paid_totals)
            db_payload.update(tax_data)
            db_payload['employee_name'] = final_name

            db_columns = set(EmployeeData.__table__.columns.keys())
            valid_db_data = {k: v for k, v in db_payload.items() if k in db_columns}

            existing = EmployeeData.query.filter_by(
                company_id=active_company_id,
                local_id=employee_local_id,
                date=date_key
            ).first()

            if existing:
                for key, val in valid_db_data.items():
                    setattr(existing, key, val)
            else:
                db.session.add(EmployeeData(**valid_db_data))

            db.session.commit()

            session['selected_employee_id'] = str(employee_local_id)
            session['owner_employee_id'] = str(employee_local_id)
            session['selected_month'] = month
            session['selected_year'] = year
            session['month_result'] = date_key

            flash("הנתונים נשמרו בהצלחה!", "success")
            return redirect(url_for('index', employee_id=employee_local_id,
                                    month=month, year=year))

        except Exception as e:
            db.session.rollback()
            import traceback
            traceback.print_exc()
            flash(f"שגיאה בעת השמירה: {str(e)}", "danger")
            return redirect(url_for('index'))

    # ===========================
    # GET: load form + hours
    # ===========================
    if session.pop('force_clear', False):
        selected_employee_id = ''
        selected_month = default_month
        selected_year = default_year
    else:
        selected_employee_id = (request.args.get('employee_id')
                                or session.get('owner_employee_id', '')
                                or session.get('selected_employee_id', ''))
        selected_employee_id = str(selected_employee_id or '').strip()
        selected_month = str(request.args.get('month') or session.get('selected_month', default_month)).zfill(2)
        selected_year = str(request.args.get('year') or session.get('selected_year', default_year))

    session['owner_employee_id'] = selected_employee_id
    session['selected_employee_id'] = selected_employee_id
    session['selected_month'] = selected_month
    session['selected_year'] = selected_year

    date_key = f"{selected_month}/{selected_year}"
    month_key = f"{selected_year}-{selected_month}"

    form_data = {}

    try:
        csv_form = entry_exists_in_csv(selected_employee_id, selected_year,
                                       selected_month, month_key, active_company_id) or {}
        if not isinstance(csv_form, dict):
            csv_form = {}
    except Exception as csv_err:
        print(f"entry_exists_in_csv failed: {csv_err}")
        csv_form = {}
    form_data.update(csv_form)

    employee = None
    selected_employee_name = ""
    if selected_employee_id.isdigit():
        employee = resolve_employee(active_company_id, selected_employee_id, date_key)

    all_hours = {}
    if employee:
        selected_employee_name = employee.employee_name or ""
        all_hours = load_hours(active_company_id, employee.local_id) or {}

        form_data['employee_id'] = employee.local_id
        form_data['month_result'] = date_key
        for column in EmployeeData.__table__.columns.keys():
            if column in ('id', 'company_id'):
                continue
            val = getattr(employee, column, None)
            if val is not None:
                form_data[column] = val

    selected_employee_name = (selected_employee_name
                              or all_hours.get('employee_name')
                              or form_data.get('employee_name')
                              or "")

    hours_table = (all_hours.get(month_key, {}) or {}).get('hours_table', {}) or {}
    for key, default_value in {'work_day_entries': [], 'monthly_totals': {},
                               'paid_totals': {}, 'tax': {}}.items():
        hours_table.setdefault(key, default_value)

    for section in ('monthly_totals', 'paid_totals', 'tax'):
        section_data = hours_table.get(section, {})
        if isinstance(section_data, dict):
            for k, v in section_data.items():
                if k != 'employee_name' and v not in (None, ''):
                    form_data[k] = v

    # ---- does the SELECTED month have saved data? ----
    month_row = None
    if employee:
        month_row = EmployeeData.query.filter_by(
            company_id=active_company_id,
            local_id=employee.local_id,
            date=date_key
        ).first()

    saved_month = (all_hours.get(month_key) or {}).get('hours_table') or {}
    has_month_data = bool(month_row or saved_month.get('tax') or saved_month.get('work_day_entries'))

    # ---- no data for this month: blank ONLY the known calculated results ----
    if employee and not has_month_data:
        wiped = []
        for k in list(form_data.keys()):
            if is_month_result_field(k):
                form_data[k] = 0 if k in NUMERIC_ZERO_FIELDS else ""
                wiped.append(k)
        form_data['month_result'] = ''

        # keys saved in hours.json (other months) that were NOT wiped: candidates if old data stays
        candidates = set()
        for mdata in all_hours.values():
            if isinstance(mdata, dict):
                ht = mdata.get('hours_table') or {}
                for sec in ('monthly_totals', 'paid_totals', 'tax'):
                    candidates.update((ht.get(sec) or {}).keys())
        not_wiped = sorted(k for k in candidates if k in form_data and k not in wiped)
        print("WIPED", date_key, "->", sorted(wiped))
        print("NOT WIPED (check if any shows old data) ->", not_wiped)

    for k, v in list(form_data.items()):
        if v is None:
            form_data[k] = 0 if k in NUMERIC_ZERO_FIELDS else ""

    form_data['employee_name'] = selected_employee_name

    employee_details = build_employee_details(
        active_company_id, selected_employee_id, selected_month, selected_year) or {}

    company_obj = db.session.get(Company, active_company_id) if active_company_id else None

    return render_template(
        'index.html',
        form_data=form_data,
        hours_table=hours_table,
        employee_data=form_data,
        selected_employee_id=selected_employee_id,
        selected_employee_name=selected_employee_name,
        employee_name=selected_employee_name,
        employeeMonth=selected_month,
        employeeYear=selected_year,
        month_result=date_key if has_month_data else '',
        months=months,
        years=years,
        employees=employees,
        days_data=get_days_in_month(int(selected_year), int(selected_month)),
        hours=get_hours_data(),
        employee_details=employee_details,
        all_hours=json.dumps(all_hours, ensure_ascii=False),
        company_db=company_obj,
        today=today.strftime('%Y-%m-%d')
    )
    
# ----------------------
#  Helper Yearly Totals Data 
# ----------------------

def compute_yearly_totals(all_hours, employee_id, year, current_month, current_tax):

    yearly_fields = [
        "sick_days_salary",
        "vacation_days_salary",
        "gross_taxable",
        "employee_pension_fund",
        "self_employed_pension_fund",
        "study_fund_deductions",
        "miscellaneous_deductions",
        "national_insurance_deductions",
        "health_insurance_deductions",
        "income_tax",
        "amount_tax_credit_points_monthly",
        "final_city_tax_benefit",
        "pension_fund",
        "compensation",
        "study_fund",
        "disability",
        "miscellaneous",
        "national_insurance",
        "salary_tax",
        "total_employer_contributions",
        "total_salary_cost"
    ]

    # Yearly entitlements
    sick_days_entitlement = 18
    vacation_days_entitlement = 12

    totals = {f"{field}_yearly": 0.0 for field in yearly_fields}
    
    # תוקן הרמטית: קובץ ה-hours.json הוא כבר המילון השטוח והישיר של החודשים של העובד!
    employee_data = all_hours if isinstance(all_hours, dict) else {}

    # Sum previous months
    for month_key, month_data in employee_data.items():
        if month_key == "employee_name":
            continue
        if not month_key.startswith(str(year)):
            continue

        try:
            # תוקן: פירוק תקני ומדויק של מפתח החודש (למשל "2026-09" לחודש 9) למניעת קריסות זיכרון
            mk_month = int(month_key.split("-")[1])
        except:
            continue

        if mk_month >= int(current_month):
            continue

        tax = month_data.get("hours_table", {}).get("tax", {})

        for field in yearly_fields:
            val = clean_number(tax.get(field, 0))
            totals[f"{field}_yearly"] += val

    # Add current month
    for field in yearly_fields:
        val = clean_number(current_tax.get(field, 0))
        totals[f"{field}_yearly"] += val

    # Balances
    sick_days_used = totals.get("sick_days_salary_yearly", 0.0)
    vacation_days_used = totals.get("vacation_days_salary_yearly", 0.0)

    totals["sick_days_balance_yearly"] = sick_days_entitlement - sick_days_used
    totals["vacation_balance_yearly"] = vacation_days_entitlement - vacation_days_used

    # Format
    def format_number(val):
        return f"{val:.2f}"

    def format_currency(val):
        return f"{val:,.2f}"

    formatted_totals = {}
    for k, v in totals.items():
        if "balance" in k or "days" in k:
            formatted_totals[k] = format_number(v)
        else:
            formatted_totals[k] = format_currency(v)

    return formatted_totals

# ----------------------
#  HELPER SAVE SALARY TO TRANSACTION ROW REPORT 
# ----------------------

SALARY_DESC_PREFIX = "עלות שכר מעביד - "

def sync_salary_transaction(company_id, employee, employee_name, month, year, tax_data):
    """Amount = the payslip's own עלות השכר box (total_salary_cost). No separate formula.
    Commits by itself, never raises. CALL IT AFTER the main data was committed.
    Returns (ok, message)."""
    from sqlalchemy.exc import SQLAlchemyError
    try:
        company_id = int(company_id)
        tax_data = {str(k).replace("-", "_"): v for k, v in (tax_data or {}).items()}

        # the ready result from the payslip box
        total_cost = clean_number(tax_data.get("total_salary_cost", 0.0))

        if total_cost <= 0:
            # gross salary exists but the cost box is empty = worth showing
            if clean_number(tax_data.get("gross_salary", 0.0)) > 0:
                print("⚠️ SALARY TX skipped: gross_salary exists but total_salary_cost is empty")
                return False, "תיבת עלות השכר (total_salary_cost) ריקה"
            return True, ""

        local_id_val = employee.local_id
        desc = f"{SALARY_DESC_PREFIX}{employee_name} | עובד #{local_id_val} ({month}/{year})"

        # 1) exact description
        existing = Transaction.query.filter_by(description=desc, company_id=company_id).first()

        # 2) same employee + month under an older name
        if not existing:
            like_pat = f"{SALARY_DESC_PREFIX}%| עובד #{local_id_val} ({month}/{year})"
            existing = Transaction.query.filter(
                Transaction.company_id == company_id,
                Transaction.description.like(like_pat)
            ).first()

        if existing:
            existing.amount = total_cost
            existing.description = desc
            db.session.commit()
            print(f"✔ SALARY TX updated: {desc} = {total_cost:,.2f}")
            return True, ""

        max_local = (db.session.query(db.func.max(Transaction.local_id))
                     .filter(Transaction.company_id == company_id).scalar())
        try:
            salary_date = datetime.strptime(f"01-{month}-{year}", "%d-%m-%Y").date()
        except Exception:
            salary_date = datetime.today().date()

        col_names = set(Transaction.__table__.columns.keys())

        def build(customer_value):
            wanted = {
                'company_id': company_id,
                'local_id': (max_local or 0) + 1,
                'date': salary_date,
                'description': desc,
                'amount': total_cost,
                'type': 'expense',
                'category_id': None,
                'invoice_id': None,
                'cost_price_at_time': 0.0,
                'quantity': 1,
                'vat_amount': 0.0,
            }
            values = {k: v for k, v in wanted.items() if k in col_names}
            if 'customer_id' in col_names:
                values['customer_id'] = customer_value
            return Transaction(**values)

        # attempt 1: customer "0" in the column's own type (text "0" or number 0, like the other rows)
        is_text_customer = ('customer_id' in col_names and any(
            t in str(Transaction.__table__.columns['customer_id'].type).lower() for t in ('char', 'text')))
        try:
            db.session.add(build("0" if is_text_customer else 0))
            db.session.commit()
            print(f"✔ SALARY TX added: {desc} = {total_cost:,.2f}")
            return True, ""
        except SQLAlchemyError as first_err:        
            db.session.rollback()
            print(f"SALARY TX attempt 1 rejected ({str(getattr(first_err, 'orig', first_err)).splitlines()[0][:150]}), retrying with no customer")

        # attempt 2 (Postgres foreign key / type): no customer
        db.session.add(build(None))
        db.session.commit()
        print(f"✔ SALARY TX added (no customer): {desc} = {total_cost:,.2f}")
        return True, ""

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        err = str(getattr(e, 'orig', e)).strip().splitlines()[0][:200]
        print(f"❌ SALARY TRANSACTION FAILED: {err}")
        return False, err

# ----------------------
#  Save Table Hours Calculate Data on Page
# ----------------------

@app.route('/save_hours', methods=['POST'])
@login_required
def save_hours_route():
    try:
        data = request.get_json() or {}

        employee_id = str(data.get('employee_id', '')).strip()
        employee_name_in = str(data.get('employee_name') or '').strip()
        month = str(data.get('month', '')).strip().zfill(2)
        year = str(data.get('year', '')).strip()

        hours_table = data.get('hours_table', {}) or {}
        work_day_entries_raw = hours_table.get('work_day_entries', []) or []
        monthly_totals_raw = hours_table.get('monthly_totals', {}) or {}
        paid_totals_raw = hours_table.get('paid_totals', {}) or {}
        tax_data_raw = hours_table.get('tax', {}) or {}

        if not employee_id or not month or not year:
            return jsonify(success=False, message="חסר עובד / חודש / שנה"), 400
        if not work_day_entries_raw:
            return jsonify(success=False, message="אין נתונים לשמירה"), 400

        active_company_id = get_active_company_id()
        db.create_all()

        employee = resolve_employee(active_company_id, employee_id)
        if not employee:
            return jsonify(success=False, message="העובד לא נמצא בחברה"), 404

        employee_name = employee_name_in or employee.employee_name or ""

        def snake(d):
            return {k.replace("-", "_"): v for k, v in d.items()}

        monthly_totals = snake(monthly_totals_raw)
        paid_totals = snake(paid_totals_raw)
        tax_data = snake(tax_data_raw)

        base_dir = app.config.get("EMPLOYEES_DIR") or (
            PERSISTENT_BASE + "/employees" if "RENDER" in os.environ
            else os.path.join(BASE_DIR, "static", "employees")
        )
        folder = os.path.join(base_dir, f"company_{active_company_id}", str(employee.local_id))
        os.makedirs(folder, exist_ok=True)
        json_file_path = os.path.join(folder, "hours.json")

        all_hours = {}
        if os.path.exists(json_file_path):
            try:
                with open(json_file_path, 'r', encoding='utf-8') as f:
                    all_hours = json.load(f) or {}
            except Exception:
                all_hours = {}

        all_hours["employee_name"] = employee_name

        yearly = compute_yearly_totals(all_hours, str(employee.local_id),
                                       year, month, tax_data_raw) or {}
        tax_data.update(snake(yearly))
        tax_data_raw.update(yearly)

        month_key = f"{year}-{month}"
        all_hours[month_key] = {
            "hours_table": {
                "work_day_entries": work_day_entries_raw,
                "monthly_totals": monthly_totals_raw,
                "paid_totals": paid_totals_raw,
                "tax": tax_data_raw
            }
        }
        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(all_hours, f, ensure_ascii=False, indent=2)

        try:
            save_to_csv_and_xlsx(active_company_id, employee.local_id)
        except Exception as csv_err:
            print(f"csv/xlsx export failed: {csv_err}")

        db_payload = {}
        db_payload.update(monthly_totals)
        db_payload.update(paid_totals)
        db_payload.update(tax_data)
        db_payload.update({
            'employee_name': employee_name,
            'employeeMonth': month,
            'employeeYear': year,
        })

        employee_columns = set(EmployeeData.__table__.columns.keys())
        for k, v in db_payload.items():
            if k not in employee_columns or k in ('id', 'company_id', 'local_id'):
                continue
            col_type = str(EmployeeData.__table__.columns[k].type).lower()
            if any(t in col_type for t in ('float', 'numeric', 'integer')):
                v = clean_float(v)
            setattr(employee, k, v)

        # 1) save the employee data FIRST
        db.session.commit()

        # 2) then the salary row, in its own commit (a failure here cannot undo step 1)
        tx_ok, tx_msg = sync_salary_transaction(
            active_company_id, employee, employee_name, month, year, tax_data)

        session['selected_employee_id'] = str(employee.local_id)
        session['selected_month'] = month
        session['selected_year'] = year

        message = "הנתונים נשמרו בהצלחה והוצאות השכר סונכרנו"
        if not tx_ok:
            message = f"הנתונים נשמרו, אבל הוצאת השכר לא נרשמה ברווח והפסד: {tx_msg}"

        return jsonify(success=True, message=message,
                       employee_name=employee_name,
                       transaction_synced=tx_ok)

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify(success=False, message=str(e)), 500        

# ----------------------
#  Save To Csv And Xlsx All Data On Folder
# ----------------------

def save_to_csv_and_xlsx(company_id, local_id):
    base_dir = app.config.get("EMPLOYEES_DIR")
    
    folder = os.path.join(base_dir, f"company_{company_id}", str(local_id))
    
    csv_path = os.path.join(folder, "hours_data.csv")
    xlsx_path = os.path.join(folder, "hours_data.xlsx")

    if not os.path.exists(csv_path):
        return  # nothing to export yet

    # Load the CSV into pandas
    df = pd.read_csv(csv_path, encoding='utf-8-sig')
    
    # Save as Excel (XLSX)
    with pd.ExcelWriter(xlsx_path, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Hours')
        
        # Access the workbook and worksheet
        workbook  = writer.book
        worksheet = writer.sheets['Hours']

        # Freeze the first row
        worksheet.freeze_panes(1, 0)

        # Set RTL Hebrew View
        worksheet.right_to_left()

        # Auto-adjust column widths
        for i, col in enumerate(df.columns):
            #  הפיקס המנצח: fillna('') הופך את כל התאים הריקים למחרוזת טקסט ריקה, ומונע לחלוטין את ה-TypeError!
            safe_series = df[col].fillna('').astype(str)
            max_len = max(safe_series.map(len).max(), len(col)) + 2
            worksheet.set_column(i, i, max_len)

        # === Define formats ===
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#000000',   # black background
            'font_color': '#FFFFFF', # white text
            'align': 'center',
            'valign': 'vcenter',
            'border': 1
        })

        body_format = workbook.add_format({
            'border': 1,
            'border_color': '#FFFFFF',  # white grid lines
            'bg_color': '#D9EAF7'       # light blue background
        })

        alt_body_format = workbook.add_format({
            'border': 1,
            'border_color': '#FFFFFF',
            'bg_color': '#F2F2F2'       # light gray alternate rows
        })

        # === Apply header format ===
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)

        # === Apply body formats (striped rows) ===
        for row_num in range(1, len(df) + 1):
            fmt = body_format if row_num % 2 else alt_body_format
            worksheet.set_row(row_num, None, fmt)


# ----------------------
# Get Hours Data: Form Page
# ----------------------

def normalize_keys(data_dict):
    """Convert snake_case keys to kebab-case for frontend."""
    return {k.replace("_", "-"): v for k, v in data_dict.items()}


@app.route('/get_hours_data')
@login_required
def get_hours_data():
    employee_id = session.get('selected_employee_id') or session.get('employee_id')
    selected_year = session.get('selected_year')
    selected_month = session.get('selected_month')

    if not employee_id or str(employee_id).strip() in ['', '0'] or not selected_year or not selected_month:
        return jsonify({"empty": True})

    # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות מיושרת לפי ה-index) -----------------
    user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
    if session.get('owner_access') or user_role == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

    if not active_company_id:
        active_company_id = 1
    # ---------------------------------------------------------------------------

    db.create_all()

    try:
        employee_id_int = int(str(employee_id).strip())
        
        employee = EmployeeData.query.filter(
            (EmployeeData.local_id == employee_id_int) | (EmployeeData.id == employee_id_int)
        ).filter_by(company_id=active_company_id).first()
    except (ValueError, TypeError):
        return jsonify({"empty": True, "error": "Invalid employee ID format"}), 400

    if not employee:
        return jsonify({"empty": True, "error": "Employee not found in this company"}), 404

    # ניקוי ואבטחה מוחלטת של מבנה התאריך למניעת תקלות התאמה בקבצי ה-JSON
    clean_month = str(selected_month).strip().zfill(2)
    month_key = f"{str(selected_year).strip()}-{clean_month}"

    all_hours = load_hours(active_company_id, employee.local_id)
    month_data = all_hours.get(month_key, {})

    hours_table = month_data.get("hours_table", {})

    work_day_entries = hours_table.get("work_day_entries", [])
    monthly_totals = hours_table.get("monthly_totals", {})
    paid_totals = hours_table.get("paid_totals", {})
    tax_data = hours_table.get("tax", {})

    if not work_day_entries:
        return jsonify({"empty": True})

    work_day_entries = [normalize_keys(entry) for entry in work_day_entries]
    monthly_totals = normalize_keys(monthly_totals)
    paid_totals = normalize_keys(paid_totals)
    tax_data = normalize_keys(tax_data)

    return jsonify({
        "empty": False,
        "work_day_entries": work_day_entries,
        "monthly_totals": monthly_totals,
        "paid_totals": paid_totals,
        "tax": tax_data
    })


# ----------------------
#  Employee Update All Yearly Tax payment Monthly On Salary
# ----------------------

def get_yearly_totals(employee_id, target_year=None):
    # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
    user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
    if session.get('owner_access') or user_role == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id or session.get('company_id')

    if not active_company_id:
        return None

    if not target_year:
        target_year = str(datetime.today().year)

    try:
        # Render: המרה קשיחה ומאובטחת ל-int למניעת קריסות טיפוסים מול פוסטגרס
        safe_emp_id = int(str(employee_id).strip())
        
        employee = EmployeeData.query.filter(
            (EmployeeData.local_id == safe_emp_id) | (EmployeeData.id == safe_emp_id)
        ).filter_by(company_id=int(active_company_id)).first()
    except (ValueError, TypeError):
        return None

    if not employee:
        return None
    # ---------------------------------------------------------------------------

    # שליפת נתוני האמת של כל החודשים ישירות מהדיסק הקבוע של Render
    all_hours = load_hours(int(active_company_id), employee.local_id)
    
    # אתחול המילון בדיוק מוחלט לפי שמות השדות (Labels) שהגדרת בשאילתת ה-SQL המקורית שלך!
    totals = {
        'sick_days_salary_yearly': 0.0,
        'vacation_days_salary': 0.0,
        'gross_taxable_yearly': 0.0,
        'employee_pension_fund_yearly': 0.0,
        'self_employed_pension_fund_yearly': 0.0,
        'study_fund_deductions_yearly': 0.0,
        'miscellaneous_deductions_yearly': 0.0,
        'national_insurance_deductions_yearly': 0.0,
        'health_insurance_deductions_yearly': 0.0,
        'income_tax_yearly': 0.0,
        'amount_tax_credit_points_monthly_yearly': 0.0,
        'final_city_tax_benefit_yearly': 0.0,
        'pension_fund_yearly': 0.0,
        'compensation_yearly': 0.0,
        'study_fund_yearly': 0.0,
        'disability_yearly': 0.0,
        'miscellaneous_yearly': 0.0,
        'national_insurance_yearly': 0.0,
        'salary_tax_yearly': 0.0,
        'total_employer_contributions_yearly': 0.0,
        'total_salary_cost_yearly': 0.0
    }
    
    def clean_number(value):
        try:
            return float(str(value).replace("₪", "").replace(",", "").strip())
        except:
            return 0.0

    # סריקה וסכימה ידנית ומבוטחת מתוך קובץ ה-JSON בדיסק למניעת עיוותי מפתחות
    if isinstance(all_hours, dict):
        for m_key, month_data in all_hours.items():
            if m_key == "employee_name": 
                continue
            if not m_key.startswith(str(target_year)): 
                continue
                
            tax = month_data.get("hours_table", {}).get("tax", {})
            
            # מיפוי קשיח ומפורט בין שדות הבסיס ב-JSON לבין השדות השנתיים המבוקשים בפרונט-אנד
            totals['sick_days_salary_yearly'] += clean_number(tax.get("sick_days_salary", 0))
            totals['vacation_days_salary'] += clean_number(tax.get("vacation_days_salary", 0))
            totals['gross_taxable_yearly'] += clean_number(tax.get("gross_taxable", 0))
            totals['employee_pension_fund_yearly'] += clean_number(tax.get("employee_pension_fund", 0))
            totals['self_employed_pension_fund_yearly'] += clean_number(tax.get("self_employed_pension_fund", 0))
            totals['study_fund_deductions_yearly'] += clean_number(tax.get("study_fund_deductions", 0))
            totals['miscellaneous_deductions_yearly'] += clean_number(tax.get("miscellaneous_deductions", 0))
            totals['national_insurance_deductions_yearly'] += clean_number(tax.get("national_insurance_deductions", 0))
            totals['health_insurance_deductions_yearly'] += clean_number(tax.get("health_insurance_deductions", 0))
            totals['income_tax_yearly'] += clean_number(tax.get("income_tax", 0))
            totals['amount_tax_credit_points_monthly_yearly'] += clean_number(tax.get("amount_tax_credit_points_monthly", 0))
            totals['final_city_tax_benefit_yearly'] += clean_number(tax.get("final_city_tax_benefit", 0))
            totals['pension_fund_yearly'] += clean_number(tax.get("pension_fund", 0))
            totals['compensation_yearly'] += clean_number(tax.get("compensation", 0))
            totals['study_fund_yearly'] += clean_number(tax.get("study_fund", 0))
            totals['disability_yearly'] += clean_number(tax.get("disability", 0))
            totals['miscellaneous_yearly'] += clean_number(tax.get("miscellaneous", 0))
            totals['national_insurance_yearly'] += clean_number(tax.get("national_insurance", 0))
            totals['salary_tax_yearly'] += clean_number(tax.get("salary_tax", 0))
            totals['total_employer_contributions_yearly'] += clean_number(tax.get("total_employer_contributions", 0))
            totals['total_salary_cost_yearly'] += clean_number(tax.get("total_salary_cost", 0))

    # בניית אובייקט תוצאה נקי (מדמה בדיוק שורה בודדת שחוזרת משאילתת הדאטהבייס המקורית שלך)
    class YearlyTotalsContext:
        def __init__(self, data_dict):
            for k, v in data_dict.items():
                setattr(self, k, v)
                
    return YearlyTotalsContext(totals)


# -----------------------------------------------------------
#  N8N NEW FOR GREAT AUTO Save_hours_from_n8n API Endpoint 
# -----------------------------------------------------------

@app.route('/api/hours_data/save_from_n8n', methods=['POST'])
def api_save_hours_from_n8n():
    try:
        # 1. security: the key must be set on the server AND match (a missing key can no longer pass)
        auth_header = request.headers.get("X-API-KEY") or ""
        expected_key = N8N_API_KEY or ""
        if not expected_key or not hmac.compare_digest(auth_header, expected_key):
            return jsonify({"error": "Unauthorized API Access"}), 401

        # 2. full JSON from n8n
        data = request.get_json(silent=True) or {}

        employee_id = str(data.get("employee_id", "")).strip()
        selected_month = str(data.get("month", "")).strip().zfill(2)
        selected_year = str(data.get("year", "")).strip()

        if (not employee_id
                or not (selected_month.isdigit() and 1 <= int(selected_month) <= 12)
                or not (selected_year.isdigit() and len(selected_year) == 4)):
            return jsonify({"error": "Missing or invalid employee_id, month, or year"}), 400

        # active company (default OWNER_COMPANY_ID)
        try:
            active_company_id = int(data.get("company_id") or OWNER_COMPANY_ID)
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid company_id"}), 400

        db.create_all()

        # same lookup as the rest of the app: local_id first, this company only
        employee = resolve_employee(active_company_id, employee_id)
        if not employee:
            return jsonify({"error": "Security Breach: Employee not found in this company"}), 404

        # name: from n8n, else from the database (no more overwriting with "עובד AI")
        employee_name = (str(data.get("employee_name") or "").strip()
                         or employee.employee_name or "עובד AI")

        hours_table = data.get("hours_table", {}) or {}
        work_day_entries_raw = hours_table.get("work_day_entries", []) or []
        monthly_totals_raw = hours_table.get("monthly_totals", {}) or {}
        paid_totals_raw = hours_table.get("paid_totals", {}) or {}
        tax_data_raw = hours_table.get("tax", {}) or {}

        month_key = f"{selected_year}-{selected_month}"

        # hours.json: same path as /save_hours
        base_dir = app.config.get("EMPLOYEES_DIR") or (
            PERSISTENT_BASE + "/employees" if "RENDER" in os.environ
            else os.path.join(BASE_DIR, "static", "employees"))
        folder = os.path.join(base_dir, f"company_{active_company_id}", str(employee.local_id))
        os.makedirs(folder, exist_ok=True)
        json_file_path = os.path.join(folder, "hours.json")

        all_hours = {}
        if os.path.exists(json_file_path):
            try:
                with open(json_file_path, 'r', encoding='utf-8') as f:
                    all_hours = json.load(f) or {}
            except Exception:
                all_hours = {}

        all_hours["employee_name"] = employee_name

        # yearly totals: same function as /save_hours, and now really saved
        # (before, they were calculated and then thrown away)
        try:
            yearly = compute_yearly_totals(all_hours, str(employee.local_id),
                                           selected_year, selected_month, tax_data_raw) or {}
            tax_data_raw.update(yearly)
        except Exception as yearly_err:
            print(f"n8n yearly totals failed: {yearly_err}")

        all_hours[month_key] = {
            "hours_table": {
                "work_day_entries": work_day_entries_raw,
                "monthly_totals": monthly_totals_raw,
                "paid_totals": paid_totals_raw,
                "tax": tax_data_raw
            }
        }

        with open(json_file_path, 'w', encoding='utf-8') as f:
            json.dump(all_hours, f, ensure_ascii=False, indent=2)

        # csv/xlsx export, same as /save_hours (never blocks the save)
        try:
            save_to_csv_and_xlsx(active_company_id, employee.local_id)
        except Exception as csv_err:
            print(f"n8n csv/xlsx export failed: {csv_err}")

        # payroll cost into profit and loss: same helper, amount = total_salary_cost
        tx_ok, tx_msg = sync_salary_transaction(
            active_company_id, employee, employee_name,
            selected_month, selected_year, tax_data_raw)

        return jsonify({
            "status": "success",
            "message": "Hours data synchronized and saved successfully from n8n",
            "employee_id": employee_id,
            "local_id": employee.local_id,
            "month": selected_month,
            "year": selected_year,
            "entries_count": len(work_day_entries_raw),
            "transaction_synced": tx_ok,
            "transaction_error": None if tx_ok else tx_msg
        }), 201

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500




# ----------------------
#   Build All Employee City Car Form
# ----------------------

#  Load Excel files City Car Form
df1 = pd.read_excel('Car.xlsm', sheet_name='Car', engine='openpyxl')
df2 = pd.read_excel('City.xlsm', sheet_name='City', engine='openpyxl')

# Separate DataFrames
df_car = df1.copy()
df_city = df2.copy()

# ----------------------
# Contact Employee_id: Contact Form Submission
# ----------------------

@app.route('/contact_form', methods=['GET', 'POST'])
@login_required
def contact_form():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')
        input_date_val = today_str
        form_data = {} 

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            active_company_id = 1

        # Render: המרה מפורשת למספר שלם למניעת קריסות טיפוסים ב-SQL
        active_company_id = int(active_company_id)

        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            company_obj = Company(id=active_company_id, name="חברה ברירת מחדל", translations_json="{}")
            db.session.add(company_obj)
            db.session.commit()
        # ---------------------------------------------------------------------------

        session.setdefault('car_data', None)
        session.setdefault('city_data', None)
        session.setdefault('car_form_fields', {'car_year': '', 'car_model': '', 'car_type': ''})
        session.setdefault('city_form_fields', {'city_name': ''})
        session.setdefault('employee_data', {})

        if request.method == 'POST':
            form_type = request.form.get('form_type')

            # 1. City Form Submission (חישוב זיכוי ישוב)
            if form_type == 'city_form':
                city_name = request.form.get('city_name', '').strip()
                session['city_form_fields']['city_name'] = city_name

                form_data_captured = {
                    'employee_name': request.form.get('employee_name', '').strip(),
                    'id_number': request.form.get('id_number', '').strip(),
                    'date': request.form.get('date', '').strip(),
                    'address': request.form.get('address', '').strip(),
                    'city': city_name,
                    'postal_code': request.form.get('postal_code', '').strip(),
                    'mobile_phone': request.form.get('phone', '').strip(),
                    'home_phone': request.form.get('home_phone', '').strip(),
                    'email': request.form.get('email', '').strip(),
                    'date_of_birth': request.form.get('date_of_birth', '').strip(),
                    'employee_number': request.form.get('employee_number', '').strip(),
                    'hourly_rate': request.form.get('hourly_rate', '').strip(),
                    'role': request.form.get('role', 'עובד').strip()
                }
                session['employee_data'] = form_data_captured

                if city_name:
                    filtered_df = df_city[df_city['שם יישוב'] == city_name]
                    if not filtered_df.empty:
                        city_sign = str(filtered_df.iloc[0]['סמל היישוב'])
                        city_top_tax = int(filtered_df.iloc[0]['סכום זיכוי'])
                        city_value = float(filtered_df.iloc[0]['שיעור 2024'])
                        city_value_percentage = f"{city_value * 100:.2f}%"
                        monthly_city_top_tax = city_top_tax / 12
                        formatted_monthly_city_top_tax = format_currency(monthly_city_top_tax)

                        session['city_data'] = {
                            'שם יישוב': city_name,
                            'שיעור 2024': city_value_percentage,
                            'city_value_percentage': city_value,
                            'סמל היישוב': city_sign,
                            'סכום זיכוי': format_currency(city_top_tax),
                            'סכום זיכוי חודשי': formatted_monthly_city_top_tax,
                            'monthly_city_tax_tops': monthly_city_top_tax
                        }
                return redirect(url_for('contact_form'))

            elif form_type == 'clear_city_form':
                session['city_data'] = None
                session['city_form_fields'] = {'city_name': ''}
                return redirect(url_for('contact_form'))

            # 2. Car Form Submission 
            elif form_type == 'car_form':
                car_year = request.form.get('car_year', '')
                car_model = request.form.get('car_model', '')
                car_type = request.form.get('car_type', '')
                session['car_form_fields'] = {
                    'car_year': car_year,
                    'car_model': car_model,
                    'car_type': car_type
                }

                if all(session['car_form_fields'].values()):
                    try:
                        filtered_df = df_car[
                            (df_car['שנת רישום'] == int(car_year)) &
                            (df_car['קוד תוצר'] == int(car_model)) &
                            (df_car['קוד דגם'] == int(car_type))
                        ]
                    except (ValueError, TypeError):
                        filtered_df = pd.DataFrame()

                    if not filtered_df.empty:
                        car_value_raw = filtered_df.iloc[0]['שווי שימוש']
                        car_value = float(car_value_raw)
                        formatted_car_value = format_currency(car_value)

                        session['car_data'] = {
                            'car_year': car_year,
                            'car_model': car_model,
                            'car_type': car_type,
                            'שווי שימוש': formatted_car_value
                        }
                return redirect(url_for('contact_form'))

            elif form_type == 'clear_car':
                session['car_data'] = None
                session['car_form_fields'] = {'car_year': '', 'car_model': '', 'car_type': ''}
                return redirect(url_for('contact_form'))

            # 3. Contact Form Submission
            elif form_type == 'contact_form':
                employee_name = request.form.get('employee_name', '').strip()
                id_number = request.form.get('id_number', '').strip()
                date_str = request.form.get('date', '').strip()
                city_name = request.form.get('city', '').strip()

                if not employee_name or not id_number or not date_str:
                    flash("שם העובד, מספר זהות ותאריך הם שדות חובה", "error")
                    return redirect(url_for('contact_form'))

                try:
                    if '-' in date_str:
                        parts = date_str.split('-')
                        if len(parts) > 0 and len(parts) == 3:  
                            parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
                        else:  # DD-MM-YYYY
                            parsed_date = datetime.strptime(date_str, '%d-%m-%Y')
                    elif '/' in date_str:
                        parts = date_str.split('/')
                        if len(parts) > 0 and len(parts) == 3:  
                            parsed_date = datetime.strptime(date_str, '%Y/%m/%d')
                        else:  # DD/MM/YYYY
                            parsed_date = datetime.strptime(date_str, '%d/%m/%Y')
                    else:
                        parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
                except Exception:
                    parsed_date = datetime.today()

                formatted_date = parsed_date.strftime('%d/%m/%Y')

                auto_city_value_percentage = 0.0
                auto_monthly_city_tax_tops = 0.0

                if city_name:
                    filtered_df = df_city[df_city['שם יישוב'] == city_name]
                    if not filtered_df.empty:
                        try:
                            city_top_tax = int(filtered_df.iloc[0]['סכום זיכוי'])
                            city_value = float(filtered_df.iloc[0]['שיעור 2024'])
                            
                            auto_city_value_percentage = city_value
                            auto_monthly_city_tax_tops = city_top_tax / 12
                        except Exception as e:
                            print(f"⚠️ Error pulling city data from dataframe: {e}")

                email = request.form.get('email', '').strip()
                password = request.form.get('password', '').strip()

                user = User.query.filter_by(email=email).first() if email else None
                if email and not user:
                    user = User(
                        email=email,
                        username=employee_name,
                        role='employee',
                        company_id=active_company_id,
                        customer_id=None
                    )
                    user.set_password(password if password else "123456")
                    db.session.add(user)
                    db.session.commit()

                form_data = {
                    'user_id': user.id if user else None,
                    'company_id': active_company_id,
                    'employee_id': user.id if user else 0,  
                    'employee_name': employee_name,
                    'id_number': id_number,
                    'date': formatted_date,
                    'employeeMonth': parsed_date.strftime('%m'),
                    'employeeYear': parsed_date.strftime('%Y'),
                    'address': request.form.get('address', '').strip(),
                    'city': city_name,
                    'postal_code': request.form.get('postal_code', '').strip(),
                    'mobile_phone': request.form.get('phone', '').strip(),
                    'home_phone': request.form.get('home_phone', '').strip(),
                    'email': email,
                    'start_date': request.form.get('start_date', '').strip(),
                    'aliyah_date': request.form.get('aliyah_date', '').strip(),
                    'date_of_birth': request.form.get('date_of_birth', '').strip(),
                    'bank_number': request.form.get('bank_number', '').strip(),
                    'branch_number': request.form.get('branch_number', '').strip(),
                    'account_number': request.form.get('account_number', '').strip(),
                    'clothing_value': to_float(request.form.get('clothing_value')),
                    'cars_value': to_float(request.form.get('cars_value')),
                    'monthly_city_tax_tops': to_float(request.form.get('monthly_city_tax_tops')) or auto_monthly_city_tax_tops,
                    'city_value_percentage': to_float(request.form.get('city_value_percentage')) or auto_city_value_percentage,
                    'lunch_value': to_float(request.form.get('lunch_value')),
                    'mobile_value': to_float(request.form.get('mobile_value')),
                    'hourly_rate': to_float(request.form.get('hourly_rate')),
                    'employee_number': request.form.get('employee_number', '').strip(),
                    'thirteenth_salary': to_float(request.form.get('thirteenth_salary')),
                    'message': request.form.get('message', '').strip(),
                    'work_apartment': request.form.get('work_apartment', '').strip(),
                    'work_percent': to_float(request.form.get('work_percent')),
                    'marital_status': request.form.get('marital_status', '').strip(),
                    'gender_status': request.form.get('gender_status', '').strip(),
                    'tax_credit_points': to_float(request.form.get('tax_credit_points')),
                    'resident_status': request.form.get('resident_status', '').strip(),
                    'hmo_member': request.form.get('hmo_member', '').strip(),
                    'social_number': request.form.get('social_number', '').strip(),
                    'irs_status': request.form.get('irs_status', '').strip(),
                    'contract_status': request.form.get('contract_status', '').strip(),
                    'kibbutz_member_status': request.form.get('kibbutz_member_status', '').strip(),
                    'tax_point_child': to_float(request.form.get('tax_point_child')),
                    'role': request.form.get('role', 'עובד').strip()
                }

                employee = EmployeeData.query.filter_by(id_number=id_number, company_id=active_company_id).first()

                if employee:
                    for key, value in form_data.items():
                        setattr(employee, key, value)
                else:
                    employee = EmployeeData()
                    
                    for column in employee.__table__.columns:
                        if not column.nullable and column.default is None and not column.primary_key:
                            if isinstance(column.type, (db.Integer, db.Float)):
                                setattr(employee, column.name, 0.0)
                            elif isinstance(column.type, db.Boolean):
                                setattr(employee, column.name, False)
                            else:
                                setattr(employee, column.name, '')
                    
                    last_emp = EmployeeData.query.filter_by(company_id=active_company_id).order_by(EmployeeData.local_id.desc()).first()
                    next_local_id = 1 if not last_emp or not last_emp.local_id else (int(last_emp.local_id) + 1)
                    employee.local_id = next_local_id
                    
                    for key, value in form_data.items():
                        setattr(employee, key, value)
                        
                    db.session.add(employee)
                    
                db.session.flush() 

                # Render: המרה מפורשת של ה-company_id למספר שלם למניעת קריסות SQL בענן
                safe_company_id = int(active_company_id)

                # הצינור הדו-כיווני ל-Render: חיפוש וסנכרון לפי ה-id_number והחברה למניעת קריסות Primary Key
                employee_card = Employee.query.filter_by(id_number=id_number, company_id=safe_company_id).first()
                if employee_card:
                    employee_card.employee_name = employee_name
                    employee_card.address       = request.form.get('address', '')
                    employee_card.city          = city_name
                    employee_card.postal_code   = request.form.get('postal_code', '')
                    employee_card.mobile_phone  = request.form.get('phone', '') 
                    employee_card.email         = request.form.get('email', '').strip().lower()
                else:
                    new_employee_row = Employee(
                        company_id=safe_company_id, 
                        local_id=employee.local_id,
                        date=date_str, 
                        employee_name=employee_name, 
                        id_number=id_number,
                        address=request.form.get('address', ''), 
                        city=city_name,
                        postal_code=request.form.get('postal_code', ''),
                        mobile_phone=request.form.get('phone', ''), 
                        email=request.form.get('email', '').strip().lower(), 
                        role='employee', 
                        is_active=True
                    )
                    db.session.add(new_employee_row)

                session['shared_employee_name'] = employee_name
                session['employee_id'] = employee.id_number
                session['selected_month'] = datetime.now().month
                session['selected_year'] = datetime.now().year
                session['employee_data'] = form_data
                
                db.session.commit()
                flash("נתוני העובד נשמרו בהצלחה!", "success")

                # DISK SYNC & i18n MULTI-COMPANY LAYER (get_val)
                try:
                    safe_company_id = int(active_company_id)

                    base_employees_dir = app.config.get("EMPLOYEES_DIR")

                    emp_folder = os.path.join(base_employees_dir, f"company_{safe_company_id}", str(employee.local_id))
                    os.makedirs(emp_folder, exist_ok=True)
                    json_file_path = os.path.join(emp_folder, "employee.json")

                    existing_i18n_data = {}
                    if os.path.isfile(json_file_path):
                        try:
                            with open(json_file_path, "r", encoding="utf-8") as f:
                                existing_i18n_data = json.load(f)
                        except:
                            existing_i18n_data = {}

                    if not isinstance(existing_i18n_data, dict):
                        existing_i18n_data = {}

                    lookup_lang = language if 'language' in locals() else get_lang()
                    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(lookup_lang, lookup_lang)

                    fields_to_sync = {
                        "name": employee_name,
                        "address": request.form.get('address', '').strip(),
                        "city": city_name,
                        "mobile_phone": request.form.get('phone', '').strip(),
                        "id_number": id_number,
                        "postal_code": request.form.get('postal_code', '').strip(),
                        "message": request.form.get('message', '').strip()
                    }

                    for field_key, field_value in fields_to_sync.items():
                        existing_i18n_data.setdefault(field_key, {})
                        if not isinstance(existing_i18n_data[field_key], dict):
                            existing_i18n_data[field_key] = {}
                        existing_i18n_data[field_key]["he"] = field_value
                        existing_i18n_data[field_key][lookup_lang] = field_value

                    existing_i18n_data["local_id"] = employee.local_id
                    existing_i18n_data["company_id"] = safe_company_id
                    existing_i18n_data["email"] = email

                    with open(json_file_path, "w", encoding="utf-8") as f:
                        json.dump(existing_i18n_data, f, ensure_ascii=False, indent=4)

                    # המרה קשיחה של המזהים ל-int לפני שליחתם לטרד של התרגום ברקע
                    safe_emp_id_thread = int(employee.id)

                    translate_employee_in_background(
                        employee_id=safe_emp_id_thread,
                        company_id=safe_company_id,
                        name=employee_name,
                        address=request.form.get('address', '').strip(),
                        city=city_name,
                        message=request.form.get('message', '').strip(),
                        mobile_phone=request.form.get('phone', '').strip(),
                        id_number=id_number,
                        postal_code=request.form.get('postal_code', '').strip()
                    )
                    print(f"✔ Airtight File initialized 1st and 30-lang translation thread invoked for local_id: {employee.local_id}")
                except Exception as file_err:
                    print(f"⚠️ Warning: Employee 30-lang translation layer failed: {file_err}")

                session['employee_data'] = {}

                try:
                    msg = Message(
                        subject="פרטי העובד שלך נשמרו בהצלחה",
                        sender=app.config['MAIL_USERNAME'],
                        recipients=[employee.email]
                    )
                    msg.html = render_template('email_template.html', employee=employee)
                    mail.send(msg)
                    flash("נתוני העובד והתרגומים נשמרו בהצלחה, ומייל נשלח לעובד!", "success")
                except Exception as e:
                    flash(f"הנתונים נשמרו, אך אירעה שגיאה בשליחת מייל: {str(e)}", "warning")

                return redirect(url_for('contact_form', employee_id=employee.id))

        # ----------------- LOGIC FOR GET REQUEST -----------------
        language  = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')

        # Render: המרה מפורשת ל-int של ה-company_id לכל אורך שאילתות ה-GET
        safe_active_company_id = int(active_company_id)

        all_employees = EmployeeData.query.filter_by(
            company_id=safe_active_company_id
        ).order_by(EmployeeData.employee_name).all()

        pending_users = User.query.filter_by(
            company_id=safe_active_company_id,
            role='employee'
        ).all()

        existing_employee_emails = {c.email for c in all_employees if c.email}

        pending_employees_list = []
        for pu in pending_users:
            if pu.email and pu.email not in existing_employee_emails:
                dummy_emp = EmployeeData(
                    id=pu.id,
                    company_id=safe_active_company_id,
                    local_id=0,   
                    employee_id=0, 
                    employee_name=pu.username or pu.email,
                    email=pu.email,
                    date=today_str
                )
                pending_employees_list.append(dummy_emp)

        all_employees_combined = all_employees + pending_employees_list

        req_employee_id = request.args.get('employee_id')
        employee = None
        employee_i18n = {}

        if req_employee_id and str(req_employee_id).strip() not in ['', '0']:
            try:
                # Render: המרה מפורשת של ה-id המגיע מה-URL ל-int כדי למנוע קריסות VARCHAR מול Integer
                employee_id_int = int(req_employee_id)
                employee = EmployeeData.query.filter_by(
                    id=employee_id_int,
                    company_id=safe_active_company_id
                ).first()
            except (ValueError, TypeError):
                employee = None

            if not employee:
                try:
                    employee = next((c for c in pending_employees_list if c.id == int(req_employee_id)), None)
                except (ValueError, TypeError):
                    employee = None

            if employee:
                employee_i18n = load_employee_translated(
                    employee,
                    language,
                    company_id=safe_active_company_id
                ) or {}
        else:
            id_number = request.args.get('id_number', '').strip()
            if id_number:
                employee = EmployeeData.query.filter_by(id_number=id_number, company_id=safe_active_company_id).first()
                if employee:
                    employee_i18n = load_employee_translated(employee, language, company_id=safe_active_company_id) or {}

        input_date_val = today_str
        form_data_get = {}

        if employee:
            form_data_get = {c.name: getattr(employee, c.name) for c in employee.__table__.columns}
            
            if getattr(employee, 'date', None):
                input_date_val = employee.date
                if "/" in input_date_val:
                    try:
                        d_obj = datetime.strptime(input_date_val, '%d/%m/%Y')
                        input_date_val = d_obj.strftime('%Y-%m-%d')
                    except:
                        pass
            elif employee.employeeYear and employee.employeeMonth:
                input_date_val = f"{employee.employeeYear}-{employee.employeeMonth}-01"

        employee_i18n_list = {}
        for c in all_employees_combined:
            # Render: שימוש ב-safe_active_company_id המומר למספר שלם למניעת קריסות טיפוסים ב-PostgreSQL
            trans = load_employee_translated(c, language, company_id=safe_active_company_id) or {}
            
            final_data = {
                "name": trans.get("name") or c.employee_name or "",
                "address": trans.get("address") or getattr(c, "address", "") or "",
                "city": trans.get("city") or getattr(c, "city", "") or "",
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or "",
                "mobile_phone": trans.get("mobile_phone") or getattr(c, "phone", "") or ""
            }

            key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
            employee_i18n_list[key] = final_data

        months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
        years = list(range(2020, 2031))
        days_data = get_days_in_month(2024, 1)
        company_obj = db.session.get(Company, safe_active_company_id) if safe_active_company_id else None

        saved_ids = session.get('last_search_results', [])
        search_results_objects = []
        if saved_ids and isinstance(saved_ids, list):
            try:
                clean_ids = [int(x) for x in saved_ids if str(x).strip() not in ['', '0']]
                if clean_ids:
                    search_results_objects = EmployeeData.query.filter(EmployeeData.id.in_(clean_ids)).all()
            except (ValueError, TypeError):
                search_results_objects = []

        return render_template(
            'contact_form.html',
            employee_data=session.get('employee_data', {}),
            employee=employee,                             
            employees=search_results_objects,
            all_employees=all_employees_combined,
            all_company_employees=all_employees_combined,  
            employee_i18n=employee_i18n,                  
            employee_i18n_list=employee_i18n_list,          
            formatted_monthly_city_top_tax=session['city_data']['סכום זיכוי חודשי'] if session.get('city_data') else '',
            formatted_car_value=session['car_data']['שווי שימוש'] if session.get('car_data') else '',
            cars_value=session.get('car_data'),
            car_form_fields=session.get('car_form_fields'),
            city_form_fields=session.get('city_form_fields'),
            car_data=session.get('car_data'),
            city_data=session.get('city_data'),
            contact_data=session.get('contact_data', {}),
            months=months,
            years=years,
            days_data=days_data,
            form_data=form_data_get if employee else {},                       
            company=load_company_translated(company_obj, language) if company_obj else {},
            company_db=company_obj,
            language=language,
            today=today_str,
            input_date_val=input_date_val
        )

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בטעינת טופס אנשי הקשר", "danger")
        return redirect(url_for('index'))


# ----------------------
# Contact Search Run Auto Search CITY Results הנחת יישוב בהזנת עיר בפורם
# ----------------------

@app.route('/api/get_city_benefits')
@login_required
def api_get_city_benefits():
    city_name = request.args.get('city_name', '').strip()
    if not city_name:
        return jsonify({'success': False, 'message': 'שם עיר ריק'}), 400
        
    filtered_df = df_city[df_city['שם יישוב'] == city_name]
    if not filtered_df.empty:
        try:
            city_sign = str(filtered_df.iloc[0]['סמל היישוב'])
            city_top_tax = int(filtered_df.iloc[0]['סכום זיכוי'])
            city_value = float(filtered_df.iloc[0]['שיעור 2024'])
            
            return jsonify({
                'success': True,
                'city_name': city_name,
                'city_value_percentage': f"{city_value * 100:.2f}%",
                'city_sign': city_sign,
                'city_top_tax': format_currency(city_top_tax),
                'monthly_city_top_tax': format_currency(city_top_tax / 12)
            })
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)}), 500
            
    return jsonify({'success': False, 'message': 'היישוב לא נמצא במאגר'}), 404

# ----------------------
# Contact Search Clear Employee: Form Page
# ----------------------

@app.route('/search_employee', methods=['GET', 'POST'])
@login_required
def search_employee():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')
        input_date_val = today_str

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            active_company_id = 1

        # המרה מפורשת ל-int עבור PostgreSQL ב-Render
        active_company_id = int(active_company_id)

        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            company_obj = Company(id=active_company_id, name="חברה ברירת מחדל", translations_json="{}")
            db.session.add(company_obj)
            db.session.commit()
        # ---------------------------------------------------------------------------

        session['city_data'] = None
        session['city_form_fields'] = {
            'city_name': '', 'city_value': '', 'city_sign': '', 
            'city_top_tax': '', 'monthly_city_top_tax': ''
        }
        session['car_data'] = None
        session['car_form_fields'] = {
            'car_year': '', 'car_model': '', 'car_type': '', 'car_value': ''
        }

        search_name = request.form.get('search_name') if request.method == 'POST' else request.args.get('search_name')
        
        if not search_name and session.get('shared_employee_name'):
            search_name = session.get('shared_employee_name')

        search_results = EmployeeData.query.filter(
            EmployeeData.company_id == active_company_id,
            EmployeeData.employee_name.ilike(f'%{search_name}%')
        ).all() if search_name else []

        session['last_search_results'] = [emp.id for emp in search_results]

        all_employees = EmployeeData.query.filter_by(company_id=active_company_id).order_by(EmployeeData.employee_name).all()
        
        pending_users = User.query.filter_by(
            company_id=active_company_id,
            role='employee'
        ).all()

        existing_employee_emails = {c.email for c in all_employees if c.email}
        pending_employees_list = []
        for pu in pending_users:
            if pu.email and pu.email not in existing_employee_emails:
                dummy_emp = EmployeeData(
                    id=pu.id,
                    company_id=active_company_id,
                    local_id=0,
                    employee_name=pu.username or pu.email,
                    email=pu.email,
                    date=today_str
                )
                pending_employees_list.append(dummy_emp)

        all_employees_combined = all_employees + pending_employees_list

        months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי',
                  'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
        years = list(range(2020, 2031))
        days_data = get_days_in_month(2024, 1)

        employee = search_results[0] if search_results else None
        employee_i18n = {}

        form_data = {}
        if employee:
            full_address = (employee.address or "").strip()
            address_parts = full_address.rsplit(' ', 1) if ' ' in full_address else [full_address, ""]
            
            db_date = employee.date_of_birth
            formatted_birthday = ""
            if db_date:
                if hasattr(db_date, 'strftime'):
                    formatted_birthday = db_date.strftime('%Y-%m-%d')
                elif isinstance(db_date, str):
                    formatted_birthday = db_date.strip()

                if "/" in formatted_birthday:
                    try:
                        d_obj = datetime.strptime(formatted_birthday, '%d/%m/%Y')
                        formatted_birthday = d_obj.strftime('%Y-%m-%d')
                    except:
                        pass

            form_data = {c.name: getattr(employee, c.name) for c in employee.__table__.columns}
            form_data.update({
                'employee_id': employee.id,
                'employee_name': employee.employee_name, 
                'id_number': employee.id_number or "",
                'address': employee.address or "",
                'city': employee.city or "",
                'postal_code': employee.postal_code or "", 
                'mobile_phone': employee.mobile_phone or "", 
                'email': employee.email or "",
                'date': datetime.today().strftime('%d/%m/%Y')
            })

            # בודק אם לעובד יש עיר בשדות הפנדה מדליק את הדגל אוטומטית!
            has_city = bool(employee.city and employee.city.strip())
            has_no_tax_calc = (getattr(employee, 'monthly_city_tax_tops', 0.0) or 0.0) == 0.0
            if has_city and has_no_tax_calc:
                form_data['run_auto_city'] = True

            #  דגל ריצה אוטומטית לשווי השימוש ברכב 
            has_car_specs = bool(getattr(employee, 'car_year', None) and getattr(employee, 'car_model', None) and getattr(employee, 'car_type', None))
            if has_car_specs and not session.get('car_data'):
                form_data['run_auto_car'] = True

            if employee.employeeYear and employee.employeeMonth:
                input_date_val = f"{employee.employeeYear}-{employee.employeeMonth}-01"

            employee_i18n = load_employee_translated(
                employee,
                language,
                company_id=active_company_id
            ) or {}

        elif session.get('shared_employee_name'):
            employee = EmployeeData(
                employee_name=session.get('shared_employee_name'),
                email=session.get('shared_email', ''),
                address=session.get('shared_address', ''),
                mobile_phone=session.get('shared_phone', '')
            )
            form_data = {
                'employee_name': session.get('shared_employee_name'),
                'email': session.get('shared_email', ''),
                'address': session.get('shared_address', ''),
                'mobile_phone': session.get('shared_phone', ''),
                'date': datetime.today().strftime('%d/%m/%Y'),
                'run_auto_city': True,
                'run_auto_car': True  
            }

        if employee:
            for attr in ["id_number", "postal_code", "city", "mobile_phone", "address", "email", "message"]:
                if getattr(employee, attr, None) is None:
                    setattr(employee, attr, "")
        for k, v in form_data.items():
            if v is None:
                form_data[k] = ""

        employee_i18n_list = {}
        for c in all_employees_combined:
            trans = load_employee_translated(c, language, company_id=active_company_id) or {}
            
            final_data = {
                "name": trans.get("name") or c.employee_name or "",
                "address": trans.get("address") or getattr(c, "address", "") or "",
                "city": trans.get("city") or getattr(c, "city", "") or "",
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or "",
                "mobile_phone": trans.get("mobile_phone") or getattr(c, "phone", "") or ""
            }
            
            for fk, fv in final_data.items():
                if fv is None:
                    final_data[fk] = ""
                    
            key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
            employee_i18n_list[key] = final_data

        return render_template(
            'contact_form.html',
            months=months,
            years=years,
            days_data=days_data,
            employees=search_results,
            all_employees=all_employees_combined,
            all_company_employees=all_employees_combined,  
            form_data=form_data if form_data else {},  
            employee=employee,
            employee_i18n=employee_i18n,                  
            employee_i18n_list=employee_i18n_list,          
            employee_data=session.get('employee_data', {}),  
            car_data=session['car_data'],
            city_data=session['city_data'],
            car_form_fields=session['car_form_fields'],
            city_form_fields=session['city_form_fields'],
            company=load_company_translated(company_obj, language) if company_obj else {},
            company_db=company_obj,
            language=language,
            today=today_str,
            input_date_val=input_date_val
        )
    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בביצוע החיפוש", "danger")
        return redirect(url_for('contact_form'))


@app.route('/clear_search_results_employee', methods=['POST'])
@login_required
def clear_search_results_employee():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')
        
        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            active_company_id = 1

        # המרה מפורשת ל-int עבור PostgreSQL ב-Render
        active_company_id = int(active_company_id)

        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            company_obj = Company(id=active_company_id, name="חברה ברירת מחדל", translations_json="{}")
            db.session.add(company_obj)
            db.session.commit()
        # ---------------------------------------------------------------------------

        session['employee_data'] = {}
        session['city_data'] = None
        session['city_form_fields'] = {
            'city_name': '', 'city_value': '', 'city_sign': '', 
            'city_top_tax': '', 'monthly_city_top_tax': ''
        }
        session['car_data'] = None
        session['car_form_fields'] = {
            'car_year': '', 'car_model': '', 'car_type': '', 'car_value': ''
        }
        
        session.pop('shared_employee_name', None)
        session.pop('shared_email', None)
        session.pop('shared_address', None)
        session.pop('shared_phone', None)

        form_data = {} 
        
        months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני', 'יולי',
                  'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
        years = list(range(2020, 2031))
        days_data = get_days_in_month(2024, 1)

        all_employees = EmployeeData.query.filter_by(company_id=active_company_id).order_by(EmployeeData.employee_name).all()
        
        pending_users = User.query.filter_by(
            company_id=active_company_id,
            role='employee'
        ).all()

        existing_employee_emails = {c.email for c in all_employees if c.email}
        pending_employees_list = []
        for pu in pending_users:
            if pu.email and pu.email not in existing_employee_emails:
                dummy_emp = EmployeeData(
                    id=pu.id,
                    company_id=active_company_id,
                    local_id=0,
                    employee_name=pu.username or pu.email,
                    email=pu.email,
                    date=today_str
                )
                pending_employees_list.append(dummy_emp)

        all_employees_combined = all_employees + pending_employees_list
        employee = None

        employee_i18n_list = {}
        for c in all_employees_combined:
            trans = load_employee_translated(c, language, company_id=active_company_id) or {}
            final_data = {
                "name": trans.get("name") or c.employee_name or "",
                "address": trans.get("address") or getattr(c, "address", "") or "",
                "city": trans.get("city") or getattr(c, "city", "") or "",
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or ""
            }
            
            for fk, fv in final_data.items():
                if fv is None:
                    final_data[fk] = ""
                    
            key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
            employee_i18n_list[key] = final_data

        flash('החיפוש נוקה בהצלחה!', 'info')

        return render_template(
            'contact_form.html',
            months=months,
            years=years,
            days_data=days_data,
            employees=[],  
            all_employees=all_employees_combined,
            all_company_employees=all_employees_combined,
            form_data=form_data if form_data else {},  
            employee=employee,
            employee_i18n={},                              
            employee_i18n_list=employee_i18n_list,          
            employee_data=session.get('employee_data', {}),  
            car_data=session['car_data'],
            city_data=session['city_data'],
            car_form_fields=session['car_form_fields'],
            city_form_fields=session['city_form_fields'],
            company=load_company_translated(company_obj, language) if company_obj else {},
            company_db=company_obj,
            language=language,
            today=today_str,
            input_date_val=today_str,
            formatted_car_value='',
            formatted_monthly_city_top_tax=''
        )
    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בניקוי נתוני החיפוש", "danger")
        return redirect(url_for('index'))


# ----------------------
# Get Employee Details: Form Page
# ----------------------

@app.route('/get_employee_details/<employee_id>/<string:month>/<string:year>')
@login_required
def get_employee_details(employee_id, month, year):
    if not employee_id or employee_id.strip() == "":
        return jsonify({'error': 'Invalid employee_id'}), 400
    try:
        int(employee_id)
    except ValueError:
        return jsonify({'error': 'Employee ID must be an integer'}), 400

    data = build_employee_details(get_active_company_id(), employee_id, month, year)
    if data is None:
        return jsonify({'error': 'Employee not found'}), 404
    return jsonify(data)




# ----------------------
# Get Tax Credit Simulator: Form Page
# ----------------------

@app.route('/tax_credit_simulator', methods=['GET', 'POST'])
@login_required
def tax_credit_simulator():
    tax_credits = 0.0

    # ----------------- COMPANY CONTEXT (אבטחת מולטי-חברה כמו בחשבוניות שלכם) -----------------
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

    if request.method == 'POST':
        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        # א. קליטת נתוני הבסיס והרדיו
        is_israeli_resident = request.form.get('is_israeli_resident') == 'on'
        gender = request.form.get('gender', '')
        is_teenager = request.form.get('is_teenager') == 'on'
        
        # תוקן: קליטת כפתור הרדיו של מצב משפחתי לפי ה-value (yes/no) שהגדרתם ב-HTML
        is_married = request.form.get('is_married') == 'yes'
        is_special_situation = request.form.get('is_special_situation') == 'on'
        has_children = request.form.get('has_children') == 'yes'
        married_to_widower = request.form.get('married_to_widower') == 'yes'
        single_parent = request.form.get('single_parent') == 'yes'
        separate_household = request.form.get('separate_household') == 'yes'
        single_parent_no_spouse = request.form.get('single_parent_no_spouse') == 'yes'
        paying_child_support = request.form.get('paying_child_support') == 'yes'
        remarried_paying_alimony = request.form.get('remarried_paying_alimony') == 'yes'
        
        # קליטת שדות הניוד החדשים מה-HTML שלכם
        transfer_points_newborn = request.form.get('transfer_points_newborn') == 'yes'
        points_from_prev_year = request.form.get('points_from_prev_year') == 'yes'

        # ב. קליטת שדות הילדים - בלוק 1: ילדים שבחזקת ההורה
        newborn_count = int(request.form.get('newborn_count') or 0)
        age_1_count = int(request.form.get('age_1_count') or 0)
        age_2_count = int(request.form.get('age_2_count') or 0)
        age_3_count = int(request.form.get('age_3_count') or 0)
        age_4_count = int(request.form.get('age_4_count') or 0)
        age_5_count = int(request.form.get('age_5_count') or 0)
        age_6_17_count = int(request.form.get('age_6_17_count') or 0)
        age_18_count = int(request.form.get('age_18_count') or 0)

        # ג. קליטת שדות הילדים - בלוק 2: ילדים שאינם בחזקת ההורה (תוקן לסנכרון שמות השדות החדשים no_custody_...)
        no_custody_newborn_count = int(request.form.get('no_custody_newborn_count') or 0)
        no_custody_age_1_count = int(request.form.get('no_custody_age_1_count') or 0)
        no_custody_age_2_count = int(request.form.get('no_custody_age_2_count') or 0)
        no_custody_age_3_count = int(request.form.get('no_custody_age_3_count') or 0)
        no_custody_age_4_count = int(request.form.get('no_custody_age_4_count') or 0)
        no_custody_age_5_count = int(request.form.get('no_custody_age_5_count') or 0)
        no_custody_age_6_17_count = int(request.form.get('no_custody_age_6_17_count') or 0)

        # ---------------------------------------------------------------------------
        # ד. לוגיקת חישוב נקודות הזיכוי
        # ---------------------------------------------------------------------------
        if is_israeli_resident: tax_credits += 2.25
        if gender == 'female': tax_credits += 0.5
        if is_teenager: tax_credits += 1.0
        if is_special_situation: tax_credits += 1.0
        if has_children: tax_credits += 0.5
        if married_to_widower: tax_credits += 1.0
        if single_parent: tax_credits += 1.0
        if separate_household: tax_credits += 1.0
        if single_parent_no_spouse: tax_credits += 1.0
        if paying_child_support: tax_credits += 1.0
        if remarried_paying_alimony: tax_credits += 1.0
        if points_from_prev_year: tax_credits += 1.0  # תוספת בגין ניוד נקודות משנה קודמת

        # חישוב נקודות - בלוק 1: ילדים שבחזקת ההורה
        tax_credits += 2.5 * newborn_count
        tax_credits += 4.5 * age_1_count
        tax_credits += 4.5 * age_2_count
        tax_credits += 3.5 * age_3_count
        tax_credits += 2.5 * age_4_count
        tax_credits += 2.5 * age_5_count
        tax_credits += 2.0 * age_6_17_count
        tax_credits += 0.5 * age_18_count

        # חישוב נקודות - בלוק 2: ילדים שאינם בחזקת ההורה (מסונכרן ומחושב במדויק לפי החוק בארץ)
        tax_credits += 2.5 * no_custody_newborn_count
        tax_credits += 4.5 * no_custody_age_1_count
        tax_credits += 4.5 * no_custody_age_2_count
        tax_credits += 3.5 * no_custody_age_3_count
        tax_credits += 2.5 * no_custody_age_4_count
        tax_credits += 2.5 * no_custody_age_5_count
        tax_credits += 1.0 * no_custody_age_6_17_count


        new_tax_credit = TaxCredit(
            is_israeli_resident=is_israeli_resident,
            gender=gender,
            is_teenager=is_teenager,
            is_married=is_married,
            is_special_situation=is_special_situation,
            has_children=has_children,
            married_to_widower=married_to_widower,
            single_parent=single_parent,
            separate_household=separate_household,
            single_parent_no_spouse=single_parent_no_spouse,
            paying_child_support=paying_child_support,
            remarried_paying_alimony=remarried_paying_alimony,
            newborn_count=newborn_count,
            age_1_count=age_1_count,
            age_2_count=age_2_count,
            age_3_count=age_3_count,
            age_4_count=age_4_count,
            age_5_count=age_5_count,
            age_6_17_count=age_6_17_count,
            age_18_count=age_18_count,
            children_not_in_custody=no_custody_newborn_count + no_custody_age_1_count + no_custody_age_2_count + no_custody_age_3_count,
            total_tax_credits=tax_credits
        )

        db.session.add(new_tax_credit)
        db.session.commit()

        flash('נקודות הזיכוי חושבו ונשמרו בהצלחה במערכת!', 'success')
        # תוקן: מציג את התוצאה הסופית בצורה נקייה ויפה (למשל 4.25 במקום מספרים ארוכים)
        return render_template('tax_credit_simulator.html', tax_credits=f"{tax_credits:.2f}".rstrip('0').rstrip('.'))

    # בקשת GET - הצגת טופס נקי לחלוטין
    return render_template('tax_credit_simulator.html', tax_credits='')


# --------------------
# Clear Employee Form Data
# ----------------------

@app.route('/clear_display', methods=['POST'])
@login_required
def clear_data():
    for key in ('employee', 'employee_id', 'selected_employee_id', 'owner_employee_id',
                'selected_month', 'selected_year', 'employee_data', 'hours_table',
                'month_result', 'form_data', 'last_search_results', 'ctx_key'):
        session.pop(key, None)

    session['force_clear'] = True

    flash("הטופס נוקה בהצלחה", "info")
    return redirect(url_for('index'))

# ----------------------
# Tax Form Results 
# ----------------------

@app.route('/tax_form_results', methods=['GET', 'POST'])
@login_required
def tax_form_results():
    # 1. שליפת נתוני זמן מה-Session
    s_month = session.get('selected_month')
    s_year = session.get('selected_year')
    s_month_str = str(s_month).zfill(2) if s_month else ""
    s_year_str = str(s_year) if s_year else ""

    # 2. שליפת רשומה אחת לכל עובד (תיקון DISTINCT)
    sub = db.session.query(
        HoursData.employee_id,
        db.func.min(HoursData.id).label("min_id")
    ).filter_by(
        employeeMonth=s_month_str,
        employeeYear=s_year_str
    ).group_by(HoursData.employee_id).subquery()

    summary_records = HoursData.query.join(
        sub, HoursData.id == sub.c.min_id
    ).all()

    # 3. עיבוד נתונים לתצוגה
    for entry in summary_records:

        # הזרקת row_data
        if entry.row_data and isinstance(entry.row_data, dict):
            for key, value in entry.row_data.items():
                setattr(entry, key, value)

        entry.month_display = f"{entry.employeeMonth}/{entry.employeeYear}"

        # שליפת כל ימי העבודה של העובד
        entry.hours_data = HoursData.query.filter_by(
            employee_id=entry.employee_id,
            employeeMonth=s_month_str,
            employeeYear=s_year_str
        ).all()

    # 4. POST - עדכון פרטי עובד
    if request.method == 'POST':
        try:
            target_id = request.form.get('employee_id')

            if not target_id:
                flash("לא נבחר עובד לעדכון", "danger")
                return redirect(url_for('tax_form_results'))

            emp = EmployeeData.query.get(target_id)

            if not emp:
                flash("העובד לא נמצא", "danger")
                return redirect(url_for('tax_form_results'))

            # עדכון שדות
            for k, v in request.form.items():
                if hasattr(emp, k) and k != "employee_id":
                    setattr(emp, k, v.strip() if v.strip() != "" else None)

            db.session.commit()
            flash("נתוני העובד עודכנו בהצלחה", "success")
            return redirect(url_for('tax_form_results'))

        except Exception as e:
            db.session.rollback()
            print(f"Error updating employee: {e}")
            flash("שגיאה בעדכון העובד", "danger")

    return render_template(
        'tax_form_results.html',
        employees=summary_records,
        selected_month=s_month,
        selected_year=s_year
    )





# ==================================================================
# ALL FORMS 101-102-106- (Multi-Tenant)
# ==================================================================

# Profile helper: newest NON-EMPTY value of each field across all rows of one employee
def profile_getter(company_id, local_id):
    rows = (EmployeeData.query.filter_by(company_id=company_id, local_id=local_id)
            .order_by(EmployeeData.id.desc()).all())

    def pick(*names):
        for r in rows:
            for n in names:
                v = getattr(r, n, None)
                if v not in (None, ''):
                    return v
        return ""
    return pick

# --------------------
#  Form 101 Data
# ----------------------

@app.route('/form_101', methods=['GET', 'POST'])
@login_required
def form_101():
    try:
        language = get_lang()
        months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני',
                  'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
        years = list(range(2020, 2041))

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        safe_company_id = int(active_company_id)

        company_obj = db.session.get(Company, safe_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------

        # one entry per employee (latest row), this company only
        all_rows = (EmployeeData.query.filter_by(company_id=safe_company_id)
                    .order_by(EmployeeData.id.desc()).all())
        latest_by_emp = {}
        for r in all_rows:
            key = r.local_id if r.local_id is not None else r.id
            latest_by_emp.setdefault(key, r)
        employees_list = sorted(latest_by_emp.values(), key=lambda e: e.employee_name or "")

        today = datetime.today()
        default_month = f"{today.month:02d}"
        default_year = str(today.year)

        # employee: explicit choice (URL / form) first, else what index selected (read only)
        clear_flag = session.pop('clear_101', False) if request.method == 'GET' else False
        explicit_emp = request.args.get('employee_id') or request.form.get('employee_id')
        session_emp = None if clear_flag else (session.get('owner_employee_id')
                                                or session.get('selected_employee_id'))
        selected_employee_id = explicit_emp or session_emp

        # month / year: READ ONLY (this page never changes the month of the other pages)
        selected_month = (request.args.get('month') or request.form.get('month')
                          or session.get('selected_month', default_month))
        selected_year = (request.args.get('year') or request.form.get('year')
                         or session.get('selected_year', default_year))

        selected_month_str = str(selected_month).zfill(2)
        selected_year_str = str(selected_year)

        try:
            selected_employee_id_int = (int(selected_employee_id)
                                        if selected_employee_id and str(selected_employee_id).strip() not in ['', 'None', '0']
                                        else None)
        except (ValueError, TypeError):
            selected_employee_id_int = None

        # employee by local_id (then id), this company only
        employee = None
        if selected_employee_id_int:
            employee = resolve_employee(safe_company_id, selected_employee_id_int)

        emp_local_id = None
        if employee:
            emp_local_id = employee.local_id if employee.local_id is not None else employee.id

        # FIX: write to the shared session ONLY when the user explicitly chose an employee here,
        # and never an empty value. Month and year are never written.
        if employee and explicit_emp:
            session['selected_employee_id'] = str(emp_local_id)
            session['owner_employee_id'] = str(emp_local_id)

        # temporary check: remove these lines once the problem is gone
        print("FORM101:", "explicit=", explicit_emp, "| session emp=", session_emp,
              "| month/year=", selected_month_str, selected_year_str, "| employee=", emp_local_id)

        form_data = {
            'reportMonth': selected_month_str,
            'reportYear': selected_year_str,
            'companyName': company_obj.name or "",
            'taxFileNumber': company_obj.deduction_file or "",
            'companyAddress': company_obj.address or "",
            'companyPhone': company_obj.phone or "",
            'company_email': company_obj.email or ""
        }

        if employee:
            pick = profile_getter(safe_company_id, emp_local_id)

            full_address = str(pick('address')).strip()
            address_parts = full_address.rsplit(' ', 1) if ' ' in full_address else [full_address, ""]

            db_date = pick('date_of_birth')
            formatted_birthday = ""
            if db_date:
                if hasattr(db_date, 'strftime'):
                    formatted_birthday = db_date.strftime('%Y-%m-%d')
                elif isinstance(db_date, str):
                    formatted_birthday = db_date.strip()

            form_data.update({
                'employee_id': emp_local_id,
                'employee_name': employee.employee_name or pick('employee_name'),
                'id_number': pick('id_number'),
                'address': address_parts[0],
                'house_number': address_parts[1] if len(address_parts) > 1 else "",
                'city': pick('city'),
                'zip_code': pick('postal_code', 'zip_code'),
                'aliyah_date': pick('aliyah_date'),
                'mobile_phone': pick('mobile_phone', 'phone'),
                'home_phone': pick('home_phone'),
                'email': pick('email'),
                'marital_status': pick('marital_status'),
                'gender_status': pick('gender_status'),
                'resident_status': pick('resident_status'),
                'kibbutz_member_status': pick('kibbutz_member_status'),
                'hmo_member': pick('hmo_member'),
                'date_of_birth': formatted_birthday,
                'date': today.strftime('%d/%m/%Y')
            })

        if request.method == 'POST':
            if selected_employee_id_int and not employee:
                flash("שגיאה: אין הרשאה להגיש טופס עבור עובד זה", "danger")
                return redirect(url_for('unauthorized'))

            form_payload = request.form.to_dict()
            form_data.update(form_payload)

            # the posted form must not override the verified employee
            if employee:
                form_data['employee_id'] = emp_local_id
                form_data['employee_name'] = employee.employee_name or form_data.get('employee_name', '')

            signature_b64 = request.form.get('signature_data')
            if signature_b64:
                form_data['signature_data'] = signature_b64

            company_email = request.form.get('company_email')
            employee_email = request.form.get('employee_email')

            if company_email and employee_email:
                try:
                    full_form_html = render_template('form_101.html', form_data=form_data, is_pdf=False)

                    msg = Message(
                        subject=f"Tofes 101 Signed - {form_data.get('employee_name')}",
                        recipients=[company_email, employee_email],
                        html=full_form_html
                    )

                    mail.send(msg)
                    print("📧 Full Form Email Sent (HTML Only)")
                except Exception as mail_e:
                    print(f"❌ Mail Error: {mail_e}")

            new_submission = Tofes101Submission(
                company_id=safe_company_id,
                employee_id=emp_local_id if emp_local_id else 0,
                month=selected_month_str,
                year=selected_year_str,
                data_json=form_data,
                submission_date=datetime.utcnow()
            )
            db.session.add(new_submission)
            db.session.commit()

            flash("Form sent successfully!", "success")
            return redirect(url_for('form_101', employee_id=emp_local_id or '',
                                    month=selected_month_str, year=selected_year_str))

        return render_template('form_101.html',
                               company=company_obj,
                               company_db=company_obj,
                               employees=employees_list,
                               months=months,
                               years=years,
                               selected_employee_id=str(emp_local_id) if emp_local_id else '',
                               employeeMonth=selected_month_str,
                               employeeYear=selected_year_str,
                               form_data=form_data)

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return f"שגיאה במערכת: {str(e)}", 500

# --------------------
# Clear Form 101 Data
# ----------------------

@app.route('/clear_form101', methods=['POST'])
@login_required
def clear_form101():
    try:
        for key in ('employee_id', 'selected_employee_id', 'owner_employee_id', 'form_data'):
            session.pop(key, None)

        session['clear_101'] = True      # now read by form_101

        db.session.close()

        flash('הטופס נוקה בהצלחה!', 'info')
        return redirect(url_for('form_101'))

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"⚠️ Warning inside clear_form101 session purge: {e}")
        return redirect(url_for('form_101'))




# --------------------
#  Fix Form 102 Number Format Data
# ----------------------

# ===== עיגול מספרים לפי חוק =====
def round_form_number(val):
    if not val:
        return ""
    try:
        cleaned = str(val).replace(",", "").strip()
        num = float(cleaned)
        return f"{round(num):,}"
    except:
        return ""

# --------------------
#  Form 102 Form Data
# --------------------

@app.route('/form_102', methods=['GET', 'POST'])
@login_required
def form_102():
    try:
        now = datetime.now()
        months = range(1, 13)
        years = range(now.year - 2, now.year + 3)

        # ----------------- COMPANY CONTEXT -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        safe_company_id = int(active_company_id)

        company_obj = db.session.get(Company, safe_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------

        # CHANGE 1: employees of THIS company only, one entry per employee (latest row)
        all_rows = (EmployeeData.query.filter_by(company_id=safe_company_id)
                    .order_by(EmployeeData.id.desc()).all())
        latest_by_emp = {}
        for r in all_rows:
            key = r.local_id if r.local_id is not None else r.id
            latest_by_emp.setdefault(key, r)
        employees = sorted(latest_by_emp.values(), key=lambda e: e.employee_name or "")

        # month / year
        selected_month = int(request.args.get('month') or session.get('selected_month', now.month))
        selected_year = int(request.args.get('year') or session.get('selected_year', now.year))
        s_month_str = str(selected_month).zfill(2)
        s_year_str = str(selected_year)

        session.update({'selected_month': selected_month, 'selected_year': selected_year})

        # CHANGE 2: AJAX lookup (local_id first, then id, then id_number, same company only)
        if request.method == 'GET' and request.args.get('action') == 'get_employee_data':
            employee_id = request.args.get('employee_id', '').strip()

            valid_emp = None
            if employee_id.isdigit():
                n = int(employee_id)
                valid_emp = (EmployeeData.query.filter_by(company_id=safe_company_id, local_id=n)
                             .order_by(EmployeeData.id.desc()).first()
                             or EmployeeData.query.filter_by(company_id=safe_company_id, id=n).first())
            if not valid_emp and employee_id:
                valid_emp = (EmployeeData.query.filter_by(company_id=safe_company_id, id_number=employee_id)
                             .order_by(EmployeeData.id.desc()).first())

            if not valid_emp:
                return jsonify({'success': False, 'message': 'Unauthorized'}), 403

            emp_key = str(valid_emp.local_id if valid_emp.local_id is not None else valid_emp.id)
            details = get_employee_details(emp_key, s_month_str, s_year_str)
            if details:
                session['employee_data'] = details
                session['employee_id'] = valid_emp.id_number
            return jsonify({'success': bool(details), **(details or {})})

        # CHANGE 3: counters + data source (same as index / save_hours)
        processed_employee_ids = set()
        total_gross = total_income_tax = total_ni_employee = total_health = 0.0
        total_study_fund_deductions = emp_pension = self_pension = 0.0
        pension_val = comp_val = disability_val = study_fund_val = 0.0
        regular_salary = reduced_salary = 0.0
        regular_count = reduced_count = 0

        def cn(val):
            try:
                return float(str(val).replace("₪", "").replace(",", "").strip())
            except:
                return 0.0

        month_key = f"{s_year_str}-{s_month_str}"
        date_key = f"{s_month_str}/{s_year_str}"
        db_cols = [c for c in EmployeeData.__table__.columns.keys() if c not in ('id', 'company_id')]

        for emp_key, emp_row in latest_by_emp.items():
            all_hours = load_hours(safe_company_id, emp_key) or {}
            tax_data = ((all_hours.get(month_key) or {}).get('hours_table') or {}).get('tax') or {}

            if not tax_data:
                month_row = EmployeeData.query.filter_by(
                    company_id=safe_company_id, local_id=emp_key, date=date_key).first()
                if not month_row:
                    continue
                tax_data = {c: getattr(month_row, c, None) for c in db_cols}

            processed_employee_ids.add(emp_key)
            gross_taxable = cn(tax_data.get("gross_taxable", 0))

            dob = tax_data.get("date_of_birth") or getattr(emp_row, 'date_of_birth', None)
            age = 0
            if dob:
                try:
                    if hasattr(dob, 'year'):
                        birth_year = dob.year
                    else:
                        birth_year = None
                        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                            try:
                                birth_year = datetime.strptime(str(dob), fmt).year
                                break
                            except ValueError:
                                pass
                    if birth_year:
                        age = selected_year - birth_year
                except Exception:
                    pass

            if age < 18 or age >= 67:
                reduced_salary += gross_taxable
                reduced_count += 1
            else:
                regular_salary += gross_taxable
                regular_count += 1

            total_gross += gross_taxable
            total_income_tax += cn(tax_data.get("income_tax", 0))
            total_ni_employee += cn(tax_data.get("national_insurance_deductions", 0))
            total_health += cn(tax_data.get("health_insurance_deductions", 0))
            total_study_fund_deductions += cn(tax_data.get("study_fund_deductions", 0))
            emp_pension += cn(tax_data.get("employee_pension_fund", 0))
            self_pension += cn(tax_data.get("self_employed_pension_fund", 0))
            pension_val += cn(tax_data.get("pension_fund", 0))
            comp_val += cn(tax_data.get("compensation", 0))
            disability_val += cn(tax_data.get("disability", 0))
            study_fund_val += cn(tax_data.get("study_fund", 0))

        # final calculations (unchanged)
        final_emp_pension_combined = emp_pension + self_pension
        final_emp_deductions_total = (total_ni_employee + total_health +
                                      final_emp_pension_combined + total_study_fund_deductions)
        total_employer_pension_combined = (pension_val + comp_val + disability_val + study_fund_val)
        final_totals_paid = final_emp_deductions_total + total_employer_pension_combined

        # form_data for the template (unchanged)
        form_data = {
            'NumEmployees': len(processed_employee_ids),
            'regular_salary': int(round(regular_salary)),
            'reduced_salary': int(round(reduced_salary)),
            'regular_count': regular_count,
            'reduced_count': reduced_count,
            'totalGrossSalary': int(round(total_gross)),
            'totalIncomeTax': int(round(total_income_tax)),
            'totalNationalInsurance': int(round(total_ni_employee)),
            'totalHealthInsurance': int(round(total_health)),
            'totalProvidentDeduction': int(round(total_study_fund_deductions)),
            'totalPensionDeduction': int(round(final_emp_pension_combined)),
            'employerPension': int(round(total_employer_pension_combined)),
            'finalTotalsPaid': int(round(final_totals_paid)),
            'finalTotalIncomeTax': int(round(total_income_tax)),
            'totalEmpDeductions': int(round(final_emp_deductions_total)),
            'reportMonth': s_month_str,
            'reportYear': s_year_str,
            'companyName': company_obj.name or "",
            'taxFileNumber': company_obj.deduction_file or "",
            'companyAddress': company_obj.address or ""
        }

        # POST: save (unchanged)
        if request.method == 'POST':
            incoming_data = request.form.to_dict()

            if 'employee_id' in incoming_data:
                session['employee_id'] = incoming_data['employee_id']

            report = Form102Report.query.filter_by(
                company_id=safe_company_id,
                report_month=s_month_str,
                report_year=s_year_str
            ).first()

            if report:
                report.data_json = incoming_data
            else:
                report = Form102Report(
                    company_id=safe_company_id,
                    report_month=s_month_str,
                    report_year=s_year_str,
                    data_json=incoming_data
                )
                db.session.add(report)

            db.session.commit()
            flash('טופס 102 נשמר בהצלחה בבסיס הנתונים!', 'success')
            return redirect(url_for('form_102', month=selected_month, year=selected_year))

        return render_template(
            'form_102.html',
            months=months,
            years=years,
            selected_month=selected_month,
            selected_year=selected_year,
            form_data=form_data,
            employees=employees
        )

    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error inside form_102 master handler: {e}")
        flash("שגיאה בטעינת או שמירת טופס 102", "danger")
        return redirect(url_for('index'))

# --------------------
#  Save Form 102 Report Data To Folder
# ---------------------- 

@app.route("/save_form102_report", methods=["POST"])
@login_required
def save_form102_report():
    try:
        data = request.json
        if not data:
            return jsonify(success=False, message="No data received"), 400

        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return jsonify(success=False, message="שגיאה: אין חברה פעילה משויכת למשתמש"), 401

        safe_company_id = int(active_company_id)

        today = datetime.today()
        emp = data.get("employer", {}) or {}

        raw_month = emp.get("reportMonth") or data.get("reportMonth") or f"{today.month:02d}"
        month = str(raw_month).strip().zfill(2)

        raw_year = emp.get("reportYear") or data.get("reportYear") or str(today.year)
        year = str(raw_year).strip()

        xml_content = data.get("xml", "")

        report = Form102Report.query.filter_by(
            company_id=safe_company_id,
            report_month=month,
            report_year=year
        ).first()

        if report:
            report.data_json = data
            report.xml_content = xml_content
            if hasattr(report, 'employee_id'): report.employee_id = 0
            if hasattr(report, 'tax_response'): report.tax_response = ""
            if hasattr(report, 'company_id'): report.company_id = safe_company_id
        else:
            report = Form102Report(
                company_id=safe_company_id,
                report_month=month,
                report_year=year,
                data_json=data,
                xml_content=xml_content,
                employee_id=0,
                tax_response=""
            )
            db.session.add(report)

        db.session.commit()

        # CHANGE 4: needed by send_102_to_tax
        session['last_report_id'] = report.id

        return jsonify({"success": True, "message": "נשמר ב-DB בהצלחה!"})

    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"🚨 SQL ERROR IN save_form102_report: {e}")
        return jsonify(success=False, message=str(e)), 500


# --------------------
#   Convert Fields Name From App To Tax Office Name Report Form 102 Form Data
# ----------------------

FIELD_MAP_102 = {

    # ===== פרטי מעסיק =====
    "companyName": ["companyName"],
    "companyAddress": ["companyAddress"],
    "taxFileNumber": ["taxFileNumber"],
    "reportMonth": ["reportMonth"],
    "reportYear": ["reportYear"],

    # ===== שורה 216 =====
    "totalIncomeTax": ["totalIncomeTax"],
    "totalGrossSalary": ["totalGrossSalary"],
    "NumEmployees": ["NumEmployees"],

    # ===== שורה 254 =====
    "totalPensionVal": ["totalPensionVal"],
    "totalCompVal": ["totalCompVal"],
    "NumEmployeesNonSalary": ["NumEmployeesNonSalary"],

    # ===== שורה 291 =====
    "totalEmpDeductions": ["totalEmpDeductions"],
    "totalStudyFundVal": ["totalStudyFundVal"],

    # ===== שורה 329 =====
    "employerPension": ["employerPension"],
    "totalPensionDeduction": ["totalPensionDeduction"],
    "NumEmployeesCharges": ["NumEmployeesCharges"],

    # ===== שורה 367 =====
    "totalNationalInsurance": ["totalNationalInsurance"],
    "totalHealthInsurance": ["totalHealthInsurance"],
    "NumEmployeesCharges2": ["NumEmployeesCharges2"],

    # ===== שורה 404 =====
    "finalTotalIncomeTax": ["finalTotalIncomeTax"],
}

# --------------------
#  Form 102 download_xml_102 Data
# ----------------------

@app.route('/download_xml_102/<year>/<month>')
@login_required
def download_xml_102(year, month):
    try:
        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return "Unauthorized Workspace Access", 401

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        month_str = str(month).strip().zfill(2)
        year_str = str(year).strip()

        report = Form102Report.query.filter_by(
            company_id=safe_company_id,
            report_month=month_str,
            report_year=year_str
        ).first()

        if not report or not report.xml_content:
            return f"""
            <script>
                alert('שגיאה: לא נמצא דוח XML שמור לחודש {month_str}/{year_str}');
                window.history.back();
            </script>
            """, 404

        # FIX: lock this report for the send step and keep the screen on the same month
        session['last_report_id'] = report.id
        session['selected_month'] = month_str
        session['selected_year'] = year_str

        xml_data = report.xml_content or ""

        filename = f"form102_{month_str}_{year_str}.xml"
        return Response(
            xml_data,
            mimetype='application/xml',
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error inside download_xml_102 master handler: {e}")
        return "Internal Server Error", 500

# --------------------
# Send Form 102 To Tax Office Requst Data
# ----------------------

@app.route('/send_102_to_tax', methods=['POST'])
@login_required
def send_102_to_tax():
    try:
        # 1. permissions (owner only, unchanged)
        if not session.get("owner_access"):
            flash("⛔ רק בעלים יכול לשדר לרשות המיסים.", "danger")
            return redirect(url_for('form_102'))

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אימות סביבת עבודה נכשל. השידור נחסם.", "danger")
            return redirect(url_for('form_102'))

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        # 2. load the saved report (this company only)
        report_id = session.get("last_report_id")
        if not report_id:
            flash("דוח לא נמצא בזיכרון — אנא לחץ על 'הורד XML' קודם כדי לנעול את הנתונים.", "warning")
            return redirect(url_for('form_102'))

        try:
            report_id_int = int(report_id)
        except (ValueError, TypeError):
            report_id_int = 0

        report = Form102Report.query.filter_by(
            id=report_id_int,
            company_id=safe_company_id
        ).first()

        if not report or not report.xml_content:
            flash('הנתונים ב-DB פגומים, לא קיימים או שייכים לחברה אחרת — צור את הדו"ח מחדש.', "danger")
            return redirect(url_for('form_102'))

        # FIX 1: the report must be the month shown on screen
        screen_month = str(session.get('selected_month', '')).zfill(2)
        screen_year = str(session.get('selected_year', ''))
        if (screen_month != str(report.report_month).zfill(2)) or (screen_year != str(report.report_year)):
            flash(f"הדוח השמור הוא לחודש {report.report_month}/{report.report_year} "
                  f"ולא לחודש שמוצג ({screen_month}/{screen_year}). שמור והורד XML לחודש הנכון.", "warning")
            return redirect(url_for('form_102'))

        # FIX 2: do not send the same report twice
        if getattr(report, 'is_submitted', False):
            flash("דוח זה כבר שודר בעבר. כדי לשדר שוב, צור דוח חדש.", "warning")
            return redirect(url_for('form_102'))

        xml_data = report.xml_content.encode('utf-8')

        # 3. transmit settings
        TAX_URL = "https://tax.gov.il/api/102/upload"   # replace with the real production URL

        if "RENDER" in os.environ:
            base_certs_dir = os.path.join(PERSISTENT_BASE, "companies", f"company_{safe_company_id}", "certs")
        else:
            base_certs_dir = os.path.join(BASE_DIR, "static", "companies", f"company_{safe_company_id}", "certs")

        CERT_FILE = os.path.join(base_certs_dir, "company_cert.pfx")

        # FIX 3: password from the environment first (the old value stays only as a fallback)
        CERT_PASS = os.environ.get("TAX_CERT_PASS") or "12345678"

        if not os.path.exists(CERT_FILE):
            flash("⛔ שגיאה: תעודה דיגיטלית חתומה (.pfx) לא נמצאה על הדיסק עבור חברה זו. אנא העלה תעודה חוקית תחילה.", "danger")
            return redirect(url_for('form_102'))

        # 4. transmit
        response = requests.post(
            TAX_URL,
            data=xml_data,
            cert=(CERT_FILE, CERT_PASS),
            headers={"Content-Type": "text/xml"},
            timeout=30
        )

        # 5. record the answer; FIX 4: mark as submitted ONLY on success
        report.tax_response = response.text
        if response.status_code == 200:
            report.is_submitted = True
        db.session.commit()

        if response.status_code == 200:
            flash("✅ השידור בוצע בהצלחה! התקבל אישור מרשות המיסים.", "success")
        else:
            flash(f"⚠️ השידור לא אושר (קוד {response.status_code}). הדוח לא סומן כשודר.", "warning")

        return redirect(url_for('form_102'))

    except requests.exceptions.Timeout:
        flash("❌ השידור נכשל: השרת של רשות המיסים לא הגיב בזמן. נסה שוב.", "danger")
        return redirect(url_for('form_102'))
    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
        import traceback
        traceback.print_exc()
        flash(f"❌ שגיאה קריטית בשידור: {str(e)}", "danger")
        return redirect(url_for('form_102'))

# --------------------
# Clear Form 102 Data
# ----------------------

@app.route('/clear_form102', methods=['POST'])
@login_required
def clear_form102():
    try:
        # דגל האיתות המקורי והחכם שלך עבור הפרונט-אנד
        session['clear_102'] = True
        
        #  הפיקס המנצח: מנקים מהסשן את שאריות העובד הקודם כדי להבטיח ניקוי ויזואלי מוחלט של הטופס על המסך!
        session.pop('employee_id', None)
        session.pop('employee_data', None)
        
        # Render: סגירה יזומה של ה-session הרגיל לוודא חיבור נקי לחלוטין ומשאבים פנויים
        db.session.close()
        return redirect(url_for('form_102'))

    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"⚠️ Warning inside clear_form102 session purge: {e}")
        return redirect(url_for('form_102'))





# --------------------
#  Form B102 Form Data
# ----------------------

@app.route('/form_B102', methods=['GET', 'POST'])
@login_required
def form_B102():
    try:
        now = datetime.now()
        months = range(1, 13)
        years = range(now.year - 2, now.year + 3)

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        safe_company_id = int(active_company_id)

        company_obj = db.session.get(Company, safe_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------

        # CHANGE 1: one entry per employee (latest row), this company only
        all_rows = (EmployeeData.query.filter_by(company_id=safe_company_id)
                    .order_by(EmployeeData.id.desc()).all())
        latest_by_emp = {}
        for r in all_rows:
            key = r.local_id if r.local_id is not None else r.id
            latest_by_emp.setdefault(key, r)
        employees = sorted(latest_by_emp.values(), key=lambda e: e.employee_name or "")

        selected_month = int(request.args.get('month') or session.get('selected_month', now.month))
        selected_year = int(request.args.get('year') or session.get('selected_year', now.year))
        s_month_str = str(selected_month).zfill(2)
        s_year_str = str(selected_year)

        session.update({'selected_month': selected_month, 'selected_year': selected_year})

        # TOTALS
        processed_ids = set()
        t = {
            'gross': 0.0, 'tax': 0.0, 'ni': 0.0, 'health': 0.0, 'study': 0.0,
            'pension_emp': 0.0, 'pension_self': 0.0, 'pension_val': 0.0,
            'comp_val': 0.0, 'dis_val': 0.0, 'study_val': 0.0,
            'red_sal': 0.0, 'reg_sal': 0.0
        }
        red_count = reg_count = 0

        def cn(val):
            try:
                return float(str(val).replace("₪", "").replace(",", "").strip())
            except:
                return 0.0

        month_key = f"{s_year_str}-{s_month_str}"
        date_key = f"{s_month_str}/{s_year_str}"
        db_cols = [c for c in EmployeeData.__table__.columns.keys() if c not in ('id', 'company_id')]

        # CHANGE 2: same source as index / save_hours / form_102:
        # hours.json of each employee of THIS company, fallback = that employee's monthly row (MM/YYYY)
        for emp_key, emp_row in latest_by_emp.items():
            all_hours = load_hours(safe_company_id, emp_key) or {}
            tax_data = ((all_hours.get(month_key) or {}).get('hours_table') or {}).get('tax') or {}

            if not tax_data:
                month_row = EmployeeData.query.filter_by(
                    company_id=safe_company_id, local_id=emp_key, date=date_key).first()
                if not month_row:
                    continue            # nothing saved for this employee in this month
                tax_data = {c: getattr(month_row, c, None) for c in db_cols}

            processed_ids.add(emp_key)

            g_taxable = cn(tax_data.get("gross_taxable", 0))
            t['gross'] += g_taxable
            t['tax'] += cn(tax_data.get("income_tax", 0))
            t['ni'] += cn(tax_data.get("national_insurance_deductions", 0))
            t['health'] += cn(tax_data.get("health_insurance_deductions", 0))
            t['study'] += cn(tax_data.get("study_fund_deductions", 0))
            t['pension_emp'] += cn(tax_data.get("employee_pension_fund", 0))
            t['pension_self'] += cn(tax_data.get("self_employed_pension_fund", 0))
            t['pension_val'] += cn(tax_data.get("pension_fund", 0))
            t['comp_val'] += cn(tax_data.get("compensation", 0))
            t['dis_val'] += cn(tax_data.get("disability", 0))
            t['study_val'] += cn(tax_data.get("study_fund", 0))

            # age logic: age in the REPORT month (not today), several date formats
            dob = tax_data.get("date_of_birth") or getattr(emp_row, 'date_of_birth', None)
            age = 0
            if dob:
                try:
                    if hasattr(dob, 'year'):
                        by, bm = dob.year, dob.month
                    else:
                        by = bm = None
                        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                            try:
                                d = datetime.strptime(str(dob), fmt)
                                by, bm = d.year, d.month
                                break
                            except ValueError:
                                pass
                    if by:
                        age = selected_year - by - (1 if selected_month < bm else 0)
                except Exception:
                    pass

            if age < 18 or age >= 67:
                t['red_sal'] += g_taxable
                red_count += 1
            else:
                t['reg_sal'] += g_taxable
                reg_count += 1

        # summary (unchanged)
        final_pension = t['pension_emp'] + t['pension_self']
        total_employer_pension = t['pension_val'] + t['comp_val'] + t['dis_val'] + t['study_val']
        final_paid = t['ni'] + t['health'] + final_pension + t['study'] + total_employer_pension
        final_national_paid = t['ni'] + t['health']

        form_data = {
            'NumEmployees': len(processed_ids),
            'totalGrossSalary': round(t['gross'], 2),
            'totalIncomeTax': round(t['tax'], 2),
            'totalNationalInsurance': round(t['ni'], 2),
            'totalHealthInsurance': round(t['health'], 2),
            'totalProvidentDeduction': round(t['study'], 2),
            'totalPensionDeduction': round(final_pension, 2),
            'employerPension': round(total_employer_pension, 2),
            'finalTotalsPaid': round(final_paid, 2),
            'finalNationalTotalsPaid': round(final_national_paid, 2),
            'finalTotalIncomeTax': round(t['tax'], 2),
            'reportMonth': s_month_str,
            'reportYear': s_year_str,
            'companyName': company_obj.name or "",
            'taxFileNumber': company_obj.deduction_file or "",
            'companyAddress': company_obj.address or "",
            'regular_salary': round(t['reg_sal'], 2),
            'reduced_salary': round(t['red_sal'], 2),
            'regular_count': reg_count,
            'reduced_count': red_count
        }

        if request.method == 'POST':
            form_data.update(request.form.to_dict())
            session['form_data'] = form_data
            flash('נתוני טופס B102 עודכנו בהצלחה!', 'success')
            return redirect(url_for('form_B102', month=selected_month, year=selected_year))

        session['form_data'] = form_data

        return render_template('form_B102.html',
                               form_data=form_data,
                               company=company_obj,
                               company_db=company_obj,
                               employees=employees,
                               employeeMonth=s_month_str,
                               employeeYear=s_year_str,
                               months=months,
                               years=years)

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בטעינת טופס ב/102", "danger")
        return redirect(url_for('index'))

# --------------------
#  Save Form B102 Report Data To Folder
# ---------------------- 

@app.route("/save_formB102_report", methods=["POST"])
@login_required
def save_formB102_report():
    try:
        data = request.get_json(silent=True)
        if not data or 'employer' not in data:
            return jsonify(success=False, message="נתונים חסרים"), 400

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return jsonify(success=False, message="שגיאה: אין חברה פעילה משויכת למשתמש"), 401

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        employer = data.get("employer") or {}

        # 1. month / year: from the report, else from the selected month on screen
        today = datetime.today()
        month = str(employer.get("reportMonth") or session.get('selected_month') or today.month).strip().zfill(2)
        year = str(employer.get("reportYear") or session.get('selected_year') or today.year).strip()

        if not (month.isdigit() and 1 <= int(month) <= 12 and year.isdigit() and len(year) == 4):
            return jsonify(success=False, message="חודש או שנה לא תקינים"), 400

        employer["reportMonth"], employer["reportYear"] = month, year

        # 2. XML from FIELD_MAP_B102 (unchanged logic)
        root = ET.Element("FormB102_Data")
        for tax_field, app_keys in FIELD_MAP_B102.items():
            app_key = app_keys[0] if isinstance(app_keys, list) else app_keys
            val = employer.get(app_key, 0)
            ET.SubElement(root, tax_field).text = str(val)

        final_xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True)
        xml_text = final_xml_str.decode('utf-8')

        # 3. save to DB (isolated by company, month, year)
        report = FormB102Report.query.filter_by(
            company_id=safe_company_id,
            report_month=month,
            report_year=year
        ).first()

        if report:
            report.data_json = data
            report.xml_content = xml_text
            report.created_at = datetime.utcnow()
        else:
            report = FormB102Report(
                company_id=safe_company_id,
                report_month=month,
                report_year=year,
                data_json=data,
                xml_content=xml_text
            )
            db.session.add(report)

        db.session.commit()
        session["last_b102_id"] = report.id

        # 4. physical copies (non-fatal: the report is already saved in the DB)
        xml_file_name = None
        try:
            persistent_base = app.config.get("COMPANY_DIR") or (
                os.path.join(PERSISTENT_BASE, "companies") if "RENDER" in os.environ
                else os.path.join(BASE_DIR, "static", "companies"))
            base_dir = os.path.join(persistent_base, f"company_{safe_company_id}", "form_B102_reports")
            year_folder = os.path.join(base_dir, f"Form_B102_{year}")
            os.makedirs(year_folder, exist_ok=True)

            filename_base = f"FormB102_{month}_{year}"

            with open(os.path.join(year_folder, f"{filename_base}.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            with open(os.path.join(year_folder, f"{filename_base}.xml"), "w", encoding="utf-8") as f:
                f.write(xml_text)

            xml_file_name = f"{filename_base}.xml"
        except Exception as file_err:
            print(f"B102 file copy failed (DB save OK): {file_err}")

        return jsonify({
            "success": True,
            "message": "דוח B102 נשמר ב-DB" + (" ובשרת!" if xml_file_name else " (העתק הקבצים נכשל)"),
            "db_id": report.id,
            "xml_file": xml_file_name
        })

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"B102 Save Error: {e}")
        return jsonify(success=False, message=str(e)), 500

# --------------------
#  Submit Form B102 XML Data To Postgres Folder
# ----------------------

@app.route('/submitB102', methods=['POST'])
@login_required
def submit_form_B102():
    try:
        form_data = dict(session.get('form_data', {}) or {})
        form_data.update(request.form.to_dict())

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return "Unauthorized Workspace Access", 401

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        today = datetime.today()
        month = str(form_data.get('reportMonth') or session.get('selected_month') or today.month).zfill(2)
        year = str(form_data.get('reportYear') or session.get('selected_year') or today.year)
        form_data['reportMonth'], form_data['reportYear'] = month, year

        # XML: only valid tag names, no form tokens
        root = ET.Element("FormB102_Data")
        for k, v in form_data.items():
            tag = re.sub(r'[^A-Za-z0-9_]', '_', str(k))
            if not tag or tag[0].isdigit() or tag in ('csrf_token', 'signature_data'):
                continue
            ET.SubElement(root, tag).text = str(v)

        xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        report = FormB102Report.query.filter_by(
            company_id=safe_company_id, report_month=month, report_year=year).first()

        if report:
            report.data_json = form_data
            report.xml_content = xml_str.decode('utf-8')
        else:
            report = FormB102Report(
                company_id=safe_company_id, report_month=month, report_year=year,
                data_json=form_data, xml_content=xml_str.decode('utf-8'))
            db.session.add(report)

        db.session.commit()
        session["last_b102_id"] = report.id

        filename = f"FormB102_{month}_{year}.xml"
        return Response(xml_str, mimetype="application/xml",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        return f"Error: {str(e)}", 500

# --------------------
#   Convert Fields Name From App To Tax Office Name Report Form B102 Form Data
# ----------------------

# mapping between form field names (HTML) and model attributes (DB)
FIELD_MAP_B102 = {

    # ----- Employer info -----
    "companyName": ["company_name"],
    "taxFileNumber": ["taxFileNumber"],
    "reportMonth": ["selected_month"],
    "reportYear": ["selected_year"],

    # ----- Employee totals -----
    "NumEmployees": ["employee_count"],

    "regular_salary": ["regular_salary_hidden"],
    "reduced_salary": ["reduced_salary_hidden"],


    # שכר מעל תקרה לעובד (רק אם חישבת)
    "employeeAboveMaxSalary": ["employee_above_max"],

    # שכר מופחת למעסיק = שכר מופחת לעובד
    "employerReducedSalary": ["reduced_salary_hidden"],

    # שכר מעל תקרה למעסיק = כמו עובד
    "employerAboveMaxSalary": ["employee_above_max"],

    # ספירת עובדים רגילים / מופחתים
    "regular_count": ["regular_count_hidden"],
    "reducedEmployeeCount": ["reduced_count_hidden"],   

    # סה״כ שכר
    "total_salary": ["total_salary_hidden"],

    # ----- Original totals -----
    "totalGrossSalary": ["total_gross"],
    "totalIncomeTax": ["total_income_tax"],
    "totalNationalInsurance": ["total_ni_employee"],
    "totalHealthInsurance": ["total_health"],
    "totalEmpPension": ["emp_pension"],
    "totalSelfPension": ["self_pension"],
    "totalPensionDeduction": ["final_emp_pension_combined"],
    "totalProvidentDeduction": ["total_study_fund_deductions"],

    # ----- Employer contributions -----
    "totalPensionVal": ["pension_val"],
    "totalCompVal": ["comp_val"],
    "totalDisabilityVal": ["disability_val"],
    "employerPension": ["total_employer_pension_combined"],
    "totalStudyFundVal": ["study_fund_val"],

    # ----- Summary -----
    "totalEmpDeductions": ["final_emp_deductions_total"],
    "totalContributions": ["total_employer_contributions"],
    "finalTotalsPaid": ["finalTotalsPaid"],
    "finalNationalTotalsPaid": ["finalNationalTotalsPaid"],
    "finalTotalIncomeTax": ["final_totals_income_tax"],

    #  טור 4 שורה 8 = שורה 7
    "reducedEmployeeCount_total": ["finalTotalsPaid"],
}


# --------------------
# Send Form B102 To Tax Office Requst Data
# ----------------------

@app.route('/send_B102_to_tax', methods=['POST'])
@login_required
def send_B102_to_tax():
    try:
        # 1. permissions (owner only, unchanged)
        if not session.get("owner_access"):
            flash("⛔ רק בעלים יכול לשדר לרשות המיסים.", "danger")
            return redirect(url_for('form_B102'))

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אימות סביבת עבודה נכשל. השידור נחסם.", "danger")
            return redirect(url_for('form_B102'))

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        # 2. load the saved report (this company only)
        report_id = session.get("last_b102_id")
        if not report_id:
            flash("דוח B102 לא נמצא — אנא לחץ על 'הורד XML' קודם כדי לנעול את הנתונים.", "warning")
            return redirect(url_for('form_B102'))

        try:
            report_id_int = int(report_id)
        except (ValueError, TypeError):
            report_id_int = 0

        report = FormB102Report.query.filter_by(
            id=report_id_int,
            company_id=safe_company_id
        ).first()

        if not report or not report.xml_content:
            flash('הנתונים ב-DB פגומים, לא קיימים או שייכים לחברה אחרת — צור את הדו"ח מחדש.', "danger")
            return redirect(url_for('form_B102'))

        # FIX 1: the report must be the month shown on screen
        screen_month = str(session.get('selected_month', '')).zfill(2)
        screen_year = str(session.get('selected_year', ''))
        if (screen_month != str(report.report_month).zfill(2)) or (screen_year != str(report.report_year)):
            flash(f"הדוח השמור הוא לחודש {report.report_month}/{report.report_year} "
                  f"ולא לחודש שמוצג ({screen_month}/{screen_year}). שמור והורד XML לחודש הנכון.", "warning")
            return redirect(url_for('form_B102'))

        xml_data = report.xml_content.encode('utf-8')

        # 3. transmit settings
        TAX_URL = "https://tax.gov.il/api/B102/upload"

        # FIX 2: certificate per company (same layout as Form 102), not one shared file
        if "RENDER" in os.environ:
            base_certs_dir = os.path.join(PERSISTENT_BASE, "companies", f"company_{safe_company_id}", "certs")
        else:
            base_certs_dir = os.path.join(BASE_DIR, "static", "companies", f"company_{safe_company_id}", "certs")

        CERT_FILE = os.path.join(base_certs_dir, "company_cert.pfx")

        # FIX 3: password from the environment first (the old value stays only as a fallback)
        CERT_PASS = os.environ.get("B102_CERT_PASS") or "12345678"

        if not os.path.exists(CERT_FILE):
            flash("⛔ תעודה דיגיטלית (.pfx) לא נמצאה עבור חברה זו. אנא העלה תעודה חוקית תחילה.", "danger")
            return redirect(url_for('form_B102'))

        # 4. transmit
        response = requests.post(
            TAX_URL,
            data=xml_data,
            cert=(CERT_FILE, CERT_PASS),
            headers={"Content-Type": "text/xml"},
            timeout=30
        )

        # 5. record the answer; FIX 4: mark as submitted ONLY on success
        report.tax_response = response.text
        if response.status_code == 200:
            report.is_submitted = True
        db.session.commit()

        if response.status_code == 200:
            flash("✅ שידור B102 בוצע בהצלחה! התקבל אישור מרשות המיסים.", "success")
        else:
            flash(f"⚠️ השידור לא אושר (קוד {response.status_code}). הדוח לא סומן כשודר.", "warning")

        return redirect(url_for('form_B102'))

    except requests.exceptions.Timeout:
        flash("❌ השידור נכשל: השרת של רשות המיסים לא הגיב בזמן. נסה שוב.", "danger")
        return redirect(url_for('form_B102'))
    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"B102 Submission Error: {e}")
        flash(f"❌ שגיאה בשידור B102: {str(e)}", "danger")
        return redirect(url_for('form_B102'))

# --------------------
# Clear Form B102 Data
# ----------------------

@app.route('/clear_formB102', methods=['POST'])
@login_required
def clear_formB102():
    try:
        for key in ('form_data', 'last_b102_id'):
            session.pop(key, None)
        session['clear_B102'] = True

        db.session.close()
        return redirect(url_for('form_B102'))

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"⚠️ Warning inside clear_formB102 session purge: {e}")
        return redirect(url_for('form_B102'))


# --------------------
#  Form H102 Form Data
# ----------------------

# True  = income_tax in the payslip is already AFTER the Eilat/city benefit
# False = income_tax is BEFORE the benefit
CITY_BENEFIT_ALREADY_IN_INCOME_TAX = True


@app.route('/form_H102', methods=['GET', 'POST'])
@login_required
def form_H102():
    try:
        now = datetime.now()
        months = range(1, 13)
        years = range(now.year - 2, now.year + 3)

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        safe_company_id = int(active_company_id)

        company_obj = db.session.get(Company, safe_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------

        # FIX 1: one entry per employee (latest row), this company only
        all_rows = (EmployeeData.query.filter_by(company_id=safe_company_id)
                    .order_by(EmployeeData.id.desc()).all())
        latest_by_emp = {}
        for r in all_rows:
            key = r.local_id if r.local_id is not None else r.id
            latest_by_emp.setdefault(key, r)
        employees = sorted(latest_by_emp.values(), key=lambda e: e.employee_name or "")

        selected_month = int(request.args.get('month') or session.get('selected_month', now.month))
        selected_year = int(request.args.get('year') or session.get('selected_year', now.year))
        s_month_str = str(selected_month).zfill(2)
        s_year_str = str(selected_year)

        session.update({'selected_month': selected_month, 'selected_year': selected_year})

        # manual fields belong to ONE company + month + year
        ctx = f"{safe_company_id}:{s_month_str}:{s_year_str}"

        # --- POST: switch employee or save manual fields ---
        if request.method == 'POST':
            new_emp_id = (request.form.get('employee_id') or '').strip()
            current_sel = str(session.get('selected_employee_id') or '')

            if new_emp_id and new_emp_id != current_sel:
                # FIX 2: local_id first, same company only (same as index)
                valid_new_emp = resolve_employee(safe_company_id, new_emp_id)
                if not valid_new_emp:
                    flash('שגיאה: אין הרשאה לגשת לעובד זה', 'danger')
                    return redirect(url_for('unauthorized'))

                new_local = valid_new_emp.local_id if valid_new_emp.local_id is not None else valid_new_emp.id
                session['form_data'] = {}
                session['form_data_ctx'] = ctx
                session['selected_employee_id'] = str(new_local)
                session['owner_employee_id'] = str(new_local)
                return redirect(url_for('form_H102', month=selected_month, year=selected_year))

            session['form_data'] = request.form.to_dict()
            session['form_data_ctx'] = ctx
            flash('נתוני טופס H102 עודכנו בהצלחה!', 'success')
            return redirect(url_for('form_H102', month=selected_month, year=selected_year))

        # --- counters ---
        processed_employee_ids = set()
        total_gross = total_income_tax = total_ni_employee = total_health = 0.0
        total_study_fund_deductions = emp_pension = self_pension = 0.0
        pension_val = comp_val = disability_val = study_fund_val = 0.0
        regular_salary = reduced_salary = 0.0
        regular_count = reduced_count = 0
        city_benefit_total = 0.0
        city_benefit_count = 0

        def cn(val):
            try:
                return float(str(val).replace("₪", "").replace(",", "").strip())
            except:
                return 0.0

        month_key = f"{s_year_str}-{s_month_str}"
        date_key = f"{s_month_str}/{s_year_str}"
        db_cols = [c for c in EmployeeData.__table__.columns.keys() if c not in ('id', 'company_id')]

        # FIX 3: same source as index / save_hours / form_102 / B102
        for emp_key, emp_row in latest_by_emp.items():
            all_hours = load_hours(safe_company_id, emp_key) or {}
            tax_data = ((all_hours.get(month_key) or {}).get('hours_table') or {}).get('tax') or {}

            if not tax_data:
                month_row = EmployeeData.query.filter_by(
                    company_id=safe_company_id, local_id=emp_key, date=date_key).first()
                if not month_row:
                    continue
                tax_data = {c: getattr(month_row, c, None) for c in db_cols}

            processed_employee_ids.add(emp_key)
            g_taxable = cn(tax_data.get("gross_taxable", 0))

            # FIX 4: age in the REPORT month, several date formats
            dob = tax_data.get("date_of_birth") or getattr(emp_row, 'date_of_birth', None)
            age = 0
            if dob:
                try:
                    if hasattr(dob, 'year'):
                        by, bm = dob.year, dob.month
                    else:
                        by = bm = None
                        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                            try:
                                d = datetime.strptime(str(dob), fmt)
                                by, bm = d.year, d.month
                                break
                            except ValueError:
                                pass
                    if by:
                        age = selected_year - by - (1 if selected_month < bm else 0)
                except Exception:
                    pass

            if age < 18 or age >= 67:
                reduced_salary += g_taxable
                reduced_count += 1
            else:
                regular_salary += g_taxable
                regular_count += 1

            total_gross += g_taxable
            total_income_tax += cn(tax_data.get("income_tax", 0))
            total_ni_employee += cn(tax_data.get("national_insurance_deductions", 0))
            total_health += cn(tax_data.get("health_insurance_deductions", 0))
            total_study_fund_deductions += cn(tax_data.get("study_fund_deductions", 0))
            emp_pension += cn(tax_data.get("employee_pension_fund", 0))
            self_pension += cn(tax_data.get("self_employed_pension_fund", 0))
            pension_val += cn(tax_data.get("pension_fund", 0))
            comp_val += cn(tax_data.get("compensation", 0))
            disability_val += cn(tax_data.get("disability", 0))
            study_fund_val += cn(tax_data.get("study_fund", 0))

            # FIX 5: Eilat/city benefit = what each employee's payslip already calculated
            emp_city_benefit = cn(tax_data.get("final_city_tax_benefit", 0))
            if emp_city_benefit > 0:
                city_benefit_total += emp_city_benefit
                city_benefit_count += 1

        final_emp_pension_combined = emp_pension + self_pension
        total_employer_pension_combined = pension_val + comp_val + disability_val + study_fund_val
        final_totals_paid = (total_ni_employee + total_health + final_emp_pension_combined +
                             total_study_fund_deductions + total_employer_pension_combined)

        # ====== H102 Eilat: benefit taken from the payslips, not a flat 20% ======
        if CITY_BENEFIT_ALREADY_IN_INCOME_TAX:
            eilat_regular_tax = total_income_tax + city_benefit_total
            eilat_total_tax_after_benefit = total_income_tax
        else:
            eilat_regular_tax = total_income_tax
            eilat_total_tax_after_benefit = max(total_income_tax - city_benefit_total, 0.0)
        total_tax_all = eilat_total_tax_after_benefit

        # manual fields only for the same company + month + year
        saved_manual = session.get('form_data') if session.get('form_data_ctx') == ctx else {}
        form_data = dict(saved_manual or {})
        form_data.update({
            'NumEmployees': len(processed_employee_ids),
            'regular_salary_hidden': round(regular_salary, 2),
            'reduced_salary_hidden': round(reduced_salary, 2),
            'regular_count_hidden': regular_count,
            'reduced_count_hidden': reduced_count,
            'totalGrossSalary': round(total_gross, 2),
            'totalIncomeTax': round(total_income_tax, 2),
            'totalNationalInsurance': round(total_ni_employee, 2),
            'totalHealthInsurance': round(total_health, 2),
            'totalProvidentDeduction': round(total_study_fund_deductions, 2),
            'totalPensionDeduction': round(final_emp_pension_combined, 2),
            'employerPension': round(total_employer_pension_combined, 2),
            'finalTotalsPaid': round(final_totals_paid, 2),
            'finalTotalIncomeTax': round(total_tax_all, 2),

            # names kept for the template (the old "20%" is now the real benefit)
            'eilat_regular_tax': round(eilat_regular_tax, 2),
            'eilat_benefit_20': round(city_benefit_total, 2),
            'eilat_tax_after_benefit': round(eilat_total_tax_after_benefit, 2),
            'eilat_benefit_employees': city_benefit_count,

            'reportMonth': s_month_str,
            'reportYear': s_year_str,
            'companyName': company_obj.name or "",
            'taxFileNumber': company_obj.deduction_file or "",
            'companyAddress': company_obj.address or ""
        })

        address = form_data.get("companyAddress", "")
        parts = address.split(',')
        company_city = parts[1].strip() if len(parts) > 1 else ""

        session['form_data'] = form_data
        session['form_data_ctx'] = ctx

        return render_template('form_H102.html',
                               form_data=form_data,
                               company_city=company_city,
                               company=company_obj,
                               company_db=company_obj,
                               employees=employees,
                               employeeMonth=s_month_str,
                               employeeYear=s_year_str,
                               months=months,
                               years=years)

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בטעינת טופס H102", "danger")
        return redirect(url_for('index'))

# --------------------
#  Save Form H102 Report Data To Folder
# ---------------------- 

@app.route("/save_formH102_report", methods=["POST"])
@login_required
def save_formH102_report():
    try:
        data = request.get_json(silent=True)
        if not data or 'employer' not in data:
            return jsonify(success=False, message="נתונים חסרים"), 400

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return jsonify(success=False, message="שגיאה: אין חברה פעילה משויכת למשתמש"), 401

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        employer = data.get("employer") or {}

        # FIX 1: month / year from the report, else from the month on screen (not "01" / "2024")
        today = datetime.today()
        month = str(employer.get("reportMonth") or session.get('selected_month') or today.month).strip().zfill(2)
        year = str(employer.get("reportYear") or session.get('selected_year') or today.year).strip()

        # FIX 2: month and year go into folder and file names, so only valid values are allowed
        if not (month.isdigit() and 1 <= int(month) <= 12 and year.isdigit() and len(year) == 4):
            return jsonify(success=False, message="חודש או שנה לא תקינים"), 400

        # FIX 3: folder that also works on Windows (local test) and on Render
        persistent_base = app.config.get("COMPANY_DIR") or (
            os.path.join(PERSISTENT_BASE, "companies") if "RENDER" in os.environ
            else os.path.join(BASE_DIR, "static", "companies"))
        base_dir = os.path.join(persistent_base, f"company_{safe_company_id}", "form_H102_reports")
        year_folder = os.path.join(base_dir, f"Form_H102_{year}")
        os.makedirs(year_folder, exist_ok=True)

        filename_json = f"Form_H102_report_{month}_{year}.json"
        filename_xml = f"Form_H102_report_{month}_{year}.xml"

        # JSON is always saved
        with open(os.path.join(year_folder, filename_json), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        # FIX 4: XML only if one was sent (the old code wrote an empty file)
        xml_content = data.get("xml", "") or ""
        saved_xml = None
        if xml_content.strip():
            with open(os.path.join(year_folder, filename_xml), "w", encoding="utf-8") as f:
                f.write(xml_content)
            saved_xml = filename_xml

        return jsonify({
            "success": True,
            "message": "דוח H102 נשמר בשרת" + ("" if saved_xml else " (ללא XML)"),
            "json_file": filename_json,
            "xml_file": saved_xml,
            "month": month,
            "year": year
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ H102 Save Report Error: {e}")
        return jsonify(success=False, message=f"Internal Server Error: {str(e)}"), 500
        
# --------------------
#  Submit Form H102 XML Data To Postgres Folder
# ----------------------

@app.route('/submitH102', methods=['POST'])
@login_required
def submit_form_H102():
    try:
        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return "Unauthorized Workspace Access", 401

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        # FIX 1: month / year from the form, else from the month on screen
        today = datetime.today()
        month = str(request.form.get('reportMonth') or session.get('selected_month') or today.month).strip().zfill(2)
        year = str(request.form.get('reportYear') or session.get('selected_year') or today.year).strip()

        if not (month.isdigit() and 1 <= int(month) <= 12 and year.isdigit() and len(year) == 4):
            flash("חודש או שנה לא תקינים", "danger")
            return redirect(url_for('form_H102'))

        # FIX 2: session data counts only if it is H102 data of THIS company + month + year
        ctx = f"{safe_company_id}:{month}:{year}"
        saved = (session.get('form_data') if session.get('form_data_ctx') == ctx else None) or {}

        if 'eilat_benefit_20' not in saved:       # marker: only form_H102 sets this key
            flash("נתוני H102 לא נמצאו בזיכרון (ייתכן שנפתח טופס אחר). פתח את H102 לחודש הנכון ונסה שוב.", "warning")
            return redirect(url_for('form_H102', month=int(month), year=int(year)))

        form_data = dict(saved)
        form_data.update(request.form.to_dict())      # manual edits from the form
        form_data['reportMonth'], form_data['reportYear'] = month, year

        # 2. XML from FIELD_MAP_H102 (unchanged logic), and report the keys that were not found
        root = ET.Element("FormH102_Data")
        missing = []
        for tax_key, app_keys in FIELD_MAP_H102.items():
            val = 0
            found = False
            if isinstance(app_keys, list):
                for key in app_keys:
                    if key in form_data:
                        val = form_data[key]
                        found = True
                        break
            else:
                if app_keys in form_data:
                    val = form_data[app_keys]
                    found = True
            if not found:
                missing.append(tax_key)
            ET.SubElement(root, tax_key).text = str(val)

        if missing:
            print(f"H102 XML: no value found (written as 0) for: {missing}")

        xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        # 3. save to the database (isolated by company, month, year)
        report = FormH102Report.query.filter_by(
            company_id=safe_company_id,
            report_month=month,
            report_year=year
        ).first()

        if report:
            report.data_json = form_data
            report.xml_content = xml_str.decode('utf-8')
        else:
            report = FormH102Report(
                company_id=safe_company_id,
                report_month=month,
                report_year=year,
                data_json=form_data,
                xml_content=xml_str.decode('utf-8')
            )
            db.session.add(report)

        db.session.commit()

        # 4. prepare for sending and download
        session["last_h102_id"] = report.id
        session['selected_month'] = month
        session['selected_year'] = year

        filename = f"FormH102_{month}_{year}.xml"
        return Response(
            xml_str,
            mimetype="application/xml",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"Error in H102 Submit: {e}")
        return f"שגיאה: {str(e)}", 500

# --------------------
#   Convert Fields Name From App To Tax Office Name Report Form H102 Form Data
# ----------------------

# mapping between form field names (HTML) and model attributes (DB)
FIELD_MAP_H102 = {

    #   Employer Information
    "companyName": ["companyName"],
    "taxFileNumber": ["taxFileNumber"],
    "reportMonth": ["reportMonth"],
    "reportYear": ["reportYear"],

    "NumEmployees": ["employee_count"],

    "regular_salary": ["regular_salary_hidden"],
    "reduced_salary": ["reduced_salary_hidden"],

    # שכר מעל תקרה לעובד (רק אם חישבת)
    "employeeAboveMaxSalary": ["employee_above_max"],

    # שכר מופחת למעסיק = שכר מופחת לעובד
    "employerReducedSalary": ["reduced_salary_hidden"],

    # שכר מעל תקרה למעסיק = כמו עובד
    "employerAboveMaxSalary": ["employee_above_max"],

    # ספירת עובדים רגילים / מופחתים
    "regular_count": ["regular_count_hidden"],
    "reducedEmployeeCount": ["reduced_count_hidden"],   

    # סה״כ שכר
    "total_salary": ["total_salary_hidden"],

    # ----- Original totals -----
    "totalGrossSalary": ["total_gross"],
    "totalIncomeTax": ["total_income_tax"],
    "totalNationalInsurance": ["total_ni_employee"],
    "totalHealthInsurance": ["total_health"],
    "totalEmpPension": ["emp_pension"],
    "totalSelfPension": ["self_pension"],
    "totalPensionDeduction": ["final_emp_pension_combined"],
    "totalProvidentDeduction": ["total_study_fund_deductions"],

    # ----- Employer contributions -----
    "totalPensionVal": ["pension_val"],
    "totalCompVal": ["comp_val"],
    "totalDisabilityVal": ["disability_val"],
    "employerPension": ["total_employer_pension_combined"],
    "totalStudyFundVal": ["study_fund_val"],

    # ----- Summary -----
    "totalEmpDeductions": ["final_emp_deductions_total"],
    "totalContributions": ["total_employer_contributions"],
    "finalTotalsPaid": ["finalTotalsPaid"],
    "finalTotalIncomeTax": ["final_totals_income_tax"],

    #  טור 4 שורה 8 = שורה 7
    "reducedEmployeeCount_total": ["finalTotalsPaid"],

    #   Column A (א)
    "regularSalary": ["regular_salary_hidden"],
    "eilatRegularTax": ["eilat_regular_tax"],
    "eilatBenefit20": ["eilat_benefit_20"],
    "eilatTotalTaxAfterBenefit": ["eilat_total_tax_after_benefit"],

    #   Column B (ב)
    "controllingSalary": ["controlling_salary"],
    "controllingTax": ["controlling_tax"],

    #   Column C (ג)
    "outsideEilatSalary": ["outside_eilat_salary"],
    "outsideEilatTax": ["outside_eilat_tax"],
    "totalTaxAll": ["total_tax_all"],
}


# --------------------
# Send Form H102 To Tax Office Requst Data
# ----------------------

@app.route('/send_H102_to_tax', methods=['POST'])
@login_required
def send_H102_to_tax():
    try:
        # 1. owner permission (unchanged)
        if not session.get("owner_access"):
            flash("⛔ רק בעלים יכול לשדר לרשות המיסים.", "danger")
            return redirect(url_for('form_H102'))

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אימות סביבת עבודה נכשל. השידור נחסם.", "danger")
            return redirect(url_for('form_H102'))

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        # 2. load the saved report (this company only)
        report_id = session.get("last_h102_id")
        if not report_id:
            flash("דוח H102 לא נמצא בזיכרון — אנא לחץ על 'הורד XML' קודם כדי לנעול את הנתונים.", "warning")
            return redirect(url_for('form_H102'))

        try:
            report_id_int = int(report_id)
        except (ValueError, TypeError):
            report_id_int = 0

        report = FormH102Report.query.filter_by(
            id=report_id_int,
            company_id=safe_company_id
        ).first()

        if not report or not report.xml_content:
            flash("הנתונים ב-DB פגומים, לא קיימים או שייכים לחברה אחרת — אנא הפק את הדו\"ח מחדש.", "danger")
            return redirect(url_for('form_H102'))

        # FIX 1: the report must be the month shown on screen
        screen_month = str(session.get('selected_month', '')).zfill(2)
        screen_year = str(session.get('selected_year', ''))
        if (screen_month != str(report.report_month).zfill(2)) or (screen_year != str(report.report_year)):
            flash(f"הדוח השמור הוא לחודש {report.report_month}/{report.report_year} "
                  f"ולא לחודש שמוצג ({screen_month}/{screen_year}). הורד XML לחודש הנכון ונסה שוב.", "warning")
            return redirect(url_for('form_H102'))

        # FIX 2: do not send the same report twice
        if getattr(report, 'is_submitted', False):
            flash("דוח זה כבר שודר בעבר. כדי לשדר שוב, צור דוח חדש.", "warning")
            return redirect(url_for('form_H102'))

        xml_data = report.xml_content.encode('utf-8')

        # 3. transmit settings
        TAX_URL = "https://tax.gov.il/api/H102/upload"

        # FIX 3: certificate per company (same layout as Form 102 / B102), not one shared file
        if "RENDER" in os.environ:
            base_certs_dir = os.path.join(PERSISTENT_BASE, "companies", f"company_{safe_company_id}", "certs")
        else:
            base_certs_dir = os.path.join(BASE_DIR, "static", "companies", f"company_{safe_company_id}", "certs")

        CERT_FILE = os.path.join(base_certs_dir, "company_cert.pfx")

        # FIX 4: password from the environment first (the old value stays only as a fallback)
        CERT_PASS = os.environ.get("TAX_CERT_PASS") or "12345678"

        if not os.path.exists(CERT_FILE):
            flash("⛔ תעודה דיגיטלית (.pfx) לא נמצאה עבור חברה זו. אנא העלה תעודה חוקית תחילה.", "danger")
            return redirect(url_for('form_H102'))

        # 4. transmit
        response = requests.post(
            TAX_URL,
            data=xml_data,
            cert=(CERT_FILE, CERT_PASS),
            headers={"Content-Type": "text/xml"},
            timeout=30
        )

        # 5. record the answer; FIX 5: mark as submitted ONLY on success
        report.tax_response = response.text
        if response.status_code == 200:
            report.is_submitted = True
        db.session.commit()

        if response.status_code == 200:
            flash("✅ שידור H102 בוצע בהצלחה! אישור ה-XML נשמר ב-DB.", "success")
        else:
            flash(f"⚠️ השידור לא אושר (קוד {response.status_code}). הדוח לא סומן כשודר. בדוק את תשובת השרת.", "warning")

        return redirect(url_for('form_H102'))

    except requests.exceptions.Timeout:
        flash("❌ השידור נכשל: השרת של רשות המיסים לא הגיב בזמן. נסה שוב.", "danger")
        return redirect(url_for('form_H102'))
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"H102 Send Error: {e}")
        flash(f"❌ שגיאה בשידור H102: {str(e)}", "danger")
        return redirect(url_for('form_H102'))

# --------------------
# Clear Form H102 Data
# ----------------------

@app.route('/clear_formH102', methods=['POST'])
@login_required
def clear_formH102():
    try:
        # manual fields and the report locked for sending
        for key in ('form_data', 'form_data_ctx', 'last_h102_id'):
            session.pop(key, None)

        session['clear_H102'] = True      

        db.session.close()
        return redirect(url_for('form_H102'))

    except Exception as e:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        print(f"⚠️ Warning inside clear_formH102 session purge: {e}")
        return redirect(url_for('form_H102'))

# --------------------
#  Form 126 Form Data
# ----------------------

@app.route('/form_126', methods=['GET', 'POST'])
@login_required
def form_126():
    try:
        from types import SimpleNamespace

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        safe_company_id = int(active_company_id)

        company_obj = db.session.get(Company, safe_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------

        this_year = datetime.now().year
        years = range(this_year - 2, this_year + 2)

        # one entry per employee (latest row), this company only
        all_rows = (EmployeeData.query.filter_by(company_id=safe_company_id)
                    .order_by(EmployeeData.id.desc()).all())
        latest_by_emp = {}
        for r in all_rows:
            key = r.local_id if r.local_id is not None else r.id
            latest_by_emp.setdefault(key, r)
        employees = sorted(latest_by_emp.values(), key=lambda e: e.employee_name or "")

        # the template builds field names from employee.id -> give it local_id as .id
        # (plain copy, nothing is written back to the database)
        employees_view = []
        for e in employees:
            row = {c: getattr(e, c, None) for c in EmployeeData.__table__.columns.keys()}
            row['id'] = e.local_id if e.local_id is not None else e.id
            row['local_id'] = row['id']
            employees_view.append(SimpleNamespace(**row))

        # year from POST / URL / session, only a valid 4-digit year
        def valid_year(y):
            y = str(y or '').strip()
            return y if (y.isdigit() and len(y) == 4) else None

        if request.method == 'POST':
            y = valid_year(request.form.get('reportYear'))
            if y:
                session['selected_year'] = y
            return redirect(url_for('form_126', year=y or ''))

        selected_year = (valid_year(request.args.get('year'))
                         or valid_year(session.get('selected_year'))
                         or str(this_year))
        session['selected_year'] = selected_year

        form_data = {}

        GRAND_TOTAL_GROSS = 0.0
        GRAND_TOTAL_TAX = 0.0
        GRAND_TOTAL_NI = 0.0
        GRAND_TOTAL_HEALTH = 0.0
        GRAND_TOTAL_PENSION = 0.0
        GRAND_TOTAL_STUDY = 0.0
        GRAND_COUNT_EMPLOYEES = 0

        def cn(val):
            try:
                return float(str(val).replace("₪", "").replace(",", "").strip())
            except:
                return 0.0

        db_cols = [c for c in EmployeeData.__table__.columns.keys() if c not in ('id', 'company_id')]

        # LOOP: each employee ONCE, by local_id, this company only
        for emp_key, emp in latest_by_emp.items():
            eid = str(emp_key)

            all_hours = load_hours(safe_company_id, emp_key) or {}

            # monthly EmployeeData rows of that year (fallback when hours.json has no tax for a month)
            year_rows = EmployeeData.query.filter(
                EmployeeData.company_id == safe_company_id,
                EmployeeData.local_id == emp_key,
                EmployeeData.date.like(f"%/{selected_year}")
            ).all()
            rows_by_date = {r.date: r for r in year_rows}

            e_gross = e_tax = e_pension = e_ni = e_health = e_study = 0.0
            months_worked = 0

            for m in range(1, 13):
                mm = str(m).zfill(2)
                tax_data = ((all_hours.get(f"{selected_year}-{mm}") or {})
                            .get('hours_table', {}) or {}).get('tax') or {}

                if not tax_data:
                    month_row = rows_by_date.get(f"{mm}/{selected_year}")
                    if not month_row:
                        continue
                    tax_data = {c: getattr(month_row, c, None) for c in db_cols}

                months_worked += 1
                e_gross += cn(tax_data.get("gross_taxable", 0))
                e_tax += cn(tax_data.get("income_tax", 0))
                e_ni += cn(tax_data.get("national_insurance_deductions", 0))
                e_health += cn(tax_data.get("health_insurance_deductions", 0))
                e_pension += (cn(tax_data.get("employee_pension_fund", 0)) +
                              cn(tax_data.get("self_employed_pension_fund", 0)))
                e_study += cn(tax_data.get("study_fund_deductions", 0))

            if months_worked == 0:
                continue

            form_data.update({
                f'employee_name_{eid}': emp.employee_name or all_hours.get('employee_name', ''),
                f'id_number_{eid}': emp.id_number,
                f'grossSalary_{eid}': f"{e_gross:.2f}",
                f'incomeTax_{eid}': f"{e_tax:.2f}",
                f'nationalInsurance_{eid}': f"{e_ni:.2f}",
                f'healthInsurance_{eid}': f"{e_health:.2f}",
                f'pensionDeduction_{eid}': f"{e_pension:.2f}",
                f'providentDeduction_{eid}': f"{e_study:.2f}",
                f'monthsWorked_{eid}': months_worked
            })

            GRAND_TOTAL_GROSS += e_gross
            GRAND_TOTAL_TAX += e_tax
            GRAND_TOTAL_NI += e_ni
            GRAND_TOTAL_HEALTH += e_health
            GRAND_TOTAL_PENSION += e_pension
            GRAND_TOTAL_STUDY += e_study
            GRAND_COUNT_EMPLOYEES += 1

        GRAND_TOTAL_DEDUCTIONS = (GRAND_TOTAL_TAX + GRAND_TOTAL_NI + GRAND_TOTAL_HEALTH +
                                  GRAND_TOTAL_PENSION + GRAND_TOTAL_STUDY)

        form_data.update({
            'companyName': company_obj.name or "",
            'taxFileNumber': company_obj.deduction_file or "",
            'companyId': company_obj.company_id_number or "",
            'companyAddress': company_obj.address or "",
            'companyPhone': company_obj.phone or "",
            'reportYear': selected_year,

            'NumEmployees': GRAND_COUNT_EMPLOYEES,
            'grossSalary': f"{GRAND_TOTAL_GROSS:,.2f}",
            'incomeTax': f"{GRAND_TOTAL_TAX:,.2f}",
            'nationalInsurance': f"{GRAND_TOTAL_NI:,.2f}",
            'healthInsurance': f"{GRAND_TOTAL_HEALTH:,.2f}",
            'pensionDeposits': f"{GRAND_TOTAL_PENSION:,.2f}",
            'totalDeductions': f"{GRAND_TOTAL_DEDUCTIONS:,.2f}"
        })

        # quick check in the terminal: these keys must match the names in the HTML
        print("FORM126 KEYS:", sorted(k for k in form_data if k.startswith('grossSalary_')))

        return render_template('form_126.html',
                               form_data=form_data,
                               employees=employees_view,
                               years=years,
                               employeeYear=selected_year,
                               company=company_obj,
                               company_db=company_obj)

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בטעינת טופס 126", "danger")
        return redirect(url_for('index'))

# --------------------
#  Submit Form 126 XML Data To Postgres Folder
# ----------------------

@app.route('/submit126', methods=['POST'])
@login_required
def submit_form_126():
    try:
        incoming_data = request.form.to_dict()

        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return "Unauthorized Workspace Access", 401

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        company_obj = db.session.get(Company, safe_company_id)
        if not company_obj:
            return "Company not found", 404

        # FIX: valid 4-digit year, else the year on screen
        year = str(incoming_data.get('reportYear') or session.get('selected_year') or datetime.now().year).strip()
        if not (year.isdigit() and len(year) == 4):
            return "שנה לא תקינה", 400

        # FIX: each employee ONCE (latest row), by local_id, this company only
        all_rows = (EmployeeData.query.filter_by(company_id=safe_company_id)
                    .order_by(EmployeeData.id.desc()).all())
        latest_by_emp = {}
        for r in all_rows:
            key = r.local_id if r.local_id is not None else r.id
            latest_by_emp.setdefault(key, r)

        # Helper: clean numbers for Tax Office (no symbols, 2 decimal places)
        def cn(val):
            try:
                if not val:
                    return "0.00"
                clean_val = str(val).replace("₪", "").replace(",", "").strip()
                return f"{float(clean_val):.2f}"
            except:
                return "0.00"

        # Helper: per-employee field, accepts both naming styles
        def pick(eid, *names):
            for n in names:
                v = incoming_data.get(f"{n}_{eid}")
                if v not in (None, ''):
                    return v
            return ""

        # 1. XML ROOT
        root = ET.Element("Form126_Report")

        # 2. HEADER: employer details from the company record, not from the browser
        header = ET.SubElement(root, "EmployerHeader")
        ET.SubElement(header, "RecordType").text = "04"
        ET.SubElement(header, "TaxFileNumber").text = company_obj.deduction_file or incoming_data.get("taxFileNumber", "")
        ET.SubElement(header, "CompanyName").text = company_obj.name or incoming_data.get("companyName", "")
        ET.SubElement(header, "ReportYear").text = year

        summary = ET.SubElement(header, "GlobalSummary")
        ET.SubElement(summary, "TotalEmployees").text = str(incoming_data.get("NumEmployees", "0"))
        ET.SubElement(summary, "TotalGrossSalary").text = cn(incoming_data.get("grossSalary", "0"))
        ET.SubElement(summary, "TotalIncomeTax").text = cn(incoming_data.get("incomeTax", "0"))
        ET.SubElement(summary, "TotalNationalInsurance").text = cn(incoming_data.get("nationalInsurance", "0"))
        ET.SubElement(summary, "TotalHealthInsurance").text = cn(incoming_data.get("healthInsurance", "0"))
        ET.SubElement(summary, "TotalDeductions").text = cn(incoming_data.get("totalDeductions", "0"))

        # 3. DETAIL RECORDS: one per employee
        employees_container = ET.SubElement(root, "EmployeeRecords")

        for emp_key, emp in latest_by_emp.items():
            try:
                emp_local_id = int(emp_key)
            except (TypeError, ValueError):
                continue
            if emp_local_id == 0:
                continue                      # 0 is reserved for the master record

            eid = str(emp_local_id)
            gross_val = pick(eid, "grossSalary") or "0"

            # only employees who earned money this year
            if float(cn(gross_val)) == 0:
                continue

            emp_node = ET.SubElement(employees_container, "EmployeeDetail")
            ET.SubElement(emp_node, "RecordType").text = "07"
            ET.SubElement(emp_node, "EmployeeID").text = str(emp.id_number or pick(eid, "id_number", "idNumber") or "")
            ET.SubElement(emp_node, "EmployeeName").text = str(emp.employee_name or pick(eid, "employee_name", "fullName") or "")

            ET.SubElement(emp_node, "GrossSalary").text = cn(gross_val)
            ET.SubElement(emp_node, "IncomeTax").text = cn(pick(eid, "incomeTax"))
            ET.SubElement(emp_node, "NationalInsurance").text = cn(pick(eid, "nationalInsurance"))
            ET.SubElement(emp_node, "HealthInsurance").text = cn(pick(eid, "healthInsurance"))
            ET.SubElement(emp_node, "PensionDeduction").text = cn(pick(eid, "pensionDeduction"))
            ET.SubElement(emp_node, "StudyFundDeduction").text = cn(pick(eid, "providentDeduction"))
            ET.SubElement(emp_node, "MonthsWorked").text = str(pick(eid, "monthsWorked") or "0")

            # individual record for history (employee_id = local_id, same id as every other page)
            emp_report = Form126Report.query.filter_by(
                company_id=safe_company_id,
                employee_id=emp_local_id,
                report_year=year
            ).first()

            if not emp_report:
                emp_report = Form126Report(
                    company_id=safe_company_id,
                    employee_id=emp_local_id,
                    report_year=year
                )
                db.session.add(emp_report)

            # FIX: exact suffix match (employee 1 no longer picks up _10, _11, _12)
            emp_report.data_json = {k: v for k, v in incoming_data.items() if k.endswith(f"_{eid}")}
            emp_report.xml_content = ET.tostring(emp_node, encoding='utf-8').decode('utf-8')

        # 4. FULL MASTER XML (employee_id = 0 is the company master record)
        full_xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')

        master_report = Form126Report.query.filter_by(
            company_id=safe_company_id,
            employee_id=0,
            report_year=year
        ).first()

        if not master_report:
            master_report = Form126Report(
                company_id=safe_company_id,
                employee_id=0,
                report_year=year
            )
            db.session.add(master_report)

        master_report.data_json = incoming_data
        master_report.xml_content = full_xml_str

        db.session.commit()

        session['selected_year'] = year
        flash("דוח 126 נשמר בהצלחה!", "success")
        return redirect(url_for('form_126', year=year))

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"Error in submit_form_126: {e}")
        return f"Error: {str(e)}", 500

# --------------------
#   Convert Fields Name From App To Tax Office Name Report Form 126 Form Data
# ----------------------

FIELD_MAP_126 = {

    # ----- Employer Details -----
    "companyName": "companyName",
    "taxFileNumber": "taxFileNumber",
    "companyId": "companyId",
    "companyAddress": "companyAddress",
    "reportYear": "reportYear",

    # ----- Employee Details (per employee_id) -----
    "fullName": "fullName_{id}",
    "idNumber": "idNumber_{id}",
    "NumEmployees": ["NumEmployees"],

    # ----- Income (per employee_id) -----
    "grossSalary": "grossSalary_{id}",
    "bonuses": "bonuses_{id}",
    "benefits": "benefits_{id}",
    "severance": "severance_{id}",
    "pensionDeposits": "pensionDeposits_{id}",
    "providentFund": "providentFund_{id}",

    # ----- Deductions (per employee_id) -----
    "incomeTax": "incomeTax_{id}",
    "nationalInsurance": "nationalInsurance_{id}",
    "healthInsurance": "healthInsurance_{id}",
    "pensionDeduction": "pensionDeduction_{id}",
    "providentDeduction": "providentDeduction_{id}",
    "monthsWorked": "monthsWorked_{id}",

    # ----- Summary (per employee_id) -----
    "totalIncome": "totalIncome_{id}",
    "taxableIncome": "taxableIncome_{id}",
    "totalDeductions": "totalDeductions_{id}",
    "netSalary": "netSalary_{id}"
}

# --------------------
#  Form 126 download_xml_126 Data
# ----------------------

@app.route('/download_xml_126/<year>')
@login_required
def download_xml_126(year):
    try:
        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return "Unauthorized Workspace Access", 401

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        # FIX 1: only a real 4-digit year (blocks script injection and bad file names)
        year_str = str(year).strip()
        if not (year_str.isdigit() and len(year_str) == 4):
            return "שנה לא תקינה", 400

        # master record of this company and year (employee_id = 0)
        report = Form126Report.query.filter_by(
            company_id=safe_company_id,
            employee_id=0,
            report_year=year_str
        ).first()

        if not report or not report.xml_content:
            return f"""
            <script>
                alert('שגיאה: לא נמצא דוח שמור לשנת {year_str}. אנא לחץ על שמירה/Submit לפני ההורדה.');
                window.history.back();
            </script>
            """, 404

        # FIX 2: keep the screen on the same year
        session['selected_year'] = year_str

        return Response(
            report.xml_content,
            mimetype='application/xml',
            headers={"Content-Disposition": f"attachment; filename=form126_{year_str}.xml"}
        )

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error inside download_xml_126: {e}")
        return "Internal Server Error", 500

# --------------------
# Clear Form 126 Data
# ----------------------

@app.route('/clear_form126', methods=['POST'])
@login_required
def clear_form126():
    try:
        for key in ('form_data', 'form_data_ctx', 'employee_id'):
            session.pop(key, None)

        session['clear_126'] = True        # kept in case your HTML reads it

        # optional: also go back to the current year
        # session['selected_year'] = str(datetime.now().year)

        db.session.close()

        flash('הטופס נוקה בהצלחה!', 'info')
        return redirect(url_for('form_126'))

    except Exception as e:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        print(f"⚠️ Warning inside clear_form126 session purge: {e}")
        return redirect(url_for('form_126'))


# --------------------
#  Form 161 Form Data
# ----------------------

@app.route('/form_161', methods=['GET', 'POST'])
@login_required
def form_161():
    try:
        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        safe_company_id = int(active_company_id)

        company_obj = db.session.get(Company, safe_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------

        # one entry per employee (latest row), this company only
        all_rows = (EmployeeData.query.filter_by(company_id=safe_company_id)
                    .order_by(EmployeeData.id.desc()).all())
        latest_by_emp = {}
        for r in all_rows:
            key = r.local_id if r.local_id is not None else r.id
            latest_by_emp.setdefault(key, r)
        employees = sorted(latest_by_emp.values(), key=lambda e: e.employee_name or "")

        def parse_date(v):
            if not v:
                return None
            if isinstance(v, datetime):
                return v
            if hasattr(v, 'year') and hasattr(v, 'month') and hasattr(v, 'day'):
                return datetime(v.year, v.month, v.day)
            s = str(v).strip()
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
                try:
                    return datetime.strptime(s, fmt)
                except ValueError:
                    pass
            return None

        def cn(val):
            try:
                return float(str(val).replace("₪", "").replace(",", "").strip())
            except:
                return 0.0

        # ===================== POST: save =====================
        if request.method == 'POST':
            employee_id = (request.form.get('employee_id') or '').strip()

            # local_id first, then id, this company only
            employee = resolve_employee(safe_company_id, employee_id)
            if not employee:
                flash('שגיאה: אין הרשאה לעדכן נתוני עובד זה', 'danger')
                return redirect(url_for('unauthorized'))

            emp_local_id = employee.local_id if employee.local_id is not None else employee.id

            # update ALL rows of this employee, so every month shows the same profile
            emp_rows = EmployeeData.query.filter_by(
                company_id=safe_company_id, local_id=emp_local_id).all() or [employee]

            protected = {'id', 'company_id', 'local_id', 'employee_id'}
            cols = EmployeeData.__table__.columns

            for form_field, model_attrs in FIELD_MAP_161.items():
                if form_field not in request.form:
                    continue
                val = request.form.get(form_field)
                if isinstance(model_attrs, str):
                    model_attrs = [model_attrs]

                for attr in model_attrs:
                    if attr in protected or attr not in cols:
                        continue
                    col_type = str(cols[attr].type).lower()
                    v = val
                    if any(t in col_type for t in ('float', 'numeric', 'integer')):
                        v = clean_float(val)
                    elif col_type.startswith('date'):
                        d = parse_date(val)
                        v = (d.date() if 'time' not in col_type else d) if d else None
                    for r in emp_rows:
                        setattr(r, attr, v)

            db.session.commit()

            ctx = f"{safe_company_id}:{emp_local_id}"
            session['form_161_data'] = request.form.to_dict()
            session['form_161_ctx'] = ctx
            session['selected_employee_id'] = str(emp_local_id)
            session['owner_employee_id'] = str(emp_local_id)
            flash('טופס 161 עודכן בהצלחה!', 'success')
            return redirect(url_for('form_161', employee_id=emp_local_id))

        # ===================== GET: calculate =====================
        employee_id = (request.args.get('employee_id')
                       or session.get('owner_employee_id')
                       or session.get('selected_employee_id'))

        form_data = {}
        selected_id = None

        if employee_id and str(employee_id).strip() not in ['', '0', 'None']:
            emp = resolve_employee(safe_company_id, employee_id)

            if emp:
                emp_local_id = emp.local_id if emp.local_id is not None else emp.id
                selected_id = str(emp_local_id)

                # manual fields only for THIS employee of THIS company
                ctx = f"{safe_company_id}:{emp_local_id}"
                if session.get('form_161_ctx') == ctx:
                    form_data = dict(session.get('form_161_data') or {})

                # profile value = newest non-empty value across all rows of this employee
                emp_rows = (EmployeeData.query.filter_by(company_id=safe_company_id, local_id=emp_local_id)
                            .order_by(EmployeeData.id.desc()).all()) or [emp]

                def pick(*names):
                    for r in emp_rows:
                        for n in names:
                            v = getattr(r, n, None)
                            if v not in (None, ''):
                                return v
                    return ""

                # last salary: latest saved month in hours.json, fallback = monthly EmployeeData rows
                all_hours = load_hours(safe_company_id, emp_local_id) or {}
                last_salary = 0.0
                month_keys = sorted((k for k in all_hours if re.match(r'^\d{4}-\d{2}$', str(k))), reverse=True)
                for mk in month_keys:
                    tax = ((all_hours.get(mk) or {}).get('hours_table') or {}).get('tax') or {}
                    v = cn(tax.get('gross_taxable', 0))
                    if v:
                        last_salary = v
                        break

                if not last_salary:
                    best = None
                    for r in emp_rows:
                        m = re.match(r'^(\d{2})/(\d{4})$', str(getattr(r, 'date', '') or ''))
                        g = cn(getattr(r, 'gross_taxable', 0))
                        if m and g:
                            key = (int(m.group(2)), int(m.group(1)))
                            if best is None or key > best[0]:
                                best = (key, g)
                    if best:
                        last_salary = best[1]

                # seniority in years (several date formats, date objects too)
                seniority = 0
                start = parse_date(pick('start_date'))
                if start:
                    seniority = round((datetime.now() - start).days / 365.25, 1)
                    if seniority < 0:
                        seniority = 0

                name = (emp.employee_name or pick('employee_name') or "").strip()
                name_parts = name.split()

                form_data.update({
                    'employee_id': selected_id,
                    'employee_first_name': name_parts[0] if name_parts else "",
                    'employee_last_name': " ".join(name_parts[1:]) if name_parts else "",
                    'employee_id_number': pick('id_number'),
                    'employee_address': pick('address'),
                    'employee_start_date': pick('start_date'),
                    'employee_date_of_birth': pick('date_of_birth'),
                    'last_salary': f"{last_salary:,.2f}",
                    'seniority_years': seniority,
                    'severance_paid': f"{(last_salary * seniority):,.2f}",
                    'companyName': company_obj.name or "",
                    'companyId': company_obj.company_id_number or "",
                    'companyAddress': company_obj.address or "",
                    'companyPhone': company_obj.phone or "",
                    'companyEmail': company_obj.email or ""
                })

                # keep the selection shared with index / other pages
                session['selected_employee_id'] = selected_id
                session['owner_employee_id'] = selected_id
                session['form_161_ctx'] = ctx
            else:
                form_data = {}          # id of another company or unknown: nothing is shown

        session['form_161_data'] = form_data

        return render_template(
            'form_161.html',
            form_data=form_data,
            employees=employees,
            selected_employee_id=selected_id,
            company=company_obj,
            company_db=company_obj
        )

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בטעינת טופס 161", "danger")
        return redirect(url_for('index'))

# --------------------
#  Submit Form 161 XML Data To Postgres Folder
# ----------------------

@app.route('/submit161', methods=['POST'])
@login_required
def submit_form_161():
    try:
        # ----------------- COMPANY CONTEXT (unchanged) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            return "Unauthorized Workspace Access", 401

        safe_company_id = int(active_company_id)
        # ---------------------------------------------------------------

        # FIX 1: the employee must exist in THIS company (local_id first, then id)
        employee_id = (request.form.get('employee_id')
                       or session.get('owner_employee_id')
                       or session.get('selected_employee_id')
                       or '')
        if not str(employee_id).strip():
            return "שגיאה: עובד לא נבחר", 400

        employee = resolve_employee(safe_company_id, employee_id)
        if not employee:
            return "שגיאה: העובד לא נמצא בחברה זו", 403

        emp_local_id = employee.local_id if employee.local_id is not None else employee.id

        # FIX 2: session data counts only if it belongs to THIS employee of THIS company
        ctx = f"{safe_company_id}:{emp_local_id}"
        saved = (session.get('form_161_data') if session.get('form_161_ctx') == ctx else None) or {}

        form_data = dict(saved)
        form_data.update(request.form.to_dict())
        form_data['employee_id'] = str(emp_local_id)

        # XML from FIELD_MAP_161
        root = ET.Element("Form161_Data")
        missing = []

        for tax_key, app_keys in FIELD_MAP_161.items():
            # FIX 3: map value may be a string or a list
            if isinstance(app_keys, str):
                app_keys = [app_keys]

            # the form field itself first, then the alternative names
            val = None
            for key in [tax_key] + list(app_keys):
                if key in form_data and form_data[key] not in (None, ''):
                    val = form_data[key]
                    break

            if val is None:
                missing.append(tax_key)
                val = 0

            s = str(val).strip()
            # FIX 4: strip commas and ₪ only from NUMBERS, never from text like an address
            if re.fullmatch(r'[\d,.\-₪\s]+', s):
                s = s.replace(",", "").replace("₪", "").strip()

            # FIX 5: valid XML tag name
            tag = re.sub(r'[^A-Za-z0-9_]', '_', str(tax_key))
            if not tag or tag[0].isdigit():
                tag = '_' + tag

            ET.SubElement(root, tag).text = s

        if missing:
            print(f"Form 161 XML: no value found (written as 0) for: {missing}")

        xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        # save (company + employee local_id)
        report = Form161Report.query.filter_by(
            company_id=safe_company_id,
            employee_id=emp_local_id
        ).first()

        if report:
            report.data_json = form_data
            report.xml_content = xml_str.decode('utf-8')
        else:
            report = Form161Report(
                company_id=safe_company_id,
                employee_id=emp_local_id,
                data_json=form_data,
                xml_content=xml_str.decode('utf-8')
            )
            db.session.add(report)

        db.session.commit()

        session['selected_employee_id'] = str(emp_local_id)
        session['owner_employee_id'] = str(emp_local_id)

        return Response(xml_str, mimetype="application/xml",
                        headers={"Content-Disposition": f"attachment; filename=Form161_{emp_local_id}.xml"})

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return f"Error Submit 161: {str(e)}", 500

# --------------------
#   Convert Fields Name From App To Tax Office Name Report Form 161 Form Data
# ----------------------

FIELD_MAP_161 = {
    'employee_first_name': ['first_name'],
    'employee_last_name': ['last_name'],
    'employee_id_number': ['id_number'],
    'employee_address': ['address'],
    'employee_date_of_birth': ['date_of_birth'],

    'companyName': ['employer_name'],
    'companyId': ['company_id'],
    'companyAddress': ['companyAddress'],
    'companyPhone': ['companyPhone'],

    'employment_start_date': ['start_date'],
    'employment_end_date': ['end_date'],
    'termination_reason': ['termination_reason'],

    'last_salary': ['last_salary'],
    'seniority_years': ['seniority_years'],
    'severance_paid': ['severance_paid'],
    'severance_in_funds': ['severance_in_funds'],
    'severance_exempt': ['severance_exempt'],
    'severance_taxable': ['severance_taxable'],

    'fund_name': ['fund_name'],
    'fund_number': ['fund_number'],
    'fund_balance': ['fund_balance'],
    'fund_released': ['fund_released'],

    'tax_route': ['tax_route'],
    'form_date': ['form_date']
}





# ==================================================================
#  מנוע שעון נוכחות מולטי-חברה CSV Files (Multi-Tenant Employee Clock In/Out  Hours File)
# ==================================================================

# ----------------------
# HoursCard Clock In: timesheet (WITH MODEL ATTRIBUTE MATCHING)
# ----------------------

@app.route("/api/clockin", methods=["POST"])
@login_required
def api_clockin():
    try:
        data = request.get_json() or {}
        employee_id_pk = session.get("employee_id") or current_user.id

        if not employee_id_pk:
            return jsonify({"status": "error", "message": "Employee ID not found in session."}), 400

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברה מאובטחת) -----------------
        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return jsonify({"status": "error", "message": "שגיאה: אין חברה פעילה משויכת למשתמש"}), 401

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        #  שימוש במודל החדש EmployeeData ואיתור לפי ה-local_id או ה-id והחברה הפעילה בלבד
        if str(employee_id_pk).isdigit():
            #  המרת ה-ID ל-int נקי לפני שהוא נוגע בשאילתות ה-SQL של פוסטגרס
            employee_id_int = int(employee_id_pk)
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == employee_id_int) | (EmployeeData.id == employee_id_int)
            ).filter_by(company_id=safe_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=str(employee_id_pk), company_id=safe_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל העובד לא נמצא בחברה הפעילה במערכת"}), 404
        # ---------------------------------------------------------------------------

        # תוקן: בדיקת מצב משמרת לפי ה-ID הכללי המאובטח של העובד (employee.id) והחברה שלו
        #  הבטחה שגם מזהה ה-employee.id יעבור המרה נקייה ל-int
        safe_employee_id_int = int(employee.id)
        shift = ShiftState.query.filter_by(employee_id=safe_employee_id_int, company_id=safe_company_id).first()
        
        start_time = data.get("startTime")
        if not start_time:
            return jsonify({"status": "error", "message": "Start time missing in request."}), 400

        def clean_clockin_time(t_input):
            if not t_input:
                return "00:00"
            t_str = str(t_input).strip()
            if "T" in t_str:
                t_str = t_str.split("T")[-1]
            if "-" in t_str:
                parts = t_str.split()
                t_str = parts[-1] if parts else "00:00"
            
            if not t_str or t_str in ["None", "None:00", "00:00:00", ""]:
                return "00:00"
                
            t_short = t_str[:5]
            if ":" in t_short and len(t_short) == 5:
                return t_short
            return "00:00"

        clean_start_time = clean_clockin_time(start_time)

        if shift and (shift.isClockedIn == "true" or shift.isClockedIn is True):
            db.session.close() 
            return jsonify({"status": "already_clocked_in", "message": "Already clocked in."}), 200

        if not shift:
            shift = ShiftState(
                company_id=safe_company_id, 
                employee_id=safe_employee_id_int,  # קיבוע ה-ID הגלובלי המאובטח כמספר שלם
                employee_name=employee.employee_name or "Unknown",
                isClockedIn="true",
                startTime=clean_start_time
            )
            db.session.add(shift)
            
        else:
            shift.isClockedIn = "true"
            shift.startTime = clean_start_time
            shift.endTime = None 
            shift.task = None    

        db.session.commit()
        db.session.close() 
        
        return jsonify({"status": "clocked_in"}), 200

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Database error on clockin: {str(e)}") 
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500
        

# ----------------------
# HoursCard Clock Out: timesheet (WITH MODEL ATTRIBUTE MATCHING)
# ----------------------

@app.route("/api/clockout", methods=["POST"])
@login_required
def api_clockout():
    try:
        data = request.get_json() or {}
        employee_data_id = session.get("employee_id") or current_user.id

        if not employee_data_id:
            return jsonify({"status": "error", "message": "Employee ID not found in session."}), 400

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברה מאובטחת) -----------------
        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return jsonify({"status": "error", "message": "שגיאה: אין חברה פעילה משויכת למשתמש"}), 401

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        #  שימוש במודל החדש EmployeeData ואיתור לפי ה-local_id או ה-id והחברה הפעילה בלבד
        if str(employee_data_id).isdigit():
            #  המרת ה-ID ל-int נקי לפני שהוא נוגע בשאילתות של פוסטגרס בענן
            employee_data_id_int = int(employee_data_id)
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == employee_data_id_int) | (EmployeeData.id == employee_data_id_int)
            ).filter_by(company_id=safe_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=str(employee_data_id), company_id=safe_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל העובד לא נמצא בחברה הפעילה במערכת"}), 404
        # ---------------------------------------------------------------------------

        #  עדכון מצב משמרת לפי ה-ID הכללי המאובטח של העובד (employee.id) והחברה שלו
        # Render: הבטחה שגם מזהה ה-employee.id יעבור המרה נקייה ל-int בשאילתה
        safe_employee_id_int = int(employee.id)
        shift = ShiftState.query.filter_by(employee_id=safe_employee_id_int, company_id=safe_company_id).first()
        
        if shift:
            def clean_clockout_time(t_input):
                if not t_input:
                    return "00:00"
                t_str = str(t_input).strip()
                if "T" in t_str:
                    t_str = t_str.split("T")[-1]
                if "-" in t_str:
                    parts = t_str.split()
                    t_str = parts[-1] if parts else "00:00"
                
                if not t_str or t_str in ["None", "None:00", "00:00:00", ""]:
                    return "00:00"
                    
                t_short = t_str[:5]
                if ":" in t_short and len(t_short) == 5:
                    return t_short
                return "00:00"

            clean_end_time = clean_clockout_time(data.get("endTime"))

            shift.isClockedIn = "false"
            shift.endTime = clean_end_time
            
            task_val = data.get("task", "").strip()
            if task_val:
                shift.task = task_val

            db.session.commit()
            db.session.close() 
            
            return jsonify({"status": "clocked_out"}), 200
        else:
            db.session.close() 
            return jsonify({"status": "warning", "message": "No active shift found for your company."}), 200

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Database error on clockout: {str(e)}")
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500


# ----------------------
# API: Load Current Shift State
# ----------------------

@app.route("/api/shiftstate", methods=["GET"])
@login_required
def api_shiftstate():
    try:
        # שליפת מזהה העובד מהסשן או מהמשתמש המחובר
        employee_data_id = session.get("employee_id") or current_user.id

        if not employee_data_id:
            return jsonify({"status": "no_employee"}), 200

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברה מאובטחת) -----------------
        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return jsonify({"status": "error", "message": "שגיאה: אין חברה פעילה משויכת למשתמש"}), 401

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        #  שימוש במודל החדש EmployeeData ואיתור לפי ה-local_id או ה-id והחברה הפעילה בלבד
        if str(employee_data_id).isdigit():
            #  המרת ה-ID ל-int נקי לפני שהוא נוגע בשאילתות של פוסטגרס בענן
            employee_data_id_int = int(employee_data_id)
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == employee_data_id_int) | (EmployeeData.id == employee_data_id_int)
            ).filter_by(company_id=safe_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=str(employee_data_id), company_id=safe_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל העובד לא נמצא בחברה הפעילה במערכת"}), 404
        # ---------------------------------------------------------------------------

        #  בדיקת מצב משמרת לפי ה-ID הכללי המאובטח של העובד (employee.id) והחברה שלו
        # Render: הבטחה שגם מזהה ה-employee.id יעבור המרה נקייה ל-int בשאילתה
        safe_employee_id_int = int(employee.id)
        shift = ShiftState.query.filter_by(employee_id=safe_employee_id_int, company_id=safe_company_id).first()

        if shift:
            raw_start_time = str(shift.startTime).strip() if shift.startTime else ""
            if "T" in raw_start_time:
                raw_start_time = raw_start_time.split("T")[-1]
            if "-" in raw_start_time:
                parts = raw_start_time.split()
                raw_start_time = parts[-1] if parts else ""
            
            if not raw_start_time or raw_start_time in ["None", "None:00", "00:00:00", ""]:
                clean_start_time = "00:00"
            else:
                clean_start_time = raw_start_time[:5] if ":" in raw_start_time else "00:00"

            is_clocked_in_val = shift.isClockedIn
            if is_clocked_in_val is True or str(is_clocked_in_val).lower() == "true":
                is_clocked_in_val = "true"
            else:
                is_clocked_in_val = "false"

            response_data = {
                "isClockedIn": is_clocked_in_val, 
                "startTime": clean_start_time,
                "task": shift.task or ""
            }
            db.session.close() 
            return jsonify(response_data), 200
        else:
            default_data = {
                "isClockedIn": "false",
                "startTime": None,
                "task": ""
            }
            db.session.close() 
            return jsonify(default_data), 200

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Database error on api_shiftstate: {str(e)}")
        return jsonify({"status": "error", "message": f"Server error: {str(e)}"}), 500


# ------------------------------------------------------------------
#  API Endpoint: רישום לחיצת כניסה/יציאה בזמן אמת (POST)
# ------------------------------------------------------------------

@app.route('/api/record_time', methods=['POST'])
@login_required
def record_time():
    try:
        data = request.get_json() or {}
        event_type = data.get('type') # 'START' or 'END'
        
        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברה מאובטחת) -----------------
        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return jsonify({"status": "error", "message": "שגיאה: אין חברה פעילה משויכת למשתמש"}), 401

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        # שליפת מזהה העובד מהסשן או מהיוזר המחובר
        req_employee_id = session.get("employee_id") or current_user.id
        
        if not req_employee_id:
            return jsonify({"status": "error", "message": "Employee ID missing in session"}), 400

        #  מציאת העובד במודל החדש EmployeeData לפי שילוב המזהה והחברה הפעילה
        if str(req_employee_id).isdigit():
            #  המרת מזהה העובד ל-int נקי לפני השאילתה של פוסטגרס
            req_employee_id_int = int(req_employee_id)
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == req_employee_id_int) | (EmployeeData.id == req_employee_id_int)
            ).filter_by(company_id=safe_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=str(req_employee_id), company_id=safe_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל עובד לא נמצא בחברה הפעילה במערכת"}), 404
            
        emp_name = str(employee.employee_name or "Unknown")
        # ---------------------------------------------------------------------------

        now = datetime.now()
        current_year = now.strftime('%Y')
        current_month = now.strftime('%m') 
        date_str = now.strftime('%Y-%m-%d')
        time_str = now.strftime('%H:%M:%S')

        location_data = data.get('location', 'None')
        if not location_data or str(location_data).strip() == "":
            location_data = "None"

        #  העברת employee.id הגלובלי והמאובטח לקבלת נתיב קובץ ה-CSV המבודד והנכון של החברה
        # Render: העברת safe_company_id המומר למנוע נתיבי הקבצים בדיסק המאובטח
        target_folder, _, csv_file_path = get_company_clock_paths(
            safe_company_id, employee.id, current_year, current_month
        )
        os.makedirs(target_folder, exist_ok=True)

        # יצירת קובץ ה-CSV של החודש באופן אוטומטי אם הוא לא קיים בדיסק
        if not os.path.exists(csv_file_path):
            with open(csv_file_path, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['EventID', 'Date', 'Time', 'Type', 'Duration', 'Task', 'employee_id', 'employee_name', 'Location'])
                
                import calendar
                _, num_days = calendar.monthrange(int(current_year), int(current_month))
                row_id = 1
                
                for day in range(1, num_days + 1):
                    formatted_date = f"{current_year}-{current_month.zfill(2)}-{str(day).zfill(2)}"
                    
                    # הזרקת ה-local_id הנכון של העובד לתוך הרשומות הסטטיות של החודש
                    writer.writerow([row_id, formatted_date, "00:00:00", "START", "0.0", "", str(employee.local_id), emp_name, "None"])
                    row_id += 1
                    writer.writerow([row_id, formatted_date, "00:00:00", "END", "0.0", "", str(employee.local_id), emp_name, "None"])
                    row_id += 1

        updated_rows = []
        headers = []

        # עדכון שעת הלחיצה והמיקום בזמן אמת בתוך ה-CSV
        with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            headers = next(reader)
            
            for row in reader:
                if len(row) >= 4:
                    if row[1] == date_str and row[3] == event_type:
                        row[2] = time_str
                        
                        if len(row) >= 7:
                            row[6] = str(employee.local_id) # קיבוע ה-local_id לתרגום תקין
                        
                        if len(row) == 9:
                            row[8] = location_data
                        else:
                            while len(row) < 8:
                                row.append("")
                            row.append(location_data)
                                
                updated_rows.append(row)

        with open(csv_file_path, mode='w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(headers)
            writer.writerows(updated_rows)
            
        db.session.close() 

        print(f"✔ Realtime {event_type} and Location ({location_data}) updated in unified 9-column CSV: {csv_file_path}")
        return jsonify({"status": "success", "message": f"{event_type} updated successfully"}), 200

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Error inside record_time API handler: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ----------------------
# API: Save Completed Timesheet Entry (The one we implemented)
# ----------------------

@app.route("/api/savetimesheet", methods=["POST"])
@login_required
def api_savetimesheet():
    try:
        data = request.get_json() or {}
        
        required_fields = ["employee_id", "date", "startTime", "endTime", "totalHours", "task"]
        for field in required_fields:
            if not data.get(field):
                return jsonify({
                    "status": "error", 
                    "message": f"Missing required field: {field}"
                }), 400

        local_employee_id = data.get("employee_id") 
        
        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברה מאובטחת) -----------------
        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return jsonify({"status": "error", "message": "שגיאה: אין חברה פעילה משויכת למשתמש"}), 401

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        if str(local_employee_id).isdigit():
            # Render: המרת מזהה העובד ל-int נקי לפני השאילתות של פוסטגרס בענן
            local_employee_id_int = int(local_employee_id)
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == local_employee_id_int) | (EmployeeData.id == local_employee_id_int)
            ).filter_by(company_id=safe_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=local_employee_id, company_id=safe_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל העובד לא נמצא בחברה זו."}), 404
        # ---------------------------------------------------------------------------

        raw_date = data.get("date")
        if isinstance(raw_date, list) and len(raw_date) > 0:
            work_date_str = str(raw_date[0]).strip()
        else:
            work_date_str = str(raw_date).strip()

        # המרת המזהה הפנימי ל-int למניעת קריסות בטבלת Timesheet בענן
        safe_employee_id_pk = int(employee.id)

        existing_entry = Timesheet.query.filter_by(
            company_id=safe_company_id,
            employee_id=safe_employee_id_pk,
            date=work_date_str
        ).first()
        
        start_loc = data.get("startLocation") or data.get("location") or ""
        end_loc = data.get("endLocation") or data.get("location") or ""

        if not start_loc or start_loc == "":
            start_loc = "None"
        if not end_loc or end_loc == "":
            end_loc = "None"

        def clean_savetimesheet_time(t_input):
            if not t_input:
                return "00:00"
            t_str = str(t_input).strip()
            if "T" in t_str:
                t_str = t_str.split("T")[-1]
            if "-" in t_str:
                parts = t_str.split()
                t_str = parts[-1] if parts else "00:00"
            
            if not t_str or t_str in ["None", "None:00", "00:00:00", ""]:
                return "00:00"
                
            t_short = t_str[:5]
            if ":" in t_short and len(t_short) == 5:
                return t_short
            return "00:00"

        clean_start_time = clean_savetimesheet_time(data.get("startTime"))
        clean_end_time = clean_savetimesheet_time(data.get("endTime"))

        if existing_entry:
            existing_entry.startTime = clean_start_time
            existing_entry.endTime = clean_end_time
            existing_entry.totalHours = float(data.get("totalHours") or 0.0)
            existing_entry.task = data.get("task")
            existing_entry.employee_name = employee.employee_name or "Unknown"
            existing_entry.id_number = employee.id_number or ""
            if start_loc != "None":
                existing_entry.startLocation = start_loc
                existing_entry.endLocation = end_loc
        else:
            new_timesheet_entry = Timesheet(
                company_id=safe_company_id,
                employee_id=safe_employee_id_pk,  
                employee_name=employee.employee_name or "Unknown",
                id_number=employee.id_number or "",
                date=work_date_str,
                startTime=clean_start_time,
                endTime=clean_end_time,
                startLocation=start_loc,
                endLocation=end_loc,
                task=data.get("task"),
                totalHours=float(data.get("totalHours") or 0.0)
            )
            db.session.add(new_timesheet_entry)

        try:
            d_parts = work_date_str.split('-')
            if len(d_parts) == 3:
                c_year, c_month = d_parts[0], d_parts[1]
                # Render: העברת safe_company_id המומר למנוע נתיבי הקבצים בדיסק המאובטח
                target_folder, _, csv_file_path = get_company_clock_paths(safe_company_id, safe_employee_id_pk, c_year, c_month)
                
                if os.path.exists(csv_file_path):
                    fieldnames = ['EventID', 'Date', 'Time', 'Type', 'Duration', 'Task', 'employee_id', 'employee_name', 'Location']
                    updated_csv_rows = []
                    
                    with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
                        reader = csv.reader(f)
                        header_row = next(reader) 
                        
                        for r in reader:
                            if len(r) >= 4 and r[1].strip() == work_date_str:
                                r[5] = data.get("task", "") 
                                
                                if len(r) >= 8:
                                    r[6] = str(employee.local_id) # קיבוע ה-local_id לתרגום בקובץ
                                    
                                r[7] = employee.employee_name or "Unknown" 
                                
                                if r[3].strip() == 'START':
                                    r[2] = f"{clean_start_time}:00"
                                    if start_loc != "None" and len(r) >= 9: r[8] = start_loc
                                elif r[3].strip() == 'END':
                                    r[2] = f"{clean_end_time}:00"
                                    r[4] = str(data.get("totalHours", "0.0"))
                                    if end_loc != "None" and len(r) >= 9: r[8] = end_loc
                                    
                            if len(r) == 9:
                                updated_csv_rows.append(dict(zip(fieldnames, r)))

                    with open(csv_file_path, mode='w', newline='', encoding='utf-8-sig') as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(updated_csv_rows)
        except Exception as csv_err:
            print(f"⚠️ Warning sync CSV inside api_savetimesheet: {csv_err}")

        # ניקוי מצב המשמרת הפעילה
        # שימוש במזהים המומרים בשאילתת ה-ShiftState למניעת שגיאות טיפוסים
        shift_state = ShiftState.query.filter_by(employee_id=safe_employee_id_pk, company_id=safe_company_id).first()
        if shift_state:
            db.session.delete(shift_state)
            
        db.session.commit()
        db.session.close() 
        
        return jsonify({"status": "saved", "message": "Timesheet entry saved and shift state cleared successfully"}), 200

    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Error saving timesheet: {e}") 
        return jsonify({"status": "error", "message": f"Server error while saving timesheet: {str(e)}"}), 500


# ----------------------
# Get Hours Data: Form Page
# ----------------------

def normalize_keys(data_dict):
    """Convert snake_case keys to kebab-case for frontend."""
    return {k.replace("_", "-"): v for k, v in data_dict.items()}


@app.route('/get_clock_hours_data', methods=['GET'])
@login_required
def get_clock_hours_data():
    try:
        # --- קורא קודם את ה-employee_id שנשלח מהקומבו-בוקס של הדף ---
        req_employee_id = request.args.get('employee_id') or request.args.get('employeeId')
        
        if req_employee_id and req_employee_id.strip() != "":
            employee_id = req_employee_id.strip()
        else:
            employee_id = session.get('employee_id') or current_user.id

        today = datetime.today()
        selected_year = request.args.get('year') or session.get('selected_year') or str(today.year)
        selected_month = request.args.get('month') or session.get('selected_month') or f"{today.month:02d}"

        selected_month = str(selected_month).zfill(2)
        selected_year = str(selected_year)

        if not employee_id:
            return jsonify({}), 400

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות) -----------------
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return jsonify({}), 401

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        # מציאת העובד המדויק של החברה הפעילה לפי ה-local_id או ה-id הגלובלי כדי לאבטח את השילוב
        if str(employee_id).isdigit():
            # המרת מזהה העובד ל-int נקי לפני פנייה לשאילתות של פוסטגרס בענן
            employee_id_int = int(employee_id)
            employee_profile = EmployeeData.query.filter(
                (EmployeeData.local_id == employee_id_int) | (EmployeeData.id == employee_id_int)
            ).filter_by(company_id=safe_company_id).first()
        else:
            employee_profile = EmployeeData.query.filter_by(employee_id=employee_id, company_id=safe_company_id).first()

        if not employee_profile:
            return jsonify({'error': 'Employee not found in this company'}), 403

        #  שליפת המידע מקובץ ה-JSON בדיסק (לפי ה-ID המאובטח והחברה הפעילה)
        # ---------------------------------------------------------------------
        # אנחנו מעבירים את ה-ID האמיתי של העובד או את ה-local_id שלו לפונקציית הטעינה של השעון
        # Render: העברת safe_company_id המומר למנוע טעינת השעות מהדיסק המאובטח
        month_data = load_clock_hours(safe_company_id, str(employee_profile.id), str(selected_year), str(selected_month)) or {}

        # פריסת ה-hours_table כפי שהקוד המקורי שלך מצפה
        hours_table = month_data.get("hours_table", month_data) or {}
        if not hours_table and hasattr(month_data, 'get'):
            hours_table = month_data

        work_day_entries = hours_table.get("work_day_entries") or []
        tax_data = hours_table.get("tax") or {}

        # פונקציית העזר המקורית והנקייה שלכם לניקוי זמנים (נשמרה במלואה ללא שינוי)
        def clean_api_time(time_val):
            if not time_val:
                return "00:00"
            t_str = str(time_val).strip()
            if "T" in t_str:
                t_str = t_str.split("T")[-1]
            if " " in t_str:
                t_str = t_str.split()[-1]
            if "-" in t_str and ":" not in t_str:
                return "00:00"
            
            if not t_str or t_str in ["None", "None:00", "00:00:00", ""]:
                return "00:00"
                
            return t_str[:5]

        clean_entries = []
        for entry in work_day_entries:
            raw_start = entry.get('start') or entry.get('start_time') or entry.get('start-time', '')
            raw_end = entry.get('end') or entry.get('end_time') or entry.get('end-time', '')
            
            clean_entries.append({
                'date': entry.get('date', ''),
                'day': entry.get('day', ''),
                'saturday': entry.get('saturday', ''),
                'holiday': entry.get('holiday', ''),
                'start_time': clean_api_time(raw_start), 
                'end_time': clean_api_time(raw_end),     
                'totalHours': entry.get('totalHours') or entry.get('total_hours') or entry.get('total-hours') or '0.0',
                'task': entry.get('task') or entry.get('task-description') or entry.get('task_description', '')
            })

        # אם אין רשומות, מפעיל את מנגנון הגיבוי המקורי שלכם get_clockinout_days
        if not clean_entries:
            # Render: העברת safe_company_id המומר למערך הגיבויים המשלים
            clean_entries = get_clockinout_days(selected_year, selected_month, str(employee_profile.id), safe_company_id)

        dailyFields = [
            'date', 'day', 'saturday', 'holiday', 'start_time', 'end_time', 'totalHours', 'task'
        ]

        db.session.close() 

        # החזרת אובייקט ה-JSON המלא, התואם ב-100% לפונקציית האכלוס ב-Front-end של המאסטר
        return jsonify({
            "employee_id": str(employee_id),
            "month": selected_month,
            "year": selected_year,
            "work_day_entries": clean_entries,
            "tax": tax_data,
            "dailyFields": dailyFields
        })

    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
            db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Error in get_clock_hours_data route: {e}")
        return jsonify({"error": str(e)}), 500


# ------------------------------------------------------------------
#  API Endpoint: טעינת נתוני הדו"ח החודשי לתוך ה-JS (GET)
# ------------------------------------------------------------------

@app.route('/api/report_data/<int:year>/<int:month>', methods=['GET'])
@login_required
def get_report_data(year, month):
    try:
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        #  קורא קודם את המזהה שהתקבל מהבקשה (עבור מנהל) ואם אין - לוקח את המשתמש הנוכחי (עבור עובד)
        req_emp_id = request.args.get('employee_id') or request.args.get('employeeId')
        
        if req_emp_id and req_emp_id.strip() != "":
            raw_id = req_emp_id.strip()
            
            # המרת ה-ID הנבחר ל-int נקי לפני שהוא נוגע בשאילתות ה-SQL
            try:
                raw_id_int = int(raw_id)
            except (ValueError, TypeError):
                raw_id_int = 0
                
            # מוודא שזה העובד של החברה הפעילה בלבד
            employee_profile = EmployeeData.query.filter(
                (EmployeeData.local_id == raw_id_int) | (EmployeeData.id == raw_id_int)
            ).filter_by(company_id=safe_company_id).first()
            selected_employee_id = employee_profile.id if employee_profile else None
        else:
            selected_employee_id = session.get('employee_id') or current_user.id

        if not selected_employee_id:
            return jsonify([])

        # Render: העברת ה-safe_company_id המומר למנוע נתיבי הקבצים בדיסק המאובטח
        _, _, csv_file_path = get_company_clock_paths(safe_company_id, selected_employee_id, year, month)
        
        if not os.path.isfile(csv_file_path):
            return jsonify([])

        days_map = {}

        with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                row_date_str = row.get('Date', '').strip() # פורמט YYYY-MM-DD
                if row_date_str and '-' in row_date_str:
                    try:
                        d_parts = row_date_str.split('-')
                        if len(d_parts) == 3:
                            # החזרת האינדקסים של המערך לקריאה תקינה של מחרוזת התאריך
                            r_year = int(d_parts[0])
                            r_month = int(d_parts[1])
                            r_day = int(d_parts[2])
                            
                            if r_year == year and r_month == month:
                                api_key = f"{r_year}-{str(r_month).zfill(2)}-{str(r_day).zfill(2)}"
                                
                                if api_key not in days_map:
                                    days_map[api_key] = {
                                        'date': api_key, 
                                        'clicks_start': [], 
                                        'clicks_end': [],
                                        'task': '',
                                        'duration': '0.0'
                                    }
                                
                                raw_time = row.get('Time', '').strip()
                                
                                if raw_time and raw_time != "00:00:00":
                                    if "T" in raw_time:
                                        raw_time = raw_time.split("T")[-1]
                                    
                                    if "-" in raw_time:
                                        time_parts = raw_time.split()
                                        raw_time = time_parts[-1] if time_parts else "00:00:00"
                                    
                                    if ":" in raw_time:
                                        if row.get('Type') == 'START':
                                            days_map[api_key]['clicks_start'].append(raw_time)
                                        elif row.get('Type') == 'END':
                                            days_map[api_key]['clicks_end'].append(raw_time)
                                
                                if row.get('Type') == 'END' and row.get('Duration') and row.get('Duration') != '0.0':
                                    days_map[api_key]['duration'] = row.get('Duration')
                                
                                if row.get('Task'):
                                    days_map[api_key]['task'] = row.get('Task')
                    except:
                        pass

        report_list = []
        for api_key, day_data in days_map.items():
            start_time_raw = min(day_data['clicks_start']) if day_data['clicks_start'] else ''
            end_time_raw = max(day_data['clicks_end']) if day_data['clicks_end'] else ''
            
            if start_time_raw == "00:00:00": start_time_raw = ''
            if end_time_raw == "00:00:00": end_time_raw = ''
            
            start_time = ""
            if start_time_raw and "-" not in str(start_time_raw):
                start_time = start_time_raw[:5]

            end_time = ""
            if end_time_raw and "-" not in str(end_time_raw):
                end_time = end_time_raw[:5]

            report_list.append({
                'date': day_data['date'],
                'start_time': start_time, 
                'end_time': end_time,     
                'task': day_data['task'], 
                'totalHours': str(day_data['duration']) 
            })

        return jsonify(report_list)

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Critical error inside get_report_data monthly API: {e}")
        return jsonify([]), 500





# ----------------------
#  Employee Clock In/Out   
# ----------------------

@app.route('/clock-in-out', methods=['GET', 'POST'])
@login_required
def clock_in_out():
    try:
        language = get_lang()
        months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני',
                  'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
        years = list(range(2020, 2041))

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()

        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('invoice'))

        # Render: המרה קשיחה ל-int של ה-company_id למניעת קריסות SQL ב-PostgreSQL
        safe_company_id = int(active_company_id)

        db.session.expire_all()

        if session.get('role') == 'employee':
            active_employee_id = session.get('employee_id')
            if not active_employee_id:
                flash("שגיאה: מזהה עובד חסר ב-Session, נא להיכנס מחדש.", "danger")
                return redirect(url_for('login'))
            
            #  המרת מזהה העובד החם מהסשן ל-int למניעת קריסת VARCHAR מול Integer ב-Render
            try:
                active_employee_id_int = int(active_employee_id)
            except (ValueError, TypeError):
                active_employee_id_int = 0

            employees = EmployeeData.query.filter_by(id=active_employee_id_int, company_id=safe_company_id).all()
        else:
            employees = EmployeeData.query.filter_by(company_id=safe_company_id).order_by(EmployeeData.employee_name).all()

        today = datetime.today()
        default_month = f"{today.month:02d}"
        default_year = str(today.year)

        session.setdefault('employee_id', '')
        session.setdefault('selected_month', default_month)
        session.setdefault('selected_year', default_year)
        session.setdefault('employee_data', {})
        session.setdefault('hours_table', {'work_day_entries': [], 'tax': {}})


        if request.method == 'POST':
            form_type = request.form.get('form_type')

            if form_type == 'save_all_data':
                try:
                    if session.get('role') == 'employee':
                        selected_employee_id = session.get('employee_id')
                    else:
                        selected_employee_id = request.form.get('employee_id', '').strip() or session.get('employee_id')
                        
                    selected_month = request.form.get('employeeMonth', '').strip() or session.get('selected_month')
                    selected_year = request.form.get('employeeYear', '').strip() or session.get('selected_year')
                    captured_id_number = request.form.get('id_number', '').strip()

                    if not selected_employee_id or not selected_month or not selected_year:
                        flash("נא לבחור עובד, חודש ושנה!", "warning")
                        return redirect(url_for('clock_in_out'))

                    # Render: המרת ה-ID הנבחר מה-Combo Box ל-int למניעת קריסת ה-LIMIT והחיפוש ב-PostgreSQL!
                    try:
                        selected_employee_id_int = int(selected_employee_id)
                    except (ValueError, TypeError):
                        selected_employee_id_int = 0

                    employee = EmployeeData.query.filter_by(id=selected_employee_id_int, company_id=safe_company_id).first()
                    if not employee:
                        flash("שגיאה: העובד המבוקש אינו קיים במערכת שלך", "danger")
                        return redirect(url_for('clock_in_out'))

                    u_bound = User.query.filter_by(email=employee.email, company_id=safe_company_id).first() if employee.email else None
                    target_storage_id = str(u_bound.id if u_bound else employee.id)

                    active_lang = get_lang()
                    trans_emp = load_employee_translated(employee, active_lang, company_id=safe_company_id) or {}
                            
                    form_data = session.get('employee_data', {})
                    form_data['employee_name'] = trans_emp.get('name') or employee.employee_name or ""
                    form_data['id_number'] = captured_id_number or employee.id_number or ""
                    session['employee_data'] = form_data

                    date_key = f"{selected_month}/{selected_year}"

                    hours_table_json = request.form.get('hours_table_data') or request.form.get('hours_table_json')
                    table_data = {'work_day_entries': [], 'tax': {}}
                    
                    if hours_table_json:
                        try:
                            parsed = json.loads(hours_table_json)
                            hours_table = parsed.get('hours_table', parsed)
                            table_data['work_day_entries'] = hours_table.get('work_day_entries', [])
                            table_data['tax'] = hours_table.get('tax', {})
                            session['hours_table'] = table_data
                        except json.JSONDecodeError:
                            flash("שגיאה בקריאת נתוני שעות העבודה", "danger")
                            return redirect(url_for('clock_in_out'))

                    _, _, csv_file_path = get_company_clock_paths(safe_company_id, target_storage_id, selected_year, selected_month)
                    existing_csv_rows = {}
                    if os.path.exists(csv_file_path):
                        try:
                            with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
                                reader = csv.DictReader(f)
                                for r in reader:
                                    r_date = r.get('Date', '').strip()
                                    r_type = r.get('Type', '').strip()
                                    if r_date and r_type:
                                        existing_csv_rows[(r_date, r_type)] = r
                        except:
                            pass

                    month_filter = f"{selected_year}-{selected_month.zfill(2)}-%"
                    Timesheet.query.filter(
                        Timesheet.company_id == safe_company_id,
                        Timesheet.employee_id == target_storage_id,
                        Timesheet.date.like(month_filter)
                    ).delete(synchronize_session=False)

                    new_entries = []
                    for entry in table_data['work_day_entries']:
                        raw_day = str(entry.get('day', '1')).zfill(2)
                        entry_date_str = f"{selected_year}-{selected_month.zfill(2)}-{raw_day}"
                        
                        start_t = entry.get('start_time') or entry.get('start') or entry.get('start-time') or ''
                        end_t = entry.get('end_time') or entry.get('end') or entry.get('end-time') or ''
                        task_t = entry.get('task') or entry.get('task_description') or entry.get('task-description') or ''
                        total_t = entry.get('totalHours') or entry.get('total_hours') or entry.get('total-hours') or '0.0'

                        if start_t and "T" in start_t: start_t = start_t.split("T")[-1]
                        if end_t and "T" in end_t:     end_t = end_t.split("T")[-1]
                        
                        if start_t: start_t = start_t.strip()[:5]
                        if end_t:   end_t = end_t.strip()[:5]
                        
                        if not start_t or start_t in ["None", "None:00", ""]: start_t = "00:00"
                        if not end_t or end_t in ["None", "None:00", ""]:     end_t = "00:00"

                        saved_start = existing_csv_rows.get((entry_date_str, 'START'), {})
                        final_loc = saved_start.get('Location', 'None') if saved_start.get('Location') else 'None'
                        if not task_t:
                            task_t = saved_start.get('Task', '')

                        if start_t and end_t and start_t != "00:00" and end_t != "00:00":
                            new_timesheet = Timesheet(
                                company_id=safe_company_id, 
                                employee_id=target_storage_id,  
                                employee_name=employee.employee_name,
                                id_number=employee.id_number or captured_id_number or "",
                                date=entry_date_str, 
                                startTime=start_t, 
                                endTime=end_t,     
                                startLocation=final_loc,
                                endLocation=final_loc,
                                task=task_t,
                                totalHours=float(total_t or 0.0) 
                            )
                            new_entries.append(new_timesheet)

                    if new_entries:
                        db.session.add_all(new_entries)
                    db.session.commit() 

                    session['employee_id'] = selected_employee_id
                    session['selected_month'] = selected_month
                    session['selected_year'] = selected_year
                    session['month_result'] = date_key 

                    save_clock_hours(safe_company_id, target_storage_id, selected_year, selected_month, table_data)

                    db.session.close()
                    flash("נתוני השעות והמשימות נשמרו בהצלחה למערכת!", "success")
                    return redirect(url_for('clock_in_out'))

                except Exception as e:
                    if 'db' in locals():
                        db.session.rollback()
                    db.session.close()
                    import traceback
                    traceback.print_exc()
                    flash(f"שגיאה בעת שמירת נתוני השעות: {str(e)}", "danger")
                    return redirect(url_for('clock_in_out'))

        # ===== Handle GET (or fallback) =====
        if session.get('role') == 'employee':
            selected_employee_id = session.get('employee_id')
        else:
            selected_employee_id = request.args.get('employee_id') or session.get('employee_id', '')
              
        selected_month = request.args.get('month') or session.get('selected_month', default_month)
        selected_year = request.args.get('year') or session.get('selected_year', default_year)
        
        session['employee_id'] = selected_employee_id
        session['selected_month'] = selected_month
        session['selected_year'] = selected_year
        
        month_key = f"{selected_year}-{selected_month.zfill(2)}"

        form_data = session.get('employee_data', {})
        
        # Render: המרת מזהה העובד ב-GET ל-int למניעת קריסת VARCHAR מול Integer ב-PostgreSQL!
        employee = None
        if selected_employee_id and str(selected_employee_id).strip() not in ['', '0']:
            try:
                selected_employee_id_int = int(selected_employee_id)
                employee = EmployeeData.query.filter_by(id=selected_employee_id_int, company_id=safe_company_id).first()
            except (ValueError, TypeError):
                employee = None
        
        if employee:
            form_data['employee_id'] = employee.id 
            form_data['employee_name'] = employee.employee_name
            form_data['id_number'] = employee.id_number
            form_data['month_result'] = f"{selected_month}/{selected_year}"
        
        hours_table = session.get('hours_table', {'work_day_entries': [], 'tax': {}})
        
        try:
            days_calc = get_days_in_month(int(selected_year), int(selected_month))
            total_days = len(days_calc) if isinstance(days_calc, list) else int(days_calc)
        except:
            total_days = 31

        if selected_employee_id and employee:
            u_bound = User.query.filter_by(email=employee.email, company_id=safe_company_id).first() if employee.email else None
            target_storage_id = str(u_bound.id if u_bound else employee.id)

            # Render: המרה מפורשת ל-int של מזהה העובד בשאילתת השעון למניעת קריסת VARCHAR מול Integer ב-PostgreSQL
            try:
                target_storage_id_int = int(target_storage_id)
            except (ValueError, TypeError):
                target_storage_id_int = target_storage_id

            month_filter = f"{selected_year}-{selected_month.zfill(2)}-%"
            
            timesheet_entries = Timesheet.query.filter(
                Timesheet.company_id == safe_company_id,
                Timesheet.employee_id == target_storage_id_int,  
                Timesheet.date.like(month_filter)
            ).all()
            
            work_day_entries = []
            db_entries_by_day = {}
            
            if timesheet_entries:
                for entry in timesheet_entries:
                    try:
                        day_num = int(entry.date.split('-')[2])
                        db_entries_by_day[day_num] = entry
                    except:
                        pass

            csv_entries_by_day = {}
            
            _, _, csv_file_path = get_company_clock_paths(
                safe_company_id, 
                target_storage_id,  
                selected_year, 
                selected_month
            )
            
            if os.path.isfile(csv_file_path):
                try:
                    with open(csv_file_path, 'r', encoding='utf-8-sig') as f:
                        reader = csv.DictReader(f)
                        for row in reader:
                            row_date = row.get('Date', '').strip()
                            if row.get('employee_id') == target_storage_id:
                                try:
                                    d_parts = row_date.split('-')
                                    r_year = d_parts[0]
                                    r_month = d_parts[1]
                                    r_day = int(d_parts[2])
                                    
                                    if r_month == selected_month and r_year == selected_year:
                                        if r_day not in csv_entries_by_day:
                                            csv_entries_by_day[r_day] = {'start': '', 'end': '', 'total': '0.0', 'task': '', 'location': 'None'}
                                        
                                        if row.get('Type') == 'START':
                                            csv_entries_by_day[r_day]['start'] = row.get('Time', '')
                                            if row.get('Location'):
                                                csv_entries_by_day[r_day]['location'] = row.get('Location', 'None')
                                        elif row.get('Type') == 'END':
                                            csv_entries_by_day[r_day]['end'] = row.get('Time', '')
                                            csv_entries_by_day[r_day]['total'] = row.get('Duration', '0.0')
                                        
                                        if row.get('Task'):
                                            csv_entries_by_day[r_day]['task'] = row.get('Task', '')
                                except:
                                    pass
                except:
                    pass

            for day in range(1, total_days + 1):
                entry_date_str = f"{selected_year}-{selected_month.zfill(2)}-{str(day).zfill(2)}"
                
                day_start = ''
                day_end = ''
                day_total = '0.0'
                day_task = ''
                day_loc = 'None'

                if day in db_entries_by_day:
                    db_item = db_entries_by_day[day]
                    day_start = db_item.startTime or ''
                    day_end = db_item.endTime or ''
                    day_total = str(db_item.totalHours or '0.0')
                    day_task = db_item.task or ''
                    day_loc = db_item.startLocation or 'None'
                
                if day in csv_entries_by_day:
                    csv_item = csv_entries_by_day[day]
                    if not day_start: day_start = csv_item['start']
                    if not day_end: day_end = csv_item['end']
                    if day_total == '0.0' or day_total == '0': day_total = str(csv_item['total'] or '0.0')
                    if not day_task: day_task = csv_item['task']
                    if day_loc == 'None' or not day_loc: day_loc = csv_item['location']

                if day_start and "T" in day_start: day_start = day_start.split("T")[-1]
                if day_end and "T" in day_end:     day_end = day_end.split("T")[-1]
                
                if day_start: day_start = day_start.strip()[:5]
                if day_end:   day_end = day_end.strip()[:5]
                
                if not day_start or day_start in ["None", "None:00", "00:00:00"]: day_start = "00:00"
                if not day_end or day_end in ["None", "None:00", "00:00:00"]:     day_end = "00:00"

                work_day_entries.append({
                    'day': day,
                    'date': entry_date_str,
                    'start_time': day_start, 
                    'end_time': day_end,
                    'totalHours': day_total,
                    'task': day_task,
                    'location': day_loc
                })
                        
            hours_table['work_day_entries'] = work_day_entries
        else:
            hours_table = {'work_day_entries': [], 'tax': {}}

        session['form_data'] = form_data
        session['hours_table'] = hours_table

        # Render: המרה קשיחה של החברה הפעילה למספר שלם למניעת קריסות טיפוסים
        safe_active_company_id = int(active_company_id)

        company_obj = db.session.get(Company, safe_active_company_id) if safe_active_company_id else None
        translated_company = load_company_translated(company_obj, language) if company_obj else {}

        if employee:
            #  הבטחה שגם מזהה החברה וגם מזהה העובד יעברו המרה מפורשת ל-int לפני השאילתות של ה-User
            u_bound = User.query.filter_by(email=employee.email, company_id=safe_active_company_id).first() if employee.email else None
            target_storage_id = str(u_bound.id if u_bound else employee.id)
            
            company_all_clock_hours = load_clock_hours(safe_active_company_id, target_storage_id, selected_year, selected_month) or {}
        else:
            company_all_clock_hours = {}

        if company_all_clock_hours and "work_day_entries" in company_all_clock_hours:
            for entry in company_all_clock_hours["work_day_entries"]:
                for t_key in ["start_time", "end_time", "start", "end"]:
                    if entry.get(t_key):
                        t_val = str(entry[t_key]).strip()
                        if "T" in t_val: t_val = t_val.split("T")[-1]
                        entry[t_key] = t_val[:5]

        employee_i18n_list = {}
        for emp_row in employees:
            # העברת ה-safe_active_company_id המומר ללולאת הרינדור של שאר העובדים
            trans = load_employee_translated(emp_row, language, company_id=safe_active_company_id) or {}
                
            key = str(emp_row.local_id) if getattr(emp_row, "local_id", None) else f"user_{emp_row.id}"
            employee_i18n_list[key] = {
                "name": trans.get("name") or emp_row.employee_name or ""
            }

        db.session.close() 

        return render_template(
            'clock_in_out.html',
            form_data=session.get('form_data', {}),
            hours_table=session.get('hours_table', {}),
            employee_data=session.get('employee_data', {}),
            selected_employee_id=selected_employee_id,
            employeeMonth=selected_month,
            employeeYear=selected_year,
            month_result=session.get('month_result', ''),
            months=months,
            years=years,
            employees=employees,
            days_data=total_days,
            company=translated_company,
            company_db=company_obj,
            language=language,
            employee_i18n_list=employee_i18n_list,
            all_hours=json.dumps(company_all_clock_hours, ensure_ascii=False)
        )

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        flash(f"שגיאה בעת טעינת נתוני השעות: {str(e)}", "danger")
        return redirect(url_for('clock_in_out'))


# ----------------------
# HoursCard Main Page: timesheet
# ----------------------

@app.route("/timesheet", methods=["GET", "POST"])
@login_required
def timesheet():
    try:
        today = datetime.today()
        months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני',
                  'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
        years = list(range(2020, 2041))

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('invoice'))

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        db.session.expire_all()

        if request.method == "POST":
            selected_month = request.form.get("timesheetMonth") or f"{today.month:02d}"
            selected_year = request.form.get("timesheetYear") or str(today.year)
            employee_id = request.form.get("employee_id") or session.get("employee_id")
        else:
            selected_month = session.get("timesheet_month", f"{today.month:02d}")
            selected_year = session.get("timesheet_year", str(today.year))
            employee_id = session.get("employee_id") 

        if not employee_id:
            flash("יש לבחור עובד כדי לצפות בגיליון שעות.", "danger")
            return redirect(url_for('clock_in_out')) 

        try:
            employee_id_int = int(employee_id)
        except (ValueError, TypeError):
            employee_id_int = 0

        employee = EmployeeData.query.filter_by(id=employee_id_int, company_id=safe_company_id).first()
        if not employee:
            flash("שגיאה: פרטי העובד לא נמצאו במערכת שלך.", "danger")
            session.pop("employee_id", None)
            return redirect(url_for('clock_in_out'))

        language = get_lang()
        trans_emp = load_employee_translated(employee, language, company_id=safe_company_id) or {}

        employee_name = trans_emp.get("name") or employee.employee_name or ""
        id_number = employee.id_number 

        session["timesheet_month"] = str(selected_month).zfill(2)
        session["timesheet_year"] = selected_year
        session["employee_id"] = employee_id

        # Render: שימוש ב-safe_company_id המומר למספר שלם בשאילתת ה-Timesheet
        timesheet_data = (
            Timesheet.query
            .filter_by(company_id=safe_company_id, employee_id=employee.id) 
            .filter(Timesheet.date.like(f"{selected_year}-{selected_month.zfill(2)}-%"))
            .order_by(Timesheet.date.desc())
            .all()
        )

        # Render: העברת ה-safe_company_id המומר לפונקציית מנוע נתיבי הדיסק
        _, _, csv_file_path = get_company_clock_paths(safe_company_id, employee.id, selected_year, selected_month)
        csv_metadata_map = {}

        if os.path.isfile(csv_file_path):
            try:
                with open(csv_file_path, 'r', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        row_date = row.get('Date', '').strip()
                        row_type = row.get('Type', '').strip()
                        
                        if row_date and row_type:
                            if row_date not in csv_metadata_map:
                                csv_metadata_map[row_date] = {'Task': '', 'Location': 'None'}
                            
                            if row.get('Task'):
                                csv_metadata_map[row_date]['Task'] = row.get('Task')
                            if row_type == 'START' and row.get('Location') and row.get('Location') != 'None':
                                csv_metadata_map[row_date]['Location'] = row.get('Location')
            except:
                pass

        for item in timesheet_data:
            if item.date in csv_metadata_map:
                csv_day_data = csv_metadata_map[item.date]
                if csv_day_data['Task'] and not item.task:
                    item.task = csv_day_data['Task']
                if csv_day_data['Location'] and csv_day_data['Location'] != 'None':
                    item.startLocation = csv_day_data['Location']
                    item.endLocation = csv_day_data['Location']

        # Render: שימוש ב-safe_company_id המומר בשאילתת ה-ShiftState
        shift_state = ShiftState.query.filter_by(company_id=safe_company_id, employee_id=employee.id).first() 
        
        db.session.close()

        return render_template(
            "timesheet.html",
            months=months,
            years=years,
            selected_month=selected_month,
            selected_year=selected_year,
            employee_id=employee_id,
            employee_name=employee_name, 
            id_number=id_number,
            timesheet_data=timesheet_data,
            shift_state=shift_state 
        )

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Timesheet Route Error: {e}")
        return f"Internal Server Error: {e}", 500


# ----------------------
# Save Employee Clock Hours Route
# ----------------------

# === Helper: convert dash-names → snake_case (Defined Globally & Kept Intact) ===
def denormalize_keys(data_dict):
    if isinstance(data_dict, dict):
        return {k.replace("-", "_"): v for k, v in data_dict.items()}
    return data_dict


@app.route('/save_clock_hours', methods=['POST'])
@login_required
def save_clock_hours_route():
    try:
        today = datetime.today()
        default_month = f"{today.month:02d}"
        default_year = str(today.year)

        data = request.get_json() or {}
        
        employee_id = str(data.get('employee_id', '')).strip()
        employee_name = str(data.get('employee_name', '')).strip()
        id_number = str(data.get('id_number', '')).strip()
        month = str(data.get('month', default_month)).strip().zfill(2)
        year = str(data.get('year', default_year)).strip()

        hours_table = data.get('hours_table') or data.get('hours_table_data') or {}
        if isinstance(hours_table, str):
            import json
            try:
                hours_table = json.loads(hours_table)
            except:
                hours_table = {}
                
        if "hours_table" in hours_table:
            hours_table = hours_table["hours_table"]

        work_day_entries = hours_table.get('work_day_entries', []) or data.get('work_day_entries', [])
        tax_data = data.get('tax_data', {}) or hours_table.get('tax', {})

        if not employee_id or employee_id == "null":
            return jsonify(success=False, message="מזהה עובד חסר בבקשה"), 400

        if not work_day_entries:
            return jsonify(success=False, message="אין נתוני שעות לשמירה"), 400

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()

        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        payload_company_id = data.get('company_id')
        if payload_company_id and str(payload_company_id).isdigit():
            if user_role not in ['owner', 'admin'] and int(payload_company_id) != active_company_id:
                print(f"🔒 Security Alert: Cross-tenant data injection blocked for user {current_user.id}")
                return jsonify(success=False, message="Unauthorized cross-tenant operation blocked"), 403
            
            if user_role in ['owner', 'admin']:
                active_company_id = int(payload_company_id)

        # Render: המרה מפורשת ל-int של החברה ומזהה העובד למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)
        try:
            employee_id_int = int(employee_id)
        except (ValueError, TypeError):
            employee_id_int = 0

        employee_profile = EmployeeData.query.filter_by(id=employee_id_int, company_id=safe_company_id).first()
        if not employee_profile:
            return jsonify(success=False, message="שגיאה: העובד אינו משויך לחברה שלך"), 403

        try:
            work_day_entries = [denormalize_keys(entry) for entry in work_day_entries]
            tax_data = denormalize_keys(tax_data)
        except NameError:
            pass

        for entry in work_day_entries:
            start_val = str(entry.get('start', '') or entry.get('start_time', '') or entry.get('start-time', '')).strip()
            end_val   = str(entry.get('end', '') or entry.get('end_time', '') or entry.get('end-time', '')).strip()

            if "T" in start_val: start_val = start_val.split("T")[-1]
            if "T" in end_val:   end_val = end_val.split("T")[-1]

            if not start_val or start_val in ["None", "None:00", ""]: start_val = "00:00"
            if not end_val or end_val in ["None", "None:00", ""]:     end_val = "00:00"

            entry['start'] = start_val[:5]
            entry['end']   = end_val[:5]

        try:
            json_payload = {"hours_table": {"work_day_entries": work_day_entries, "tax": tax_data}}
            # Render: העברת safe_company_id המומר למנוע שמירת הקבצים בדיסק המאובטח
            save_clock_hours(safe_company_id, employee_id, year, month, json_payload)
        except:
            pass

        session['table_data'] = work_day_entries

        # Render: העברת safe_company_id המומר למנוע נתיבי הקבצים בדיסק המאובטח
        target_folder, _, csv_file_path = get_company_clock_paths(safe_company_id, employee_id, year, month)
        os.makedirs(target_folder, exist_ok=True)

        existing_csv_rows = {}
        if os.path.exists(csv_file_path):
            try:
                with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
                    reader = csv.reader(f)
                    header_row = next(reader)
                    
                    for r in reader:
                        if len(r) >= 4:
                            r_date = r[1].strip()
                            r_type = r[3].strip()
                            existing_csv_rows[(r_date, r_type)] = r
            except Exception as csv_err:
                print(f"⚠ Warning reading existing CSV for merge: {csv_err}")

        with open(csv_file_path, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['EventID', 'Date', 'Time', 'Type', 'Duration', 'Task', 'employee_id', 'employee_name', 'Location'])
            
            row_id = 1
            
            for entry in work_day_entries:
                raw_date = entry.get('date', '').strip()
                start_time = entry.get('start', '00:00')
                end_time = entry.get('end', '00:00')
                
                total_hours = entry.get('totalHours') or entry.get('total_hours') or entry.get('total-hours') or '0.0'
                task_desc = entry.get('task') or entry.get('task-description') or entry.get('task_description', '')

                saved_start = existing_csv_rows.get((raw_date, 'START'), [])
                saved_end = existing_csv_rows.get((raw_date, 'END'), [])

                old_start_time = saved_start[2].strip() if len(saved_start) >= 3 else '00:00:00'
                old_end_time = saved_end[2].strip() if len(saved_end) >= 3 else '00:00:00'
                old_duration = saved_end[4].strip() if len(saved_end) >= 5 else '0.0'
                
                old_loc = 'None'
                if len(saved_start) == 8:
                    old_loc = saved_start[7]
                elif len(saved_start) >= 9:
                    old_loc = saved_start[8]

                old_task = saved_start[5] if len(saved_start) >= 6 and len(saved_start) != 8 else ''

                if start_time and start_time != "00:00" and start_time != "00:00:00":
                    if old_start_time.startswith(start_time):
                        final_start = old_start_time
                    else:
                        final_start = start_time if len(start_time) == 8 else f"{start_time}:00"
                else:
                    final_start = old_start_time

                if end_time and end_time != "00:00" and end_time != "00:00:00":
                    if old_end_time.startswith(end_time):
                        final_end = old_end_time
                    else:
                        final_end = end_time if len(end_time) == 8 else f"{end_time}:00"
                else:
                    final_end = old_end_time
                
                final_duration = total_hours if (total_hours and total_hours != '0.0' and total_hours != 0 and total_hours != "0.0") else old_duration
                final_task = task_desc if task_desc else old_task
                final_name = employee_name if employee_name else employee_profile.employee_name

                writer.writerow([row_id, raw_date, final_start, 'START', '0.0', final_task, str(employee_profile.local_id), final_name, old_loc])
                row_id += 1
                
                writer.writerow([row_id, raw_date, final_end, 'END', str(final_duration), final_task, str(employee_profile.local_id), final_name, old_loc])
                row_id += 1

        db.session.close() 
        return jsonify(success=True, message="השעות נשמרו בהצלחה!"), 200

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        return jsonify(success=False, message=f"שגיאה בעת שמירת הנתונים: {str(e)}"), 500


# ----------------------------------------------------
# ROUTE:  Save History Download CSV from physical file
# ----------------------------------------------------

@app.route("/save_history", methods=["POST", "GET"])
@login_required
def save_history():
    try:
        user_role = (current_user.role or '').lower() or session.get('role', '').lower()

        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return "Access Denied: Unverified Workspace", 401

        # Render: המרה מפורשת ל-int של ה-company_id למניעת קריסות טיפוסים ב-PostgreSQL
        safe_company_id = int(active_company_id)

        selected_employee_id = session.get('employee_id') or current_user.id

        if not selected_employee_id:
            return "Error: No employee selected for download context.", 400

        #  הבטחה שגם בשאילתת המפתח הראשי ה-ID יעבור כמספר שלם נקי
        try:
            selected_employee_id_int = int(selected_employee_id)
        except (ValueError, TypeError):
            selected_employee_id_int = 0

        employee = db.session.get(Employee, selected_employee_id_int) if str(selected_employee_id).isdigit() else None
        if not employee and str(selected_employee_id).isdigit():
            #  שימוש ב-safe_company_id ובמזהה העובד המומרים ל-int לעבודה חלקה בענן
            employee = EmployeeData.query.filter_by(local_id=selected_employee_id_int, company_id=safe_company_id).first()

        csv_employee_id_str = str(employee.local_id) if employee and employee.local_id else str(selected_employee_id)

        now_date = datetime.today()
        selected_month = request.args.get('month') or session.get('selected_month') or now_date.strftime('%m')
        selected_year = request.args.get('year') or session.get('selected_year') or now_date.strftime('%Y')

        selected_month = str(selected_month).zfill(2)
        selected_year = str(selected_year)

        # Render: העברת ה-safe_company_id המומר למנוע נתיבי הקבצים בדיסק המאובטח
        target_folder, _, csv_file_path = get_company_clock_paths(
            safe_company_id, 
            selected_employee_id, 
            selected_year, 
            selected_month
        )

        if request.method == "GET":
            try:
                session.pop('timesheet_filter_state', None)
            except:
                pass

        if not os.path.exists(csv_file_path):
            os.makedirs(target_folder, exist_ok=True)
            with open(csv_file_path, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['EventID', 'Date', 'Time', 'Type', 'Duration', 'Task', 'employee_id', 'employee_name', 'Location'])

        db.session.close() 

        print(f"✔ Secure CSV history download triggered for Employee: {selected_employee_id} ({selected_month}/{selected_year})")
        
        return send_file(
            csv_file_path,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"employee_{csv_employee_id_str}_{selected_month}_{selected_year}_hours.csv",
            max_age=0 
        )

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Critical Error inside save_history exporter: {e}")
        return f"Internal Server Error: {e}", 500



# --------------------
# Clear Employee Form Data
# ----------------------

@app.route('/clear_clock_display', methods=['POST'])
@login_required
def clear_clock_data():
    try:
        session.pop('employee', None)
        session.pop('employee_id', None)
        session.pop('month_result', None)        
        session.pop('employee_data', None)
        session.pop('form_data', None)
        
        session['hours_table'] = {'work_day_entries': [], 'tax': {}}
        session['employee_id'] = ''

        db.session.close() 
        flash("הטופס ונתוני הזהות נוקו בהצלחה", "info")
        
    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
            db.session.close()
        print(f"⚠️ Warning inside clear_clock_data session purge: {e}")
        
    return redirect(url_for('clock_in_out'))





# ---------------------------------------------------------------------------
#  Secure Route to Serve Employee JSONs from Render Persistent Disk
# ---------------------------------------------------------------------------

@app.route('/static/employees/<path:filename>')
@login_required
def serve_persistent_employees(filename):
    try:
        # משיכת הנתיב הרשמי והנקי ישירות מתוך ה-config (יהיה /data/employees ב-Render ו-static/employees בלוקאל)
        base_dir = app.config.get("EMPLOYEES_DIR")
        
        # Render: אם ה-JavaScript מבקש נתיב שמתחיל ב-"static/employees",  כדי למנוע כפל תיקיות בדיסק
        clean_filename = filename
        if clean_filename.startswith("static/employees/"):
            clean_filename = clean_filename.replace("static/employees/", "", 1)
            
        print(f"📁 Serving employee file safely: {clean_filename} from base base_dir: {base_dir}")
        return send_from_directory(base_dir, clean_filename)

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
        import traceback
        traceback.print_exc()
        return "File Not Found", 404

# -----------------------------------------------------------
#  Database Sync & App Run
# -----------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=not IS_RENDER
    )

