# -----------------------------
import os
import json
import logging
import calendar
import re
import shutil
import secrets
import base64
import time
import xml.etree.ElementTree as ET
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from flask_babel import Babel
import threading
import babel.dates
import babel.numbers
import random
# -----------------------------------------------------------
import openpyxl
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, flash
from playwright.sync_api import sync_playwright
from flask_mail import Mail, Message  
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity
from sqlalchemy import func, Text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as SQLSession
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
from functools import wraps 
from deep_translator import GoogleTranslator
from concurrent.futures import ThreadPoolExecutor

# -----------------------------
# Models & Logic 
# -----------------------------
from database import db, PasswordResetToken, Customer, BankAccount, Payment, Invoice, InvoiceItem, Product, User, OwnerUser 

# -----------------------------------------------------------
#  Load Environment & Init Flask
# -----------------------------------------------------------
load_dotenv()
app = Flask(__name__, static_folder="static")

# Detect Render
is_render = bool(os.environ.get("RENDER"))

# Fetch BASE_URL for HTTPS detection
base_url = os.environ.get("BASE_URL", "*")
is_https = base_url.startswith("https")

# -----------------------------------------------------------
#  Security, Session & Proxy Config
# -----------------------------------------------------------
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Ensure instance folder exists (important for session files)
os.makedirs(app.instance_path, exist_ok=True)

app.config.update(
    SESSION_PERMANENT=True,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),

    SESSION_COOKIE_SECURE=is_render,
    REMEMBER_COOKIE_SECURE=is_render,
    SESSION_COOKIE_HTTPONLY=True,
    REMEMBER_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax'
)

# -----------------------------
# Secret Key
# -----------------------------
app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)

# -----------------------------
# JWT Config
# -----------------------------
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", secrets.token_hex(32))

# -----------------------------
# Database Config (multi-option)
# -----------------------------
db_choice = os.getenv("DB_CHOICE", "sqlite").lower()

if db_choice == "postgres":
    uri = os.getenv("POSTGRES_URI")
    # תיקון קריטי: Render/Heroku שולחים postgres:// אבל SQLAlchemy 2.0 דורש postgresql://
    if uri and uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = uri
elif db_choice == "mysql":
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("MYSQL_URI")
elif db_choice == "mssql":
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("MSSQL_URI")
else:
    # Default to SQLite
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("SQLITE_URI", f"sqlite:///{os.path.join(app.instance_path, 'data.db')}")

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# -----------------------------
# Gmail Mail Configuration
# -----------------------------
app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER", "smtp.gmail.com")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

# -----------------------------
# Init Extensions
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
# Owner Credentials
# -----------------------------
OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = generate_password_hash(os.getenv("OWNER_PASSWORD"))


# ----------------------
# Getting Import Time GLOBAL LANGUAGE + COUNTRY Format All Processor
# ----------------------

def py_i18n(key):
    lang = request.cookies.get("lang", "he")
    path = os.path.join("static", f"{lang}.json")

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data.get(key, key)
    except:
        return key


@app.route("/set_language/<lang>")
def set_language(lang):
    resp = make_response(redirect(request.referrer or url_for("home")))
    resp.set_cookie("lang", lang, max_age=60*60*24*365)
    return resp


def get_lang():
    return (request.cookies.get("lang") or "he").lower()

def get_country():
    return (request.cookies.get("country") or "IL").upper()


# ----------------------
# BUILD LOCALE
# ----------------------

def get_locale():
    lang = get_lang()      # "en"
    country = get_country()  # "US"

    # Always enforce correct format: en_US, he_IL, fr_FR...
    return f"{lang}_{country}"


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
    if hasattr(date_value, "strftime"):
        pass
    else:
        if isinstance(date_value, str):
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%m-%d-%Y"):
                try:
                    date_value = datetime.strptime(date_value, fmt).date()
                    break
                except ValueError:
                    continue
            else:
                return date_value
        if not hasattr(date_value, "strftime"):
            return str(date_value)

    locale = get_locale()
    if locale in ["en_US"]:
        return date_value.strftime("%m-%d-%Y")
    if locale in ["ja_JP", "zh_CN", "ko_KR"]:
        return date_value.strftime("%Y-%m-%d")
    return date_value.strftime("%d-%m-%Y")

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


# בענן (Render) – שמור בתיקייה קבועה App TRANSLATIONS Path & Folders
# -----------------------------
# COMPANY TRANSLATION FOLDER
# -----------------------------

if os.environ.get("RENDER"):
    COMPANY_DIR = "/opt/render/project/src/company"
else:
    COMPANY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company")

os.makedirs(COMPANY_DIR, exist_ok=True)

# -----------------------------
# ITEMS TRANSLATION FOLDER
# -----------------------------
if os.environ.get("RENDER"):
    ITEMS_DIR = "/opt/render/project/src/static/items"
else:
    ITEMS_DIR = os.path.join(os.getcwd(), "static", "items")

os.makedirs(ITEMS_DIR, exist_ok=True)


# -----------------------------
# CUSTOMERS TRANSLATION FOLDER - FIXED FOR RENDER
# -----------------------------
if os.environ.get("RENDER"):
    CUSTOMERS_DIR = "/tmp/customers"
else:
    CUSTOMERS_DIR = os.path.join(os.getcwd(), "customers")

os.makedirs(CUSTOMERS_DIR, exist_ok=True)


# -----------------------------
#   App From Web To Translations
# -----------------------------

def generate_translations(text):
    if not text: return {} # Safety check
    
    languages = [
        "he","en","fr","es","de","ru","ar","zh-CN","ja","hi","pt","it","nl","sv",
        "tr","ko","pl","uk","fa","ro","cs","el","th","vi","bn","id","ms","tl",
        "hu","bg"
    ]
    result = {}

    def translate_single(lang):
        try:
            # Skip translation if it's already Hebrew
            if lang == "he": return lang, text
            # Stable translator from deep_translator
            translated = GoogleTranslator(source='auto', target=lang).translate(text)
            return lang, (translated if translated else text)
        except:
            return lang, text

    # FIX: Use max_workers=10 (Google sometimes blocks you if you hit them with 15 at once)
    with ThreadPoolExecutor(max_workers=10) as executor:
        translations = list(executor.map(translate_single, languages))

    for lang, translated_text in translations:
        result[lang] = translated_text
        
    return result


# ---------------------------------------------------------
# Flask-Login: User Loader (CRITICAL FOR SESSIONS)
# ---------------------------------------------------------
@login_manager.user_loader
def load_user(user_id):
    from database import User
    
    # 1. חיפוש רגיל ב-DB
    user = User.query.get(int(user_id))
    if user:
        return user

    # 2. אם אתה מחובר כ-Owner (0) אבל Postgres דורש משתמש בטבלה (1)
    if user_id == "0" or user_id == "1":
        # אנחנו יוצרים שורה ב-DB שתתאים לנתונים מה-.env שלך
        # ככה Postgres יזהה את ה-ID ולא יזרוק 500
        admin = User.query.filter_by(username=os.getenv("OWNER_USERNAME")).first()
        if not admin:
            admin = User(
                id=1, 
                username=os.getenv("OWNER_USERNAME"), 
                password=generate_password_hash(os.getenv("OWNER_PASSWORD")),
                role='admin'
            )
            db.session.add(admin)
            db.session.commit()
        return admin

    return None

# -----------------------------
# Decorators
# -----------------------------

def OWNER_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('owner_access'):
            flash(py_i18n('auth.owner_only'), 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask_login import current_user
        if not session.get('owner_access') and not current_user.is_authenticated:
            flash(py_i18n("auth.login_required"), "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('owner_access'):
            return f(*args, **kwargs)
        if session.get('role') != 'manager':
            flash(py_i18n("auth.manager_only"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def customer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('owner_access'):
            return f(*args, **kwargs)
        if session.get('role') != 'customer':
            flash(py_i18n("auth.customer_only"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def customer_self_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('owner_access'):
            return f(*args, **kwargs)

        selected_customer_id = kwargs.get('customer_id') or request.args.get('customer_id')

        if session.get('role') == 'customer':
            if str(session.get('customer_id')) != str(selected_customer_id):
                flash(py_i18n("auth.customer_self_only"), "danger")
                return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


def customer_or_manager_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('owner_access'):
            return f(*args, **kwargs)
        if session.get('role') not in ['customer', 'manager']:
            flash(py_i18n("auth.no_permission"), "danger")
            return redirect(url_for('unauthorized'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/unauthorized')
def unauthorized():
    role = session.get('role')
    if session.get('owner_access') or role == 'manager':
        return redirect('/invoice')
    if role == 'customer':
        return redirect('/customer_dashboard')
    return f"<h1>{py_i18n('auth.no_permission')}</h1>", 403

# -----------------------------
# Login Route
# -----------------------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        session.clear()
        return render_template('login.html')

    role = request.form.get('role')

    email = request.form.get('email', '').strip()
    customer_email = request.form.get('customer_email', '').strip()

    login_email = email if email else customer_email
    password = request.form.get('password', '').strip()
    selected_role = request.form.get('role', 'customer')

    # OWNER LOGIN
    if login_email == OWNER_USERNAME and check_password_hash(OWNER_PASSWORD, password):
        session.clear()
        owner_obj = OwnerUser(login_email)
        login_user(owner_obj)
        session['owner_access'] = True
        session['user_name'] = login_email
        session['role'] = 'owner'
        session['user_role'] = 'owner'
        flash(py_i18n('login.owner_success'), 'success')
        return redirect(url_for('invoice'))

    # NORMAL USER LOGIN
    user = User.query.filter_by(email=login_email).first()
    if not user or not check_password_hash(user.password_hash, password):
        flash(py_i18n("login.invalid_credentials"), "danger")
        return redirect(url_for('login'))

    if user.role != selected_role:
        flash(py_i18n('login.role_mismatch'), 'danger')
        return redirect(url_for('login'))

    session.clear()
    login_user(user)
    session['user_id'] = user.id
    session['user_name'] = user.email
    session['role'] = user.role
    session['user_role'] = user.role

    # MANAGER LOGIN
    if user.role == 'manager':
        customer = Customer.query.filter_by(user_id=user.id).first()
        if customer:
            session['customer_id'] = customer.id
            session['customer_name'] = customer.customer_name
        flash(py_i18n('login.manager_success'), 'success')
        return redirect(url_for('invoice'))

    # CUSTOMER LOGIN
    if user.role == 'customer':
        customer = Customer.query.filter_by(user_id=user.id).first()
        if customer:
            session['customer_id'] = customer.id
            session['customer_name'] = customer.customer_name
        flash(py_i18n('login.customer_success'), 'success')
        return redirect(url_for('customer_dashboard'))

    flash(py_i18n('login.success'), 'success')
    return redirect(url_for('invoice'))

# -----------------------------
# Logout
# -----------------------------

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    flash(py_i18n("auth.logout_success"), "info")
    return redirect(url_for('login'))

# -----------------------------
# Register
# -----------------------------

@app.route('/register', methods=['POST'])
def register():
    username = request.form.get('username', '').strip()
    email = request.form.get('email', '').strip()
    password = request.form.get('password', '').strip()
    role = request.form.get('role', 'customer')

    if not email or not password:
        flash(py_i18n('auth.register_missing_fields'), 'warning')
        return redirect(url_for('login'))

    existing = User.query.filter_by(email=email).first()
    if existing:
        flash(py_i18n('auth.register_email_exists'), 'warning')
        return redirect(url_for('login'))

    user = User(
        email=email,
        username=username or email,
        role=role
    )
    user.set_password(password)

    db.session.add(user)
    db.session.commit()

    flash(py_i18n('auth.register_success'), 'success')
    return redirect(url_for('login'))

# -----------------------------
# Clients (Owner only)
# -----------------------------

@app.route('/clients')
@OWNER_required
def clients():
    users = User.query.order_by(User.id.desc()).all()
    now_dt = datetime.utcnow()
    now = int(now_dt.timestamp())

    view_users = []
    for u in users:
        if u.access_expires_at:
            seconds_left = int((u.access_expires_at - now_dt).total_seconds())
        else:
            seconds_left = -1  # ללא הגבלה

        # STATUS LABELS WITH TRANSLATION
        if not u.is_active:
            status_label = py_i18n("client.status_blocked")
        elif u.access_expires_at and seconds_left <= 0:
            status_label = py_i18n("client.status_expired")
        else:
            status_label = py_i18n("client.status_active")

        view_users.append({
            'id': u.id,
            'username': u.username or u.email,
            'email': u.email,
            'role': u.role,
            'created_at': u.created_at,
            'last_login': u.last_login,
            'seconds_left': max(0, seconds_left),
            'status_label': status_label,
        })

    return render_template('clients.html', users=view_users, now=now, is_owner=True)

# -----------------------------
# Update Access (Owner only)
# -----------------------------

@app.route('/update_access', methods=['POST'])
@OWNER_required
def update_access():
    email = request.form.get('email')
    status = request.form.get('status')      # 'active' / 'blocked'
    duration = request.form.get('duration')  # seconds or '' (unlimited)

    user = User.query.filter_by(email=email).first()
    if not user:
        flash(py_i18n("client.not_found"), "danger")
        return redirect(url_for('clients'))

    user.is_active = (status == 'active')

    if duration and duration.isdigit():
        seconds = int(duration)
        user.access_expires_at = datetime.utcnow() + timedelta(seconds=seconds)
    else:
        user.access_expires_at = None  # unlimited

    db.session.commit()
    flash(py_i18n("client.access_updated"), "success")
    return redirect(url_for('clients'))

# -----------------------------
# Create Reset Token (Owner only)
# -----------------------------

def create_reset_token(user):
    token = secrets.token_urlsafe(32)
    expires = datetime.utcnow() + timedelta(hours=1)

    entry = PasswordResetToken(
        user_id=user.id,
        token=token,
        expires_at=expires
    )
    db.session.add(entry)
    db.session.commit()

    return token


# -----------------------------
# E-Mail Send Link (Owner only)
# -----------------------------

@app.route('/send-reset-link', methods=['POST'])
@OWNER_required
def send_reset_link():
    email = request.form.get('email')
    user = User.query.filter_by(email=email).first()

    token = create_reset_token(user)

    base_url = os.getenv('BASE_URL') or request.host_url.rstrip('/')
    reset_url = f"{base_url}/set-password?token={token}"

    msg = Message(
        py_i18n("reset.email_subject"),
        recipients=[email],
        body=f"{py_i18n('reset.email_body')}\n{reset_url}"
    )
    mail.send(msg)

    flash(py_i18n("reset.link_sent") + f" {reset_url}", "success")
    return redirect(url_for('clients'))


# -----------------------------
# Set Password (via token)
# -----------------------------

@app.route('/set-password', methods=['GET', 'POST'])
def set_password():

    # OWNER CAN ENTER WITHOUT TOKEN
    if session.get('owner_access'):
        return render_template('set_password.html')

    # customer REQUIRE TOKEN
    token = request.args.get('token')
    entry = PasswordResetToken.query.filter_by(token=token).first()

    if not entry or entry.expires_at < datetime.utcnow():
        flash(py_i18n("reset.invalid_or_expired_token"), "danger")
        return redirect(url_for('login'))

    user = User.query.get(entry.user_id)

    if request.method == 'POST':
        new_pass = request.form.get('password')
        if not new_pass:
            flash(py_i18n("reset.password_required"), "warning")
            return redirect(request.url)

        user.password_hash = generate_password_hash(new_pass)

        db.session.delete(entry)
        db.session.commit()

        flash(py_i18n("reset.password_updated"), "success")
        return redirect(url_for('login'))

    return render_template('set_password.html')

# -----------------------------
# Delete selected users (Owner only)
# -----------------------------

@app.route('/delete_selected_users', methods=['POST'])
@OWNER_required
def delete_selected_users():
    ids = request.form.getlist('delete_ids')

    if not ids:
        flash(py_i18n("client.delete_none_selected"), "warning")
        return redirect(url_for('clients'))

    for user_id in ids:
        user = User.query.get(user_id)
        if user:
            db.session.delete(user)

    db.session.commit()

    flash(py_i18n("client.deleted_count").format(count=len(ids)), "success")
    return redirect(url_for('clients'))


# -----------------------------
# Update role (Owner only)
# -----------------------------

@app.route('/update_role', methods=['POST'])
@OWNER_required
def update_role():
    email = request.form.get('email')
    new_role = request.form.get('role')

    if not email or not new_role:
        flash(py_i18n("client.role_invalid_data"), "danger")
        return redirect(url_for('clients'))

    user = User.query.filter_by(email=email).first()
    if not user:
        flash(py_i18n("client.not_found"), "danger")
        return redirect(url_for('clients'))

    user.role = new_role
    db.session.commit()

    flash(py_i18n("client.role_updated"), "success")
    return redirect(url_for('clients'))

# -----------------------------
# customer dashboard
# -----------------------------

@app.route('/customer-dashboard')
@login_required
def customer_dashboard():
    role = session.get('role')

    # OWNER 
    if session.get('owner_access'):
        return redirect(url_for('invoice'))

    # MANAGER 
    if role == 'manager':
        return redirect(url_for('invoice'))

    # customer 
    if role == 'customer':
        return redirect(url_for('invoice'))

    return redirect(url_for('invoice'))




# -----------------------------
#   Add Great Save Company Json Translations
# -----------------------------

@app.route("/admin/generate_company", methods=["POST"])
@login_required
def admin_generate_company():
    data = request.get_json(silent=True) or {}
    generate_company_translations()
    return jsonify({"status": "ok", "message": "Success"})

# -----------------------------
#  Translations Add Great Save Company Json File
# -----------------------------

def save_company_file(data):
    """Saves data specifically to the background COMPANY_DIR"""
    os.makedirs(COMPANY_DIR, exist_ok=True)
    file_path = os.path.join(COMPANY_DIR, "company.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    return file_path

def load_company_file():
    """Generic loader for the background company file"""
    file_path = os.path.join(COMPANY_DIR, "company.json")
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)

def generate_company_translations():
    """Reads from Root, translates, and saves to COMPANY_DIR"""
    # 1. Look for the source file in the Root directory
    root_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company.json")
    
    if not os.path.exists(root_path):
        print(f"Error: Source file not found at {root_path}")
        return

    with open(root_path, "r", encoding="utf-8") as f:
        base = json.load(f)

    # 2. Run translation logic
    translated = {}
    for key, value in base.items():
        translated[key] = generate_translations(value)

    # 3. Save to the background folder (COMPANY_DIR)
    save_company_file(translated)

def load_company_data():
    language = get_lang() 
    # 1. Map zh to zh-CN
    lookup_lang = "zh-CN" if language == "zh" else language

    trans_path = os.path.join(COMPANY_DIR, "company.json")
    root_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company.json")

    if os.path.exists(trans_path):
        final_path = trans_path
    elif os.path.exists(root_path):
        final_path = root_path
    else:
        return {}

    try:
        with open(final_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error loading company JSON: {e}")
        return {}

    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            # 2. Try the mapped language first, then Hebrew, then the first available value
            result[key] = value.get(lookup_lang) or value.get("he") or next(iter(value.values()), "")
        else:
            # 3. If it's already a simple string (like the logo often is), just use it
            result[key] = value
            
    return result


# -----------------------------
#  Translations Add Great Save Customer Json File
# -----------------------------

def translate_in_background(customer_id, name, address, city, message):
    name_trans = generate_translations(name or "")
    address_trans = generate_translations(address or "")
    city_trans = generate_translations(city or "")
    message_trans = generate_translations(message or "")
    save_customer_file(customer_id, name_trans, address_trans, city_trans, message_trans)


def save_customer_file(customer_id, name_trans, address_trans, city_trans, message_trans):
    customer_path = os.path.join(CUSTOMERS_DIR, str(customer_id))
    os.makedirs(customer_path, exist_ok=True)

    file_path = os.path.join(customer_path, f"{customer_id}.json")

    data = {
        "name": name_trans,
        "address": address_trans,
        "city": city_trans,
        "message": message_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return file_path


def load_customer_file(customer_id):
    file_path = os.path.join(CUSTOMERS_DIR, str(customer_id), f"{customer_id}.json")

    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None


def load_customer_translated(customer, language):
    # 1. טיפול במיפוי שפות מיוחדות (כמו סינית)
    special_mappings = {
        "zh": "zh-CN",
        "en": "en", # דוגמה אם תרצה להוסיף בעתיד
    }
    lookup_lang = special_mappings.get(language, language)
    
    # 2. ניסיון טעינת הקובץ
    data = load_customer_file(customer.id)

    # 3. אם אין קובץ (קורה הרבה ב-Render בגלל ה-Restart), נחזיר את נתוני ה-DB המקוריים
    if not data:
        return {
            "name": customer.customer_name or "",
            "address": customer.address or "",
            "city": customer.city or "",
            "message": customer.message or ""
        }

    # 4. פונקציית עזר פנימית לחילוץ התרגום (למניעת חזרתיות)
    def get_val(field_key, default_val):
        field_data = data.get(field_key, {})
        # סדר העדיפויות: השפה שנבחרה -> עברית (מקור) -> הערך בבסיס הנתונים -> מחרוזת ריקה
        return field_data.get(lookup_lang) or field_data.get("he") or default_val or ""

    return {
        "name": get_val("name", customer.customer_name),
        "address": get_val("address", customer.address),
        "city": get_val("city", customer.city),
        "message": get_val("message", customer.message)
    }


# -----------------------------
#  Translations Add Great Save Item Json File
# -----------------------------

def ensure_product_folder(product_id):
    product_path = os.path.join(ITEMS_DIR, str(product_id))
    images_path = os.path.join(product_path, "images")

    os.makedirs(product_path, exist_ok=True)
    os.makedirs(images_path, exist_ok=True)

    return product_path


def save_item_file(product_id, name_trans, desc_trans, price, category):
    product_path = ensure_product_folder(product_id)
    file_path = os.path.join(product_path, f"{product_id}.json")

    data = {
        "id": product_id,
        "price": price,
        "category": category,
        "name": name_trans,
        "description": desc_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return file_path


def load_item_file(product_id):
    file_path = os.path.join(ITEMS_DIR, str(product_id), f"{product_id}.json")

    if not os.path.exists(file_path):
        return None  

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ----------------------
#  Home Login Page
# ----------------------

@app.route('/')
def home():
    return redirect(url_for('login'))

# ----------------------
#  Company Form Page
# ----------------------

@app.route('/company', methods=['GET', 'POST'])
@login_required
def company():
    if request.method == 'GET':
        company_data = load_company_data() 
        return render_template('company.html', company=company_data)

    if request.method == 'POST':
        company_json = {
            "name": request.form.get('name', ''),
            "company_id_number": request.form.get('company_id_number', ''),
            "deduction_file": request.form.get('deduction_file', ''),
            "address": request.form.get('address', ''),
            "city": request.form.get('city', ''),
            "postal_code": request.form.get('postal_code', ''),
            "phone": request.form.get('phone', ''),
            "email": request.form.get('email', ''),
            "logo": request.form.get('logo', '')
        }

        #  Save to ROOT
        root_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company.json")
        with open(root_path, "w", encoding="utf-8") as f:
            json.dump(company_json, f, ensure_ascii=False, indent=4)

        #  GET CURRENT LANGUAGE from the hidden input we added to HTML
        current_lang = request.form.get('lang', 'he')

        #  Translate in background (Saves to COMPANY_DIR)
        thread = threading.Thread(target=generate_company_translations)
        thread.daemon = True # Helps Render not hang on restart
        thread.start()

        flash("Details saved! Translations updating in background.", "success")
        return redirect(url_for('company'))

# ----------------------
#  Clear Company Results Form 
# ----------------------

@app.route('/clear_company_results', methods=['POST'])
@login_required
def clear_company_results():
    root_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "company.json")
    dir_path = os.path.join(COMPANY_DIR, "company.json")

    for p in [root_path, dir_path]:
        if os.path.exists(p):
            try:
                os.remove(p)
            except:
                pass

    flash("Data cleared!", "success")
    return redirect(url_for('company'))

# ----------------------
# customer Bank Account Page
# ----------------------

@app.route('/customer/<int:customer_id>/bank', methods=['GET', 'POST'])
@login_required
def manage_bank_account(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    bank_account = BankAccount.query.filter_by(customer_id=customer_id).first()

    if request.method == 'POST':
        bank_code = request.form.get('bank_code')
        branch_code = request.form.get('branch_code')
        account_number = request.form.get('account_number')

        if not bank_code or not branch_code or not account_number:
            flash(py_i18n("bank.missing_fields"), "error")
            return redirect(url_for('manage_bank_account', customer_id=customer_id))

        if not bank_account:
            bank_account = BankAccount(customer_id=customer_id)
            db.session.add(bank_account)

        bank_account.bank_code = bank_code
        bank_account.branch_code = branch_code
        bank_account.account_number = account_number

        db.session.commit()
        flash(py_i18n("bank.updated_success"), "success")
        return redirect(url_for('manage_bank_account', customer_id=customer_id))

    return render_template('manage_bank.html', customer=customer, bank_account=bank_account)


# Route to delete an customer's bank account
@app.route('/customer/<int:customer_id>/bank/delete', methods=['POST'])
@login_required
def delete_bank_account(customer_id):
    bank_account = BankAccount.query.filter_by(customer_id=customer_id).first_or_404()
    db.session.delete(bank_account)
    db.session.commit()
    flash(py_i18n("bank.deleted_success"), "success")
    return redirect(url_for('manage_bank_account', customer_id=customer_id))
       
# --------------------
# HELPER Invoice  Data
# ----------------------

def base_invoice_context(customer_id=None):
    language = get_lang()  

    # Load translated company data
    company = load_company_data()

    # Load products
    products_db = Product.query.all()
    products_list_for_js = [product.to_dict() for product in products_db]

    # Load translated customer
    customer = None
    if customer_id:
        customer_obj = Customer.query.get(customer_id)
        customer = load_customer_translated(customer_obj, language)

    return {
        'products': products_list_for_js,
        'all_customers': Customer.query.all(),
        'customer': customer,   # already translated
        "vat_options": list(range(1, 31)),
        'company': company
    }

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
    data = request.get_json()
    invoice_id = data.get("invoice_id")

    # שליפת נתוני החשבונית מהמערכת שלך
    invoice_data = get_invoice_data(invoice_id)

    # שליחה לרשות המיסים
    try:
        allocation_number = send_invoice_to_tax_authority(invoice_data)
        return jsonify({"status": "success", "allocation_number": allocation_number})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

# --------------------
# Get Invoice Number Form Data
# ----------------------

def get_next_invoice_number():
    last_invoice = Invoice.query.order_by(Invoice.invoice_number.desc()).first()
    if last_invoice:
        return last_invoice.invoice_number + 1
    return 1  


# --------------------
# Helper Build Invoice Context Data
# ----------------------

def invoice_context(invoice_id=None):
    try:
        language = get_lang()
        invoice = Invoice.query.get(invoice_id) if invoice_id else None

        # ----- לקוח נבחר -----
        customer_json = {}
        if invoice and invoice.customer_id:
            c_obj = Customer.query.get(invoice.customer_id)
            if c_obj:
                trans = load_customer_translated(c_obj, language)
                # שימוש ב-.get כדי למנוע קריסה
                customer_json = {
                    "id": c_obj.id,
                    "customer_name": trans.get("name", c_obj.customer_name),
                    "address": trans.get("address", c_obj.address),
                    "city": trans.get("city", c_obj.city),
                    "postal_code": c_obj.postal_code or "",
                    "id_number": c_obj.id_number or "",
                    "phone": c_obj.phone or "",
                    "email": c_obj.email or ""
                }

        # ----- פריטים -----
        items_json = []
        if invoice:
            items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
            for item in items:
                items_json.append({
                    "product_id": item.product_id,
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "discount": float(item.discount or 0),
                    "total_price": float(item.total_price or 0)
                })

        # ----- כל הלקוחות (אופטימיזציה) -----
        all_customers_json = []
        for c in Customer.query.order_by(Customer.customer_name).all():
            trans = load_customer_translated(c, language)
            all_customers_json.append({
                "id": c.id,
                "customer_name": trans.get("name", c.customer_name),
                "id_number": c.id_number or ""
            })

        # ----- כל המוצרים -----
        products_json = []
        for p in Product.query.all():
            item_file = load_item_file(p.id) or {} # הגנה אם חסר
            
            # חילוץ שם מתורגם או שם ברירת מחדל
            p_name = item_file.get("name", {}).get(language, p.name)
            
            products_json.append({
                "id": p.id,
                "name": p_name,
                "price": float(p.price or 0)
            })

        # ----- תשלומים וסכומים -----
        payments_json = []
        if invoice:
            payments = Payment.query.filter_by(invoice_id=invoice.id).all()
            for p in payments:
                payments_json.append({
                    "payment_date": p.payment_date.strftime('%Y-%m-%d') if p.payment_date else "",
                    "payment_method": p.payment_method,
                    "payment_amount": float(p.payment_amount or 0)
                })

        sub_total = float(invoice.sub_total or 0) if invoice else 0
        vat_amount = float(invoice.vat_amount or 0) if invoice else 0
        grand_total = float(invoice.grand_total or 0) if invoice else 0
        discount_total = float(getattr(invoice, 'discount_total', 0) or 0) if invoice else 0

        # ----- בסיס והחזרה -----
        ctx = base_invoice_context()
        ctx.update({
            "invoice": invoice,
            "invoice_id": invoice.id if invoice else None,
            "invoice_number": invoice.invoice_number if invoice else get_next_invoice_number(),
            "invoice_date": invoice.invoice_date.strftime('%d-%m-%Y') if invoice else datetime.today().strftime('%d-%m-%Y'),
            "customer_json": customer_json,
            "all_customers_json": all_customers_json,
            "items": items_json,
            "products": products_json,
            "loadedPayments": payments_json,
            "sub_total": sub_total,
            "vat_rate": 17,
            "vat_amount": vat_amount,
            "grand_total": grand_total,
            "discount_total": discount_total,
            "invoice_status": invoice.status if invoice else "active"
        })
        return ctx

    except Exception as e:
        print(f"CRITICAL ERROR in invoice_context: {e}")
        # החזרת קונטקסט מינימלי כדי שהדף לא יקרוס לגמרי
        return {"error": str(e), "invoice": None, "all_customers_json": [], "products": []}

# --------------------
#  Invoice View Empty Form Save Data
# ----------------------

    # ------ Format Helper--------

def clean_float(value):
    if value is None or value == "":
        return 0.0
    
    # אם זה כבר מספר (float או int), פשוט תחזיר אותו
    if isinstance(value, (float, int)):
        return float(value)

    s = str(value).strip()
    
    # 1. ניקוי סימני מטבע ורווחים
    s = re.sub(r'[^\d,.\-]', '', s)
    
    if not s:
        return 0.0

    # 2. לוגיקה חכמה למניעת הריסת חשבוניות קיימות:
    # אם יש גם פסיק וגם נקודה - נבדוק מי האחרון (כמו שעשינו)
    if ',' in s and '.' in s:
        if s.rfind(',') > s.rfind('.'): # פורמט אירופאי 1.500,50
            s = s.replace('.', '').replace(',', '.')
        else: # פורמט אנגלי 1,500.50
            s = s.replace(',', '')
    
    # 3. אם יש רק פסיק אחד - וזה לא ב-3 הספרות האחרונות, כנראה שזה נקודה עשרונית
    elif ',' in s:
        # אם הפסיק הוא בסוף (למשל 10,50) נהפוך לנקודה
        if len(s.split(',')[1]) <= 2:
            s = s.replace(',', '.')
        else:
            # אם זה 1,500 - נוריד את הפסיק
            s = s.replace(',', '')

    try:
        return float(s)
    except ValueError:
        return 0.0


def generate_allocation_number():
    """
    יוצר מספר הקצאה ייחודי לפי מודל חשבוניות ישראל 2024.
    מבוסס על timestamp + מספר רנדומלי.
    """
    timestamp = int(time.time())  # שניות מאז 1970
    rand = random.randint(1000, 9999)
    return f"{timestamp}{rand}"


@app.route('/invoice/save', methods=['POST'])
@login_required
def save_invoice():
    invoice_id = request.form.get("invoice_id")
    customer_id = request.form.get("customer_id")

    if not customer_id:
        return redirect(url_for('invoice'))

    # Helper to pull and clean form data
    sub_total = clean_float(request.form.get('sub_total'))
    vat_amount = clean_float(request.form.get('vat_amount'))
    grand_total = clean_float(request.form.get('grand_total'))

    # ------ עדכון חשבונית קיימת --------
    if invoice_id:
        invoice = Invoice.query.get(invoice_id)
        if not invoice:
            return redirect(url_for('invoice'))

        invoice.customer_id = customer_id
        invoice.sub_total = sub_total
        invoice.vat_amount = vat_amount
        invoice.grand_total = grand_total
        invoice.status = "active"

        # אם אין מספר הקצאה – צור אחד
        if not invoice.allocation_number:
            invoice.allocation_number = generate_allocation_number()

        # מחיקת פריטים ישנים
        InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()

        # פריטים חדשים
        items = request.form.getlist('items[]')
        for item_json in items:
            item = json.loads(item_json)
            quantity = clean_float(item.get('quantity'))
            price = clean_float(item.get('price'))
            discount = clean_float(item.get('discount', 0))
            total_after_discount = (quantity * price) - (quantity * price * (discount/100) if discount < 100 else discount)

            new_item = InvoiceItem(
                invoice_id=invoice.id,
                product_id=item['product_id'],
                quantity=quantity,
                unit_price=price,
                discount=discount,
                total_price=total_after_discount
            )
            db.session.add(new_item)

        # תשלומים
        Payment.query.filter_by(invoice_id=invoice.id).delete()
        
        amounts = request.form.getlist('payment_amount[]')
        payment_dates = request.form.getlist('payment_date[]')
        payment_methods = request.form.getlist('payment_method[]')
        banks = request.form.getlist('bank[]')
        branches = request.form.getlist('branch[]')
        accounts = request.form.getlist('account_number[]')

        for i in range(len(amounts)):
            amt = clean_float(amounts[i])
            if amt <= 0:
                continue

            date_str = payment_dates[i]
            payment_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None

            db.session.add(Payment(
                invoice_id=invoice.id,
                payment_date=payment_date,
                payment_method=payment_methods[i],
                bank=banks[i],
                branch=branches[i],
                account_number=accounts[i],
                payment_amount=amt
            ))

        db.session.commit()
        return redirect(url_for('invoice_view', invoice_id=invoice.id))

    # ------ יצירת חשבונית חדשה --------
    invoice_number = get_next_invoice_number()

    new_invoice = Invoice(
        invoice_number=invoice_number,
        invoice_date=datetime.today().date(),
        customer_id=customer_id,
        sub_total=sub_total,
        vat_amount=vat_amount,
        grand_total=grand_total,
        status="active"
    )

    # יצירת מספר הקצאה לחשבונית חדשה
    new_invoice.allocation_number = generate_allocation_number()

    db.session.add(new_invoice)
    db.session.flush()  # Get ID before items

    # פריטים חשבונית חדשה
    items = request.form.getlist('items[]')
    for item_json in items:
        item = json.loads(item_json)
        quantity = clean_float(item.get('quantity'))
        price = clean_float(item.get('price'))
        discount = clean_float(item.get('discount', 0))

        db.session.add(InvoiceItem(
            invoice_id=new_invoice.id,
            product_id=item['product_id'],
            quantity=quantity,
            unit_price=price,
            discount=discount,
            total_price=(quantity * price) - (quantity * price * (discount/100))
        ))

    # תשלומים חשבונית חדשה
    amounts = request.form.getlist('payment_amount[]')
    payment_dates = request.form.getlist('payment_date[]')
    
    for i in range(len(amounts)):
        amt = clean_float(amounts[i])
        if amt <= 0:
            continue

        date_str = payment_dates[i]
        p_date = datetime.strptime(date_str, "%Y-%m-%d").date() if date_str else None

        db.session.add(Payment(
            invoice_id=new_invoice.id,
            payment_date=p_date,
            payment_method=request.form.getlist('payment_method[]')[i],
            bank=request.form.getlist('bank[]')[i],
            branch=request.form.getlist('branch[]')[i],
            account_number=request.form.getlist('account_number[]')[i],
            payment_amount=amt
        ))

    db.session.commit()
    return redirect(url_for('invoice_view', invoice_id=new_invoice.id))

# --------------------
# Greate Invoice Form Data
# ----------------------

@app.route('/invoice/create', methods=['GET'])
@login_required
def invoice():
    ctx = invoice_context()  
    return render_template('invoice.html', **ctx)

# --------------------
# Show Invoice View Data
# ----------------------

@app.route('/invoice/<int:invoice_id>', methods=['GET'])
@login_required
def invoice_view(invoice_id):
    ctx = invoice_context(invoice_id)
    if not ctx["invoice"]:
        return redirect(url_for('invoice'))
    return render_template('invoice.html', **ctx)

# --------------------
# Clear All Invoice Data
# ----------------------

@app.route("/invoice/new", methods=["POST"])
def new_invoice():
    return render_template("invoice.html", **invoice_context(None))

# --------------------
# Cancel Invoice ID Form Data
# ----------------------

@app.route('/invoice/<int:invoice_id>/cancel', methods=['POST'])
@login_required
def cancel_invoice(invoice_id):

    invoice = Invoice.query.get(invoice_id)

    # אם החשבונית לא קיימת – אל תקרוס
    if not invoice:
        return redirect(url_for('invoice'))

    # אם כבר מבוטלת – אל תבטל שוב
    if invoice.status in ["canceled", "מבוטלת"]:
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    # ביטול החשבונית
    invoice.status = "canceled"
    db.session.commit()

    return redirect(url_for('invoice_view', invoice_id=invoice_id))

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

    invoices = Invoice.query.order_by(Invoice.id.desc()).all()

    filtered_invoices = []
    customer_i18n_list = {} # יצירת המילון כבר כאן כדי להשתמש בו בחיפוש
    is_numeric_search = search.isdigit()

    for inv in invoices:
        # טעינת תרגום לפני הבדיקה כדי שהחיפוש יכיר את השם באנגלית
        trans_name = ""
        if inv.customer:
            cid = inv.customer.id
            if cid not in customer_i18n_list:
                customer_i18n_list[cid] = load_customer_translated(inv.customer, language)
            trans_name = customer_i18n_list[cid].get('name', '').lower()

        if not search:
            match_search = True
        else:
            if is_numeric_search:
                match_search = (str(inv.invoice_number) == search)
            else:
                # חיפוש גם בשם המקורי (עברית) וגם בתרגום (אנגלית/אחר)
                db_name = inv.customer.customer_name.lower() if inv.customer else ""
                match_search = (
                    (inv.customer and search in db_name) or 
                    (inv.customer and search in trans_name) or
                    search in str(inv.invoice_date).lower()
                )

        # לוגיקת תאריכים
        if isinstance(inv.invoice_date, str):
            parts = inv.invoice_date.split('-')
            inv_year = parts[0]
            inv_month = parts[1]
        else:
            inv_year = str(inv.invoice_date.year)
            inv_month = inv.invoice_date.strftime('%m')

        match_month = not selected_month or inv_month == selected_month
        match_year = not selected_year or inv_year == selected_year

        if match_search and match_month and match_year:
            filtered_invoices.append(inv)

    active_invoices = [inv for inv in filtered_invoices if inv.status != "canceled"]
    total_amount = sum(inv.grand_total for inv in active_invoices)
    total_count = len(active_invoices)

    months_hebrew = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
    available_years = [str(y) for y in range(2024, 2031)]

    return render_template(
        'invoice_data.html',
        invoices=filtered_invoices,
        total_amount=total_amount,
        total_count=total_count,
        search=search,
        selected_month=selected_month,
        selected_year=selected_year,
        months=months_hebrew,
        years=available_years,
        customer_i18n_list=customer_i18n_list, # המילון המלא עובר ל-HTML
        language=language
    )

# ----------------------
# Send Email Invoices To Customers invoice_data Page
# ----------------------

@app.route('/send_invoice_email/<int:invoice_id>')
@login_required
def send_invoice_email(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)

    language = get_lang()

    company_data = load_company_data()

    customer = invoice.customer

    if not customer or not customer.email:
        flash("ללקוח לא מוגדר אימייל!", "danger")
        return redirect(url_for('invoice'))

    try:
        # 1. הכנת הנתונים וה-HTML (עם is_pdf=True להסתרת כפתורים)
        ctx = invoice_context(invoice_id)
        ctx["company"] = company_data
        html_content = render_template("invoice.html", **ctx, is_pdf=True)

        # 2. יצירת PDF באמצעות Playwright (מנוע כרום מודרני)
        with sync_playwright() as p:
            # הפעלת דפדפן "שקט"
            browser = p.chromium.launch()
            page = browser.new_page()
            
            # טעינת התוכן והמתנה לסיום טעינת ה-CSS/תמונות
            page.set_content(html_content, wait_until="networkidle")
            
            # יצירת הקובץ - print_background שומר על הצבעים והעיצוב
            pdf_data = page.pdf(
                format="A4",
                print_background=True,
                          scale=1.0, 
                margin={"top": "20px", "right": "20px", "bottom": "20px", "left": "20px"}
            )
            browser.close()

        # 3. הגדרת גוף המייל ההודעה
        email_body = f"""
שלום {customer.customer_name}

להלן מצורפת חשבונית מספר {invoice.invoice_number}
לתאריך {invoice.invoice_date.strftime('%d-%m-%Y')}

תודה לשירותך תמיד
"""

        # 4. יצירת הודעת המייל
        msg = Message(
            subject=f"חשבונית מס {invoice.invoice_number} - {invoice.customer.customer_name}",
            recipients=[customer.email],
            body=email_body
        )

        # 5. צירוף ה-PDF (שנוצר ע"י Playwright)
        msg.attach(
            filename=f"invoice_{invoice.invoice_number}.pdf",
            content_type="application/pdf",
            data=pdf_data
        )

        # 6. שליחה
        mail.send(msg)
        flash(f"החשבונית נשלחה בהצלחה ל-{customer.email}", "success")

    except Exception as e:
        print(f"Detailed Error: {str(e)}")
        flash(f"שגיאה בשליחה: {str(e)}", "danger")

    return redirect(url_for('invoice'))


# --------------------
#  Create Payment Invoice Form Data
# ----------------------

@app.route('/api/payments/create', methods=['POST'])
@login_required
def create_payment():
    data = request.get_json()
    allocation_number = data.get('allocation_number')

    # 1) שליפת החשבונית לפי מספר הקצאה
    invoice = Invoice.query.filter_by(allocation_number=allocation_number).first_or_404()

    # 2) שליפת הלקוח מה-DB
    customer = Customer.query.get(invoice.customer_id)

    # 3) בניית בקשה לספק הסליקה
    payment_request = {
        "merchant_id": "YOUR_MERCHANT_ID",

        "amount": invoice.grand_total,

        "description": f"תשלום עבור חשבונית מס {invoice.allocation_number}",

        # פרטי לקוח
        "customer_name": customer.customer_name,
        "customer_phone": customer.phone,
        "customer_address": f"{customer.address}, {customer.city}",
        "customer_id_number": customer.id_number,

        # מזהים פנימיים
        "internal_customer_id": customer.id,
        "internal_invoice_id": invoice.id
    }

    # לינק דמה
    payment_url = "https://gateway.com/pay/EXAMPLE123"

    return jsonify({"payment_url": payment_url})

# --------------------
#  קבלת אישור תשלום Payment Callback Invoice Form Data
# ----------------------

@app.route('/api/payments/callback', methods=['POST'])
def payment_callback():
    data = request.get_json()

    status = data.get("status")
    invoice_id = data.get("internal_invoice_id")
    customer_id = data.get("internal_customer_id")
    transaction_id = data.get("transaction_id")

    if status == "success":

        invoice = Invoice.query.get(invoice_id)
        if invoice:
            invoice.is_paid = True
            invoice.payment_transaction_id = transaction_id
            invoice.payment_date = datetime.utcnow()
            db.session.commit()

        customer = Customer.query.get(customer_id)
        if customer:
            customer.is_active = True
            db.session.commit()

    return "OK", 200


# --------------------
#  Products Manage Form Data
# ----------------------

@app.route('/products_manage', methods=['GET', 'POST'])
@login_required
def manage_products():

    language = get_lang()  

    # POST – CREATE / UPDATE
    if request.method == 'POST':

        product_id = request.form.get("id")

        # UPDATE EXISTING PRODUCT
        if product_id:
            product = Product.query.get(product_id)
            if not product:
                return redirect(url_for('manage_products'))

            product.name = request.form.get('name', product.name)
            product.price = float(request.form.get('price', product.price))
            product.description = request.form.get('description', product.description)
            category = request.form.get("category", "")

            db.session.commit()

            # create translations
            name_trans = generate_translations(product.name)
            desc_trans = generate_translations(product.description)

            # save ONE file
            save_item_file(product.id, name_trans, desc_trans, product.price, category)

            return redirect(url_for('manage_products'))

        # CREATE NEW PRODUCT
        else:
            product_name = request.form.get('name')
            product_price = request.form.get('price')
            product_description = request.form.get('description')
            category = request.form.get("category", "")

            new_product = Product(
                name=product_name,
                price=float(product_price),
                description=product_description
            )
            db.session.add(new_product)
            db.session.commit()

            product_id = new_product.id

            # create translations
            name_trans = generate_translations(product_name)
            desc_trans = generate_translations(product_description)

            # save ONE file
            save_item_file(product_id, name_trans, desc_trans, float(product_price), category)

            return redirect(url_for('manage_products'))

    # GET – SEARCH
    search = request.args.get("q", "").strip().lower()

    all_products = Product.query.order_by(Product.id.desc()).all()

    # FILTERED LIST
    if not search:
        filtered_objects = all_products
    else:
        filtered_objects = []
        is_numeric = search.isdigit()

        for p in all_products:
            item_file = load_item_file(p.id)

            # Hebrew fallback
            name_he = p.name
            desc_he = p.description or ""

            if item_file:
                names = item_file["name"]
                descs = item_file["description"]
            else:
                names = {"he": name_he}
                descs = {"he": desc_he}

            # match
            if is_numeric:
                match = (str(p.id) == search)
            else:
                match = (
                    search in names.get("he", "").lower() or
                    search in descs.get("he", "").lower()
                )

            if match:
                filtered_objects.append(p)

    # BUILD item_i18n_list
    item_i18n_list = {}

    for p in filtered_objects:
        item_file = load_item_file(p.id)

        if item_file:
            item_i18n_list[p.id] = {
                "id": p.id,
                "name": item_file["name"].get(language, item_file["name"].get("he", "")),
                "description": item_file["description"].get(language, item_file["description"].get("he", "")),
                "price": p.price
            }
        else:
            item_i18n_list[p.id] = {
                "id": p.id,
                "name": p.name,
                "description": p.description or "",
                "price": p.price
            }

    # SELECTED PRODUCT FOR FORM
    item_i18n = None
    if search and len(filtered_objects) == 1:
        p = filtered_objects[0]
        item_i18n = item_i18n_list[p.id]

    # JSON FOR JAVASCRIPT
    products_json = []

    for p in filtered_objects:
        item_file = load_item_file(p.id)

        if item_file:
            products_json.append({
                "id": p.id,
                "name": item_file["name"],
                "description": item_file["description"],
                "price": float(p.price or 0)
            })
        else:
            products_json.append({
                "id": p.id,
                "name": {"he": p.name},
                "description": {"he": p.description or ""},
                "price": float(p.price or 0)
            })

    return render_template(
        'products_manage.html',
        products=filtered_objects,
        products_json=products_json,
        search=search,
        item_i18n=item_i18n,
        item_i18n_list=item_i18n_list
    )


# --------------------
#  Products List Selected Combobox Data
# ----------------------

@app.get("/api/products_list")
def products_list():
    products = Product.query.order_by(Product.id.asc()).all()

    result = []
    for p in products:
        item_file = load_item_file(p.id)

        if item_file:
            result.append({
                "id": p.id,
                "name": item_file.get("name", {"he": p.name}),               
                "price": float(p.price or 0),
                "description": item_file.get("description", {"he": p.description})
            })
        else:
            result.append({
                "id": p.id,
                "name": {"he": p.name},                                      
                "price": float(p.price or 0),
                "description": {"he": p.description}
            })

    return jsonify(result)


# --------------------
#  Products Delete Folder Path From Static Folder
# ----------------------

def delete_product_folder(product_id):
    folder_path = os.path.join(ITEMS_DIR, str(product_id))
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)

# --------------------
#  Products Delete Selected Form Data
# ----------------------

@app.route('/delete_selected_products', methods=['POST'])
@login_required
def delete_selected_products():
    selected_product_ids = request.form.getlist('delete_products')

    if not selected_product_ids:
        flash(py_i18n("products.delete_none_selected"), "warning")
        return redirect(url_for('manage_products'))

    try:
        # המרה למספרים
        product_ids_int = [int(p_id) for p_id in selected_product_ids]

        # מחיקת מוצרים מה־DB
        Product.query.filter(Product.id.in_(product_ids_int)).delete(synchronize_session=False)
        db.session.commit()

        # מחיקת תיקיות JSON לכל מוצר
        for pid in product_ids_int:
            delete_product_folder(pid)

        flash(py_i18n("products.delete_success").format(count=len(selected_product_ids)), "success")
        return redirect(url_for('manage_products'))

    except Exception as e:
        db.session.rollback()
        flash(py_i18n("products.delete_error").format(error=str(e)), "danger")
        return redirect(url_for('manage_products'))


# ----------------------
#   Build All Customer Form
# ----------------------

@app.route('/customer', methods=['GET', 'POST'])
@login_required
def customer():
    if request.method == 'POST':
        # --- Validate date ---
        date_str = request.form.get('date')
        if not date_str:
            flash('תאריך הוא שדה חובה', 'error')
            return redirect(url_for('customer'))

        try:
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%d/%m/%Y')
        except ValueError:
            formatted_date = date_str 

        customer_id = request.form.get('customer_id')
        id_number = request.form.get('id_number')

        # --- UPDATE EXISTING CUSTOMER ---
        if customer_id:
            customer_obj = Customer.query.get(customer_id)
            if customer_obj:
                customer_obj.date = formatted_date
                customer_obj.customer_name = request.form.get('customer_name')
                customer_obj.id_number = id_number
                customer_obj.address = request.form.get('address')
                customer_obj.city = request.form.get('city')
                customer_obj.postal_code = request.form.get('postal_code')
                customer_obj.phone = request.form.get('phone')
                customer_obj.email = request.form.get('email')
                customer_obj.contract_status = request.form.get('contract_status')
                customer_obj.message = request.form.get('message')
                
                db.session.commit()
                
                # תרגום ברקע
                t = threading.Thread(
                    target=translate_in_background,
                    args=(customer_obj.id, customer_obj.customer_name, customer_obj.address, customer_obj.city, customer_obj.message or "")
                )
                t.daemon = True
                t.start()
                
                flash('הנתונים עודכנו בהצלחה!', 'success')
        
        else:
            # --- Check if ID number exists (New customer only) ---
            if Customer.query.filter_by(id_number=id_number).first():
                flash("קיים כבר לקוח עם מספר זהות זה", "error")
                return redirect(url_for('customer'))

            # --- FIX FOR USER_ID 0 ---
            # אם המערכת מזהה 0, אנחנו נותנים את ה-ID של המשתמש המחובר, 
            # ואם גם הוא לא תקין, ברירת מחדל ל-1 (המשתמש הראשון במערכת)
            safe_user_id = current_user.id if (current_user.is_authenticated and current_user.id != 0) else 1

            # --- ADD NEW CUSTOMER ---
            new_customer = Customer(
                date=formatted_date,
                customer_name=request.form.get('customer_name'),
                id_number=id_number,
                address=request.form.get('address'),
                city=request.form.get('city'),
                postal_code=request.form.get('postal_code'),
                phone=request.form.get('phone'),
                email=request.form.get('email'),
                contract_status=request.form.get('contract_status'),
                message=request.form.get('message'),
                role='customer',
                is_active=True,
                user_id=safe_user_id  # <--- השתמשנו ב-ID המאובטח
            )

            db.session.add(new_customer)
            db.session.commit()

            # שליחה לתרגום ברקע
            t = threading.Thread(
                target=translate_in_background,
                args=(new_customer.id, new_customer.customer_name, new_customer.address, new_customer.city, new_customer.message or "")
            )
            t.daemon = True
            t.start()

            flash('הלקוח נוסף בהצלחה!', 'success')

        return redirect(url_for('customer'))

    # --- GET REQUEST ---
    customer_id = request.args.get('customer_id')
    selected_customer = Customer.query.get(customer_id) if customer_id else None

    language = get_lang()
    customer_i18n = load_customer_translated(selected_customer, language) if selected_customer else {}

    all_customers = Customer.query.all()
    customer_i18n_list = {
        c.id: load_customer_translated(c, language)
        for c in all_customers
    }

    return render_template(
        'customer.html',
        customer=selected_customer,
        all_customers=all_customers,
        customer_i18n=customer_i18n,
        customer_i18n_list=customer_i18n_list
    )

# ----------------------
#   API All Customer Form
# ----------------------

@app.route('/api/customer/<int:customer_id>')
@login_required
def api_get_customer(customer_id):
    try:
        language = get_lang()
        # שימוש ב-get במקום get_or_404 כדי לשלוט בתגובה
        c = Customer.query.get(customer_id)
        
        if not c:
            return jsonify({"error": "Customer not found"}), 404

        # טעינת התרגום
        translated = load_customer_translated(c, language)

        # הגנה למקרה שהתרגום מחזיר None או ריק
        if not translated:
            translated = {
                "name": c.customer_name,
                "address": c.address,
                "city": c.city,
                "message": c.message
            }

        # החזרת JSON מסודר
        return jsonify({
            "customer_name": translated.get("name", c.customer_name),
            "address": translated.get("address", c.address),
            "city": translated.get("city", c.city),
            "postal_code": c.postal_code or "",
            "id_number": c.id_number or "",
            "phone": c.phone or "",
            "email": c.email or "",
            "contract_status": c.contract_status or "",
            "message": translated.get("message", c.message or ""),
            "date": c.date,
            "is_active": c.is_active,
            "role": c.role
        })

    except Exception as e:
        print(f"API Error: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500


@app.route('/customer_data', methods=['GET'])
@login_required
def customer_data():
    customers = Customer.query.all()
    return render_template('customer_data.html', customers=customers)


# ----------------------
#   Search And Clear Customer Form
# ----------------------

@app.route('/search_customer', methods=['GET', 'POST'])
@login_required
def search_customer():
    language = get_lang()

    # קבלת שם החיפוש
    search_name = request.form.get('search_name') if request.method == 'POST' else request.args.get('search_name')

    search_results = []
    customer = None

    if search_name:
        search_name = search_name.strip()

        # חיפוש לפי שם (תומך חלקי)
        search_results = Customer.query.filter(
            Customer.customer_name.ilike(f'%{search_name}%')
        ).all()

        if search_results:
            customer = search_results[0]

            # --- תיקון תאריך ל-YYYY-MM-DD עבור input type="date" ---
            if customer.date:
                try:
                    if "/" in customer.date:
                        day, month, year = customer.date.split("/")
                        customer.date = f"{year}-{month}-{day}"
                    else:
                        datetime.strptime(customer.date, "%Y-%m-%d")
                except:
                    customer.date = ""

    # רשימת כל הלקוחות ל-select
    all_customers = Customer.query.all()

    # --- LOAD I18N FOR CURRENT CUSTOMER ---
    customer_i18n = load_customer_translated(customer, language) if customer else {}

    # --- LOAD I18N FOR ALL CUSTOMERS (FOR COMBO BOX) ---
    customer_i18n_list = {
        c.id: load_customer_translated(c, language)
        for c in all_customers
    }

    return render_template(
        'customer.html',
        customers=search_results,
        all_customers=all_customers,
        customer=customer,
        customer_i18n=customer_i18n,
        customer_i18n_list=customer_i18n_list
    )


@app.route('/clear_search_results_customer', methods=['POST'])
@login_required
def clear_search_results_customer():
    language = get_lang()

    all_customers = Customer.query.all()

    # --- LOAD I18N FOR ALL CUSTOMERS (FOR COMBO BOX) ---
    customer_i18n_list = {
        c.id: load_customer_translated(c, language)
        for c in all_customers
    }

    return render_template(
        'customer.html',
        customers=[],
        all_customers=all_customers,
        customer=None,
        customer_i18n={},
        customer_i18n_list=customer_i18n_list
    )

 
# ----------------------
# Protected Route
# ----------------------
@app.route('/get_days/<int:year>/<int:month>')
@login_required
def get_days(year, month):
    days_data = get_days_in_month(year, month)
    return jsonify(days_data)

# -----------------------------------------------------------
#  Create Tables (Safe context)
# -----------------------------------------------------------
with app.app_context():
    try:
        db.create_all()
        print("✅ Database tables synced successfully")
    except Exception as e:
        print(f"❌ Database startup error: {e}")

# ----------------------
# Run it
# ----------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

