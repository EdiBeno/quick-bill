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
import random
import concurrent.futures
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from pathlib import Path  
from urllib.parse import parse_qs
from functools import wraps 
# -----------------------------------------------------------
from flask import (
    Flask, render_template, request, redirect, url_for, session, 
    jsonify, make_response, Response, flash, current_app
)
from flask_babel import Babel
import babel.dates
import babel.numbers

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
from translate import Translator
from concurrent.futures import ThreadPoolExecutor
from playwright.sync_api import sync_playwright

import pandas as pd
import numpy as np
import openpyxl

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

# -----------------------------
# Models N8N_API_KEY WORKS WITH AI AGENT
# -----------------------------

# קריאת מפתח האבטחה מה-.env (הגדרת אותו כברירת מחדל בשבילך)
N8N_API_KEY = os.environ.get("N8N_API_KEY", "QuickBill_Local_Secure_n8n_Connection_2026_Tokens")


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
    TRANSACTIONS_DIR  = os.path.join(PERSISTENT_BASE, "transactions")
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

# -----------------------------
#  DB Create All & Auto-Migration Script
# -----------------------------

def initialize_database():
    with app.app_context():

        db.create_all()

        owner_company = Company.query.filter_by(id=OWNER_COMPANY_ID).first()
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

        if not owner_user:
            owner_user = User(
                id=1,
                email=OWNER_USERNAME,
                username="Owner",
                company_id=OWNER_COMPANY_ID,
                role="owner",
                is_active=True,
                is_approved=True
            )
            owner_user.set_password(OWNER_PASSWORD)
            db.session.add(owner_user)

        try:
            db.session.execute(db.text(
                "SELECT setval('company_id_seq', COALESCE((SELECT MAX(id)+1 FROM company), 2), false);"
            ))
        except Exception:
            pass

        db.session.commit()

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
                return lang, str(translated).strip()
            
            return lang, text_str
        except Exception:
            return lang, text_str

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
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
    # אם לא נשלח מטבע → אל תשתמש בשפה
    if currency_code is None:
        return format_number_only(value)

    symbol = get_currency_symbol(currency_code)
    formatted_num = format_number_only(value)
    return f"{formatted_num} {symbol}"





# ----------------------
# Getting Format All Percentage Currency : Form 
# ----------------------

# Safely convert a value to a float פונקציה להמיר ערכים למספרים
def to_float(value):
    try:
        cleaned = str(value).replace(',', '').strip()
        if cleaned.lower() in ('', 'n/a', 'none'):
            return 0.0
        return float(cleaned)
    except (ValueError, TypeError):
        return 0.0


def format_currency(amount):
    try:
        return "{:,.2f}".format(float(amount))
    except:
        return amount

app.jinja_env.filters['currency'] = format_currency


def format_currency(value):
    """Formats numbers as currency (Shekel symbol added)."""
    if not value or value == 0.0:
        return '0.00'
    return f"{value:,.2f}"

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

def clean_number(value):
    try:
        return float(str(value).replace("₪", "").replace(",", "").strip())
    except:
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
        # 1. עקיפה מיידית עבור Owner או מזהה הזהב 0 לצורך הפקת חשבוניות עצמיות
        if is_owner() or str(session.get('customer_id')) == "0":
            return f(*args, **kwargs)

        db_role = getattr(current_user, 'role', '').lower() if current_user.is_authenticated else None
        active_role = db_role or session.get('role')

        # 2. מנהל מערכת מורשה לגשת לכל הלקוחות של החברה שלו
        if active_role == 'manager':
            return f(*args, **kwargs)

        # 3. הגנה על לקוח רגיל: נעילה הרמטית רק ל-ID האישי שלו
        if active_role == 'customer':
            selected_customer_id = kwargs.get('customer_id') or request.args.get('customer_id')
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

        # OWNER LOGIN - עוקף קריאות DB ומבוצר כראוי כמו בחברות Enterprise ענקיות!
        if login_email == OWNER_USERNAME and password == OWNER_PASSWORD:
            session.clear()

            # הזרקת אובייקט הבעלים הווירטואלי ישירות דרך ה-Loader ללא תלות ברשומות SQL פיזיות
            virtual_owner = OwnerUser(OWNER_USERNAME)
            login_user(virtual_owner, force=True)

            session['owner_access']  = True
            session['user_id']       = 0  # מזהה וירטואלי קבוע של הבעלים
            session['role']          = 'owner'
            session['user_role']     = 'owner'
            session['company_id']    = OWNER_COMPANY_ID
            session['company_name']  = "לרקוד על הגג"
            session['company_email'] = OWNER_USERNAME
            session['customer_id']   = None

            # עדכון זמן כניסה ישירות לטבלה המקומית במידת הצורך
            try:
                owner_db_record = User.query.filter_by(email=OWNER_USERNAME, company_id=OWNER_COMPANY_ID).first()
                if owner_db_record:
                    owner_db_record.last_login = datetime.utcnow()
                    db.session.commit()
            except Exception:
                pass

            db.session.close()
            flash(py_i18n('login.owner_success'), 'success')
            return redirect(url_for('invoice'))

        # USER LOGIN
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

        session['user_id']    = user.id
        session['role']       = user.role
        session['user_role']  = user.role
        session['company_id'] = user.company_id

        if user.company_id:
            company_obj = db.session.get(Company, user.company_id)
            if company_obj:
                session['company_name']  = company_obj.name
                session['company_email'] = company_obj.email

        user.last_login = datetime.utcnow()
        db.session.commit()

        # MANAGER
        if user.role == 'manager':
            session['customer_id'] = None
            db.session.close()
            flash(py_i18n('login.manager_success'), 'success')
            return redirect(url_for('company'))

        # EMPLOYEE - אינטגרציה מלאה ומאובטחת למולטי-חברות 
        if (user.role or '').strip().lower() == 'employee':
            session['customer_id'] = None

            # שליפת פרופיל העובד הרשמי מתוך החברה הספציפית הזו
            employee_profile = EmployeeData.query.filter_by(employee_id=user.id, company_id=user.company_id).first()
            if not employee_profile:
                employee_profile = EmployeeData.query.filter_by(email=user.email, company_id=user.company_id).first()

            # נעילת המזהים הקריטיים ב-Session למניעת זריקה ללוגין
            session['employee_id'] = employee_profile.id if employee_profile else user.id
            session['employee_name'] = employee_profile.employee_name if employee_profile else user.username
            session['role'] = 'employee'
            session['user_role'] = 'employee'
            session['company_id'] = user.company_id

            db.session.close()
            flash(py_i18n('login.success'), 'success')
            # הפניה ישירה לדשבורד העובד המרכזי ששיתפת
            return redirect(url_for('employee_dashboard'))

        db.session.close()
        flash(py_i18n('login.success'), 'success')
        return redirect(url_for('invoice'))

    except Exception as e:
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

        # 1) MANAGER – פותח עסק חדש לחלוטין
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

            # תיקון הזהב: לא כופים את id=user.id! נותנים למסד הנתונים לייצר מפתח ראשי אוטומטי ונקי
            # ובכך מונעים לחלוטין את בעיית הדריסה וכפילויות השמות בדאשבורד ובחשבונית
            platform_customer = Customer(
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
                # 1. יצירת קובץ Placeholder זמני לחברה (1.json) בנתיב המדויק שלו!
                comp_folder = os.path.join(app.config["COMPANIES_DIR"], f"{new_company.id}")
                comp_file_path = os.path.join(comp_folder, f"{new_company.id}.json")
                if not os.path.exists(comp_file_path):
                    os.makedirs(comp_folder, exist_ok=True)
                    with open(comp_file_path, "w", encoding="utf-8") as f:
                        json.dump({"name": {}, "company_id_number": "", "deduction_file": "", "address": {}, "city": {}}, f, ensure_ascii=False, indent=4)

                # 2. יצירת קובץ Placeholder זמני ללקוח בנתיב המדויק שלו!
                cust_folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{OWNER_COMPANY_ID}_{next_local_id}")
                cust_file_path = os.path.join(cust_folder, "customer.json")
                if not os.path.exists(cust_file_path):
                    os.makedirs(cust_folder, exist_ok=True)
                    with open(cust_file_path, "w", encoding="utf-8") as f:
                        json.dump({"name": {}, "address": {}, "city": {}, "message": {}}, f, ensure_ascii=False, indent=4)

                # הוסר לחלוטין: קריאות הרקע המפוזרות לגוגל/רשת נמחקו! מונע הצפת טרדים וחסימות IP
                print(f"✔ Platform Manager configuration and placeholders initialized cleanly on disk.")
            except Exception as e:
                print(f"⚠️ Fixed Placeholder or background trigger failed: {e}")

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

                    try:
                        # יצירת ה-Placeholder הזמני ללקוח הקיים
                        folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{company.id}_{customer.local_id}")
                        file_path = os.path.join(folder, "customer.json")
                        if not os.path.exists(file_path):
                            os.makedirs(folder, exist_ok=True)
                            with open(file_path, "w", encoding="utf-8") as f:
                                json.dump({"name": {}, "address": {}, "city": {}, "message": {}}, f, ensure_ascii=False, indent=4)

                        # הוסר לחלוטין: מחיקת קריאת הרקע המיותרת לגוגל למניעת הצפת טרדים וחסימות!
                        print(f"✔ Verified customer placeholder asset created cleanly on disk.")
                    except Exception as e:
                        print(f"⚠️ Pre-created customer asset initialization skipped: {e}")

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
                # יצירת קובץ Placeholder זמני ללקוח החדש בנתיב המדויק שלו!
                folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{company.id}_{saved_local_id}")
                file_path = os.path.join(folder, "customer.json")
                if not os.path.exists(file_path):
                    os.makedirs(folder, exist_ok=True)
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump({"name": {}, "address": {}, "city": {}, "message": {}}, f, ensure_ascii=False, indent=4)

                # הוסר לחלוטין: מניעת הצפת הטרדים של גוגל/רשת ברישום
                print(f"✔ Fresh customer workspace placeholders established without flooding threads.")
            except Exception as e:
                print(f"⚠️ Fresh customer workspace file system initialization failed: {e}")

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

        # =========================================================================
        # 3) EMPLOYEE – עובד של חברה קיימת במערכת
        # =========================================================================
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
        lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

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
                    emp_profile = EmployeeData.query.filter_by(email=c.email).first()

                try:
                    trans_cust = load_customer_translated(c, language, company_id=OWNER_COMPANY_ID) or {}
                    translated_name = trans_cust.get("name")
                except:
                    translated_name = None

                display_name = (
                    translated_name or
                    (emp_profile.employee_name if emp_profile else None) or 
                    (u.username if u else None) or 
                    c.customer_name or 
                    c.email or 
                    "No Name"
                )

                role_check = (role_label or '').lower()
                view_clients.append({
                    'type': 'manager' if role_check == 'manager' else ('employee' if role_check == 'employee' else 'customer'),
                    'id': c.id,
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
                        
                        comp_folder = ensure_company_folder(m.company_id)
                        if comp_folder and os.path.exists(comp_folder):
                            comp_file = os.path.join(comp_folder, f"{m.company_id}.json")
                            if os.path.isfile(comp_file):
                                try:
                                    with open(comp_file, "r", encoding="utf-8") as f:
                                        json_data = json.load(f)
                                        name_dict = json_data.get("name", {})
                                        if isinstance(name_dict, dict):
                                            display_company_name = name_dict.get(lookup_lang) or name_dict.get("he") or display_company_name
                                except Exception as e:
                                    print(f"⚠️ Error reading corporate translation json: {e}")

                if m.access_expires_at and delta <= 0:
                    display_status_label = "employee.status_expired"

                emp_profile_m = None
                if m.email:
                    emp_profile_m = EmployeeData.query.filter_by(email=m.email).first()

                try:
                    trans_emp = load_employee_translated(emp_profile_m, language, company_id=m.company_id) if emp_profile_m else {}
                    translated_name_m = trans_emp.get("name")
                except:
                    translated_name_m = None

                display_name_m = (
                    translated_name_m or
                    (emp_profile_m.employee_name if emp_profile_m else None) or 
                    m.username or 
                    m.email or 
                    "No Name"
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
                    emp_profile = EmployeeData.query.filter_by(email=c.email, company_id=active_company_id).first()

                # 🚀 תוקן: משיכת השם הדינמי ללקוח של המנהל מתוך מנגנון ה-i18n בהתאם לשפה החיה של הדפדפן!
                try:
                    trans_cust = load_customer_translated(c, language, company_id=active_company_id) or {}
                    translated_name = trans_cust.get("name")
                except:
                    translated_name = None

                display_name = (
                    translated_name or
                    (emp_profile.employee_name if emp_profile else None) or 
                    (u.username if u else None) or 
                    c.customer_name or 
                    c.email or 
                    "No Name"
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
                    'email': c.email or "ללא אימייל",
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

                emp_profile = EmployeeData.query.filter_by(email=emp_user.email, company_id=active_company_id).first()
                
                try:
                    trans_emp = load_employee_translated(emp_profile, language, company_id=active_company_id) if emp_profile else {}
                    translated_name_e = trans_emp.get("name")
                except:
                    translated_name_e = None
                    
                display_name = translated_name_e or (emp_profile.employee_name if emp_profile else (emp_user.username or emp_user.email or "עובד חדש"))

                status_label = "employee.status_active" if emp_user.is_active else "employee.status_blocked"
                if emp_user.access_expires_at and delta <= 0:
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

        if is_owner:
            all_employees_db = EmployeeData.query.all()
            all_customers_db = Customer.query.all()
        else:
            all_employees_db = EmployeeData.query.filter_by(company_id=active_company_id).all()
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
            
            key_cust = f"{cust.company_id}_{cust.local_id}" if getattr(cust, "local_id", None) else f"user_{cust.id}"
            customer_i18n_list[key_cust] = {"name": trans_cust.get("name") or cust.customer_name or ""}

        for u_item in view_clients:
            if u_item['created_at'] and hasattr(u_item['created_at'], 'strftime'):
                u_item['created_at'] = u_item['created_at'].strftime('%d/%m/%Y %H:%M')
            else:
                u_item['created_at'] = str(u_item['created_at'] or '')

            if u_item['last_login'] and hasattr(u_item['last_login'], 'strftime'):
                u_item['last_login'] = u_item['last_login'].strftime('%d/%m/%Y %H:%M')
            else:
                u_item['last_login'] = str(u_item['last_login'] or '')

        db.session.close()

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
        if 'db' in locals():
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
        email      = request.form.get('email', '').strip()
        status     = request.form.get('status', '').strip().lower()
        duration   = request.form.get('duration', '').strip()
        company_id = request.form.get('company_id')

        current_lang = request.form.get('lang') or request.args.get('lang') or get_lang()

        user_role = (current_user.role or '').lower() or session.get('role', '').lower()
        is_owner = (user_role == 'owner') or (session.get('owner_access') is True)
        active_company_id = current_user.company_id or session.get('company_id')

        db.session.expire_all()

        # OWNER CAN UPDATE ANY COMPANY
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
            return redirect(url_for('clients', lang=current_lang))

        target_user_role_clean = (user.role or '').lower()

        # MANAGER CANNOT TOUCH MANAGER/OWNER
        if not is_owner:
            if target_user_role_clean in ['manager', 'owner']:
                db.session.close()
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients', lang=current_lang))

            if user.company_id != active_company_id:
                db.session.close()
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients', lang=current_lang))

        # STATUS UPDATE
        is_status_active = status in ['active', 'approved', '1', 'true', 'מאושר', 'פעיל']
        user.is_active   = is_status_active
        user.is_approved = is_status_active

        # ACCESS DURATION
        if duration and duration.isdigit():
            seconds = int(duration)
            user.access_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=seconds)
        else:
            user.access_expires_at = None

        search_company_id = user.company_id if user.company_id else active_company_id
        now = datetime.now()
        current_year = now.strftime('%Y')
        current_month = now.strftime('%m')

        # CUSTOMER RECORD SYNC
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

        # EMPLOYEE RECORD SYNC - תוקן לשם הטבלה החדש שלכם employee_data
        employee_profile = EmployeeData.query.filter_by(email=user.email, company_id=search_company_id).first()
        if employee_profile:
            if hasattr(employee_profile, 'is_active'):
                employee_profile.is_active = user.is_active

        db.session.commit()

        # DISK SYNC
        try:
            # EMPLOYEE JSON SYNC
            if target_user_role_clean == 'employee' and employee_profile:
                # הוחזר לקוד המקור הבטוח שלך המשתמש ב-id
                target_folder, json_file_path, _ = get_company_clock_paths(
                    search_company_id,
                    employee_profile.id,
                    current_year,
                    current_month
                )
                os.makedirs(target_folder, exist_ok=True)

                existing_data = {}
                if os.path.isfile(json_file_path):
                    try:
                        with open(json_file_path, "r", encoding="utf-8") as f:
                            existing_data = json.load(f)
                    except:
                        existing_data = {}

                existing_data.setdefault("hours_table", {"work_day_entries": [], "tax": {}})
                existing_data["is_active"] = user.is_active
                existing_data["email"] = user.email
                existing_data["employee_name"] = employee_profile.employee_name

                with open(json_file_path, "w", encoding="utf-8") as f:
                    json.dump(existing_data, f, ensure_ascii=False, indent=2)

            # CUSTOMER JSON SYNC 
            elif target_user_role_clean == 'customer' and customer:
                if customer.local_id:
                    folder = ensure_customer_folder(search_company_id, customer.local_id)
                    if folder:
                        os.makedirs(folder, exist_ok=True)
                        file_path = os.path.join(folder, "customer.json")
                        
                        existing_data = {}
                        is_file_exists = os.path.isfile(file_path)
                        
                        if is_file_exists:
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    existing_data = json.load(f)
                            except:
                                existing_data = {}
                        
                        existing_data.setdefault("name", {})
                        existing_data.setdefault("address", {})
                        existing_data.setdefault("city", {})
                        existing_data.setdefault("message", {})
                        
                        existing_data["is_active"] = user.is_active
                        
                        with open(file_path, "w", encoding="utf-8") as f:
                            json.dump(existing_data, f, ensure_ascii=False, indent=4)

        except Exception as file_err:
            print(f"⚠️ Warning: Disk sync skipped: {file_err}")

        db.session.close()
        flash(py_i18n("client.access_updated"), "success")
        return redirect(url_for('clients', lang=current_lang))

    except Exception as e:
        if 'db' in locals():
            db.session.rollback()
            db.session.close()
        flash("שגיאה חמורה בעדכון הרשאות הגישה", "danger")
        return redirect(url_for('clients', lang=get_lang()))


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
        
        current_lang = request.form.get('lang') or request.args.get('lang') or get_lang()

        owner_mode = is_owner() 
        active_company_id = current_user.company_id or session.get('company_id')

        if not email:
            flash("כתובת אימייל חסרה", "danger")
            return redirect(url_for('invoice_data', lang=current_lang))

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
            return redirect(url_for('invoice_data', lang=current_lang))

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
        return redirect(url_for('invoice_data', lang=current_lang))

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error in send_reset_link: {e}")
        flash("שגיאה במערכת שליחת המייל", "danger")
        return redirect(url_for('invoice_data', lang=get_lang()))

# -----------------------------
#   שלב שני: קביעת ועדכון הסיסמה בפועל (Set Password via token)
# -----------------------------

@app.route('/set-password', methods=['GET', 'POST'])
def set_password():
    try:
        current_lang = request.form.get('lang') or request.args.get('lang') or get_lang()

        if session.get('owner_access'):
            if request.method == 'POST':
                flash(py_i18n("reset.owner_info"), "info")
                return redirect(url_for('invoice_data', lang=current_lang))
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

        try:
            db.session.execute(db.text("PRAGMA foreign_keys = OFF;"))
        except:
            pass

        for user_id in ids:
            if not user_id.isdigit():
                continue

            target_uid = int(user_id)
            user = db.session.get(User, target_uid)

            #  BLOCK OWNER DELETE (מוגן מפני ערכים ריקים ב-Database)
            if user:
                user_email_clean = (user.email or "").strip()
                user_role_clean = (user.role or "").strip().lower()

                if user_email_clean == OWNER_USERNAME:
                    flash("אי אפשר למחוק את משתמש הבעלים!", "danger")
                    continue

                if user_role_clean == "owner":
                    flash("אי אפשר למחוק משתמש בעלים!", "danger")
                    continue

                if user.company_id == OWNER_COMPANY_ID:
                    flash("אי אפשר למחוק משתמש ששייך לחברת הבעלים!", "danger")
                    continue

            #  SAVE COMPANY BEFORE DELETE
            target_comp_id = user.company_id if user else None

            owner_cust_row = Customer.query.filter_by(id=target_uid, company_id=OWNER_COMPANY_ID).first()
            owner_local_id = owner_cust_row.local_id if owner_cust_row else None

            #  DELETE USER RECORDS (מנקה הרמטית את כל הטבלאות לפי ה-ID)
            try:
                db.session.execute(db.text(f"DELETE FROM shift_states WHERE employee_id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM timesheets WHERE employee_id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM employee_data WHERE id = {target_uid} OR user_id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM password_reset_tokens WHERE user_id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM customer WHERE id = {target_uid}"))
                db.session.execute(db.text(f"DELETE FROM users WHERE id = {target_uid}"))
                db.session.flush()
            except Exception as e:
                print(f"User rows query bypass check: {e}")

            #  DELETE COMPANY (NOT OWNER COMPANY)
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
                    db.session.execute(db.text(f"DELETE FROM timesheets WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM employee_data WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM users WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM customer WHERE company_id = {target_comp_id}"))
                    db.session.execute(db.text(f"DELETE FROM company WHERE id = {target_comp_id}"))
                    db.session.flush()
                except Exception as e:
                    print(f"Company row bypass check: {e}")

                #  DELETE COMPANY FOLDERS
                try:
                    comp_dir = os.path.join(app.config["COMPANY_DIR"], str(target_comp_id))
                    if os.path.exists(comp_dir):
                        shutil.rmtree(comp_dir)
                except Exception as e:
                    print(f"Failed to delete company folder: {e}")

                try:
                    emp_dir = os.path.join(app.config["EMPLOYEES_DIR"], f"company_{target_comp_id}")
                    if os.path.exists(emp_dir):
                        shutil.rmtree(emp_dir)
                except Exception as e:
                    print(f"Failed to delete employees folder: {e}")

            #  DELETE OWNER CUSTOMER FOLDER
            if owner_local_id:
                try:
                    cust_folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{OWNER_COMPANY_ID}_{owner_local_id}")
                    if os.path.exists(cust_folder):
                        shutil.rmtree(cust_folder)
                except Exception as e:
                    print(f"⚠ Failed to rmtree customer folder: {e}")

            deleted_count += 1

        try:
            db.session.execute(db.text("PRAGMA foreign_keys = ON;"))
        except:
            pass

        db.session.commit()
        db.session.close()

        if deleted_count > 0:
            flash(py_i18n("client.deleted_count").format(count=deleted_count), "success")
        else:
            flash(py_i18n("auth.no_permission"), "danger")

        return redirect(url_for('clients'))

    except Exception as e:
        if 'db' in locals():
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
    try:
        email      = request.form.get('email', '').strip()
        new_role   = request.form.get('role', '').strip().lower()
        company_id = request.form.get('company_id')

        current_lang = request.form.get('lang') or request.args.get('lang') or get_lang()

        if not email or not new_role:
            flash(py_i18n("client.role_invalid_data"), "danger")
            return redirect(url_for('clients', lang=current_lang))

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
            return redirect(url_for('clients', lang=current_lang))

        target_user_role_clean = (user.role or '').lower()

        if not owner_mode:
            if target_user_role_clean in ['manager', 'owner']:
                db.session.close()
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients', lang=current_lang))

            if user.company_id != active_company_id:
                db.session.close()
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients', lang=current_lang))

            if new_role in ['manager', 'owner']:
                db.session.close()
                flash(py_i18n("auth.no_permission"), "danger")
                return redirect(url_for('clients', lang=current_lang))

        old_role       = user.role
        old_company_id = user.company_id
        now            = datetime.now()
        current_year   = now.strftime('%Y')
        current_month  = now.strftime('%m')

        if old_role != new_role:
            # ניקוי תיקיית לקוח ישן אם היה לקוח
            try:
                old_cust = Customer.query.filter_by(email=user.email, company_id=old_company_id).first()
                if old_cust and old_cust.local_id and target_user_role_clean == 'customer':
                    old_folder = os.path.join(app.config["CUSTOMERS_DIR"], f"{old_company_id}_{old_cust.local_id}")
                    if os.path.exists(old_folder):
                        import shutil
                        shutil.rmtree(old_folder)
            except Exception as e:
                print(f"⚠️ Safe disk cleanup skipped in update_role: {e}")

            user.role = new_role

            # MANAGER
            if new_role == 'manager':
                company_name = user.username or user.email
                new_company = Company(name=company_name, email=user.email, translations_json="{}")
                db.session.add(new_company)
                db.session.flush()

                # מחיקת רשומות לקוח ישנות של המשתמש בחברה הישנה
                old_cust_records = Customer.query.filter_by(email=user.email, company_id=old_company_id).all()
                for o_c in old_cust_records:
                    db.session.delete(o_c)

                # מחיקת רשומות עובד ודיווחי שעות קשורים למניעת IntegrityError
                old_emp_records = EmployeeData.query.filter_by(email=user.email, company_id=old_company_id).all()
                for o_e in old_emp_records:
                    ShiftState.query.filter_by(employee_id=o_e.id, company_id=old_company_id).delete()
                    Timesheet.query.filter_by(employee_id=o_e.id, company_id=old_company_id).delete()
                    db.session.delete(o_e)

                user.company_id  = new_company.id
                user.is_active   = True
                user.is_approved = True

            # CUSTOMER
            elif new_role == 'customer':
                if owner_mode and company_id:
                    user.company_id = int(company_id)

                if not user.company_id:
                    db.session.close()
                    flash(py_i18n("auth.no_company_assigned"), "danger")
                    return redirect(url_for('clients', lang=current_lang))

                emp_prof = EmployeeData.query.filter_by(email=user.email, company_id=old_company_id).first()
                if emp_prof:
                    ShiftState.query.filter_by(employee_id=emp_prof.id, company_id=old_company_id).delete()
                    Timesheet.query.filter_by(employee_id=emp_prof.id, company_id=old_company_id).delete()
                    db.session.delete(emp_prof)

                active_comp  = db.session.get(Company, user.company_id)
                comp_address = getattr(active_comp, 'address', '') or ""
                comp_city    = getattr(active_comp, 'city', '') or ""

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
                        address=comp_address,
                        city=comp_city,
                        role='customer',
                        is_active=user.is_active,
                        date=datetime.today().strftime('%d/%m/%Y')
                    )
                    db.session.add(customer)
                else:
                    customer.role      = 'customer'
                    customer.is_active = user.is_active
                    if not customer.customer_name:
                        customer.customer_name = user.username or user.email
                    if not customer.address:
                        customer.address = comp_address
                    if not customer.city:
                        customer.city = comp_city

                db.session.flush()

                try:
                    comp_folder = ensure_company_folder(user.company_id)
                    if comp_folder:
                        os.makedirs(comp_folder, exist_ok=True)
                        comp_file = os.path.join(comp_folder, f"{user.company_id}.json")
                        if not os.path.exists(comp_file):
                            with open(comp_file, "w", encoding="utf-8") as f:
                                json.dump(
                                    {
                                        "name": {},
                                        "company_id_number": "",
                                        "deduction_file": "",
                                        "address": {},
                                        "city": {}
                                    },
                                    f,
                                    ensure_ascii=False,
                                    indent=4
                                )

                    folder = ensure_customer_folder(user.company_id, customer.local_id)
                    if folder:
                        os.makedirs(folder, exist_ok=True)
                        file_path = os.path.join(folder, "customer.json")
                        if not os.path.exists(file_path):
                            with open(file_path, "w", encoding="utf-8") as f:
                                json.dump(
                                    {"name": {}, "address": {}, "city": {}, "message": {}},
                                    f,
                                    ensure_ascii=False,
                                    indent=4
                                )
                    print(f"✔ Role placeholders validated cleanly on disk without spawning translation threads.")
                except Exception as bg_err:
                    print(f"⚠️ Background folder setup failed in customer conversion: {bg_err}")

            # EMPLOYEE
            elif new_role == 'employee':
                if owner_mode and company_id:
                    user.company_id = int(company_id)

                user.is_active   = True
                user.is_approved = True

                old_cust_records = Customer.query.filter_by(email=user.email, company_id=user.company_id).all()
                for o_c in old_cust_records:
                    db.session.delete(o_c)

                employee_profile = EmployeeData.query.filter_by(email=user.email, company_id=user.company_id).first()
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
                        user.company_id,
                        clock_key,
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
                except Exception as file_err:
                    print(f"⚠️ Warning: Disk sync skipped: {file_err}")

        db.session.commit()
        db.session.close()

        flash(py_i18n("client.role_updated"), "success")
        return redirect(url_for('clients', lang=current_lang))

    except Exception as e:
        if 'db' in locals():
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

        # הרשאות בסיסיות
        if role not in ['customer', 'manager'] and not is_owner_user:
            return redirect(url_for('unauthorized'))

        # טעינת מזהים פעילים
        active_company_id  = session.get('company_id')
        active_customer_id = session.get('customer_id')

        # טעינת לקוח פעיל
        customer = Customer.query.filter_by(
            id=active_customer_id,
            company_id=active_company_id
        ).first()

        # fallback לבעלים
        if not customer:
            customer = Customer.query.filter_by(
                id=active_customer_id,
                company_id=OWNER_COMPANY_ID
            ).first()

        # מניעת גישה לא מורשית
        if not customer and role != 'manager' and not is_owner_user:
            print(f"⚠️ Security alert: Customer {active_customer_id} tried accessing company {active_company_id}")
            return redirect(url_for('unauthorized'))

        # החברה שאליה שייך הלקוח
        target_company_id = customer.company_id if customer else active_company_id
        company_obj = db.session.get(Company, target_company_id)

        language = get_lang()
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        # מצב Self Billing
        is_self_billing = False
        if customer and str(customer.company_id) == str(OWNER_COMPANY_ID) and role == 'manager':
            is_self_billing = True
        elif not customer and (role == 'manager' or is_owner_user):
            is_self_billing = True

        # טעינת חשבוניות
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

        # שיעור מע"מ ברירת מחדל
        last_invoice = Invoice.query.filter_by(company_id=target_company_id).order_by(Invoice.invoice_number.desc()).first()
        default_vat_rate = float(last_invoice.vat_rate) if last_invoice and last_invoice.vat_rate is not None else 0.0

        # בניית JSON ללקוח פעיל
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
            trans_active = load_customer_translated(customer, language) if customer else {}
            customer_json = {
                "id": customer.id if customer else None,
                "local_id": getattr(customer, 'local_id', customer.id) if customer else None,
                "customer_name": trans_active.get("name") or (customer.customer_name or customer.email or "לקוח") if customer else "מנהל מערכת",
                "address": trans_active.get("address") or (customer.address or "") if customer else "",
                "city": trans_active.get("city") or (customer.city or "") if customer else "",
                "postal_code": customer.postal_code or "" if customer else "",
                "email": customer.email or "" if customer else "",
                "phone": customer.phone or "" if customer else "",
                "message": trans_active.get("message") or (customer.message or "") if customer else ""
            }

        # שליחת כל הלקוחות לדף החשבונית
        all_customers = Customer.query.filter_by(company_id=target_company_id).all()
        all_customers_json = []
        for c in all_customers:
            trans_c = load_customer_translated(c, language)
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

        # סיבת ביטול מתורגמת
        cancellation_reason_trans = ""
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

        # זיהוי מטבע מקורי ומטבע יעד לחשבונית הנוכחית ב-Dashboard
        invoice_original_currency = (
            getattr(current_active_inv, 'currency', None) 
            or getattr(company_obj, 'currency', 'ILS') 
            if company_obj else 'ILS'
        )
        current_currency = request.cookies.get('currency') or session.get('currency') or invoice_original_currency

        base_ctx = base_invoice_context(customer_id=customer_json["id"])

        base_ctx.update({
            "invoices": invoices,
            "invoice": current_active_inv,
            "invoice_id": current_active_inv.id if current_active_inv else None,
            "allocation_number": current_active_inv.allocation_number if current_active_inv else None,
            "invoice_number": current_active_inv.invoice_number if current_active_inv else 1,
            "invoice_date": current_active_inv.invoice_date.strftime('%d-%m-%Y') if current_active_inv else datetime.today().strftime('%d-%m-%Y'),

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

            # הזרקת משתני המטבע ב-Dashboard
            "invoice_original_currency": invoice_original_currency,
            "current_currency": current_currency
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

@app.route('/employee-dashboard')
@login_required
def employee_dashboard():
    try:
        language = get_lang()
        
        # 1. זיהוי התפקיד והחברה הפעילה לפי מנגנון האבטחה הקיים שלך
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        active_company_id_session = getattr(current_user, 'company_id', None) or session.get('company_id')

        # ----------------- OWNER ACCESS -----------------
        if is_owner() or session.get('owner_access') or user_role == 'owner':
            # אונר מנותב ישירות לניהול הראשי (אינדקס)
            return redirect(url_for('index'))

        # ----------------- MANAGER ACCESS -----------------
        if user_role == 'manager':
            # מנהל שמגיע לדאשבורד יופנה לטופס החיפוש והניהול הקיים שלכם
            req_emp_id = request.args.get('employee_id')
            if req_emp_id:
                return redirect(url_for('contact_form', employee_id=req_emp_id))
            
            # אם לא נבחר עובד ספציפי, נשלח אותו לעובד הראשון של החברה שלו
            first_emp = EmployeeData.query.filter_by(company_id=active_company_id_session).first()
            if first_emp:
                return redirect(url_for('contact_form', employee_id=first_emp.id))
            return redirect(url_for('contact_form'))

        # ----------------- EMPLOYEE ACCESS (הגנה הרמטית) -----------------
        if user_role == 'employee':
            # שליפת רשומת ה-EmployeeData של העובד המחובר לפי ה-user_id (מזהה ה-User המחובר) או לפי המייל שלו
            current_employee_profile = EmployeeData.query.filter(
                (EmployeeData.user_id == current_user.id) | (EmployeeData.email == current_user.email)
            ).filter_by(company_id=active_company_id_session).first()

            if not current_employee_profile:
                print(f"⚠️ Warning: Employee user {current_user.id} has no EmployeeData profile created yet.")
                flash("שגיאה: לא נמצא פרופיל עובד תואם במערכת החברה.", "danger")
                return redirect(url_for('unauthorized'))

            # --- תוקן הרמטית: הזרקת ה-local_id או ה-id של העובד לסשן כדי שהשעונים וה-API לא יקרסו ---
            # משתמשים ב-local_id (מִסְפּוּר פנימי 1, 2, 3...) בדיוק לפי הצינור שביצרנו ב-current_user_info
            session['employee_id'] = current_employee_profile.local_id if current_employee_profile.local_id else current_employee_profile.id
            
            # חסימה והפניה: עובד מורשה להגיע אך ורק לעמוד שעון הנוכחות שלכם!
            return redirect(url_for('clock_in_out'))

        # הגנה כללית לכל תפקיד לא מוכר
        return redirect(url_for('unauthorized'))

    except Exception as e:
        import traceback
        print(f"❌ CRITICAL ERROR in employee_dashboard router logic: {e}")
        traceback.print_exc()
        return redirect(url_for('unauthorized'))


# -----------------------------
#  COMPANY FOLDERS Translation (Threading + MULTI COMPANY)
# -----------------------------

def ensure_company_folder(company_id):
    base_dir = app.config.get("COMPANIES_DIR") or os.path.join(app.root_path, "companies")
    company_dir = os.path.join(base_dir, str(company_id))
    return company_dir


def translate_company_in_background(
    company_id, name, id_number, deduction_file, address, city, postal_code, phone, email, logo, source_lang="he"
):
    # שומרים את האפליקציה הנוכחית כדי להשתמש בה בתוך הטרד
    app_instance = current_app._get_current_object()

    def background_worker():
        with app_instance.app_context():
            # העברת ה-source_lang המפורש לתוך מנוע הריצה
            run_company_translation(
                company_id, name, id_number, deduction_file, address, city, postal_code, phone, email, logo, source_lang
            )

    thread = threading.Thread(target=background_worker)
    thread.daemon = True
    thread.start()


def run_company_translation(
    company_id, name, id_number, deduction_file, address, city, postal_code, phone, email, logo, source_lang="he"
):
    try:
        import time

        # הזרקה קשיחה של שפת המקור לתוך מנוע ה-MyMemory החדש למניעת פאלבק לעברית
        name_trans = generate_translations(name or "", source_lang=source_lang)
        time.sleep(0.4)

        address_trans = generate_translations(address or "", source_lang=source_lang)
        time.sleep(0.4)

        city_trans = generate_translations(city or "", source_lang=source_lang)

        def clean_translation_dict(trans_dict, original_text, src_l):
            if not trans_dict:
                return {src_l: original_text}
            
            # אבטחת ברזל: שפת המקור מקבלת תמיד את הטקסט המקורי הגולמי שהמשתמש הקליד בטופס
            trans_dict[src_l] = original_text
            
            if "iw" in trans_dict:
                trans_dict["he"] = trans_dict["iw"]
            for lang, val in list(trans_dict.items()):
                if val and ("Error 500" in val or "That’s an error" in val or "Please try again later" in val):
                    trans_dict[lang] = original_text
            return trans_dict

        name_clean    = clean_translation_dict(name_trans, name or "", source_lang)
        address_clean = clean_translation_dict(address_trans, address or "", source_lang)
        city_clean    = clean_translation_dict(city_trans, city or "", source_lang)

        # שימוש בפונקציית השמירה שמנהלת את הנתיבים בצורה מושלמת
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

    folder = ensure_company_folder(company_id)
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

    # התאמה דו-כיוונית מלאה לקודים שהמנוע מייצר (כמו zh-CN ו-iw) כדי למנוע לצמיתות נפילה לפאלבק של עברית
    special_mappings = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "en": "en",
        "he": "he",
        "iw": "he"
    }
    lookup_lang = special_mappings.get(str(language).lower().strip(), language)

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
        
        # שליפה חכמה ומדורגת במעלה עץ השפות כדי לחסל לצמיתות את תקלות השפה
        return field_data.get(lookup_lang) or field_data.get(language) or field_data.get("he") or field_data.get("iw") or default_val or ""

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


def translate_customer_in_background(customer_id, company_id, name, address, city, message, source_lang="he"):
    # שומרים את אובייקט האפליקציה האקטיבי של Flask לפני פתיחת הטרד
    app_instance = current_app._get_current_object()

    def background_worker():
        # מזריקים את הקונטקסט בצורה קשיחה לתוך הטרד כדי לאפשר גישה ל-app.config ללא קריסות!
        with app_instance.app_context():
            customer = Customer.query.filter_by(id=customer_id, company_id=company_id).first()
            if not customer:
                print(f"⚠ translate_customer_in_background: customer {customer_id} not found")
                return

            local_id = customer.local_id
            if not local_id:
                return

            # הרצת התרגום האמיתי עם שפת המקור האמיתית מתוך הקוקיז
            run_customer_translation(company_id, local_id, name, address, city, message, source_lang)

    thread = threading.Thread(target=background_worker)
    thread.daemon = True
    thread.start()


def run_customer_translation(company_id, local_id, name, address, city, message, source_lang="he"):
    try:
        # קריאה מפורשת למנוע התרגום החדש עם שפת המקור האמיתית מהקוקיז של הדפדפן
        name_trans    = generate_translations(name or "", source_lang=source_lang)
        address_trans = generate_translations(address or "", source_lang=source_lang)
        city_trans    = generate_translations(city or "", source_lang=source_lang)
        message_trans = generate_translations(message or "", source_lang=source_lang)

        def clean_translation_dict(trans_dict, original_text, src_l):
            if not trans_dict:
                return {src_l: original_text}
            
            # אבטחת ברזל: שפת המקור מקבלת תמיד את הטקסט המקורי הגולמי שהמשתמש הקליד בטופס
            trans_dict[src_l] = original_text
            
            if "iw" in trans_dict:
                trans_dict["he"] = trans_dict["iw"]
            return trans_dict

        name_clean    = clean_translation_dict(name_trans, name or "", source_lang)
        address_clean = clean_translation_dict(address_trans, address or "", source_lang)
        city_clean    = clean_translation_dict(city_trans, city or "", source_lang)
        message_clean = clean_translation_dict(message_trans, message or "", source_lang)

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
    # התאמה דו-כיוונית מלאה לקודים שהמנוע מייצר (כמו zh-CN ו-iw) כדי למנוע לצמיתות נפילה לפאלבק
    special_mappings = {
        "zh": "zh-CN",
        "zh-cn": "zh-CN",
        "en": "en",
        "he": "he",
        "iw": "he"
    }
    lookup_lang = special_mappings.get(str(language).lower().strip(), language)

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
        
        # שליפה חכמה ומדורגת במעלה עץ השפות כדי לחסל לצמיתות את תקלות השפה
        return field_data.get(lookup_lang) or field_data.get(language) or field_data.get("he") or field_data.get("iw") or default_val or ""

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
    base_dir = app.config.get("EMPLOYEES_DIR", "static/employees")
    folder = os.path.join(base_dir, f"company_{company_id}", str(local_id))
    return folder


def translate_employee_in_background(employee_id, company_id, name, address, city, message, mobile_phone, id_number, postal_code):
    employee = Employee.query.filter_by(id=employee_id, company_id=company_id).first()
    if not employee:
        print(f"⚠ translate_employee_in_background: employee {employee_id} not found")
        return

    local_id = employee.local_id

    thread = threading.Thread(
        target=run_employee_translation,
        args=(company_id, local_id, name, address, city, mobile_phone, id_number, postal_code, message)
    )
    thread.daemon = True
    thread.start()


def run_employee_translation(company_id, local_id, name, address, city, mobile_phone, id_number, postal_code, message):
    try:
        name_trans    = generate_translations(name or "")
        address_trans = generate_translations(address or "")
        city_trans    = generate_translations(city or "")
        mobile_phone_trans    = generate_translations(mobile_phone or "")
        id_number_trans = generate_translations(id_number or "")
        postal_code_trans    = generate_translations(postal_code or "")
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
        mobile_phone_clean    = clean_translation_dict(mobile_phone_trans, mobile_phone or "")
        id_number_clean = clean_translation_dict(id_number_trans, id_number or "")
        postal_code_clean    = clean_translation_dict(postal_code_trans, postal_code or "")
        message_clean = clean_translation_dict(message_trans, message or "")

        save_employee_file(company_id, local_id, name_clean, address_clean, city_clean, mobile_phone_clean, id_number_clean, postal_code_clean, message_clean)

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
    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(language, language)

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
        "mobile_phone": get_val("mobile_phone", fallback_data["mobile_phone"]),
        "id_number": get_val("id_number", fallback_data["id_number"]),
        "message": get_val("message", fallback_data["message"]),
        "postal_code": get_val("postal_code", fallback_data["postal_code"])
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

    #  אם יש ID של ספק אבל השם הגיע ריק, נשלוף את השם האמיתי מה-DB לפני שכותבים לקובץ!
    if supplier_id and not supplier_name:
        db_supplier = Supplier.query.filter_by(id=int(supplier_id), company_id=company_id).first()
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

    # מבטיח שאפילו אם קובץ ה-JSON עדיין לא נוצר או ריק - המסך לעולם לא יציג זבל או קופסאות ריקות!
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





# -----------------------------------------------------------
#  Company Views & Management (Multi-Tenant Secure)
# -----------------------------------------------------------

@app.route('/company', methods=['GET', 'POST'])
@login_required
def company():
    try:
        # שליפת השפה הפעילה של הטופס למניעת כפילויות עברית בקובצי ה-JSON
        language = get_lang()

        # קביעת החברה הפעילה
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            active_company_id = OWNER_COMPANY_ID
            current_user.company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        company_obj = db.session.get(Company, active_company_id)

        # -------- GET: טוען חברה מתורגמת --------
        if request.method == 'GET':
            company_i18n = load_company_translated(company_obj, language)
            return render_template(
                'company.html',
                company=company_i18n,
                company_db=company_obj
            )

        # -------- POST: עדכון / יצירת חברה --------
        form_name = request.form.get('name', '').strip()
        if not form_name:
            flash("שם חברה הוא שדה חובה", "warning")
            return redirect(url_for('company'))

        # אבטחת מחרוזות נקיות למניעת שגיאות NoneType בשרת
        company_data = {
            "name": form_name,
            "company_id_number": request.form.get('company_id_number', '').strip(),
            "deduction_file": request.form.get('deduction_file', '').strip(),
            "address": request.form.get('address', '').strip(),
            "city": request.form.get('city', '').strip(),
            "postal_code": request.form.get('postal_code', '').strip(),
            "phone": request.form.get('phone', '').strip(),
            "email": request.form.get('email', '').strip(),
            "logo": request.form.get('logo', '').strip()
        }

        # -------- ענף בעלים (OWNER COMPANY - חברה 1) --------
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            if not company_obj:
                company_obj = Company(id=OWNER_COMPANY_ID, **company_data)
                db.session.add(company_obj)
            else:
                for key, value in company_data.items():
                    setattr(company_obj, key, value)

            db.session.commit()
            flash("נתוני חברת הבעלים עודכנו בהצלחה!", "success")

        # -------- ענף חברה רגילה (חברות 2 ומעלה) --------
        else:
            if company_obj:
                # עדכון חברה קיימת
                for key, value in company_data.items():
                    setattr(company_obj, key, value)
                db.session.commit()
                flash("נתוני החברה עודכנו בבסיס הנתונים!", "success")
            else:
                # יצירת חברה חדשה
                company_obj = Company(**company_data)
                db.session.add(company_obj)
                db.session.commit()

                current_user.company_id = company_obj.id
                session['company_id'] = company_obj.id
                db.session.commit()

                flash("חברה חדשה נוצרה בהצלחה!", "success")

        # -------- תרגום חברה ברקע מבוסס שפת מקור דינמית --------
        print(f"DEBUG: Launching clean background translation file for Company ID: {company_obj.id}")
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
            logo=company_obj.logo,
            source_lang=get_lang()  
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
        # שליפת השפה הפעילה כדי להעביר אותה למנגנון התרגום הנקי ברקע
        language = get_lang()

        # קביעת החברה הפעילה בדיוק לפי חוקי הברזל של ה-POST
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            company_id = OWNER_COMPANY_ID
        else:
            company_id = current_user.company_id

        if not company_id:
            flash("No active company.", "warning")
            return redirect(url_for('company'))

        # שימוש ב-COMPANIES_DIR המדויק ליצירת נתיב מוחלט וחסין אש לתיקיית החברה
        comp_base_dir = app.config.get("COMPANIES_DIR") or os.path.join(app.root_path, "companies")
        folder_path = os.path.join(comp_base_dir, str(company_id))
        file_path = os.path.join(folder_path, f"{company_id}.json")

        file_deleted = False
        if os.path.exists(file_path):
            os.remove(file_path)
            file_deleted = True

        # שליפת אובייקט החברה מבסיס הנתונים
        company_obj = db.session.get(Company, company_id)
        if company_obj:
            if company_id == OWNER_COMPANY_ID:
                # חוק הברזל שלך: אם זה ה-Owner (חברה 1) - מחזירים לערכי ברירת המחדל המקוריים כדי למנוע קריסות רינדור!
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
                # אם זה מנהל רגיל (חברה 2 ומעלה) - מאפסים את כל השדות למחרוזת ריקה כפי שדרשת
                company_obj.company_id_number = ""
                company_obj.deduction_file = ""
                company_obj.address = ""
                company_obj.city = ""
                company_obj.postal_code = ""
                company_obj.phone = ""
                company_obj.email = ""
                company_obj.logo = ""
            
            db.session.commit()

        # >>> בנייה מיידית מחדש של קובץ ה-JSON בדיסק לכל 30 השפות <<<
        if company_obj:
            print(f"DEBUG CLEAR: Re-triggering clean translation file for Company ID: {company_obj.id}")
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
                logo=company_obj.logo,
                source_lang=language
            )

        if file_deleted:
            flash("נתוני וקובץ התרגומים של החברה נמחקו ואופסו בהצלחה.", "success")
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
# HELPER Invoice Data (Multi-Tenant Secure)
# ----------------------

def base_invoice_context(customer_id=None):
    language = get_lang()

    # ----------------- COMPANY -----------------
    if customer_id is None and session.get('role') == 'customer':
        customer_id = session.get('customer_id')

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    company_obj = db.session.get(Company, company_id)
    company_translated = load_company_translated(company_obj, language) if company_obj else {}

    # ----------------- PRODUCTS -----------------
    products_list_for_js = []
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

    # ----------------- ALL CUSTOMERS -----------------
    all_customers_json = []
    for c in Customer.query.filter_by(company_id=company_id).all():
        trans = load_customer_translated(c, language)

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

    if customer_id is not None:
        if str(customer_id) == "0":
            if company_obj:
                customer_data = {
                    "id": "0",
                    "local_id": None,
                    "customer_name": f"★ {company_translated.get('name', company_obj.name)}",
                    "id_number": company_obj.company_id_number or "",
                    "address": company_obj.address or "",
                    "city": company_obj.city or "",
                    "postal_code": company_obj.postal_code or "",
                    "phone": company_obj.phone or "",
                    "email": company_obj.email or "",
                    "message": ""
                }
        else:
            c_obj = Customer.query.filter_by(id=customer_id, company_id=company_id).first()
            if c_obj:
                trans = load_customer_translated(c_obj, language)
                customer_data = {
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

    # ----------------- CURRENCY METADATA INJECTION -----------------
    # זיהוי המטבע המקורי של החברה/מסמך ומטבע היעד להמרות דינמיות בצד הלקוח (JS)
    from flask import request
    invoice_original_currency = getattr(company_obj, 'currency', 'ILS') if company_obj else 'ILS'
    current_currency = request.cookies.get('currency') or session.get('currency') or invoice_original_currency

    # ----------------- RETURN CONTEXT -----------------
    return {
        "products": products_list_for_js,
        "customer": customer_data,
        "all_customers_json": all_customers_json,
        "company": company_translated,
        "company_db": company_obj,
        "vat_options": list(range(0, 21)),
        "next_invoice_num": get_next_invoice_number(company_id=company_id),
        "invoice_original_currency": invoice_original_currency,
        "current_currency": current_currency
    }

# --------------------
# Get Invoice Context Form Data (Multi-Tenant Secure)
# ----------------------

def invoice_context(invoice_id=None):
    try:
        language = get_lang()

        # קביעת מזהה החברה האקטיבית
        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            company_id = OWNER_COMPANY_ID
        else:
            company_id = current_user.company_id

        # ----------------- LOAD INVOICE -----------------
        invoice = None
        if invoice_id:
            invoice = db.session.get(Invoice, invoice_id)
            if invoice and invoice.company_id != company_id:
                invoice = None

        # החברה של החשבונית (אם יש)
        target_company_id = invoice.company_id if invoice else company_id

        # ----------------- LOAD COMPANY -----------------
        company_obj = db.session.get(Company, target_company_id)
        company_translated = load_company_translated(company_obj, language) if company_obj else {}

        # ----------------- CUSTOMER JSON -----------------
        customer_json = {}

        if invoice and invoice.customer_id is not None:

            # SELF-INVOICE → החברה היא הלקוח
            if str(invoice.customer_id) == "0":
                customer_json = {
                    "id": "0",
                    "local_id": None,
                    "customer_name": f"★ {company_translated.get('name', company_obj.name if company_obj else '')}",
                    "address": company_translated.get('address', company_obj.address or ""),
                    "city": company_translated.get('city', company_obj.city or ""),
                    "postal_code": company_obj.postal_code or "",
                    "id_number": getattr(company_obj, 'company_id_number', ""),
                    "phone": company_obj.phone or "",
                    "email": company_obj.email or "",
                    "message": ""
                }

            else:
                c_obj = Customer.query.filter_by(
                    id=invoice.customer_id,
                    company_id=target_company_id
                ).first()

                if c_obj:
                    trans = load_customer_translated(c_obj, language, target_company_id)
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

        # ----------------- ITEMS JSON -----------------
        items_json = []
        if invoice:
            items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()

            for item in items:
                target_p = None
                if str(item.product_id).isdigit():
                    target_p = Product.query.filter_by(
                        local_id=int(item.product_id),
                        company_id=target_company_id
                    ).first()

                row_sku = str(item.product_id)
                row_name_trans = item.description or ""

                if target_p:
                    translated_p_data = load_item_translated(target_p, language, target_company_id)
                    item_file_data = load_item_file(target_company_id, target_p.local_id) or {}

                    row_sku = target_p.sku if target_p.sku else item_file_data.get("sku", str(target_p.local_id))
                    row_name_trans = translated_p_data.get("name") or target_p.name or ""

                items_json.append({
                    "product_id": item.product_id,
                    "sku": row_sku,
                    "name": row_name_trans,
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "discount": float(item.discount or 0),
                    "total_price": float(item.total_price or 0),
                    "cost_price": float(getattr(item, 'cost_price_at_time', 0.0) or 0.0)
                })

        # ----------------- ALL CUSTOMERS JSON -----------------
        all_customers = Customer.query.filter_by(company_id=target_company_id).all()

        all_customers_json = []
        for c in all_customers:
            trans = load_customer_translated(c, language, target_company_id)
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

        # ----------------- PRODUCTS JSON -----------------
        products_json = []
        my_products = Product.query.filter_by(company_id=target_company_id).all()

        for p in my_products:
            item_file = load_item_file(target_company_id, p.local_id if p.local_id else p.id) or {}
            translated_core = load_item_translated(p, language, target_company_id)

            p_name = translated_core.get("name") or p.name or ""
            i_cat = translated_core.get("income_category") or p.income_category or "product"

            total_sold = db.session.query(db.func.sum(InvoiceItem.quantity)) \
                .join(Invoice) \
                .filter(
                    InvoiceItem.product_id == str(p.local_id if p.local_id else p.id),
                    InvoiceItem.income_category == 'product',
                    Invoice.company_id == target_company_id,
                    Invoice.status != "canceled"
                ).scalar() or 0

            stock_in_json = item_file.get("stock_in")
            stock_in = int(stock_in_json) if stock_in_json is not None else (
                int(getattr(p, 'quantity', 0) or 0) + int(total_sold)
            )
            actual_quantity = stock_in - int(total_sold)

            products_json.append({
                "id": p.id,
                "local_id": p.local_id,
                "sku": p.sku if p.sku else item_file.get("sku", str(p.local_id if p.local_id else p.id)),
                "name": p_name,
                "price": float(p.price or 0),
                "cost_price": float(p.cost_price or 0),
                "quantity": actual_quantity,
                "income_category": i_cat
            })

        # ----------------- PAYMENTS JSON -----------------
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

        # ----------------- TOTALS -----------------
        sub_total = float(invoice.sub_total or 0) if invoice else 0.0
        vat_amount = float(invoice.vat_amount or 0) if invoice else 0.0
        grand_total = float(invoice.grand_total or 0) if invoice else 0.0
        discount_total = float(getattr(invoice, 'discount_total', 0) or 0) if invoice else 0.0
        vat_rate = float(invoice.vat_rate) if invoice and invoice.vat_rate is not None else 0.0

        # ----------------- CANCELLATION -----------------
        cancellation_reason_trans = ""
        if invoice and getattr(invoice, 'status', '') == 'canceled':
            cancel_file_data = load_cancellation_file(target_company_id, invoice.id) or {}
            reason_dict = cancel_file_data.get("cancellation_reason", {})

            cancellation_reason_trans = (
                reason_dict.get(language)
                or reason_dict.get("he")
                or getattr(invoice, 'cancellation_reason', '')
                or "ביטول כללי"
            )

        # ----------------- BASE CONTEXT -----------------
        base_ctx = base_invoice_context(
            customer_id=str(invoice.customer_id) if invoice else None
        )

        customer_final = base_ctx.get("customer", customer_json or {})

        language = get_lang()
        # מתאים לכל 30 השפות: אם זו לא עברית (או שפות מימין לשמאל שמשתמשות ב-ILS), נגדיר USD, או ניקח לפי המטבע של השפה
        app_currency = "ILS" if language in ["he", "ar"] else "USD"

        base_ctx.update({
            "invoice": invoice,
            "invoice_id": invoice.id if invoice else None,
            "allocation_number": invoice.allocation_number if invoice else None,
            "invoice_number": invoice.invoice_number if invoice else get_next_invoice_number(company_id=target_company_id),

            "invoice_date": (
                invoice.invoice_date.strftime('%Y-%m-%d')
                if invoice and invoice.invoice_date else datetime.today().strftime('%Y-%m-%d')
            ),

            # הוספת מפתחות המטבע עבור כל 30 השפות
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
            "transaction": Transaction.query.filter_by(
                invoice_id=invoice.invoice_number,
                company_id=target_company_id
            ).first() if invoice else None
        })

        return base_ctx

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ CRITICAL ERROR in invoice_context: {e}")

        if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
            company_id = OWNER_COMPANY_ID
        else:
            company_id = current_user.company_id

        company_obj = db.session.get(Company, company_id) if company_id else None
        
        language = get_lang()
        company_translated = load_company_translated(company_obj, language) if company_obj else {}
        app_currency = "ILS" if language in ["he", "ar"] else "USD"

        products_backup = []
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
        except:
            pass

        backup_reason = ""
        try:
            if invoice:
                backup_reason = load_cancellation_translated(invoice, language, company_id)
        except:
            if invoice:
                backup_reason = getattr(invoice, 'cancellation_reason', '') or ""

        return {
            "error": str(e),
            "invoice": None,
            "invoice_id": None,
            "company": company_translated,
            "company_db": company_obj,
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
            "invoice_number": get_next_invoice_number(company_id=company_id),
            "invoice_date": datetime.today().strftime('%Y-%m-%d'),
            
            "invoice_original_currency": "ILS",
            "current_app_currency": app_currency,
            
            "invoice_status": "active",
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
    invoice_id  = request.form.get("invoice_id")
    customer_id = request.form.get("customer_id")

    # ----------------- COMPANY CONTEXT -----------------
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

    # ----------------- CUSTOMER VALIDATION -----------------
    # SELF-INVOICE → החברה עצמה היא הלקוח
    if str(customer_id) == "0":
        checked_customer = True
    else:
        try:
            customer_id_int = int(customer_id)
        except ValueError:
            flash("שגיאה: מזהה לקוח אינו חוקי", "error")
            return redirect(url_for('invoice'))

        checked_customer = Customer.query.filter_by(
            id=customer_id_int,
            company_id=company_id
        ).first()

    if not checked_customer:
        flash("שגיאה: אין לך הרשאה לגשת לנתוני לקוח זה", "error")
        return redirect(url_for('invoice'))

    # ----------------- BASIC TOTALS -----------------
    sub_total   = clean_float(request.form.get('sub_total'))
    vat_amount  = clean_float(request.form.get('vat_amount'))
    grand_total = clean_float(request.form.get('grand_total'))

    vat_rate_raw = request.form.get('vat_rate_select')
    vat_rate = clean_float(vat_rate_raw) if vat_rate_raw not in [None, "", "null"] else 0.0

    total_invoice_cost = 0.0

    # ----------------- DUPLICATE INVOICE NUMBER CHECK (NEW ONLY) -----------------
    if not invoice_id:
        invoice_number_check = get_next_invoice_number(company_id=company_id)
        existing_invoice = Invoice.query.filter_by(
            company_id=company_id,
            invoice_number=invoice_number_check
        ).first()
        if existing_invoice:
            flash(f"שגיאה: חשבונית מספר {invoice_number_check} כבר שמורה ומאובטחת במערכת.", "error")
            return redirect(url_for('invoice'))

    # 1. עדכון חשבונית קיימת (מצב עריכה)
    if invoice_id:
        invoice = db.session.get(Invoice, int(invoice_id))
        if not invoice or invoice.company_id != company_id:
            flash("החשבונית המבוקשת לעריכה אינה קיימת במערכת שלך", "error")
            return redirect(url_for('invoice'))

        if invoice.status in ["canceled", "מבוטלת"]:
            flash("שגיאה חמורה: לא ניתן לערוך או לשמור חשבונית מבוטלת.", "error")
            return redirect(url_for('invoice_view', invoice_id=invoice.id))

        # ---------- שלב א': החזרת מלאי ישן ----------
        old_items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
        for old_item in old_items:
            if old_item.product_id in ['rent', 'stocks', 'dividend', 'unspecified', 'deleted']:
                continue

            if not str(old_item.product_id).isdigit():
                continue

            prod = Product.query.filter_by(
                local_id=int(old_item.product_id),
                company_id=company_id
            ).first()
            if not prod:
                prod = Product.query.filter_by(
                    id=int(old_item.product_id),
                    company_id=company_id
                ).first()
            if not prod:
                continue

            item_file_old = load_item_file(company_id, prod.local_id if prod.local_id is not None else prod.id) or {}
            i_cat_old = item_file_old.get("income_category", getattr(prod, 'income_category', 'service'))

            if i_cat_old == 'product':
                prod.quantity += old_item.quantity
                db.session.flush()

                current_stock_out_old = int(item_file_old.get("stock_out", 0))
                new_stock_out = max(0, current_stock_out_old - int(old_item.quantity))
                existing_batches_old = item_file_old.get("batches", [])

                save_item_file(
                    company_id=company_id,
                    local_id=prod.local_id if prod.local_id is not None else prod.id,
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

        # ---------- שלב ב': עדכון כותרת חשבונית והמטבע הפעיל ----------
        invoice.customer_id = customer_id if str(customer_id) == "0" else int(customer_id)
        invoice.sub_total   = sub_total
        invoice.vat_amount  = vat_amount
        invoice.grand_total = grand_total
        invoice.vat_rate    = vat_rate
        invoice.status      = "active"
        
        if not invoice.allocation_number:
            invoice.allocation_number = generate_allocation_number()

        InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()
        Payment.query.filter_by(invoice_id=invoice.id).delete()
        db.session.flush()

        # ---------- שלב ג': פריטים חדשים ----------
        items = request.form.getlist('items[]')
        for item_json in items:
            try:
                item_data = json.loads(item_json)
            except Exception:
                continue

            p_id_val = item_data['product_id']
            qty      = clean_float(item_data.get('quantity'))
            u_price  = clean_float(item_data.get('price'))
            disc     = clean_float(item_data.get('discount', 0))

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
            if not prod and str(p_id_val).isdigit():
                prod = Product.query.filter_by(local_id=int(p_id_val), company_id=company_id).first()
            if not prod:
                continue

            db_product_id = prod.local_id if prod.local_id is not None else prod.id

            item_file = load_item_file(company_id, prod.local_id if prod.local_id is not None else prod.id) or {}
            i_cat     = item_file.get("income_category", getattr(prod, 'income_category', 'service'))
            c_price   = float(item_file.get("cost_price", prod.cost_price or 0.0))

            if not item_description:
                item_description = item_file.get("name", {}).get("he", prod.name)

            if i_cat == 'product':
                prod.quantity -= qty
                db.session.flush()

                existing_batches       = item_file.get("batches", [])
                current_stock_out_now  = int(item_file.get("stock_out", 0))
                new_stock_out          = current_stock_out_now + int(qty)

                save_item_file(
                    company_id=company_id,
                    local_id=prod.local_id if prod.local_id is not None else prod.id,
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

        # ---------- שלב ד': תשלומים ----------
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
                branch=branches[i] if i < len(branches) else "",
                account_number=accounts[i] if i < len(accounts) else ""
            ))

        # ---------- שלב ה': Transaction ----------
        existing_trans = Transaction.query.filter_by(
            invoice_id=invoice.invoice_number,
            company_id=company_id
        ).first()

        if existing_trans:
            existing_trans.date               = invoice.invoice_date
            existing_trans.description        = f"חשבונית #{invoice.invoice_number}"
            existing_trans.amount             = sub_total
            existing_trans.customer_id        = customer_id if str(customer_id) == "0" else int(customer_id)
            existing_trans.cost_price_at_time = total_invoice_cost
        else:
            max_local_trans = db.session.query(db.func.max(Transaction.local_id))\
                .filter(Transaction.company_id == company_id).scalar()
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
                customer_id=customer_id if str(customer_id) == "0" else int(customer_id),
                cost_price_at_time=total_invoice_cost,
                quantity=1
            ))

        db.session.commit()
        flash('החשבונית עודכנה בהצלחה והמלאי סונכרן', 'success')
        return redirect(url_for('invoice_view', invoice_id=invoice.id))

    # 2. יצירת חשבונית חדשה (מצב יצירה)
    else:
        invoice_number = get_next_invoice_number(company_id=company_id)

        new_invoice = Invoice(
            company_id=company_id,
            invoice_number=invoice_number,
            invoice_date=datetime.today().date(),
            customer_id=customer_id if str(customer_id) == "0" else int(customer_id),
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
            qty      = clean_float(item_data.get('quantity'))
            u_price  = clean_float(item_data.get('price'))
            disc     = clean_float(item_data.get('discount', 0))

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
            if not prod and str(p_id_val).isdigit():
                prod = Product.query.filter_by(local_id=int(p_id_val), company_id=company_id).first()
            if not prod:
                continue

            db_product_id = prod.local_id if prod.local_id is not None else prod.id

            item_file = load_item_file(company_id, prod.local_id if prod.local_id is not None else prod.id) or {}
            i_cat     = item_file.get("income_category", getattr(prod, 'income_category', 'service'))
            c_price   = float(item_file.get("cost_price", prod.cost_price or 0.0))

            if not item_description:
                item_description = item_file.get("name", {}).get("he", prod.name)

            if i_cat == 'product':
                prod.quantity -= qty
                db.session.flush()

                existing_batches       = item_file.get("batches", [])
                current_stock_out_new  = int(item_file.get("stock_out", 0))
                new_stock_out          = current_stock_out_new + int(qty)

                save_item_file(
                    company_id=company_id,
                    local_id=prod.local_id if prod.local_id is not None else prod.id,
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
                branch=branches[i] if i < len(branches) else "",
                account_number=accounts[i] if i < len(accounts) else ""
            ))

        db.session.flush()

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
            invoice_id=new_invoice.invoice_number,
            customer_id=customer_id if str(customer_id) == "0" else int(customer_id),
            cost_price_at_time=total_invoice_cost,
            quantity=1
        )
        db.session.add(new_trans)

        db.session.commit()
        flash('החשבונית הופקה בהצלחה והמלאי עודכן', 'success')
        return redirect(url_for('invoice_view', invoice_id=new_invoice.id))


# --------------------
# טופס יצירת/עריכת חשבונית (Multi-Tenant Secure via invoice_context)
# ----------------------

@app.route('/invoice/create', methods=['GET'])
@login_required
def invoice():
    invoice_id = request.args.get('invoice_id')

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

    ctx = invoice_context(invoice_id)
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

    ctx = invoice_context(invoice_id)

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

    invoice = db.session.get(Invoice, invoice_id)

    if not invoice or invoice.company_id != company_id:
        flash("המסמך המבוקש אינו קיים במערכת שלך", "error")
        return redirect(url_for('invoice_data'))

    if invoice.status in ["canceled", "מבוטלת"]:
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    # שלב א': החזרת מלאי ועדכון קבצי ה-JSON של הפריטים בחשבונית
    items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()

    for item in items:
        if item.product_id in ['rent', 'stocks', 'dividend', 'unspecified', 'deleted']:
            continue

        # product_id נשמר כמחרוזת של local_id/id → להמיר ל-int
        if not str(item.product_id).isdigit():
            continue

        prod = Product.query.filter_by(
            local_id=int(item.product_id),
            company_id=company_id
        ).first()
        if not prod:
            prod = Product.query.filter_by(
                id=int(item.product_id),
                company_id=company_id
            ).first()
        if not prod:
            continue
        
        if getattr(prod, 'income_category', 'service') == 'product':
            prod.quantity += item.quantity
            db.session.flush()
            
            product_local_id = prod.local_id if prod.local_id is not None else prod.id
            item_file = load_item_file(company_id, product_local_id) or {}

            current_json_stock_out = int(item_file.get("stock_out", 0))
            actual_out_calc = max(0, current_json_stock_out - int(item.quantity))

            existing_batches = _load_inventory_batches(company_id, product_local_id)

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

    # שלב ב': עדכון התנועה הפיננסית (Transaction) במקום מחיקה
    current_lang = get_lang()
    default_text = "General Cancellation" if current_lang != "he" else "ביטול כללי"
    invoice_reason = request.form.get("cancel_reason", "").strip() or default_text

    # כאן חייבים להשתמש ב-invoice_number, כמו בכל המערכת
    trans = Transaction.query.filter_by(
        invoice_id=invoice.invoice_number,
        company_id=company_id
    ).first()

    if trans:
        trans.amount = 0.0
        trans.cost_price_at_time = 0.0
        if current_lang != "he":
            trans.description = f"Invoice #{invoice.invoice_number} (Canceled: {invoice_reason})"
        else:
            trans.description = f"חשבונית #{invoice.invoice_number} (מבוטלת: {invoice_reason})"

    # שלב ג': שינוי סטטוס החשבונית לביטול
    invoice.status = "canceled"

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

    flash("החשבונית בוטלה בהצלחה ותנועות המס עודכנו", "success")
    return redirect(url_for('invoice_view', invoice_id=invoice.id))


# ----------------------
# Show All Invoices invoice_data Page
# ----------------------

# עדכון שלב ההחזרה ב-Flask Route כדי להבטיח שגם העוגיות (Cookies) וגם ה-Session מעודכנים כראוי לפי בחירת המשתמש
@app.route('/invoices')
@login_required
def invoice_data():
    language = get_lang()    
    search = request.args.get("q", "").strip().lower()
    selected_month = request.args.get("month", "")
    selected_year = request.args.get("year", "")
    selected_status = request.args.get("status", "all")

    if not selected_year:
        selected_year = str(datetime.today().year)
    if not selected_month:
        selected_month = datetime.today().strftime('%m')

    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        company_id = OWNER_COMPANY_ID
    else:
        company_id = current_user.company_id

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

    for inv in invoices:
        trans_name = ""
        db_name = ""
        
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
            row_profit = 0.0
            
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

    # שליפה מאובטחת של מטבע המקור מהחברה ומטבע היעד מתוך עוגיות ה-Cookie או ה-Session
    invoice_original_currency = getattr(company_obj, 'currency', None) or "ILS"
    current_currency = request.cookies.get('currency') or session.get('currency') or invoice_original_currency

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
        years=[str(y) for y in range(2024, 2031)], 
        customer_i18n_list=customer_i18n_list, 
        business_categories=business_categories,
        language=language, 
        company=company_translated, 
        company_db=company_obj,
        invoice_original_currency=invoice_original_currency,
        current_currency=current_currency
    ))
    
    # הצמדת מטבע היעד לעוגיות דפדפן כדי שסקריפט ה-JS יקרא אותו מיידית
    response.set_cookie('currency', current_currency)
    return response

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

            total_sold = db.session.query(func.sum(InvoiceItem.quantity)) \
                .join(Invoice) \
                .filter(
                    InvoiceItem.product_id == str(target_local_id),
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

        #   Self Invoice (חשבונית עצמית)
        if customer_id == '0' or (not customer_name and not email_input):
            print("📝 Self Invoice mode detected (Customer ID = 0 / None). Skipping standard creation workflow.")
            flash('מצב חשבונית עצמית עבר בהצלחה', 'success')
            return redirect(url_for('customer'))

        # ------------------ UPDATE EXISTING CUSTOMER ------------------
        if customer_id:
            customer_obj = Customer.query.filter_by(
                id=customer_id,
                company_id=active_company_id
            ).first()

            if customer_obj:
                old_name    = customer_obj.customer_name
                old_address = customer_obj.address
                old_city    = customer_obj.city
                old_message = customer_obj.message

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

                if customer_obj.local_id:
                    folder = ensure_customer_folder(active_company_id, customer_obj.local_id) if 'ensure_customer_folder' in globals() else os.path.join(app.config['CUSTOMERS_DIR'], f"{active_company_id}_{customer_obj.local_id}")
                    if folder:
                        os.makedirs(folder, exist_ok=True)
                        file_path = os.path.join(folder, "customer.json")
                        
                        existing_data = {}
                        if os.path.isfile(file_path):
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    content = f.read().strip()
                                    if content:
                                        existing_data = json.loads(content)
                            except:
                                existing_data = {}
                        
                        existing_data.setdefault("name", {})["he"] = customer_name
                        existing_data.setdefault("address", {})["he"] = customer_obj.address or ""
                        existing_data.setdefault("city", {})["he"] = customer_obj.city or ""
                        existing_data.setdefault("message", {})["he"] = customer_obj.message or ""
                        
                        with open(file_path, "w", encoding="utf-8") as f:
                            json.dump(existing_data, f, ensure_ascii=False, indent=4)

                        is_text_changed = (old_name != customer_name or 
                                           old_address != customer_obj.address or 
                                           old_city != customer_obj.city or 
                                           old_message != customer_obj.message)

                        if is_text_changed:
                            print(f"✔ Content changed. Spawning single translation thread for customer update.")
                            translate_customer_in_background(
                                customer_id=customer_obj.id,
                                company_id=active_company_id,
                                name=customer_obj.customer_name,
                                address=customer_obj.address,
                                city=customer_obj.city,
                                message=customer_obj.message,
                                source_lang=get_lang()
                            )
                        else:
                            print(f"✔ No textual content changed. Existing translations preserved. Zero threads spawned.")

                flash('הנתונים עודכנו בהצלחה!', 'success')

        # ------------------ CREATE NEW CUSTOMER ------------------
        else:
            duplicate = Customer.query.filter(
                (Customer.company_id == active_company_id) &
                ((Customer.email == email_input) | (Customer.customer_name == customer_name))
            ).first()

            if duplicate:
                flash("לקוח עם אימייל זה או שם זה כבר קיים במערכת שלך!", "warning")
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
                        logo="",
                        source_lang=get_lang()
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

            #  תיקון הזהב: המזהה id=user_obj.id הוחזר למקומו כדי לאפשר שמירה תקינה בבסיס הנתונים
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

            try:
                folder = os.path.join(app.config['CUSTOMERS_DIR'], f"{active_company_id}_{next_local_id}")
                os.makedirs(folder, exist_ok=True)
            except Exception as e:
                print(f"⚠ Failed to create customer folder: {e}")

            translate_customer_in_background(
                customer_id=new_customer.id,
                company_id=active_company_id,
                name=new_customer.customer_name,
                address=new_customer.address,
                city=new_customer.city,
                message=new_customer.message,
                source_lang=get_lang()  
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

        # הגנה מוחלטת: מכיוון שחברת הבעלים (חברה 1) לא משתמשת ב-local_id אלא מזהה את הלקוחות ישירות לפי ה-id הראשי שלהם,
        # אנחנו מחפשים קודם כל לפי המפתח הראשי הבלתי תלוי, ורק אז מבצעים פאלבק ל-local_id.
        c = Customer.query.filter_by(
            id=customer_id,
            company_id=active_company_id
        ).first()

        if not c:
            c = Customer.query.filter_by(
                local_id=customer_id,
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


# ---------------------------------------------------------------------------
#   Build All Employees Form employee.html (Multi-Tenant Secure)
# ---------------------------------------------------------------------------

@app.route('/employee', methods=['GET', 'POST'])
@login_required
def employee():
    today_str = datetime.today().strftime('%Y-%m-%d')
    language  = get_lang()

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

        # ------------------ UPDATE CUSTOMER (טבלת Employee + EmployeeData) ------------------
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
                employee_obj.mobile_phone    = request.form.get('phone')
                employee_obj.email           = email_input
                employee_obj.contract_status = request.form.get('contract_status')
                employee_obj.message         = request.form.get('message')

                #  סנכרון עדכון  EmployeeData
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
                mobile_phone=request.form.get('phone'),
                email=email_input,
                contract_status=request.form.get('contract_status'),
                message=request.form.get('message'),
                role='employee',
                is_active=True
            )
            db.session.add(new_employee)

            #   שמירה מבוצרת לטבלת EmployeeData 
            new_salary_emp = EmployeeData(
                id=user_obj.id,
                company_id=active_company_id,
                local_id=next_local_id,
                employee_id=user_obj.id, 
                employee_name=employee_name,
                id_number=id_number,
                address=request.form.get('address'),
                city=request.form.get('city'),
                postal_code=request.form.get('postal_code'),
                mobile_phone=request.form.get('phone'), 
                email=email_input,
                date=today_str,
                
                # NOT NULL עבור שדות המספרים
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
                tax_point_child="",
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


# ---------------------------------------------------------------------------
# Search Employees Engine (employee.html) 
# ---------------------------------------------------------------------------

@app.route('/search_employee_data', methods=['GET', 'POST'])
@login_required
def search_employee_data():
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
                "message": trans.get("message") or employee.message or "",
                "mobile_phone": trans.get("mobile_phone") or getattr(c, "phone", "") or "",
                "postal_code": trans.get("postal_code") or getattr(c, "postal_code", "") or ""
            }

        employee_i18n_list = {}
        for c in all_employees:
            trans = load_employee_translated(c, language, company_id=active_company_id) or {}
            
            payload = {
                "name": trans.get("name") or c.employee_name or "",
                "address": trans.get("address") or c.address or "",
                "city": trans.get("city") or c.city or "",
                "message": trans.get("message") or c.message or "",                
                "mobile_phone": trans.get("mobile_phone") or getattr(c, "phone", "") or "",
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

# ---------------------------------------------------------------------------
# Clear Employee Search (employee.html)
# ---------------------------------------------------------------------------

@app.route('/clear_employee_data', methods=['POST'])
@login_required
def clear_employee_data():
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

        # קביעת החברה האקטיבית (n8n יכול לשלוח company_id מותאם, או ברירת מחדל OWNER_COMPANY_ID)
        active_company_id = data.get("company_id", OWNER_COMPANY_ID)
        email_input = data.get("email", "").strip().lower()
        supplier_number = data.get("supplier_number", "").strip()

        # 2. בדיקת כפילויות בדיוק כמו בראוט הרגיל שלך
        duplicate = Supplier.query.filter(
            (Supplier.company_id == active_company_id) &
            ((Supplier.email == email_input) | (Supplier.supplier_name == supplier_name))
        ).first()
        if duplicate:
            return jsonify({"error": "Supplier with this email or name already exists in this company"}), 400

        if supplier_number and Supplier.query.filter_by(
            supplier_number=supplier_number, 
            company_id=active_company_id
        ).first():
            return jsonify({"error": "Supplier with this number already exists"}), 400

        # 3. חישוב ה-local_id הבא בתור לחברה הזו
        last_supplier = Supplier.query.filter_by(company_id=active_company_id)\
            .order_by(Supplier.local_id.desc()).first()
        next_local_id = 1 if not last_supplier else last_supplier.local_id + 1

        # עיבוד תאריך (n8n ישלח תאריך או שנשתמש בהיום)
        date_str = data.get("date", datetime.today().strftime('%Y-%m-%d'))
        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%d/%m/%Y')
        except Exception:
            formatted_date = date_str

        # 4. יצירת אובייקט הספק החדש עם כל השדות התואמים
        new_supplier = Supplier(
            company_id=active_company_id,
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

        # 5. יצירת תיקיית הספק בדיסק בדיוק לפי המנגנון שלך
        try:
            folder = os.path.join(
                app.config['SUPPLIERS_DIR'],
                f"{active_company_id}_{next_local_id}"
            )
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            print(f"⚠ n8n flow - Failed to create supplier folder: {e}")

        # 6. הפעלת מנגנון התרגום ברקע של המערכת שלך
        try:
            translate_supplier_in_background(
                supplier_id=new_supplier.id,
                company_id=active_company_id,
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
            
            #  הצלבה מדויקת לפי מספר חשבונית פנימי ומזהה חברה אקטיבית 
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

        # משתמשים אך ורק ב-invoice_number הרשמי ומזהה החברה האקטיבית (שדה future_invoice_id מכיל כאן את מספר החשבונית המקומי של החברה!)
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
        
        # מחקנו לחלוטין את ה-db.session.flush() המזיק שגרם לקפיצות מספרים בדאטהבייס
        db.session.commit()

        current_curr = get_currency() if 'get_currency' in globals() else 'ILS'
        try:
            translate_transaction_in_background(
                transaction_id=new_trans.id,
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
                #  אם אין תרגום, מצליבים לפי מספר חשבונית פנימי ומזהה חברה אקטיבית 
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
#  N8N NEW FOR GREAT AUTO Transaction API Endpoint 
# -----------------------------------------------------------

@app.route('/api/transaction/add_from_n8n', methods=['POST'])
def api_add_transaction_from_n8n():
    try:
        # 1. בדיקת אבטחה: אימות מפתח ה-API שהגיע מ-n8n
        auth_header = request.headers.get("X-API-KEY")
        if auth_header != N8N_API_KEY:
            return jsonify({"error": "Unauthorized API Access"}), 401

        # 2. קבלת ה-JSON שחולץ מהחשבונית על ידי ה-AI בתוך n8n
        data = request.get_json() or {}
        
        # וידוא שקיים סכום (שדה חובה)
        if "amount" not in data:
            return jsonify({"error": "Amount is required"}), 400

        # קביעת החברה האקטיבית (ברירת מחדל לחברה הראשית שלכם = 1)
        active_company_id = data.get("company_id", OWNER_COMPANY_ID)

        # 3. עיבוד תאריך (המרה מפורמט YYYY-MM-DD של ה-AI לאובייקט תאריך)
        date_str = data.get("date", "").strip()
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

        # 4. חישוב ה-local_id הבא בתור לחברה הזו (בדיוק לפי הלוגיקה שלכם)
        max_local = db.session.query(db.func.max(Transaction.local_id))\
            .filter(Transaction.company_id == active_company_id).scalar()
        next_local_id = (max_local or 0) + 1

        # 5. עיבוד שדות כספיים וקטגוריות
        raw_amount = float(data.get("amount", 0.0))
        raw_vat = float(data.get("vat_amount", 0.0))
        cost_price = float(data.get("cost_price", 0.0))
        
        raw_cat = data.get("category_id")
        category_id = int(raw_cat) if raw_cat and str(raw_cat).isdigit() else None
        
        raw_customer_id = data.get("customer_id")
        final_customer_id = "0" if str(raw_customer_id).strip() == "0" else (int(raw_customer_id) if raw_customer_id and str(raw_customer_id).isdigit() else None)

        final_description = data.get("description", "הוצאה אוטומטית מחשבונית מייל via n8n")

        # 6. יצירת אובייקט התנועה החדש (תמיד מסוג expense עבור חשבוניות ספק נכנסות)
        new_trans = Transaction(
            company_id=active_company_id,
            local_id=next_local_id,
            date=trans_date,
            description=final_description,
            amount=raw_amount,
            vat_amount=raw_vat,  
            type='expense',
            category_id=category_id,
            invoice_id=data.get("invoice_id"), # מספר החשבונית המקורית של הספק 
            attachment_path=data.get("attachment_path", ""), # נתיב לקובץ במידה ו-n8n שומר אותו
            cost_price_at_time=cost_price, 
            customer_id=str(final_customer_id) if final_customer_id is not None else None,
            quantity=1
        )
        
        db.session.add(new_trans)
        db.session.commit()

        # 7. הפעלת מנגנון תרגום התנועות ברקע שלכם בלייב
        current_curr = get_currency() if 'get_currency' in globals() else 'ILS'
        try:
            translate_transaction_in_background(
                transaction_id=new_trans.id,
                company_id=active_company_id,   
                description=final_description,
                amount=raw_amount,
                type_trans='expense',
                category_id=category_id,
                currency_code=current_curr,
                cost_price=cost_price,
                income_category='service' 
            )
        except Exception as e:
            print(f"⚠️ n8n transaction flow - Translation trigger bypassed: {e}")

        db.session.close()

        return jsonify({
            "status": "success",
            "message": "Transaction recorded successfully from n8n",
            "transaction_id": new_trans.id,
            "local_id": new_trans.local_id
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

    db.session.expire_all()

    trans = Transaction.query.filter_by(id=id, company_id=active_company_id).first()
    
    if trans:
        # mobile_phone הגנה חשבונאית אטומית: חוסם מחיקה אך ורק אם התנועה היא פיזית תנועת הכנסה מסוג חשבונית השייכת לחברה!
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

            # mobile_phone הגנה חשבונאית: ניתוק תנועות קיימות המשויכות לקטגוריה זו כדי למנוע קריסות של דפים ודוחות
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

        # ------------------ PROCESS INVOICES ------------------
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

        # עיבוד חשבוניות דיווח עצמי / הכנסה פסיבית פנימית (מזהה 0)
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

        # החזרת כל הנתונים המעודכנים לתבנית התצוגה בדפדפן
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
    
    final_emp_id = str(employee_id).strip()
    
    # תוקן הרמטית: הגנה מפני קריסות ValueError במקרה ומגיע מזהה טקסט חופשי, ובדיקה חסינת נפילות מול המודל החדש
    if employee_id and str(employee_id).strip().isdigit():
        emp_id_int = int(str(employee_id).strip())
        emp_row = EmployeeData.query.filter_by(id=emp_id_int, company_id=company_id).first()
        if not emp_row:
            emp_row = EmployeeData.query.filter_by(local_id=emp_id_int, company_id=company_id).first()
            
        if emp_row and emp_row.local_id:
            final_emp_id = str(emp_row.local_id) 

    # קיבוע נתיב מבודד לחלוטין בדיסק לכל מעסיק בנפרד (תואם לשיטת החשבוניות שלכם)
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
    user_role = (current_user.role or '').lower() or session.get('role', '').lower()
    if session.get('owner_access') or user_role == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

    if not active_company_id:
        return False

    # תוקן הרמטית: מציאת ה-ID המאובטח של העובד בטבלת EmployeeData למניעת הצלבות מידע בין חברות
    if str(employee_id).isdigit():
        emp_profile = EmployeeData.query.filter(
            (EmployeeData.local_id == int(employee_id)) | (EmployeeData.id == int(employee_id))
        ).filter_by(company_id=active_company_id).first()
        final_db_id = emp_profile.id if emp_profile else int(employee_id)
    else:
        emp_profile = EmployeeData.query.filter_by(employee_id=str(employee_id), company_id=active_company_id).first()
        final_db_id = emp_profile.id if emp_profile else employee_id

    month_filter = f"{selected_year}-{str(selected_month).zfill(2)}-%"
  
    # השאילתה מסננת בצורה הרמטית ומבודדת לפי המזהים האמיתיים של אותה חברה
    return Timesheet.query.filter(
        Timesheet.company_id == active_company_id,
        Timesheet.employee_id == final_db_id,
        Timesheet.date.like(month_filter)
    ).first() is not None


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
    base_dir = app.config.get("EMPLOYEES_DIR", "static/employees")
    folder = os.path.join(base_dir, f"company_{company_id}", str(local_id))
    file_path = os.path.join(folder, "hours.json")
    if not os.path.exists(file_path):
        return {}
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_hours(company_id, local_id, data):
    if not company_id or not local_id:
        return
    base_dir = app.config.get("EMPLOYEES_DIR", "static/employees")
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
# Protect Main Page , Index: Form Page
# ----------------------

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    # Redirect employees to their own dashboard
    if session.get('role') == 'employee' or getattr(current_user, 'role', '').lower() == 'employee':
        return redirect(url_for('employee_dashboard'))

    # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
    user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
    if session.get('owner_access') or user_role == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

    if not active_company_id:
        flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
        return redirect(url_for('login'))
    # ---------------------------------------------------------------------------
    months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'מאי', 'יוני',
              'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
    years = list(range(2020, 2041))
    
    employees = EmployeeData.query.filter_by(company_id=active_company_id).order_by(EmployeeData.employee_name).all()

    today = datetime.today()
    default_month = f"{today.month:02d}"
    default_year = str(today.year)

    session.setdefault('selected_employee_id', '')
    session.setdefault('selected_month', default_month)
    session.setdefault('selected_year', default_year)
    session.setdefault('employee_data', {})
    session.setdefault('table_data', {})
    session.setdefault('hours_table', {})

    # ----- Handle POST -----
    if request.method == 'POST':
        form_type = request.form.get('form_type')

        if form_type == 'save_all_data':
            try:
                selected_year = request.form.get('employeeYear', '').strip() or session['selected_year']
                selected_month = request.form.get('employeeMonth', '').strip() or session['selected_month']
                selected_employee_id = request.form.get('employee_id', '').strip() or session['selected_employee_id']

                if not selected_employee_id or selected_employee_id == '0' or selected_employee_id == '':
                    flash("נא למלא שם עובד חוקי, חודש ושנה!", "warning")
                    return redirect(url_for('index'))

                session['selected_employee_id'] = selected_employee_id
                session['selected_month'] = selected_month
                session['selected_year'] = selected_year

                date_key = f"{selected_month}/{selected_year}"
                month_key = f"{selected_year}-{selected_month.zfill(2)}"

                if not selected_month or not selected_year:
                    flash("נא למלא חודש ושנה!", "warning")
                    return redirect(url_for('index'))

                employee = EmployeeData.query.filter(
                    (EmployeeData.local_id == int(selected_employee_id)) | (EmployeeData.id == int(selected_employee_id))
                ).filter_by(company_id=active_company_id).first()

                if not employee:
                    flash("העובד לא נמצא בחברה זו", "danger")
                    return redirect(url_for('index'))

                employee_name = employee.employee_name

                existing = EmployeeData.query.filter_by(id=employee.id, company_id=active_company_id, date=date_key).first()
                if existing or entry_exists_in_csv(selected_employee_id, month_key, company_id=active_company_id):
                    flash("הנתונים כבר קיימים בחברה זו", "warning")
                    return redirect(url_for('index'))
                    
                hours_table_json = request.form.get('hours_table_data')
                table_data = {'work_day_entries': [], 'monthly_totals': {}, 'paid_totals': {}, 'tax': {}}

                if hours_table_json:
                    try:
                        parsed = json.loads(hours_table_json)
                        if isinstance(parsed, dict) and 'hours_table' in parsed:
                            parsed = parsed['hours_table']
                        table_data['work_day_entries'] = parsed.get('work_day_entries', [])
                        table_data['monthly_totals'] = parsed.get('monthly_totals', {})
                        table_data['paid_totals'] = parsed.get('paid_totals', {})
                        table_data['tax'] = parsed.get('tax', {})
                        session['table_data'] = parsed
                    except json.JSONDecodeError:
                        flash("שגיאה בקריאת נתוני שעות העבודה", "danger")
                        session['table_data'] = {}

                try:
                    all_hours_snapshot = load_hours(active_company_id, employee.local_id)
                    yearly_totals = compute_yearly_totals(
                        all_hours=all_hours_snapshot,
                        employee_id=selected_employee_id,
                        year=selected_year,
                        current_month=selected_month,
                        current_tax=table_data.get('tax', {})
                    ) or {}
                except Exception:
                    yearly_totals = {}

                table_data.setdefault('tax', {})
                table_data['tax'].update(yearly_totals)

                all_hours = load_hours(active_company_id, employee.local_id)
                all_hours.setdefault(str(selected_employee_id), {})
                all_hours[str(selected_employee_id)]["employee_name"] = employee_name
                all_hours[str(selected_employee_id)][month_key] = {"hours_table": table_data}
                save_hours(active_company_id, employee.local_id, all_hours)
   
                # Build form_data
                form_data = request.form.to_dict(flat=True)
                form_data['employee_id'] = selected_employee_id
                form_data['employee_name'] = employee_name
                form_data['employeeMonth'] = selected_month
                form_data['employeeYear'] = selected_year
                form_data['date'] = date_key
                form_data.update(yearly_totals)

                form_data.update(table_data.get('monthly_totals', {}))
                form_data.update(table_data.get('paid_totals', {}))
                form_data.update(table_data.get('tax', {}))

                session['employee_data'] = form_data
                session['month_result'] = date_key
                session['hours_table'] = table_data

                # Save to DB
                form_data['company_id'] = active_company_id                
                valid_db_data = {k: v for k, v in form_data.items() if k in EmployeeData.__table__.columns}
                
                new_tax_entry = EmployeeData(**valid_db_data)
                db.session.add(new_tax_entry)
                db.session.commit()

                session.pop('employee_data', None)
                session.pop('form_data', None)
                session.pop('hours_table', None)
                session['force_clear'] = True  
                print("✔ Database successfully committed and old session memory completely wiped!")

                # Save to CSV
                row = {'company_id': active_company_id, 'employee_id': selected_employee_id, 'employee_name': employee_name, 'month': month_key}
                row.update(table_data.get('tax', {}))
                row.update(table_data.get('monthly_totals', {}))
                row.update(table_data.get('paid_totals', {}))

                csv_path = 'hours_data.csv'
                write_headers = not os.path.exists(csv_path)
                with open(csv_path, 'a', encoding='utf-8-sig', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=row.keys())
                    if write_headers:
                        writer.writeheader()
                    writer.writerow(row)

                flash("הנתונים נשמרו בהצלחה!", "success")
                return redirect(url_for(
                    'index',
                    employee_id=selected_employee_id,
                    month=selected_month,
                    year=selected_year
                ))

            except Exception as e:
                db.session.rollback()
                import traceback
                traceback.print_exc()
                flash(f"שגיאה בעת השמירה: {str(e)}", "danger")
                return redirect(url_for('index'))

    # ----- Handle GET -----
    if session.get('force_clear'):
        selected_employee_id = ''
        selected_month = default_month
        selected_year = default_year
        session['selected_employee_id'] = ''
        session['employee_data'] = {}
        session['hours_table'] = {}
        session.pop('force_clear', None)  
    else:
        selected_employee_id = request.args.get('employee_id') or session.get('selected_employee_id', '')
        selected_month = request.args.get('month') or session.get('selected_month', default_month)
        selected_year = request.args.get('year') or session.get('selected_year', default_year)

    session['selected_employee_id'] = selected_employee_id
    session['selected_month'] = selected_month
    session['selected_year'] = selected_year

    date_key = f"{selected_month}/{selected_year}"                
    month_key = f"{selected_year}-{str(selected_month).zfill(2)}"

    if not selected_employee_id or not selected_month or not selected_year:
        pass 

    form_data = {}
    if selected_employee_id:
        form_data.update(session.get('employee_data', {}))

        csv_form = get_form_data_from_csv(active_company_id, selected_employee_id, selected_year, selected_month, month_key)
        for k, v in csv_form.items():
            form_data.setdefault(k, v)

    employee = EmployeeData.query.filter(
        (EmployeeData.local_id == int(selected_employee_id)) | (EmployeeData.id == int(selected_employee_id))
    ).filter_by(company_id=active_company_id).first() if selected_employee_id and str(selected_employee_id).isdigit() else None

    if employee:
        # Stop None - form_data 
        def clean_val(attr_name, default_fallback=0.0):
            val = getattr(employee, attr_name, None)
            if val is None:
                return default_fallback
            try:
                if isinstance(default_fallback, (int, float)):
                    return float(val)
            except:
                return default_fallback
            return val

        form_data['employee_id'] = getattr(employee, 'local_id', selected_employee_id)
        form_data['employee_name'] = clean_val('employee_name', '')
        form_data['id_number'] = clean_val('id_number', '')
        form_data['address'] = clean_val('address', '')

        # Contact & personal info
        form_data['city'] = clean_val('city', '')
        form_data['postal_code'] = clean_val('postal_code', '')
        form_data['mobile_phone'] = clean_val('mobile_phone', '')
        form_data['email'] = clean_val('email', '')
        form_data['start_date'] = clean_val('start_date', '')
        form_data['date_of_birth'] = clean_val('date_of_birth', '')

        # Bank details
        form_data['bank_number'] = clean_val('bank_number', '')
        form_data['branch_number'] = clean_val('branch_number', '')
        form_data['account_number'] = clean_val('account_number', '')

        # Salary & benefits
        form_data['hourly_rate'] = clean_val('hourly_rate', 0.0)
        form_data['total_work_days'] = clean_val('total_work_days', 0.0)
        form_data['totals_lunch_value'] = clean_val('totals_lunch_value', 0.0)
        form_data['total_missing_hours'] = clean_val('total_missing_hours', 0.0)
        form_data['mobile_value'] = clean_val('mobile_value', 0.0)
        form_data['clothing_value'] = clean_val('clothing_value', 0.0)
        form_data['lunch_value'] = clean_val('lunch_value', 0.0)
        form_data['cars_value'] = clean_val('cars_value', 0.0)
        form_data['advance_payment_salary'] = clean_val('advance_payment_salary', 0.0)
        form_data['monthly_city_tax_tops'] = clean_val('monthly_city_tax_tops', 0.0)
        form_data['city_value_percentage'] = clean_val('city_value_percentage', 0.0)
        form_data['final_city_tax_benefit'] = clean_val('final_city_tax_benefit', 0.0)

        # Salary breakdown
        form_data['basic_salary'] = clean_val('basic_salary', 0.0)
        form_data['additional_payments'] = clean_val('additional_payments', 0.0)
        form_data['net_value'] = clean_val('net_value', 0.0)
        form_data['gross_salary'] = clean_val('gross_salary', 0.0)
        form_data['gross_taxable'] = clean_val('gross_taxable', 0.0)

        # Above ceiling
        form_data['above_ceiling_value'] = clean_val('above_ceiling_value', 0.0)
        form_data['above_ceiling_fund'] = clean_val('above_ceiling_fund', 0.0)
        form_data['above_ceiling_compensation'] = clean_val('above_ceiling_compensation', 0.0)

        # Employer contributions
        form_data['pension_fund'] = clean_val('pension_fund', 0.0)
        form_data['compensation'] = clean_val('compensation', 0.0)
        form_data['study_fund'] = clean_val('study_fund', 0.0)
        form_data['disability'] = clean_val('disability', 0.0)
        form_data['miscellaneous'] = clean_val('miscellaneous', 0.0)
        form_data['national_insurance'] = clean_val('national_insurance', 0.0)
        form_data['salary_tax'] = clean_val('salary_tax', 0.0)
        form_data['total_employer_contributions'] = clean_val('total_employer_contributions', 0.0)
        form_data['total_salary_cost'] = clean_val('total_salary_cost', 0.0)

        # Employee contributions
        form_data['employee_pension_fund'] = clean_val('employee_pension_fund', 0.0)
        form_data['self_employed_pension_fund'] = clean_val('self_employed_pension_fund', 0.0)
        form_data['study_fund_deductions'] = clean_val('study_fund_deductions', 0.0)
        form_data['miscellaneous_deductions'] = clean_val('miscellaneous_deductions', 0.0)
        form_data['national_insurance_deductions'] = clean_val('national_insurance_deductions', 0.0)
        form_data['health_insurance_deductions'] = clean_val('health_insurance_deductions', 0.0)
        form_data['income_tax'] = clean_val('income_tax', 0.0)
        form_data['income_tax_before_credit'] = clean_val('income_tax_before_credit', 0.0)
        form_data['tax_credit_points'] = clean_val('tax_credit_points', 2.25)
        form_data['amount_tax_credit_points_monthly'] = clean_val('amount_tax_credit_points_monthly', 0.0)
        form_data['tax_level_precente'] = clean_val('tax_level_precente', 0.0)
        form_data['total_salary_pension_funds'] = clean_val('total_salary_pension_funds', 0.0)
        form_data['total_deductions'] = clean_val('total_deductions', 0.0)
        form_data['net_payment'] = clean_val('net_payment', 0.0)

        # Yearly values
        form_data['employee_pension_fund_yearly'] = clean_val('employee_pension_fund_yearly', 0.0)
        form_data['self_employed_pension_fund_yearly'] = clean_val('self_employed_pension_fund_yearly', 0.0)
        form_data['study_fund_deductions_yearly'] = clean_val('study_fund_deductions_yearly', 0.0)
        form_data['miscellaneous_deductions_yearly'] = clean_val('miscellaneous_deductions_yearly', 0.0)
        form_data['national_insurance_deductions_yearly'] = clean_val('national_insurance_deductions_yearly', 0.0)
        form_data['health_insurance_deductions_yearly'] = clean_val('health_insurance_deductions_yearly', 0.0)
        form_data['income_tax_yearly'] = clean_val('income_tax_yearly', 0.0)
        form_data['amount_tax_credit_points_monthly_yearly'] = clean_val('amount_tax_credit_points_monthly_yearly', 0.0)
        form_data['final_city_tax_benefit_yearly'] = clean_val('final_city_tax_benefit_yearly', 0.0)
        form_data['pension_fund_yearly'] = clean_val('pension_fund_yearly', 0.0)
        form_data['compensation_yearly'] = clean_val('compensation_yearly', 0.0)
        form_data['study_fund_yearly'] = clean_val('study_fund_yearly', 0.0)
        form_data['disability_yearly'] = clean_val('disability_yearly', 0.0)
        form_data['miscellaneous_yearly'] = clean_val('miscellaneous_yearly', 0.0)
        form_data['national_insurance_yearly'] = clean_val('national_insurance_yearly', 0.0)
        form_data['salary_tax_yearly'] = clean_val('salary_tax_yearly', 0.0)
        form_data['total_employer_contributions_yearly'] = clean_val('total_employer_contributions_yearly', 0.0)
        form_data['total_salary_cost_yearly'] = clean_val('total_salary_cost_yearly', 0.0)
        form_data['sick_days_salary_yearly'] = clean_val('sick_days_salary_yearly', 0.0)
        form_data['vacation_days_salary_yearly'] = clean_val('vacation_days_salary_yearly', 0.0)
        form_data['sick_days_balance_yearly'] = clean_val('sick_days_balance_yearly', 0.0)
        form_data['vacation_balance_yearly'] = clean_val('vacation_balance_yearly', 0.0)
        form_data['gross_taxable_yearly'] = clean_val('gross_taxable_yearly', 0.0)
        
        # Other info
        form_data['thirteenth_salary'] = clean_val('thirteenth_salary', 0.0)
        form_data['work_percent'] = clean_val('work_percent', 100.0)
        form_data['sick_days_salary'] = clean_val('sick_days_salary', 0.0)
        form_data['vacation_days_salary'] = clean_val('vacation_days_salary', 0.0)
        form_data['sick_days_entitlement'] = clean_val('sick_days_entitlement', 0.0)
        form_data['vacation_days_entitlement'] = clean_val('vacation_days_entitlement', 0.0)
        form_data['final_extra_hours_weekend'] = clean_val('final_extra_hours_weekend', 0.0)
        form_data['final_extra_hours_regular'] = clean_val('final_extra_hours_regular', 0.0)
        form_data['food_break_unpaid_salary'] = clean_val('food_break_unpaid_salary', 0.0)
        form_data['hours125_regular_salary'] = clean_val('hours125_regular_salary', 0.0)
        form_data['hours150_regular_salary'] = clean_val('hours150_regular_salary', 0.0)
        form_data['hours150_holidays_saturday_salary'] = clean_val('hours150_holidays_saturday_salary', 0.0)
        form_data['hours175_holidays_saturday_salary'] = clean_val('hours175_holidays_saturday_salary', 0.0)
        form_data['hours200_holidays_saturday_salary'] = clean_val('hours200_holidays_saturday_salary', 0.0)
        form_data['employee_number'] = clean_val('employee_number', '')
        form_data['marital_status'] = clean_val('marital_status', '')
        form_data['work_apartment'] = clean_val('work_apartment', '')
        form_data['hospital'] = clean_val('hospital', '')
        form_data['social_number'] = clean_val('social_number', '')
        form_data['irs_status'] = clean_val('irs_status', '')
        form_data['contract_status'] = clean_val('contract_status', '')
        form_data['tax_point_child'] = clean_val('tax_point_child', 0.0)
        form_data['message'] = clean_val('message', '')

    # Stop None - form_data 
    for k, v in form_data.items():
        if v is None:
            form_data[k] = 0 if k in ["hourly_rate", "total_work_days", "totals_lunch_value", "total_missing_hours", "mobile_value", "clothing_value", "lunch_value", "cars_value", "advance_payment_salary", "monthly_city_tax_tops", "city_value_percentage", "final_city_tax_benefit", "hours150_holidays_saturday_salary", "hours175_holidays_saturday_salary", "hours200_holidays_saturday_salary", "tax_point_child"] else ""

    hours_table = session.get('hours_table', {})

    if not hours_table and employee:
          all_hours = load_hours(active_company_id, employee.local_id)
          hours_table = all_hours.get(month_key, {}).get('hours_table', {})

    default_hours = {
          'work_day_entries': [],
          'monthly_totals': {},
          'paid_totals': {},
          'tax': {}
    }

    for key, default_value in default_hours.items():
        hours_table.setdefault(key, default_value)

    session['form_data'] = form_data
    session['hours_table'] = hours_table

    company_obj = db.session.get(Company, active_company_id) if active_company_id else None

    return render_template(
          'index.html',
          form_data=session.get('form_data', {}),
          hours_table=session.get('hours_table', {}),
          employee_data=session.get('employee_data', {}),
          selected_employee_id=session.get('selected_employee_id', ''), 
          employeeMonth=session.get('selected_month', default_month),
          employeeYear=session.get('selected_year', default_year),
          month_result=session.get('month_result', ''),
          months=months,
          years=years,
          employees=employees,
          days_data=get_days_in_month(int(selected_year), int(selected_month)),
          hours=get_hours_data(),
          employee_details=get_employee_details(session.get('selected_employee_id', ''), selected_month, selected_year),
          all_hours=json.dumps(load_hours(active_company_id, employee.local_id if employee else 0), ensure_ascii=False),
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
#  Save Table Hours Calculate Data on Page
# ----------------------

@app.route('/save_hours', methods=['POST'])
@login_required
def save_hours_route():
    try:
        today = datetime.today()
        default_month = f"{today.month:02d}"
        default_year = str(today.year)

        data = request.get_json()
        employee_id = str(data.get('employee_id', '')).strip()
        employee_name = data.get('employee_name', '').strip()
        month = str(data.get('month', default_month)).zfill(2)
        year = str(data.get('year', default_year)).strip()

        hours_table = data.get('hours_table', {}) or {}
        work_day_entries_raw = hours_table.get('work_day_entries', []) or []
        monthly_totals_raw = hours_table.get('monthly_totals', {}) or []
        paid_totals_raw = hours_table.get('paid_totals', {}) or []
        tax_data_raw = hours_table.get('tax', {}) or {}

        if not work_day_entries_raw:
            return jsonify(success=False, message="אין נתונים לשמירה"), 400

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = current_user.company_id

        if not active_company_id:
            return jsonify(success=False, message="שגיאה: אין חברה פעילה משויכת למעסיק"), 400

        # מציאת העובד המדויק של החברה הפעילה לפי ה-local_id או ה-id הכללי
        employee = EmployeeData.query.filter(
            (EmployeeData.local_id == int(employee_id)) | (EmployeeData.id == int(employee_id))
        ).filter_by(company_id=active_company_id).first()

        if not employee:
            return jsonify(success=False, message="שגיאה אבטחה: העובד המבוקש לא נמצא בחברה זו"), 404
        # ---------------------------------------------------------------------------

        month_key = f"{year}-{month}"

        # kebab → snake
        def snake(d):
            return {k.replace("-", "_"): v for k, v in d.items()}

        work_day_entries = [snake(e) for e in work_day_entries_raw]
        monthly_totals = snake(monthly_totals_raw)
        paid_totals = snake(paid_totals_raw)
        tax_data = snake(tax_data_raw)

        # === YEARLY TOTALS (FIXED) ===
        def clean_number(value):
            try:
                return float(str(value).replace("₪", "").replace(",", "").strip())
            except:
                return 0.0

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

            sick_days_entitlement = 18
            vacation_days_entitlement = 12

            totals = {f"{field}_yearly": 0.0 for field in yearly_fields}
            
            # תוקן הרמטית: קובץ hours.json הוא כבר המילון השטוח והישיר של החודשים של העובד במולטי-חברה!
            employee_data = all_hours if isinstance(all_hours, dict) else {}

            for m_key, month_data in employee_data.items():
                if m_key == "employee_name":
                    continue
                if not m_key.startswith(str(year)):
                    continue

                try:
                    mk_month = int(m_key.split("-")[1])
                except:
                    continue

                if mk_month >= int(current_month):
                    continue

                tax = month_data.get("hours_table", {}).get("tax", {})

                for field in yearly_fields:
                    val = clean_number(tax.get(field, 0))
                    totals[f"{field}_yearly"] += val

            for field in yearly_fields:
                val = clean_number(current_tax.get(field, 0))
                totals[f"{field}_yearly"] += val

            sick_days_used = totals.get("sick_days_salary_yearly", 0.0)
            vacation_days_used = totals.get("vacation_days_salary_yearly", 0.0)

            totals["sick_days_balance_yearly"] = sick_days_entitlement - sick_days_used
            totals["vacation_balance_yearly"] = vacation_days_entitlement - vacation_days_used

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

        # === JSON SAVE (תוקן הרמטית לנתיב השטוח המבודד של החברה והעובד) ===
        base_dir = app.config.get("EMPLOYEES_DIR", "static/employees")
        folder = os.path.join(base_dir, f"company_{active_company_id}", str(employee.local_id))
        os.makedirs(folder, exist_ok=True)
        json_file_path = os.path.join(folder, "hours.json")

        all_hours = {}
        if os.path.exists(json_file_path):
            try:
                with open(json_file_path, 'r', encoding='utf-8') as f:
                    all_hours = json.load(f)
            except:
                all_hours = {}

        all_hours["employee_name"] = employee_name

        yearly = compute_yearly_totals(all_hours, str(employee.local_id), year, month, tax_data_raw)
        tax_data.update(snake(yearly))
        tax_data_raw.update(yearly)

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

        # === BUILD COLUMN ORDER ===
        fieldnames = [
            "employee_id", "employee_name", "month", "section",
            "day", "date", "saturday", "holiday"
        ]

        for f in [
            'start_time','end_time','hours_calculated','hours_calculated_regular_day',
            'total_extra_hours_regular_day','extra_hours125_regular_day',
            'extra_hours150_regular_day','hours_holidays_day',
            'extra_hours150_holidays_saturday','extra_hours175_holidays_saturday',
            'extra_hours200_holidays_saturday','sick_day','day_off','food_break',
            'final_totals_hours','calc1','calc2','calc3','work_day',
            'missing_work_day','advance_payment'
        ]:
            fieldnames.append(f)

        for f in monthly_totals.keys():
            fieldnames.append(f)

        for f in paid_totals.keys():
            fieldnames.append(f)

        for f in tax_data.keys():
            fieldnames.append(f)

        fieldnames = list(dict.fromkeys(fieldnames))

        # === REBUILD CSV ===
        csv_path = "hours_data.csv"
        rows = []

        # תוקן הרמטית: פריסת המילון השטוח של hours.json המבודד לפי החברה הפעילה בלבד
        emp_name = all_hours.get("employee_name", employee_name)

        # הגדרת נתיב ה-CSV המקומי המבודד של העובד בתוך התיקייה השטוחה שלו
        csv_path = os.path.join(folder, "hours_data.csv")

        for mk, mdata in all_hours.items():
            if mk == "employee_name": continue

            tbl = mdata["hours_table"]
            wd = [snake(e) for e in tbl["work_day_entries"]]
            mt = snake(tbl["monthly_totals"])
            pt = snake(tbl["paid_totals"])
            tx = snake(tbl["tax"])

            wrote_summary = False

            for entry in wd:
                row = {
                    "employee_id": str(employee.local_id) if not wrote_summary else "",
                    "employee_name": emp_name if not wrote_summary else "",
                    "month": mk if not wrote_summary else "",
                    "section": "daily",
                    "day": entry.get("day", ""),
                    "date": entry.get("date", ""),
                    "saturday": entry.get("saturday", ""),
                    "holiday": entry.get("holiday", "")
                }

                for k, v in entry.items():
                    if k not in ("day","date","saturday","holiday"):
                        row[k] = v

                if not wrote_summary:
                    row.update(mt)
                    row.update(pt)
                    row.update(tx)
                    wrote_summary = True
                else:
                    for k in mt.keys(): row[k] = ""
                    for k in pt.keys(): row[k] = ""
                    for k in tx.keys(): row[k] = ""

                rows.append(row)

        # write CSV
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow(r)

        # תוקן הרמטית: העברת מזהי המולטי-חברה לפונקציית ייצור האקסל המבודדת שלכם!
        save_to_csv_and_xlsx(active_company_id, employee.local_id)

        return jsonify(success=True, message="הנתונים נשמרו בהצלחה")

    except Exception as e:
        if 'db' in locals() and db.session:
            db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify(success=False, message=str(e)), 500


# ----------------------
#  Save To Csv And Xlsx All Data On Folder
# ----------------------

def save_to_csv_and_xlsx(company_id, local_id):
    # תוקן: בניית הנתיבים המבודדים והמאובטחים של קבצי המקור והאקסל בתיקיית העובד
    base_dir = app.config.get("EMPLOYEES_DIR", "static/employees")
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
            max_len = max(df[col].astype(str).map(len).max(), len(col)) + 2
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

    if not employee_id or not selected_year or not selected_month:
        return jsonify({"empty": True})

    # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
    user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
    if session.get('owner_access') or user_role == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id or session.get('company_id')

    if not active_company_id:
        return jsonify({"empty": True, "error": "No active company context"}), 400

    employee = EmployeeData.query.filter(
        (EmployeeData.local_id == int(employee_id)) | (EmployeeData.id == int(employee_id))
    ).filter_by(company_id=active_company_id).first()

    if not employee:
        return jsonify({"empty": True, "error": "Employee not found in this company"}), 404
    # ---------------------------------------------------------------------------

    month_key = f"{selected_year}-{str(selected_month).zfill(2)}"

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

def get_yearly_totals(employee_id):
    # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
    user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
    if session.get('owner_access') or user_role == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id or session.get('company_id')

    if not active_company_id:
        return None

    # מציאת העובד המדויק בתוך החברה הפעילה בלבד כדי לחלץ את ה-ID האמיתי שלו בדאטהבייס
    employee = EmployeeData.query.filter(
        (EmployeeData.local_id == int(employee_id)) | (EmployeeData.id == int(employee_id))
    ).filter_by(company_id=active_company_id).first()

    if not employee:
        return None
    # ---------------------------------------------------------------------------

    # תוקן הרמטית: השאילתה מריצה סכימה על מודל EmployeeData ומסוננת לחברה הפעילה בלבד!
    totals = db.session.query(
        func.sum(EmployeeData.sick_days_salary).label('sick_days_salary_yearly'),
        func.sum(EmployeeData.vacation_days_salary).label('vacation_days_salary'),
        func.sum(EmployeeData.gross_taxable).label('gross_taxable_yearly'),
        func.sum(EmployeeData.employee_pension_fund).label('employee_pension_fund_yearly'),
        func.sum(EmployeeData.self_employed_pension_fund).label('self_employed_pension_fund_yearly'),
        func.sum(EmployeeData.study_fund_deductions).label('study_fund_deductions_yearly'),
        func.sum(EmployeeData.miscellaneous_deductions).label('miscellaneous_deductions_yearly'),
        func.sum(EmployeeData.national_insurance_deductions).label('national_insurance_deductions_yearly'),
        func.sum(EmployeeData.health_insurance_deductions).label('health_insurance_deductions_yearly'),
        func.sum(EmployeeData.income_tax).label('income_tax_yearly'),
        func.sum(EmployeeData.amount_tax_credit_points_monthly).label('amount_tax_credit_points_monthly_yearly'),
        func.sum(EmployeeData.final_city_tax_benefit).label('final_city_tax_benefit_yearly'),
        func.sum(EmployeeData.pension_fund).label('pension_fund_yearly'),
        func.sum(EmployeeData.compensation).label('compensation_yearly'),
        func.sum(EmployeeData.study_fund).label('study_fund_yearly'),
        func.sum(EmployeeData.disability).label('disability_yearly'),
        func.sum(EmployeeData.miscellaneous).label('miscellaneous_yearly'),
        func.sum(EmployeeData.national_insurance).label('national_insurance_yearly'),
        func.sum(EmployeeData.salary_tax).label('salary_tax_yearly'),
        func.sum(EmployeeData.total_employer_contributions).label('total_employer_contributions_yearly'),
        func.sum(EmployeeData.total_salary_cost).label('total_salary_cost_yearly')
    ).filter(EmployeeData.id == employee.id, EmployeeData.company_id == active_company_id).first()

    return totals


# -----------------------------------------------------------
#  N8N NEW FOR GREAT AUTO Save_hours_from_n8n API Endpoint 
# -----------------------------------------------------------

@app.route('/api/hours_data/save_from_n8n', methods=['POST'])
def api_save_hours_from_n8n():
    try:
        # 1. בדיקת אבטחה: אימות ה-Header שהגיע מ-n8n מול המפתח בראש הקובץ
        auth_header = request.headers.get("X-API-KEY")
        if auth_header != N8N_API_KEY:
            return jsonify({"error": "Unauthorized API Access"}), 401

        # 2. קבלת ה-JSON המלא של דיווח השעות והחישובים מ-n8n
        data = request.get_json() or {}
        
        employee_id = data.get("employee_id")
        selected_month = data.get("month")
        selected_year = data.get("year")

        if not employee_id or not selected_month or not selected_year:
            return jsonify({"error": "Missing employee_id, month, or year"}), 400

        # קביעת החברה האקטיבית (ברירת מחדל OWNER_COMPANY_ID = 1)
        active_company_id = data.get("company_id", OWNER_COMPANY_ID)

        # 3. כאן אנחנו מדמים בדיוק את שמירת הקובץ או הנתונים ש-/get_hours_data מצפה לקרוא
        # n8n ישלח את המבנה המלא שכולל את 'work_day_entries' בדיוק כפי שה-Front שלך צריך
        work_day_entries = data.get("work_day_entries", [])

        # לוגיקת השמירה שלך לדאטהבייס או לקובץ JSON בדיסק הדינמי:
        # אם המערכת שלך שומרת בקובץ JSON פיזי בדיסק תחת EMPLOYEES_DIR:
        try:
            # דוגמה לשמירה בנתיב המותאם ל-Multi-Tenant ו-Render Safe שלכם
            filename = f"hours_{active_company_id}_{employee_id}_{selected_month}_{selected_year}.json"
            target_folder = os.path.join(EMPLOYEES_DIR, f"company_{active_company_id}")
            os.makedirs(target_folder, exist_ok=True)
            file_path = os.path.join(target_folder, filename)

            # כתיבת הנתונים המעודכנים לקובץ בצורה נקייה
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)

        except Exception as file_err:
            print(f"⚠️ n8n hours flow - Disk write simulation/fallback bypassed: {file_err}")

        # 4. עדכון דאטהבייס במידה ויש מודל מלווה (כמו EmployeeData או HoursData שקיימים אצלך)
        # ניתן להוסיף כאן שמירה/עדכון של שורת השכר הסופית כדי שמשתני ה-Profit יסתנכרנו ישירות.

        return jsonify({
            "status": "success",
            "message": "Hours data synchronized and saved successfully from n8n",
            "employee_id": employee_id,
            "month": selected_month,
            "year": selected_year,
            "entries_count": len(work_day_entries)
        }), 201

    except Exception as e:
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
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לאמצאו במערכת", "error")
            return redirect(url_for('index'))
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
                        if len(parts)[0] == 4:  # YYYY-MM-DD
                            parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
                        else:  # DD-MM-YYYY
                            parsed_date = datetime.strptime(date_str, '%d-%m-%Y')
                    elif '/' in date_str:
                        parts = date_str.split('/')
                        if len(parts)[0] == 4:  # YYYY/MM/DD
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

                user = User.query.filter_by(email=email).first()
                if not user:
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
                    'user_id': user.id,
                    'company_id': active_company_id,
                    'employee_id': user.id,
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
                    'monthly_city_tax_tops': to_float(request.form.get('monthly_city_tax_tops')),
                    'city_value_percentage': to_float(request.form.get('city_value_percentage')),
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
                    next_local_id = 1 if not last_emp or not last_emp.local_id else (last_emp.local_id + 1)
                    employee.local_id = next_local_id
                    
                    for key, value in form_data.items():
                        setattr(employee, key, value)
                        
                    db.session.add(employee)

                #  הצינור הדו-כיווני: מעדכן/מייצר במקביל 
                employee_card = Employee.query.filter_by(id=employee.id, company_id=active_company_id).first()
                if employee_card:
                    employee_card.employee_name = employee_name
                    employee_card.id_number     = id_number
                    employee_card.address       = request.form.get('address', '')
                    employee_card.city          = city_name
                    employee_card.postal_code   = request.form.get('postal_code', '')
                    employee_card.mobile_phone  = request.form.get('mobile_phone', '')
                    employee_card.email         = request.form.get('email', '').strip().lower()
                else:
                    new_employee_row = Employee(
                        id=employee.id, company_id=active_company_id, local_id=employee.local_id,
                        date=date_str, employee_name=employee_name, id_number=id_number,
                        address=request.form.get('address', ''), city=city_name,
                        postal_code=request.form.get('postal_code', ''),
                        mobile_phone=request.form.get('mobile_phone', ''),
                        email=request.form.get('email', '').strip().lower(), role='employee', is_active=True
                    )
                    db.session.add(new_employee_row)

                session['shared_employee_name'] = employee_name
                session['employee_id'] = employee.id_number
                session['selected_month'] = datetime.now().month
                session['selected_year'] = datetime.now().year
                session['employee_data'] = form_data
                db.session.commit()
                flash("נתוני העובד נשמרו בהצלחה!", "success")

                #  DISK SYNC & i18n MULTI-COMPANY LAYER (get_val)
                try:
                    # א. בניית ויצירת התיקייה המבודדת של המולטי-חברה בדיסק בצורה קשיחה
                    emp_folder = os.path.join(app.config.get("EMPLOYEES_DIR", "static/employees"), f"company_{active_company_id}", str(employee.local_id))
                    os.makedirs(emp_folder, exist_ok=True)
                    json_file_path = os.path.join(emp_folder, "employee.json")

                    # ב. בדיקה וטעינה של קובץ קיים, או יצירת מילון חדש מאפס אם העובד חדש לחלוטין
                    existing_i18n_data = {}
                    if os.path.isfile(json_file_path):
                        try:
                            with open(json_file_path, "r", encoding="utf-8") as f:
                                existing_i18n_data = json.load(f)
                        except:
                            existing_i18n_data = {}

                    if not isinstance(existing_i18n_data, dict):
                        existing_i18n_data = {}

                    # חילוץ שפת הדפדפן הנוכחית
                    lookup_lang = language if 'language' in locals() else get_lang()
                    lookup_lang = {"zh": "zh-CN", "en": "en"}.get(lookup_lang, lookup_lang)

                    # ג. הזרקה וכתיבה מיידית של 4 שדות הבסיס החמים ישירות לתוך קובץ ה-JSON
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

                    # נעילת נתוני הליבה של המולטי-חברה - תוקן הרמטית: ה-is_active נמחק לצמיתות!
                    existing_i18n_data["local_id"] = employee.local_id
                    existing_i18n_data["company_id"] = active_company_id
                    existing_i18n_data["email"] = email

                    # ד. כתיבה פיזית של הקובץ לדיסק לפני שה-Thread מתעורר!
                    with open(json_file_path, "w", encoding="utf-8") as f:
                        json.dump(existing_i18n_data, f, ensure_ascii=False, indent=4)

                    # ה. קריאה למנוע ה-Thread שלכם שישלים את שאר 30 השפות ברקע בצורה חלקה
                    translate_employee_in_background(
                        employee_id=employee.id,
                        company_id=active_company_id,
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

        all_employees = EmployeeData.query.filter_by(
            company_id=active_company_id
        ).order_by(EmployeeData.employee_name).all()

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

        employee_id = request.args.get('employee_id', type=int)
        employee = None
        employee_i18n = {}

        if employee_id:
            employee = EmployeeData.query.filter_by(
                id=employee_id,
                company_id=active_company_id
            ).first()

            if not employee:
                employee = next(
                    (c for c in pending_employees_list if c.id == employee_id),
                    None
                )

            if employee:
                employee_i18n = load_employee_translated(
                    employee,
                    language,
                    company_id=active_company_id
                ) or {}

        else:
            id_number = request.args.get('id_number', '').strip()
            if id_number:
                employee = EmployeeData.query.filter_by(id_number=id_number, company_id=active_company_id).first()
                if employee:
                    employee_i18n = load_employee_translated(employee, language, company_id=active_company_id) or {}

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
            trans = load_employee_translated(c, language, company_id=active_company_id) or {}
            
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
        company_obj = db.session.get(Company, active_company_id) if active_company_id else None

        saved_ids = session.get('last_search_results', [])
        search_results_objects = EmployeeData.query.filter(EmployeeData.id.in_(saved_ids)).all() if saved_ids else []

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
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
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

            if has_city and has_no_tax_calc and not session.get('city_data'):
                form_data['run_auto_city'] = False
            elif has_city and has_no_tax_calc:
                form_data['run_auto_city'] = True
            else:
                form_data['run_auto_city'] = False

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
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        company_obj = db.session.get(Company, active_company_id)
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

@app.route('/get_employee_details/<int:employee_id>/<string:month>/<string:year>')
@login_required
def get_employee_details(employee_id, month, year):
    # ----------------- COMPANY CONTEXT  -----------------
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

    if not active_company_id:
        return jsonify({'error': 'שגיאה: אין חברה פעילה משויכת למשתמש'}), 400

    employee = EmployeeData.query.filter(
        (EmployeeData.local_id == employee_id) | (EmployeeData.id == employee_id)
    ).filter_by(company_id=active_company_id).first()

    if not employee:
        return jsonify({'error': 'עובד לא נמצא בחברה הפעילה במערכת'}), 404

    try:
        # ----------------------
        # Helper Formatters 
        # ----------------------

        def format_hours(value):
            return "" if value == 0 else str(int(value)) if value == int(value) else f"{value:.2f}"

        def format_currency(value):
            try:
                return f"{float(value):,.2f}"  # No ₪, just "12,345.67"
            except (ValueError, TypeError):
                return "0.00"

        def format_percentage(value):
            return f"{float(value):.2f}%" if value not in [None, ""] else "0.00%"

        def format_text(value):
            return value.strip() if isinstance(value, str) and value.strip() else "N/A"

        def format_date(value):
            try:
                date_obj = datetime.strptime(value, "%d/%m/%Y")
                return date_obj.strftime("%d/%m/%Y")
            except:
                return "N/A"

        def format_day_name(value):
            try:
                date_obj = datetime.strptime(value, "%d/%m/%Y")
                hebrew_days = ['ראשון', 'שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת']
                return hebrew_days[date_obj.weekday()]
            except:
                return "N/A"

        def safe_float(value):
            try:
                return float(value) if value.strip() != '' else None
            except:
                return None

        # ----------------------
        # Build Employee Data
        # ----------------------

        employee_data = {
            'employee_id': employee_id,
            'month_result': format_text(getattr(employee, 'month_result', "")),
            'employee_name': format_text(getattr(employee, 'employee_name', "")),
            'id_number': format_text(getattr(employee, 'id_number', "")),
            'address': format_text(getattr(employee, 'address', "")),
            'city': format_text(getattr(employee, 'city', "")),
            'postal_code': format_text(getattr(employee, 'postal_code', "")),
            'mobile_phone': format_text(getattr(employee, 'mobile_phone', "")),
            'home_phone': format_text(getattr(employee, 'home_phone', "")),
            'email': format_text(getattr(employee, 'email', "")),
            'start_date': format_text(getattr(employee, 'start_date', "")),
            'aliyah_date': format_text(getattr(employee, 'aliyah_date', "")),
            'date_of_birth': format_text(getattr(employee, 'date_of_birth', "")),
            'bank_number': format_text(getattr(employee, 'bank_number', "")),
            'branch_number': format_text(getattr(employee, 'branch_number', "")),
            'account_number': format_text(getattr(employee, 'account_number', "")),
            'hourly_rate': format_currency(getattr(employee, 'hourly_rate', 0.0)),
            'total_work_days': format_currency(getattr(employee, 'total_work_days', 0.0)),
            'totals_lunch_value': format_currency(getattr(employee, 'totals_lunch_value', 0.0)),
            'total_missing_hours': format_hours(getattr(employee, 'total_missing_hours', 0.0)),
            'mobile_value': format_currency(getattr(employee, 'mobile_value', 0.0)),
            'clothing_value': format_currency(getattr(employee, 'clothing_value', 0.0)),
            'lunch_value': format_currency(getattr(employee, 'lunch_value', 0.0)),
            'cars_value': format_currency(getattr(employee, 'cars_value', 0.0)),
            'advance_payment_salary': format_currency(getattr(employee, 'advance_payment_salary', 0.0)),
            'monthly_city_tax_tops': format_currency(getattr(employee, 'monthly_city_tax_tops', 0.0)),
            'city_value_percentage': format_percentage(getattr(employee, 'city_value_percentage', 0.0)),
            'final_city_tax_benefit': format_currency(getattr(employee, 'final_city_tax_benefit', 0.0)),
            'basic_salary': format_currency(getattr(employee, 'basic_salary', 0.0)),
            'additional_payments': format_currency(getattr(employee, 'additional_payments', 0.0)),
            'net_value': format_currency(getattr(employee, 'net_value', 0.0)),
            'gross_salary': format_currency(getattr(employee, 'gross_salary', 0.0)),
            'gross_taxable': format_currency(getattr(employee, 'gross_taxable', 0.0)),
            'above_ceiling_value': format_currency(getattr(employee, 'above_ceiling_value', 0.0)),
            'above_ceiling_fund': format_currency(getattr(employee, 'above_ceiling_fund', 0.0)),
            'above_ceiling_compensation': format_currency(getattr(employee, 'above_ceiling_compensation', 0.0)),
            'pension_fund': format_currency(getattr(employee, 'pension_fund', 0.0)),
            'compensation': format_currency(getattr(employee, 'compensation', 0.0)),
            'study_fund': format_currency(getattr(employee, 'study_fund', 0.0)),
            'disability': format_currency(getattr(employee, 'disability', 0.0)),
            'miscellaneous': format_currency(getattr(employee, 'miscellaneous', 0.0)),
            'national_insurance': format_currency(getattr(employee, 'national_insurance', 0.0)),
            'salary_tax': format_currency(getattr(employee, 'salary_tax', 0.0)),
            'total_employer_contributions': format_currency(getattr(employee, 'total_employer_contributions', 0.0)),
            'total_salary_cost': format_currency(getattr(employee, 'total_salary_cost', 0.0)),
            'employee_pension_fund': format_currency(getattr(employee, 'employee_pension_fund', 0.0)),
            'self_employed_pension_fund': format_currency(getattr(employee, 'self_employed_pension_fund', 0.0)),
            'study_fund_deductions': format_currency(getattr(employee, 'study_fund_deductions', 0.0)),
            'miscellaneous_deductions': format_currency(getattr(employee, 'miscellaneous_deductions', 0.0)),
            'national_insurance_deductions': format_currency(getattr(employee, 'national_insurance_deductions', 0.0)),
            'health_insurance_deductions': format_currency(getattr(employee, 'health_insurance_deductions', 0.0)),
            'income_tax': format_currency(getattr(employee, 'income_tax', 0.0)),
            'income_tax_before_credit': format_currency(getattr(employee, 'income_tax_before_credit', 0.0)),
            'tax_point_child': format_percentage(getattr(employee, 'tax_point_child', 0.0)),
            'tax_credit_points': format_percentage(getattr(employee, 'tax_credit_points', 0.0)),
            'amount_tax_credit_points_monthly': format_currency(getattr(employee, 'amount_tax_credit_points_monthly', 0.0)),
            'tax_level_precente': format_percentage(getattr(employee, 'tax_level_precente', 0.0)),
            'total_salary_pension_funds': format_currency(getattr(employee, 'total_salary_pension_funds', 0.0)),
            'total_deductions': format_currency(getattr(employee, 'total_deductions', 0.0)),
            'net_payment': format_currency(getattr(employee, 'net_payment', 0.0)),
            'employee_pension_fund_yearly': format_currency(getattr(employee, 'employee_pension_fund_yearly', 0.0)),
            'self_employed_pension_fund_yearly': format_currency(getattr(employee, 'self_employed_pension_fund_yearly', 0.0)),
            'study_fund_deductions_yearly': format_currency(getattr(employee, 'study_fund_deductions_yearly', 0.0)),
            'miscellaneous_deductions_yearly': format_currency(getattr(employee, 'miscellaneous_deductions_yearly', 0.0)),
            'national_insurance_deductions_yearly': format_currency(getattr(employee, 'national_insurance_deductions_yearly', 0.0)),
            'health_insurance_deductions_yearly': format_currency(getattr(employee, 'health_insurance_deductions_yearly', 0.0)),
            'income_tax_yearly': format_currency(getattr(employee, 'income_tax_yearly', 0.0)),
            'amount_tax_credit_points_monthly_yearly': format_currency(getattr(employee, 'amount_tax_credit_points_monthly_yearly', 0.0)),
            'final_city_tax_benefit_yearly': format_currency(getattr(employee, 'final_city_tax_benefit_yearly', 0.0)),
            'pension_fund_yearly': format_currency(getattr(employee, 'pension_fund_yearly', 0.0)),
            'compensation_yearly': format_currency(getattr(employee, 'compensation_yearly', 0.0)),
            'study_fund_yearly': format_currency(getattr(employee, 'study_fund_yearly', 0.0)),
            'disability_yearly': format_currency(getattr(employee, 'disability_yearly', 0.0)),
            'miscellaneous_yearly': format_currency(getattr(employee, 'miscellaneous_yearly', 0.0)),
            'national_insurance_yearly': format_currency(getattr(employee, 'national_insurance_yearly', 0.0)),
            'salary_tax_yearly': format_currency(getattr(employee, 'salary_tax_yearly', 0.0)),          
            'total_employer_contributions_yearly': format_currency(getattr(employee, 'total_employer_contributions_yearly', 0.0)),          
            'total_salary_cost_yearly': format_currency(getattr(employee, 'total_salary_cost_yearly', 0.0)),          
            'sick_days_salary_yearly': format_currency(getattr(employee, 'sick_days_salary_yearly', 0.0)),
            'vacation_days_salary_yearly': format_currency(getattr(employee, 'vacation_days_salary_yearly', 0.0)),
            'sick_days_balance_yearly': format_currency(getattr(employee, 'sick_days_balance_yearly', 0.0)),
            'vacation_balance_yearly': format_currency(getattr(employee, 'vacation_balance_yearly', 0.0)),
            'gross_taxable_yearly': format_currency(getattr(employee, 'gross_taxable_yearly', 0.0)),
            'thirteenth_salary': format_currency(getattr(employee, 'thirteenth_salary', 0.0)),
            'work_percent': format_currency(getattr(employee, 'work_percent', 0.0)),
            'sick_days_salary': format_currency(getattr(employee, 'sick_days_salary', 0.0)),
            'vacation_days_salary': format_currency(getattr(employee, 'vacation_days_salary', 0.0)),
            'sick_days_entitlement': format_currency(getattr(employee, 'sick_days_entitlement', 0.0)),
            'vacation_days_entitlement': format_currency(getattr(employee, 'vacation_days_entitlement', 0.0)),
            'final_extra_hours_weekend': format_currency(getattr(employee, 'final_extra_hours_weekend', 0.0)),
            'final_extra_hours_regular': format_currency(getattr(employee, 'final_extra_hours_regular', 0.0)),
            'food_break_unpaid_salary': format_currency(getattr(employee, 'food_break_unpaid_salary', 0.0)),
            'hours125_regular_salary': format_currency(getattr(employee, 'hours125_regular_salary', 0.0)),
            'hours150_regular_salary': format_currency(getattr(employee, 'hours150_regular_salary', 0.0)),
            'hours150_holidays_saturday_salary': format_currency(getattr(employee, 'hours150_holidays_saturday_salary', 0.0)),
            'hours175_holidays_saturday_salary': format_currency(getattr(employee, 'hours175_holidays_saturday_salary', 0.0)),
            'hours200_holidays_saturday_salary': format_currency(getattr(employee, 'hours200_holidays_saturday_salary', 0.0)),
            'employee_number': format_text(getattr(employee, 'employee_number', "")),
            'marital_status': format_text(getattr(employee, 'marital_status', "")),
            'gender_status': format_text(getattr(employee, 'gender_status', "")),
            'work_apartment': format_text(getattr(employee, 'work_apartment', "")),
            'resident_status': format_text(getattr(employee, 'resident_status', "")),
            'hmo_member': format_text(getattr(employee, 'hmo_member', "")),
            'social_number': format_text(getattr(employee, 'social_number', "")),
            'irs_status': format_text(getattr(employee, 'irs_status', "")),
            'contract_status': format_text(getattr(employee, 'contract_status', "")),
            'kibbutz_member_status': format_text(getattr(employee, 'kibbutz_member_status', "")),
            'tax_credit_points': format_text(getattr(employee, 'tax_credit_points', "")),
            'tax_point_child': format_text(getattr(employee, 'tax_point_child', "")),
            'message': format_text(getattr(employee, 'message', ""))
        }

        # ----------------------
        # Build hoursData
        # ----------------------

        dailyFields = [
            'date', 'day', 'saturday', 'holiday', 'start-time', 'end-time',
            'hours-calculated', 'hours-calculated-regular-day', 'total-extra-hours-regular-day',
            'extra-hours125-regular-day', 'extra-hours150-regular-day', 'hours-holidays-day',
            'extra-hours150-holidays-saturday', 'extra-hours175-holidays-saturday', 'extra-hours200-holidays-saturday',
            'sick-day', 'day-off', 'food-break', 'final-totals-hours', 'calc1', 'calc2', 'calc3',
            'work-day', 'missing-work-day', 'advance-payment',
        ]

        monthlyFields = [
            "hours-calculated-monthly", "hours-calculated-regular-day-monthly", "total-extra-hours-regular-day-monthly",
            "extra-hours125-regular-day-monthly", "extra-hours150-regular-day-monthly", "hours-holidays-day-monthly",
            "extra-hours150-holidays-saturday-monthly", "extra-hours175-holidays-saturday-monthly",
            "extra-hours200-holidays-saturday-monthly", "sick-day-monthly", "day-off-monthly",
            "food-break-monthly", "final-totals-hours-monthly", "calc1-monthly", "calc2-monthly",
            "calc3-monthly", "work-day-monthly", "missing-work-day-monthly", "advance-payment-monthly"
        ]

        paidFields = [
            "hours-calculated-paid", "hours-calculated-regular-day-paid", "total-extra-hours-regular-day-paid",
            "extra-hours125-regular-day-paid", "extra-hours150-regular-day-paid", "hours-holidays-day-paid",
            "extra-hours150-holidays-saturday-paid", "extra-hours175-holidays-saturday-paid",
            "extra-hours200-holidays-saturday-paid", "sick-day-paid", "day-off-paid",
            "food-break-unpaid", "final-totals-hours-paid", "calc1-paid", "calc2-paid", "calc3-paid",
            "final-totals-lunch-value-paid", "final-total-extra-hours-weekend-monthly", "advance-payment-paid"
        ]

        # השאילתה מסננת לפי ה-ID הגלובלי המאובטח של העובד ולפי ה-company_id המבודד
        hours_rows = HoursData.query.filter_by(
            employee_id=employee.id,
            company_id=active_company_id,
            employeeMonth=month,
            employeeYear=year
        ).all()

        existing_record_found = len(hours_rows) > 0
        employee_data['existing_record_found'] = existing_record_found

        work_day_entries = []
        monthly_totals = {}
        paid_totals = {}

        # --- תוקן הרמטית: פריסה משולבת מתוך ה-row_data של השמירה למניעת שיבושי המכפלות במסך! ---
        for row in hours_rows:
            # המרה תקנית למילון פייתון
            row_dict = row.to_dict() if hasattr(row, 'to_dict') else row.__dict__
            
            # שואבים את הנתונים המרוכזים ששמרנו ב-combined_row_data
            tax_backup = row.row_data if (hasattr(row, 'row_data') and isinstance(row.row_data, dict)) else {}

            # אקטיבציה: שילוב בין רמת השורה לעמודת ה-JSON למניעת השמטות של פקודות ה-kebab-case
            combined_source = {**row_dict, **tax_backup}
            
            # לצורך התאמה חלקה ל-formulas.js, נמיר את מפתחות ה-snake_case בחזרה ל-kebab-case עבור הרינדור
            frontend_mapped_source = {}
            for k, v in combined_source.items():
                frontend_mapped_source[k] = v
                frontend_mapped_source[k.replace('_', '-')] = v

            # Daily rows
            if row_dict.get('date'):
                entry = {field: frontend_mapped_source.get(field, '') for field in dailyFields}
                work_day_entries.append(entry)

            # Monthly totals
            if not monthly_totals:
                for f in monthlyFields:
                    if frontend_mapped_source.get(f) not in [None, ""]:
                        monthly_totals = {field: frontend_mapped_source.get(field, '') for field in monthlyFields}
                        break

            # Paid totals
            if not paid_totals:
                for f in paidFields:
                    if frontend_mapped_source.get(f) not in [None, ""]:
                        paid_totals = {field: frontend_mapped_source.get(field, '') for field in paidFields}
                        break

        # Fallbacks for UI
        if not work_day_entries:
            work_day_entries = [{field: "" for field in dailyFields}]
        if not monthly_totals:
            monthly_totals = {field: "" for field in monthlyFields}
        if not paid_totals:
            paid_totals = {field: "" for field in paidFields}

        employee_data['hours'] = {
            "work_day_entries": work_day_entries,
            "monthly_totals": monthly_totals,
            "paid_totals": paid_totals
        }

        # הזרקת נתוני המסים הכוללים שחושבו בתוך ה-tax בחזרה ל-formulas.js (תוקן פלס הזחה מיושרת)
        if hours_rows and hasattr(hours_rows[0], 'row_data') and isinstance(hours_rows[0].row_data, dict):
            employee_data['tax'] = hours_rows[0].row_data
        else:
            employee_data['tax'] = {}

        return jsonify(employee_data)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'שגיאה פנימית בעיבוד נתוני השכר והשעות: {str(e)}'}), 500


# ----------------------
# Get Employee Data For Save Session Storage Data: Form Page
# ----------------------

@app.route('/get_employee_data')
@login_required
def get_employee_data():
    employee_id = request.args.get('employee_id', '').strip()
    month = request.args.get('employeeMonth', '').strip()
    year = request.args.get('employeeYear', '').strip()

    if not employee_id or not month or not year:
        return jsonify({'error': 'Missing parameters'}), 400

    # ----------------- COMPANY CONTEXT (סנכרון מולטי-חברה מאובטח) -----------------
    if session.get('owner_access') or getattr(current_user, 'role', '') == 'owner':
        active_company_id = OWNER_COMPANY_ID
    else:
        active_company_id = current_user.company_id

    if not active_company_id:
        return jsonify({'error': 'שגיאה: אין חברה פעילה משויכת למשתמש'}), 400

    # תוקן: שליפת העובד לפי ה-local_id או ה-id בתוך החברה הפעילה בלבד
    if employee_id.isdigit():
        employee_id_int = int(employee_id)
        employee = EmployeeData.query.filter(
            (EmployeeData.local_id == employee_id_int) | (EmployeeData.id == employee_id_int)
        ).filter_by(company_id=active_company_id).first()
    else:
        employee = EmployeeData.query.filter_by(employee_id=employee_id, company_id=active_company_id).first()

    if not employee:
        return jsonify({'error': 'Employee not found'}), 404

    date_key = f"{month}/{year}"

    # ----------------------
    # BASIC EMPLOYEE DATA
    # ----------------------
    employee_data = {
        'employee_id': employee_id,
        'employee_name': employee.employee_name,
        'employeeMonth': month,
        'employeeYear': year,
        'date': date_key,
        'id_number': employee.id_number,
        'address': employee.address,
        'city': employee.city,
        'postal_code': employee.postal_code,
        'mobile_phone': employee.mobile_phone,
        'home_phone': employee.home_phone,
        'email': employee.email,
        'start_date': employee.start_date,
        'aliyah_date': employee.aliyah_date,
        'date_of_birth': employee.date_of_birth,
        'bank_number': employee.bank_number,
        'branch_number': employee.branch_number,
        'account_number': employee.account_number,
        'hourly_rate': employee.hourly_rate,
        'total_work_days': employee.total_work_days,
        'totals_lunch_value': employee.totals_lunch_value,
        'total_missing_hours': employee.total_missing_hours,
        'tax_credit_points': employee.tax_credit_points,
        'monthly_city_tax_tops': employee.monthly_city_tax_tops,
        'city_value_percentage': employee.city_value_percentage,
        'final_city_tax_benefit': employee.final_city_tax_benefit,
        'basic_salary': employee.basic_salary,
        'additional_payments': employee.additional_payments,
        'cars_value': employee.cars_value,
        'net_value': employee.net_value,
        'gross_salary': employee.gross_salary,
        'above_ceiling_value': employee.above_ceiling_value,
        'above_ceiling_fund': employee.above_ceiling_fund,
        'above_ceiling_compensation': employee.above_ceiling_compensation,
        'gross_taxable': employee.gross_taxable,
        'pension_fund': employee.pension_fund,
        'compensation': employee.compensation,
        'study_fund': employee.study_fund,
        'disability': employee.disability,
        'miscellaneous': employee.miscellaneous,
        'national_insurance': employee.national_insurance,
        'salary_tax': employee.salary_tax,
        'total_employer_contributions': employee.total_employer_contributions,
        'total_salary_cost': employee.total_salary_cost,
        'employee_pension_fund': employee.employee_pension_fund,
        'self_employed_pension_fund': employee.self_employed_pension_fund,
        'study_fund_deductions': employee.study_fund_deductions,
        'miscellaneous_deductions': employee.miscellaneous_deductions,
        'national_insurance_deductions': employee.national_insurance_deductions,
        'health_insurance_deductions': employee.health_insurance_deductions,
        'income_tax': employee.income_tax,
        'advance_payment_salary': employee.advance_payment_salary,
        'total_deductions': employee.total_deductions,
        'net_payment': employee.net_payment,
        'employee_pension_fund_yearly': employee.employee_pension_fund_yearly,
        'self_employed_pension_fund_yearly': employee.self_employed_pension_fund_yearly,
        'study_fund_deductions_yearly': employee.study_fund_deductions_yearly,
        'miscellaneous_deductions_yearly': employee.miscellaneous_deductions_yearly,
        'national_insurance_deductions_yearly': employee.national_insurance_deductions_yearly,
        'health_insurance_deductions_yearly': employee.health_insurance_deductions_yearly,
        'income_tax_yearly': employee.income_tax_yearly,
        'amount_tax_credit_points_monthly_yearly': employee.amount_tax_credit_points_monthly_yearly,
        'final_city_tax_benefit_yearly': employee.final_city_tax_benefit_yearly,
        'pension_fund_yearly': employee.pension_fund_yearly,
        'compensation_yearly': employee.compensation_yearly,
        'study_fund_yearly': employee.study_fund_yearly,
        'disability_yearly': employee.disability_yearly,
        'miscellaneous_yearly': employee.miscellaneous_yearly,
        'national_insurance_yearly': employee.national_insurance_yearly,
        'salary_tax_yearly': employee.salary_tax_yearly,
        'total_employer_contributions_yearly': employee.total_employer_contributions_yearly,
        'total_salary_cost_yearly': employee.total_salary_cost_yearly,
        'sick_days_salary_yearly': employee.sick_days_salary_yearly,
        'vacation_days_salary_yearly': employee.vacation_days_salary_yearly,
        'sick_days_balance_yearly': employee.sick_days_balance_yearly,
        'vacation_balance_yearly': employee.vacation_balance_yearly,
        'gross_taxable_yearly': employee.gross_taxable_yearly,
        'tax_level_precente': employee.tax_level_precente,
        'income_tax_before_credit': employee.income_tax_before_credit,
        'amount_tax_credit_points_monthly': employee.amount_tax_credit_points_monthly,
        'thirteenth_salary': employee.thirteenth_salary,
        'work_percent': employee.work_percent,
        'sick_days_salary': employee.sick_days_salary,
        'vacation_days_salary': employee.vacation_days_salary,
        'sick_days_entitlement': employee.sick_days_entitlement,
        'vacation_days_entitlement': employee.vacation_days_entitlement,
        'final_extra_hours_weekend': employee.final_extra_hours_weekend,
        'final_extra_hours_regular': employee.final_extra_hours_regular,
        'food_break_unpaid_salary': employee.food_break_unpaid_salary,
        'hours125_regular_salary': employee.hours125_regular_salary,
        'hours150_regular_salary': employee.hours150_regular_salary,
        'hours150_holidays_saturday_salary': employee.hours150_holidays_saturday_salary,
        'hours175_holidays_saturday_salary': employee.hours175_holidays_saturday_salary,
        'hours200_holidays_saturday_salary': employee.hours200_holidays_saturday_salary,
        'mobile_value': employee.mobile_value,
        'clothing_value': employee.clothing_value,
        'contract_status': employee.contract_status,
        'kibbutz_member_status': employee.kibbutz_member_status,
        'lunch_value': employee.lunch_value,
        'total_salary_pension_funds': employee.total_salary_pension_funds,
        'employee_number': employee.employee_number,
        'marital_status': employee.marital_status,
        'gender_status': employee.gender_status,
        'hmo_member': employee.hmo_member,
        'resident_status': employee.resident_status,
        'message': employee.message
    }

    # ----------------------
    # ADD HOURS TABLE
    # ----------------------

    # השאילתה מסננת לפי ה-ID המאובטח והמבודד של העובד והחברה הפעילה
    hours_rows = HoursData.query.filter_by(
        employee_id=employee.id,
        company_id=active_company_id,
        employeeMonth=month,
        employeeYear=year
    ).all()

    employee_data['existing_record_found'] = len(hours_rows) > 0

    # FULL HOURS TABLE (ALL FIELDS)
    dailyFields = [
        'date', 'day', 'saturday', 'holiday',
        'start_time', 'end_time',
        'hours_calculated', 'hours_calculated_regular_day', 'total_extra_hours_regular_day',
        'extra_hours125_regular_day', 'extra_hours150_regular_day', 'hours_holidays_day',
        'extra_hours150_holidays_saturday', 'extra_hours175_holidays_saturday', 'extra_hours200_holidays_saturday',
        'sick_day', 'day_off', 'food_break',
        'final_totals_hours', 'calc1', 'calc2', 'calc3',
        'work_day', 'missing_work_day', 'advance_payment'
    ]

    monthlyFields = [
        'hours_calculated_monthly', 'hours_calculated_regular_day_monthly', 'total_extra_hours_regular_day_monthly',
        'extra_hours125_regular_day_monthly', 'extra_hours150_regular_day_monthly', 'hours_holidays_day_monthly',
        'extra_hours150_holidays_saturday_monthly', 'extra_hours175_holidays_saturday_monthly',
        'extra_hours200_holidays_saturday_monthly', 'sick_day_monthly', 'day_off_monthly',
        'food_break_monthly', 'final_totals_hours_monthly', 'calc1_monthly', 'calc2_monthly',
        'calc3_monthly', 'work_day_monthly', 'missing_work_day_monthly', 'advance_payment_monthly'
    ]

    paidFields = [
        'hours_calculated_paid', 'hours_calculated_regular_day_paid', 'total_extra_hours_regular_day_paid',
        'extra_hours125_regular_day_paid', 'extra_hours150_regular_day_paid', 'hours_holidays_day_paid',
        'extra_hours150_holidays_saturday_paid', 'extra_hours175_holidays_saturday_paid',
        'extra_hours200_holidays_saturday_paid', 'sick_day_paid', 'day_off_paid',
        'food_break_unpaid', 'final_totals_hours_paid', 'calc1_paid', 'calc2_paid', 'calc3_paid',
        'final_totals_lunch_value_paid', 'final_total_extra_hours_weekend_monthly', 'advance_payment_paid'
    ]

    daily_rows = []
    monthly_rows = []
    paid_rows = []

    for row in hours_rows:
        r = row.to_dict() if hasattr(row, 'to_dict') else row.__dict__

        if any(r.get(f) not in [None, "", "0"] for f in dailyFields):
            daily_rows.append(r)
            continue

        if any(r.get(f) not in [None, "", "0"] for f in monthlyFields):
            monthly_rows.append(r)
            continue

        if any(r.get(f) not in [None, "", "0"] for f in paidFields):
            paid_rows.append(r)
            continue

    employee_data["hours_data"] = {
        "daily": daily_rows,
        "monthly": monthly_rows,
        "paid": paid_rows
    }

    return jsonify(employee_data)





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
    session.pop('employee', None)
    session.pop('employee_id', None)
    session.pop('selected_employee_id', None)
    session.pop('selected_month', None)
    session.pop('selected_year', None)
    session.pop('employee_data', None)
    session.pop('hours_table', None)
    session.pop('month_result', None)
    session.pop('form_data', None)
    session.pop('last_search_results', None)

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

@app.route('/form_101', methods=['GET', 'POST'])
@login_required
def form_101():
    try:
        # 1. הכנות (Combo Boxes) וזיהוי השפה הפעילה
        language = get_lang()
        months = ['ינואר', 'פברואר', 'מרץ', 'אפריל', 'มאי', 'יוני',
                  'יולי', 'אוגוסט', 'ספטמבר', 'אוקטובר', 'נובמבר', 'דצמבר']
        years = list(range(2020, 2041))

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        # שליפת אובייקט החברה הפעילה ישירות מתוך ה-DB
        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------------------

        # 2. סינון העובדים - מציג אך ורק את עובדי החברה הנוכחית למניעת זליגת מידע!
        employees_list = EmployeeData.query.filter_by(company_id=active_company_id).order_by(EmployeeData.employee_name).all()
        
        today = datetime.today()
        default_month = f"{today.month:02d}"
        default_year = str(today.year)

        # 3. קבלת פרמטרים מנוהלת
        selected_employee_id = request.args.get('employee_id') or request.form.get('employee_id') or session.get('employee_id')
        selected_month = request.args.get('month') or request.form.get('month') or session.get('selected_month', default_month)
        selected_year = request.args.get('year') or request.form.get('year') or session.get('selected_year', default_year)

        selected_month_str = str(selected_month).zfill(2)
        selected_year_str = str(selected_year)

        session.update({
            'employee_id': selected_employee_id,
            'selected_month': selected_month_str,
            'selected_year': selected_year_str
        })

        # 4. שליפת עובד חוקי - מוודא שהוא שייך אך ורק לחברה הפעילה
        employee = None
        if selected_employee_id:
            employee = EmployeeData.query.filter_by(id=selected_employee_id, company_id=active_company_id).first()
        
        # 5. בניית form_data דינמי מתוך אובייקט החברה הרשמי ב-DB
        form_data = {
            'reportMonth': selected_month_str,
            'reportYear': selected_year_str,
            'companyName': company_obj.name or "",
            'taxFileNumber': company_obj.deduction_file or "",  # תיק ניכויים
            'companyAddress': company_obj.address or "",
            'companyPhone': company_obj.phone or "",
            'company_email': company_obj.email or "" 
        }

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

            form_data.update({
                'employee_id': employee.id,
                'employee_name': employee.employee_name, 
                'id_number': employee.id_number or "",
                'address': address_parts[0],
                'house_number': address_parts[1] if len(address_parts) > 1 else "",
                'city': employee.city or "",
                'zip_code': getattr(employee, 'postal_code', getattr(employee, 'zip_code', "")),
                'aliyah_date': employee.aliyah_date or "",
                'mobile_phone': employee.mobile_phone or "",
                'home_phone': employee.home_phone or "",
                'email': employee.email or "",
                'marital_status': employee.marital_status or "",
                'gender_status': employee.gender_status or "",
                'resident_status': employee.resident_status or "",
                'kibbutz_member_status': employee.kibbutz_member_status or "",
                'hmo_member': employee.hmo_member or "",
                'date_of_birth': formatted_birthday,
                'date': today.strftime('%d/%m/%Y')
            })

        if request.method == 'POST':
            # אבטחת ה-POST: מוודא שהעובד שלו מגישים את הטופס אכן שייך לחברה הנוכחית
            if selected_employee_id:
                valid_post_emp = EmployeeData.query.filter_by(id=selected_employee_id, company_id=active_company_id).first()
                if not valid_post_emp:
                    flash("שגיאה: אין הרשאה להגיש טופס עבור עובד זה", "danger")
                    return redirect(url_for('unauthorized'))

            form_payload = request.form.to_dict()
            form_data.update(form_payload)

            # חתימה
            signature_b64 = request.form.get('signature_data')
            if signature_b64:
                form_data['signature_data'] = signature_b64 

            # שליחת מייל עם HTML מלא (כולל כפתור הדפסה)
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

            # שמירה בבסיס נתונים - הוספת שדה חברה קריטי למניעת ערבוב הגשות!
            new_submission = Tofes101Submission(
                company_id=active_company_id,
                employee_id=selected_employee_id,
                month=selected_month_str, 
                year=selected_year_str,
                data_json=form_data, 
                submission_date=datetime.utcnow()
            )
            db.session.add(new_submission)
            db.session.commit()
            
            flash("Form sent successfully!", "success")
            return redirect(url_for('form_101'))

        # הוספת אובייקטי החברה (company ו-company_db) ישירות לרינדור מלא של 30 השפות בדסקטופ ובמובייל
        return render_template('form_101.html', 
                               company=company_obj,
                               company_db=company_obj,
                               employees=employees_list,
                               months=months,
                               years=years,
                               selected_employee_id=selected_employee_id,
                               employeeMonth=selected_month_str, 
                               employeeYear=selected_year_str,
                               form_data=form_data)

    except Exception as e:
        if 'db' in locals(): 
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
    session.pop('employee_id', None)
    session.pop('selected_employee_id', None)
    session.pop('form_data', None)
    
    # 2. סימון שהטופס נוקה (אופציונלי)
    session['clear_101'] = True
    
    flash('הטופס נוקה בהצלחה!', 'info')
    
    # 3. חזרה לדף הטופס - עכשיו הוא ייטען ריק בגלל שאין employee_id
    return redirect(url_for('form_101'))


# --------------------
#  Form 102 Form Data
# --------------------

@app.route('/form_102', methods=['GET', 'POST'])
@login_required
def form_102():
    try:
        # 1. הכנות בסיסיות וניהול זמן
        now = datetime.now()
        months = range(1, 13)
        years = range(now.year - 2, now.year + 3)

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        # שליפת אובייקט החברה הפעילה ישירות מתוך ה-DB
        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------------------

        # 2. סינון העובדים - מציג אך ורק את עובדי החברה הנוכחית למניעת זליגה!
        employees = EmployeeData.query.filter_by(company_id=active_company_id).all()

        # 3. ניהול זמן (חודש ושנה)
        selected_month = int(request.args.get('month') or session.get('selected_month', now.month))
        selected_year = int(request.args.get('year') or session.get('selected_year', now.year))
        s_month_str = str(selected_month).zfill(2)
        s_year_str = str(selected_year)
        
        session.update({'selected_month': selected_month, 'selected_year': selected_year})

        # 4. טיפול ב-AJAX - מנרמל ותומך בשני סוגי המזהים (ת"ז מול ID סידורי) למניעת קריסה
        if request.method == 'GET' and request.args.get('action') == 'get_employee_data':
            employee_id = request.args.get('employee_id')
            
            # בדיקה בטוחה אם הפרמטר הוא ת"ז ארוכה או ID קצר, ושליפת העובד המשויך לחברה
            valid_emp = EmployeeData.query.filter(
                (EmployeeData.id_number == str(employee_id)) | 
                (EmployeeData.id == (int(employee_id) if str(employee_id).isdigit() and len(str(employee_id)) < 8 else -1))
            ).filter(EmployeeData.company_id == active_company_id).first()
            
            if not valid_emp:
                return jsonify({'success': False, 'message': 'Unauthorized'}), 403
                
            details = get_employee_details(valid_emp.id, s_month_str, s_year_str)
            if details:
                session['employee_data'] = details
                session['employee_id'] = valid_emp.id_number
            return jsonify({'success': bool(details), **(details or {})})

        # 5. שליפת נתוני שכר מ-DB - מסונן הרמטית לפי חברה, חודש ושנה!
        current_month_records = HoursData.query.filter_by(
            company_id=active_company_id,
            employeeMonth=s_month_str, 
            employeeYear=s_year_str
        ).all()

        # 5. אתחול מונים לחישוב
        processed_employee_ids = set()
        total_gross = total_income_tax = total_ni_employee = total_health = 0.0
        total_study_fund_deductions = emp_pension = self_pension = 0.0
        pension_val = comp_val = disability_val = study_fund_val = 0.0
        regular_salary = reduced_salary = 0.0
        regular_count = reduced_count = 0

        def cn(val):
            try: return float(str(val).replace("₪", "").replace(",", "").strip())
            except: return 0.0

        # 6. לופ חישובים - מבודד ומחשב נתונים בצורה מאובטחת
        for rec in current_month_records:
            if rec.employee_id in processed_employee_ids:
                continue
            
            tax_data = rec.row_data if isinstance(rec.row_data, dict) else {}
            if not tax_data: continue
            
            processed_employee_ids.add(rec.employee_id)
            gross_taxable = cn(tax_data.get("gross_taxable", 0))
            
            dob = tax_data.get("date_of_birth")
            age = 0
            if dob:
                try:
                    birth = datetime.strptime(dob, "%Y-%m-%d")
                    age = now.year - birth.year
                except: pass

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

        # 7. חישובים סופיים
        final_emp_pension_combined = emp_pension + self_pension
        final_emp_deductions_total = (total_ni_employee + total_health + final_emp_pension_combined + total_study_fund_deductions)
        total_employer_pension_combined = (pension_val + comp_val + disability_val + study_fund_val)
        final_totals_paid = final_emp_deductions_total + total_employer_pension_combined

        # 8. בניית form_data מלא ל-Template מתוך נתוני ה-DB המאובטחים
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

        # 9. שמירה ל-DATABASE (POST) - מבוצר הרמטית למולטי-חברות!
        if request.method == 'POST':
            incoming_data = request.form.to_dict()
            
            if 'employee_id' in incoming_data:
                session['employee_id'] = incoming_data['employee_id']

            report = Form102Report.query.filter_by(
                company_id=active_company_id,
                report_month=s_month_str, 
                report_year=s_year_str
            ).first()

            if report:
                report.data_json = incoming_data
            else:
                report = Form102Report(
                    company_id=active_company_id,
                    report_month=s_month_str,
                    report_year=s_year_str,
                    data_json=incoming_data
                )
                db.session.add(report)

            db.session.commit()
            flash('טופס 102 נשמר בהצלחה בבסיס הנתונים!', 'success')
            return redirect(url_for('form_102', month=selected_month, year=selected_year))

        # 10. סגירה רשמית ותקינה של ה-Template עם ה-Context המלא
        return render_template(
            'form_102.html', 
            form_data=form_data, 
            company=company_obj,
            company_db=company_obj,
            employees=employees,
            employee_data=session.get('employee_data', {}),
            selected_employee_id=session.get('employee_id', "")
        )

    except Exception as e:
        if 'db' in locals(): db.session.rollback()
        import traceback
        traceback.print_exc()
        flash("שגיאה בטעינת טופס 102", "danger")
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

        # 1. חילוץ פרמטרים - מוודא שכלום לא None
        emp = data.get("employer", {})
        month = str(emp.get("reportMonth") or data.get("reportMonth", "01")).zfill(2)
        year = str(emp.get("reportYear") or data.get("reportYear", "2024"))
        xml_content = data.get("xml", "")

        # 2. חיפוש דוח קיים
        report = Form102Report.query.filter_by(report_month=month, report_year=year).first()
        
        if report:
            # עדכון קיים
            report.data_json = data
            report.xml_content = xml_content
            # מילוי שדות חובה כדי למנוע את ה-IntegrityError בעדכון
            if hasattr(report, 'employee_id'): report.employee_id = 0
            if hasattr(report, 'tax_response'): report.tax_response = ""
        else:
            # יצירה חדשה - כאן אנחנו דוחפים ערכי ברירת מחדל לשדות ה-NOT NULL
            # שים לב לשמות השדות (employee_id, tax_response) - זה מה ש-SQLite מחפש
            report = Form102Report(
                report_month=month,
                report_year=year,
                data_json=data,
                xml_content=xml_content,
                employee_id=0,       # פותר את ה-Not Null של employee_id
                tax_response=""      # פותר את ה-Not Null של tax_response
            )
            db.session.add(report)
        
        db.session.commit()
        return jsonify({"success": True, "message": "נשמר ב-DB בהצלחה!"})

    except Exception as e:
        db.session.rollback()
        # הדפסה לטרמינל כדי לראות בדיוק איזה שדה חסר
        print(f"🚨 SQL ERROR: {e}") 
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
    # Ensure month is two digits (e.g., '01')
    month_str = str(month).zfill(2)
    
    # Form 102 search: Filter by BOTH month and year
    report = Form102Report.query.filter_by(
        report_month=month_str, 
        report_year=str(year)
    ).first()
    
    if not report or not report.xml_content:
        return f"""
        <script>
            alert('שגיאה: לא נמצא דוח שמור לחודש {month_str}/{year}');
            window.history.back();
        </script>
        """, 404

    # Return the file
    filename = f"form102_{month_str}_{year}.xml"
    return Response(
        report.xml_content,
        mimetype='application/xml',
        headers={"Content-Disposition": f"attachment;filename={filename}"}
    )

# --------------------
# Send Form 102 To Tax Office Requst Data
# ----------------------

@app.route('/send_102_to_tax', methods=['POST'])
@login_required
def send_102_to_tax():
    # 1. בדיקת הרשאות (רק בעלים)
    if not session.get("owner_access"):
        flash("⛔ רק בעלים יכול לשדר לרשות המיסים.", "danger")
        return redirect(url_for('form_102'))

    # 2. שליפת הדו"ח מה-Postgres במקום מהקובץ הפיזי
    # אנחנו משתמשים ב-ID ששמרנו ב-Session בצעד הקודם (submit_form_102)
    report_id = session.get("last_report_id")
    
    if not report_id:
        flash("דוח לא נמצא בזיכרון — אנא לחץ על 'הורד XML' קודם כדי לנעול את הנתונים.", "warning")
        return redirect(url_for('form_102'))

    report = Form102Report.query.get(report_id)

    if not report or not report.xml_content:
        flash('הנתונים ב-DB פגומים או לא קיימים — צור את הדו"ח מחדש.', "danger")
        return redirect(url_for('form_102'))

    # הפיכת ה-XML מה-DB לבייטס לצורך השידור
    xml_data = report.xml_content.encode('utf-8')

    # 3. הגדרות שידור (רשות המיסים)
    TAX_URL = "https://tax.gov.il/api/102/upload" # תחליף ל-URL האמיתי בייצור
    CERT_FILE = "certs/company_cert.pfx"
    CERT_PASS = "12345678" 

    try:
        # 4. ביצוע השידור בפועל עם התעודה הדיגיטלית
        response = requests.post(
            TAX_URL,
            data=xml_data,
            cert=(CERT_FILE, CERT_PASS),
            headers={"Content-Type": "text/xml"},
            timeout=30
        )

        # 5. תיעוד התשובה ב-DB (חשוב מאוד להוכחת שידור!)
        # במקום קובץ log, אנחנו שומרים את התשובה בתוך הרשומה ב-Postgres
        report.tax_response = response.text
        report.is_submitted = True
        db.session.commit()

        if response.status_code == 200:
            flash("✅ השידור בוצע בהצלחה! התקבלה אישור מרשות המיסים.", "success")
        else:
            flash(f"⚠️ השידור עבר אך התקבלה שגיאה מהשרת: {response.status_code}", "warning")
            
        return redirect(url_for('form_102'))

    except Exception as e:
        db.session.rollback()
        flash(f"❌ שגיאה קריטית בשידור: {str(e)}", "danger")
        return redirect(url_for('form_102'))


# --------------------
# Clear Form 102 Data
# ----------------------

@app.route('/clear_form102', methods=['POST'])
@login_required
def clear_form102():
    session['clear_102'] = True
    return redirect(url_for('form_102'))



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
#  Form B102 Form Data
# ----------------------

@app.route('/form_B102', methods=['GET', 'POST'])
@login_required
def form_B102():
    try:
        # 1. הכנות בסיסיות וניהול זמן
        now = datetime.now()
        months = range(1, 13)
        years = range(now.year - 2, now.year + 3)

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        # שליפת אובייקט החברה הפעילה ישירות מתוך ה-DB
        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------------------

        # 2. סינון העובדים - מציג אך ורק את עובדי החברה הנוכחית למניעת זליגה!
        employees = EmployeeData.query.filter_by(company_id=active_company_id).all()

        # 3. ניהול זמן נבחר
        selected_month = int(request.args.get('month') or session.get('selected_month', now.month))
        selected_year = int(request.args.get('year') or session.get('selected_year', now.year))
        s_month_str = str(selected_month).zfill(2)
        s_year_str = str(selected_year)
        
        session.update({'selected_month': selected_month, 'selected_year': selected_year})

        # 4. שליפת נתוני שכר מ-DB - מסונן הרמטית לפי חברה, חודש ושנה!
        current_month_records = HoursData.query.filter_by(
            company_id=active_company_id,
            employeeMonth=s_month_str, 
            employeeYear=s_year_str
        ).all()

        # 4. חישובי TOTALS
        processed_ids = set()
        t = {
            'gross': 0.0, 'tax': 0.0, 'ni': 0.0, 'health': 0.0, 'study': 0.0,
            'pension_emp': 0.0, 'pension_self': 0.0, 'pension_val': 0.0,
            'comp_val': 0.0, 'dis_val': 0.0, 'study_val': 0.0,
            'red_sal': 0.0, 'reg_sal': 0.0
        }
        red_count = reg_count = 0

        def cn(val):
            try: return float(str(val).replace("₪", "").replace(",", "").strip())
            except: return 0.0

        for rec in current_month_records:
            if rec.employee_id in processed_ids: continue
            
            tax_data = rec.row_data if isinstance(rec.row_data, dict) else {}
            if not tax_data: continue
            
            processed_ids.add(rec.employee_id)
            
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

            # לוגיקת גיל
            dob = tax_data.get("date_of_birth")
            age = 0
            if dob:
                try:
                    birth = datetime.strptime(dob, "%Y-%m-%d")
                    age = now.year - birth.year - ((now.month, now.day) < (birth.month, birth.day))
                except: pass

            if age < 18 or age >= 67:
                t['red_sal'] += g_taxable
                red_count += 1
            else:
                t['reg_sal'] += g_taxable
                reg_count += 1

        # חישובים מסכמים
        final_pension = t['pension_emp'] + t['pension_self']
        total_employer_pension = t['pension_val'] + t['comp_val'] + t['dis_val'] + t['study_val']
        final_paid = t['ni'] + t['health'] + final_pension + t['study'] + total_employer_pension
        final_national_paid = t['ni'] + t['health']

        # 5. בניית form_data מתוך נתוני ה-DB של החברה הפעילה
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
            'reduced_salary': round(t['red_sal'], 2)
        }

        # טיפול ב-POST (עדכון ידני)
        if request.method == 'POST':
            form_data.update(request.form.to_dict())
            session['form_data'] = form_data
            flash('נתוני טופס B102 עודכנו בהצלחה!', 'success')
            return redirect(url_for('form_B102'))

        session['form_data'] = form_data
        
        # העברת אובייקטי החברה (company ו-company_db) ישירות לרינדור מלא של 30 השפות בדסקטופ ובמובייל
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
        if 'db' in locals(): db.session.rollback()
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
        data = request.json
        if not data or 'employer' not in data:
            return jsonify(success=False, message="נתונים חסרים"), 400

        # 1. חילוץ פרמטרים לזיהוי
        month = str(data["employer"].get("reportMonth", "")).zfill(2)
        year = str(data["employer"].get("reportYear", ""))
        xml_content = data.get("xml", "")

        # 2. בניית ה-XML לפי ה-FIELD_MAP_B102 שלך
        # אנחנו משתמשים במפה שנתת כדי שה-XML יהיה בפורמט של מס הכנסה
        root = ET.Element("FormB102_Data")
        for tax_field, app_keys in FIELD_MAP_B102.items():
            # מושך את הערך מה-data['employer'] לפי המפתח הראשון במפה
            app_key = app_keys[0] if isinstance(app_keys, list) else app_keys
            val = data["employer"].get(app_key, 0)
            ET.SubElement(root, tax_field).text = str(val)

        final_xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        # 3. שמירה ל-Postgres (טבלת FormB102Report)
        report = FormB102Report.query.filter_by(report_month=month, report_year=year).first()
        
        if report:
            report.data_json = data
            report.xml_content = final_xml_str.decode('utf-8')
            report.created_at = datetime.utcnow()
        else:
            report = FormB102Report(
                report_month=month,
                report_year=year,
                data_json=data,
                xml_content=final_xml_str.decode('utf-8')
            )
            db.session.add(report)
        
        db.session.commit()
        session["last_b102_id"] = report.id # שומר ID לשידור עתידי

        # 4. יצירת תיקיות וקבצים פיזיים (גיבוי)
        base_dir = os.path.join(app.root_path, "static", "form_B102_reports")
        year_folder = os.path.join(base_dir, f"Form_B102_{year}")
        os.makedirs(year_folder, exist_ok=True)

        filename_base = f"FormB102_{month}_{year}"
        
        # שמירת JSON ו-XML פיזיים
        with open(os.path.join(year_folder, f"{filename_base}.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        with open(os.path.join(year_folder, f"{filename_base}.xml"), "w", encoding="utf-8") as f:
            f.write(final_xml_str.decode('utf-8'))

        return jsonify({
            "success": True,
            "message": "דוח B102 נשמר ב-DB ובשרת!",
            "db_id": report.id,
            "xml_file": f"{filename_base}.xml"
        })

    except Exception as e:
        db.session.rollback()
        print(f"B102 Save Error: {e}")
        return jsonify(success=False, message=str(e)), 500


# --------------------
#  Submit Form B102 XML Data To Postgres Folder
# ----------------------

@app.route('/submitB102', methods=['POST'])
@login_required
def submit_form_B102():
    try:
        form_data = session.get('form_data', {})
        form_data.update(request.form.to_dict())

        month = str(form_data.get('reportMonth')).zfill(2)
        year = str(form_data.get('reportYear'))

        # יצירת XML (לפי המבנה שאתה צריך ל-B102)
        root = ET.Element("FormB102_Data")
        for k, v in form_data.items():
            ET.SubElement(root, k).text = str(v)
        
        xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        # שמירה ב-DB (טבלה נפרדת ל-B102!)
        report = FormB102Report.query.filter_by(report_month=month, report_year=year).first()
        if report:
            report.data_json = form_data
            report.xml_content = xml_str.decode('utf-8')
        else:
            report = FormB102Report(
                report_month=month, report_year=year,
                data_json=form_data, xml_content=xml_str.decode('utf-8')
            )
            db.session.add(report)
        
        db.session.commit()
        session["last_b102_id"] = report.id

        filename = f"FormB102_{month}_{year}.xml"
        return Response(xml_str, mimetype="application/xml",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    except Exception as e:
        db.session.rollback()
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
    # 1. בדיקת הרשאות (רק בעלים)
    if not session.get("owner_access"):
        flash("⛔ רק בעלים יכול לשדר לרשות המיסים.", "danger")
        return redirect(url_for('form_B102'))

    # 2. שליפת הדו"ח מה-DB (לפי ה-ID ששמרנו ב-submitB102)
    report_id = session.get("last_b102_id")
    
    if not report_id:
        flash("דוח B102 לא נמצא — אנא לחץ על 'הורד XML' קודם כדי לנעול את הנתונים.", "warning")
        return redirect(url_for('form_B102'))

    # שליפה מהטבלה החדשה שבנינו
    report = FormB102Report.query.get(report_id)

    if not report or not report.xml_content:
        flash('הנתונים ב-DB פגומים או לא קיימים — צור את הדו"ח מחדש.', "danger")
        return redirect(url_for('form_B102'))

    # הפיכת ה-XML מה-DB לבייטס לצורך השידור
    xml_data = report.xml_content.encode('utf-8')

    # 3. הגדרות שידור (רשות המיסים - נתיב B102)
    TAX_URL = "https://tax.gov.il/api/B102/upload" 
    CERT_FILE = "certs/company_cert.pfx"
    CERT_PASS = "12345678" 

    try:
        # 4. ביצוע השידור בפועל
        response = requests.post(
            TAX_URL,
            data=xml_data,
            cert=(CERT_FILE, CERT_PASS),
            headers={"Content-Type": "text/xml"},
            timeout=30
        )

        # 5. תיעוד התשובה ב-DB (חשוב מאוד להוכחת שידור!)
        report.tax_response = response.text  # כאן נכנס ה-XML שהם מחזירים
        report.is_submitted = True
        db.session.commit()

        # בדיקת סטטוס תגובה
        if response.status_code == 200:
            flash("✅ שידור B102 בוצע בהצלחה! התקבל אישור מרשות המיסים.", "success")
        else:
            flash(f"⚠️ השידור עבר אך התקבלה תשובה חריגה (קוד {response.status_code})", "warning")
            
        return redirect(url_for('form_B102'))

    except Exception as e:
        db.session.rollback()
        print(f"B102 Submission Error: {e}")
        flash(f"❌ שגיאה בשידור B102: {str(e)}", "danger")
        return redirect(url_for('form_B102'))


# --------------------
# Clear Form B102 Data
# ----------------------

@app.route('/clear_formB102', methods=['POST'])
@login_required
def clear_formB102():
    session['clear_B102'] = True
    return redirect(url_for('form_B102'))



# --------------------
#  Form H102 Form Data
# ----------------------

@app.route('/form_H102', methods=['GET', 'POST'])
@login_required
def form_H102():
    try:
        # 1. הכנות בסיסיות וניהול זמן
        now = datetime.now()
        months = range(1, 13)
        years = range(now.year - 2, now.year + 3)

        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        # שליפת אובייקט החברה הפעילה ישירות מתוך ה-DB
        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------------------

        # 2. סינון העובדים - מציג אך ורק את עובדי החברה הנוכחית למניעת זליגה!
        employees = EmployeeData.query.filter_by(company_id=active_company_id).all()

        # 3. ניהול זמן (GET/Session)
        selected_month = int(request.args.get('month') or session.get('selected_month', now.month))
        selected_year = int(request.args.get('year') or session.get('selected_year', now.year))
        s_month_str = str(selected_month).zfill(2)
        s_year_str = str(selected_year)
        
        session.update({'selected_month': selected_month, 'selected_year': selected_year})

        # --- POST — עדכון נתונים ידני מאובטח ---
        if request.method == 'POST':
            # אם החליפו עובד בטופס
            new_emp_id = request.form.get('employee_id')
            if new_emp_id and new_emp_id != session.get('employee_id'):
                
                # 🔒 וידוא בטחוני שהעובד החדש אכן משויך לחברה הפעילה ב-Session
                valid_new_emp = EmployeeData.query.filter_by(id=new_emp_id, company_id=active_company_id).first()
                if not valid_new_emp:
                    flash('שגיאה: אין הרשאה לגשת לעובד זה', 'danger')
                    return redirect(url_for('unauthorized'))

                session['form_data'] = {}
                session['employee_id'] = new_emp_id
                return redirect(url_for('form_H102'))

            # שמירה ב-Session של מה שהוזן ידנית
            session['form_data'] = request.form.to_dict()
            flash('נתוני טופס H102 עודכנו בהצלחה!', 'success')
            return redirect(url_for('form_H102'))

        # 4. שליפת נתוני שכר מ-DB - מסונן הרמטית לפי חברה, חודש ושנה!
        current_month_records = HoursData.query.filter_by(
            company_id=active_company_id,
            employeeMonth=s_month_str, 
            employeeYear=s_year_str
        ).all()

        # 4. חישובי TOTALS
        processed_employee_ids = set()
        total_gross = total_income_tax = total_ni_employee = total_health = 0.0
        total_study_fund_deductions = emp_pension = self_pension = 0.0
        pension_val = comp_val = disability_val = study_fund_val = 0.0
        regular_salary = reduced_salary = 0.0
        regular_count = reduced_count = 0

        def cn(val):
            try: return float(str(val).replace("₪", "").replace(",", "").strip())
            except: return 0.0

        for rec in current_month_records:
            if rec.employee_id in processed_employee_ids:
                continue
            
            tax_data = rec.row_data if isinstance(rec.row_data, dict) else {}
            if not tax_data: continue
            
            processed_employee_ids.add(rec.employee_id)
            
            g_taxable = cn(tax_data.get("gross_taxable", 0))
            
            # חישוב גיל לטובת שכר רגיל/מופחת
            dob = tax_data.get("date_of_birth")
            age = 0
            if dob:
                try:
                    birth = datetime.strptime(dob, "%Y-%m-%d")
                    age = now.year - birth.year - ((now.month, now.day) < (birth.month, birth.day))
                except: pass

            if age < 18 or age >= 67:
                reduced_salary += g_taxable
                reduced_count += 1
            else:
                regular_salary += g_taxable
                regular_count += 1

            # סכימה (הכל ב-Floats)
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

        # חישובי ביניים ופנסיות
        final_emp_pension_combined = emp_pension + self_pension
        total_employer_pension_combined = pension_val + comp_val + disability_val + study_fund_val
        final_totals_paid = (total_ni_employee + total_health + final_emp_pension_combined + 
                             total_study_fund_deductions + total_employer_pension_combined)

        # ====== לוגיקת H102 אילת (20% זיכוי) ======
        eilat_regular_tax = total_income_tax
        eilat_benefit_20 = eilat_regular_tax * 0.20
        eilat_total_tax_after_benefit = eilat_regular_tax - eilat_benefit_20
        total_tax_all = eilat_total_tax_after_benefit # סיכום סופי

        # 5. עדכון form_data לשליחה לטמפלייט מתוך אובייקט ה-DB הרשמי של החברה
        form_data = session.get('form_data', {})
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
            
            # שדות ספציפיים לאילת/H102
            'eilat_benefit_20': round(eilat_benefit_20, 2),
            'eilat_tax_after_benefit': round(eilat_total_tax_after_benefit, 2),
            
            'reportMonth': s_month_str,
            'reportYear': s_year_str,
            'companyName': company_obj.name or "",
            'taxFileNumber': company_obj.deduction_file or "",
            'companyAddress': company_obj.address or ""
        })

        # חילוץ עיר מתוך כתובת המעסיק הרשמית
        address = form_data.get("companyAddress", "")
        parts = address.split(',')
        company_city = parts[1].strip() if len(parts) > 1 else ""

        session['form_data'] = form_data

        # העברת אובייקטי החברה (company ו-company_db) ישירות לטובת רינדור מושלם של 30 השפות במובייל
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
        if 'db' in locals(): db.session.rollback()
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
    import os, json
    data = request.json

    month = data["employer"]["reportMonth"]
    year = data["employer"]["reportYear"]

    # === יצירת תיקייה ראשית ===
    base_dir = os.path.join(app.root_path, "form_H102_report")
    os.makedirs(base_dir, exist_ok=True)

    # === יצירת תיקייה לפי שנה ===
    year_folder = os.path.join(base_dir, f"Form_H102_{year}")
    os.makedirs(year_folder, exist_ok=True)

    # === יצירת שמות קבצים ===
    filename_json = f"Form_H102_report_{month}_{year}.json"
    filename_xml = f"Form_H102_report_{month}_{year}.xml"

    json_path = os.path.join(year_folder, filename_json)
    xml_path = os.path.join(year_folder, filename_xml)

    # === שמירת JSON ===
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    # === שמירת XML ===
    xml_content = data["xml"]
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_content)

    return jsonify({
        "success": True,
        "json_file": filename_json,
        "xml_file": filename_xml,
        "folder": year_folder
    })

# --------------------
#  Submit Form H102 XML Data To Postgres Folder
# ----------------------

@app.route('/submitH102', methods=['POST'])
@login_required
def submit_form_H102():
    try:
        # 1. משיכת נתונים מה-Session ועדכון ידני מהטופס
        form_data = session.get('form_data', {})
        form_update = request.form.to_dict()
        form_data.update(form_update)

        if not form_data:
            return "שגיאה: אין נתוני טופס H102 בזיכרון", 400

        month = str(form_data.get('reportMonth', '')).zfill(2)
        year = str(form_data.get('reportYear', ''))

        # 2. בניית ה-XML באמצעות FIELD_MAP_H102
        root = ET.Element("FormH102_Data")
        
        for tax_key, app_keys in FIELD_MAP_H102.items():
            val = 0
            # המפה שלך היא דיקשנרי של רשימות
            if isinstance(app_keys, list):
                # מחפשים את הערך הראשון שקיים ב-form_data
                for key in app_keys:
                    if key in form_data:
                        val = form_data[key]
                        break
            else:
                val = form_data.get(app_keys, 0)
            
            ET.SubElement(root, tax_key).text = str(val)

        xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        # 3. שמירה ל-Postgres/SQLite (אחידות מלאה עם 102)
        report = FormH102Report.query.filter_by(
            report_month=month, 
            report_year=year
        ).first()
        
        if report:
            report.data_json = form_data
            report.xml_content = xml_str.decode('utf-8')
        else:
            report = FormH102Report(
                report_month=month,
                report_year=year,
                data_json=form_data,
                xml_content=xml_str.decode('utf-8')
            )
            db.session.add(report)
        
        db.session.commit()

        # 4. הכנה לשידור ושלח להורדה
        session["last_h102_id"] = report.id
        filename = f"FormH102_{month}_{year}.xml"
        
        return Response(
            xml_str,
            mimetype="application/xml",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        db.session.rollback()
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
    # 1. בדיקת הרשאת בעלים (אבטחה ראשונה)
    if not session.get("owner_access"):
        flash("⛔ רק בעלים יכול לשדר לרשות המיסים.", "danger")
        return redirect(url_for('form_H102'))

    # 2. שליפת הדו"ח מה-DB (לפי ה-ID שנשמר ב-submitH102)
    # אנחנו לא מחפשים קובץ בתיקייה, אנחנו הולכים ישר למקור ב-Postgres/SQLite
    report_id = session.get("last_h102_id")
    
    if not report_id:
        flash("דוח H102 לא נמצא בזיכרון — אנא לחץ על 'הורד XML' קודם כדי לנעול את הנתונים.", "warning")
        return redirect(url_for('form_H102'))

    # שליפה מהטבלה לפי ה-ID
    report = FormH102Report.query.get(report_id)

    if not report or not report.xml_content:
        flash("הנתונים ב-DB פגומים או לא קיימים — אנא הפק את הדו\"ח מחדש.", "danger")
        return redirect(url_for('form_H102'))

    # 3. הכנת הנתונים לשידור
    # ה-XML כבר נבנה ב-submitH102 לפי ה-FIELD_MAP_H102, אנחנו רק הופכים אותו לבייטס
    xml_data = report.xml_content.encode('utf-8')

    # הגדרות שידור (H102 Endpoint)
    TAX_URL = "https://tax.gov.il/api/H102/upload"
    CERT_FILE = "certs/company_cert.pfx"
    CERT_PASS = "12345678" 

    try:
        # 4. שידור בפועל עם התעודה הדיגיטלית (PFX)
        response = requests.post(
            TAX_URL,
            data=xml_data,
            cert=(CERT_FILE, CERT_PASS),
            headers={"Content-Type": "text/xml"},
            timeout=30
        )

        # 5. תיעוד התגובה בתוך ה-DB (החלק הכי חשוב בברית!)
        # אנחנו שומרים את הסטטוס ואת התשובה המלאה של השרת
        report.tax_response = response.text
        report.is_submitted = (response.status_code == 200)
        db.session.commit()

        # בדיקת סטטוס תגובה להצגה למשתמש
        if response.status_code == 200:
            flash("✅ שידור H102 בוצע בהצלחה! אישור ה-XML נשמר ב-DB.", "success")
        else:
            flash(f"⚠️ השידור הסתיים עם קוד {response.status_code}. בדוק את תשובת השרת ביומן.", "warning")
            
        return redirect(url_for('form_H102'))

    except Exception as e:
        db.session.rollback()
        print(f"H102 Send Error: {e}")
        flash(f"❌ שגיאה בשידור H102: {str(e)}", "danger")
        return redirect(url_for('form_H102'))


# --------------------
# Clear Form H102 Data
# ----------------------

@app.route('/clear_formH102', methods=['POST'])
@login_required
def clear_formH102():
    session['clear_H102'] = True
    return redirect(url_for('form_H102'))


# --------------------
#  Form 126 Form Data
# ----------------------

@app.route('/form_126', methods=['GET', 'POST'])
@login_required
def form_126():
    try:
        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        # שליפת אובייקט החברה הפעילה ישירות מתוך ה-DB
        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))

        # 1. סינון העובדים - מציג אך ורק את עובדי החברה הנוכחית בדו"ח השנתי!
        employees = EmployeeData.query.filter_by(company_id=active_company_id).all()
                
        selected_year = str(session.get('selected_year', datetime.now().year))
        years = range(datetime.now().year - 2, datetime.now().year + 2)

        # 2. טיפול ב-POST - עדכון שנה מאובטח
        if request.method == 'POST':
            if request.form.get('reportYear'):
                session['selected_year'] = request.form.get('reportYear')
            return redirect(url_for('form_126'))

        form_data = {}
        
        # --- UNIQUE GLOBAL TOTAL NAMES (to prevent breaking the loop) ---
        GRAND_TOTAL_GROSS = 0.0
        GRAND_TOTAL_TAX = 0.0
        GRAND_TOTAL_NI = 0.0
        GRAND_TOTAL_HEALTH = 0.0
        GRAND_TOTAL_PENSION = 0.0
        GRAND_TOTAL_STUDY = 0.0
        GRAND_COUNT_EMPLOYEES = 0

        def cn(val):
            try: return float(str(val).replace("₪", "").replace(",", "").strip())
            except: return 0.0

        # LOOP ALL EMPLOYEES
        for emp in employees:
            eid = str(emp.id)
            yearly_records = HoursData.query.filter_by(employee_id=emp.id, employeeYear=selected_year).all()
            
            records_by_month = {}
            for rec in yearly_records:
                if rec.employeeMonth not in records_by_month or rec.id > records_by_month[rec.employeeMonth].id:
                    records_by_month[rec.employeeMonth] = rec

            if not records_by_month:
                continue

            # Employee specific accumulators
            e_gross = 0.0
            e_tax = 0.0
            e_pension = 0.0
            e_ni = 0.0
            e_health = 0.0
            e_study = 0.0

            for rec in records_by_month.values():
                data = rec.row_data if isinstance(rec.row_data, dict) else {}
                tax_data = data.get("hours_table", {}).get("tax", data)
                
                e_gross += cn(tax_data.get("gross_taxable", 0))
                e_tax   += cn(tax_data.get("income_tax", 0))
                e_ni    += cn(tax_data.get("national_insurance_deductions", 0))
                e_health += cn(tax_data.get("health_insurance_deductions", 0))
                e_pension += (cn(tax_data.get("employee_pension_fund", 0)) + cn(tax_data.get("self_employed_pension_fund", 0)))
                e_study += cn(tax_data.get("study_fund_deductions", 0))

            # --- Update Individual Employee Fields ---
            form_data.update({
                f'employee_name_{eid}': emp.employee_name,
                f'id_number_{eid}': emp.id_number,
                f'grossSalary_{eid}': f"{e_gross:.2f}",
                f'incomeTax_{eid}': f"{e_tax:.2f}",
                f'nationalInsurance_{eid}': f"{e_ni:.2f}",
                f'healthInsurance_{eid}': f"{e_health:.2f}",
                f'pensionDeduction_{eid}': f"{e_pension:.2f}",
                f'providentDeduction_{eid}': f"{e_study:.2f}",
                f'monthsWorked_{eid}': len(records_by_month)
            })

            # --- Update UNIQUE Global Totals ---
            GRAND_TOTAL_GROSS += e_gross
            GRAND_TOTAL_TAX += e_tax
            GRAND_TOTAL_NI += e_ni
            GRAND_TOTAL_HEALTH += e_health
            GRAND_TOTAL_PENSION += e_pension
            GRAND_TOTAL_STUDY += e_study
            GRAND_COUNT_EMPLOYEES += 1

        # Final Deduction calculation for the whole company
        GRAND_TOTAL_DEDUCTIONS = GRAND_TOTAL_TAX + GRAND_TOTAL_NI + GRAND_TOTAL_HEALTH + GRAND_TOTAL_PENSION + GRAND_TOTAL_STUDY

        # Global Company Info & Totals מתוך אובייקט ה-DB הרשמי
        form_data.update({
            'companyName': company_obj.name or "",
            'taxFileNumber': company_obj.deduction_file or "",
            'companyId': company_obj.company_id_number or "",
            'companyAddress': company_obj.address or "",
            'companyPhone': company_obj.phone or "",
            'reportYear': selected_year,
            
            # Summary Fields
            'NumEmployees': GRAND_COUNT_EMPLOYEES,
            'grossSalary': f"{GRAND_TOTAL_GROSS:,.2f}",
            'incomeTax': f"{GRAND_TOTAL_TAX:,.2f}",
            'nationalInsurance': f"{GRAND_TOTAL_NI:,.2f}",
            'healthInsurance': f"{GRAND_TOTAL_HEALTH:,.2f}",
            'pensionDeposits': f"{GRAND_TOTAL_PENSION:,.2f}",
            'totalDeductions': f"{GRAND_TOTAL_DEDUCTIONS:,.2f}"
        })

        return render_template('form_126.html', form_data=form_data, employees=employees, years=years, employeeYear=selected_year, company=company_obj, company_db=company_obj)

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
        employees = EmployeeData.query.all()
        year = str(incoming_data.get('reportYear', datetime.now().year))

        # Helper: Clean numbers for Tax Office (no symbols, 2 decimal places)
        def cn(val):
            try:
                if not val: return "0.00"
                clean_val = str(val).replace("₪", "").replace(",", "").strip()
                return f"{float(clean_val):.2f}"
            except:
                return "0.00"

        # 1. CREATE OFFICIAL XML ROOT (Form 126 Structure)
        root = ET.Element("Form126_Report")
        
        # 2. HEADER RECORD (Type 04 Style) - Employer Info & Global Totals
        header = ET.SubElement(root, "EmployerHeader")
        ET.SubElement(header, "RecordType").text = "04"
        ET.SubElement(header, "TaxFileNumber").text = incoming_data.get("taxFileNumber", "")
        ET.SubElement(header, "CompanyName").text = incoming_data.get("companyName", "")
        ET.SubElement(header, "ReportYear").text = year
        
        # Global Summary Totals inside Header
        summary = ET.SubElement(header, "GlobalSummary")
        ET.SubElement(summary, "TotalEmployees").text = str(incoming_data.get("NumEmployees", "0"))
        ET.SubElement(summary, "TotalGrossSalary").text = cn(incoming_data.get("grossSalary", "0"))
        ET.SubElement(summary, "TotalIncomeTax").text = cn(incoming_data.get("incomeTax", "0"))
        ET.SubElement(summary, "TotalNationalInsurance").text = cn(incoming_data.get("nationalInsurance", "0"))
        ET.SubElement(summary, "TotalHealthInsurance").text = cn(incoming_data.get("healthInsurance", "0"))
        ET.SubElement(summary, "TotalDeductions").text = cn(incoming_data.get("totalDeductions", "0"))

        # 3. DETAIL RECORDS (Type 07 Style) - Individual Employees
        employees_container = ET.SubElement(root, "EmployeeRecords")

        for emp in employees:
            eid = str(emp.id)
            gross_val = incoming_data.get(f"grossSalary_{eid}", "0")
            
            # Only include employees who actually earned money this year
            if float(cn(gross_val)) == 0:
                continue 

            emp_node = ET.SubElement(employees_container, "EmployeeDetail")
            ET.SubElement(emp_node, "RecordType").text = "07"
            ET.SubElement(emp_node, "EmployeeID").text = incoming_data.get(f"idNumber_{eid}", "")
            ET.SubElement(emp_node, "EmployeeName").text = incoming_data.get(f"fullName_{eid}", "")
            
            # Salary & Deduction Breakdown
            ET.SubElement(emp_node, "GrossSalary").text = cn(gross_val)
            ET.SubElement(emp_node, "IncomeTax").text = cn(incoming_data.get(f"incomeTax_{eid}", "0"))
            ET.SubElement(emp_node, "NationalInsurance").text = cn(incoming_data.get(f"nationalInsurance_{eid}", "0"))
            ET.SubElement(emp_node, "HealthInsurance").text = cn(incoming_data.get(f"healthInsurance_{eid}", "0"))
            ET.SubElement(emp_node, "PensionDeduction").text = cn(incoming_data.get(f"pensionDeduction_{eid}", "0"))
            ET.SubElement(emp_node, "StudyFundDeduction").text = cn(incoming_data.get(f"providentDeduction_{eid}", "0"))
            ET.SubElement(emp_node, "MonthsWorked").text = str(incoming_data.get(f"monthsWorked_{eid}", "0"))

            # Save/Update individual record for the database (for history/retrieval)
            emp_report = Form126Report.query.filter_by(employee_id=eid, report_year=year).first()
            if not emp_report:
                emp_report = Form126Report(employee_id=eid, report_year=year)
                db.session.add(emp_report)
            
            # Store only the specific data for this employee
            emp_report.data_json = {k: v for k, v in incoming_data.items() if f"_{eid}" in k}
            emp_report.xml_content = ET.tostring(emp_node, encoding='utf-8').decode('utf-8')

        # 4. SAVE THE FULL MASTER XML (Global Record)
        # We use employee_id=0 as the "Master File" record for the whole company
        full_xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
        
        master_report = Form126Report.query.filter_by(employee_id=0, report_year=year).first()
        if not master_report:
            master_report = Form126Report(employee_id=0, report_year=year)
            db.session.add(master_report)
        
        master_report.data_json = incoming_data # Save full form state here
        master_report.xml_content = full_xml_str

        db.session.commit()

        flash("דוח 126 נשמר בהצלחה!", "success") 
        return redirect(url_for('form_126'))

    except Exception as e:
        db.session.rollback()
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
    # שולף את הרשומה המרכזית ששמרנו (ה-Master Record)
    report = Form126Report.query.filter_by(employee_id=0, report_year=str(year)).first()
    
    if not report or not report.xml_content:
        # הודעת שגיאה ידידותית אם שכחו לעשות Submit קודם
        return """
        <script>
            alert('שגיאה: לא נמצא דוח שמור לשנת {}. אנא לחץ על שמירה/Submit לפני ההורדה.');
            window.history.back();
        </script>
        """.format(year), 404

    # מחזיר את ה-XML כקובץ להורדה
    return Response(
        report.xml_content,
        mimetype='application/xml',
        headers={"Content-Disposition": f"attachment;filename=form126_{year}.xml"}
    )

# --------------------
# Clear Form 126 Data
# ----------------------

@app.route('/clear_form126', methods=['POST'])
@login_required
def clear_form126():
    session['form_data'] = {}          
    session['employee_id'] = None      
    session['clear_126'] = True        

    flash('הטופס נוקה בהצלחה!', 'info')
    return redirect(url_for('form_126'))



# --------------------
#  Form 161 Form Data
# ----------------------

@app.route('/form_161', methods=['GET', 'POST'])
@login_required
def form_161():
    try:
        # ----------------- COMPANY CONTEXT (נעילת מולטי-חברות הרמטית) -----------------
        user_role = (getattr(current_user, 'role', '') or session.get('role') or '').lower()
        if session.get('owner_access') or user_role == 'owner':
            active_company_id = OWNER_COMPANY_ID
        else:
            active_company_id = getattr(current_user, 'company_id', None) or session.get('company_id')

        if not active_company_id:
            flash("שגיאה: אין חברה פעילה משויכת למשתמש", "error")
            return redirect(url_for('login'))

        # שליפת אובייקט החברה הפעילה ישירות מתוך ה-DB 
        company_obj = db.session.get(Company, active_company_id)
        if not company_obj:
            flash("שגיאה: נתוני החברה לא נמצאו במערכת", "error")
            return redirect(url_for('index'))
        # ---------------------------------------------------------------------------

        # סינון העובדים - מציג בתיבת הבחירה אך ורק את עובדי החברה הנוכחית!
        employees = EmployeeData.query.filter_by(company_id=active_company_id).all()
        
        # 1. POST — שמירה ועדכון מאובטח
        if request.method == 'POST':
            employee_id = request.form.get('employee_id')
            
            # וידוא בטחוני קריטי שהעובד המעודכן שייך לחברה הנוכחית בלבד!
            employee = EmployeeData.query.filter_by(id=employee_id, company_id=active_company_id).first()
            if employee:
                # עדכון שדות לפי ה-FIELD_MAP_161
                for form_field, model_attrs in FIELD_MAP_161.items():
                    if form_field in request.form:
                        val = request.form.get(form_field)
                        for attr in model_attrs:
                            if hasattr(employee, attr): 
                                setattr(employee, attr, val)
                db.session.commit()
            else:
                flash('שגיאה: אין הרשאה לעדכן נתוני עובד זה', 'danger')
                return redirect(url_for('unauthorized'))
            
            session['form_161_data'] = request.form.to_dict()
            session['employee_id'] = employee_id
            flash('טופס 161 עודכן בהצלחה!', 'success')
            return redirect(url_for('form_161', employee_id=employee_id))

        # 2. GET — חישוב נתונים אוטומטי מאובטח
        employee_id = request.args.get('employee_id') or session.get('employee_id')
        form_data = session.get('form_161_data', {})

        if employee_id:
            # וידוא שהעובד הנשלף שייך לחברה הפעילה
            emp = EmployeeData.query.filter_by(id=employee_id, company_id=active_company_id).first()
            if emp:
                # משיכת השכר האחרון שלו מה-DB מסונן לפי החברה הנוכחית
                last_record = HoursData.query.filter_by(employee_id=employee_id, company_id=active_company_id)\
                              .order_by(HoursData.employeeYear.desc(), HoursData.employeeMonth.desc()).first()
                
                last_salary = 0.0
                if last_record and isinstance(last_record.row_data, dict):
                    last_salary = float(str(last_record.row_data.get('gross_taxable', 0)).replace(',',''))

                # חישוב ותק (שנים)
                seniority = 0
                if emp.start_date: 
                    try:
                        start = emp.start_date if isinstance(emp.start_date, datetime) else datetime.strptime(emp.start_date, '%Y-%m-%d')
                        end = datetime.now()
                        seniority = round((end - start).days / 365.25, 1)
                    except: 
                        seniority = 0

                # הזרקת נתונים לפורם מתוך אובייקט ה-DB של החברה והעובד
                form_data.update({
                    'employee_id': employee_id,
                    'employee_first_name': emp.employee_name.split()[0] if emp.employee_name else "",
                    'employee_last_name': " ".join(emp.employee_name.split()[1:]) if emp.employee_name else "",
                    'employee_id_number': emp.id_number,
                    'employee_address': getattr(emp, 'address', ''),
                    'employee_start_date': getattr(emp, 'start_date', ''),
                    'employee_date_of_birth': getattr(emp, 'date_of_birth', ''),
                    'last_salary': f"{last_salary:,.2f}",
                    'seniority_years': seniority,
                    'severance_paid': f"{(last_salary * seniority):,.2f}", 
                    'companyName': company_obj.name or "",
                    'companyId': company_obj.company_id_number or "",
                    'companyAddress': company_obj.address or "",
                    'companyPhone': company_obj.phone or "",
                    'companyEmail': company_obj.email or ""
                })
            else:
                # אם ניסו להזריק במרמה ID של חברה אחרת, מנקים את הבקשה
                employee_id = None
                form_data = {}

        session['form_161_data'] = form_data
        
        # העברת אובייקטי החברה (company ו-company_db) ישירות ל-HTML לרינדור לוגו ופרטים תקינים במובייל ובדסקטופ
        return render_template(
            'form_161.html', 
            form_data=form_data, 
            employees=employees, 
            selected_employee_id=employee_id,
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
        form_data = session.get('form_161_data', {})
        form_data.update(request.form.to_dict())
        employee_id = form_data.get('employee_id')

        if not employee_id:
            return "שגיאה: עובד לא נבחר", 400

        # בניית XML לפי המפה
        root = ET.Element("Form161_Data")
        for tax_key, app_keys in FIELD_MAP_161.items():
            val = 0
            for key in app_keys:
                if key in form_data:
                    val = form_data[key]
                    break
            clean_val = str(val).replace(",", "").replace("₪", "").strip()
            ET.SubElement(root, tax_key).text = clean_val

        xml_str = ET.tostring(root, encoding='utf-8', xml_declaration=True)

        # שמירה ב-Postgres
        report = Form161Report.query.filter_by(employee_id=employee_id).first()
        if report:
            report.data_json = form_data
            report.xml_content = xml_str.decode('utf-8')
        else:
            report = Form161Report(employee_id=employee_id, data_json=form_data, xml_content=xml_str.decode('utf-8'))
            db.session.add(report)
        
        db.session.commit()

        return Response(xml_str, mimetype="application/xml",
                        headers={"Content-Disposition": f"attachment; filename=Form161_{employee_id}.xml"})

    except Exception as e:
        db.session.rollback()
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

        # תוקן הרמטית: שימוש במודל החדש EmployeeData ואיתור לפי ה-local_id או ה-id והחברה הפעילה בלבד
        if str(employee_id_pk).isdigit():
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == int(employee_id_pk)) | (EmployeeData.id == int(employee_id_pk))
            ).filter_by(company_id=active_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=str(employee_id_pk), company_id=active_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל העובד לא נמצא בחברה הפעילה במערכת"}), 404
        # ---------------------------------------------------------------------------

        # תוקן: בדיקת מצב משמרת לפי ה-ID הכללי המאובטח של העובד (employee.id) והחברה שלו
        shift = ShiftState.query.filter_by(employee_id=employee.id, company_id=active_company_id).first()
        
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
                employee_id=employee.id,  # קיבוע ה-ID הגלובלי המאובטח
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
        if 'db' in locals() and db.session:
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

        # תוקן הרמטית: שימוש במודל החדש EmployeeData ואיתור לפי ה-local_id או ה-id והחברה הפעילה בלבד
        if str(employee_data_id).isdigit():
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == int(employee_data_id)) | (EmployeeData.id == int(employee_data_id))
            ).filter_by(company_id=active_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=str(employee_data_id), company_id=active_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל העובד לא נמצא בחברה הפעילה במערכת"}), 404
        # ---------------------------------------------------------------------------

        # תוקן: עדכון מצב משמרת לפי ה-ID הכללי המאובטח של העובד (employee.id) והחברה שלו
        shift = ShiftState.query.filter_by(employee_id=employee.id, company_id=active_company_id).first()
        
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
        if 'db' in locals() and db.session:
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

        # תוקן הרמטית: שימוש במודל החדש EmployeeData ואיתור לפי ה-local_id או ה-id והחברה הפעילה בלבד
        if str(employee_data_id).isdigit():
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == int(employee_data_id)) | (EmployeeData.id == int(employee_data_id))
            ).filter_by(company_id=active_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=str(employee_data_id), company_id=active_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל העובד לא נמצא בחברה הפעילה במערכת"}), 404
        # ---------------------------------------------------------------------------

        # תוקן: בדיקת מצב משמרת לפי ה-ID הכללי המאובטח של העובד (employee.id) והחברה שלו
        shift = ShiftState.query.filter_by(employee_id=employee.id, company_id=active_company_id).first()
        
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
        if 'db' in locals() and db.session:
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

        # שליפת מזהה העובד מהסשן או מהיוזר המחובר
        req_employee_id = session.get("employee_id") or current_user.id
        
        if not req_employee_id:
            return jsonify({"status": "error", "message": "Employee ID missing in session"}), 400

        # תוקן הרמטית: מציאת העובד במודל החדש EmployeeData לפי שילוב המזהה והחברה הפעילה
        if str(req_employee_id).isdigit():
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == int(req_employee_id)) | (EmployeeData.id == int(req_employee_id))
            ).filter_by(company_id=active_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=str(req_employee_id), company_id=active_company_id).first()

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

        # תוקן: העברת employee.id הגלובלי והמאובטח לקבלת נתיב קובץ ה-CSV המבודד והנכון של החברה
        target_folder, _, csv_file_path = get_company_clock_paths(
            active_company_id, employee.id, current_year, current_month
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
        if 'db' in locals() and db.session:
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

        if str(local_employee_id).isdigit():
            employee = EmployeeData.query.filter(
                (EmployeeData.local_id == int(local_employee_id)) | (EmployeeData.id == int(local_employee_id))
            ).filter_by(company_id=active_company_id).first()
        else:
            employee = EmployeeData.query.filter_by(employee_id=local_employee_id, company_id=active_company_id).first()

        if not employee:
            return jsonify({"status": "error", "message": "פרופיל העובד לא נמצא בחברה זו."}), 404
        # ---------------------------------------------------------------------------

        raw_date = data.get("date")
        if isinstance(raw_date, list) and len(raw_date) > 0:
            work_date_str = str(raw_date[0]).strip()
        else:
            work_date_str = str(raw_date).strip()

        existing_entry = Timesheet.query.filter_by(
            company_id=active_company_id,
            employee_id=employee.id,
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
                employee_id=employee.id,  
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
                target_folder, _, csv_file_path = get_company_clock_paths(active_company_id, employee.id, c_year, c_month)
                
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
        shift_state = ShiftState.query.filter_by(employee_id=employee.id, company_id=active_company_id).first()
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
        # --- תוקן הרמטית: קורא קודם את ה-employee_id שנשלח מהקומבו-בוקס של הדף ---
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

        # מציאת העובד המדויק של החברה הפעילה לפי ה-local_id או ה-id הגלובלי כדי לאבטח את השילוב
        if str(employee_id).isdigit():
            employee_profile = EmployeeData.query.filter(
                (EmployeeData.local_id == int(employee_id)) | (EmployeeData.id == int(employee_id))
            ).filter_by(company_id=active_company_id).first()
        else:
            employee_profile = EmployeeData.query.filter_by(employee_id=employee_id, company_id=active_company_id).first()

        if not employee_profile:
            return jsonify({'error': 'Employee not found in this company'}), 403

        #  שליפת המידע מקובץ ה-JSON בדיסק (לפי ה-ID המאובטח והחברה הפעילה)
        # ---------------------------------------------------------------------
        # אנחנו מעבירים את ה-ID האמיתי של העובד או את ה-local_id שלו לפונקציית הטעינה של השעון
        month_data = load_clock_hours(active_company_id, str(employee_profile.id), str(selected_year), str(selected_month)) or {}

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
            clean_entries = get_clockinout_days(selected_year, selected_month, str(employee_profile.id), active_company_id)

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

        # תוקן: קורא קודם את המזהה שהתקבל מהבקשה (עבור מנהל) ואם אין - לוקח את המשתמש הנוכחי (עבור עובד)
        req_emp_id = request.args.get('employee_id') or request.args.get('employeeId')
        
        if req_emp_id and req_emp_id.strip() != "":
            raw_id = req_emp_id.strip()
            # מוודא שזה העובד של החברה הפעילה בלבד
            employee_profile = EmployeeData.query.filter(
                (EmployeeData.local_id == int(raw_id)) | (EmployeeData.id == int(raw_id))
            ).filter_by(company_id=active_company_id).first()
            selected_employee_id = employee_profile.id if employee_profile else None
        else:
            selected_employee_id = session.get('employee_id') or current_user.id

        if not selected_employee_id:
            return jsonify([])

        # שליפת הנתיבים הנכונים בדיסק (הפונקציה שהחזרנו הבוקר לעבוד מול ה-id המאובטח)
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
            employees = EmployeeData.query.filter_by(id=active_employee_id, company_id=active_company_id).all()
        else:
            employees = EmployeeData.query.filter_by(company_id=active_company_id).order_by(EmployeeData.employee_name).all()

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

                    employee = EmployeeData.query.filter_by(id=selected_employee_id, company_id=active_company_id).first()
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
        
        employee = EmployeeData.query.filter_by(id=selected_employee_id, company_id=active_company_id).first() if selected_employee_id else None
        
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

        employee = EmployeeData.query.filter_by(id=employee_id, company_id=active_company_id).first()
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

        employee_profile = EmployeeData.query.filter_by(id=employee_id, company_id=active_company_id).first()
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
            employee = EmployeeData.query.filter_by(local_id=int(selected_employee_id), company_id=active_company_id).first()

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





# ---------------------------------------------------------------------------
#  Secure Route to Serve Employee JSONs from Render Persistent Disk
# ---------------------------------------------------------------------------
@app.route('/static/employees/<path:filename>')
@login_required
def serve_persistent_employees(filename):
    base_dir = app.config.get("EMPLOYEES_DIR")
    return send_from_directory(base_dir, filename)

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

