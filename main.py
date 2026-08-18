# -----------------------------
import os
import threading
import json
import re
import logging
import calendar
import shutil
import secrets
import base64
import time
import csv
import xml.etree.ElementTree as ET
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from flask_babel import Babel
import babel.dates
import babel.numbers
import random
# -----------------------------------------------------------
import openpyxl
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, make_response, Response, flash, app, current_app
from deep_translator import GoogleTranslator
from playwright.sync_api import sync_playwright
from flask_mail import Mail, Message  
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, Text
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as SQLSession
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename
from functools import wraps 
from pathlib import Path  
from urllib.parse import parse_qs

# -----------------------------
# Models & Logic Imports (Bulletproof Sync)
# -----------------------------

from database import (
    db, PasswordResetToken, Company, Customer, Employee, Payment, PaymentLink, Invoice, InvoiceItem,
    Product, Category, Supplier, SupplierPurchase, Transaction, User, OwnerUser, ShiftState, Timesheet, TimeEntry, Task,
    OWNER_COMPANY_ID
)


# -----------------------------------------------------------
#  1. Load Environment & Init Flask
# -----------------------------------------------------------

load_dotenv()
app = Flask(__name__, static_folder="static")

IS_RENDER = "RENDER" in os.environ
BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# -----------------------------------------------------------
#  2. הגדרת נתיבי תיקיות (Paths) - Local & Render Safe
#  מעביר את כל קבצי המוצרים, האצוות והלקוחות לתוך הדיסק הקבוע
# -----------------------------------------------------------

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

# נקודת החיבור הרשמית של הדיסק הקבוע ב-Render
PERSISTENT_BASE = "/data" if (IS_RENDER and os.path.exists("/data")) else BASE_DIR

if IS_RENDER:
    #  כל התיקיות הקריטיות של המערכת עוברות לדיסק הקבוע ב-/data!
    # זה מונע מחיקת אצוות, לקוחות וספקים ומבטל לחלוטין את ה-Undelete בריסטארטים!
    CUSTOMERS_DIR     = os.path.join(PERSISTENT_BASE, "customers")
    SUPPLIERS_DIR     = os.path.join(PERSISTENT_BASE, "suppliers")
    COMPANY_DIR       = os.path.join(PERSISTENT_BASE, "companies")
    ITEMS_DIR         = os.path.join(PERSISTENT_BASE, "items")
    TRANSACTIONS_DIR  = os.path.join(PERSISTENT_BASE, "transactions")
    CATEGORIES_DIR    = os.path.join(PERSISTENT_BASE, "categories")    
    CANCELLATIONS_DIR = os.path.join(PERSISTENT_BASE, "cancel_reasons")
    
    # משמרות ועובדים נשארים תחת static לצורך קריאה מהירה מה-JS
    EMPLOYEES_DIR     = os.path.join(BASE_DIR, "static", "employees")
else:
    CUSTOMERS_DIR     = os.path.join(BASE_DIR, "customers")
    SUPPLIERS_DIR     = os.path.join(BASE_DIR, "suppliers")
    ITEMS_DIR         = os.path.join(BASE_DIR, "static", "items")
    TRANSACTIONS_DIR  = os.path.join(BASE_DIR, "static", "transactions")
    CATEGORIES_DIR    = os.path.join(BASE_DIR, "static", "categories")
    COMPANY_DIR       = os.path.join(BASE_DIR, "companies")
    EMPLOYEES_DIR     = os.path.join(BASE_DIR, "static", "employees")     
    CANCELLATIONS_DIR = os.path.join(BASE_DIR, "cancel_reasons")

folders_to_create = [
    ITEMS_DIR,
    TRANSACTIONS_DIR,
    UPLOAD_FOLDER,
    CATEGORIES_DIR,
    CUSTOMERS_DIR,
    EMPLOYEES_DIR,
    SUPPLIERS_DIR,
    COMPANY_DIR,
    CANCELLATIONS_DIR, 
    app.instance_path
]

for d in folders_to_create:
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        print(f"⚠️ Warning: Could not create folder {d}: {e}")

# -----------------------------------------------------------
#  3. Security & Session Config
# -----------------------------------------------------------

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

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

# החברה של הבעלים – אתה יוצר אותה פעם אחת בדף "חברה" (Company ID = 1)
OWNER_COMPANY_ID = 1

# -----------------------------
#  DB Create All & Auto-Migration Script
# -----------------------------

with app.app_context():
    # 1. יצירת הטבלאות במידה והן לא קיימות (SQLite או Postgres)
    db.create_all()

    try:
        broken_customers = Customer.query.filter(Customer.local_id == None).all()
        if broken_customers:
            print(f"🛠️ Found {len(broken_customers)} legacy customers without local_id. Repairing...")
            for cust in broken_customers:
                cust.local_id = cust.id
            db.session.commit()
            print("✔ Legacy customers repaired successfully!")

        broken_managers = User.query.filter_by(role='manager', company_id=OWNER_COMPANY_ID).all()
        for mgr in broken_managers:
            # הגנה: אם המייל שלו הוא לא המייל של האונר, סימן שהוא עסק עצמאי שנתקע בגלל הסלט הישן
            if mgr.email != OWNER_USERNAME:
                # פותחים לו ישות חברה חדשה ב-DB אוטומטית כדי לשחרר לו את הנעילה!
                new_comp = Company(name=mgr.username or mgr.email, email=mgr.email, translations_json="{}")
                db.session.add(new_comp)
                db.session.flush()
                
                mgr.company_id = new_comp.id
                print(f"🛠️ Automatically migrated legacy manager {mgr.email} to new Company ID: {new_comp.id}")
        
        db.session.commit()
    except Exception as migration_err:
        db.session.rollback()
        print(f"⚠️ Warning: Auto-migration script skipped or already fixed: {migration_err}")





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


def get_locale():
    lang = get_lang()      
    country = get_country()  
    return f"{lang}_{country}"


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

def generate_translations(text, source_lang="auto"):
    if not text:
        return {}

    text_str = str(text).strip()
    
    src = str(source_lang).lower().strip() if source_lang else "auto"
    context_text = text_str

    languages = [
        "he","en","fr","es","de","ru","ar","zh-CN","ja","hi","pt","it","nl","sv",
        "tr","ko","pl","uk","fa","ro","cs","el","th","vi","bn","id","ms","tl",
        "hu","bg"
    ]
    result = {}

    def translate_single(lang):
        try:
            if lang == src:
                return lang, text_str

            target_code = "iw" if lang == "he" else lang
            translated = GoogleTranslator(source=src, target=target_code).translate(context_text)

            if translated:
                return lang, translated.strip()

            return lang, text_str
        except:
            return lang, text_str

    with ThreadPoolExecutor(max_workers=10) as executor:
        translations = list(executor.map(translate_single, languages))

    for lang, translated_text in translations:
        result[lang] = translated_text

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
    if currency_code is None:
        currency_code = get_currency()
    symbol = get_currency_symbol(currency_code)
    formatted_num = format_number_only(value)
    return f"{formatted_num} {symbol}"





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
        # Owner תמיד user_id = 0
        if str(user_id) == "0":
            return OwnerUser(OWNER_USERNAME)
        return db.session.get(User, int(user_id))
    except Exception:
        return None

# ---------------------------------------------------------
#  Helpers — Owner Detection
# ---------------------------------------------------------

def is_owner():
    try:
        return (
            session.get('owner_access') is True or
            session.get('role') == 'owner' or
            getattr(current_user, 'role', '') == 'owner' or
            (current_user.is_authenticated and getattr(current_user, 'email', None) == OWNER_USERNAME)
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

        if hasattr(current_user, 'has_valid_access'):
            if getattr(current_user, 'role', '') == 'manager':
                if not current_user.has_valid_access():
                    flash(py_i18n("login.access_expired_or_inactive"), "danger")
                    return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function


def manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        db_role = getattr(current_user, 'role', '').lower() if current_user.is_authenticated else None
        active_role = db_role or session.get('role')

        if active_role != 'manager':
            flash(py_i18n("auth.manager_only"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def employee_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        db_role = getattr(current_user, 'role', '').strip().lower() if current_user.is_authenticated else None
        session_role = str(session.get('role', '')).strip().lower()
        active_role = db_role or session_role

        if active_role != 'employee':
            flash(py_i18n("auth.employee_only"), "danger")
            return redirect(url_for('unauthorized'))
            
        return f(*args, **kwargs)
    return decorated_function

def customer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        db_role = getattr(current_user, 'role', '').lower() if current_user.is_authenticated else None
        active_role = db_role or session.get('role')

        if active_role != 'customer':
            flash(py_i18n("auth.customer_only"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def customer_self_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        selected_customer_id = kwargs.get('customer_id') or request.args.get('customer_id')
        
        db_role = getattr(current_user, 'role', '').lower() if current_user.is_authenticated else None
        active_role = db_role or session.get('role')

        if active_role == 'customer':
            if str(session.get('customer_id')) != str(selected_customer_id):
                flash(py_i18n("auth.customer_self_only"), "danger")
                return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def customer_or_manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_owner():
            return f(*args, **kwargs)

        db_role = getattr(current_user, 'role', '').lower() if current_user.is_authenticated else None
        active_role = db_role or session.get('role')

        if active_role not in ['customer', 'manager', 'employee']:
            flash(py_i18n("auth.no_permission"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


# ---------------------------------------------------------
# Unauthorized Redirect
# ---------------------------------------------------------

@app.route('/unauthorized')
def unauthorized():
    try:
        db_role = getattr(current_user, 'role', '').lower() if current_user.is_authenticated else None
        role = db_role or session.get('role')

        if is_owner() or role == 'manager':
            return redirect(url_for('invoice'))

        if role == 'employee':
            return redirect(url_for('clock_in_out'))

        if role == 'customer':
            return redirect(url_for('customer_dashboard_router'))

        return f"<h1>{py_i18n('auth.no_permission')}</h1>", 403
    except Exception as e:
        print(f"❌ Error in unauthorized navigation redirect handler: {e}")
        return f"<h1>{py_i18n('auth.no_permission')}</h1>", 403


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

        db.session.expire_all()

        # 1. OWNER LOGIN (בעל הפלטפורמה - חברה 1 של הבעלים)
        if login_email == OWNER_USERNAME and password == OWNER_PASSWORD:
            session.clear()

            owner_obj = OwnerUser(OWNER_USERNAME)
            login_user(owner_obj)

            session['owner_access']   = True
            session['user_id']        = 0
            session['role']           = 'owner'
            session['user_role']      = 'owner'
            session['company_id']     = OWNER_COMPANY_ID  
            session['company_name']   = "לרקוד על הגג"
            session['company_email']  = "appsalarybeno@gmail.com"
            session['customer_id']    = 0  

            db.session.commit()
            db.session.close() 
            flash(py_i18n('login.owner_success'), 'success')
            return redirect(url_for('invoice'))

        # 2. USER LOGIN (אימות והצלבת מפתחות מול ה-Tenant והחברה)
        user = None

        if company_email:
            target_company = Company.query.filter_by(email=company_email).first()
            if target_company:
                matching_users = User.query.filter_by(email=login_email, company_id=target_company.id).all()
                for potential_user in matching_users:
                    if potential_user.check_password(password) or potential_user.password_hash == password:
                        user = potential_user
                        break
        else:
            matching_users = User.query.filter_by(email=login_email).all()
            for potential_user in matching_users:
                if potential_user.check_password(password) or potential_user.password_hash == password:
                    user = potential_user
                    break  

        if not user:
            flash(py_i18n("login.invalid_credentials"), "danger")
            return redirect(url_for('login'))

        if not user.is_active or (user.is_approved is False):
            flash(py_i18n("login.access_expired_or_inactive"), "danger")
            return redirect(url_for('login'))

        if user.access_expires_at and user.access_expires_at < datetime.utcnow():
            flash(py_i18n("login.access_expired_or_inactive"), "danger")
            return redirect(url_for('login'))

        session.clear()
        login_user(user, force=True)

        session['user_id']     = user.id
        session['role']        = user.role
        session['user_role']   = user.role
        session['company_id']  = user.company_id  

        if user.company_id:
            company_obj = db.session.get(Company, user.company_id)
            if company_obj:
                session['company_name']  = company_obj.name
                session['company_email'] = company_obj.email

        user.last_login = datetime.utcnow()
        db.session.commit()

        # 3. MANAGER LOGIN (מנהל חברה קיימת)
        if user.role == 'manager':
            session['customer_id'] = None
            db.session.close() # סגירה בטוחה של הצינור ב-Render
            flash(py_i18n('login.manager_success'), 'success')
            return redirect(url_for('company'))

        # 4. CUSTOMER LOGIN (לקוח של חברה קיימת)
        if user.role == 'customer':
            customer = Customer.query.filter_by(id=user.id, company_id=user.company_id).first()
            if not customer:
                customer = Customer.query.filter_by(email=user.email, company_id=user.company_id).first()

            if customer:
                session['customer_id']    = customer.id
                session['customer_name']  = customer.customer_name or user.username
                session['customer_email'] = customer.email or user.email
            else:
                session['customer_id']    = user.id
                session['customer_name']  = user.username

            db.session.close() 
            flash(py_i18n('login.customer_success'), 'success')
            return redirect(url_for('customer_dashboard_router'))

        # 5. EMPLOYEE LOGIN 
        user_role_clean = (user.role or '').strip().lower()

        if user_role_clean == 'employee':
            session['customer_id'] = None
            
            employee_profile = Employee.query.filter_by(email=user.email, company_id=user.company_id).first()
            
            if employee_profile:
                session['employee_id'] = employee_profile.id
            else:
                session['employee_id'] = user.id  
            
            session['role'] = 'employee'
            session['company_id'] = user.company_id

            db.session.close() 
            flash(py_i18n('login.success'), 'success')
            
            return redirect(url_for('clock_in_out'))

        db.session.close()
        flash(py_i18n('login.success'), 'success')
        return redirect(url_for('invoice'))

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה חמורה בתהליך החיבור למערכת", "danger")
        return redirect(url_for('login'))


# -----------------------------
# Logout
# -----------------------------

@app.route('/logout', methods=['POST'])
def logout():
    try:
        session.clear()
        
        db.session.close()
        
        flash(py_i18n("auth.logout_success"), "info")
    except Exception as e:
        print(f"⚠️ Warning inside logout session clear: {e}")
        
    return redirect(url_for('login'))


# -----------------------------
# Register (Multi-Tenant Secure Engine)
# -----------------------------

@app.route('/register', methods=['POST'])
def register():
    try:
        username      = request.form.get('username', '').strip()
        email         = request.form.get('email', '').strip().lower()  
        password      = request.form.get('password', '').strip()
        role          = request.form.get('role', 'manager').strip().lower()
        company_email = request.form.get('company_email', '').strip()

        if not email or not password:
            flash(py_i18n('auth.register_missing_fields'), 'warning')
            return redirect(url_for('login'))

        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash(py_i18n('auth.register_email_exists'), 'warning')
            return redirect(url_for('login'))

        # 1) MANAGER – פותח עסק חדש לחלוטין (אורח אנונימי מהאתר)
        if role == 'manager':
            existing_company_email = Company.query.filter_by(email=email).first()
            if existing_company_email:
                error_msg = py_i18n("auth.company_email_taken") if "auth.company_email_taken" in py_i18n("auth.company_email_taken") else "חומת אש: כתובת אימייל זו כבר רשומה במערכת כעסק פעיל!"
                flash(error_msg, "danger")
                return redirect(url_for('login'))

            company_name = username if username else email
            existing_company_name = Company.query.filter_by(name=company_name).first()
            if existing_company_name:
                error_msg = py_i18n("auth.company_name_taken").format(name=company_name)
                flash(error_msg, "danger")
                return redirect(url_for('login'))

            new_company = Company(name=company_name, email=email, translations_json="{}")
            db.session.add(new_company)
            db.session.flush()  

            user = User(
                email=email,
                username=username or email,
                role='manager',
                company_id=new_company.id,
                is_active=True,
                is_approved=True,
                access_expires_at=datetime.utcnow() + timedelta(days=30)
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            last_cust = Customer.query.filter_by(company_id=OWNER_COMPANY_ID).order_by(Customer.local_id.desc()).first()
            next_local_id = 1 if not last_cust else (last_cust.local_id or 0) + 1

            platform_customer = Customer(
                id=user.id, 
                local_id=next_local_id,  
                customer_name=company_name,
                email=email,
                company_id=OWNER_COMPANY_ID,
                date=datetime.today().strftime('%d/%m/%Y'),
                role='customer',
                is_active=True
            )
            db.session.add(platform_customer)
            db.session.commit()

            try:
                folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{OWNER_COMPANY_ID}_{next_local_id}")
                os.makedirs(folder, exist_ok=True)
                file_path = os.path.join(folder, "customer.json")
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump({"name": {}}, f, ensure_ascii=False, indent=4)
                print(f"✔ Manager workspace created at: {file_path}")
            except Exception as e:
                print(f"⚠️ Failed to create manager workspace file: {e}")

            login_user(user)
            session['user_id']       = user.id
            session['company_id']    = user.company_id
            session['company_name']  = new_company.name       
            session['company_email'] = new_company.email      
            session['role']          = user.role
            session['user_role']     = user.role
            session['customer_id']   = None

            flash(py_i18n("auth.register_manager_success"), "success")
            return redirect(url_for('invoice'))

        # ------------------ 2) CUSTOMER – לקוח של חברה קיימת ------------------
        elif role == 'customer':
            if not company_email:
                flash(py_i18n("auth.company_email_required"), "danger")
                return redirect(url_for('login'))

            company = Company.query.filter_by(email=company_email).first()
            if not company:
                flash(py_i18n("auth.company_not_found"), "danger")
                return redirect(url_for('login'))

            if company.id == OWNER_COMPANY_ID:
                flash("רישום לקוחות ישירות לחברת הניהול חסום! אנא הזן אימייל של חברה עצמאית", "danger")
                return redirect(url_for('login'))

            existing_user_in_company = User.query.filter_by(email=email, company_id=company.id).first()
            
            if existing_user_in_company:
                if not existing_user_in_company.password_hash or existing_user_in_company.password_hash.strip() == "":
                    existing_user_in_company.set_password(password)
                    existing_user_in_company.username = username or existing_user_in_company.username or email
                    existing_user_in_company.is_active = True
                    existing_user_in_company.is_approved = True
                    
                    customer = Customer.query.filter_by(id=existing_user_in_company.id, company_id=company.id).first()
                    if not customer:
                        last_c = Customer.query.filter_by(company_id=company.id).order_by(Customer.local_id.desc()).first()
                        cust_local_id = 1 if not last_c else (last_c.local_id or 0) + 1
                        
                        today_str = datetime.today().strftime('%d/%m/%Y')
                        customer = Customer(
                            id=existing_user_in_company.id,
                            local_id=cust_local_id,
                            company_id=company.id,
                            date=today_str,
                            customer_name=username or email,
                            id_number="",
                            email=email,
                            role='customer',
                            is_active=True
                        )
                        db.session.add(customer)
                    else:
                        customer.is_active = True
                        if username:
                            customer.customer_name = username

                    db.session.commit()

                    login_user(existing_user_in_company)
                    session['user_id']        = existing_user_in_company.id
                    session['company_id']     = company.id          
                    session['company_name']   = company.name          
                    session['company_email']  = company.email         
                    session['customer_id']    = customer.id     
                    session['customer_name']  = customer.customer_name
                    session['customer_email'] = existing_user_in_company.email
                    session['role']           = 'customer'
                    session['user_role']      = 'customer'

                    flash("חשבונך אומת והסיסמה הוגדרה בהצלחה!", "success")
                    return redirect(url_for('customer_dashboard_router'))
                else:
                    flash("הנך כבר רשום כמשתמש בחברה זו, אנא התחבר", "warning")
                    return redirect(url_for('login'))

            pre_created_customer = Customer.query.filter_by(email=email, company_id=company.id).first()

            if not pre_created_customer:
                duplicate_name = Customer.query.filter_by(customer_name=username, company_id=company.id).first()
                if duplicate_name:
                    flash("שם לקוח זה כבר תפוס בחברה זו, אנא בחר שם אחר", "warning")
                    return redirect(url_for('login'))

            user = User(
                email=email,
                username=username or email,
                role='customer',
                company_id=company.id,
                is_active=True,       
                is_approved=True,     
                access_expires_at=None
            )
            user.set_password(password)
            db.session.add(user)
            db.session.flush()

            today_str = datetime.today().strftime('%d/%m/%Y')

            if pre_created_customer:
                saved_id_number = getattr(pre_created_customer, 'id_number', '')
                saved_local_id  = getattr(pre_created_customer, 'local_id', None)
                
                if not saved_local_id:
                    last_c = Customer.query.filter_by(company_id=company.id).order_by(Customer.local_id.desc()).first()
                    saved_local_id = 1 if not last_c else (last_c.local_id or 0) + 1

                db.session.delete(pre_created_customer)
                db.session.flush()

                new_customer = Customer(
                    id=user.id,  
                    local_id=saved_local_id,  
                    company_id=company.id,
                    date=today_str,
                    customer_name=username or email,
                    id_number=saved_id_number,  
                    email=email,
                    role='customer',
                    is_active=True
                )
                db.session.add(new_customer)
            else:
                last_c = Customer.query.filter_by(company_id=company.id).order_by(Customer.local_id.desc()).first()
                saved_local_id = 1 if not last_c else (last_c.local_id or 0) + 1

                new_customer = Customer(
                    id=user.id,
                    local_id=saved_local_id,
                    company_id=company.id,
                    date=today_str,
                    customer_name=username or email,
                    id_number="",
                    email=email,
                    role='customer',
                    is_active=True
                )
                db.session.add(new_customer)
            
            db.session.commit()

            try:
                folder_name = f"{company.id}_{saved_local_id}"
                folder_path = os.path.join(app.config["CUSTOMERS_DIR"], folder_name)
                os.makedirs(folder_path, exist_ok=True)
                
                file_path = os.path.join(folder_path, "customer.json")
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump({"name": {}, "address": {}, "city": {}, "message": {}}, f, ensure_ascii=False, indent=4)
                    
                print(f"✔ Airtight customer workspace created at: {file_path}")
            except Exception as e:
                print(f"⚠️ Failed to write customer JSON layout: {e}")

            try:
                translate_customer_in_background(
                    customer_id=new_customer.id,
                    company_id=company.id,
                    name=new_customer.customer_name,
                    address="",
                    city="",
                    message=""
                )
            except Exception as e:
                print(f"⚠️ Background translation failed: {e}")

            login_user(user)
            session['user_id']        = user.id
            session['company_id']     = company.id          
            session['company_name']   = company.name          
            session['company_email']  = company.email         
            session['customer_id']    = new_customer.id     
            session['customer_name']  = new_customer.customer_name
            session['customer_email'] = user.email
            session['role']           = 'customer'
            session['user_role']      = 'customer'

            flash(py_i18n("auth.register_customer_success"), "success")
            return redirect(url_for('customer_dashboard_router'))

        # 3) EMPLOYEE – עובד של חברה קיימת במערכת
        elif role == 'employee':
            link_company_id = request.args.get('comp')
            
            if link_company_id:
                assigned_company_id = int(link_company_id)
            elif current_user.is_authenticated and session.get('company_id'):
                assigned_company_id = session.get('company_id')
            elif company_email:
                searched_company = Company.query.filter_by(email=company_email).first()
                if searched_company:
                    assigned_company_id = searched_company.id
                else:
                    flash(py_i18n("auth.company_not_found"), "danger")
                    return redirect(url_for('login'))
            else:
                flash("רישום עובד מחייב קישור לחברה פעילה או אימייל חברה!", "danger")
                return redirect(url_for('login'))

            if assigned_company_id == OWNER_COMPANY_ID:
                flash("רישום עובדים לחברת הניהול חסום!", "danger")
                return redirect(url_for('login'))

            existing_emp_user = User.query.filter_by(email=email, company_id=assigned_company_id).first()
            if existing_emp_user:
                flash(py_i18n('auth.register_email_exists'), 'warning')
                return redirect(url_for('login'))

            emp_company = Company.query.get(assigned_company_id)
            if not emp_company:
                flash(py_i18n("auth.company_not_found"), "danger")
                return redirect(url_for('login'))

            user = User(
                email=email,
                username=username or email,
                role='employee',
                company_id=assigned_company_id,
                is_active=True,
                is_approved=True,
                access_expires_at=datetime.utcnow() + timedelta(days=30)
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            if current_user.is_authenticated and session.get('role') == 'manager':
                flash(f"העובד {user.username} נרשם בהצלחה תחת החברה שלך!", "success")
                return redirect(url_for('invoice'))
            else:
                login_user(user)
                session['user_id']       = user.id
                session['company_id']    = user.company_id
                session['company_name']  = emp_company.name
                session['company_email'] = emp_company.email
                session['role']          = user.role
                session['user_role']     = user.role
                session['customer_id']   = None

                flash(py_i18n("auth.register_employee_success"), "success")
                return redirect(url_for('invoice'))

        flash(py_i18n("auth.register_invalid_role"), "danger")
        return redirect(url_for('login'))

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("Registration error", "danger")
        return redirect(url_for('login'))



# -----------------------------------------------------------
#  Page Clients / Users Management (Multi-Tenant Secure Unified View)
# -----------------------------------------------------------

@app.route('/clients', methods=['GET'])
@login_required
def clients():
    try:
        language = get_lang()
        now_dt = datetime.now(timezone.utc).replace(tzinfo=None)

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        is_owner = (user_role == 'owner') or (session.get('owner_access') is True)
        active_company_id = current_user.company_id or session.get('company_id')

        view_clients = []
        company_obj = None

        if is_owner:
            owner_customers = (
                Customer.query
                .filter_by(company_id=OWNER_COMPANY_ID)
                .order_by(Customer.id.desc())
                .all()
            )

            processed_user_ids = set()

            for c in owner_customers:
                u = User.query.filter_by(email=c.email).first() if c.email else db.session.get(User, c.id)
                
                created_at = u.created_at if u else None
                last_login = u.last_login if u else None
                seconds_left = None
                status_label = "employee.customer_role"
                role_label = 'customer'
                display_company_name = "חברת OWNER"

                if u:
                    status_label = "employee.status_active" if u.is_active else "employee.status_blocked"
                    role_label = u.role
                    processed_user_ids.add(u.id)
                    
                    if u.access_expires_at:
                        delta = (u.access_expires_at - now_dt).total_seconds()
                        seconds_left = max(0, int(delta))
                        if delta <= 0:
                            status_label = "employee.status_expired"
                    
                    u_role_clean = (u.role or '').lower()
                    if u_role_clean == 'manager' and u.company_id:
                        comp_info = db.session.get(Company, u.company_id)
                        if comp_info:
                            display_company_name = comp_info.name

                emp_profile = None
                if c.email:
                    emp_profile = Employee.query.filter_by(email=c.email).first()

                display_name = (
                    (emp_profile.employee_name if emp_profile else None) or 
                    (u.username if u else None) or 
                    c.customer_name or 
                    c.email or 
                    "employee.anonymous_user"
                )

                role_check = (role_label or '').lower()
                view_clients.append({
                    'type': 'manager' if role_check == 'manager' else ('employee' if role_check == 'employee' else 'customer'),
                    'id': c.id,
                    'username': display_name,
                    'email': c.email or (u.email if u else None) or "employee.no_email",
                    'role': role_label,
                    'company_name': display_company_name,
                    'created_at': created_at,   
                    'last_login': last_login,   
                    'status_label': status_label, 
                    'seconds_left': seconds_left
                })

            managers_and_employees = (
                User.query
                .filter(
                    User.company_id == OWNER_COMPANY_ID,
                    User.role.in_(['manager', 'employee', 'Manager', 'Employee'])
                )
                .order_by(User.id.desc())
                .all()
            )

            for m in managers_and_employees:
                if m.id in processed_user_ids:
                    continue

                seconds_left = None
                if m.access_expires_at:
                    delta = (m.access_expires_at - now_dt).total_seconds()
                    seconds_left = max(0, int(delta))

                m_role_clean = (m.role or '').lower()

                if m_role_clean == 'employee':
                    display_company_name = "עובד פלטפורמה - חברה 1"
                    status_type = 'employee'
                    display_status_label = "employee.company_employee" if m.is_active else "employee.status_blocked"
                else:
                    display_company_name = "לקוח של הפלטפורמה"
                    status_type = 'manager'
                    display_status_label = "employee.status_active" if m.is_active else "employee.status_blocked"

                if m.company_id:
                    comp_info = db.session.get(Company, m.company_id)
                    if comp_info:
                        display_company_name = comp_info.name

                if m.access_expires_at and delta <= 0:
                    display_status_label = "employee.status_expired"

                emp_profile_m = None
                if m.email:
                    emp_profile_m = Employee.query.filter_by(email=m.email).first()

                display_name_m = (
                    (emp_profile_m.employee_name if emp_profile_m else None) or 
                    m.username or 
                    m.email or 
                    "employee.anonymous_user"
                )

                view_clients.append({
                    'type': status_type,
                    'id': m.id,
                    'username': display_name_m,
                    'email': m.email or "employee.no_email",
                    'role': m.role,
                    'company_name': display_company_name,
                    'created_at': m.created_at,
                    'last_login': m.last_login,
                    'status_label': display_status_label,
                    'seconds_left': seconds_left
                })

            company_obj = db.session.get(Company, OWNER_COMPANY_ID)

        elif user_role == 'manager':
            if not active_company_id:
                flash(py_i18n("auth.no_company_assigned"), "danger")
                return redirect(url_for('invoice'))

            company_obj = db.session.get(Company, active_company_id)
            
            if not company_obj:
                flash("employee.company_not_found_flash", "danger")
                return redirect(url_for('login'))

            company_records = (
                Customer.query
                .filter_by(company_id=active_company_id)
                .order_by(Customer.id.desc())
                .all()
            )

            processed_emails = set()

            for c in company_records:
                u = User.query.filter_by(email=c.email, company_id=active_company_id).first() if c.email else None
                
                created_at = c.date if hasattr(c, 'date') else (u.created_at if u else None)
                last_login = u.last_login if u else None
                seconds_left = None
                
                if c.email:
                    processed_emails.add(c.email)
                
                is_emp = (u and (u.role or '').lower() == 'employee') or (getattr(c, 'role', 'customer') == 'employee')
                display_type = 'employee' if is_emp else 'customer'
                
                emp_profile = None
                if c.email:
                    emp_profile = Employee.query.filter_by(email=c.email, company_id=active_company_id).first()

                display_name = (
                    (emp_profile.employee_name if emp_profile else None) or 
                    (u.username if u else None) or 
                    c.customer_name or 
                    c.email or 
                    "employee.anonymous_user"
                )
                if u:
                    status_label = "employee.status_active" if u.is_active else "employee.status_blocked"
                    if u.access_expires_at:
                        delta = (u.access_expires_at - now_dt).total_seconds()
                        seconds_left = max(0, int(delta))
                        if delta <= 0:
                            status_label = "employee.status_expired"
                else:
                    status_label = "employee.employee_role" if is_emp else "employee.customer_role"

                view_clients.append({
                    'type': display_type, 
                    'id': u.id if u else c.id,
                    'username': display_name,
                    'email': c.email or "employee.no_email",
                    'role': display_type,
                    'company_name': company_obj.name,
                    'created_at': created_at,   
                    'last_login': last_login,   
                    'status_label': status_label, 
                    'seconds_left': seconds_left
                })

            company_employees_users = (
                User.query
                .filter_by(company_id=active_company_id, role='employee')
                .all()
            )

            for emp_user in company_employees_users:
                if emp_user.email in processed_emails:
                    continue
                
                seconds_left = None
                if emp_user.access_expires_at:
                    delta = (emp_user.access_expires_at - now_dt).total_seconds()
                    seconds_left = max(0, int(delta))

                emp_profile = Employee.query.filter_by(email=emp_user.email, company_id=active_company_id).first()
                display_name = emp_profile.employee_name if emp_profile else (emp_user.username or emp_user.email or "employee.new_employee")

                status_label = "employee.status_active" if emp_user.is_active else "employee.status_blocked"
                if emp_user.access_expires_at and delta <= 0:
                    status_label = "employee.status_expired"

                view_clients.append({
                    'type': 'employee',
                    'id': emp_user.id,
                    'username': display_name,
                    'email': emp_user.email or "employee.no_email",
                    'role': 'employee',
                    'company_name': company_obj.name,
                    'created_at': emp_user.created_at,
                    'last_login': emp_user.last_login,
                    'status_label': status_label,
                    'seconds_left': seconds_left
                })

        else:
            return redirect(url_for('unauthorized'))

        #  MULTI-TENANT TRANSLATION PAYLOAD LAYER
        if is_owner:
            all_employees_db = Employee.query.all()
            all_customers_db = Customer.query.all()
        else:
            all_employees_db = Employee.query.filter_by(company_id=active_company_id).all()
            all_customers_db = Customer.query.filter_by(company_id=active_company_id).all()

        employee_i18n_list = {}
        for emp in all_employees_db:
            try:
                trans_emp = load_employee_translated(emp, language, company_id=emp.company_id) or {}
            except:
                trans_emp = {}
            key_emp = str(emp.local_id) if getattr(emp, "local_id", None) else f"user_{emp.user_id or emp.id}"
            employee_i18n_list[key_emp] = {"name": trans_emp.get("name") or emp.employee_name or ""}

        customer_i18n_list = {}
        for cust in all_customers_db:
            try:
                trans_cust = load_customer_translated(cust, language, company_id=cust.company_id) or {}
            except:
                trans_cust = {}
            key_cust = str(cust.local_id) if getattr(cust, "local_id", None) else f"user_{cust.id}"
            customer_i18n_list[key_cust] = {"name": trans_cust.get("name") or cust.customer_name or ""}

        db.session.close()

        for u_item in view_clients:
            if u_item['created_at'] and hasattr(u_item['created_at'], 'strftime'):
                u_item['created_at'] = u_item['created_at'].strftime('%d/%m/%Y %H:%M')
            else:
                u_item['created_at'] = str(u_item['created_at'] or '')

            if u_item['last_login'] and hasattr(u_item['last_login'], 'strftime'):
                u_item['last_login'] = u_item['last_login'].strftime('%d/%m/%Y %H:%M')
            else:
                u_item['last_login'] = str(u_item['last_login'] or '')

        return render_template(
            'clients.html',
            users=view_clients,
            is_owner=is_owner,
            employee_i18n_list=employee_i18n_list,
            customer_i18n_list=customer_i18n_list,
            company=load_company_translated(company_obj, language) if company_obj else {},
            company_db=company_obj,
            language=language
        )

    except Exception as e:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Critical Error inside clients core engine route: {e}")
        flash("שגיאה בטעינת דף הלקוחות והעובדים", "danger")
        return redirect(url_for('invoice'))


# -----------------------------
# Update Access (Owner / Super Admin only)
# -----------------------------

@app.route('/update_access', methods=['POST'])
@login_required
def update_access():
    try:
        email    = request.form.get('email', '').strip()
        status   = request.form.get('status', '').strip()      
        duration = request.form.get('duration', '').strip()    
        company_id = request.form.get('company_id')            

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        is_owner = (user_role == 'owner') or (session.get('owner_access') is True)
        active_company_id = current_user.company_id or session.get('company_id')

        db.session.expire_all()

        if is_owner:
            if company_id and str(company_id).isdigit():
                user = User.query.filter_by(email=email, company_id=int(company_id)).first()
            else:
                user = User.query.filter_by(email=email).first()
        else:
            user = User.query.filter_by(email=email, company_id=active_company_id).first()

        if not user:
            db.session.close() 
            flash(py_i18n("client.not_found"), "danger")
            return redirect(url_for('clients'))

        target_user_role_clean = (user.role or '').lower()

        if not is_owner:
            if target_user_role_clean in ['manager', 'owner']:
                db.session.close() 
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients'))

            if user.company_id != active_company_id:
                db.session.close() 
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients'))

        user.is_active   = (status == 'active')
        user.is_approved = (status == 'active')

        if duration and duration.isdigit():
            seconds = int(duration)
            user.access_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=seconds)
        else:
            user.access_expires_at = None

        search_company_id = user.company_id if user.company_id else active_company_id
        now = datetime.now()
        current_year = now.strftime('%Y')
        current_month = now.strftime('%m')

        customer = Customer.query.filter_by(id=user.id, company_id=search_company_id).first()
        if not customer and is_owner:
            customer = Customer.query.filter_by(id=user.id, company_id=OWNER_COMPANY_ID).first()

        if not customer:
            customer = Customer.query.filter_by(email=user.email, company_id=search_company_id).first()
            if not customer and is_owner:
                customer = Customer.query.filter_by(email=user.email, company_id=OWNER_COMPANY_ID).first()

        if customer:
            customer.is_active = user.is_active
            if not customer.customer_name:
                customer.customer_name = user.username or user.email or "משתמש ללא שם"

        employee_profile = Employee.query.filter_by(email=user.email, company_id=search_company_id).first()
        if employee_profile:
            if hasattr(employee_profile, 'is_active'):
                employee_profile.is_active = user.is_active

        db.session.commit()

        try:
            if target_user_role_clean == 'employee' and employee_profile:
                target_folder, json_file_path, _ = get_company_clock_paths(
                    search_company_id, 
                    employee_profile.id, 
                    current_year, 
                    current_month
                )
                
                os.makedirs(target_folder, exist_ok=True)
                
                existing_data = {}
                if os.path.isfile(json_file_path):
                    with open(json_file_path, "r", encoding="utf-8") as f:
                        try: 
                            existing_data = json.load(f)
                        except: 
                            existing_data = {}
                
                existing_data.setdefault("hours_table", {"work_day_entries": [], "tax": {}})
                existing_data["is_active"] = user.is_active
                existing_data["email"] = user.email
                existing_data["employee_name"] = employee_profile.employee_name
                
                with open(json_file_path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=2)
                print(f"✔ Monthly JSON access configuration synced safely at: {json_file_path}")

            elif target_user_role_clean == 'customer' and customer:
                if customer.local_id:
                    folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{search_company_id}_{customer.local_id}")
                    file_path = os.path.join(folder, "customer.json")
                    if not os.path.exists(file_path):
                        os.makedirs(folder, exist_ok=True)
                        with open(file_path, "w", encoding="utf-8") as f:
                            json.dump({"name": {}, "address": {}, "city": {}, "message": {}}, f, ensure_ascii=False, indent=4)

                cust_address = getattr(customer, 'address', '')
                cust_city    = getattr(customer, 'city', '')
                cust_message = getattr(customer, 'message', '')

                translate_customer_in_background(
                    customer_id=customer.id,
                    company_id=search_company_id,
                    name=customer.customer_name,
                    address=cust_address,
                    city=cust_city,
                    message=cust_message
                )
        except Exception as file_err:
            print(f"⚠️ Warning: Cloud database disk sync skipped in update_access: {file_err}")

        db.session.close() 
        flash(py_i18n("client.access_updated"), "success")
        return redirect(url_for('clients'))

    except Exception as e:
        db.session.rollback()
        db.session.close()
        import traceback
        traceback.print_exc()
        print(f"❌ Error in update_access control handler: {e}")
        flash("שגיאה חמורה בעדכון הרשאות הגישה", "danger")
        return redirect(url_for('clients'))


# -----------------------------
# Create Reset Token (Helper)
# -----------------------------

def create_reset_token(user):
    try:
        PasswordResetToken.query.filter_by(user_id=user.id).delete(synchronize_session='fetch')

        token = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(hours=1)

        entry = PasswordResetToken(
            user_id=user.id,  
            token=token,
            expires_at=expires
        )
        
        db.session.add(entry)
        db.session.commit() 
        
        print(f"✔ Secure reset token successfully saved to DB for user ID: {user.id}")
        return token
        
    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error in create_reset_token: {e}")
        raise e

# -----------------------------
#   שלב ראשון: שליחת הלינק מהמערכת (E-Mail Send Link)
# -----------------------------

@app.route('/send-reset-link', methods=['POST'])
@login_required  
def send_reset_link():
    try:
        email = request.form.get('email', '').strip()
        
        owner_mode = is_owner() 
        active_company_id = current_user.company_id or session.get('company_id')

        if not email:
            flash("כתובת אימייל חסרה", "danger")
            return redirect(url_for('clients'))

        if owner_mode:
            user = User.query.filter_by(email=email).first()
            if not user:
                req_company_id = request.form.get('company_id')
                if req_company_id:
                    user = User.query.filter_by(email=email, company_id=int(req_company_id)).first()
        else:
            user = User.query.filter_by(email=email, company_id=active_company_id).first()

        if not user:
            flash(py_i18n("client.not_found"), "danger")
            return redirect(url_for('clients'))

        if not owner_mode:
            if user.role in ['manager', 'owner']:
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('unauthorized'))

        PasswordResetToken.query.filter_by(user_id=user.id).delete()

        token = create_reset_token(user)

        base_url = os.getenv('BASE_URL') or request.host_url.rstrip('/')
        reset_url = f"{base_url}/set-password?token={token}"

        msg = Message(
            py_i18n("reset.email_subject"),
            recipients=[user.email], 
            body=f"{py_i18n('reset.email_body')}\n{reset_url}"
        )
        mail.send(msg)

        flash(py_i18n("reset.link_sent"), "success")
        return redirect(url_for('clients'))

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error in send_reset_link: {e}")
        flash("שגיאה במערכת שליחת המייל", "danger")
        return redirect(url_for('clients'))

# -----------------------------
#   שלב שני: קביעת ועדכון הסיסמה בפועל (Set Password via token)
# -----------------------------

@app.route('/set-password', methods=['GET', 'POST'])
def set_password():
    try:
        if session.get('owner_access'):
            if request.method == 'POST':
                flash(py_i18n("reset.owner_info"), "info")
                return redirect(url_for('clients'))
            return render_template('set_password.html')

        token = request.args.get('token') or request.form.get('token')
        if not token:
            flash(py_i18n("reset.invalid_or_expired_token"), "danger")
            return redirect(url_for('login'))

        entry = PasswordResetToken.query.filter_by(token=token).first()
        if not entry or entry.expires_at < datetime.utcnow():
            flash(py_i18n("reset.invalid_or_expired_token"), "danger")
            return redirect(url_for('login'))

        user = entry.user
        if not user:
            user = db.session.get(User, entry.user_id)

        if not user:
            flash(py_i18n("client.not_found"), "danger")
            return redirect(url_for('login'))

        if request.method == 'POST':
            new_pass = request.form.get('password', '').strip()
            if not new_pass:
                flash(py_i18n("reset.password_required"), "warning")
                return redirect(url_for('set_password', token=token))

            user.set_password(new_pass)
            user.is_active = True
            user.is_approved = True

            customer_row = Customer.query.filter_by(id=user.id).first()
            if customer_row:
                customer_row.is_active = True

            db.session.delete(entry)
            db.session.commit()

            flash(py_i18n("reset.password_updated"), "success")
            return redirect(url_for('login'))

        return render_template('set_password.html', token=token)

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בעיבוד בקשת איפוס הסיסמה", "danger")
        return redirect(url_for('login'))


# -----------------------------
# Delete selected users (Owner only)
# -----------------------------

@app.route('/delete_selected_users', methods=['POST'])
@login_required  
def delete_selected_users():
    try:
        ids = request.form.getlist('delete_ids')

        if not ids:
            flash(py_i18n("client.delete_none_selected"), "warning")
            return redirect(url_for('clients'))

        owner_mode = True 
        deleted_count = 0

        db.session.expire_all()

        db.session.execute(db.text("PRAGMA foreign_keys = OFF;"))

        for user_id in ids:
            if not user_id.isdigit():
                continue

            target_uid = int(user_id)
            user = db.session.get(User, target_uid)
            
            owner_cust_row = Customer.query.filter_by(id=target_uid, company_id=OWNER_COMPANY_ID).first()
            owner_local_id = owner_cust_row.local_id if owner_cust_row else None

            target_comp_id = user.company_id if user else None
            if not target_comp_id and owner_cust_row:
                target_comp_id = owner_cust_row.company_id

            try:
                db.session.execute(db.text(f"DELETE FROM shift_states WHERE employee_id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM employee WHERE id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM password_reset_tokens WHERE user_id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM customer WHERE id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM users WHERE id = {target_uid}"))
                db.session.flush()
            except Exception as e:
                print(f"User rows query bypass check: {e}")

            if target_comp_id and target_comp_id != OWNER_COMPANY_ID:
                try:
                    db.session.execute(db.text(f"DELETE FROM transactions WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM payments WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM invoice_items WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM invoices WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM products WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM categories WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM suppliers WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM shift_states WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM employee WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM users WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM customer WHERE company_id = {target_comp_id}"))
                    
                    db.session.execute(db.text(f"DELETE FROM company WHERE id = {target_comp_id}"))
                    db.session.flush()
                except Exception as e:
                    print(f"Company row bypass check: {e}")

                try:
                    comp_dir = os.path.join(app.config["COMPANY_DIR"], str(target_comp_id))
                    if os.path.exists(comp_dir):
                        import shutil
                        shutil.rmtree(comp_dir)
                except: pass

                try:
                    emp_dir = os.path.join(app.config["EMPLOYEES_DIR"], f"company_{target_comp_id}")
                    if os.path.exists(emp_dir):
                        import shutil
                        shutil.rmtree(emp_dir)
                except: pass

            if owner_local_id:
                try:
                    cust_folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{OWNER_COMPANY_ID}_{owner_local_id}")
                    if os.path.exists(cust_folder):
                        import shutil
                        shutil.rmtree(cust_folder)
                except Exception as e:
                    print(f"⚠ Failed to rmtree customer folder: {e}")

            deleted_count += 1

        db.session.execute(db.text("PRAGMA foreign_keys = ON;"))
        db.session.commit()
        db.session.close()

        if deleted_count > 0:
            flash(py_i18n("client.deleted_count").format(count=deleted_count), "success")
        else:
            flash(py_i18n("auth.no_permission"), "danger")

        return redirect(url_for('clients'))

    except Exception as e:
        db.session.rollback()
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
    try:
        email      = request.form.get('email', '').strip()
        new_role   = request.form.get('role', '').strip().lower()
        company_id = request.form.get('company_id') 

        if not email or not new_role:
            flash(py_i18n("client.role_invalid_data"), "danger")
            return redirect(url_for('clients'))

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        owner_mode = (user_role == 'owner') or (session.get('owner_access') is True)
        active_company_id = current_user.company_id or session.get('company_id')

        db.session.expire_all()

        if owner_mode:
            if company_id and str(company_id).isdigit():
                user = User.query.filter_by(email=email, company_id=int(company_id)).first()
            else:
                user = User.query.filter_by(email=email).first()
        else:
            user = User.query.filter_by(email=email, company_id=active_company_id).first()

        if not user:
            db.session.close() 
            flash(py_i18n("client.not_found"), "danger")
            return redirect(url_for('clients'))

        target_user_role_clean = (user.role or '').lower()

        if not owner_mode:
            if target_user_role_clean in ['manager', 'owner']:
                db.session.close() 
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients'))

            if user.company_id != active_company_id:
                db.session.close() 
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients'))

            if new_role in ['manager', 'owner']:
                db.session.close() 
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients'))

        old_role = user.role
        old_company_id = user.company_id
        now = datetime.now()
        current_year = now.strftime('%Y')
        current_month = now.strftime('%m')
        
        if old_role != new_role:
            
            try:
                old_cust = Customer.query.filter_by(email=user.email, company_id=old_company_id).first()
                if old_cust and old_cust.local_id:
                    if target_user_role_clean == 'customer':
                        old_folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{old_company_id}_{old_cust.local_id}")
                        if os.path.exists(old_folder):
                            import shutil
                            shutil.rmtree(old_folder)
            except Exception as e:
                print(f"⚠️ Safe disk cleanup skipped in update_role: {e}")

            user.role = new_role

            if new_role == 'manager':
                company_name = user.username or user.email
                new_company = Company(name=company_name, email=user.email, translations_json="{}")
                db.session.add(new_company)
                db.session.flush()  
                
                old_cust_records = Customer.query.filter_by(email=user.email, company_id=old_company_id).all()
                for o_c in old_cust_records:
                    db.session.delete(o_c)
                
                old_emp_records = Employee.query.filter_by(email=user.email, company_id=old_company_id).all()
                for o_e in old_emp_records:
                    db.session.delete(o_e)

                user.company_id = new_company.id
                user.is_active = True
                user.is_approved = True

                last_c = Customer.query.filter_by(company_id=OWNER_COMPANY_ID).order_by(Customer.local_id.desc()).first()
                next_local_id = 1 if not last_c else (last_c.local_id or 0) + 1

                owner_customer = Customer(
                    id=user.id,
                    local_id=next_local_id,
                    company_id=OWNER_COMPANY_ID,
                    customer_name=company_name,
                    email=user.email,
                    role='customer',
                    is_active=True,
                    date=datetime.today().strftime('%d/%m/%Y')
                )
                db.session.add(owner_customer)
                db.session.flush()

                translate_company_in_background(new_company.id, new_company.name, "", "", "", "", "", user.email, "")

                try:
                    folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{OWNER_COMPANY_ID}_{next_local_id}")
                    os.makedirs(folder, exist_ok=True)
                    file_path = os.path.join(folder, "customer.json")
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump({"name": {}, "address": {}, "city": {}, "message": {}}, f, ensure_ascii=False, indent=4)
                except Exception as e:
                    print(f"⚠️ Failed to create manager customer workspace: {e}")

            elif new_role == 'customer':
                if owner_mode and company_id:
                    user.company_id = int(company_id)

                if not user.company_id:
                    db.session.close() 
                    flash(py_i18n("auth.no_company_assigned"), "danger")
                    return redirect(url_for('clients'))

                emp_prof = Employee.query.filter_by(email=user.email, company_id=old_company_id).first()
                if emp_prof:
                    old_shifts = ShiftState.query.filter_by(employee_id=emp_prof.id, company_id=old_company_id).all()
                    for o_s in old_shifts:
                        db.session.delete(o_s)
                    db.session.delete(emp_prof)

                customer = Customer.query.filter_by(email=user.email, company_id=user.company_id).first()

                if not customer:
                    last_c = Customer.query.filter_by(company_id=user.company_id).order_by(Customer.local_id.desc()).first()
                    next_local_id = 1 if not last_c else (last_c.local_id or 0) + 1

                    customer = Customer(
                        id=user.id,
                        local_id=next_local_id, 
                        customer_name=user.username or user.email or "לקוח חדש",
                        email=user.email,
                        company_id=user.company_id,
                        role='customer',
                        is_active=user.is_active,
                        date=datetime.today().strftime('%d/%m/%Y')
                    )
                    db.session.add(customer)
                else:
                    customer.role = 'customer'
                    if not customer.customer_name:
                        customer.customer_name = user.username or user.email

                db.session.flush()

                try:
                    folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{user.company_id}_{customer.local_id}")
                    os.makedirs(folder, exist_ok=True)
                    file_path = os.path.join(folder, "customer.json")
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump({"name": {}, "address": {}, "city": {}, "message": {}}, f, ensure_ascii=False, indent=4)
                except Exception as e:
                    print(f"⚠️ Failed to create customer workspace layout: {e}")

            elif new_role == 'employee':
                if owner_mode and company_id:
                    user.company_id = int(company_id)
                
                user.is_active = True
                user.is_approved = True

                old_cust_records = Customer.query.filter_by(email=user.email, company_id=user.company_id).all()
                for o_c in old_cust_records:
                    db.session.delete(o_c)
                
                employee_profile = Employee.query.filter_by(email=user.email, company_id=user.company_id).first()
                if not employee_profile:
                    employee_profile = Employee(
                        company_id=user.company_id,
                        employee_name=user.username or user.email or "עובד חדש",
                        email=user.email,
                        id_number="", 
                        user_id=user.id 
                    )
                    db.session.add(employee_profile)
                    db.session.flush() 

                try:
                    target_folder, json_file_path, _ = get_company_clock_paths(
                        user.company_id, 
                        employee_profile.id, 
                        current_year, 
                        current_month
                    )
                    os.makedirs(target_folder, exist_ok=True)
                    
                    json_payload = {
                        "hours_table": {"work_day_entries": [], "tax": {}},
                        "is_active": True,
                        "email": user.email,
                        "employee_name": employee_profile.employee_name
                    }

                    with open(json_file_path, "w", encoding="utf-8") as f:
                        json.dump(json_payload, f, ensure_ascii=False, indent=2)
                    print(f"✔ Monthly static sheet created successfully for new employee context at: {json_file_path}")
                except Exception as file_err:
                    print(f"⚠️ Warning: Cloud database disk sync skipped in update_role employee branch: {file_err}")

        db.session.commit()
        db.session.close() 

        print(f"✔ Database Role Sync Success: Role updated and committed for user: {email}")
        flash(py_i18n("client.role_updated"), "success")
        return redirect(url_for('clients'))

    except Exception as e:
        db.session.rollback()
        db.session.close() 
        import traceback
        traceback.print_exc()
        print(f"❌ Error in update_role execution block: {e}")
        flash("שגיאה חמורה בעדכון תפקיד המשתמש", "danger")
        return redirect(url_for('clients'))





# -----------------------------------------------------------
# Customer Dashboard Router (Multi‑Tenant Safe)
# -----------------------------------------------------------

@app.route('/customer_dashboard_router')
@login_required
def customer_dashboard_router():
    try:
        role = session.get('role')
        is_owner_user = is_owner()  

        if is_owner_user or role == 'manager':
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
        role = session.get('role')
        is_owner_user = is_owner()

        if role != 'customer' and role != 'manager' and not is_owner_user:
            return redirect(url_for('unauthorized'))

        active_company_id  = session.get('company_id')
        active_customer_id = session.get('customer_id')

        customer = Customer.query.filter_by(
            id=active_customer_id,
            company_id=active_company_id
        ).first()

        if not customer:
            customer = Customer.query.filter_by(
                id=active_customer_id,
                company_id=OWNER_COMPANY_ID
            ).first()

        if not customer and role != 'manager' and not is_owner_user:
            print(f"⚠️ Security alert: Customer {active_customer_id} tried accessing company {active_company_id}")
            return redirect(url_for('unauthorized'))

        target_company_id = customer.company_id if customer else active_company_id
        company_obj = db.session.get(Company, target_company_id)
        language = get_lang()
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        is_self_billing = False
        if customer and str(customer.company_id) == str(OWNER_COMPANY_ID) and role == 'manager':
            is_self_billing = True
        elif not customer and (role == 'manager' or is_owner_user):
            is_self_billing = True

        if is_self_billing:
            invoices = (
                Invoice.query
                .filter_by(company_id=target_company_id, customer_id="0")
                .order_by(db.cast(Invoice.invoice_number, db.Integer).desc())
                .all()
            )
        else:
            invoices = (
                Invoice.query
                .filter_by(company_id=target_company_id, customer_id=active_customer_id)
                .order_by(db.cast(Invoice.invoice_number, db.Integer).desc())
                .all()
            )

        # מושך לפי מספר החשבונית המקומי והרץ של החברה למניעת סלטים במע"מ!
        last_invoice = Invoice.query.filter_by(company_id=target_company_id).order_by(Invoice.invoice_number.desc()).first()
        
        if last_invoice and last_invoice.vat_rate is not None:
            default_vat_rate = float(last_invoice.vat_rate)
        else:
            default_vat_rate = 0.0

        if is_self_billing:
            customer_json = {
                "id": "0",  
                "local_id": None,  
                "customer_name": f"★ {company_translated.get('name', company_obj.name if company_obj else 'החברה שלי')}",
                "address": company_translated.get('address', company_obj.address or "") if company_obj else "",
                "city": company_translated.get('city', company_obj.city or "") if company_obj else "",
                "postal_code": company_obj.postal_code or "" if company_obj else "",
                "email": company_obj.email or "" if company_obj else "",
                "phone": company_obj.phone or "" if company_obj else "",
                "message": ""
            }
        else:
            customer_json = {
                "id": customer.id if customer else None,
                "local_id": getattr(customer, 'local_id', customer.id) if customer else None,  
                "customer_name": customer.customer_name or customer.email or "לקוח" if customer else "מנהל מערכת",
                "address": customer.address or "" if customer else "",
                "city": customer.city or "" if customer else "",
                "postal_code": customer.postal_code or "" if customer else "",
                "email": customer.email or "" if customer else "",
                "phone": customer.phone or "" if customer else "",
                "message": customer.message or "" if customer else ""
            }

        # חילוץ סיבת הביטול המתורגמת מהדיסק למניעת קופסאות ריקות ברינדור של invoice.html!
        cancellation_reason_trans = ""
        # לוקחים את החשבונית הראשונה במערך במידה והיא מבוטלת כברירת מחדל של ה-Context
        current_active_inv = invoices[0] if invoices else None
        if current_active_inv and getattr(current_active_inv, 'status', '') == 'canceled':
            cancel_file_data = load_cancellation_file(target_company_id, current_active_inv.id) or {}
            reason_dict = cancel_file_data.get("cancellation_reason", {})
            cancellation_reason_trans = (
                reason_dict.get(language)
                or reason_dict.get("he")
                or getattr(current_active_inv, 'cancellation_reason', '')
                or "ביטול כללי"
            )

        base_ctx = base_invoice_context(customer_id=customer_json["id"])

        base_ctx.update({
            "invoices": invoices,
            "invoice": current_active_inv, # מזרים את האובייקט הפעיל הנוכחי
            "invoice_id": current_active_inv.id if current_active_inv else None,
            "allocation_number": current_active_inv.allocation_number if current_active_inv else None,
            "invoice_number": current_active_inv.invoice_number if current_active_inv else 1,
            "invoice_date": current_active_inv.invoice_date.strftime('%d-%m-%Y') if current_active_inv else datetime.today().strftime('%d-%m-%Y'),

            "customer": customer_json,
            "customer_json": customer_json,
            "all_customers_json": [customer_json],  

            #   Extract and pass localized rows instead of a blank list so reload displays saved items
            "items": base_ctx.get("items", []),
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
            "language": language
        })

        return render_template('invoice.html', **base_ctx)

    except Exception as e:
        if db and db.session:
            db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ View rendering inside customer dashboard context crashed: {e}")
        return redirect(url_for('unauthorized'))


# -----------------------------------------------------------
# Employee Dashboard Router (Multi‑Tenant Safe)
# -----------------------------------------------------------

@app.route('/employee_dashboard', methods=['GET', 'POST'])
@login_required
def employee_dashboard():
    try:
        language = get_lang()
        
        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        active_company_id_session = current_user.company_id or session.get('company_id')

        if user_role != 'employee' and user_role != 'manager':
            print(f"🔒 Security Alert: Unauthorized role access attempt by user {current_user.id}")
            return redirect(url_for('unauthorized'))

        active_employee_id = session.get('employee_id') or current_user.id
        
        if user_role == 'manager':
            req_emp_id = request.args.get('employee_id')
            if req_emp_id:
                active_employee_id = req_emp_id
            else:
                first_emp = Employee.query.filter_by(company_id=active_company_id_session).first()
                active_employee_id = first_emp.id if first_emp else None

        if not active_employee_id:
            flash("שגיאה: מזהה עובד חסר ב-Session, נא להיכנס מחדש.", "danger")
            return redirect(url_for('login'))

        emp_obj = db.session.get(Employee, int(active_employee_id))
        if not emp_obj:
            return redirect(url_for('unauthorized'))

        if user_role == 'manager' and emp_obj.company_id != active_company_id_session:
            return redirect(url_for('unauthorized'))

        active_company_id = emp_obj.company_id

        db.session.expire_all()

        shift_state = ShiftState.query.filter_by(employee_id=emp_obj.id, company_id=active_company_id).first()

        today = datetime.today()
        month_str = f"{today.month:02d}"
        current_month_filter = f"{today.year}-{month_str}-%"
        
        my_shifts = (
            Timesheet.query
            .filter_by(company_id=active_company_id, employee_id=emp_obj.id)
            .filter(Timesheet.date.like(current_month_filter))
            .order_by(Timesheet.date.desc())
            .all()
        )

        for shift in my_shifts:
            if hasattr(shift, 'startTime') and shift.startTime:
                st_val = str(shift.startTime).strip()
                if "T" in st_val: st_val = st_val.split("T")[-1]
                shift.startTime = st_val[:5]
            if hasattr(shift, 'endTime') and shift.endTime:
                et_val = str(shift.endTime).strip()
                if "T" in et_val: et_val = et_val.split("T")[-1]
                shift.endTime = et_val[:5]

        company_obj = db.session.get(Company, active_company_id)
        translated_company = load_company_translated(company_obj, language) if company_obj else {}

        db.session.close()

        ctx = {
            "employee": emp_obj,
            "shift_state": shift_state,  
            "shifts": my_shifts,         
            "timesheet_data": my_shifts, 
            "company": translated_company,
            "company_db": company_obj,
            "language": language
        }

        return render_template('employee_dashboard.html', **ctx)

    except Exception as e:
        db.session.rollback()
        db.session.close() 
        import traceback
        print(f"❌ CRITICAL ERROR in employee_dashboard layout: {e}")
        traceback.print_exc()
        return redirect(url_for('unauthorized'))





# -----------------------------
#  COMPANY FOLDERS Translation (Threading + MULTI COMPANY)
# -----------------------------

def ensure_company_folder(company_id):
    base_dir = app.config["COMPANY_DIR"]
    company_dir = os.path.join(base_dir, str(company_id))
    return company_dir


def translate_company_in_background(
    company_id, name, id_number, deduction_file, address, city, postal_code, phone, email, logo
):
    thread = threading.Thread(
        target=run_company_translation,
        args=(company_id, name, id_number, deduction_file, address, city, postal_code, phone, email, logo)
    )
    thread.daemon = True
    thread.start()


def run_company_translation(
    company_id, name, id_number, deduction_file, address, city, postal_code, phone, email, logo
):
    try:
        name_trans      = generate_translations(name or "")
        address_trans   = generate_translations(address or "")
        city_trans      = generate_translations(city or "")

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

        save_company_file(
            company_id,
            name_clean,
            id_number or "",
            deduction_file or "",
            address_clean,
            city_clean,
            postal_code or "",
            phone or "",
            email or "",
            logo or ""
        )

    except Exception as e:
        print(f"⚠ Company translation failed for {company_id}: {e}")


def save_company_file(
    company_id, name_trans, id_number, deduction_file, address_trans, city_trans, postal_code, phone, email, logo
):
    folder = ensure_company_folder(company_id)
    
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, f"{company_id}.json")

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

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✔ Airtight Company JSON document created at: {file_path}")
    return file_path


def load_company_file(company_id):
    if not company_id:
        return None

    base_dir = app.config["COMPANY_DIR"]
    folder = os.path.join(base_dir, str(company_id))
    file_path = os.path.join(folder, f"{company_id}.json")

    if not os.path.isfile(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None


def load_company_translated(company, language):
    if not company:
        return {
            "name": "", "company_id_number": "", "deduction_file": "",
            "address": "", "city": "", "postal_code": "", "phone": "", "email": "", "logo": ""
        }

    special_mappings = {"zh": "zh-CN", "en": "en"}
    lookup_lang = special_mappings.get(language, language)

    fallback_data = {
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
        return fallback_data

    def get_val(field_key, default_val):
        field_data = data.get(field_key)
        if not isinstance(field_data, dict):
            return field_data if field_data else default_val or ""
        return field_data.get(lookup_lang) or field_data.get("he") or default_val or ""

    return {
        "name": get_val("name", fallback_data["name"]),
        "company_id_number": get_val("company_id_number", fallback_data["company_id_number"]),
        "deduction_file": get_val("deduction_file", fallback_data["deduction_file"]),
        "address": get_val("address", fallback_data["address"]),
        "city": get_val("city", fallback_data["city"]),
        "postal_code": get_val("postal_code", fallback_data["postal_code"]),
        "phone": get_val("phone", fallback_data["phone"]),
        "email": get_val("email", fallback_data["email"]),
        "logo": get_val("logo", fallback_data["logo"])
    }


# -----------------------------------------------------------
# Customer Translation Background Tasks (LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_customer_folder(company_id, local_id):
    base_dir = app.config["CUSTOMERS_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
    return folder


def translate_customer_in_background(customer_id, company_id, name, address, city, message):
    customer = Customer.query.filter_by(id=customer_id, company_id=company_id).first()
    if not customer:
        print(f"⚠ translate_customer_in_background: customer {customer_id} not found")
        return

    local_id = customer.local_id

    thread = threading.Thread(
        target=run_customer_translation,
        args=(company_id, local_id, name, address, city, message)
    )
    thread.daemon = True
    thread.start()


def run_customer_translation(company_id, local_id, name, address, city, message):
    try:
        name_trans    = generate_translations(name or "")
        address_trans = generate_translations(address or "")
        city_trans    = generate_translations(city or "")
        message_trans = generate_translations(message or "")

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
        message_clean = clean_translation_dict(message_trans, message or "")

        save_customer_file(company_id, local_id, name_clean, address_clean, city_clean, message_clean)

    except Exception as e:
        print(f"⚠ Translation failed for company {company_id}, local_id {local_id}: {e}")


def save_customer_file(company_id, local_id, name_trans, address_trans, city_trans, message_trans):
    folder = ensure_customer_folder(company_id, local_id)
    
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "customer.json")

    data = {
        "name": name_trans,
        "address": address_trans,
        "city": city_trans,
        "message": message_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✔ Airtight Customer JSON document created at: {file_path}")
    return file_path


def load_customer_file(company_id, local_id):
    if not company_id or not local_id:
        return None

    base_dir = app.config["CUSTOMERS_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
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
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

    if not company_id:
        company_id = getattr(customer, "company_id", None) or session.get("company_id")

    local_id = getattr(customer, "local_id", None)

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

    def get_val(field_key, default_val):
        field_data = data.get(field_key, {})
        if not isinstance(field_data, dict):
            return default_val or ""
        return field_data.get(lookup_lang) or field_data.get("he") or default_val or ""

    return {
        "name": get_val("name", fallback_data["name"]),
        "address": get_val("address", fallback_data["address"]),
        "city": get_val("city", fallback_data["city"]),
        "message": get_val("message", fallback_data["message"]),
        "postal_code": fallback_data["postal_code"]
    }


# -----------------------------------------------------------
# Employee Translation Background Tasks (LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_employee_folder(company_id, local_id):
    base_dir = app.config["EMPLOYEES_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
    return folder


def translate_employee_in_background(employee_id, company_id, name, address, city, message):
    employee = Employee.query.filter_by(id=employee_id, company_id=company_id).first()
    if not employee:
        print(f"⚠ translate_employee_in_background: employee {employee_id} not found")
        return

    local_id = employee.local_id

    thread = threading.Thread(
        target=run_employee_translation,
        args=(company_id, local_id, name, address, city, message)
    )
    thread.daemon = True
    thread.start()


def run_employee_translation(company_id, local_id, name, address, city, message):
    try:
        name_trans    = generate_translations(name or "")
        address_trans = generate_translations(address or "")
        city_trans    = generate_translations(city or "")
        message_trans = generate_translations(message or "")

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
        message_clean = clean_translation_dict(message_trans, message or "")

        save_employee_file(company_id, local_id, name_clean, address_clean, city_clean, message_clean)

    except Exception as e:
        print(f"⚠ Translation failed for company {company_id}, local_id {local_id}: {e}")


def save_employee_file(company_id, local_id, name_trans, address_trans, city_trans, message_trans):
    folder = ensure_employee_folder(company_id, local_id)
    
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "employee.json")

    data = {
        "name": name_trans,
        "address": address_trans,
        "city": city_trans,
        "message": message_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"✔ Airtight Employee JSON document created at: {file_path}")
    return file_path


def load_employee_file(company_id, local_id):
    if not company_id or not local_id:
        return None

    base_dir = app.config["EMPLOYEES_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
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
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

    if not company_id:
        company_id = getattr(employee, "company_id", None) or session.get("company_id")

    local_id = getattr(employee, "local_id", None)

    fallback_data = {
        "name": getattr(employee, 'employee_name', '') or "",
        "address": getattr(employee, 'address', '') or "",
        "city": getattr(employee, 'city', '') or "",
        "message": getattr(employee, 'message', '') or "",
        "postal_code": getattr(employee, 'postal_code', '') or ""
    }

    if not company_id or not local_id:
        return fallback_data

    data = load_employee_file(company_id, local_id)

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
        "message": get_val("message", fallback_data["message"]),
        "postal_code": fallback_data["postal_code"]
    }


# -----------------------------------------------------------
#  Supplier Translation Background Tasks (LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_supplier_folder(company_id, local_id):
    base_dir = app.config["SUPPLIERS_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
    return folder


def translate_supplier_in_background(supplier_id, company_id, name, address, city, postal_code, notes):
    supplier = Supplier.query.filter_by(id=supplier_id, company_id=company_id).first()
    if not supplier:
        print(f"⚠ translate_supplier_in_background: supplier {supplier_id} not found")
        return

    local_id = supplier.local_id

    thread = threading.Thread(
        target=run_supplier_translation,
        args=(company_id, local_id, name, address, city, postal_code, notes)
    )
    thread.daemon = True
    thread.start()


def run_supplier_translation(company_id, local_id, name, address, city, postal_code, notes):
    try:
        name_trans    = generate_translations(name or "")
        address_trans = generate_translations(address or "")
        city_trans    = generate_translations(city or "")
        notes_trans   = generate_translations(notes or "")

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
            company_id, 
            local_id, 
            name_clean, 
            address_clean, 
            city_clean, 
            postal_code or "", 
            notes_clean
        )

    except Exception as e:
        print(f"⚠ Translation failed for company {company_id}, local_id {local_id}: {e}")


def save_supplier_file(company_id, local_id, name_trans, address_trans, city_trans, postal_code, notes_trans):
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

    base_dir = app.config["SUPPLIERS_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
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
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

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

    data = load_supplier_file(company_id, local_id)

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
    base_dir = app.config["ITEMS_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
    os.makedirs(folder, exist_ok=True)
    return folder


def load_item_file(company_id_or_obj, local_id=None, company_id=None):
    final_company_id = None
    final_local_id = None

    if hasattr(company_id_or_obj, 'local_id') and hasattr(company_id_or_obj, 'company_id'):
        final_company_id = company_id_or_obj.company_id
        final_local_id = company_id_or_obj.local_id
    elif isinstance(company_id_or_obj, (int, str)) and local_id is not None:
        final_company_id = int(company_id_or_obj)
        final_local_id = int(local_id)
    elif isinstance(company_id_or_obj, (int, str)) and local_id is None and company_id is None:
        product_obj = Product.query.filter_by(id=int(company_id_or_obj)).first()
        if product_obj:
            final_company_id = product_obj.company_id
            final_local_id = product_obj.local_id

    if not final_company_id or not final_local_id:
        return None

    base_dir = app.config["ITEMS_DIR"]
    folder = os.path.join(base_dir, f"{final_company_id}_{final_local_id}")
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
    folder = ensure_product_folder(company_id, local_id)
    os.makedirs(folder, exist_ok=True)
    file_path = os.path.join(folder, "product.json")

    data = {
        "id": int(local_id),
        "sku": sku if sku else str(local_id),
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
    """מחשב את האינדקס הרץ הבא עבור קובץ האצווה הפנימי החדש בדיסק"""
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
    """סורק ומטעין בלייב את כל קבצי האצוות הפנימיים מהתיקייה בדיסק"""
    folder = ensure_product_folder(company_id, local_id)
    inv_folder = os.path.join(folder, "inventory_transactions")
    batches = []

    # תמיכה לאחור: אם קיים קובץ אצוות ישן מאוחד
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
    folder = ensure_product_folder(company_id, local_id)
    os.makedirs(folder, exist_ok=True)

    inv_folder = os.path.join(folder, "inventory_transactions")
    os.makedirs(inv_folder, exist_ok=True)

    next_idx = _get_next_inventory_index(inv_folder)
    file_path = os.path.join(inv_folder, f"inventory_transactions_{next_idx}.json")

    product_obj = Product.query.filter_by(local_id=int(local_id), company_id=company_id).first()
    raw_name = product_obj.name if product_obj else ""
    raw_desc = product_obj.description if product_obj else ""

    # הגנה קשיחה על פורמט התאריך ברישום הפנימי בדיסק (ISO אחיד)
    try:
        clean_date = datetime.strptime(received_date, '%d-%m-%Y').strftime('%Y-%m-%d')
    except:
        try:
            clean_date = datetime.strptime(received_date, '%d/%m/%Y').strftime('%Y-%m-%d')
        except:
            clean_date = received_date

    new_transaction = {
        "product_local_id": int(local_id),
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
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

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

    prod_data = load_item_file(company_id, local_id)
    
    if not prod_data:
        return fallback_data

    batches = _load_inventory_batches(company_id, local_id)
    total_stock_in = sum(int(b.get("stock_in", 0)) for b in batches) if batches else 0

    actual_out = db.session.query(func.sum(InvoiceItem.quantity)) \
        .join(Invoice).filter(
            InvoiceItem.product_id == str(local_id),
            InvoiceItem.income_category == 'product',
            Invoice.company_id == company_id,
            Invoice.status != "canceled"
        ).scalar() or 0

    # פונקציית חילוץ מחרוזת שטוחה ונקייה (String) חסינת קריסות
    def get_val(field_key, default_val):
        field_data = prod_data.get(field_key)
        if not isinstance(field_data, dict):
            # אם הנתון בקובץ נשמר בטעות כמחרוזת פשוטה, נחזיר אותה ישירות
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
    product = Product.query.filter_by(id=product_id, company_id=company_id).first()
    if not product:
        return

    local_id = product.local_id
    folder = ensure_product_folder(company_id, local_id)
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

    if not sku:
        sku = product.sku

    # חילוץ מזהה הספק הקיים מהמוצר במידה ויש
    current_supplier_id = getattr(product, 'supplier_id', None)

    thread = threading.Thread(
        target=run_product_translation,
        args=(
            company_id,
            local_id,
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
            sku
        )
    )
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
        with app.app_context():
            name_trans = generate_translations(name or "")
            desc_trans = generate_translations(description or "")
            
            supplier_trans = generate_translations(supplier_name or "מלאי פתיחה / כללי")

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

            batches = _load_inventory_batches(company_id, local_id)
            if not batches and stock_in > 0:
                batches = [{
                    "product_local_id": local_id,
                    "received_date": received_date or datetime.today().strftime('%Y-%m-%d'),
                    "stock_in": int(stock_in or 0),
                    "cost_price": float(cost_price or 0.0),
                    "supplier_id": supplier_id,
                    "supplier_name": supplier_name or 'מלאי פתיחה / כללי',
                    "name": name or "",
                    "description": description or ""
                }]

            save_item_file(
                company_id=company_id,
                local_id=local_id,
                name_trans=name_clean,
                desc_trans=desc_clean,
                price=float(price or 0.0),
                income_category=income_category or "service",
                cost_price=float(cost_price or 0.0),
                stock_out=int(stock_out or 0),
                sku=sku,
                batches=batches,
                supplier_name_trans=supplier_clean  # הזרקת הספק המטוהר
            )
            print(f"✔ Product and Supplier translation saved successfully for local_id {local_id}")
    except Exception as e:
        print(f"⚠ Product translation failed: {e}")


# -----------------------------------------------------------
# Transactions Helper - Save & Load JSON Translations (FULL COMPATIBLE LOCAL‑ID VERSION)
# -----------------------------------------------------------

def ensure_transaction_folder(company_id, local_id):
    base_dir = app.config["TRANSACTIONS_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
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
    transaction = Transaction.query.filter_by(id=transaction_id, company_id=company_id).first()
    if not transaction:
        print(f"⚠ translate_transaction_in_background: transaction {transaction_id} not found")
        return

    local_id = transaction.local_id

    thread = threading.Thread(
        target=run_transaction_translation,
        args=(
            company_id,
            local_id,
            description,
            amount,
            type_trans,
            category_id,
            currency_code,
            cost_price,
            income_category
        )
    )
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
        with app.app_context():
            desc_trans = generate_translations(description or "")

            save_transaction_file(
                company_id,
                local_id,
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
    folder = ensure_transaction_folder(company_id, local_id)
    file_path = os.path.join(folder, "transaction.json")

    try:
        final_id_value = int(local_id)
    except (ValueError, TypeError):
        final_id_value = str(local_id)

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
        transaction_obj = Transaction.query.filter_by(id=company_id_or_obj).first()
        if transaction_obj:
            final_company_id = transaction_obj.company_id
            final_local_id = transaction_obj.local_id

    if not final_company_id or not final_local_id:
        return None

    base_dir = app.config["TRANSACTIONS_DIR"]
    folder = os.path.join(base_dir, f"{final_company_id}_{final_local_id}")
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
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

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

    data = load_transaction_file(transaction)

    if not data:
        return fallback_data

    field_data = data.get("description", {})

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
    base_dir = app.config["CATEGORIES_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{local_id}")
    return folder


def translate_category_in_background(cat_id, company_id, raw_name_text):
    category = Category.query.filter_by(id=cat_id, company_id=company_id).first()
    if not category:
        print(f"⚠ translate_category_in_background: category {cat_id} not found")
        return

    local_id = category.local_id

    thread = threading.Thread(
        target=run_category_translation,
        args=(company_id, local_id, raw_name_text)
    )
    thread.daemon = True
    thread.start()


def run_category_translation(company_id, local_id, raw_name_text):
    try:
        with app.app_context():
            name_trans = generate_translations(raw_name_text or "")

            def clean_translation_dict(trans_dict, original_text):
                if not trans_dict:
                    return {}
                for lang, val in list(trans_dict.items()):
                    if val and ("Error 500" in val or "That’s an error" in val or "Please try again later" in val):
                        trans_dict[lang] = original_text
                return trans_dict

            name_clean = clean_translation_dict(name_trans, raw_name_text or "")

            save_category_file(company_id, local_id, name_clean)
            print(f"✔ Category translation saved for company {company_id}, local_id {local_id}")
    except Exception as e:
        print(f"⚠ Category translation failed for company {company_id}, local_id {local_id}: {e}")


def save_category_file(company_id, local_id, name_trans):
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
        cat_obj = Category.query.filter_by(id=company_id_or_obj).first()
        if cat_obj:
            final_company_id = cat_obj.company_id
            final_local_id = cat_obj.local_id

    if not final_company_id or not final_local_id:
        return None

    base_dir = app.config["CATEGORIES_DIR"]
    folder = os.path.join(base_dir, f"{final_company_id}_{final_local_id}")
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
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

    if not company_id:
        company_id = getattr(category, "company_id", None) or session.get("company_id")

    local_id = getattr(category, "local_id", None)

    fallback_data = {
        "name": getattr(category, "name", "") or ""
    }

    if not company_id or not local_id:
        return fallback_data

    data = load_category_file(int(company_id), int(local_id))

    if not data:
        return fallback_data

    field_data = data.get("name", {})
    if not isinstance(field_data, dict):
        return fallback_data

    return {
        "name": (
            field_data.get(lookup_lang)
            or field_data.get("he")
            or category.name
            or ""
        )
    }


# -----------------------------------------------------------
# Invoice Cancellation Translation Background Tasks (AIRTIGHT)
# -----------------------------------------------------------

def ensure_cancellation_folder(company_id, invoice_id):
    base_dir = app.config["CANCELLATIONS_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{invoice_id}")
    return folder


def translate_cancellation_in_background(invoice_id, company_id, text_to_translate):
    thread = threading.Thread(
        target=run_cancellation_translation,
        args=(company_id, invoice_id, text_to_translate)
    )
    thread.daemon = True
    thread.start()


def run_cancellation_translation(company_id, invoice_id, text_to_translate):
    try:
        # שימוש במנוע ה-generate_translations הרשמי שלך
        reason_trans = generate_translations(text_to_translate or "")

        def clean_translation_dict(trans_dict, original_text):
            if not trans_dict:
                return {}
            for lang, val in list(trans_dict.items()):
                if val and ("Error 500" in val or "That’s an error" in val or "Please try again later" in val):
                    trans_dict[lang] = original_text
            return trans_dict

        reason_clean = clean_translation_dict(reason_trans, text_to_translate or "")

        save_cancellation_file(company_id, invoice_id, reason_clean)

    except Exception as e:
        print(f"⚠ Cancellation Translation failed for company {company_id}, invoice {invoice_id}: {e}")


def save_cancellation_file(company_id, invoice_id, reason_trans):
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

    base_dir = app.config["CANCELLATIONS_DIR"]
    folder = os.path.join(base_dir, f"{company_id}_{invoice_id}")
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
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

    if not company_id:
        company_id = getattr(invoice, "company_id", None) or session.get("company_id")

    invoice_id = getattr(invoice, "id", None)

    fallback_data = {
        "cancellation_reason": getattr(invoice, 'cancellation_reason', '') or ""
    }

    if not company_id or not invoice_id:
        return fallback_data["cancellation_reason"]

    data = load_cancellation_file(company_id, invoice_id)

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
    base_dir = os.path.join(app.static_folder, "uploads", "transactions")
    
    company_dir = os.path.join(base_dir, f"company_{company_id}")
    os.makedirs(company_dir, exist_ok=True)

    unique_filename = f"{int(time.time())}_{secure_filename(filename)}"

    absolute_path = os.path.join(company_dir, unique_filename)
    
    relative_path = os.path.join("uploads", "transactions", f"company_{company_id}", unique_filename)

    return absolute_path, relative_path






# ----------------------
#  Home Login Page
# ----------------------

@app.route('/')
def home():
    return redirect(url_for('login'))


# -----------------------------------------------------------
#  Company Views & Management (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/company', methods=['GET', 'POST'])
@login_required
def company():
    try:
        language = get_lang()

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
            current_user.company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        company_obj = db.session.get(Company, active_company_id)

        if request.method == 'GET':
            company_i18n = load_company_translated(company_obj, language)
            return render_template(
                'company.html',
                company=company_i18n,
                company_db=company_obj
            )

        form_name = request.form.get('name', '').strip()
        if not form_name:
            flash("שם חברה הוא שדה חובה", "warning")
            return redirect(url_for('company'))

        company_data = {
            "name": form_name,
            "company_id_number": request.form.get('company_id_number', ''),
            "deduction_file": request.form.get('deduction_file', ''),
            "address": request.form.get('address', ''),
            "city": request.form.get('city', ''),
            "postal_code": request.form.get('postal_code', ''),
            "phone": request.form.get('phone', ''),
            "email": request.form.get('email', ''),
            "logo": request.form.get('logo', '')
        }

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            if not company_obj:
                company_obj = Company(id=OWNER_COMPANY_ID, **company_data)
                db.session.add(company_obj)
            else:
                for key, value in company_data.items():
                    setattr(company_obj, key, value)

            db.session.commit()
            flash("נתוני חברת הבעלים עודכנו בהצלחה!", "success")
        
        else:
            if company_obj:
                for key, value in company_data.items():
                    setattr(company_obj, key, value)
                db.session.commit()
                flash("נתוני החברה עודכנו בבסיס הנתונים!", "success")
            else:
                company_obj = Company(**company_data)
                db.session.add(company_obj)
                db.session.commit()

                current_user.company_id = company_obj.id
                session['company_id'] = company_obj.id
                db.session.commit()

                flash("חברה חדשה נוצרה בהצלחה!", "success")

            # בדיקה אם קיים לקוח מנהל תחת חברת הבעלים
            owner_customer_row = Customer.query.filter_by(
                id_number=str(current_user.id),          
                company_id=OWNER_COMPANY_ID
            ).first()

            if not owner_customer_row and current_user.email:
                owner_customer_row = Customer.query.filter_by(
                    email=current_user.email,
                    company_id=OWNER_COMPANY_ID
                ).first()

            if not owner_customer_row:
                last_cust = Customer.query.filter_by(company_id=OWNER_COMPANY_ID).order_by(Customer.local_id.desc()).first()
                next_local_id = 1 if not last_cust else (last_cust.local_id or 0) + 1

                owner_customer_row = Customer(
                    local_id=next_local_id,
                    company_id=OWNER_COMPANY_ID,
                    date=datetime.today().strftime('%d/%m/%Y'),
                    role='customer',
                    is_active=True
                )
                db.session.add(owner_customer_row)
                db.session.flush() # פוסטגרס מייצר כאן ID אוטומטי חוקי!

            owner_customer_row.customer_name = company_obj.name
            owner_customer_row.address       = company_obj.address
            owner_customer_row.city          = company_obj.city
            owner_customer_row.phone         = company_obj.phone
            owner_customer_row.email         = company_obj.email                
            owner_customer_row.id_number     = company_data.get("company_id_number", "")
            owner_customer_row.postal_code   = company_data.get("postal_code", "")
            
            db.session.commit()

            try:
                translate_customer_in_background(
                    customer_id=owner_customer_row.id, # קוד חוקי ואוטומטי!
                    company_id=OWNER_COMPANY_ID,
                    name=owner_customer_row.customer_name,
                    address=owner_customer_row.address,
                    city=owner_customer_row.city,
                    message=getattr(owner_customer_row, 'message', '')
                )
            except Exception as e:
                print(f"⚠️ Background translation skipped for company owner sync: {e}")

        translate_company_in_background(
            company_id=company_obj.id,
            name=company_obj.name,
            id_number=company_obj.company_id_number,
            deduction_file=company_obj.deduction_file,
            address=company_obj.address,
            city=company_obj.city,
            postal_code=company_obj.postal_code,
            phone=company_obj.phone,
            email=company_obj.email,
            logo=company_obj.logo
        )

        return redirect(url_for('company'))

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה חמורה בשמירת נתוני החברה", "danger")
        return redirect(url_for('company'))

# ----------------------
#  Clear Company Results Form 
# ----------------------

@app.route('/clear_company_results', methods=['POST'])
@login_required
def clear_company_results():
    try:
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            company_id = OWNER_COMPANY_ID
        else:
            company_id = current_user.company_id

        if not company_id:
            flash("No active company.", "warning")
            return redirect(url_for('company'))

        base_dir = ensure_company_folder(company_id)
        file_path = os.path.join(base_dir, f"{company_id}.json")

        file_deleted = False
        if os.path.exists(file_path):
            os.remove(file_path)
            file_deleted = True

        company_obj = db.session.get(Company, company_id)
        if company_obj:
            company_obj.company_id_number = ""
            company_obj.deduction_file = ""
            company_obj.address = ""
            company_obj.city = ""
            company_obj.postal_code = ""
            company_obj.phone = ""
            company_obj.email = ""
            company_obj.logo = ""
            db.session.commit()

        if file_deleted:
            flash("נתוני וקובץ התרגומים של החברה נמחקו בהצלחה.", "success")
        else:
            flash("נתוני החברה אופסו בהצלחה.", "success")

        return redirect(url_for('company'))

    except Exception as e:
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


# --------------------
# Send Permission Invoice Number Data
# ----------------------

@app.route("/send_invoice", methods=["POST"])
@login_required
def send_invoice():
    try:
        data = request.get_json()
        invoice_id = data.get("invoice_id")

        # 1. קביעת החברה הפעילה
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            company_id = OWNER_COMPANY_ID
        else:
            company_id = current_user.company_id

        if not company_id:
            return jsonify({"status": "error", "message": "No active company"}), 400

        # 2. שליפת החשבונית בצורה מאובטחת
        invoice_data = get_invoice_data(invoice_id, company_id=company_id)
        if not invoice_data:
            return jsonify({"status": "error", "message": "Invoice not found"}), 404

        # 3. שליחה לרשות המיסים לקבלת מספר הקצאה
        allocation_number = send_invoice_to_tax_authority(invoice_data)

        #   שמירה ונעילה של מספר ההקצאה בבסיס הנתונים של החברה!
        invoice_obj = db.session.get(Invoice, int(invoice_id))
        if invoice_obj and invoice_obj.company_id == company_id:
            invoice_obj.allocation_number = allocation_number
            db.session.commit()
        else:
            return jsonify({"status": "error", "message": "Database sync failed for allocation"}), 500

        return jsonify({
            "status": "success",
            "allocation_number": allocation_number
        })

    except Exception as e:
        db.session.rollback() 
        return jsonify({
            "status": "error",
            "message": str(e)
        })


# --------------------
# HELPER Invoice Data (Multi-Tenant Secure)
# ----------------------

def base_invoice_context(customer_id=None):
    language = get_lang()

    if customer_id is None and session.get('role') == 'customer':
        customer_id = session.get('customer_id')

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    company_obj = db.session.get(Company, company_id)
    company_translated = load_company_translated(company_obj, language) if company_obj else {}

    products_list_for_js = []
    for p in Product.query.filter_by(company_id=company_id).all():
        item_file = load_item_file(p.id, company_id=p.company_id) or {}
        
        # לשמור על מחרוזת שטוחה ונקייה שתואמת את דף הרווחים של המערכת שלך
        names_dict = item_file.get("name") or {}
        if isinstance(names_dict, dict):
            p_name = names_dict.get(language) or names_dict.get("he") or p.name
        else:
            p_name = p.name
            
        descs_dict = item_file.get("description") or {}
        if isinstance(descs_dict, dict):
            p_desc = descs_dict.get(language) or descs_dict.get("he") or p.description or ""
        else:
            p_desc = p.description or ""
        
        i_cat = item_file.get("income_category", getattr(p, 'income_category', 'service'))

        products_list_for_js.append({
            "id": p.id,
            "local_id": p.local_id if p.local_id is not None else p.id,
            "sku": p.sku if p.sku else (str(p.local_id) if p.local_id is not None else str(p.id)),
            "name": p_name,          
            "description": p_desc,   
            "price": float(p.price or 0),
            "cost_price": float(p.cost_price or 0),
            "quantity": int(getattr(p, 'quantity', 0) or 0),
            "income_category": i_cat
        })

    #  SELF-INVOICE VIRTUAL INJECTION: Safely bundle the banking category dictionary data if customer_id is "0"
    if customer_id and str(customer_id).strip() == "0":
        BANK_SKU_TRANSLATIONS = {
            "he": {"rent": "שכירות", "stocks": "מניות", "dividend": "דיבידנד", "unspecified": "כללי"},
            "en": {"rent": "Rental", "stocks": "Stocks", "dividend": "Dividend", "unspecified": "General"},
            "bg": {"rent": "Наем", "stocks": "Акции", "dividend": "Дивидент", "unspecified": "Общо"},
            "fr": {"rent": "Location", "stocks": "Actions", "dividend": "Dividende", "unspecified": "Général"},
            "es": {"rent": "Alquiler", "stocks": "Acciones", "dividend": "Dividendo", "unspecified": "General"},
            "de": {"rent": "Miete", "stocks": "Aktien", "dividend": "Dividende", "unspecified": "Allgemein"},
            "it": {"rent": "Affitto", "stocks": "Azioni", "dividend": "Dividendo", "unspecified": "Generale"},
            "pt": {"rent": "Aluguel", "stocks": "Ações", "dividend": "Dividendo", "unspecified": "Geral"},
            "ru": {"rent": "Аренда", "stocks": "Акции", "dividend": "Дивиденды", "unspecified": "Общий"},
            "ar": {"rent": "إيجار", "stocks": "أسهم", "dividend": "أرباح", "unspecified": "عام"},
            "zh": {"rent": "租赁", "stocks": "股票", "dividend": "股息", "unspecified": "一般"},
            "ja": {"rent": "賃貸", "stocks": "株式", "dividend": "配当", "unspecified": "一般"},
            "ko": {"rent": "임대", "stocks": "주식", "dividend": "배당", "unspecified": "일반"},
            "nl": {"rent": "Huur", "stocks": "Aandelen", "dividend": "Dividend", "unspecified": "Algemeen"},
            "pl": {"rent": "Najem", "stocks": "Akcje", "dividend": "Dywidenda", "unspecified": "Ogólne"},
            "ro": {"rent": "Chirie", "stocks": "Acțiuni", "dividend": "Dividend", "unspecified": "General"},
            "uk": {"rent": "Оренда", "stocks": "Акції", "dividend": "Дивіденди", "unspecified": "Загальний"},
            "el": {"rent": "Ενοίκιο", "stocks": "Μετοχές", "dividend": "Μέρισμα", "unspecified": "Γενικά"},
            "tr": {"rent": "Kira", "stocks": "Hisseler", "dividend": "Temettü", "unspecified": "Genel"},
            "hu": {"rent": "Bérlés", "stocks": "Részvények", "dividend": "Osztalék", "unspecified": "Általános"},
            "cs": {"rent": "Pronájem", "stocks": "Akcie", "dividend": "Dividenda", "unspecified": "Obecné"},
            "sv": {"rent": "Hyra", "stocks": "Aktier", "dividend": "Utdelning", "unspecified": "Allmänt"},
            "fi": {"rent": "Vuokra", "stocks": "Osakkeet", "dividend": "Osinko", "unspecified": "Yleinen"},
            "da": {"rent": "Leje", "stocks": "Aktier", "dividend": "Udbytte", "unspecified": "Generelt"},
            "no": {"rent": "Leie", "stocks": "Aksjer", "dividend": "Utbytte", "unspecified": "Generelt"},
            "sk": {"rent": "Prenájom", "stocks": "Akcie", "dividend": "Dividenda", "unspecified": "Všeobecné"},
            "hr": {"rent": "Najam", "stocks": "Dionice", "dividend": "Dividenda", "unspecified": "Općenito"},
            "sr": {"rent": "Закуп", "stocks": "Акције", "dividend": "Дивиденда", "unspecified": "Опште"},
            "id": {"rent": "Sewa", "stocks": "Saham", "dividend": "Dividen", "unspecified": "Umum"},
            "th": {"rent": "เช่า", "stocks": "หุ้น", "dividend": "ปันผล", "unspecified": "ทั่วไป"},
            "vi": {"rent": "Thuê", "stocks": "Cổ phiếu", "dividend": "Cổ tức", "unspecified": "Chung"}
        }
        lang_sku = BANK_SKU_TRANSLATIONS.get(language, BANK_SKU_TRANSLATIONS["he"])
        
        for cat_key, cat_sku in lang_sku.items():
            products_list_for_js.append({
                "id": cat_key,
                "local_id": cat_key,
                "sku": cat_sku,
                "name": cat_sku,
                "description": cat_sku,
                "price": 0.0,
                "cost_price": 0.0,
                "quantity": 0,  
                "income_category": cat_key
            })

    all_customers_json = []
    for c in Customer.query.filter_by(company_id=company_id).all():
        trans = load_customer_translated(c, language) or {"name": c.customer_name}
        all_customers_json.append({
            "id": c.id,
            "customer_name": trans.get("name", c.customer_name),
            "id_number": c.id_number or ""
        })

    customer_data = None
    if customer_id:
        if str(customer_id) == "0":
            if company_obj:
                customer_data = {
                    "id": "0",  
                    "customer_name": f"★ {company_translated.get('name', company_obj.name)} ",
                    "id_number": company_obj.company_id_number or "",
                    "address": company_obj.address or "",
                    "city": company_obj.city or "",
                    "postal_code": company_obj.postal_code or "",
                    "phone": company_obj.phone or "",
                    "email": company_obj.email or ""
                }
        else:
            c_obj = Customer.query.filter_by(id=customer_id, company_id=company_id).first()
            if c_obj:
                trans_dict = load_customer_translated(c_obj, language) or {}
                customer_data = {
                    "id": c_obj.id,
                    "customer_name": trans_dict.get("name") or c_obj.customer_name,
                    "id_number": getattr(c_obj, 'id_number', '') or "",
                    "address": getattr(c_obj, 'address', '') or "",
                    "city": getattr(c_obj, 'city', '') or "",
                    "postal_code": getattr(c_obj, 'postal_code', '') or "",
                    "phone": getattr(c_obj, 'phone', '') or "",
                    "email": getattr(c_obj, 'email', '') or ""
                }

    return {
        "products": products_list_for_js,
        "itemsList": products_list_for_js,
        "customer": customer_data,
        "customer_json": customer_data, 
        "all_customers_json": all_customers_json,
        "company": company_translated,
        "company_db": company_obj,
        "vat_options": list(range(0, 21)),        
        "invoice_number": get_next_invoice_number(company_id=company_id),
        "invoice": None,
        "items": [],
        "loadedPayments": [],
        "sub_total": 0.0,
        "vat_rate": 0.0,
        "vat_amount": 0.0,
        "grand_total": 0.0,
        "discount_total": 0.0,
        "invoice_status": "active",
        "translated_reason": ""
    }


# --------------------
# Get Invoice Number Form Data (Multi-Tenant Secure)
# ----------------------

def get_next_invoice_number(company_id=None):
    if company_id is None:
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            company_id = OWNER_COMPANY_ID
        else:
            company_id = current_user.company_id

    try:
        result = (
            db.session.query(
                db.func.max(db.cast(Invoice.invoice_number, db.Integer))
            )
            .filter(Invoice.company_id == company_id)
            .scalar()
        )

        if result is not None:
            return int(result) + 1
        return 1

    except Exception as e:
        print(f"⚠️ Warning: Could not calculate next invoice number automatically: {e}")

        result_raw = (
            db.session.query(db.func.max(Invoice.invoice_number))
            .filter(Invoice.company_id == company_id)
            .scalar()
        )

        try:
            return int(result_raw) + 1 if result_raw else 1
        except:
            return 1


# --------------------
# Get Invoice Context Form Data (Multi-Tenant Secure)
# ----------------------

def invoice_context(invoice_id=None, customer_id=None):
    invoice = None
    try:
        language = get_lang()

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            company_id = OWNER_COMPANY_ID
        else:
            company_id = current_user.company_id

        if invoice_id:
            invoice = db.session.get(Invoice, invoice_id)
            if invoice and invoice.company_id != company_id:
                invoice = None

        # חשבונית עצמית: אם אין חשבונית בדאטהבייס אבל יש מזהה לקוח מהטופס
        active_customer_id = customer_id
        if invoice and invoice.customer_id is not None:
            active_customer_id = invoice.customer_id

        company_obj = db.session.get(Company, company_id)
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        customer_json = {}
        if active_customer_id is not None:
            if str(active_customer_id) == "0":
                customer_json = {
                    "id": "0",  
                    "customer_name": f"★ {company_translated.get('name', company_obj.name if company_obj else '')} ",
                    "address": company_translated.get('address', company_obj.address if company_obj else ""),
                    "city": company_translated.get('city', company_obj.city if company_obj else ""),
                    "postal_code": company_obj.postal_code if company_obj else "",
                    "id_number": company_obj.company_id_number if company_obj else "",
                    "phone": company_obj.phone if company_obj else "",
                    "email": company_obj.email if company_obj else ""
                }
            else:
                c_obj = Customer.query.filter_by(id=active_customer_id, company_id=company_id).first()
                if c_obj:
                    trans = load_customer_translated(c_obj, language) or {}
                    customer_json = {
                        "id": c_obj.id,
                        "customer_name": trans.get("name") or c_obj.customer_name,
                        "address": trans.get("address") or c_obj.address,
                        "city": trans.get("city") or c_obj.city,
                        "postal_code": c_obj.postal_code or "",
                        "id_number": c_obj.id_number or "",
                        "phone": c_obj.phone or "",
                        "email": c_obj.email or ""
                    }

        #  מילון המק"טים הרשמי המתורגם לכל 30 השפות של המערכת שלך בשרת
        SKU_LOCALIZED_SERVER = {
            "he": {"rent": "שכירות", "stocks": "מניות", "dividend": "디בידנד", "unspecified": "כללי"},
            "en": {"rent": "Rental", "stocks": "Stocks", "dividend": "Dividend", "unspecified": "General"},
            "bg": {"rent": "Наем", "stocks": "Акции", "dividend": "Дивидент", "unspecified": "Общо"},
            "fr": {"rent": "Location", "stocks": "Actions", "dividend": "Dividende", "unspecified": "Général"},
            "es": {"rent": "Alquiler", "stocks": "Acciones", "dividend": "Dividendo", "unspecified": "General"},
            "de": {"rent": "Miete", "stocks": "Aktien", "dividend": "Dividende", "unspecified": "Allgemein"},
            "it": {"rent": "Affitto", "stocks": "Azioni", "dividend": "Dividendo", "unspecified": "Generale"},
            "pt": {"rent": "Aluguel", "stocks": "Ações", "dividend": "Dividendo", "unspecified": "Geral"},
            "ru": {"rent": "Аренда", "stocks": "Акции", "dividend": "Дивиденды", "unspecified": "Общий"},
            "ar": {"rent": "إيجار", "stocks": "أسهم", "dividend": "أرباح", "unspecified": "عام"},
            "zh": {"rent": "租赁", "stocks": "股票", "dividend": "股息", "unspecified": "一般"},
            "ja": {"rent": "賃貸", "stocks": "株式", "dividend": "配当", "unspecified": "一般"},
            "ko": {"rent": "임대", "stocks": "주식", "dividend": "배당", "unspecified": "일반"},
            "nl": {"rent": "Huur", "stocks": "Aandelen", "dividend": "Dividend", "unspecified": "Algemeen"},
            "pl": {"rent": "Najem", "stocks": "Akcje", "dividend": "Dywidenda", "unspecified": "Ogólne"},
            "ro": {"rent": "Chirie", "stocks": "Acțiuni", "dividend": "Dividend", "unspecified": "General"},
            "uk": {"rent": "Оренда", "stocks": "Акції", "dividend": "Дивіденди", "unspecified": "Загальний"},
            "el": {"rent": "Ενοίκιο", "stocks": "Μετοχές", "dividend": "Μέρισμα", "unspecified": "Γενικά"},
            "tr": {"rent": "Kira", "stocks": "Hisseler", "dividend": "Temettü", "unspecified": "Genel"},
            "hu": {"rent": "Bérlés", "stocks": "Részvények", "dividend": "Osztalék", "unspecified": "Általános"},
            "cs": {"rent": "Pronájem", "stocks": "Akcie", "dividend": "Dividenda", "unspecified": "Obecné"},
            "sv": {"rent": "Hyra", "stocks": "Aktier", "dividend": "Utdelning", "unspecified": "Allmänt"},
            "fi": {"rent": "Vuokra", "stocks": "Osakkeet", "dividend": "Osinko", "unspecified": "Yleinen"},
            "da": {"rent": "Leje", "stocks": "Aktier", "dividend": "Udbytte", "unspecified": "Generelt"},
            "no": {"rent": "Leie", "stocks": "Aksjer", "dividend": "Utbytte", "unspecified": "Generelt"},
            "sk": {"rent": "Prenájom", "stocks": "Akcie", "dividend": "Dividenda", "unspecified": "Všeobecné"},
            "hr": {"rent": "Najam", "stocks": "Dionice", "dividend": "Dividenda", "unspecified": "Općenito"},
            "sr": {"rent": "Закуп", "stocks": "Акције", "dividend": "Дивиденда", "unspecified": "Опште"},
            "id": {"rent": "Sewa", "stocks": "Saham", "dividend": "Dividen", "unspecified": "Umum"},
            "th": {"rent": "เช่า", "stocks": "หุ้น", "dividend": "ปันผล", "unspecified": "ทั่วไป"},
            "vi": {"rent": "Thuê", "stocks": "Cổ phiếu", "dividend": "Cổ tức", "unspecified": "Chung"}
        }

        #  מילון התיאורים הרשמי המתורגם לכל 30 השפות של המערכת שלך בשרת
        DESC_LOCALIZED_SERVER = {
            "he": {"rent": "הכנסה פסיבית מדמי שכירות נכס", "stocks": "הכנסה ממימוש / מכירת מניות בנקאיות", "dividend": "הכנסה מחלוקת דיבידנד פנימי", "unspecified": "הכנסה פנימית לא מפורטת (אישור בנקאי)"},
            "en": {"rent": "Passive income from property rental", "stocks": "Income from liquidation / sale of bank stocks", "dividend": "Income from internal dividend distribution", "unspecified": "Unspecified internal income (Bank approval)"},
            "bg": {"rent": "Пасивен доход от наем на имущество", "stocks": "Доход от ликвидация / продажба на банкови акции", "dividend": "Доход от разпределение на вътрешен дивидент", "unspecified": "Неуточнен вътрешен доход (Банково одобрение)"},
            "fr": {"rent": "Revenu passif de la location immobilière", "stocks": "Revenu de la liquidation / vente d'actions bancaires", "dividend": "Revenu de la distribution de dividendes internes", "unspecified": "Revenu interne non spécifié (Approbation bancaire)"},
            "es": {"rent": "Ingresos pasivos por alquiler de propiedades", "stocks": "Ingresos por liquidación / venta de acciones bancarias", "dividend": "Ingresos por distribución de dividendos internos", "unspecified": "Ingresos internos no específicos (Aprobación bancaria)"},
            "de": {"rent": "Passive Einkünfte aus Immobilienvermietung", "stocks": "Einnahmen aus der Liquidation / dem Verkauf von Bankaktien", "dividend": "Einnahmen aus interner Dividendenausschüttung", "unspecified": "Unspezifisches internes Einkommen (Bankgenehmigung)"},
            "it": {"rent": "Reddito passivo da locazione immobiliare", "stocks": "Reddito da liquidazione / vendita di azioni bancarie", "dividend": "Reddito da distribuzione di dividendi interni", "unspecified": "Reddito interno non specificato (Approvazione bancaria)"},
            "pt": {"rent": "Rendimento passivo de aluguel de imóveis", "stocks": "Rendimento de liquidação / venda de ações bancárias", "dividend": "Rendimento de distribuição de dividendos internos", "unspecified": "Rendimento interno não especificado (Aprovação bancária)"},
            "ru": {"rent": "Пассивный доход от аренды недвижимости", "stocks": "Доход от ликвидации / продажи банковских акций", "dividend": "Доход от распределения внутренних дивидендов", "unspecified": "Неуказанный внутренний доход (Одобрение банка)"},
            "ar": {"rent": "دخل سلبي من إيجار العقارات", "stocks": "دخل من تصفية / بيع أسهم مصرفية", "dividend": "دخل من توزيع الأرباح الداخلية", "unspecified": "دخل داخلي غير محدد (موافقة البنك)"},
            "zh": {"rent": "财产租赁的被动收入", "stocks": "清算/销售银行股票的收入", "dividend": "内部股息分配收入", "unspecified": "未指明的内部收入（银行批准）"},
            "ja": {"rent": "不動産賃貸による不労所得", "stocks": "銀行株式の清算・売却による収入", "dividend": "内部配当金の分配による収入", "unspecified": "未特定の内部収入（銀行承認）"},
            "ko": {"rent": "부동산 임대로 인한 수동적 소득", "stocks": "은행 주식 청산 및 매각으로 인한 소득", "dividend": "내부 배당금 분배로 인한 소득", "unspecified": "지정되지 않은 내부 소득 (은행 승인)"},
            "nl": {"rent": "Passief inkomen uit verhuur van onroerend goed", "stocks": "Inkomsten uit verkoop van bankaandelen", "dividend": "Inkomsten uit interne dividenduitkering", "unspecified": "Ongespecificeerd intern inkomen (Bankgoedkeuring)"},
            "pl": {"rent": "Przychód pasywny z wynajmu nieruchomości", "stocks": "Przychód ze sprzedaży akcji bankowych", "dividend": "Przychód z podziału dywidendy wewnętrznej", "unspecified": "Nieokreślony przychód wewnętrzny (Zgoda banku)"},
            "ro": {"rent": "Venit pasiv din închirierea proprietății", "stocks": "Venituri din vânzarea acțiunilor bancare", "dividend": "Venituri din distribuirea dividendelor interne", "unspecified": "Venit intern nespecificat (Aprobare bancară)"},
            "uk": {"rent": "Пасивний дохід від оренди майна", "stocks": "Доход від продажу банківських акцій", "dividend": "Доход від розподілу внутрішніх дивідендів", "unspecified": "Невизначений внутрішній дохід (Схвалення банку)"},
            "el": {"rent": "Παθητικό εισόδημα από ενοικίαση ακινήτων", "stocks": "Εισόδημα από πώληση τραπεζικών μετοχών", "dividend": "Εισόδημα από εσωτερική διανομή μερισμάτων", "unspecified": "Μη καθορισμένο εσωτερικό εισόδημα (Έγκριση τράπεζας)"},
            "tr": {"rent": "Gayrimenkul kiralamasından elde edilen pasif gelir", "stocks": "Banka hisselerinin satışından elde edilen gelir", "dividend": "İç temettü dağıtımından elde edilen gelir", "unspecified": "Belirtilmemiş iç gelir (Banka onayı)"},
            "hu": {"rent": "Ingatlan bérbeadásából származó passzív jövedelem", "stocks": "Banki részvények értékesítéséből származó jövedelem", "dividend": "Belső osztalékfizetésből származó jövedelem", "unspecified": "Nem meghatározott belső jövedelem (Banki jóváhagyás)"},
            "cs": {"rent": "Pasivní příjem z pronájmu nemovitosti", "stocks": "Příjem z prodeje bankovních akcií", "dividend": "Příjem z distribuce interních dividend", "unspecified": "Nespecifikovaný interní příjem (Schválení banky)"},
            "sv": {"rent": "Passiv inkomst från fastighetsuthyrning", "stocks": "Inkomst från försäljning av bankaktier", "dividend": "Inkomst från intern vinstutdelning", "unspecified": "Ospecificerad intern inkomst (Bankgodkännande)"},
            "fi": {"rent": "Passiivinen tulo kiinteistön vuokrauksesta", "stocks": "Tulo pankkiosakkeiden myynnistä", "dividend": "Tulo sisäisestä osingonjaosta", "unspecified": "Määrittelemätön sisäinen tulo (Pankin hyväksyntä)"},
            "da": {"rent": "Passiv indkomst fra ejendomsudlejning", "stocks": "Indkomst fra salg af bankaktier", "dividend": "Indkomst fra intern udbyttefordeling", "unspecified": "Uspecificeret intern indkomst (Bankgodkendelse)"},
            "no": {"rent": "Passiv inntekt fra eiendomsutleie", "stocks": "Inntekt fra salg av bankaksjer", "dividend": "Inntekt fra intern utbyttedistribusjon", "unspecified": "Uspesifisert intern inntekt (Bankgodkjenning)"},
            "sk": {"rent": "Pasívny príjem z prenájmu nehnuteľnosti", "stocks": "Príjem z predaju bankových akcií", "dividend": "Príjem z distribúcie interných dividend", "unspecified": "Nešpecifikovaný interný príjem (Schválenie banky)"},
            "hr": {"rent": "Pasivni prihod od iznajmljivanja nekretnina", "stocks": "Prihod od prodaje bankovnih dionica", "dividend": "Prihod od raspodjele unutarnje dividende", "unspecified": "Neodređeni unutarnji prihod (Odobrenje banke)"},
            "sr": {"rent": "Пасивни приход од издавања некретнина", "stocks": "Приход од продаје банкарских акција", "dividend": "Приход од расподеле унутрашње дивиденде", "unspecified": "Непрецизирани унутрашњи приход (Одобрење банке)"},
            "id": {"rent": "Pendapatan pasif dari sewa properti", "stocks": "Pendapatan dari penjualan saham bank", "dividend": "Pendapatan dari distribusi dividen internal", "unspecified": "Pendapatan internal yang tidak ditentukan (Persetujuan bank)"},
            "th": {"rent": "รายได้พาสซีฟจากการเช่าอสังหาริมทรัพย์", "stocks": "รายได้จากการขายหุ้นธนาคาร", "dividend": "รายได้จากการจัดสรรเงินปันผลภายใน", "unspecified": "รายได้ภายในที่ไม่ระบุรายละเอียด (การอนุมัติจากธนาคาร)"},
            "vi": {"rent": "Thuêm thu nhập thụ động từ cho thuê tài sản", "stocks": "Thu nhập từ bán cổ phiếu ngân hàng", "dividend": "Thu nhập từ phân phối cổ tức nội bộ", "unspecified": "Thu nhập nội bộ không xác định (Ngân hàng phê duyệt)"}
        }

        items_json = []
        if invoice:
            items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
            for item in items:
                target_p = None
                if str(item.product_id).isdigit():
                    target_p = Product.query.filter_by(local_id=int(item.product_id), company_id=company_id).first()
                
                row_sku = str(item.product_id)
                row_name_trans = item.description or ""
                
                if target_p:
                    translated_p_data = load_item_translated(target_p, language, company_id) or {}
                    item_file_data = load_item_file(target_p.id, company_id=company_id) or {}
                    
                    row_sku = target_p.sku if target_p.sku else item_file_data.get("sku", str(target_p.local_id))
                    row_name_trans = translated_p_data.get("name") or target_p.name or ""
                else:
                    if str(active_customer_id) == "0":
                        prod_key = str(item.product_id).strip().lower()
                        
                        lang_sku = SKU_LOCALIZED_SERVER.get(language, SKU_LOCALIZED_SERVER["he"])
                        row_sku = lang_sku.get(prod_key, str(item.product_id))
                        
                        lang_desc = DESC_LOCALIZED_SERVER.get(language, DESC_LOCALIZED_SERVER["he"])
                        row_name_trans = lang_desc.get(prod_key, item.description or "")

                items_json.append({
                    "product_id": item.product_id,        
                    "sku": str(row_sku).strip(),          
                    "name": row_name_trans,               
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "discount": float(item.discount or 0),
                    "total_price": float(item.total_price or 0),
                    "cost_price": float(getattr(item, 'cost_price_at_time', 0.0) or 0.0)
                })

        all_customers_json = []
        my_customers = (
            Customer.query.filter_by(company_id=company_id)
            .order_by(Customer.customer_name)
            .all()
        )
        for c in my_customers:
            trans = load_customer_translated(c, language) or {}
            all_customers_json.append({
                "id": c.id,
                "customer_name": trans.get("name") or c.customer_name,
                "id_number": c.id_number or ""
            })

        products_json = []
        my_products = Product.query.filter_by(company_id=company_id).all()
        for p in my_products:
            item_file = load_item_file(p.id, company_id=company_id) or {}
            translated_core = load_item_translated(p, language, company_id) or {}

            p_name = translated_core.get("name") or p.name or ""
            i_cat = translated_core.get("income_category") or p.income_category or "product"

            total_sold = db.session.query(func.sum(InvoiceItem.quantity)) \
                                               .join(Invoice) \
                                               .filter(
                                                   InvoiceItem.product_id == str(p.local_id if p.local_id is not None else p.id),
                                                   InvoiceItem.income_category == 'product',
                                                   Invoice.company_id == company_id,
                                                   Invoice.status != "canceled"
                                               ).scalar() or 0
            
            stock_in_json = item_file.get("stock_in")
            stock_in = int(stock_in_json) if stock_in_json is not None else (int(getattr(p, 'quantity', 0) or 0) + int(total_sold))
            actual_quantity = stock_in - int(total_sold)

            products_json.append({
                "id": p.id,                        
                "local_id": p.local_id,            
                "sku": p.sku if p.sku else item_file.get("sku", str(p.local_id if p.local_id is not None else p.id)),
                "name": p_name,                    
                "price": float(p.price or 0),
                "cost_price": float(p.cost_price or 0),
                "quantity": actual_quantity, 
                "income_category": i_cat
            })

        payments_json = []
        if invoice:
            payments = Payment.query.filter_by(invoice_id=invoice.id).all()
            for p in payments:
                payments_json.append({
                    "payment_date": p.payment_date.strftime('%Y-%m-%d') if p.payment_date else "",
                    "payment_method": p.payment_method,
                    "payment_amount": float(p.payment_amount or 0),
                    "bank": p.bank or "",
                    "branch": p.branch or "",
                    "account_number": p.account_number or ""
                })

        sub_total = float(invoice.sub_total or 0) if invoice else 0.0
        vat_amount = float(invoice.vat_amount or 0) if invoice else 0.0
        grand_total = float(invoice.grand_total or 0) if invoice else 0.0
        discount_total = float(getattr(invoice, 'discount_total', 0) or 0) if invoice else 0.0
        vat_rate = float(invoice.vat_rate) if invoice and invoice.vat_rate is not None else 0.0

        cancellation_reason_trans = ""
        if invoice and getattr(invoice, 'status', '') == 'canceled':
            cancel_file_data = load_cancellation_file(company_id, invoice.id) or {}
            reason_dict = cancel_file_data.get("cancellation_reason", {})
            
            cancellation_reason_trans = (
                reason_dict.get(language)
                or reason_dict.get("he")
                or getattr(invoice, 'cancellation_reason', '')
                or "ביטול כללי"
            )

        # העברת ה-ID האקטיבי כדי ש-base_invoice_context יטען את הלקוח העצמי ללא שגיאות
        base_ctx = base_invoice_context(customer_id=active_customer_id)

        base_ctx.update({
            "invoice": invoice,
            "invoice_id": invoice.id if invoice else None,
            "allocation_number": invoice.allocation_number if invoice else None,
            "invoice_number": invoice.invoice_number if invoice else get_next_invoice_number(company_id=company_id),
            "invoice_date": (
                invoice.invoice_date.strftime('%d-%m-%Y')
                if invoice else datetime.today().strftime('%d-%m-%Y')
            ),
            "customer_json": customer_json,
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
            "transaction": Transaction.query.filter_by(
                invoice_id=invoice.invoice_number, 
                company_id=company_id
            ).first() if invoice else None
        })

        return base_ctx

    except Exception as e:
        import traceback
        print(f"❌ CRITICAL ERROR in invoice_context: {e}")
        traceback.print_exc()

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            company_id = OWNER_COMPANY_ID
        else:
            company_id = current_user.company_id

        company_obj = db.session.get(Company, company_id) if company_id else None
        company_translated = load_company_translated(company_obj, get_lang()) if company_obj else {}

        products_backup = []
        try:
            if company_id:
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
        except:
            pass

        backup_reason = ""
        if invoice:
            try:
                backup_reason = load_cancellation_translated(invoice, get_lang(), company_id)
            except:
                backup_reason = getattr(invoice, 'cancellation_reason', '') or ""

        fallback_customer = {}
        if str(active_customer_id) == "0":
            fallback_customer = {
                "id": "0",
                "customer_name": f"★ {company_translated.get('name', company_obj.name if company_obj else '')} "
            }

        return {
            "error": str(e),
            "invoice": invoice,
            "invoice_id": getattr(invoice, 'id', None),
            "company": company_translated,
            "company_db": company_obj,
            "customer_json": fallback_customer,
            "all_customers_json": [],
            "products": products_backup,
            "items": [],
            "loadedPayments": [],
            "sub_total": float(getattr(invoice, 'sub_total', 0) or 0),
            "vat_amount": float(getattr(invoice, 'vat_amount', 0) or 0),
            "grand_total": float(getattr(invoice, 'grand_total', 0) or 0),
            "discount_total": float(getattr(invoice, 'discount_total', 0) or 0),
            "vat_rate": float(getattr(invoice, 'vat_rate', 0) or 0),            
            "invoice_number": getattr(invoice, 'invoice_number', get_next_invoice_number(company_id=company_id)),            
            "invoice_date": datetime.today().strftime('%d-%m-%Y'),
            "invoice_status": getattr(invoice, 'status', 'active'),
            "translated_reason": backup_reason
        }

        
# --------------------
#  Invoice View Empty Form Save Data
# ----------------------

    # ------ Format Helper--------

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


def generate_allocation_number():
    timestamp = int(time.time())  
    rand = random.randint(1000, 9999)
    return f"{timestamp}{rand}"


# -----------------------------------------------------------
# Route: Save or Update Invoice + Auto-Generate Transaction
# -----------------------------------------------------------

@app.route('/invoice/save', methods=['POST'])
@login_required
def save_invoice():
    invoice_id = request.form.get("invoice_id")
    customer_id = request.form.get("customer_id")

    # קביעת מזהה החברה האקטיבית (Multi-Company Protection)
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    if not company_id:
        flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
        return redirect(url_for('invoice'))

    if not customer_id:
        flash("שגיאה: יש לבחור לקוח חוקי על מנת לשמור את המסמך", "error")
        return redirect(url_for('invoice'))

        # פוסטגרס (בדיקה וסנכרון ה-ID החוקי לחשבונית עצמית - פתרון קבוע ומיושר)
        if str(customer_id).strip() == "0" or not customer_id:
            company_obj = db.session.get(Company, company_id)
            owner_customer = None

            # 1. שליפת הלקוח הפנימי של החברה לפי ה-email של החברה הנוכחית
            if company_obj and company_obj.email:
                owner_customer = Customer.query.filter_by(
                    email=company_obj.email,
                    company_id=company_id
                ).first()

            # 2. הגנת חירום א': חיפוש לפי ה-id_number ששווה לח.פ (company_id_number) של החברה
            if not owner_customer and company_obj and company_obj.company_id_number:
                owner_customer = Customer.query.filter_by(
                    id_number=str(company_obj.company_id_number),
                    company_id=company_id
                ).first()

            # 3. הגנת חירום ב': אם עמוד החברה מעולם לא נשמר, לוקחים את הלקוח הראשון של חברה זו
            if not owner_customer:
                owner_customer = Customer.query.filter_by(company_id=company_id).first()

            if owner_customer:
                db_customer_id = owner_customer.id  # ה-ID האוטומטי החוקי שפוסטגרס מכיר!
                checked_customer = owner_customer    # מאשרים את הלקוח למערכת האבטחה
            else:
                db_customer_id = None
                checked_customer = None
        else:
            checked_customer = Customer.query.filter_by(id=customer_id, company_id=company_id).first()
            db_customer_id = int(customer_id) if checked_customer else None

        if not checked_customer:
            flash("שגיאה: אין לך הרשאה לגשת לנתוני לקוח זה", "error")
            return redirect(url_for('invoice'))

        sub_total = clean_float(request.form.get('sub_total'))
        vat_amount = clean_float(request.form.get('vat_amount'))
        grand_total = clean_float(request.form.get('grand_total'))

        vat_rate_raw = request.form.get('vat_rate_select')
        vat_rate = clean_float(vat_rate_raw) if vat_rate_raw not in [None, "", "null"] else 0.0

        total_invoice_cost = 0.0

        if not invoice_id:
            invoice_number_check = get_next_invoice_number(company_id=company_id)
            existing_invoice = Invoice.query.filter_by(
                company_id=company_id, 
                invoice_number=invoice_number_check
            ).first()
            if existing_invoice:
                flash(f"שגיאה: חשבונית מספר {invoice_number_check} כבר שמורה ומאובטחת במערכת.", "error")
                return redirect(url_for('invoice'))

        # ====== 1. עדכון חשבונית קיימת (מצב עריכה) ======
        if invoice_id:
            invoice = db.session.get(Invoice, int(invoice_id))
            if not invoice or invoice.company_id != company_id:
                flash("החשבונית המבוקשת לעריכה אינה קיימת במערכת שלך", "error")
                return redirect(url_for('invoice'))

            # חוסם לחלוטין שמירה או עריכה מחדש של מסמך שכבר בוטל!
            if invoice.status in ["canceled", "מבוטלת"]:
                flash("שגיאה חמורה: לא ניתן לערוך או לשמור חשבונית מבוטלת.", "error")
                return redirect(url_for('invoice_view', invoice_id=invoice.id))

            # שלב א': החזרת המלאי הישן של המוצרים לפני מחיקת הפריטים
            old_items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
            for old_item in old_items:
                if old_item.product_id in ['rent', 'stocks', 'dividend', 'unspecified', 'deleted']:
                    continue

                prod = Product.query.filter_by(local_id=old_item.product_id, company_id=company_id).first()
                if not prod:
                    continue

                item_file_old = load_item_file(company_id, prod.local_id) or {}
                i_cat_old = item_file_old.get("income_category", getattr(prod, 'income_category', 'service'))

                if i_cat_old == 'product':
                    prod.quantity += old_item.quantity
                    db.session.flush()

                    current_stock_out_old = int(item_file_old.get("stock_out", 0))
                    new_stock_out = max(0, current_stock_out_old - int(old_item.quantity))
                    existing_batches_old = item_file_old.get("batches", [])

                    save_item_file(
                        company_id=company_id,
                        local_id=prod.local_id,
                        name_trans=item_file_old.get("name", {"he": prod.name}),
                        desc_trans=item_file_old.get("description", {"he": prod.description}),
                        price=prod.price,
                        income_category=i_cat_old,
                        cost_price=prod.cost_price,
                        stock_out=new_stock_out,
                        sku=prod.sku,
                        batches=existing_batches_old,
                        supplier_name_trans=item_file_old.get("supplier_name", {"he": "מלאי פתיחה / כללי"})
                    )

            # שלב ב': עדכון נתוני כותרת החשבונית
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

            # שלב ג': עיבוד והוספת פריטי החשבונית החדשים מהטופס במצב עריכה
            items = request.form.getlist('items[]')
            for item_json in items:
                try:
                    item_data = json.loads(item_json)
                except Exception:
                    continue

                p_id_val = item_data['product_id']
                qty = clean_float(item_data.get('quantity'))
                u_price = clean_float(item_data.get('price'))
                disc = clean_float(item_data.get('discount', 0))
                total_after_discount = (qty * u_price) - (qty * u_price * (disc / 100) if disc < 100 else disc)
                
                item_description = item_data.get('description', '').strip()

                if p_id_val in ['rent', 'stocks', 'dividend', 'unspecified']:
                    db.session.add(InvoiceItem(
                        invoice_id=invoice.id,
                        product_id=p_id_val,
                        description=item_description or p_id_val,
                        quantity=qty,
                        unit_price=u_price,
                        discount=disc,
                        total_price=total_after_discount,
                        cost_price_at_time=0.0,
                        income_category=p_id_val
                    ))
                    continue

                prod = Product.query.filter_by(id=p_id_val, company_id=company_id).first()
                if not prod:
                    prod = Product.query.filter_by(local_id=p_id_val, company_id=company_id).first()
                
                if not prod:
                    continue

                db_product_id = prod.local_id if prod.local_id is not None else prod.id

                item_file = load_item_file(company_id, prod.local_id) or {}
                i_cat = item_file.get("income_category", getattr(prod, 'income_category', 'service'))
                c_price = float(item_file.get("cost_price", prod.cost_price or 0.0))

                if not item_description:
                    item_description = item_file.get("name", {}).get("he", prod.name)

                if i_cat == 'product':
                    prod.quantity -= qty
                    db.session.flush()

                    existing_batches = item_file.get("batches", [])
                    current_stock_out_now = int(item_file.get("stock_out", 0))
                    new_stock_out = current_stock_out_now + int(qty)

                    save_item_file(
                        company_id=company_id,
                        local_id=prod.local_id,
                        name_trans=item_file.get("name", {"he": prod.name}),
                        desc_trans=item_file.get("description", {"he": prod.description}),
                        price=prod.price,
                        income_category=i_cat,
                        cost_price=prod.cost_price,
                        stock_out=new_stock_out,
                        sku=prod.sku,
                        batches=existing_batches,
                        supplier_name_trans=item_file.get("supplier_name", {"he": "מלאי פתיחה / כללי"})
                    )

                total_invoice_cost += (qty * c_price)

                db.session.add(InvoiceItem(
                    invoice_id=invoice.id,
                    product_id=str(db_product_id),  
                    description=item_description, 
                    quantity=qty,
                    unit_price=u_price,
                    discount=disc,
                    total_price=total_after_discount,
                cost_price_at_time=c_price, 
                income_category=i_cat
            ))

        # שלב ד': שמירת פרטי התשלומים החדשים במצב עריכה
        amounts = request.form.getlist('payment_amount[]')
        payment_dates = request.form.getlist('payment_date[]')
        methods = request.form.getlist('payment_method[]')
        banks = request.form.getlist('bank[]')
        branches = request.form.getlist('branch[]')
        accounts = request.form.getlist('account_number[]')

        for i in range(len(amounts)):
            amt = clean_float(amounts[i])
            if amt <= 0:
                continue

            p_date = None
            if i < len(payment_dates) and payment_dates[i]:
                date_str = payment_dates[i].strip()
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d"):
                    try:
                        p_date = datetime.strptime(date_str, fmt).date()
                        break
                    except ValueError:
                        continue

            db.session.add(Payment(
                company_id=company_id,
                invoice_id=invoice.id,
                payment_date=p_date if p_date else datetime.today().date(),
                payment_method=methods[i] if i < len(methods) else "",
                payment_amount=amt,
                bank=banks[i] if i < len(banks) else "",
                branch=branches[i] if i < len(banks) else "",
                account_number=accounts[i] if i < len(accounts) else ""
            ))

        # שלב ה': עדכון או יצירת תנועה פיננסית ביומן התנועות
        existing_trans = Transaction.query.filter_by(invoice_id=invoice.invoice_number, company_id=company_id).first()
        if existing_trans:
            existing_trans.date = invoice.invoice_date
            existing_trans.description = f"חשבונית #{invoice.invoice_number}"
            existing_trans.amount = sub_total
            existing_trans.customer_id = db_customer_id
            existing_trans.cost_price_at_time = total_invoice_cost
        else:
            max_local_trans = db.session.query(db.func.max(Transaction.local_id)).filter(Transaction.company_id == company_id).scalar()
            next_trans_local_id = (max_local_trans or 0) + 1
            
            db.session.add(Transaction(
                company_id=company_id,
                local_id=next_trans_local_id,
                date=invoice.invoice_date,
                description=f"חשבונית #{invoice.invoice_number}",
                amount=sub_total,
                type='income',
                category_id=None,
                invoice_id=invoice.invoice_number,
                customer_id=db_customer_id,
                cost_price_at_time=total_invoice_cost,
                quantity=1
            ))

        db.session.commit()
        flash('החשבונית עודכנה בהצלחה והמלאי סונכרן', 'success')
        return redirect(url_for('invoice_view', invoice_id=invoice.id))

    # ====== 2. יצירת חשבונית חדשה לחלוטין (מצב יצירה) ======
    else:
        invoice_number = get_next_invoice_number(company_id=company_id)

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

        items = request.form.getlist('items[]')
        for item_json in items:
            try:
                item_data = json.loads(item_json)
            except Exception:
                continue

            p_id_val = item_data['product_id']
            qty = clean_float(item_data.get('quantity'))
            u_price = clean_float(item_data.get('price'))
            disc = clean_float(item_data.get('discount', 0))
            
            row_total = (qty * u_price) - (qty * u_price * (disc / 100) if disc < 100 else disc)
            item_description = item_data.get('description', '').strip()

            if p_id_val in ['rent', 'stocks', 'dividend', 'unspecified']:
                db.session.add(InvoiceItem(
                    invoice_id=new_invoice.id,
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

            prod = Product.query.filter_by(id=p_id_val, company_id=company_id).first()
            if not prod:
                prod = Product.query.filter_by(local_id=p_id_val, company_id=company_id).first()
            
            if not prod:
                continue

            db_product_id = prod.local_id if prod.local_id is not None else prod.id

            item_file = load_item_file(company_id, prod.local_id) or {}
            i_cat = item_file.get("income_category", getattr(prod, 'income_category', 'service'))
            c_price = float(item_file.get("cost_price", prod.cost_price or 0.0))

            if not item_description:
                item_description = item_file.get("name", {}).get("he", prod.name)

            if i_cat == 'product':
                prod.quantity -= qty
                db.session.flush()

                existing_batches = item_file.get("batches", [])
                current_stock_out_new = int(item_file.get("stock_out", 0))
                new_stock_out = current_stock_out_new + int(qty)

                save_item_file(
                    company_id=company_id,
                    local_id=prod.local_id,
                    name_trans=item_file.get("name", {"he": prod.name}),
                    desc_trans=item_file.get("description", {"he": prod.description}),
                    price=prod.price,
                    income_category=i_cat,
                    cost_price=prod.cost_price,
                    stock_out=new_stock_out,
                    sku=prod.sku,
                    batches=existing_batches,
                    supplier_name_trans=item_file.get("supplier_name", {"he": "מלאי פתיחה / כללי"})
                )

            total_invoice_cost += (qty * c_price)

            db.session.add(InvoiceItem(
                invoice_id=new_invoice.id,
                product_id=str(db_product_id),  
                description=item_description, 
                quantity=qty,
                unit_price=u_price,
                discount=disc,
                total_price=row_total,
                cost_price_at_time=c_price,
                income_category=i_cat
            ))

        # קליטה ושמירה של מערך התשלומים עבור החשבונית החדשה
        amounts       = request.form.getlist('payment_amount[]')
        payment_dates = request.form.getlist('payment_date[]')
        methods       = request.form.getlist('payment_method[]')
        banks         = request.form.getlist('bank[]')
        branches      = request.form.getlist('branch[]')
        accounts      = request.form.getlist('account_number[]')

        for i in range(len(amounts)):
            amt = clean_float(amounts[i])
            if amt <= 0:
                continue

            raw_payment_date = payment_dates[i].strip() if (i < len(payment_dates) and payment_dates[i]) else ""
            p_date = None

            if raw_payment_date:
                for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y", "%Y/%m/%d"):
                    try:
                        p_date = datetime.strptime(raw_payment_date, fmt).date()
                        break
                    except ValueError:
                        continue

            db.session.add(Payment(
                company_id=company_id,
                invoice_id=new_invoice.id,
                payment_date=p_date if p_date else datetime.today().date(),
                payment_method=methods[i] if i < len(methods) else "",
                payment_amount=amt,
                bank=banks[i] if i < len(banks) else "",
                branch=branches[i] if i < len(banks) else "",
                account_number=accounts[i] if i < len(accounts) else ""
            ))

        db.session.flush()

        # חישוב ה-local_id הבא עבור התנועה הפיננסית
        max_local_trans = db.session.query(db.func.max(Transaction.local_id))\
            .filter(Transaction.company_id == company_id).scalar()
        
        next_trans_local_id = (max_local_trans or 0) + 1

        new_trans = Transaction(
            company_id=company_id,
            local_id=next_trans_local_id,
            date=new_invoice.invoice_date,
            description=f"חשבונית #{new_invoice.invoice_number}",
            amount=sub_total,
            type='income',
            category_id=None,            
            invoice_id=new_invoice.id,                        
            customer_id=db_customer_id,             
            cost_price_at_time=total_invoice_cost, 
            quantity=1
        )
        db.session.add(new_trans)

        # שמירה סופית וסגירת העסקה בפוסטגרס בצורה חלקה
        db.session.commit()
        
        flash('החשבונית הופקה בהצלחה והמלאי עודכן', 'success')
        return redirect(url_for('invoice_view', invoice_id=new_invoice.id))


# --------------------
# טופס יצירת/עריכת חשבונית (Multi-Tenant Secure via invoice_context / base_invoice_context)
# ----------------------

@app.route('/invoice/create', methods=['GET'])
@login_required
def invoice():
    invoice_id = request.args.get('invoice_id')
    
    customer_id = request.args.get('customer_id')
    if customer_id:
        customer_id = customer_id.strip()

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    if invoice_id:
        ctx = invoice_context(invoice_id=invoice_id)
    else:
        ctx = base_invoice_context(customer_id=customer_id)

    return render_template('invoice.html', **ctx)


# --------------------
# תצוגת מסמך חשבונית חתום/סגור (Multi-Tenant Secure)
# ----------------------

@app.route('/invoice/<int:invoice_id>', methods=['GET'])
@login_required
def invoice_view(invoice_id):

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    ctx = invoice_context(invoice_id=invoice_id)

    if "company" not in ctx or not ctx["company"]:
        company_obj = db.session.get(Company, company_id) if company_id else None
        ctx["company"] = load_company_translated(company_obj, get_lang())

    if not ctx.get("invoice"):
        flash("חשבונית לא נמצאה או שאין לך הרשאת גישה אליה", "danger")
        return redirect(url_for('invoice_data'))

    return render_template('invoice.html', **ctx)


# --------------------
# איפוס מצב החשבונית ומעבר למסמך חדש נקי
# ----------------------

@app.route("/invoice/new", methods=["GET", "POST"])
@login_required
def new_invoice():
    return redirect(url_for('invoice'))


# --------------------
# ביטול חשבונית קיימת וסנכרון מלאי/דוחות (Multi-Tenant Secure)
# ----------------------

@app.route('/invoice/<int:invoice_id>/cancel', methods=['POST'])
@login_required
def cancel_invoice(invoice_id):
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    # שליפת החשבונית המבוקשת לביטול
    invoice = db.session.get(Invoice, invoice_id)

    if not invoice or invoice.company_id != company_id:
        flash("המסמך המבוקש אינו קיים במערכת שלך", "error")
        return redirect(url_for('invoice_data'))

    # מניעת ביטול כפול של מסמך שכבר מבוטל
    if invoice.status in ["canceled", "מבוטלת"]:
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    # שלב א': החזרת המלאי ועדכון קבצי ה-JSON של הפריטים בחשבונית
    items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()

    for item in items:
        if item.product_id in ['rent', 'stocks', 'dividend', 'unspecified', 'deleted']:
            continue

        # קיבוע עוגן השליפה על בסיס ה-local_id שקבענו כעוגן אחיד למוצר
        prod = Product.query.filter_by(local_id=item.product_id, company_id=company_id).first()
        
        if prod and getattr(prod, 'income_category', 'service') == 'product':
            #  המלאי המקורי שלך חוזר למקומו בדיוק כפי שכתבת אותו!
            prod.quantity += item.quantity
            db.session.flush()
            
            item_file = load_item_file(company_id, prod.local_id) or {} 
            product_local_id = prod.local_id if prod.local_id is not None else prod.id

            # חישוב מתמטי מהיר ובטוח של היציאות החדשות ישירות מתוך ה-JSON המקורי שלך
            current_json_stock_out = int(item_file.get("stock_out", 0))
            actual_out_calc = max(0, current_json_stock_out - int(item.quantity))

            # שליפת כל ה-batches מהפנקס (inventory_transactions)
            existing_batches = _load_inventory_batches(company_id, product_local_id)

            # שמירה מחדש של קובץ ה-product.json המקורית שלך
            save_item_file(
                company_id=company_id,
                local_id=product_local_id,
                name_trans=item_file.get("name", {"he": prod.name}),
                desc_trans=item_file.get("description", {"he": prod.description}),
                price=prod.price,
                income_category='product',
                cost_price=prod.cost_price,
                stock_out=actual_out_calc,
                sku=prod.sku,
                batches=existing_batches,
                supplier_name_trans=item_file.get("supplier_name", {"he": "מלאי פתיחה / כללי"})
            )

    # שלב ב': עדכון התנועה הפיננסית (Transaction) במקום מחיקה פיזית
    # קובע מראש את ברירת המחדל המתורגמת כדי לשלוף אותה במקרה שהמשתמש שלח טקסט ריק
    current_lang = get_lang()
    default_text = "General Cancellation" if current_lang != "he" else "ביטול כללי"
    invoice_reason = request.form.get("cancel_reason", "").strip() or default_text

    trans = Transaction.query.filter_by(invoice_id=invoice.id).first()
    if trans:
        trans.amount = 0.0
        trans.cost_price_at_time = 0.0

        if current_lang != "he":
            trans.description = f"Invoice #{invoice.invoice_number} (Canceled: {invoice_reason})"
        else:
            trans.description = f"חשבונית #{invoice.invoice_number} (מבוטלת: {invoice_reason})"

    # שלב ג': שינוי סטטוס החשבונית לביטול סופי נקי בבסיס הנתונים
    invoice.status = "canceled"

    # נועלים את סיבת הביטול המקורית בשדה הקיים כגיבוי ראשוני
    user_typed_reason = request.form.get("cancel_reason", "").strip()
    current_lang = get_lang()
    default_text = "General Cancellation" if current_lang != "he" else "ביטול כללי"
    invoice_reason = user_typed_reason or default_text
    
    invoice.cancellation_reason = invoice_reason
    
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
            
    db.session.commit()

    flash("החשבונית בבוטלה בהצלחה ותנועות המס עודכנו", "success")
    return redirect(url_for('invoice_view', invoice_id=invoice.id))


# ----------------------
# Show All Invoices invoice_data Page
# ----------------------

@app.route('/invoices')
@login_required
def invoice_data():
    language = get_lang()   
    search = request.args.get("q", "").strip().lower()
    selected_month = request.args.get("month", "")
    selected_year = request.args.get("year", "")
    selected_status = request.args.get("status", "all")

    # ברירת מחדל לשנה וחודש נוכחיים לצורך סינון ראשוני מהיר
    if not selected_year:
        selected_year = str(datetime.today().year)
    if not selected_month:
        selected_month = datetime.today().strftime('%m')

    # קביעת מזהה החברה האקטיבית (Multi-Company Protection)
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    if not company_id:
        flash("שגיאה: אין חברה פעילה", "danger")
        return redirect(url_for('invoice'))

    # טעינת קטגוריות עסקיות מקובצי JSON (עבור תצוגה מותאמת שפה)
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

    # שליפת כל החשבוניות של החברה עם טעינה מוקדמת (Joinedload) למניעת עומס שאילתות
    invoices = Invoice.query.filter_by(company_id=company_id).options(
        db.joinedload(Invoice.customer),
        db.joinedload(Invoice.items)
    ).all()

    # מיפוי מוקדם של כל הברקודים/מק"טים בזיכרון השרת לייעול החיפוש בלولאה
    all_products_sku_map = {}
    try:
        for p in Product.query.filter_by(company_id=company_id).all():
            if p.sku:
                all_products_sku_map[str(p.local_id)] = p.sku.lower()
                all_products_sku_map[str(p.id)] = p.sku.lower()
    except Exception as e:
        print(f"⚠️ Warning: product sku preload failed: {e}")

    # מיון מאובטח בפייתון לפי מספר החשבונית מהגבוה לנמוך
    def safe_int_invoice(inv_obj):
        num_str = str(inv_obj.invoice_number or "0").strip()
        return int(num_str) if num_str.isdigit() else 0

    invoices.sort(key=safe_int_invoice, reverse=True)

    # מילון עזר פנימי לסנכרון חיפושי מק"טים של 30 שפות בדף הניהול
    SKU_LOCALIZED_SERVER = {
        "he": {"rent": "שכירות", "stocks": "מניות", "dividend": "דיבידנד", "unspecified": "כללי"},
        "en": {"rent": "rental", "stocks": "stocks", "dividend": "dividend", "unspecified": "general"}
    }

    filtered_invoices = []
    customer_i18n_list = {}
    all_customers_json = []
    total_profit = 0.0 
    is_numeric_search = search.isdigit()

    # איסוף רשימת כל הלקוחות לטובת סנכרון הקומבובוקס ברילוד
    try:
        my_customers = Customer.query.filter_by(company_id=company_id).order_by(Customer.customer_name).all()
        for c in my_customers:
            c_trans = load_customer_translated(c, language) or {}
            all_customers_json.append({
                "id": c.id,
                "customer_name": c_trans.get("name") or c.customer_name,
                "id_number": c.id_number or ""
            })
    except Exception as e:
        print(f"⚠️ Warning: customer extraction failed: {e}")

    for inv in invoices:
        trans_name = ""
        db_name = ""
        
        # טיפול והצגת שם הלקוח (חברה עצמית או לקוח חיצוני מתורגם)
        if str(inv.customer_id) == "0":
            self_name = f"★ {company_translated.get('name', company_obj.name if company_obj else '')} "
            trans_name = self_name.lower()
            db_name = self_name.lower()
            customer_i18n_list["0"] = {"name": self_name}
        elif inv.customer:
            cid = inv.customer.id
            if cid not in customer_i18n_list:
                customer_i18n_list[cid] = load_customer_translated(inv.customer, language, company_id=company_id)
            
            trans_name = (customer_i18n_list[cid].get('name', '') or "").lower()
            db_name = inv.customer.customer_name.lower()

        # בדיקת התאמה לסינונים (סטטוס וחיפוש טקסטואלי/מספרי)
        match_status = (selected_status == "all" or inv.status == selected_status)
        if not search:
            match_search = True
        else:
            if is_numeric_search:
                match_search = (str(inv.invoice_number) == search)
            else:
                match_search = (search in db_name or search in trans_name or search in str(inv.invoice_date))
            
            # הרחבת החיפוש לסריקת מק"טים ותיאורים פנימיים בתוך פריטי החשבונית
            if not match_search:
                for item in inv.items:
                    item_sku = all_products_sku_map.get(str(item.product_id), "")
                    
                    # הגנה לחשבונית עצמית: אם המק"ט לא קיים במפה הרגילה, נבדוק את מילון השפות הפנימי
                    if not item_sku and str(inv.customer_id) == "0":
                        p_key = str(item.product_id).strip().lower()
                        lang_sku = SKU_LOCALIZED_SERVER.get(language, SKU_LOCALIZED_SERVER["he"])
                        item_sku = lang_sku.get(p_key, p_key).lower()

                    item_desc = str(item.description or "").lower()
                    if search in item_sku or search in item_desc:
                        match_search = True
                        break

        inv_year = str(inv.invoice_date.year)
        inv_month = inv.invoice_date.strftime('%m')
        
        # החלת הסינון הסופי לפי תאריכים
        if match_status and match_search and (not selected_month or inv_month == selected_month) and (not selected_year or inv_year == selected_year):
            row_profit = 0.0
            
            if inv.status == "canceled":
                row_profit = 0.0
            else:
                # חישוב עלות המכר (COGS) האמיתית מתוך פריטי החשבונית שננעלו בזמן ה-POST
                total_item_cost = 0.0
                for item in inv.items:
                    total_item_cost += float(item.quantity or 0.0) * float(item.cost_price_at_time or 0.0)

                # רווח גולמי = סך הכל ללא מע"מ פחות עלות המכר
                row_profit = float(inv.sub_total or 0.0) - total_item_cost
                total_profit += row_profit

            inv.profit = row_profit
            filtered_invoices.append(inv)

    # סיכום מחזור הכנסות רק מחשבוניות פעילות (לא כולל מבוטלות)
    active_invoices = [inv for inv in filtered_invoices if inv.status != "canceled"]

    return render_template(
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
        years=[str(y) for y in range(2024, 2031)], 
        customer_i18n_list=customer_i18n_list, 
        all_customers_json=all_customers_json,  
        business_categories=business_categories,
        language=language, 
        company=company_translated, 
        company_db=company_obj
    )


# ----------------------
# Send Email Invoices To Customers invoice_data Page (Multi-Tenant Secure)
# ----------------------

@app.route('/send_invoice_email/<int:invoice_id>')
@login_required
def send_invoice_email(invoice_id):

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    invoice = db.session.get(Invoice, invoice_id)
    if not invoice or invoice.company_id != company_id:
        flash("המסמך המבוקש אינו קים במערכת שלך", "danger")
        return redirect(url_for('invoice_data'))

    customer = invoice.customer
    if not customer or not customer.email:
        flash("ללקוח לא מוגדר אימייל! אנא עדכן את כרטיס הלקוח תחילה.", "danger")
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    send_invoice_email_in_background(invoice_id, company_id)

    flash("בקשת השליחה התקבלה! החשבונית מופקת ונשלחת ללקוח ברקע.", "success")
    return redirect(url_for('invoice_view', invoice_id=invoice_id))


def send_invoice_email_in_background(invoice_id, company_id):
    thread = threading.Thread(
        target=run_invoice_email_task,
        args=(invoice_id, company_id) 
    )
    thread.daemon = True
    thread.start()


def run_invoice_email_task(invoice_id, company_id):
    try:
        with app.app_context():
            invoice = db.session.get(Invoice, invoice_id)
            if not invoice or invoice.company_id != company_id or not invoice.customer or not invoice.customer.email:
                print(f"❌ Security Block: Mail task aborted for Invoice ID {invoice_id}")
                return

            language = getattr(invoice.customer, 'language', 'he')

            company_obj = db.session.get(Company, company_id)
            company_data = load_company_translated(company_obj, language)
            customer = invoice.customer

            ctx = invoice_context(invoice_id)
            ctx["company"] = company_data
            ctx["language"] = language 

            html_content = render_template("invoice.html", **ctx, is_pdf=True)

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
                recipients=[customer.email],
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

    invoice = db.session.get(Invoice, invoice_id)
    if not invoice:
        return "Invoice not found", 404

    if status == "success":
        if invoice.is_paid:
            return "Already processed", 200

        invoice.is_paid = True
        invoice.payment_transaction_id = transaction_id
        invoice.payment_date = datetime.utcnow()

        customer = Customer.query.filter_by(
            id=customer_id, 
            company_id=invoice.company_id 
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

        # הפיקס הנכון: מייצרים את השורה הזמנית בטבלת Payment ולא ב-Transaction!
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
        display_amount = float(getattr(link_obj, 'payment_amount', 0.0))
        inv_num = getattr(link_obj, 'invoice_id', None)

        if request.method == 'GET':
            customer_name = ""
            customer_address = ""
            customer_city = ""
            
            if inv_num:
                invoice_record = db.session.get(Invoice, inv_num)
                if invoice_record and invoice_record.customer_id:
                    customer_obj = Customer.query.filter_by(id=invoice_record.customer_id, company_id=comp_id).first()
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
                    cardcom_net_call = requests.post(gateway_url, data=cardcom_payload, timeout=15)
                    cardcom_response_data = {k: v[0] for k, v in parse_qs(cardcom_net_call.text).items()}

                if cardcom_response_data.get("ResponseCode") == "0":
                    ticket_number = cardcom_response_data.get("TicketNumber", "APPROVED")
                    
                    # עדכון שדות האישור בטבלת Payment בלבד (שם זה לא מייצר כפילויות ביומן!)
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
        # הפיקס המושלם: קוראים מטבלת Payment כדי להסתנכרן עם הראוטים החדשים ומניעת הכפילויות!
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

    # קביעת מזהה החברה בהתאם להרשאות המשתמש
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    if request.method == 'POST':

        # --- נתוני טופס בסיסיים ---
        product_id_raw = (request.form.get("id") or "").strip()
        product_id = int(product_id_raw) if product_id_raw.isdigit() else None

        name = (request.form.get('name') or '').strip()
        description = (request.form.get('description') or '').strip()
        user_sku = (request.form.get('sku') or '').strip()
        income_category = request.form.get("income_category", "product")

        # --- תאריך קבלה מהטופס העליון ---
        received_date_raw = (request.form.get('received_date') or '').strip()
        if not received_date_raw:
            flash('תאריך הוא שדה חובה', 'error')
            return redirect(url_for('manage_products'))

        # מונע קריסות של ימים 13-31 בכל השפות בעולם (עברית, אנגלית, בנגלית, רוסית וערבית)
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

        # --- המרות מספריות בטוחות ---
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

        # --- זיהוי מוצר קיים בדאטהבייס ---
        product = None
        if product_id:
            product = Product.query.filter_by(id=product_id, company_id=company_id).first()
        if not product and user_sku:
            product = Product.query.filter_by(sku=user_sku, company_id=company_id).first()
        if not product and name:
            product = Product.query.filter_by(name=name, company_id=company_id).first()

        # --- שליפת ספק ---
        sp_supplier = None
        if supplier_id:
            sp_supplier = Supplier.query.filter_by(id=supplier_id, company_id=company_id).first()

        # --- טעינת מנות מלאי קיימות ---
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

            total_sold = db.session.query(func.sum(InvoiceItem.quantity)) \
                .join(Invoice) \
                .filter(
                    InvoiceItem.product_id == str(target_local_id),
                    InvoiceItem.income_category == 'product',
                    Invoice.company_id == company_id,
                    Invoice.status != "canceled"
                ).scalar() or 0

        stock_out = int(total_sold)

        # --- הוספת מלאי חדש למוצר קיים ---
        if product and stock_in > 0:
            save_inventory_transaction(
                company_id=company_id,
                local_id=product.local_id if product.local_id is not None else product.id,
                received_date=formatted_date,
                stock_in=int(stock_in),
                cost_price=float(cost_price),
                supplier_id=sp_supplier.id if sp_supplier else None,
                supplier_name=None  # עקור! אין שמות קשיחים באצוות
            )

            batches = _load_inventory_batches(company_id, product.local_id if product.local_id is not None else product.id)
            real_stock_in = sum(int(b.get("stock_in", 0)) for b in batches)
        else:
            if product:
                real_stock_in = sum(int(b.get("stock_in", 0)) for b in batches)
            else:
                real_stock_in = int(stock_in)

        current_stock = real_stock_in - stock_out

        # --- עדכון מוצר קיים בדאטהבייס SQL ---
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
        else:
            # --- יצירת מוצר חדש לחלוטין ---
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
            product_id = product.id

            save_inventory_transaction(
                company_id=company_id,
                local_id=next_local_id,
                received_date=formatted_date,
                stock_in=int(stock_in),
                cost_price=float(cost_price),
                supplier_id=sp_supplier.id if sp_supplier else None,
                supplier_name=None
            )

        #  אינטגרציה מלאה: מנוע התרגום מקבל את השם הגולמי של הספק העדכני ומתרגם אותו ל-30 שפות לתוך ה-product.json הראשי!
        translate_product_in_background(
            product_id=product_id,
            company_id=company_id,
            name=name,
            description=description,
            price=price,
            income_category=income_category,
            sku=user_sku if user_sku else (product.sku if product else None),
            supplier_name=sp_supplier.supplier_name if sp_supplier else "מלאי פתיחה / כללי"
        )

        # --- תיעוד רכישת ספק ---
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
            sups_dict = item_file.get("supplier_name", {"he": "מלאי פתיחה / כללי"})

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

    item_i18n_list = {}
    for p in filtered_objects:
        p_local_id = p.local_id if p.local_id is not None else p.id

        translated_data = load_item_translated(p, language, company_id)
        item_file = load_item_file(company_id, p_local_id) or {}
        batches_list_val = _load_inventory_batches(company_id, p_local_id)
        
        supplier_id_val = None
        if batches_list_val and isinstance(batches_list_val, list) and len(batches_list_val) > 0:
            supplier_id_val = batches_list_val[-1].get("supplier_id")

        current_date_val = p.received_date
        if current_date_val and "/" in current_date_val:
            try: current_date_val = datetime.strptime(current_date_val, '%d/%m/%Y').strftime('%Y-%m-%d')
            except: pass

        item_i18n_list[p.id] = {
            "id": p.id,
            "local_id": p.local_id,
            "sku": translated_data.get("sku"),
            "name": translated_data.get("name"),                  
            "description": translated_data.get("description"),    
            "supplier_name": translated_data.get("supplier_name"), 
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
                try: b_copy["received_date"] = datetime.strptime(b_date_raw, '%d/%m/%Y').strftime('%Y-%m-%d')
                except: pass
            
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

    # בניית רשימת הבחירה לקומבו-בוקס (Combobox) בהצלבה מול קבצי המוצרים המתורגמים!
    # מונע מיסמאץ' ומבטיח ששם הספק יוצג בשפה האקטיבית של המשתמש מייד ברילווד
    all_suppliers = Supplier.query.filter_by(company_id=company_id).order_by(Supplier.supplier_name).all()
    suppliers_translated = []
    
    for s in all_suppliers:
        s_name_flat = s.supplier_name # פולבק קשיח לעברית מה-DB
        
        # סורקים את המוצרים שכבר תורגמו מהדיסק בדף הנוכחי כדי למצוא את שם הספק בשפה הנכונה
        found_translated_name = None
        for p_id, p_info in item_i18n_list.items():
            if p_info.get("supplier_id") == s.id and p_info.get("supplier_name"):
                found_translated_name = p_info.get("supplier_name")
                break
        
        if found_translated_name:
            s_name_flat = found_translated_name
        else:
            # פולבק שני: בדיקה אם בכל זאת קיים קובץ ספק כללי סטטי בדיסק
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
    # התאמה בטוחה של קוד השפה למבנה של גוגל בדיסק
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    products = Product.query.filter_by(company_id=company_id).order_by(Product.id.asc()).all()
    result = []

    for p in products:
        p_local_lookup = p.local_id if p.local_id is not None else p.id
        
        # 1. שליפת נתוני הליבה מההלפר המתורגם
        translated = load_item_translated(p, language, company_id)

        # 2. חילוץ שמות ותיאורים שטוחים מההלפר
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

        #  טוענים את קובץ ה-JSON הגולמי מהדיסק כדי לחלץ את מילון 30 השפות האמיתי של הספק!
        # מונע מיסמאץ' של עברית הפוכה במוצרים קיימים ובאצוות מלאי חדשות.
        prod_data = load_item_file(company_id, p_local_lookup)
        if prod_data and isinstance(prod_data.get("supplier_name"), dict):
            supplier_name = prod_data["supplier_name"].get(lookup_lang) or prod_data["supplier_name"].get("he") or "מלאי פתיחה / כללי"
        else:
            # פולבק קשיח להלפר המתורגם או ל-Database במידה והקובץ לא קיים
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
            
            #  מעדכן את שם הספק בתוך האצווה לשם המתורגם האמיתי מהאב!
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
#   Delete All Product  (Multi-Tenant Secure)
# ----------------------

def delete_product_folder(product_id, company_id, local_id=None):
    effective_local_id = local_id if local_id is not None else product_id
    base_dir = app.config["ITEMS_DIR"]
    folder_path = os.path.join(base_dir, f"{company_id}_{effective_local_id}")

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

    try:
        product_ids_to_full_delete = []
        
        for payload in selected_payloads:
            if "_" in str(payload):
                parts = payload.split("_")
                p_identifier = int(parts[0])      # מזהה המוצר (4)
                raw_payload_date = parts[1]       # תאריך האצווה שהגיע מהשפה ב-DOM
                batch_qty = int(parts[2])          # כמות האצווה (50)

                product = Product.query.filter(
                    (Product.id == p_identifier) | (Product.local_id == p_identifier),
                    Product.company_id == company_id
                ).first()
                
                if not product:
                    continue

                effective_local_id = product.local_id if product.local_id is not None else product.id
                folder = ensure_product_folder(company_id, effective_local_id)
                inv_folder = os.path.join(folder, "inventory_transactions")

                # פענוח תאריך השפה הגלובלי שהגיע מה-DOM והמרתו לפורמט הרישום בדיסק (ISO)
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

                # שליפת האצוות המקוריות ישירות מקובץ ה-product.json הראשי
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

                    # השוואה הרמטית לבידוד ומחיקת האצווה הספציפית
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

                    # שכתוב קובץ ה-product.json הראשי ללא האצווה שנמחקה
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
                # מפעיל את מגרסת התיקיות האגרסיבית והנכונה
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



# ----------------------
#   Build All Customer Form (Multi-Tenant Secure)
# ----------------------

@app.route('/customer', methods=['GET', 'POST'])
@login_required
def customer():

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

    if request.method == 'POST':
        date_str = request.form.get('date')
        if not date_str:
            flash('תאריך הוא שדה חובה', 'error')
            return redirect(url_for('customer'))

        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%d/%m/%Y')
        except:
            formatted_date = date_str

        customer_id   = request.form.get('customer_id')
        id_number     = request.form.get('id_number', '').strip()
        customer_name = request.form.get('customer_name', '').strip()
        email_input   = request.form.get('email', '').strip().lower()

        # ------------------ UPDATE CUSTOMER ------------------
        if customer_id:
            customer_obj = Customer.query.filter_by(
                id=customer_id,
                company_id=active_company_id
            ).first()

            if customer_obj:
                customer_obj.date            = formatted_date
                customer_obj.customer_name   = customer_name
                customer_obj.id_number       = id_number
                customer_obj.address         = request.form.get('address')
                customer_obj.city            = request.form.get('city')
                customer_obj.postal_code     = request.form.get('postal_code')
                customer_obj.phone           = request.form.get('phone')
                customer_obj.email           = email_input
                customer_obj.contract_status = request.form.get('contract_status')
                customer_obj.message         = request.form.get('message')

                db.session.commit()

                translate_customer_in_background(
                    customer_id=customer_obj.id,
                    company_id=active_company_id,
                    name=customer_obj.customer_name,
                    address=customer_obj.address,
                    city=customer_obj.city,
                    message=customer_obj.message
                )

                flash('הנתונים עודכנו בהצלחה!', 'success')

        # ------------------ CREATE NEW CUSTOMER  ------------------
        else:
            duplicate = Customer.query.filter(
                (Customer.company_id == active_company_id) &
                ((Customer.email == email_input) | (Customer.customer_name == customer_name))
            ).first()

            if duplicate:
                flash("לקוח עם אימייל זה או שם זה כבר קיים בחברת הבעלים שלך!", "warning")
                return redirect(url_for('customer'))

            if id_number and Customer.query.filter_by(
                id_number=id_number,
                company_id=active_company_id
            ).first():
                flash("קיים כבר לקוח עם מספר זהות/ח.פ זה במערכת שלך", "error")
                return redirect(url_for('customer'))

            if active_company_id == OWNER_COMPANY_ID:
                
                existing_company = Company.query.filter(
                    (Company.name == customer_name) | (Company.email == email_input)
                ).first()
                
                if existing_company:
                    flash("חומת אש: חברה עם שם זה או אימייל זה כבר רשומה במערכת כעסק עצמאי!", "danger")
                    return redirect(url_for('customer'))

                if id_number:
                    existing_comp_by_hp = Company.query.filter_by(company_id_number=id_number).first()
                    if existing_comp_by_hp:
                        flash("חומת אש: מספר ח.פ / תעודת זהות זו כבר משויכת לחברה קיימת במערכת!", "danger")
                        return redirect(url_for('customer'))

                existing_global_user = User.query.filter_by(email=email_input).first()
                if existing_global_user:
                    flash("חומת אש: כתובת אימייל זו כבר משויכת למשתמש קיים במערכת!", "danger")
                    return redirect(url_for('login'))

                new_company = Company(
                    name=customer_name,
                    email=email_input,
                    company_id_number=id_number,
                    address=request.form.get('address'),        
                    city=request.form.get('city'),              
                    postal_code=request.form.get('postal_code'), 
                    phone=request.form.get('phone'),            
                    translations_json="{}"
                )
                db.session.add(new_company)
                db.session.flush() 

                user_obj = User(
                    email=email_input,
                    username=customer_name,
                    company_id=new_company.id, 
                    role='manager',             
                    is_active=True,
                    is_approved=True
                )
                user_obj.set_password("TemporarySetupPassword123!") 
                db.session.add(user_obj)
                db.session.flush()  

                try:
                    translate_company_in_background(
                        company_id=new_company.id,
                        name=new_company.name,
                        id_number=new_company.company_id_number,
                        deduction_file="", 
                        address=new_company.address,       
                        city=new_company.city,             
                        postal_code=new_company.postal_code or "", 
                        phone=new_company.phone or "", 
                        email=email_input, 
                        logo=""
                    )
                    print(f"✔ Auto-created completely synced company folder: companies/{new_company.id}")
                except Exception as e:
                    print(f"⚠️ Failed to trigger company background folder creation: {e}")

            else:
                existing_user = User.query.filter_by(
                    email=email_input,
                    company_id=active_company_id
                ).first()

                if existing_user:
                    user_obj = existing_user
                else:
                    user_obj = User(
                        email=email_input,
                        username=customer_name or email_input,
                        company_id=active_company_id,
                        role='customer',
                        is_active=True
                    )
                    user_obj.set_password("TemporarySetupPassword123!")
                    db.session.add(user_obj)
                    db.session.flush()

            last_customer = Customer.query.filter_by(company_id=active_company_id)\
                .order_by(Customer.local_id.desc()).first()
            next_local_id = 1 if not last_customer else (last_customer.local_id or 0) + 1

            new_customer = Customer(
                id=user_obj.id,               
                company_id=active_company_id, 
                local_id=next_local_id,  
                date=formatted_date,
                customer_name=customer_name,
                id_number=id_number,
                address=request.form.get('address'),
                city=request.form.get('city'),
                postal_code=request.form.get('postal_code'),
                phone=request.form.get('phone'),
                email=email_input,
                contract_status=request.form.get('contract_status'),
                message=request.form.get('message'),
                role='customer',
                is_active=True
            )

            db.session.add(new_customer)
            db.session.commit()

            active_lang = get_lang()

            try:
                folder = os.path.join(
                    app.config['CUSTOMERS_DIR'],
                    f"{active_company_id}_{next_local_id}"
                )
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                print(f"⚠ Failed to create customer folder: {e}")

            translate_customer_in_background(
                customer_id=new_customer.id,
                company_id=active_company_id,
                name=new_customer.customer_name,
                address=new_customer.address,
                city=new_customer.city,
                message=new_customer.message
            )

            flash('הלקוח נוסף בהצלחה וסונכרן!', 'success')

        return redirect(url_for('customer'))

    # ------------------ GET REQUEST ------------------

    language  = get_lang()
    today_str = datetime.today().strftime('%Y-%m-%d')

    all_customers = Customer.query.filter_by(
        company_id=active_company_id
    ).order_by(Customer.customer_name).all()

    pending_users = User.query.filter_by(
        company_id=active_company_id,
        role='customer'
    ).all()

    existing_customer_emails = {c.email for c in all_customers}

    pending_customers_list = []
    for pu in pending_users:
        if pu.email not in existing_customer_emails:
            pending_customers_list.append(
                Customer(
                    id=pu.id,
                    company_id=active_company_id,
                    local_id=None,   
                    customer_name=pu.username or pu.email,
                    email=pu.email,
                    date=today_str
                )
            )

    all_customers_combined = all_customers + pending_customers_list

    customer_id = request.args.get('customer_id')

    selected_customer = None
    customer_i18n     = {}

    if customer_id:
        c_obj = Customer.query.filter_by(
            id=customer_id,
            company_id=active_company_id
        ).first()

        if not c_obj:
            c_obj = next(
                (c for c in pending_customers_list if str(c.id) == str(customer_id)),
                None
            )

        if c_obj:
            selected_customer = c_obj
            customer_i18n = load_customer_translated(
                c_obj,
                language,
                company_id=active_company_id
            ) or {}

    input_date_val = today_str
    if selected_customer and selected_customer.date:
        input_date_val = selected_customer.date
        if "/" in input_date_val:
            try:
                d_obj = datetime.strptime(input_date_val, '%d/%m/%Y')
                input_date_val = d_obj.strftime('%Y-%m-%d')
            except:
                pass

    # ------------------ GET REQUEST CONTINUATION ------------------
    customer_i18n_list = {}
    for c in all_customers_combined:
        trans = load_customer_translated(c, language, company_id=active_company_id) or {}
        
        final_data = {
            "name": trans.get("name") or c.customer_name or "",
            "address": trans.get("address") or getattr(c, "address", "") or "",
            "city": trans.get("city") or getattr(c, "city", "") or "",
            "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or ""
        }
        
        key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
        customer_i18n_list[key] = final_data

    company_obj = db.session.get(Company, active_company_id) if active_company_id else None

    return render_template(
        'customer.html',
        customer=selected_customer,
        all_customers=all_customers_combined,
        customer_i18n=customer_i18n,
        customer_i18n_list=customer_i18n_list,
        today=today_str,
        input_date_val=input_date_val,
        company=load_company_translated(company_obj, language),
        company_db=company_obj
    )

# -----------------------------------------------------------
#  Secure Individual Customer API Endpoint (Multi-Tenant)
# -----------------------------------------------------------

@app.route('/api/customer/<int:customer_id>')
@login_required
def api_get_customer(customer_id):
    try:
        language = get_lang()

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if customer_id == 0:
            company_obj = db.session.get(Company, active_company_id)
            company_translated = load_company_translated(company_obj, language) if company_obj else {}
            
            if company_obj:
                return jsonify({
                    "id": 0,          
                    "local_id": None,    
                    "customer_name": f"★ {company_translated.get('name', company_obj.name)} ",
                    "address": company_translated.get('address', company_obj.address or ""),
                    "city": company_translated.get('city', company_obj.city or ""),
                    "postal_code": company_obj.postal_code or "",
                    "id_number": company_obj.company_id_number or "",
                    "phone": company_obj.phone or "",
                    "email": company_obj.email or "",
                    "message": "",
                    "date": datetime.today().strftime('%Y-%m-%d'),
                    "status": "active"
                })

        c = Customer.query.filter_by(
            local_id=customer_id,
            company_id=active_company_id
        ).first()

        if not c:
            c = Customer.query.filter_by(
                id=customer_id,
                company_id=active_company_id
            ).first()

        # ------------------ לקוח PENDING ------------------
        if not c:
            u = db.session.get(User, customer_id)

            if u and u.company_id == active_company_id and u.role == 'customer':
                return jsonify({
                    "id": u.id,          
                    "local_id": None,    
                    "customer_name": u.username or u.email,
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

            return jsonify({"error": "Customer not found"}), 404

        # ------------------ לקוח אמיתי ------------------
        formatted_date_for_picker = ""
        if c.date:
            try:
                temp_date = datetime.strptime(c.date, '%d/%m/%Y')
                formatted_date_for_picker = temp_date.strftime('%Y-%m-%d')
            except:
                formatted_date_for_picker = c.date

        trans = load_customer_translated(c, language, company_id=active_company_id) or {}

        return jsonify({
            "id": c.id,               
            "local_id": c.local_id,   
            "customer_name": trans.get("name") or c.customer_name or "",
            "address": trans.get("address") or c.address or "",
            "city": trans.get("city") or c.city or "",            
            "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or "",            
            "id_number": c.id_number or "",
            "phone": c.phone or "",
            "email": c.email or "",
            "message": trans.get("message") or c.message or "",
            "date": formatted_date_for_picker,
            "status": "active"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------
#  Search Customers Engine (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/search_customer', methods=['GET', 'POST'])
@login_required
def search_customer():
    try:
        language = get_lang()  

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        search_name = request.form.get('search_name') if request.method == 'POST' else request.args.get('search_name')

        search_results = []
        customer = None
        
        today_str = datetime.today().strftime('%Y-%m-%d')
        input_date_val = today_str

        all_customers = Customer.query.filter_by(
            company_id=active_company_id
        ).order_by(Customer.customer_name).all()

        if search_name:
            search_name = search_name.strip()

            search_results = Customer.query.filter(
                Customer.company_id == active_company_id,
                Customer.customer_name.ilike(f'%{search_name}%')
            ).all()

            if search_results:
                customer = search_results[0]

                if customer.date:
                    input_date_val = customer.date
                    try:
                        if "/" in input_date_val:
                            d = datetime.strptime(input_date_val, "%d/%m/%Y")
                            input_date_val = d.strftime("%Y-%m-%d")
                    except:
                        input_date_val = today_str
        else:
            search_results = all_customers

        customer_i18n = {}
        if customer:
            trans = load_customer_translated(customer, language, company_id=active_company_id) or {}
            customer_i18n = {
                "name": trans.get("name") or customer.customer_name or "",
                "address": trans.get("address") or customer.address or "",
                "city": trans.get("city") or customer.city or "",
                "message": trans.get("message") or customer.message or ""
            }

        company_obj = db.session.get(Company, active_company_id) if active_company_id else None
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        customer_i18n_list = {}
        
        if company_obj:
            self_name = f"★ {company_translated.get('name', company_obj.name)} "
            customer_i18n_list[0] = {
                "name": self_name,
                "address": company_translated.get("address", company_obj.address or ""),
                "city": company_translated.get("city", company_obj.city or ""),
                "message": "",
                "postal_code": company_obj.postal_code or ""
            }
            customer_i18n_list["0"] = customer_i18n_list[0]
            customer_i18n_list[self_name] = customer_i18n_list[0]

        for c in all_customers:
            trans = load_customer_translated(c, language, company_id=active_company_id) or {}
            
            key_id = c.local_id if c.local_id is not None else c.id
            key_name = c.customer_name
            
            payload = {
                "name": trans.get("name") or c.customer_name or "",
                "address": trans.get("address") or c.address or "",
                "city": trans.get("city") or c.city or "",
                "message": trans.get("message") or c.message or "",                
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or ""
            }
            
            customer_i18n_list[key_id] = payload
            if key_name:
                customer_i18n_list[key_name] = payload

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

    except Exception as e:
        import traceback
        traceback.print_exc()
        return redirect(url_for('customer'))

# -----------------------------------------------------------
#  Clear Customer Search (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/clear_search_results_customer', methods=['POST'])
@login_required
def clear_search_results_customer():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        all_customers = Customer.query.filter_by(
            company_id=active_company_id
        ).order_by(Customer.customer_name).all()

        company_obj = db.session.get(Company, active_company_id) if active_company_id else None
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        customer_i18n_list = {}
        
        if company_obj:
            self_name = f"★ {company_translated.get('name', company_obj.name)} "
            self_payload = {
                "name": self_name,
                "address": company_translated.get("address", company_obj.address or ""),
                "city": company_translated.get("city", company_obj.city or ""),
                "message": "",
                "postal_code": company_obj.postal_code or "",
                "id_number": company_obj.company_id_number or ""
            }
            customer_i18n_list["0"] = self_payload
            customer_i18n_list[self_name] = self_payload

        for c in all_customers:
            try:
                trans = load_customer_translated(c, language, company_id=active_company_id) or {}
            except Exception:
                trans = {}

            key = c.local_id if c.local_id is not None else c.id
            customer_i18n_list[key] = {
                "name": trans.get("name") or c.customer_name or "",
                "address": trans.get("address") or getattr(c, "address", "") or "",
                "city": trans.get("city") or getattr(c, "city", "") or "",
                "message": trans.get("message") or getattr(c, "message", "") or "",                
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or "",                
                "id_number": getattr(c, "id_number", "") or ""
            }

        return render_template(
            'customer.html',
            customers=all_customers,          
            all_customers=all_customers,      
            customer=None,                    
            customer_i18n={},                 
            customer_i18n_list=customer_i18n_list,
            today=today_str,
            input_date_val=today_str,
            company=company_translated,
            company_db=company_obj
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Error in clear_search_results_customer view loop: {e}")
        return redirect(url_for('customer'))



# ----------------------
#   Build All Employees Form (Multi-Tenant Secure)
# ----------------------

@app.route('/employee', methods=['GET', 'POST'])
@login_required
def employee():

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

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

        # ------------------ UPDATE CUSTOMER ------------------
        if employee_id:
            employee_obj = Employee.query.filter_by(
                id=employee_id,
                company_id=active_company_id
            ).first()

            if employee_obj:
                employee_obj.date            = formatted_date
                employee_obj.employee_name   = employee_name
                employee_obj.id_number       = id_number
                employee_obj.address         = request.form.get('address')
                employee_obj.city            = request.form.get('city')
                employee_obj.postal_code     = request.form.get('postal_code')
                employee_obj.phone           = request.form.get('phone')
                employee_obj.email           = email_input
                employee_obj.contract_status = request.form.get('contract_status')
                employee_obj.message         = request.form.get('message')

                db.session.commit()

                translate_employee_in_background(
                    employee_id=employee_obj.id,
                    company_id=active_company_id,
                    name=employee_obj.employee_name,
                    address=employee_obj.address,
                    city=employee_obj.city,
                    message=employee_obj.message
                )

                flash('הנתונים עודכנו בהצלחה!', 'success')

        # ------------------ CREATE NEW EMPLOYEE ------------------
        else:
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
            next_local_id = 1 if not last_employee else last_employee.local_id + 1

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
                phone=request.form.get('phone'),
                email=email_input,
                contract_status=request.form.get('contract_status'),
                message=request.form.get('message'),
                role='employee',
                is_active=True
            )

            db.session.add(new_employee)
            db.session.commit()

            try:
                folder = os.path.join(
                    app.config['EMPLOYEES_DIR'],
                    f"{active_company_id}_{next_local_id}"
                )
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                print(f"⚠ Failed to create employee folder: {e}")

            translate_employee_in_background(
                employee_id=new_employee.id,
                company_id=active_company_id,
                name=new_employee.employee_name,
                address=new_employee.address,
                city=new_employee.city,
                message=new_employee.message
            )

            flash('הלקוח נוסף בהצלחה וסונכרן!', 'success')

        return redirect(url_for('employee'))

    # ------------------ GET REQUEST ------------------
    language  = get_lang()
    today_str = datetime.today().strftime('%Y-%m-%d')

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

    if employee_id:
        c_obj = Employee.query.filter_by(
            id=employee_id,
            company_id=active_company_id
        ).first()

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

    input_date_val = today_str
    if selected_employee and selected_employee.date:
        input_date_val = selected_employee.date
        if "/" in input_date_val:
            try:
                d_obj = datetime.strptime(input_date_val, '%d/%m/%Y')
                input_date_val = d_obj.strftime('%Y-%m-%d')
            except:
                pass

    # ------------------ GET REQUEST CONTINUATION ------------------
    employee_i18n_list = {}
    for c in all_employees_combined:
        trans = load_employee_translated(c, language, company_id=active_company_id) or {}
        
        final_data = {
            "name": trans.get("name") or c.employee_name or "",
            "address": trans.get("address") or getattr(c, "address", "") or "",
            "city": trans.get("city") or getattr(c, "city", "") or "",
            "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or ""
        }
        
        key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
        employee_i18n_list[key] = final_data

    company_obj = db.session.get(Company, active_company_id) if active_company_id else None

    return render_template(
        'employee.html',
        employee=selected_employee,
        all_employees=all_employees_combined,
        employee_i18n=employee_i18n,
        employee_i18n_list=employee_i18n_list,
        today=today_str,
        input_date_val=input_date_val,
        company=load_company_translated(company_obj, language),
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

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

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
                temp_date = datetime.strptime(c.date, '%d/%m/%Y')
                formatted_date_for_picker = temp_date.strftime('%Y-%m-%d')
            except:
                formatted_date_for_picker = c.date

        trans = load_employee_translated(c, language, company_id=active_company_id) or {}

        return jsonify({
            "id": c.id,               
            "local_id": c.local_id,   
            "employee_name": trans.get("name") or c.employee_name or "",
            "address": trans.get("address") or c.address or "",
            "city": trans.get("city") or c.city or "",            
            "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or "",            
            "id_number": c.id_number or "",
            "phone": c.phone or "",
            "email": c.email or "",
            "message": trans.get("message") or c.message or "",
            "date": formatted_date_for_picker,
            "status": "active"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -----------------------------------------------------------
#  Search Employees Engine (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/search_employee', methods=['GET', 'POST'])
@login_required
def search_employee():
    try:
        language = get_lang()  

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        search_name = request.form.get('search_name') if request.method == 'POST' else request.args.get('search_name')

        search_results = []
        employee = None
        
        today_str = datetime.today().strftime('%Y-%m-%d')
        input_date_val = today_str

        all_employees = Employee.query.filter_by(
            company_id=active_company_id
        ).order_by(Employee.employee_name).all()

        if search_name:
            search_name = search_name.strip()

            search_results = Employee.query.filter(
                Employee.company_id == active_company_id,
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
            trans = load_employee_translated(employee, language, company_id=active_company_id) or {}
            employee_i18n = {
                "name": trans.get("name") or employee.employee_name or "",
                "address": trans.get("address") or employee.address or "",
                "city": trans.get("city") or employee.city or "",
                "message": trans.get("message") or employee.message or ""
            }

        employee_i18n_list = {}
        for c in all_employees:
            trans = load_employee_translated(c, language, company_id=active_company_id) or {}
            
            payload = {
                "name": trans.get("name") or c.employee_name or "",
                "address": trans.get("address") or c.address or "",
                "city": trans.get("city") or c.city or "",
                "message": trans.get("message") or c.message or "",                
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or ""
            }
            
            key = str(c.local_id) if getattr(c, "local_id", None) else f"user_{c.id}"
            employee_i18n_list[key] = payload

        company_obj = db.session.get(Company, active_company_id) if active_company_id else None

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
            company=load_company_translated(company_obj, language),
            company_db=company_obj
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return redirect(url_for('employee'))

# -----------------------------------------------------------
#  Clear Employee Search (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/clear_search_results_employee', methods=['POST'])
@login_required
def clear_search_results_employee():
    try:
        language = get_lang()
        today_str = datetime.today().strftime('%Y-%m-%d')

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

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
                "id_number": getattr(c, "id_number", "") or ""
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
            company=load_company_translated(company_obj, language),
            company_db=company_obj
        )

    except Exception as e:
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

            supplier_id = request.form.get('supplier_id')
            supplier_number = request.form.get('supplier_number', '').strip()
            supplier_name = request.form.get('supplier_name', '').strip()
            email_input = request.form.get('email', '').strip().lower()

            # ------------------ UPDATE SUPPLIER ------------------
            if supplier_id:
                supplier_obj = Supplier.query.filter_by(id=supplier_id, company_id=active_company_id).first()
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

                    translate_supplier_in_background(
                        supplier_id=supplier_obj.id,
                        company_id=active_company_id,
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
                    (Supplier.company_id == active_company_id) &
                    ((Supplier.email == email_input) | (Supplier.supplier_name == supplier_name))
                ).first()

                if duplicate:
                    flash("ספק עם אימייל זה או שם זה כבר קיים בחברה שלך", "warning")
                    return redirect(url_for('supplier'))

                if supplier_number and Supplier.query.filter_by(
                    supplier_number=supplier_number, 
                    company_id=active_company_id
                ).first():
                    flash("קיים כבר ספק עם מספר ספק זה במערכת שלך", "error")
                    return redirect(url_for('supplier'))

                last_supplier = Supplier.query.filter_by(company_id=active_company_id)\
                    .order_by(Supplier.local_id.desc()).first()
                next_local_id = 1 if not last_supplier else last_supplier.local_id + 1

                new_supplier = Supplier(
                    company_id=active_company_id,
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

                try:
                    folder = os.path.join(
                        app.config['SUPPLIERS_DIR'],
                        f"{active_company_id}_{next_local_id}"
                    )
                    os.makedirs(folder, exist_ok=True)
                except Exception as e:
                    print(f"⚠ Failed to create supplier folder: {e}")

                translate_supplier_in_background(
                    supplier_id=new_supplier.id,
                    company_id=active_company_id,
                    name=new_supplier.supplier_name,
                    address=new_supplier.address,
                    city=new_supplier.city,
                    postal_code=new_supplier.postal_code,
                    notes=new_supplier.notes
                )

                flash('הספק נוסף בהצלחה!', 'success')

            return redirect(url_for('supplier'))

        # ------------------ GET ------------------
        all_suppliers = Supplier.query.filter_by(company_id=active_company_id).order_by(Supplier.supplier_name).all()
        supplier_id = request.args.get('supplier_id')

        selected_supplier = None
        supplier_i18n = {}

        if supplier_id:
            s_obj = Supplier.query.filter_by(id=supplier_id, company_id=active_company_id).first()
            if s_obj:
                selected_supplier = s_obj
                supplier_i18n = load_supplier_translated(s_obj, language, company_id=active_company_id) or {}

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
            trans = load_supplier_translated(s, language, company_id=active_company_id) or {"name": s.supplier_name}
            key = s.local_id if getattr(s, "local_id", None) else s.id
            supplier_i18n_list[key] = trans

        company_obj = db.session.get(Company, active_company_id) if active_company_id else None

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
        return redirect(url_for('invoice'))


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

        s = Supplier.query.filter(
            ((Supplier.id == supplier_id) | (Supplier.local_id == supplier_id)),
            Supplier.company_id == active_company_id
        ).first()

        if not s:
            u = db.session.get(User, supplier_id)

            if u and u.company_id == active_company_id and u.role == 'supplier':
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

        trans = load_supplier_translated(s, language, company_id=active_company_id) or {}

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

        search_name = request.form.get('search_supplier') if request.method == 'POST' else request.args.get('search_supplier')

        search_results = []
        supplier = None
        
        today_str = datetime.today().strftime('%Y-%m-%d')
        input_date_val = today_str

        if search_name:
            search_name = search_name.strip()

            search_results = Supplier.query.filter(
                Supplier.company_id == active_company_id,
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
            company_id=active_company_id
        ).order_by(Supplier.supplier_name).all()

        supplier_i18n = {}
        if supplier:
            trans = load_supplier_translated(supplier, language, company_id=active_company_id) or {}
            supplier_i18n = {
                "name": trans.get("name", supplier.supplier_name),
                "address": trans.get("address", supplier.address),
                "city": trans.get("city", supplier.city),
                "postal_code": trans.get("postal_code", supplier.postal_code),
                "notes": trans.get("notes", supplier.notes)
            }

        supplier_i18n_list = {}
        for s in all_suppliers:
            trans = load_supplier_translated(s, language, company_id=active_company_id) or {}
            key = s.local_id if getattr(s, "local_id", None) else s.id
            supplier_i18n_list[key] = {
                "name": trans.get("name", s.supplier_name),
                "address": trans.get("address", s.address),
                "city": trans.get("city", s.city),
                "postal_code": trans.get("postal_code", s.postal_code),
                "notes": trans.get("notes", s.notes)
            }

        company_obj = db.session.get(Company, active_company_id)

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

        all_suppliers = Supplier.query.filter_by(
            company_id=active_company_id
        ).order_by(Supplier.supplier_name).all()

        supplier_i18n_list = {}
        for s in all_suppliers:
            try:
                trans = load_supplier_translated(s, language, company_id=active_company_id) or {}
            except Exception:
                trans = {}

            key = s.local_id if getattr(s, "local_id", None) else s.id
            supplier_i18n_list[key] = {
                "name": trans.get("name", s.supplier_name),
                "address": trans.get("address", s.address),
                "city": trans.get("city", s.city),
                "postal_code": trans.get("postal_code", s.postal_code),
                "notes": trans.get("notes", s.notes)
            }

        company_obj = db.session.get(Company, active_company_id) if active_company_id else None

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
        return redirect(url_for('invoice'))


# ----------------------
# All Transaction Route (Multi-Tenant Secure)
# ----------------------

@app.route('/transactions')
@login_required
def transactions():
    try:
        language = get_lang()

        search = request.args.get("q", "").strip().lower()
        selected_month = request.args.get("month", "")
        selected_year = request.args.get("year", "")

        if not selected_year:
            selected_year = str(datetime.today().year)
        if not selected_month:
            selected_month = datetime.today().strftime('%m')

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        db.session.expire_all()

        all_transactions = (
            Transaction.query
            .filter_by(company_id=active_company_id)
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
            db_categories = Category.query.filter_by(company_id=active_company_id).all()
            for cat in db_categories:
                # פיקס בטוח: מעבירים את מזהה הקטגוריה לפונקציית הטעינה
                cat_id_param = cat.id if hasattr(cat, 'id') else cat
                data = load_category_file(cat_id_param)
                
                if data and "name" in data:
                    name_obj = data["name"]
                    translated_name = (
                        name_obj.get(language)
                        or name_obj.get("he")
                        or getattr(cat, 'name', 'Unknown')
                    )
                else:
                    translated_name = getattr(cat, 'name', 'Unknown')
                
                if getattr(cat, 'local_id', None):
                    business_categories[str(cat.local_id)] = translated_name
                if getattr(cat, 'id', None):
                    business_categories[str(cat.id)] = translated_name

        except Exception as e:
            print(f"⚠️ Warning: Safe database-driven category layout mapping bypassed: {e}")

        company_obj = db.session.get(Company, active_company_id)
        translated_company = load_company_translated(company_obj, language) if company_obj else {}

        customer_i18n_list = {}
        if company_obj:
            self_name = f"★ {translated_company.get('name', company_obj.name)} "
            customer_i18n_list["0"] = {"name": self_name}

        all_customers = Customer.query.filter_by(company_id=active_company_id).all()
        for c in all_customers:
            trans_c = load_customer_translated(c, language, company_id=active_company_id) or {}
            customer_i18n_list[c.id] = {"name": trans_c.get("name") or c.customer_name or ""}
            if c.local_id is not None:
                customer_i18n_list[c.local_id] = {"name": trans_c.get("name") or c.customer_name or ""}

        filtered_transactions = []
        trans_i18n_list = {}
        costs_at_time = {}
        vat_amounts_list = {}  

        is_numeric_search = search.replace(".", "", 1).isdigit()

        for t in all_transactions:
            trans_file = load_transaction_file(t)
            desc_obj = trans_file.get("description", {}) if trans_file else {}
            
            #  הצלבה מדויקת לפי מספר חשבונית פנימי ומזהה חברה אקטיבית למניעת סלטים מה-Database!
            if desc_obj.get(language):
                translated_desc = desc_obj.get(language)
            elif desc_obj.get("he"):
                translated_desc = desc_obj.get("he")
            elif getattr(t, 'invoice_id', None):
                invoice_core = Invoice.query.filter_by(
                    invoice_number=t.invoice_id,
                    company_id=active_company_id
                ).first()
                if invoice_core:
                    translated_desc = f"חשבונית #{invoice_core.invoice_number}"
                else:
                    translated_desc = f"חשבונית #{t.invoice_id}"
            else:
                translated_desc = t.description or ""

            t_month = t.date.strftime('%m')
            t_year = str(t.date.year)

            match_month = not selected_month or t_month == selected_month
            match_year = not selected_year or t_year == selected_year

            if not search:
                match_search = True
            else:
                raw_desc = (t.description or "").lower()
                trans_desc_lower = translated_desc.lower()
                
                cat_name = business_categories.get(str(t.category_id), "").lower()
                
                cust_mapped = customer_i18n_list.get(t.customer_id) or customer_i18n_list.get(str(t.customer_id)) or {}
                cust_mapped_name = (cust_mapped.get("name") or "").lower()
                
                amount_str = str(t.amount)
                date_str = t.date.strftime("%d/%m/%Y")

                if is_numeric_search:
                    match_search = (search == amount_str)
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
                
                if getattr(t, 'invoice_id', None):
                    invoice_obj = Invoice.query.filter_by(
                        invoice_number=t.invoice_id, 
                        company_id=active_company_id
                    ).first()
                    
                    if invoice_obj and invoice_obj.status == "canceled":
                        #  אם מבוטלת - עמודה 5 הופכת ל-0, עמודה 7 הופכת ל-0, והמע"מ הופך ל-0!
                        current_amount = 0.0
                        current_cost_price = 0.0
                        vat_amounts_list[t.id] = 0.0
                    elif invoice_obj and getattr(invoice_obj, 'vat_amount', None) is not None:
                        vat_amounts_list[t.id] = float(invoice_obj.vat_amount)
                    else:
                        vat_amounts_list[t.id] = float(getattr(t, 'vat_amount', 0.0) or 0.0)
                else:
                    vat_amounts_list[t.id] = float(getattr(t, 'vat_amount', 0.0) or 0.0)

                # ננעל בהצלחה בנפרד לכל שורה
                costs_at_time[t.id] = current_cost_price
                
                # מעדכנים את אובייקט התנועה הזמני בלייב עבור הרינדור ב-Jinja של עמודה 5
                t.amount = current_amount

        months_list = ["01","02","03","04","05","06","07","08","09","10","11","12"]
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
        print(f"❌ Critical Exception caught inside transactions index route handler: {e}")
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
        trans_type = request.form.get('type')  # 'income' or 'expense'

        raw_invoice_id = request.form.get('invoice_id')
        future_invoice_id = int(raw_invoice_id) if raw_invoice_id and str(raw_invoice_id).isdigit() else None

        if future_invoice_id:
            return redirect(url_for('transactions'))
            
        if trans_type == 'expense' and ("חשבונית" in description or "אשראי" in description or "סליקה" in description):
            return redirect(url_for('transactions'))

        raw_cat = request.form.get('category')
        category_id = int(raw_cat) if raw_cat and str(raw_cat).isdigit() else None
        
        cost_price = 0.0
        income_cat = 'service'

        raw_customer_id = request.form.get('customer_id')
        final_customer_id = "0" if str(raw_customer_id).strip() == "0" else (int(raw_customer_id) if raw_customer_id and str(raw_customer_id).isdigit() else None)

        if trans_type == 'income':
            p_id = request.form.get('product_id')
            if p_id:
                if p_id in ['rent', 'stocks', 'dividend', 'unspecified']:
                    income_cat = p_id
                    cost_price = 0.0  
                else:
                    product = Product.query.filter_by(local_id=p_id, company_id=active_company_id).first()
                    if not product:
                        product = Product.query.filter_by(id=p_id, company_id=active_company_id).first()
                        
                    if product:
                        item_file = load_item_file(product.id, company_id=active_company_id) or {}
                        income_cat = item_file.get("income_category", getattr(product, 'income_category', 'service'))
                        
                        if income_cat == 'product':
                            cost_price = float(item_file.get("cost_price", product.cost_price or 0.0))
        
        relative_db_pointer = None
        if 'attachment' in request.files:
            file = request.files['attachment']
            if file and file.filename != '':
                filename = secure_filename(f"{int(time.time())}_{file.filename}")
                base_upload_dir = Path(app.config["UPLOAD_FOLDER"])
                company_upload_dir = base_upload_dir / f"company_{active_company_id}"
                company_upload_dir.mkdir(parents=True, exist_ok=True)
                absolute_save_target = company_upload_dir / filename
                relative_db_pointer = f"company_{active_company_id}/{filename}"
                file.save(str(absolute_save_target))

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
        db.session.add(new_trans)
        
        db.session.commit()

        saved_transaction_id = new_trans.id

        current_curr = get_currency() if 'get_currency' in globals() else 'ILS'
        try:
            translate_transaction_in_background(
                transaction_id=saved_transaction_id, # משתמשים במזהה השמור הבטוח
                company_id=active_company_id,   
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
        
        db.session.expire_all()

        transactions = (
            Transaction.query
            .filter_by(company_id=active_company_id)
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
            db_categories = Category.query.filter_by(company_id=active_company_id).all()
            for cat in db_categories:
                # פיקס בטוח: מעבירים את מזהה הקטגוריה המפורש לפונקציית הטעינה
                cat_id_param = cat.id if hasattr(cat, 'id') else cat
                data = load_category_file(cat_id_param)

                if data and "name" in data:
                    names = data.get("name", {})
                    translated_name = (
                        names.get(language) or 
                        names.get('he') or 
                        getattr(cat, 'name', 'Unknown') or 
                        f"Cat {getattr(cat, 'local_id', cat_id_param)}"
                    )
                else:
                    translated_name = getattr(cat, 'name', 'Unknown') or f"Cat {getattr(cat, 'local_id', cat_id_param)}"
                
                if getattr(cat, 'local_id', None):
                    cat_map[str(cat.local_id)] = translated_name
                if getattr(cat, 'id', None):
                    cat_map[str(cat.id)] = translated_name

        except Exception:
            pass

        company_obj = db.session.get(Company, active_company_id)
        translated_company = load_company_translated(company_obj, language) if company_obj else {}
        
        customer_map = {}
        if company_obj:
            customer_map["0"] = f"★ {translated_company.get('name', company_obj.name)} "

        all_customers = Customer.query.filter_by(company_id=active_company_id).all()
        for c in all_customers:
            trans_c = load_customer_translated(c, language, company_id=active_company_id) or {}
            c_name = trans_c.get("name") or c.customer_name or f"Customer {c.local_id}"
            customer_map[str(c.id)] = c_name
            if c.local_id is not None:
                customer_map[str(c.local_id)] = c_name

        result = []
        for t in transactions:
            trans_file = load_transaction_file(t)
            
            if trans_file and "description" in trans_file:
                desc_dict = trans_file["description"]
            else:
                desc_dict = {"he": t.description or ""}
            
            if isinstance(desc_dict, dict):
                # אם אין תרגום, מצליבים לפי מספר חשבונית פנימי ומזהה חברה אקטיבית כדי להציג את מספר 1 הנכון למסך!
                if desc_dict.get(language):
                    p_desc = desc_dict.get(language)
                elif desc_dict.get("he"):
                    p_desc = desc_dict.get("he")
                elif getattr(t, 'invoice_id', None):
                    invoice_core = Invoice.query.filter_by(
                        invoice_number=t.invoice_id,
                        company_id=active_company_id
                    ).first()
                    p_desc = f"חשבונית #{invoice_core.invoice_number}" if invoice_core else f"חשבונית #{t.invoice_id}"
                else:
                    p_desc = t.description or ""
            else:
                p_desc = t.description or ""
            
            cat_id_str = str(t.category_id) if t.category_id else None
            
            if cat_id_str and cat_id_str in cat_map:
                translated_cat = cat_map[cat_id_str]
            else:
                translated_cat = getattr(t, 'category', 'General') or 'General'
            
            current_amount = float(t.amount or 0.0)
            current_cost_price = float(getattr(t, 'cost_price_at_time', 0.0) or 0.0)
            vat_amount_val = getattr(t, 'vat_amount', 0.0)
            final_vat = float(vat_amount_val if vat_amount_val is not None else 0.0)

            if getattr(t, 'invoice_id', None):
                # API: מאפס את כל הנתונים, הסכומים והמע"מ ל-0 ברגע שהחשבונית מבוטלת!
                invoice_obj = Invoice.query.filter_by(
                    invoice_number=t.invoice_id, 
                    company_id=active_company_id
                ).first()
                
                if invoice_obj and invoice_obj.status == "canceled":
                    current_amount = 0.0      
                    final_vat = 0.0           
                    current_cost_price = 0.0  
                elif invoice_obj and getattr(invoice_obj, 'vat_amount', None) is not None:
                    final_vat = float(invoice_obj.vat_amount)

            t_cust_id_str = str(t.customer_id) if t.customer_id else None
            resolved_cust_name = customer_map.get(t_cust_id_str, "") if t_cust_id_str else ""

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
                "attachment": t.attachment_path or "",
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
#  Delete Transaction Endpoint (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/transaction/delete/<int:id>', methods=['POST'])
@login_required
def delete_transaction(id):
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

    db.session.expire_all()

    trans = Transaction.query.filter_by(id=id, company_id=active_company_id).first()
    
    if trans:
        #  חוסם מחיקה אך ורק אם התנועה היא פיזית תנועת הכנסה מסוג חשבונית השייכת לחברה!
        if getattr(trans, 'invoice_id', None) and trans.type == 'income' and "חשבונית" in (trans.description or ""):
            invoice_exists = Invoice.query.filter_by(
                invoice_number=trans.invoice_id, 
                company_id=active_company_id
            ).first()
            
            if invoice_exists:
                flash('לא ניתן למחוק תנועה הקשורה לחשבונית. יש למחוק או לבטל את החשבונית עצמה.', 'danger')
                return redirect(request.referrer or url_for('transactions'))

        if trans.attachment_path:
            base_upload_dir = app.config.get('UPLOAD_FOLDER', '')
            
            if "company_" in str(trans.attachment_path):
                file_path = os.path.join(base_upload_dir, trans.attachment_path)
            else:
                file_path = os.path.join(base_upload_dir, f"company_{active_company_id}", trans.attachment_path)
            
            if os.path.exists(file_path) and os.path.isfile(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass

        current_local_id = trans.local_id

        db.session.delete(trans)
        db.session.commit()

        if current_local_id:
            try:
                if 'ensure_transaction_folder' in globals():
                    trans_folder_path = ensure_transaction_folder(active_company_id, current_local_id)
                else:
                    base_trans_dir = app.config.get("TRANSACTIONS_DIR") or os.path.join(app.config.get("UPLOAD_FOLDER", ""), "transactions")
                    trans_folder_path = os.path.join(base_trans_dir, f"{active_company_id}_{current_local_id}")

                if trans_folder_path and os.path.exists(trans_folder_path) and os.path.isdir(trans_folder_path):
                    import shutil
                    shutil.rmtree(trans_folder_path)
            except Exception:
                pass
        
        db.session.close()
        flash('התנועה וכל הקבצים הקשורים אליה נמחקו בהצלחה', 'success')
    else:
        flash('התנועה לאמצאה במערכת שלך', 'warning')
        
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
        
        # שליפת כל הקטגוריות השייכות לחברה הנוכחית
        db_categories = Category.query.filter_by(company_id=active_company_id).all()
        all_categories = []
        
        for cat in db_categories:
            # פיקס בטוח: העברת מזהה הקטגוריה המפורש לפונקציית הטעינה כדי למנוע קריסת שרת
            cat_id_param = cat.id if hasattr(cat, 'id') else cat
            data = load_category_file(cat_id_param)

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

        company_obj = db.session.get(Company, active_company_id)

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

    # חישוב ה-local_id הרץ הבא עבור קטגוריות החברה הנוכחית
    max_local = db.session.query(db.func.max(Category.local_id))\
        .filter(Category.company_id == active_company_id).scalar()
    
    next_local_id = (max_local or 0) + 1

    # יצירת הקטגוריה החדשה בדאטהבייס
    new_cat = Category(
        name=category_name,
        company_id=active_company_id,
        local_id=next_local_id 
    )
    db.session.add(new_cat)
    db.session.commit()

    # יצירת התיקייה הפיזית לקובצי ה-JSON של הקטגוריה
    try:
        ensure_category_folder(active_company_id, next_local_id)
    except Exception as folder_err:
        print(f"⚠️ Non-critical category folder setup warning: {folder_err}")

    # הפעלת תהליך התרגום ברקע
    try:
        translate_category_in_background(
            cat_id=new_cat.id,
            company_id=active_company_id,
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

        # שליפת הקטגוריה תוך וידוא קשיח שהיא שייכת לחברה הנוכחית
        cat = Category.query.filter_by(id=cat_id, company_id=active_company_id).first()
        
        if cat:
            current_local_id = cat.local_id

            # ניתוק תנועות קיימות המשויכות לקטגוריה זו כדי למנוע קריסות של דפים ודוחות
            # אנחנו מעדכנים את ה-category_id ל-None (Null) עבור כל התנועות של החברה שקשורות לקטגוריה הנמחקת
            db.session.query(Transaction).filter(
                Transaction.category_id == cat.id,
                Transaction.company_id == active_company_id
            ).update({Transaction.category_id: None}, synchronize_session=False)

            # מחיקת השורה של הקטגוריה מטבלת Category בדאטהבייס
            db.session.delete(cat)
            db.session.commit()

            # מחיקה פיזית של תיקיית קבצי ה-JSON של הקטגוריה מהשרת (מניעת זבל בדיסק)
            if current_local_id:
                try:
                    # שימוש בפונקציית העזר לקבלת נתיב התיקייה המדויק
                    cat_path = ensure_category_folder(active_company_id, current_local_id)
                    
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
        
        # שליפת כל הקטגוריות של החברה הנוכחית
        db_categories = Category.query.filter_by(company_id=active_company_id).all()
        result = []
        
        for cat in db_categories:
            # פיקס בטוח: העברת מזהה הקטגוריה המפורש לפונקציית הטעינה כדי למנוע קריסת הצינור
            cat_id_param = cat.id if hasattr(cat, 'id') else cat
            data = load_category_file(cat_id_param)

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

        all_suppliers = Supplier.query.filter_by(company_id=active_company_id).order_by(Supplier.supplier_name).all()

        company_obj = db.session.get(Company, active_company_id) if active_company_id else None
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

        data = request.get_json() or {}

        supplier_data = data.get("supplier_payment", {})
        invoice_data = data.get("invoice_payment", {})
        authority_data = data.get("authority_payment", {})

        if supplier_data:
            req_supplier_id = supplier_data.get("supplier_id")
            
            checked_supplier = Supplier.query.filter_by(id=req_supplier_id, company_id=active_company_id).first()
            if not checked_supplier:
                return jsonify({"status": "error", "message": "Unauthorized or missing vendor profile token"}), 403

            save_supplier_payment(
                supplier_id=checked_supplier.id,
                supplier_name=supplier_data.get("supplier_name"),
                supplier_number=supplier_data.get("supplier_number"),
                amount=supplier_data.get("supplier_amount"),
                description=supplier_data.get("supplier_description"),
                reference=supplier_data.get("supplier_reference"),
                company_id=active_company_id
            )

        if invoice_data:
            req_invoice_num = invoice_data.get("invoice_number")
            
            checked_invoice = Invoice.query.filter_by(invoice_number=req_invoice_num, company_id=active_company_id).first()
            if not checked_invoice:
                return jsonify({"status": "error", "message": "Unauthorized or missing document sequence mapping reference"}), 403

            save_invoice_payment(
                invoice_number=checked_invoice.invoice_number,
                customer=invoice_data.get("invoice_customer"),
                amount=invoice_data.get("invoice_amount"),
                description=invoice_data.get("invoice_description"),
                company_id=active_company_id
            )

        if authority_data:
            authority_data["company_id"] = active_company_id
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

        data = request.json or {}

        supplier_id = data.get("supplier_id")
        product_id = data.get("product_id")
        quantity = float(data.get("quantity", 0) or 0)
        cost_price = float(data.get("cost_price", 0) or 0)
        reference = data.get("reference", "")
        notes = data.get("notes", "")
        date = datetime.today().strftime("%Y-%m-%d")   

        checked_supplier = Supplier.query.filter_by(id=supplier_id, company_id=active_company_id).first()
        product = Product.query.filter_by(id=product_id, company_id=active_company_id).first()

        if not checked_supplier or not product:
            return jsonify({"status": "error", "message": "Unauthorized or invalid inventory parameters selection"}), 403

        total = quantity * cost_price

        purchase = SupplierPurchase(
            company_id=active_company_id,   
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

        product.quantity += quantity
        product.cost_price = cost_price

        db.session.commit()
        return jsonify({"status": "success"})
        
    except Exception as e:
        db.session.rollback()
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

        suppliers = Supplier.query.filter_by(company_id=active_company_id).order_by(Supplier.supplier_name).all()

        return jsonify([
            {"id": s.id, "supplier_name": s.supplier_name}
            for s in suppliers
        ])
    except Exception as e:
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
        selected_month = request.args.get("month", "")
        selected_year = request.args.get("year", "")

        if not selected_year: 
            selected_year = str(datetime.today().year)
        if not selected_month: 
            selected_month = datetime.today().strftime('%m')

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None)

        if not active_company_id:
            return "<h1>Access Denied: Unverified Workspace</h1>", 401

        db.session.expire_all()

        # שליפת כל הלקוחות כולל טעינה מוקדמת של החשבוניות והפריטים למניעת עומס
        all_customers = Customer.query.filter_by(company_id=active_company_id).options(
            db.joinedload(Customer.invoices).joinedload(Invoice.items)
        ).all()
        
        all_transactions = (
            Transaction.query
            .filter_by(company_id=active_company_id)
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

        # מיפוי מהיר של המוצרים (תומך גם ב-local_id וגם ב-id גלובלי ליתר ביטחון)
        all_products_dict = {}
        for p in Product.query.filter_by(company_id=active_company_id).all():
            if p.local_id is not None:
                all_products_dict[str(p.local_id)] = p
            all_products_dict[str(p.id)] = p

        # מיפוי קטגוריות מתורגמות - כולל הפיקס המאובטח למניעת קריסות קבצים
        business_categories = {}
        try:
            db_categories = Category.query.filter_by(company_id=active_company_id).all()
            for cat in db_categories:
                cat_id_param = cat.id if hasattr(cat, 'id') else cat
                data = load_category_file(cat_id_param)
                if data and "name" in data:
                    name_obj = data["name"]
                    translated_name = (
                        name_obj.get(language) or 
                        name_obj.get("he") or 
                        getattr(cat, 'name', 'Unknown') or 
                        f"Cat {getattr(cat, 'local_id', cat_id_param)}"
                    )
                else:
                    translated_name = getattr(cat, 'name', 'Unknown') or f"Cat {getattr(cat, 'local_id', cat_id_param)}"

                if getattr(cat, 'local_id', None):
                    business_categories[str(cat.local_id)] = translated_name
                if getattr(cat, 'id', None):
                    business_categories[str(cat.id)] = translated_name
        except Exception as e:
            print(f"⚠️ Warning: Safe database-driven category loading skipped in Profit: {e}")

        total_revenue = 0.0
        total_expenses = 0.0
        total_cogs = 0.0 
        total_manual_income = 0.0  
        
        total_invoice_vat = 0.0  # מע"מ עסקאות
        total_expense_vat = 0.0  # מע"מ תשומות
        
        customer_totals = {}
        customer_i18n_list = {}
        product_i18n_list = {}  
        filtered_customers = []
        
        manual_incomes_list = []
        expenses_list = []
        trans_i18n_list = {}

        company_obj = db.session.get(Company, active_company_id)
        translated_company = load_company_translated(company_obj, language) if company_obj else {}

        # ------------------ PROCESS INVOICES (REGULAR CUSTOMERS) ------------------
        for customer in all_customers:
            customer_i18n_list[customer.id] = load_customer_translated(customer, language, company_id=active_company_id)
            cust_revenue = 0.0
            
            for inv in customer.invoices:
                if inv.status == "canceled":
                    continue
                    
                inv_month = inv.invoice_date.strftime('%m')
                inv_year = str(inv.invoice_date.year)
                
                if inv_month == selected_month and inv_year == selected_year:
                    cust_revenue += float(inv.sub_total or 0.0)
                    total_invoice_vat += float(inv.vat_amount or 0.0)

                    # חישוב דינמי ומדויק של עלות המכר מתוך ערכי ה-cost_price_at_time שננעלו
                    for item in inv.items:
                        prod_obj = all_products_dict.get(str(item.product_id)) if item.product_id else None
                        
                        if prod_obj and str(item.product_id) not in product_i18n_list:
                            product_i18n_list[str(item.product_id)] = load_item_translated(prod_obj, language)

                        item_cost = float(getattr(item, 'cost_price_at_time', 0.0) or 0.0)

                        # גיבוי קשיח במידה ומדובר במוצר ישן שבו ה-cost_price_at_time עדיין ריק
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

        # ------------------ PROCESS SELF INVOICES (CUSTOMER ID 0) ------------------
        self_invoices = Invoice.query.filter_by(company_id=active_company_id, customer_id="0").all()
        self_revenue = 0.0

        for inv in self_invoices:
            if inv.status == "canceled":
                continue
                
            inv_month = inv.invoice_date.strftime('%m')
            inv_year = str(inv.invoice_date.year)
            
            if inv_month == selected_month and inv_year == selected_year:
                self_revenue += float(inv.sub_total or 0.0)
                total_invoice_vat += float(inv.vat_amount or 0.0)
                
                # לקטגוריות הפנימיות האלו אין ניהול מלאי או עלות מכר, ולכן ה-COGS שלהן הוא תמיד 0
                for item in inv.items:
                    total_cogs += 0.0

        if self_revenue > 0.0 or not search:
            self_title = f"★ {translated_company.get('name', company_obj.name if company_obj else '')} "
            customer_totals["0"] = self_revenue
            total_revenue += self_revenue
            customer_i18n_list["0"] = {"name": self_title}
            
            class VirtualSelfCustomer:
                id = "0"
                customer_name = self_title
                invoices = [inv for inv in self_invoices if inv.invoice_date.strftime('%m') == selected_month and str(inv.invoice_date.year) == selected_year]
            
            if self_revenue > 0.0 or (not search and self_invoices):
                filtered_customers.append(VirtualSelfCustomer())

        # ==================== מיפוי שמות הפריטים עבור הטבלאות בדף התצוגה HTML ====================
        item_names_map = {}
        for customer in all_customers:
            for inv in customer.invoices:
                if inv.status == "canceled":
                    continue
                for item in inv.items:
                    prod_obj = Product.query.filter_by(local_id=item.product_id, company_id=active_company_id).first()
                    if not prod_obj:
                        prod_obj = Product.query.filter_by(id=item.product_id, company_id=active_company_id).first()
                    
                    if prod_obj:
                        trans_p = load_item_translated(prod_obj, language) or {}
                        # חילוץ מחרוזת שטוחה ונקייה מתוך מילון השפות למניעת הדפסת JSON גולמי בטבלה
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
        for trans in all_transactions:
            trans_month = trans.date.strftime('%m')
            trans_year = str(trans.date.year)

            if trans_month == selected_month and trans_year == selected_year:
                t_file = load_transaction_file(trans)
                desc_obj = t_file.get("description", {}) if t_file else {}
                trans_i18n_list[trans.id] = (
                    desc_obj.get(language) or 
                    desc_obj.get("he") or 
                    trans.description
                )

                # חישוב ונטרול מע"מ עבור תנועות הקשורות לחשבוניות (כולל בדיקת מבוטלות)
                current_trans_vat = 0.0
                if getattr(trans, 'invoice_id', None):
                    invoice_obj = db.session.get(Invoice, trans.invoice_id)
                    if invoice_obj and invoice_obj.company_id == active_company_id and invoice_obj.status != "canceled" and getattr(invoice_obj, 'vat_amount', None) is not None:
                        current_trans_vat = float(invoice_obj.vat_amount)
                    else:
                        current_trans_vat = 0.0 if (invoice_obj and invoice_obj.status == "canceled") else float(getattr(trans, 'vat_amount', 0.0) or 0.0)
                else:
                    vat_val = getattr(trans, 'vat_amount', 0.0)
                    current_trans_vat = float(vat_val if vat_val is not None else 0.0)

                # סיווג הוצאות העסק (Expenses)
                if trans.type == 'expense':
                    total_expenses += float(trans.amount or 0.0)
                    total_expense_vat += current_trans_vat
                    expenses_list.append(trans)
                    
                # סיווג הכנסות ידניות (Incomes) שלא הופקו דרך מודול החשבוניות הראשי
                elif trans.type == 'income':
                    if not trans.invoice_id:
                        total_manual_income += float(trans.amount or 0.0)
                        total_revenue += float(trans.amount or 0.0)
                        total_cogs += float(getattr(trans, 'cost_price_at_time', 0.0) or 0.0)
                        total_invoice_vat += current_trans_vat
                        manual_incomes_list.append(trans)

        # חישוב השורה התחתונה: רווח נקי וסך הכל מע"מ לתשלום/החזר
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
    base_dir = app.config.get("EMPLOYEES_DIR", "static/employees")
    month_str = str(month).zfill(2)
    year_str = str(year)
    
    final_emp_id = str(employee_id)
    
    if employee_id and str(employee_id).isdigit():
        emp_row = Employee.query.filter_by(id=int(employee_id), company_id=company_id).first()
        if not emp_row:
            emp_row = Employee.query.filter_by(local_id=int(employee_id), company_id=company_id).first()
            
        if emp_row and emp_row.local_id:
            final_emp_id = str(emp_row.local_id) 

    target_folder = os.path.join(
        base_dir, 
        f"company_{company_id}",
        f"employee_{final_emp_id}",  
        year_str, 
        month_str
    )
    
    json_path = os.path.join(target_folder, 'clock_hours_data.json')
    csv_path = os.path.join(target_folder, 'hours_report.csv')
    
    return target_folder, json_path, csv_path


def load_clock_hours(company_id, employee_id, year, month):
  if not company_id or not employee_id:
    return {}
      
  _, json_file_path, _ = get_company_clock_paths(company_id, employee_id, year, month)
  
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
            for row in reader:
                if row.get('employee_id') == str(employee_id):
                    return row
    except Exception:
        pass

    return empty_structure


def timesheet_entry_exists(employee_id, selected_year, selected_month):
    user_role = (current_user.role or '').lower() or session.get('role', '').lower()
    if session.get('owner_access') or user_role == 'owner':
        active_company_id = current_user.company_id or session.get('company_id')
    else:
        active_company_id = current_user.company_id

    month_filter = f"{selected_year}-{str(selected_month).zfill(2)}-%"
  
    return Timesheet.query.filter(
        Timesheet.company_id == active_company_id,
        Timesheet.employee_id == employee_id,
        Timesheet.date.like(month_filter)
    ).first() is not None


def calculate_hours(start_time_str, end_time_str):
    if not start_time_str or not end_time_str or start_time_str in ["00:00:00", "00:00", "None"] or end_time_str in ["00:00:00", "00:00", "None"]:
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

    start_clean = extract_time_part(start_time_str)
    end_clean = extract_time_part(end_time_str)

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
                        if row.get('Duration') and row.get('Duration') != '0.0' and row.get('Duration') != '0':
                            csv_hours_map[row_date_str]['Duration'] = row.get('Duration')
        except Exception as e:
            print(f"⚠ Error reading CSV inside get_clockinout_days: {e}")

    for day in range(1, num_days + 1):
        date_obj = datetime(int(year), int(month), day)
        formatted_date = date_obj.strftime('%Y-%m-%d')
        day_index = date_obj.isoweekday() % 7

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
            "date": formatted_date,
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
        resolved_id = session.get("employee_id") or current_user.id
        
        raw_role = getattr(current_user, 'role', 'employee') or 'employee'
        clean_role = str(raw_role).strip().lower()
        
        db.session.close() 

        return jsonify({
            "employee_id": resolved_id,
            "role": clean_role,
            "company_id": current_user.company_id
        }), 200
        
    except Exception as e:
        if db and db.session:
            db.session.rollback()
            db.session.close()
        print(f"❌ Error inside current_user_info API node: {e}")
        return jsonify({"error": str(e)}), 500


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

        employee = db.session.get(Employee, employee_id_pk)
        if not employee:
            return jsonify({"status": "error", "message": "Employee profile not found in database."}), 404

        active_company_id = employee.company_id

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if user_role not in ['owner', 'admin'] and employee.company_id != (current_user.company_id or session.get('company_id')):
            print(f"🔒 Security Alert: Cross-tenant data injection blocked in api_clockin for user {current_user.id}")
            return jsonify({"status": "error", "message": "Unauthorized cross-tenant operation blocked"}), 403

        shift = ShiftState.query.filter_by(employee_id=employee_id_pk, company_id=active_company_id).first()
        
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
                company_id=active_company_id, 
                employee_id=employee_id_pk, 
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

        employee = db.session.get(Employee, employee_data_id)
        if not employee:
            return jsonify({"status": "error", "message": "Employee profile not found in database."}), 404

        active_company_id = employee.company_id

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if user_role not in ['owner', 'admin'] and employee.company_id != (current_user.company_id or session.get('company_id')):
            print(f"🔒 Security Alert: Cross-tenant data injection blocked in api_clockout for user {current_user.id}")
            return jsonify({"status": "error", "message": "Unauthorized cross-tenant operation blocked"}), 403

        shift = ShiftState.query.filter_by(employee_id=employee_data_id, company_id=active_company_id).first()
        
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
        employee_data_id = session.get("employee_id") or current_user.id

        if not employee_data_id:
            return jsonify({"status": "no_employee"}), 200

        employee = db.session.get(Employee, employee_data_id)
        if not employee:
            return jsonify({"status": "error", "message": "Employee profile not found in database."}), 404

        active_company_id = employee.company_id

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if user_role not in ['owner', 'admin'] and employee.company_id != (current_user.company_id or session.get('company_id')):
            print(f"🔒 Security Alert: Cross-tenant access blocked in api_shiftstate for user {current_user.id}")
            return jsonify({"status": "error", "message": "Unauthorized cross-tenant operation blocked"}), 403

        shift = ShiftState.query.filter_by(employee_id=employee_data_id, company_id=active_company_id).first()
        
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
        
        selected_employee_id = session.get("employee_id") or current_user.id
        if not selected_employee_id:
            return jsonify({"status": "error", "message": "Employee ID missing in session"}), 400

        employee = db.session.get(Employee, selected_employee_id)
        if not employee:
            return jsonify({"status": "error", "message": "Employee profile not found"}), 404
            
        active_company_id = employee.company_id
        emp_name = str(employee.employee_name or "Unknown")

        now = datetime.now()
        current_year = now.strftime('%Y')
        current_month = now.strftime('%m') 
        date_str = now.strftime('%Y-%m-%d')
        time_str = now.strftime('%H:%M:%S')

        location_data = data.get('location', 'None')
        if not location_data or str(location_data).strip() == "":
            location_data = "None"

        target_folder, _, csv_file_path = get_company_clock_paths(
            active_company_id, selected_employee_id, current_year, current_month
        )
        os.makedirs(target_folder, exist_ok=True)

        if not os.path.exists(csv_file_path):
            with open(csv_file_path, mode='w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['EventID', 'Date', 'Time', 'Type', 'Duration', 'Task', 'employee_id', 'employee_name', 'Location'])
                
                import calendar
                _, num_days = calendar.monthrange(int(current_year), int(current_month))
                row_id = 1
                
                for day in range(1, num_days + 1):
                    formatted_date = f"{current_year}-{current_month.zfill(2)}-{str(day).zfill(2)}"
                    
                    writer.writerow([row_id, formatted_date, "00:00:00", "START", "0.0", "", str(employee.local_id), emp_name, "None"])
                    row_id += 1
                    writer.writerow([row_id, formatted_date, "00:00:00", "END", "0.0", "", str(employee.local_id), emp_name, "None"])
                    row_id += 1

        updated_rows = []
        headers = []

        with open(csv_file_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            headers = next(reader)
            
            for row in reader:
                if len(row) >= 4:
                    if row[1] == date_str and row[3] == event_type:
                        row[2] = time_str
                        
                        if len(row) >= 7:
                            row[6] = str(employee.local_id)
                        
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
        if db and db.session:
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

        employee_id_pk = data.get("employee_id") 
        
        employee = db.session.get(Employee, employee_id_pk)
        if not employee:
            return jsonify({"status": "error", "message": "Employee profile not found in database."}), 404

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = employee.company_id
        else:
            active_company_id = current_user.company_id

        if user_role not in ['owner', 'admin'] and employee.company_id != active_company_id:
            print(f"🔒 Security Alert: Cross-tenant data injection blocked for user {current_user.id}")
            return jsonify({"status": "error", "message": "Unauthorized cross-tenant operation blocked"}), 403

        raw_date = data.get("date")
        if isinstance(raw_date, list) and len(raw_date) > 0:
            work_date_str = str(raw_date[0]).strip()
        else:
            work_date_str = str(raw_date).strip()

        existing_entry = Timesheet.query.filter_by(
            company_id=active_company_id,
            employee_id=employee_id_pk,
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
                company_id=active_company_id,
                employee_id=employee_id_pk,
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
                target_folder, _, csv_file_path = get_company_clock_paths(active_company_id, employee_id_pk, c_year, c_month)
                
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
                                    r[6] = str(employee.local_id)
                                    
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

        shift_state = ShiftState.query.filter_by(employee_id=employee_id_pk, company_id=active_company_id).first()
        if shift_state:
            db.session.delete(shift_state)
            
        db.session.commit()
        db.session.close() 
        
        return jsonify({"status": "saved", "message": "Timesheet entry saved and shift state cleared"}), 200

    except Exception as e:
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
        employee_id = session.get('employee_id') or current_user.id

        today = datetime.today()
        selected_year = request.args.get('year') or session.get('selected_year') or str(today.year)
        selected_month = request.args.get('month') or session.get('selected_month') or f"{today.month:02d}"

        selected_month = str(selected_month).zfill(2)
        selected_year = str(selected_year)

        if not employee_id:
            return jsonify({}), 400

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()

        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return jsonify({}), 401

        employee_profile = Employee.query.filter_by(id=employee_id, company_id=active_company_id).first()
        if not employee_profile:
            return jsonify({}), 403

        month_data = load_clock_hours(active_company_id, str(employee_id), str(selected_year), str(selected_month)) or {}

        hours_table = month_data.get("hours_table", month_data) or {}

        work_day_entries = hours_table.get("work_day_entries") or []
        tax_data = hours_table.get("tax") or {}

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

        if not clean_entries:
            clean_entries = get_clockinout_days(selected_year, selected_month, employee_id, active_company_id)

        dailyFields = [
            'date', 'day', 'saturday', 'holiday', 'start_time', 'end_time', 'totalHours', 'task'
        ]

        db.session.close() 

        return jsonify({
            "employee_id": employee_id,
            "month": selected_month,
            "year": selected_year,
            "work_day_entries": clean_entries,
            "tax": tax_data,
            "dailyFields": dailyFields
        })

    except Exception as e:
        if db and db.session:
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

        selected_employee_id = session.get('employee_id') or current_user.id
        if not selected_employee_id:
            return jsonify([])

        _, _, csv_file_path = get_company_clock_paths(active_company_id, selected_employee_id, year, month)
        
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

        db.session.expire_all()

        if session.get('role') == 'employee':
            active_employee_id = session.get('employee_id')
            if not active_employee_id:
                flash("שגיאה: מזהה עובד חסר ב-Session, נא להיכנס מחדש.", "danger")
                return redirect(url_for('login'))
            employees = Employee.query.filter_by(id=active_employee_id, company_id=active_company_id).all()
        else:
            employees = Employee.query.filter_by(company_id=active_company_id).order_by(Employee.employee_name).all()

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

                    employee = Employee.query.filter_by(id=selected_employee_id, company_id=active_company_id).first()
                    if not employee:
                        flash("שגיאה: העובד המבוקש אינו קיים במערכת שלך", "danger")
                        return redirect(url_for('clock_in_out'))

                    u_bound = User.query.filter_by(email=employee.email, company_id=active_company_id).first() if employee.email else None
                    target_storage_id = str(u_bound.id if u_bound else employee.id)

                    active_lang = get_lang()
                    trans_emp = load_employee_translated(employee, active_lang, company_id=active_company_id) or {}
                            
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

                    _, _, csv_file_path = get_company_clock_paths(active_company_id, target_storage_id, selected_year, selected_month)
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
                        Timesheet.company_id == active_company_id,
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
                                company_id=active_company_id, 
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

                    save_clock_hours(active_company_id, target_storage_id, selected_year, selected_month, table_data)

                    db.session.close()
                    flash("נתוני השעות והמשימות נשמרו בהצלחה למערכת!", "success")
                    return redirect(url_for('clock_in_out'))

                except Exception as e:
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
        
        employee = Employee.query.filter_by(id=selected_employee_id, company_id=active_company_id).first() if selected_employee_id else None
        
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
            u_bound = User.query.filter_by(email=employee.email, company_id=active_company_id).first() if employee.email else None
            target_storage_id = str(u_bound.id if u_bound else employee.id)

            month_filter = f"{selected_year}-{selected_month.zfill(2)}-%"
            timesheet_entries = Timesheet.query.filter(
                Timesheet.company_id == active_company_id,
                Timesheet.employee_id == target_storage_id,  
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
                active_company_id, 
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

        company_obj = db.session.get(Company, active_company_id)
        translated_company = load_company_translated(company_obj, language) if company_obj else {}

        if employee:
            u_bound = User.query.filter_by(email=employee.email, company_id=active_company_id).first() if employee.email else None
            target_storage_id = str(u_bound.id if u_bound else employee.id)
            company_all_clock_hours = load_clock_hours(active_company_id, target_storage_id, selected_year, selected_month) or {}
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
            trans = load_employee_translated(emp_row, language, company_id=active_company_id) or {}
                
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

        employee = Employee.query.filter_by(id=employee_id, company_id=active_company_id).first()
        if not employee:
            flash("שגיאה: פרטי העובד לא נמצאו במערכת שלך.", "danger")
            session.pop("employee_id", None)
            return redirect(url_for('clock_in_out'))

        language = get_lang()
        trans_emp = load_employee_translated(employee, language, company_id=active_company_id) or {}

        employee_name = trans_emp.get("name") or employee.employee_name or ""
        id_number = employee.id_number 

        session["timesheet_month"] = str(selected_month).zfill(2)
        session["timesheet_year"] = selected_year
        session["employee_id"] = employee_id

        timesheet_data = (
            Timesheet.query
            .filter_by(company_id=active_company_id, employee_id=employee.id) 
            .filter(Timesheet.date.like(f"{selected_year}-{selected_month.zfill(2)}-%"))
            .order_by(Timesheet.date.desc())
            .all()
        )

        _, _, csv_file_path = get_company_clock_paths(active_company_id, employee.id, selected_year, selected_month)
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

        shift_state = ShiftState.query.filter_by(company_id=active_company_id, employee_id=employee.id).first() 
        
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

        employee_profile = Employee.query.filter_by(id=employee_id, company_id=active_company_id).first()
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
            save_clock_hours(active_company_id, employee_id, year, month, json_payload)
        except:
            pass

        session['table_data'] = work_day_entries

        target_folder, _, csv_file_path = get_company_clock_paths(active_company_id, employee_id, year, month)
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

        selected_employee_id = session.get('employee_id') or current_user.id

        if not selected_employee_id:
            return "Error: No employee selected for download context.", 400

        employee = db.session.get(Employee, int(selected_employee_id)) if str(selected_employee_id).isdigit() else None
        if not employee and str(selected_employee_id).isdigit():
            employee = Employee.query.filter_by(local_id=int(selected_employee_id), company_id=active_company_id).first()

        csv_employee_id_str = str(employee.local_id) if employee and employee.local_id else str(selected_employee_id)

        now_date = datetime.today()
        selected_month = request.args.get('month') or session.get('selected_month') or now_date.strftime('%m')
        selected_year = request.args.get('year') or session.get('selected_year') or now_date.strftime('%Y')

        selected_month = str(selected_month).zfill(2)
        selected_year = str(selected_year)

        target_folder, _, csv_file_path = get_company_clock_paths(
            active_company_id, 
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
        if db and db.session:
            db.session.rollback()
            db.session.close()
        print(f"⚠️ Warning inside clear_clock_data session purge: {e}")
        
    return redirect(url_for('clock_in_out'))



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
