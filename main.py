# -----------------------------
import os
import threading
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
import babel.dates
import babel.numbers
import random
# -----------------------------------------------------------
import openpyxl
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, flash, current_app
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
from deep_translator import GoogleTranslator
from concurrent.futures import ThreadPoolExecutor

# -----------------------------
# Models & Logic 
# -----------------------------
from database import db, PasswordResetToken, Company, Customer, Payment, Invoice, InvoiceItem, Product, Category, Supplier, SupplierPurchase, Transaction, User, OwnerUser 

# -----------------------------------------------------------
#  1. Load Environment & Init Flask
# -----------------------------------------------------------
load_dotenv()
app = Flask(__name__, static_folder="static")

IS_RENDER = "RENDER" in os.environ

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

# -----------------------------------------------------------
#  2. הגדרת נתיבי תיקיות (Paths) - Local & Render Safe
# -----------------------------------------------------------

UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

if IS_RENDER:
    CUSTOMERS_DIR = "/tmp/customers"
    SUPPLIERS_DIR = "/tmp/suppliers"
    ITEMS_DIR = "/tmp/items"
    TRANSACTIONS_DIR = "/tmp/transactions"
    CATEGORIES_DIR = "/tmp/categories"
else:
    CUSTOMERS_DIR = os.path.join(BASE_DIR, "customers")
    SUPPLIERS_DIR = os.path.join(BASE_DIR, "suppliers")
    ITEMS_DIR = os.path.join(BASE_DIR, "static", "items")
    TRANSACTIONS_DIR = os.path.join(BASE_DIR, "static", "transactions")
    CATEGORIES_DIR = os.path.join(BASE_DIR, "static", "categories")

folders_to_create = [
    ITEMS_DIR,
    TRANSACTIONS_DIR,
    UPLOAD_FOLDER,
    CATEGORIES_DIR,
    CUSTOMERS_DIR,
    SUPPLIERS_DIR,
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

    # רישום נתיבים ב-Config לגישה מכל מקום ב-Routes
    ITEMS_DIR=ITEMS_DIR,
    TRANSACTIONS_DIR=TRANSACTIONS_DIR,
    UPLOAD_FOLDER=UPLOAD_FOLDER,
    CATEGORIES_DIR=CATEGORIES_DIR,
    CUSTOMERS_DIR=CUSTOMERS_DIR,
    SUPPLIERS_DIR=SUPPLIERS_DIR
)

# -----------------------------
#  Database Config (Postgres)
# -----------------------------

db_choice = os.getenv("DB_CHOICE", "sqlite").lower()

if db_choice == "postgres":
    uri = os.getenv("POSTGRES_URI")

    if not uri:
        raise RuntimeError("POSTGRES_URI is missing but DB_CHOICE=postgres")

    # תיקון אוטומטי ל-Render אם צריך
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)

    app.config["SQLALCHEMY_DATABASE_URI"] = uri

else:
    # LOCAL SQLITE
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
#  Owner Credentials
# -----------------------------
OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = generate_password_hash(os.getenv("OWNER_PASSWORD"))

# -----------------------------
#  DB Create All
# -----------------------------
with app.app_context():
        db.create_all()

# ---------------------------------------------------------
# Flask-Login: User Loader (CRITICAL FOR SESSIONS)
# ---------------------------------------------------------

@login_manager.user_loader
def load_user(user_id):
    try:
        from database import User
        return User.query.get(int(user_id))
    except Exception:
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

    # OWNER LOGIN (real DB user, not id=0)
    if login_email == OWNER_USERNAME and check_password_hash(OWNER_PASSWORD, password):
        session.clear()

        # חפש owner ב-DB, ואם אין – צור אותו
        owner_user = User.query.filter_by(email=OWNER_USERNAME).first()
        if not owner_user:
            owner_user = User(
                email=OWNER_USERNAME,
                username=OWNER_USERNAME,
                role='owner',
                is_active=True
            )
            # OWNER_PASSWORD הוא כבר hash (אתה בודק עם check_password_hash)
            owner_user.password_hash = OWNER_PASSWORD
            db.session.add(owner_user)
            db.session.commit()

        login_user(owner_user)
        session['owner_access'] = True
        session['user_id'] = owner_user.id
        session['user_name'] = owner_user.email
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



# ----------------------
# Getting Import Time GLOBAL LANGUAGE + COUNTRY Format All Processor
# ----------------------

def py_i18n(key):
    lang = request.cookies.get("lang", "he")
    # שימוש בנתיב אבסולוטי כדי למנוע בעיות ב-Render
    path = os.path.join(BASE_DIR, "static", f"{lang}.json")

    try:
        if not os.path.exists(path):
            return key
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            # מחזיר את הערך, או את המפתח עצמו אם הערך חסר ב-JSON
            return data.get(key, key)
    except Exception as e:
        print(f"Translation error: {e}")
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
            if lang == "he": return lang, text
            translated = GoogleTranslator(source='auto', target=lang).translate(text)
            return lang, (translated if translated else text)
        except:
            return lang, text

    with ThreadPoolExecutor(max_workers=10) as executor:
        translations = list(executor.map(translate_single, languages))

    for lang, translated_text in translations:
        result[lang] = translated_text
        
    return result


# -----------------------------------------------------------
#  Company Translation (Threading + DB JSON)
# -----------------------------------------------------------

def translate_company_in_background(company_id, name, address, city):
    thread = threading.Thread(
        target=run_company_translation,
        args=(company_id, name, address, city)
    )
    thread.daemon = True
    thread.start()


def run_company_translation(company_id, name, address, city):
    try:
        # חייבים context בתוך Thread
        with app.app_context():

            # תרגום ל‑30 שפות
            name_trans = generate_translations(name or "")
            address_trans = generate_translations(address or "")
            city_trans = generate_translations(city or "")

            # שמירה ל‑DB
            company = Company.query.get(company_id)
            if not company:
                print(f"⚠ Company not found: {company_id}")
                return

            company.translations_json = json.dumps({
                "name": name_trans,
                "address": address_trans,
                "city": city_trans
            }, ensure_ascii=False)

            db.session.commit()

            print(f"✔ Company translation saved: {company_id}")

    except Exception as e:
        print(f"⚠ Company translation failed for {company_id}: {e}")


# -----------------------------------------------------------
#  Load Company Data (DB + translations_json)
# -----------------------------------------------------------

def load_company_data():
    company = Company.query.filter_by(user_id=current_user.id).first()
    if not company:
        return {}

    try:
        data = json.loads(company.translations_json or "{}")
    except:
        data = {}

    lang = get_lang()

    return {
        "name": data.get("name", {}).get(lang) or company.name,
        "company_id_number": company.company_id_number or "",
        "deduction_file": company.deduction_file or "",
        "address": data.get("address", {}).get(lang) or company.address,
        "city": data.get("city", {}).get(lang) or company.city,
        "postal_code": company.postal_code or "",
        "phone": company.phone or "",
        "email": company.email or "",
        "logo": company.logo or ""
    }


# -----------------------------------------------------------
#  Customer Translation Background Tasks 
# -----------------------------------------------------------

def translate_customer_in_background(customer_id, name, address, city, message):
    thread = threading.Thread(
        target=run_customer_translation,
        args=(customer_id, name, address, city, message)
    )
    thread.daemon = True
    thread.start()


def run_customer_translation(customer_id, name, address, city, message):
    try:
        name_trans = generate_translations(name or "")
        address_trans = generate_translations(address or "")
        city_trans = generate_translations(city or "")
        message_trans = generate_translations(message or "")

        save_customer_file(
            customer_id,
            name_trans,
            address_trans,
            city_trans,
            message_trans
        )

        print(f"✔ Customer translation saved: {customer_id}")

    except Exception as e:
        print(f"⚠ Translation failed for customer {customer_id}: {e}")


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
    special_mappings = {
        "zh": "zh-CN",
        "en": "en"
    }
    lookup_lang = special_mappings.get(language, language)

    data = load_customer_file(customer.id)

    if not data:
        return {
            "name": customer.customer_name or "",
            "address": customer.address or "",
            "city": customer.city or "",
            "message": customer.message or ""
        }

    def get_val(field_key, default_val):
        field_data = data.get(field_key, {})
        return field_data.get(lookup_lang) or field_data.get("he") or default_val or ""

    return {
        "name": get_val("name", customer.customer_name),
        "address": get_val("address", customer.address),
        "city": get_val("city", customer.city),
        "message": get_val("message", customer.message)
    }


# -----------------------------------------------------------
#  Translations Add Great Save Supplier Json File
# -----------------------------------------------------------

def translate_supplier_in_background(supplier_id, name, address, city, postal_code, notes):
    thread = threading.Thread(
        target=run_supplier_translation,
        args=(supplier_id, name, address, city, postal_code, notes)
    )
    thread.daemon = True
    thread.start()


def run_supplier_translation(supplier_id, name, address, city, postal_code, notes):
    try:
        name_trans = generate_translations(name or "")
        address_trans = generate_translations(address or "")
        city_trans = generate_translations(city or "")
        postal_code_trans = generate_translations(postal_code or "")
        notes_trans = generate_translations(notes or "")

        save_supplier_file(
            supplier_id,
            name_trans,
            address_trans,
            city_trans,
            postal_code_trans,
            notes_trans
        )

        print(f"✔ Supplier translation saved: {supplier_id}")

    except Exception as e:
        print(f"⚠ Supplier translation failed for {supplier_id}: {e}")


def save_supplier_file(supplier_id, name_trans, address_trans, city_trans, postal_code_trans, notes_trans):
    supplier_path = os.path.join(SUPPLIERS_DIR, str(supplier_id))
    os.makedirs(supplier_path, exist_ok=True)

    file_path = os.path.join(supplier_path, f"{supplier_id}.json")

    data = {
        "name": name_trans,
        "address": address_trans,
        "city": city_trans,
        "postal_code": postal_code_trans,
        "notes": notes_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return file_path


def load_supplier_file(supplier_id):
    file_path = os.path.join(SUPPLIERS_DIR, str(supplier_id), f"{supplier_id}.json")

    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None


def load_supplier_translated(supplier, language):
    special_mappings = {
        "zh": "zh-CN",
        "en": "en"
    }
    lookup_lang = special_mappings.get(language, language)

    data = load_supplier_file(supplier.id)

    if not data:
        return {
            "name": supplier.supplier_name or "",
            "address": supplier.address or "",
            "city": supplier.city or "",
            "postal_code": supplier.postal_code or "",
            "notes": supplier.notes or ""
        }

    def get_val(field_key, default_val):
        field_data = data.get(field_key, {})
        return (
            field_data.get(lookup_lang)
            or field_data.get("he")
            or default_val
            or ""
        )

    return {
        "name": get_val("name", supplier.supplier_name),
        "address": get_val("address", supplier.address),
        "city": get_val("city", supplier.city),
        "postal_code": get_val("postal_code", supplier.postal_code),
        "notes": get_val("notes", supplier.notes)
    }


# -----------------------------------------------------------
#  Products - Items Helper - Save & Load JSON Translations
# -----------------------------------------------------------

def ensure_product_folder(product_id):
    path = os.path.join(ITEMS_DIR, str(product_id))
    os.makedirs(path, exist_ok=True)
    return path


def translate_product_in_background(
    product_id,
    name,
    description,
    price,
    income_category,
    cost_price=0.0,
    stock_in=0,
    stock_out=0,
    supplier_id=None,
    received_date=None
):
    thread = threading.Thread(
        target=run_product_translation,
        args=(
            product_id,
            name,
            description,
            price,
            income_category,
            cost_price,
            stock_in,
            stock_out,
            supplier_id,
            received_date
        )
    )
    thread.daemon = True
    thread.start()


def run_product_translation(
    product_id,
    name,
    description,
    price,
    income_category,
    cost_price=0.0,
    stock_in=0,
    stock_out=0,
    supplier_id=None,
    received_date=None
):
    try:
        # חובה! אחרת Flask / Render לא יאפשרו גישה ל‑config ול‑paths
        with app.app_context():

            name_trans = generate_translations(name or "")
            desc_trans = generate_translations(description or "")

            save_item_file(
                product_id=product_id,
                name_trans=name_trans,
                desc_trans=desc_trans,
                price=float(price or 0.0),
                income_category=income_category or "service",
                cost_price=float(cost_price or 0.0),
                stock_in=int(stock_in or 0),
                stock_out=int(stock_out or 0),
                supplier_id=supplier_id or None,
                received_date=received_date
            )

            print(f"✔ Product translation saved: {product_id}")

    except Exception as e:
        print(f"⚠ Product translation failed for {product_id}: {e}")


def save_item_file(
    product_id,
    name_trans,
    desc_trans,
    price,
    income_category,
    cost_price=0.0,
    stock_in=0,
    stock_out=0,
    supplier_id=None,
    received_date=None
):
    product_path = ensure_product_folder(product_id)
    file_path = os.path.join(product_path, f"{product_id}.json")

    data = {
        "id": int(product_id),
        "price": float(price or 0.0),
        "cost_price": float(cost_price or 0.0),
        "income_category": income_category or "service",
        "stock_in": int(stock_in or 0),
        "stock_out": int(stock_out or 0),
        "supplier_id": supplier_id or None,
        "received_date": received_date,
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
    except:
        return None


def load_item_translated(product, language):
    special_mappings = {
        "zh": "zh-CN",
        "en": "en"
    }
    lookup_lang = special_mappings.get(language, language)

    data = load_item_file(product.id)

    # אם אין קובץ עדיין ← מחזיר מה‑DB בצורה בטוחה ללא קריסה
    if not data:
        return {
            "name": getattr(product, 'name', '') or "",
            "description": getattr(product, 'description', '') or "",
            "price": getattr(product, 'price', 0.0) or 0.0,
            "income_category": getattr(product, 'income_category', 'service') or "service",
            "cost_price": getattr(product, 'cost_price', 0.0) or 0.0,
            "stock_in": getattr(product, 'stock_in', 0) or 0,
            "stock_out": getattr(product, 'stock_out', 0) or 0,
            "supplier_id": getattr(product, 'supplier_id', None),
            "received_date": getattr(product, 'received_date', None)
        }

    def get_val(field_key, default_val):
        field_data = data.get(field_key, {})
        return (
            field_data.get(lookup_lang)
            or field_data.get("he")
            or default_val
            or ""
        )

    return {
        "name": get_val("name", getattr(product, 'name', '')),
        "description": get_val("description", getattr(product, 'description', '')),
        "price": data.get("price", getattr(product, 'price', 0.0)),
        "income_category": data.get("income_category", getattr(product, 'income_category', 'service')),
        "cost_price": data.get("cost_price", getattr(product, 'cost_price', 0.0)),
        "stock_in": data.get("stock_in", getattr(product, 'stock_in', 0)),
        "stock_out": data.get("stock_out", getattr(product, 'stock_out', 0)),
        "supplier_id": data.get("supplier_id", getattr(product, 'supplier_id', None)),
        "received_date": data.get("received_date", getattr(product, 'received_date', None))
    }


# -----------------------------------------------------------
#  Transactions Helper - Save & Load JSON Translations
# -----------------------------------------------------------

def ensure_transaction_folder(transaction_id):
    path = os.path.join(TRANSACTIONS_DIR, str(transaction_id))
    os.makedirs(path, exist_ok=True)
    return path


def translate_transaction_in_background(
    transaction_id,
    description,
    amount,
    type_trans,
    category_id,
    currency_code=None,
    cost_price=0.0,
    income_category='service'
):
    thread = threading.Thread(
        target=run_transaction_translation,
        args=(
            transaction_id,
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
    transaction_id,
    description,
    amount,
    type_trans,
    category_id,
    currency_code=None,
    cost_price=0.0,
    income_category='service'
):
    try:
        # חובה! אחרת Flask/Render לא יאפשרו גישה לנתיבים
        with app.app_context():

            desc_trans = generate_translations(description or "")

            save_transaction_file(
                transaction_id=transaction_id,
                desc_trans=desc_trans,
                amount=float(amount or 0.0),
                type_trans=type_trans,
                category_id=category_id,
                currency_code=currency_code,
                cost_price=float(cost_price or 0.0),
                income_category=income_category or 'service'
            )

            print(f"✔ Transaction translation saved: {transaction_id}")

    except Exception as e:
        print(f"⚠ Transaction translation failed for {transaction_id}: {e}")


def save_transaction_file(
    transaction_id,
    desc_trans,
    amount,
    type_trans,
    category_id,
    currency_code=None,
    cost_price=0.0,
    income_category='service'
):
    trans_path = ensure_transaction_folder(transaction_id)
    file_path = os.path.join(trans_path, f"{transaction_id}.json")

    data = {
        "id": int(transaction_id),
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


def load_transaction_file(transaction_id):
    file_path = os.path.join(TRANSACTIONS_DIR, str(transaction_id), f"{transaction_id}.json")

    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None


def load_transaction_translated(transaction, language):
    special_mappings = {
        "zh": "zh-CN",
        "en": "en"
    }
    lookup_lang = special_mappings.get(language, language)

    data = load_transaction_file(transaction.id)

    if not data:
        return {
            "description": transaction.description or "",
            "amount": transaction.amount or 0.0,
            "type": transaction.type_trans or "",
            "category_id": transaction.category_id or None,
            "currency": transaction.currency_code or None,
            "cost_price": transaction.cost_price or 0.0,
            "income_category": transaction.income_category or "service"
        }

    field_data = data.get("description", {})

    return {
        "description": (
            field_data.get(lookup_lang)
            or field_data.get("he")
            or transaction.description
            or ""
        ),
        "amount": data.get("amount", transaction.amount),
        "type": data.get("type", transaction.type_trans),
        "category_id": data.get("category_id", transaction.category_id),
        "currency": data.get("currency", transaction.currency_code),
        "cost_price": data.get("cost_price", transaction.cost_price),
        "income_category": data.get("income_category", transaction.income_category)
    }


# -----------------------------------------------------------
#  Categories Helper - Save & Load JSON Translations (Threading Version)
# -----------------------------------------------------------

def ensure_category_folder(cat_id):
    path = os.path.join(CATEGORIES_DIR, str(cat_id))
    os.makedirs(path, exist_ok=True)
    return path


def translate_category_in_background(cat_id, raw_name_text):
    thread = threading.Thread(
        target=run_category_translation,
        args=(cat_id, raw_name_text)
    )
    thread.daemon = True
    thread.start()


def run_category_translation(cat_id, raw_name_text):
    try:
        # חובה! אחרת Flask/Render לא יאפשרו גישה לנתיבים
        with app.app_context():

            name_trans = generate_translations(raw_name_text or "")

            save_category_file(
                cat_id=cat_id,
                name_trans=name_trans
            )

            print(f"✔ Category translation saved: {cat_id}")

    except Exception as e:
        print(f"⚠ Category translation failed for {cat_id}: {e}")


def save_category_file(cat_id, name_trans):
    cat_path = ensure_category_folder(cat_id)
    file_path = os.path.join(cat_path, f"{cat_id}.json")

    data = {
        "id": int(cat_id),
        "name": name_trans
    }

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    return file_path


def load_category_file(cat_id):
    file_path = os.path.join(CATEGORIES_DIR, str(cat_id), f"{cat_id}.json")

    if not os.path.exists(file_path):
        return None

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return None


def load_category_translated(category, language):
    special_mappings = {
        "zh": "zh-CN",
        "en": "en"
    }
    lookup_lang = special_mappings.get(language, language)

    data = load_category_file(category.id)

    if not data:
        return {
            "name": category.name or ""
        }

    field_data = data.get("name", {})
    return {
        "name": (
            field_data.get(lookup_lang)
            or field_data.get("he")
            or category.name
            or ""
        )
    }



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
        # Load or create company row
        company = Company.query.filter_by(user_id=current_user.id).first()
        if not company:
            company = Company(user_id=current_user.id)
            db.session.add(company)

        # Update base fields
        company.name = request.form.get('name', '')
        company.company_id_number = request.form.get('company_id_number', '')
        company.deduction_file = request.form.get('deduction_file', '')
        company.address = request.form.get('address', '')
        company.city = request.form.get('city', '')
        company.postal_code = request.form.get('postal_code', '')
        company.phone = request.form.get('phone', '')
        company.email = request.form.get('email', '')
        company.logo = request.form.get('logo', '')

        db.session.commit()

        # Extract fields for translation
        name = company.name
        address = company.address
        city = company.city

        # Run background translation (Threading)
        translate_company_in_background(
            company_id=company.id,
            name=name,
            address=address,
            city=city
        )

        flash("Details saved! Translations updating in background.", "success")
        return redirect(url_for('company'))

# ----------------------
#  Clear Company Results Form 
# ----------------------

@app.route('/clear_company_results', methods=['POST'])
@login_required
def clear_company_results():
    company = Company.query.filter_by(user_id=current_user.id).first()
    if company:
        company.translations_json = "{}"
        db.session.commit()

    flash("Company translations cleared.", "success")
    return redirect(url_for('company'))

       
# --------------------
# HELPER Invoice Data
# ----------------------

def base_invoice_context(customer_id=None):
    language = get_lang()  
    company = load_company_data()

    products_db = Product.query.all()
    products_list_for_js = []
    
    for p in products_db:
        item_file = load_item_file(p.id) or {}
        names_dict = item_file.get("name", {})
        
        if isinstance(names_dict, dict):
            p_name = names_dict.get(language) or names_dict.get("he") or p.name
        else:
            p_name = p.name
            
        i_cat = item_file.get("income_category", getattr(p, 'income_category', 'service'))
        
        products_list_for_js.append({
            "id": p.id,
            "name": p_name,   # 🟢 RESTORED: Passed as a clean string directly to the interface loop
            "price": float(p.price or 0),
            "cost_price": float(p.cost_price or 0),
            "quantity": int(p.quantity or 0),
            "income_category": i_cat  
        })

    customer_data = None
    if customer_id:
        c_obj = Customer.query.get(customer_id)
        if c_obj:
            customer_data = load_customer_translated(c_obj, language)

    return {
        'products': products_list_for_js,
        'all_customers': Customer.query.order_by(Customer.customer_name).all(),
        'customer': customer_data,   
        "vat_options": list(range(0, 21)), # Updated to include up to 20% comfortably
        'company': company,
        'next_invoice_num': get_next_invoice_number() 
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
    try:
        result = db.session.query(db.func.max(db.cast(Invoice.invoice_number, db.Integer))).scalar()
        if result is not None:
            return int(result) + 1
        return 1
    except Exception as e:
        print(f"⚠️ Warning: Could not calculate next invoice number automatically: {e}")
        result_raw = db.session.query(db.func.max(Invoice.invoice_number)).scalar()
        try:
            return int(result_raw) + 1 if result_raw else 1
        except:
            return 1


def invoice_context(invoice_id=None):
    try:
        language = get_lang()
        invoice = Invoice.query.get(invoice_id) if invoice_id else None

        # ----- Customer Details -----
        customer_json = {}
        if invoice and invoice.customer_id:
            c_obj = Customer.query.get(invoice.customer_id)
            if c_obj:
                trans = load_customer_translated(c_obj, language)
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

        # ----- Invoice Items -----
        items_json = []
        if invoice:
            items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
            for item in items:
                items_json.append({
                    "product_id": item.product_id,
                    "quantity": float(item.quantity or 0),
                    "unit_price": float(item.unit_price or 0),
                    "discount": float(item.discount or 0),
                    "total_price": float(item.total_price or 0),
                    "cost_price": float(getattr(item, 'cost_price_at_time', 0) or 0)
                })

        # ----- All Customers -----
        all_customers_json = []
        for c in Customer.query.order_by(Customer.customer_name).all():
            trans = load_customer_translated(c, language)
            all_customers_json.append({
                "id": c.id,
                "customer_name": trans.get("name", c.customer_name),
                "id_number": c.id_number or ""
            })

        # ----- All Products -----
        products_json = []
        for p in Product.query.all():
            item_file = load_item_file(p.id) or {}
            names_dict = item_file.get("name", {})
            
            if isinstance(names_dict, dict):
                p_name = names_dict.get(language) or names_dict.get("he") or p.name
            else:
                p_name = p.name
            
            i_cat = item_file.get("income_category", getattr(p, 'income_category', 'service'))
            
            products_json.append({
                "id": p.id,
                "name": p_name,
                "price": float(p.price or 0),
                "cost_price": float(p.cost_price or 0),
                "quantity": int(p.quantity or 0),
                "income_category": i_cat
            })

        # ----- Payments -----
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

        # ----- Totals -----
        sub_total = float(invoice.sub_total or 0) if invoice else 0
        vat_amount = float(invoice.vat_amount or 0) if invoice else 0
        grand_total = float(invoice.grand_total or 0) if invoice else 0
        discount_total = float(getattr(invoice, 'discount_total', 0) or 0) if invoice else 0

        # ----- VAT RATE  -----
        if invoice and hasattr(invoice, 'vat_rate') and invoice.vat_rate is not None:
            vat_rate = float(invoice.vat_rate)
        else:
            # ברירת מחדל לחשבונית חדשה – שנה כאן אם אתה רוצה 6/17/18
            vat_rate = 0.0

        ctx = base_invoice_context()
        ctx.update({
            "invoice": invoice,
            "invoice_id": invoice.id if invoice else None,
            "allocation_number": invoice.allocation_number if invoice else None,
            "invoice_number": invoice.invoice_number if invoice else get_next_invoice_number(),
            "invoice_date": invoice.invoice_date.strftime('%d-%m-%Y') if invoice else datetime.today().strftime('%d-%m-%Y'),
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
            "transaction": Transaction.query.filter_by(invoice_id=invoice.id).first() if invoice else None
        })
        return ctx

    except Exception as e:
        import traceback
        print(f"❌ CRITICAL ERROR in invoice_context: {e}")
        traceback.print_exc()
        return {
            "error": str(e),
            "invoice": None,
            "invoice_id": None,
            "company": load_company_data() or {},
            "customer_json": {},
            "all_customers_json": [],
            "products": [],
            "items": [],
            "loadedPayments": [],
            "sub_total": 0.0,
            "vat_amount": 0.0,
            "grand_total": 0.0,
            "discount_total": 0.0,
            "vat_rate": 0.0,  
            "invoice_number": get_next_invoice_number(),
            "invoice_date": datetime.today().strftime('%d-%m-%Y'),
            "invoice_status": "active"
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

    # Guardrail against uninitialized client properties
    if not customer_id:
        flash("שגיאה: יש לבחור לקוח חוקי על מנת לשמור את המסמך", "error")
        return redirect(url_for('invoice'))

    sub_total = clean_float(request.form.get('sub_total'))
    vat_amount = clean_float(request.form.get('vat_amount'))
    grand_total = clean_float(request.form.get('grand_total'))
    
    vat_rate_raw = request.form.get('vat_rate_select')
    vat_rate = clean_float(vat_rate_raw) if vat_rate_raw not in [None, "", "null"] else 0.0

    total_invoice_cost = 0.0 

    # ------ 1. UPDATE EXISTING INVOICE --------
    if invoice_id:
        invoice = Invoice.query.get(invoice_id)
        if not invoice:
            flash("החשבונית המבוקשת לעריכה אינה קיימת במערכת", "error")
            return redirect(url_for('invoice'))

        old_items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
        for old_item in old_items:
            prod = Product.query.get(old_item.product_id)
            i_cat_old = getattr(prod, 'income_category', 'service')
            
            item_file_old = load_item_file(old_item.product_id) if old_item.product_id else None
            if item_file_old:
                i_cat_old = item_file_old.get("income_category", i_cat_old)

            if prod and i_cat_old == 'product':
                prod.quantity += old_item.quantity 
                if item_file_old:
                    save_item_file(
                        product_id=prod.id, name_trans=item_file_old.get("name", {}), desc_trans=item_file_old.get("description", {}),
                        price=prod.price, income_category=i_cat_old, cost_price=prod.cost_price,
                        stock_in=item_file_old.get("stock_in", 0), stock_out=int(db.session.query(func.sum(InvoiceItem.quantity)).filter(InvoiceItem.product_id == prod.id).scalar() or 0),
                        supplier_id=item_file_old.get("supplier_id"), received_date=item_file_old.get("received_date")
                    )

        invoice.customer_id = customer_id
        invoice.sub_total = sub_total
        invoice.vat_amount = vat_amount
        invoice.grand_total = grand_total
        invoice.vat_rate = vat_rate 
        invoice.status = "active"

        if not invoice.allocation_number:
            invoice.allocation_number = generate_allocation_number()

        InvoiceItem.query.filter_by(invoice_id=invoice.id).delete()
        Payment.query.filter_by(invoice_id=invoice.id).delete()

        items = request.form.getlist('items[]')
        for item_json in items:
            try:
                item_data = json.loads(item_json)
            except Exception:
                continue 

            prod = Product.query.get(item_data['product_id'])
            item_file = load_item_file(prod.id) if prod else None
            
            i_cat = getattr(prod, 'income_category', 'service') if prod else 'service'
            if item_file:
                i_cat = item_file.get("income_category", i_cat)
            
            c_price = prod.cost_price if (prod and i_cat == 'product') else 0.0
            qty = clean_float(item_data.get('quantity'))
            
            if prod and i_cat == 'product':
                prod.quantity -= qty
                db.session.flush() # Forces synchronization state before calculation tracking runs
                
                if item_file:
                    actual_out_calc = int(db.session.query(func.sum(InvoiceItem.quantity)).filter(InvoiceItem.product_id == prod.id).scalar() or 0) + int(qty)
                    save_item_file(
                        product_id=prod.id, name_trans=item_file.get("name", {}), desc_trans=item_file.get("description", {}),
                        price=prod.price, income_category=i_cat, cost_price=prod.cost_price,
                        stock_in=item_file.get("stock_in", 0), stock_out=actual_out_calc,
                        supplier_id=item_file.get("supplier_id"), received_date=item_file.get("received_date")
                    )
            
            u_price = clean_float(item_data.get('price'))
            disc = clean_float(item_data.get('discount', 0))
            total_after_discount = (qty * u_price) - (qty * u_price * (disc/100) if disc < 100 else disc)
            total_invoice_cost += (qty * c_price)

            db.session.add(InvoiceItem(
                invoice_id=invoice.id, product_id=item_data['product_id'],
                quantity=qty, unit_price=u_price, discount=disc,
                total_price=total_after_discount, cost_price_at_time=c_price,
                income_category=i_cat 
            ))

        amounts = request.form.getlist('payment_amount[]')
        payment_dates = request.form.getlist('payment_date[]')
        methods = request.form.getlist('payment_method[]')
        for i in range(len(amounts)):
            amt = clean_float(amounts[i])
            if amt <= 0: continue
            p_date = datetime.strptime(payment_dates[i], "%Y-%m-%d").date() if payment_dates[i] else None
            db.session.add(Payment(
                invoice_id=invoice.id, payment_date=p_date, payment_method=methods[i],
                payment_amount=amt, bank=request.form.getlist('bank[]')[i],
                branch=request.form.getlist('branch[]')[i], account_number=request.form.getlist('account_number[]')[i]
            ))

        Transaction.query.filter_by(invoice_id=invoice.id).delete()
        db.session.add(Transaction(
            date=invoice.invoice_date, 
            description=f"חשבונית #{invoice.invoice_number}",
            amount=sub_total, # 🟢 Restored net basis tracking to insulate P&L against tax pollution
            type='income', category_id=None, invoice_id=invoice.id, customer_id=customer_id, 
            cost_price_at_time=total_invoice_cost, 
            user_id=current_user.id if (current_user.is_authenticated and str(current_user.id) != "0") else None
        ))

        db.session.commit()
        flash('החשבונית עודכנה בהצלחה והמלאי סונכרן', 'success')
        return redirect(url_for('invoice_view', invoice_id=invoice.id))

    # ------ 2. CREATE NEW INVOICE --------
    invoice_number = get_next_invoice_number()

    new_invoice = Invoice(
        invoice_number=invoice_number,
        invoice_date=datetime.today().date(),
        customer_id=customer_id, 
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

        prod = Product.query.get(item_data['product_id'])
        item_file = load_item_file(prod.id) if prod else None
        
        i_cat = getattr(prod, 'income_category', 'service') if prod else 'service'
        if item_file:
            i_cat = item_file.get("income_category", i_cat)
        
        c_price = prod.cost_price if (prod and i_cat == 'product') else 0.0
        qty = clean_float(item_data.get('quantity'))
        
        if prod and i_cat == 'product':
            prod.quantity -= qty
            db.session.flush()
            
            if item_file:
                actual_out_calc = int(db.session.query(func.sum(InvoiceItem.quantity)).filter(InvoiceItem.product_id == prod.id).scalar() or 0) + int(qty)
                save_item_file(
                    product_id=prod.id, name_trans=item_file.get("name", {}), desc_trans=item_file.get("description", {}),
                    price=prod.price, income_category=i_cat, cost_price=prod.cost_price,
                    stock_in=item_file.get("stock_in", 0), stock_out=actual_out_calc,
                    supplier_id=item_file.get("supplier_id"), received_date=item_file.get("received_date")
                )
            
        u_price = clean_float(item_data.get('price'))
        disc = clean_float(item_data.get('discount', 0))
        row_total = (qty * u_price) - (qty * u_price * (disc/100))
        total_invoice_cost += (qty * c_price)

        db.session.add(InvoiceItem(
            invoice_id=new_invoice.id, product_id=item_data['product_id'],
            quantity=qty, unit_price=u_price, discount=disc,
            total_price=row_total, cost_price_at_time=c_price,
            income_category=i_cat 
        ))

    amounts = request.form.getlist('payment_amount[]')
    payment_dates = request.form.getlist('payment_date[]')
    methods = request.form.getlist('payment_method[]')
    for i in range(len(amounts)):
        amt = clean_float(amounts[i])
        if amt <= 0: continue
        p_date = datetime.strptime(payment_dates[i], "%Y-%m-%d").date() if payment_dates[i] else None
        db.session.add(Payment(
            invoice_id=new_invoice.id, payment_date=p_date, payment_method=methods[i],
            payment_amount=amt, bank=request.form.getlist('bank[]')[i],
            branch=request.form.getlist('branch[]')[i], account_number=request.form.getlist('account_number[]')[i]
        ))

    new_trans = Transaction(
        date=new_invoice.invoice_date, 
        description=f"חשבונית #{new_invoice.invoice_number}",
        amount=sub_total, #  Restored net basis tracking to isolate reports against tax pollution
        type='income', category_id=None, invoice_id=new_invoice.id, customer_id=customer_id, 
        cost_price_at_time=total_invoice_cost, 
        user_id=current_user.id if (current_user.is_authenticated and str(current_user.id) != "0") else None
    )
    db.session.add(new_trans)

    db.session.commit()
    flash('החשבונית הופקה בהצלחה והמלאי עודכן', 'success')
    return redirect(url_for('invoice_view', invoice_id=new_invoice.id))

# --------------------
# Create Invoice Form Data
# ----------------------

@app.route('/invoice/create', methods=['GET'])
@login_required
def invoice():
    invoice_id = request.args.get('invoice_id')
    
    ctx = invoice_context(invoice_id)  
    
    return render_template('invoice.html', **ctx)

# --------------------
# Show Invoice View Data
# ----------------------

@app.route('/invoice/<int:invoice_id>', methods=['GET'])
@login_required
def invoice_view(invoice_id):
    ctx = invoice_context(invoice_id)
    
    if "company" not in ctx or not ctx["company"]:
        ctx["company"] = load_company_data()
        
    if not ctx.get("invoice"):
        flash("חשבונית לא נמצאה", "danger")
        return redirect(url_for('invoice_data')) # Redirects to your central list data matrix
        
    return render_template('invoice.html', **ctx)

# --------------------
# Clear All Invoice Data (State Reset)
# ----------------------

@app.route("/invoice/new", methods=["GET", "POST"])
@login_required
def new_invoice():
    return redirect(url_for('invoice'))

# --------------------
# Cancel Invoice ID Form Data
# ----------------------

@app.route('/invoice/<int:invoice_id>/cancel', methods=['POST'])
@login_required
def cancel_invoice(invoice_id):
    invoice = Invoice.query.get(invoice_id)

    if not invoice:
        flash("המסמך המבוקש אינו קיים במערכת", "error")
        return redirect(url_for('invoice_data'))

    if invoice.status in ["canceled", "מבוטלת"]:
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    items = InvoiceItem.query.filter_by(invoice_id=invoice.id).all()
    for item in items:
        prod = Product.query.get(item.product_id)
        if prod and getattr(prod, 'income_category', 'service') == 'product':
            prod.quantity += item.quantity 

    Transaction.query.filter_by(invoice_id=invoice.id).delete()

    invoice.status = "canceled"
    
    db.session.commit()

    flash("החשבונית בוטלה בהצלחה ותנועות המס עודכנו", "success")
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
    selected_status = request.args.get("status", "all")

    if not selected_year:
        selected_year = str(datetime.today().year)
    if not selected_month:
        selected_month = datetime.today().strftime('%m')

    invoices = Invoice.query.options(
        db.joinedload(Invoice.customer),
        db.joinedload(Invoice.transactions)
    ).order_by(db.cast(Invoice.invoice_number, db.Integer).desc()).all()

    filtered_invoices = []
    customer_i18n_list = {}
    total_profit = 0.0 
    is_numeric_search = search.isdigit()

    for inv in invoices:
        trans_name = ""
        if inv.customer:
            cid = inv.customer.id
            if cid not in customer_i18n_list:
                customer_i18n_list[cid] = load_customer_translated(inv.customer, language)
            
            trans_name = (customer_i18n_list[cid].get('name', '') or "").lower()

        match_status = (selected_status == "all" or inv.status == selected_status)

        if not search:
            match_search = True
        else:
            db_name = inv.customer.customer_name.lower() if inv.customer else ""
            if is_numeric_search:
                try:
                    match_search = (int(inv.invoice_number) == int(search))
                except Exception:
                    match_search = (str(inv.invoice_number) == search)
            else:
                match_search = (search in db_name or 
                                search in trans_name or 
                                search in str(inv.invoice_date))

        inv_year = str(inv.invoice_date.year)
        inv_month = inv.invoice_date.strftime('%m')
        match_month = not selected_month or inv_month == selected_month
        match_year = not selected_year or inv_year == selected_year

        if match_status and match_search and match_month and match_year:
            inv_trans = next((t for t in inv.transactions if t.type == 'income'), None)
            row_profit = 0.0
            
            if inv_trans:
                cost = float(inv_trans.cost_price_at_time or 0.0)
                row_profit = float(inv.sub_total or 0.0) - cost
                
                if inv.status != "canceled":
                    total_profit += row_profit
            
            inv.profit = row_profit
            
            filtered_invoices.append(inv)

    active_invoices = [inv for inv in filtered_invoices if inv.status != "canceled"]
    total_amount = sum(float(inv.grand_total or 0.0) for inv in active_invoices)
    total_count = len(active_invoices)

    months_hebrew = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]
    available_years = [str(y) for y in range(2024, 2031)]

    return render_template(
        'invoice_data.html',
        invoices=filtered_invoices,
        total_amount=total_amount,
        total_profit=total_profit,
        total_count=total_count,
        search=search,
        selected_month=selected_month,
        selected_year=selected_year,
        selected_status=selected_status,
        months=months_hebrew,
        years=available_years,
        customer_i18n_list=customer_i18n_list,
        language=language,
        company=load_company_data()
    )

# ----------------------
# Send Email Invoices To Customers invoice_data Page
# ----------------------

@app.route('/send_invoice_email/<int:invoice_id>')
@login_required
def send_invoice_email(invoice_id):
    invoice = Invoice.query.get_or_404(invoice_id)
    customer = invoice.customer

    if not customer or not customer.email:
        flash("ללקוח לא מוגדר אימייל! אנא עדכן את כרטיס הלקוח תחילה.", "danger")
        return redirect(url_for('invoice_view', invoice_id=invoice_id))

    #  REPLACED CELERY WITH THREADING
    send_invoice_email_in_background(invoice_id)

    flash("בקשת השליחה התקבלה! החשבונית מופקת ונשלחת ללקוח ברקע.", "success")
    return redirect(url_for('invoice_view', invoice_id=invoice_id))


def send_invoice_email_in_background(invoice_id):
    thread = threading.Thread(
        target=run_invoice_email_task,
        args=(invoice_id,)
    )
    thread.daemon = True
    thread.start()


def run_invoice_email_task(invoice_id):
    try:
        from playwright.sync_api import sync_playwright
        from flask_mail import Message

        with app.app_context():
            invoice = Invoice.query.get(invoice_id)
            if not invoice or not invoice.customer or not invoice.customer.email:
                print(f"❌ Missing invoice or customer email for ID {invoice_id}")
                return

            company_data = load_company_data()
            customer = invoice.customer

            ctx = invoice_context(invoice_id)
            ctx["company"] = company_data

            # Render invoice HTML
            html_content = render_template("invoice.html", **ctx, is_pdf=True)

            # Generate PDF
            with sync_playwright() as p:
                browser = p.chromium.launch(args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage"
                ])
                page = browser.new_page()
                page.set_content(html_content, wait_until="networkidle")

                pdf_data = page.pdf(
                    format="A4",
                    print_background=True,
                    scale=1.0,
                    margin={"top": "20px", "right": "20px", "bottom": "20px", "left": "20px"}
                )
                browser.close()

            # Email body
            email_body = f"""
שלום {customer.customer_name}

להלן מצורפת חשבונית מספר {invoice.invoice_number}
לתאריך {invoice.invoice_date.strftime('%d-%m-%Y')}

תודה לשירותך תמיד
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
            print(f"✔ Email sent for Invoice #{invoice.invoice_number}")

    except Exception as e:
        print(f"❌ Email sending failed for invoice {invoice_id}: {e}")


# --------------------
#  Create Payment Invoice Form Data
# ----------------------

@app.route('/api/payments/create', methods=['POST'])
@login_required
def create_payment():
    data = request.get_json()
    allocation_number = data.get('allocation_number')

    invoice = Invoice.query.filter_by(allocation_number=allocation_number).first_or_404()

    customer = Customer.query.get(invoice.customer_id)

    payment_request = {
        "merchant_id": "YOUR_MERCHANT_ID",

        "amount": invoice.grand_total,

        "description": f"תשלום עבור חשבונית מס {invoice.allocation_number}",

        "customer_name": customer.customer_name,
        "customer_phone": customer.phone,
        "customer_address": f"{customer.address}, {customer.city}",
        "customer_id_number": customer.id_number,

        "internal_customer_id": customer.id,
        "internal_invoice_id": invoice.id
    }

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
#  Manage Products Invoice Form Data
# ----------------------

@app.route('/products_manage', methods=['GET', 'POST'])
@login_required
def manage_products():
    language = get_lang()

    if request.method == 'POST':
        product_id = (request.form.get("id") or "").strip()
        name = (request.form.get('name') or '').strip()
        description = (request.form.get('description') or '').strip()
        
        received_date_raw = request.form.get('received_date')
        if not received_date_raw:
            flash('תאריך הוא שדה חובה', 'error')
            return redirect(url_for('manage_products'))

        try:
            date_obj = datetime.strptime(received_date_raw, '%Y-%m-%d')
            formatted_date = date_obj.strftime('%d/%m/%Y')
        except Exception:
            formatted_date = received_date_raw

        income_category = request.form.get("income_category", "service")

        def to_float(val, default=0.0):
            try: return float(val)
            except Exception: return default

        def to_int(val, default=0):
            try: return int(val)
            except Exception: return default

        price = to_float(request.form.get('price', 0))
        cost_price = to_float(request.form.get('cost_price', 0))
        stock_in = to_int(request.form.get('stock_in', 0))
        supplier_id = (request.form.get('supplier_id') or '').strip()

        total_sold = 0
        if product_id:
            total_sold = db.session.query(func.sum(InvoiceItem.quantity))\
                                   .filter(InvoiceItem.product_id == product_id)\
                                   .scalar() or 0

        stock_out = int(total_sold)
        current_stock = stock_in - stock_out

        if product_id:
            product = Product.query.get(product_id)
            if product:
                product.name = name
                product.price = price
                product.description = description
                product.cost_price = cost_price
                product.quantity = current_stock
                product.income_category = income_category 
                product.received_date = formatted_date 
                db.session.commit()
        else:
            product = Product(
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

        if supplier_id and stock_in > 0:
            try:
                sp = SupplierPurchase(
                    supplier_id=int(supplier_id),
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
            except Exception:
                db.session.rollback()

        translate_product_in_background(
            product_id=product_id,
            name=name,
            description=description,
            price=price,
            income_category=income_category,
            cost_price=cost_price,
            stock_in=stock_in,
            stock_out=stock_out,
            supplier_id=supplier_id or None,
            received_date=formatted_date
        )

        flash('הנתונים עודכנו בהצלחה ותהליך התרגום רץ ברקע!', 'success')
        return redirect(url_for('manage_products'))

    search = (request.args.get("q") or "").strip().lower()
    today_str = datetime.today().strftime('%Y-%m-%d') 
    all_products = Product.query.order_by(Product.id.desc()).all()

    filtered_objects = all_products if not search else []
    if search:
        is_numeric = search.isdigit()
        for p in all_products:
            item_file = load_item_file(p.id)
            names = item_file.get("name", {"he": p.name or ""}) if item_file else {"he": p.name or ""}
            descs = item_file.get("description", {"he": p.description or ""}) if item_file else {"he": p.description or ""}
            
            if is_numeric:
                match = (str(p.id) == search)
            else:
                match = (search in (names.get("he", "") or "").lower() or 
                         search in (descs.get("he", "") or "").lower())
            if match:
                filtered_objects.append(p)

    item_i18n_list = {}
    for p in filtered_objects:
        item_file = load_item_file(p.id)
        actual_out = db.session.query(func.sum(InvoiceItem.quantity))\
                               .filter(InvoiceItem.product_id == p.id)\
                               .scalar() or 0

        current_date_val = p.received_date
        if current_date_val and "/" in current_date_val:
            try:
                d_obj = datetime.strptime(current_date_val, '%d/%m/%Y')
                current_date_val = d_obj.strftime('%Y-%m-%d')
            except Exception:
                pass

        if item_file:
            names = item_file.get("name", {})
            descs = item_file.get("description", {})
            item_i18n_list[p.id] = {
                "id": p.id,
                "name": names.get(language, names.get("he", "")),
                "description": descs.get(language, descs.get("he", "")),
                "income_category": item_file.get("income_category", getattr(p, 'income_category', 'service')),
                "price": p.price,
                "cost_price": p.cost_price,
                "quantity": p.quantity,
                "stock_in": item_file.get("stock_in", 0),
                "stock_out": int(actual_out),
                "supplier_id": item_file.get("supplier_id"),
                "received_date": current_date_val
            }
        else:
            item_i18n_list[p.id] = {
                "id": p.id,
                "name": p.name or "",
                "description": p.description or "",
                "income_category": getattr(p, 'income_category', 'service'),
                "price": p.price,
                "cost_price": p.cost_price or 0,
                "quantity": p.quantity or 0,
                "stock_in": 0,
                "stock_out": int(actual_out),
                "supplier_id": None,
                "received_date": current_date_val
            }

    item_i18n = item_i18n_list[filtered_objects[0].id] if (search and len(filtered_objects) == 1) else None

    products_json = []
    for p in filtered_objects:
        # 👇 זה התיקון הקריטי
        item_file = load_item_file(p.id) or {}
        
        p_date = p.received_date
        if p_date and "/" in p_date:
            try:
                p_date = datetime.strptime(p_date, '%d/%m/%Y').strftime('%Y-%m-%d')
            except Exception:
                pass

        products_json.append({
            "id": p.id,
            "name": item_file.get("name", {"he": p.name or ""}) if item_file else {"he": p.name or ""},
            "description": item_file.get("description", {"he": p.description or ""}) if item_file else {"he": p.description or ""},
            "price": float(p.price or 0),
            "cost_price": float(p.cost_price or 0),
            "income_category": item_file.get("income_category", getattr(p, 'income_category', 'service')),
            "received_date": p_date,
            "stock_in": item_file.get("stock_in", 0) if item_file else 0
        })

    all_suppliers = Supplier.query.order_by(Supplier.supplier_name).all()
    suppliers_translated = []
    
    for s in all_suppliers:
        s_trans = load_supplier_translated(s, language)
        suppliers_translated.append({
            "id": s.id,
            "supplier_name": s_trans.get("name", s.supplier_name)
        })

    return render_template(
        'products_manage.html',
        products=filtered_objects,
        products_json=products_json,
        search=search,
        item_i18n=item_i18n,
        item_i18n_list=item_i18n_list,
        current_lang=language,
        suppliers=suppliers_translated,
        today=today_str
    )


# --------------------
#  Products List Selected Combobox Data
# ----------------------
@app.get("/api/products_list")
@login_required
def products_list():
    language = get_lang()
    products = Product.query.order_by(Product.id.asc()).all()
    result = []

    for p in products:
        # 👇 התיקון הקריטי — לא לתת ל-None להפיל את כל ה-API
        item_file = load_item_file(p.id) or {}

        # כמה נמכר בפועל
        total_sold = db.session.query(func.sum(InvoiceItem.quantity))\
                               .filter(InvoiceItem.product_id == p.id)\
                               .scalar() or 0

        # ערכי ברירת מחדל מה-DB
        stock_in = item_file.get("stock_in", 0)
        supplier_id = item_file.get("supplier_id")
        income_category = item_file.get("income_category", getattr(p, 'income_category', 'service'))
        received_date = item_file.get("received_date", getattr(p, 'received_date', None))

        # מלאי נוכחי
        actual_quantity = int(stock_in) - int(total_sold)

        # תרגום שם ותיאור
        name_he = p.name or ""
        desc_he = p.description or ""

        names_dict = item_file.get("name", {"he": name_he})
        descs_dict = item_file.get("description", {"he": desc_he})

        # שם מתורגם
        if isinstance(names_dict, dict):
            p_name = names_dict.get(language) or names_dict.get("he") or name_he
        else:
            p_name = name_he

        # תיאור מתורגם
        if isinstance(descs_dict, dict):
            p_desc = descs_dict.get(language) or descs_dict.get("he") or desc_he
        else:
            p_desc = desc_he

        # ספק מתורגם
        supplier_name = None
        if supplier_id:
            supplier = Supplier.query.get(supplier_id)
            if supplier:
                s_trans = load_supplier_translated(supplier, language)
                supplier_name = s_trans.get("name", supplier.supplier_name)

        api_date = received_date if received_date else ""

        result.append({
            "id": p.id,
            "name": p_name,
            "description": p_desc,
            "price": float(p.price or 0),
            "cost_price": float(p.cost_price or 0),
            "quantity": actual_quantity,
            "stock_in": int(stock_in),
            "stock_out": int(total_sold),
            "supplier_id": supplier_id,
            "supplier_name": supplier_name,
            "income_category": income_category,
            "received_date": api_date
        })

    return jsonify(result)



#  Products Delete Folder Path From Static Folder
def delete_product_folder(product_id):
    """Safely purges external translation file containers from the local volume storage disk"""
    folder_path = os.path.join(ITEMS_DIR, str(product_id))
    if os.path.exists(folder_path):
        try:
            shutil.rmtree(folder_path)
        except Exception as e:
            print(f"⚠️ Warning: Could not remove product folder path directory node: {e}")


#  Products Delete Selected Form Data
@app.route('/delete_selected_products', methods=['POST'])
@login_required
def delete_selected_products():
    selected_product_ids = request.form.getlist('delete_products')

    if not selected_product_ids:
        flash(py_i18n("products.delete_none_selected"), "warning")
        return redirect(url_for('manage_products'))

    try:
        product_ids_int = [int(p_id) for p_id in selected_product_ids]

        Product.query.filter(Product.id.in_(product_ids_int)).delete(synchronize_session=False)
        db.session.commit()

        for pid in product_ids_int:
            delete_product_folder(pid)

        flash(py_i18n("products.delete_success").format(count=len(selected_product_ids)), "success")
        return redirect(url_for('manage_products'))

    except Exception as e:
        db.session.rollback()
        print(f"❌ Product Deletion Fault Triggered: {e}")
        flash(py_i18n("products.delete_error").format(error=str(e)), "danger")
        return redirect(url_for('manage_products'))


# ----------------------
#   Build All Customer Form
# ----------------------

@app.route('/customer', methods=['GET', 'POST'])
@login_required
def customer():
    current_uid = None
    try:
        if current_user.is_authenticated:
            uid = str(current_user.id)
            if uid != "0":
                current_uid = int(uid)
    except:
        current_uid = None

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

        customer_id = request.form.get('customer_id')
        id_number = request.form.get('id_number')

        # ------------------ UPDATE CUSTOMER ------------------
        if customer_id:
            customer_obj = Customer.query.filter_by(id=customer_id, user_id=current_uid).first()
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

                #  REPLACED CELERY WITH THREADING
                translate_customer_in_background(
                    customer_obj.id,
                    customer_obj.customer_name,
                    customer_obj.address,
                    customer_obj.city,
                    customer_obj.message
                )

                flash('הנתונים עודכנו בהצלחה!', 'success')

        # ------------------ CREATE NEW CUSTOMER ------------------
        else:
            if id_number and Customer.query.filter_by(id_number=id_number, user_id=current_uid).first():
                flash("קיים כבר לקוח עם מספר זהות זה", "error")
                return redirect(url_for('customer'))

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
                user_id=current_uid
            )

            db.session.add(new_customer)
            db.session.commit()

            #  REPLACED CELERY WITH THREADING
            translate_customer_in_background(
                new_customer.id,
                new_customer.customer_name,
                new_customer.address,
                new_customer.city,
                new_customer.message
            )

            flash('הלקוח נוסף בהצלחה!', 'success')

        return redirect(url_for('customer'))

    # ------------------ GET REQUEST ------------------
    language = get_lang()
    today_str = datetime.today().strftime('%Y-%m-%d')

    all_customers = Customer.query.filter_by(user_id=current_uid).order_by(Customer.customer_name).all()
    customer_id = request.args.get('customer_id')

    selected_customer = None
    customer_i18n = {}

    if customer_id:
        c_obj = Customer.query.filter_by(id=customer_id, user_id=current_uid).first()
        if c_obj:
            selected_customer = c_obj
            try:
                customer_i18n = load_customer_translated(c_obj, language) or {}
            except:
                customer_i18n = {}

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
    for c in all_customers:
        try:
            customer_i18n_list[c.id] = load_customer_translated(c, language) or {}
        except:
            customer_i18n_list[c.id] = {}

    return render_template(
        'customer.html',
        customer=selected_customer,
        all_customers=all_customers,
        customer_i18n=customer_i18n,
        customer_i18n_list=customer_i18n_list,
        today=today_str,
        input_date_val=input_date_val
    )


# ----------------------
#   API All Customer Form
# ----------------------

@app.route('/api/customer/<int:customer_id>')
@login_required
def api_get_customer(customer_id):
    current_uid = None
    try:
        if current_user.is_authenticated:
            uid = str(current_user.id)
            if uid != "0":
                current_uid = int(uid)
    except:
        current_uid = None

    language = get_lang()
    
    c = Customer.query.filter_by(id=customer_id, user_id=current_uid).first()

    if not c:
        return jsonify({"error": "Customer not found"}), 404

    formatted_date_for_picker = ""
    if c.date:
        try:
            temp_date = datetime.strptime(c.date, '%d/%m/%Y')
            formatted_date_for_picker = temp_date.strftime('%Y-%m-%d')
        except Exception:
            formatted_date_for_picker = c.date

    try:
        trans = load_customer_translated(c, language) or {}
    except:
        trans = {}

    return jsonify({
        "customer_name": trans.get("name", c.customer_name or ""),
        "address": trans.get("address", c.address or ""),
        "city": trans.get("city", c.city or ""),
        "postal_code": c.postal_code or "",
        "id_number": c.id_number or "",
        "phone": c.phone or "",
        "email": c.email or "",
        "contract_status": c.contract_status or "",
        "message": trans.get("message", c.message or ""),
        "date": formatted_date_for_picker,
        "is_active": c.is_active,
        "role": c.role or "customer"
    })


@app.route('/customer_data', methods=['GET'])
@login_required
def customer_data():
    current_uid = None
    try:
        if current_user.is_authenticated:
            uid = str(current_user.id)
            if uid != "0":
                current_uid = int(uid)
    except:
        current_uid = None

    language = get_lang()
    
    raw_customers = Customer.query.filter_by(user_id=current_uid).all()

    customers_list = []
    customer_i18n_list = {}

    for c in raw_customers:
        try:
            trans = load_customer_translated(c, language) or {}
        except:
            trans = {}

        customers_list.append({
            "id": c.id,
            "date": c.date or "",
            "customer_name": trans.get("name", c.customer_name or ""),
            "id_number": c.id_number or "",
            "address": trans.get("address", c.address or ""),
            "city": trans.get("city", c.city or ""),
            "postal_code": c.postal_code or "",
            "phone": c.phone or "",
            "email": c.email or "",
            "contract_status": c.contract_status or "",
            "message": trans.get("message", c.message or "")
        })

        customer_i18n_list[c.id] = trans

    return render_template(
        'customer_data.html', 
        customers=customers_list, 
        customer_i18n_list=customer_i18n_list
    )


# --------------------
#   Search And Clear Customer Form
# ----------------------

@app.route('/search_customer', methods=['GET', 'POST'])
@login_required
def search_customer():
    current_uid = None
    try:
        if current_user.is_authenticated:
            uid = str(current_user.id)
            if uid != "0":
                current_uid = int(uid)
    except:
        current_uid = None

    language = get_lang()

    search_name = request.form.get('search_name') if request.method == 'POST' else request.args.get('search_name')

    search_results = []
    customer = None
    
    today_str = datetime.today().strftime('%Y-%m-%d')
    input_date_val = today_str

    if search_name:
        search_name = search_name.strip()

        search_results = Customer.query.filter(
            Customer.user_id == current_uid,
            Customer.customer_name.ilike(f'%{search_name}%')
        ).all()

        if search_results:
            # Anchor to the first matching customer row entry found
            customer = search_results[0]

            if customer.date:
                input_date_val = customer.date
                try:
                    if "/" in input_date_val:
                        # Safely convert from DB layout (DD/MM/YYYY) to HTML structure (YYYY-MM-DD)
                        parts = input_date_val.split("/")
                        if len(parts) == 3:
                            input_date_val = f"{parts[2]}-{parts[1]}-{parts[0]}"
                    else:
                        datetime.strptime(input_date_val, "%Y-%m-%d")
                except Exception:
                    input_date_val = today_str # Clean validation fallback

    all_customers = Customer.query.filter_by(user_id=current_uid).order_by(Customer.customer_name).all()

    customer_i18n = {}
    if customer:
        try:
            customer_i18n = load_customer_translated(customer, language) or {}
        except:
            customer_i18n = {}

    customer_i18n_list = {}
    for c in all_customers:
        try:
            customer_i18n_list[c.id] = load_customer_translated(c, language) or {}
        except:
            customer_i18n_list[c.id] = {}

    return render_template(
        'customer.html',
        customers=search_results,         
        all_customers=all_customers,     
        customer=customer,               
        customer_i18n=customer_i18n,     
        customer_i18n_list=customer_i18n_list,
        today=today_str,
        input_date_val=input_date_val     
    )


@app.route('/clear_search_results_customer', methods=['POST'])
@login_required
def clear_search_results_customer():
    current_uid = None
    try:
        if current_user.is_authenticated:
            uid = str(current_user.id)
            if uid != "0":
                current_uid = int(uid)
    except:
        current_uid = None

    language = get_lang()
    today_str = datetime.today().strftime('%Y-%m-%d')

    all_customers = Customer.query.filter_by(user_id=current_uid).order_by(Customer.customer_name).all()

    customer_i18n_list = {}
    for c in all_customers:
        try:
            customer_i18n_list[c.id] = load_customer_translated(c, language) or {}
        except:
            customer_i18n_list[c.id] = {}

    return render_template(
        'customer.html',
        customers=[],                    
        all_customers=all_customers,
        customer=None,
        customer_i18n={},
        customer_i18n_list=customer_i18n_list,
        today=today_str,
        input_date_val=today_str         
    )


# ----------------------
#   Build All Supplier Form
# ----------------------

@app.route('/supplier', methods=['GET', 'POST'])
@login_required
def supplier():
    if request.method == 'POST':

        # --- DATE FORMAT ---
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
        supplier_number = request.form.get('supplier_number')

        # UPDATE SUPPLIER
        if supplier_id:
            supplier_obj = Supplier.query.get(supplier_id)
            if supplier_obj:
                supplier_obj.date = formatted_date
                supplier_obj.supplier_name = request.form.get('supplier_name')
                supplier_obj.supplier_number = supplier_number
                supplier_obj.address = request.form.get('address')
                supplier_obj.city = request.form.get('city')
                supplier_obj.postal_code = request.form.get('postal_code')
                supplier_obj.phone = request.form.get('phone')
                supplier_obj.email = request.form.get('email')
                supplier_obj.payment_terms = request.form.get('payment_terms')
                supplier_obj.notes = request.form.get('notes')

                db.session.commit()

                #  REPLACED CELERY WITH THREADING
                translate_supplier_in_background(
                    supplier_obj.id,
                    supplier_obj.supplier_name,
                    supplier_obj.address,
                    supplier_obj.city,
                    supplier_obj.postal_code,
                    supplier_obj.notes
                )

                flash('נתוני הספק עודכנו בהצלחה!', 'success')

        # CREATE NEW SUPPLIER
        else:
            # --- CHECK DUPLICATE SUPPLIER NUMBER ---
            if supplier_number and Supplier.query.filter_by(supplier_number=supplier_number).first():
                flash("קיים כבר ספק עם מספר ספק זה", "error")
                return redirect(url_for('supplier'))

            # --- USER ID EXTRACTION ---
            actual_user_id = None
            try:
                if current_user.is_authenticated:
                    uid = str(current_user.id)
                    if uid != "0":
                        actual_user_id = int(uid)
            except Exception:
                actual_user_id = None

            new_supplier = Supplier(
                date=formatted_date,
                supplier_name=request.form.get('supplier_name'),
                supplier_number=supplier_number,
                address=request.form.get('address'),
                city=request.form.get('city'),
                postal_code=request.form.get('postal_code'),
                phone=request.form.get('phone'),
                email=request.form.get('email'),
                payment_terms=request.form.get('payment_terms'),
                notes=request.form.get('notes'),
                role='supplier',
                is_active=True,
                user_id=actual_user_id
            )

            db.session.add(new_supplier)
            db.session.commit()

            #  REPLACED CELERY WITH THREADING
            translate_supplier_in_background(
                new_supplier.id,
                new_supplier.supplier_name,
                new_supplier.address,
                new_supplier.city,
                new_supplier.postal_code,
                new_supplier.notes
            )

            flash('הספק נוסף בהצלחה!', 'success')

        return redirect(url_for('supplier'))

    # ---------- GET PART ----------
    language = get_lang()
    today_str = datetime.today().strftime('%Y-%m-%d')

    all_suppliers = Supplier.query.order_by(Supplier.supplier_name).all()
    supplier_id = request.args.get('supplier_id')

    selected_supplier = None
    supplier_i18n = {}

    if supplier_id:
        s_obj = Supplier.query.get(supplier_id)
        if s_obj:
            selected_supplier = s_obj
            supplier_i18n = load_supplier_translated(s_obj, language)

    # Convert date to YYYY-MM-DD for HTML input
    input_date_val = today_str
    if selected_supplier and selected_supplier.date:
        input_date_val = selected_supplier.date
        if "/" in input_date_val:
            try:
                d_obj = datetime.strptime(input_date_val, '%d/%m/%Y')
                input_date_val = d_obj.strftime('%Y-%m-%d')
            except Exception:
                pass

    supplier_i18n_list = {
        s.id: load_supplier_translated(s, language)
        for s in all_suppliers
    }

    return render_template(
        'supplier.html',
        supplier=selected_supplier,
        all_suppliers=all_suppliers,
        supplier_i18n=supplier_i18n,
        supplier_i18n_list=supplier_i18n_list,
        today=today_str,
        input_date_val=input_date_val
    )


@app.route('/api/supplier/<int:supplier_id>')
@login_required
def api_get_supplier(supplier_id):
    try:
        language = get_lang()
        s = Supplier.query.get(supplier_id)

        if not s:
            return jsonify({"error": "Supplier not found"}), 404

        formatted_date_for_picker = ""
        if s.date:
            try:
                temp_date = datetime.strptime(s.date, '%d/%m/%Y')
                formatted_date_for_picker = temp_date.strftime('%Y-%m-%d')
            except Exception:
                formatted_date_for_picker = s.date

        translated = load_supplier_translated(s, language)

        if not translated:
            translated = {
                "name": s.supplier_name or "",
                "address": s.address or "",
                "city": s.city or "",
                "postal_code": s.postal_code or "",
                "notes": s.notes or ""
            }

        return jsonify({
            "supplier_name": translated.get("name", s.supplier_name or ""),
            "supplier_number": s.supplier_number or "",
            "address": translated.get("address", s.address or ""),
            "city": translated.get("city", s.city or ""),
            "postal_code": translated.get("postal_code", s.postal_code or ""),
            "phone": s.phone or "",
            "email": s.email or "",
            "payment_terms": s.payment_terms or "",
            "notes": translated.get("notes", s.notes or ""),
            "date": formatted_date_for_picker,
            "is_active": s.is_active,
            "role": s.role or "supplier"
        })

    except Exception as e:
        print(f"❌ API Supplier Error: {e}")
        return jsonify({"error": "Internal server error", "details": str(e)}), 500



@app.route('/supplier_data', methods=['GET'])
@login_required
def supplier_data():
    language = get_lang()
    raw_suppliers = Supplier.query.all()

    suppliers_list = []
    supplier_i18n_list = {}

    for s in raw_suppliers:
        trans = load_supplier_translated(s, language)

        # 1. Build a clean, normalized payload array matching your standard data grids
        suppliers_list.append({
            "id": s.id,
            "date": s.date or "",
            "supplier_name": trans.get("name", s.supplier_name or ""),
            "supplier_number": s.supplier_number or "",
            "address": trans.get("address", s.address or ""),
            "city": trans.get("city", s.city or ""),
            "postal_code": trans.get("postal_code", s.postal_code or ""),
            "phone": s.phone or "",
            "email": s.email or "",
            "payment_terms": s.payment_terms or "",
            "notes": trans.get("notes", s.notes or "")
        })

        # 2. Re-populate your structural i18n map dictionary to prevent template loop failures
        supplier_i18n_list[s.id] = trans

    #  ALIGNED & RESTORED: Passes uniform dictionary structures to satisfy the HTML loops completely
    return render_template(
        'supplier_data.html', 
        suppliers=suppliers_list,
        supplier_i18n_list=supplier_i18n_list
    )

# ----------------------
#   Search And Clear Supplier Form
# ----------------------

@app.route('/search_supplier', methods=['GET', 'POST'])
@login_required
def search_supplier():
    language = get_lang()

    # Capture the search payload string securely from both POST and GET channels
    search_name = request.form.get('search_supplier') if request.method == 'POST' else request.args.get('search_supplier')

    search_results = []
    supplier = None

    # Baseline fallback template initialization variables
    today_str = datetime.today().strftime('%Y-%m-%d')
    input_date_val = today_str

    if search_name:
        search_name = search_name.strip()

        # Perform optimized pattern search on primary naming values
        search_results = Supplier.query.filter(
            Supplier.supplier_name.ilike(f'%{search_name}%')
        ).all()

        if search_results:
            # Anchor to the first matching supplier row entry found
            supplier = search_results[0]

            #   Extract date translation into an isolated variable to safeguard master database schemas
            if supplier.date:
                input_date_val = supplier.date
                try:
                    if "/" in input_date_val:
                        # Safely convert from DB layout (DD/MM/YYYY) to HTML structure (YYYY-MM-DD)
                        parts = input_date_val.split("/")
                        if len(parts) == 3:
                            input_date_val = f"{parts[2]}-{parts[1]}-{parts[0]}"
                    else:
                        datetime.strptime(input_date_val, "%Y-%m-%d")
                except Exception:
                    input_date_val = today_str  # Clean validation fallback

    all_suppliers = Supplier.query.order_by(Supplier.supplier_name).all()

    # Load multi-lingual localization translation assets
    supplier_i18n = load_supplier_translated(supplier, language) if supplier else {}
    supplier_i18n_list = {
        s.id: load_supplier_translated(s, language)
        for s in all_suppliers
    }

    #  ALIGNED: Passes identical layout variable properties matching the core dashboard loops
    return render_template(
        'supplier.html',
        suppliers=search_results,         # Passes the targeted query results matrix array
        all_suppliers=all_suppliers,       # Keeps global supplier dropdown options intact
        supplier=supplier,                 # Passes the resolved supplier entry model
        supplier_i18n=supplier_i18n,       # Serves localized values for form edit field bindings
        supplier_i18n_list=supplier_i18n_list,
        today=today_str,
        input_date_val=input_date_val     # Feeds clean date string to the HTML front picker
    )

# ----------------------
#   Clear Supplier Search Form
# ----------------------

@app.route('/clear_search_results_supplier', methods=['POST'])
@login_required
def clear_search_results_supplier():
    language = get_lang()

    # Baseline fallback template initialization variables
    today_str = datetime.today().strftime('%Y-%m-%d')

    all_suppliers = Supplier.query.order_by(Supplier.supplier_name).all()

    # Reload localization translation maps to ensure interface uniformity on view clearing
    supplier_i18n_list = {
        s.id: load_supplier_translated(s, language)
        for s in all_suppliers
    }

    return render_template(
        'supplier.html',
        suppliers=[],                      # Resets search data arrays
        all_suppliers=all_suppliers,
        supplier=None,           
        supplier_i18n={},        
        supplier_i18n_list=supplier_i18n_list,
        today=today_str,
        input_date_val=today_str           # Returns date selector element back to system baseline default
    )


# ----------------------
# Profit And Lost
# ----------------------

@app.route('/profit')
@login_required
def profit():
    language = get_lang()   
    search = request.args.get("q", "").strip().lower()
    selected_month = request.args.get("month", "")
    selected_year = request.args.get("year", "")

    if not selected_year: 
        selected_year = str(datetime.today().year)
    if not selected_month: 
        selected_month = datetime.today().strftime('%m')

    #  אופטימיזציה קריטית (Eager Loading): מונע את בעיית N+1 Queries ומאיץ את המסך פי 50 ב-Render
    all_customers = Customer.query.options(
        db.joinedload(Customer.invoices).joinedload(Invoice.items)
    ).all()
    
    all_transactions = Transaction.query.all()
    
    # טעינה של מפת קטגוריות מתורגמות מה-JSON
    business_categories = {}
    cat_dir = app.config.get('CATEGORIES_DIR')
    if cat_dir and os.path.exists(cat_dir):
        for cat_id in os.listdir(cat_dir):
            data = load_category_file(cat_id)
            if data and "name" in data:
                business_categories[str(cat_id)] = data["name"].get(language) or data["name"].get("he") or cat_id

    total_revenue = 0.0
    total_expenses = 0.0
    total_cogs = 0.0 
    total_manual_income = 0.0  
    total_vat = 0.0          
    
    customer_totals = {}
    customer_i18n_list = {}
    product_i18n_list = {}  # מילון חדש לשמירת תרגומי המוצרים/פריטים דינמית
    filtered_customers = []
    
    manual_incomes_list = []
    expenses_list = []
    trans_i18n_list = {}

    # 1. PROCESS INVOICES (כולל הכנסות משכירות נכס, שירותים ומוצרים)
    for customer in all_customers:
        # טעינת תרגום הלקוח לשפת המערכת הנוכחית
        customer_i18n_list[customer.id] = load_customer_translated(customer, language)
        cust_revenue = 0.0
        
        for inv in customer.invoices:
            if inv.status == "canceled": 
                continue #  מנטרל חשבוניות מבוטלות אוטומטית לפי תנאי ה-QA החשבונאי המלא
                
            inv_month = inv.invoice_date.strftime('%m')
            inv_year = str(inv.invoice_date.year)
            
            if (not selected_month or inv_month == selected_month) and \
               (not selected_year or inv_year == selected_year):
                
                # סכימת ההכנסה נטו (sub_total) כדי למנוע ניפוח של המע"מ בדוח רווח והפסד
                cust_revenue += float(inv.sub_total or 0.0)
                
                # סכימת המע"מ לקוביית המס הירוקה החדשה על המסך
                total_vat += float(inv.vat_amount or 0.0)
                
                # חישוב עלות המכר (COGS) לכל פריט בחשבונית
                for item in inv.items:
                    # טעינה בטוחה: רק אם המוצר קיים והוא אובייקט תקין ב-DB
                    if item.product and item.product_id and item.product_id not in product_i18n_list:
                        product_i18n_list[item.product_id] = load_item_translated(item.product, language)

                    item_cost = float(getattr(item, 'cost_price_at_time', 0.0) or 0.0)
                    if item_cost == 0.0:
                        prod = Product.query.get(item.product_id)
                        if prod and getattr(prod, 'income_category', 'service') == 'product':
                            item_cost = float(prod.cost_price or 0.0)
                    total_cogs += (item_cost * float(item.quantity or 0.0))
        
        # מנגנון החיפוש החכם
        trans_name = (customer_i18n_list[customer.id].get('name', '') or "").lower()
        match_search = not search or (search in customer.customer_name.lower() or search in trans_name)
        
        if match_search and (cust_revenue > 0.0 or not search):
            customer_totals[customer.id] = cust_revenue
            total_revenue += cust_revenue
            filtered_customers.append(customer)

    # 2. PROCESS TRANSACTIONS (הכנסות ידניות כמו מניות והוצאות תפעוליות)
    for trans in all_transactions:
        trans_month = trans.date.strftime('%m')
        trans_year = str(trans.date.year)

        if (not selected_month or trans_month == selected_month) and \
           (not selected_year or trans_year == selected_year):
            
            # טעינת התרגום של תיאור התנועה מה-JSON
            t_file = load_transaction_file(trans.id)
            desc_obj = t_file.get("description", {}) if t_file else {}
            trans_i18n_list[trans.id] = desc_obj.get(language) or desc_obj.get("he") or trans.description

            # הוצאות עסק קלאסיות
            if trans.type == 'expense':
                total_expenses += float(trans.amount or 0.0)
                expenses_list.append(trans)
                
            # הכנסות ידניות
            elif trans.type == 'income':
                if not trans.invoice_id:  # סינון קריטי: לוקח רק הכנסות ידניות (כמו מניות) ולא חשבוניות כפולות
                    total_manual_income += float(trans.amount or 0.0)
                    total_revenue += float(trans.amount or 0.0)
                    total_cogs += float(getattr(trans, 'cost_price_at_time', 0.0) or 0.0)
                    manual_incomes_list.append(trans)

    # החישוב החשבונאי הסופי והנקי: הכנסות נטו פחות הוצאות ופחות עלות המכר
    net_profit = total_revenue - total_expenses - total_cogs

    months_list = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
    years_list = [str(y) for y in range(2024, 2031)]

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
        total_vat=total_vat, 
        customer_i18n_list=customer_i18n_list,
        product_i18n_list=product_i18n_list,  
        selected_month=selected_month,
        selected_year=selected_year,
        search=search,
        months=months_list,
        years=years_list,
        language=language,
        company=load_company_data() 
    )


# ----------------------
# All Transaction Route
# ----------------------

@app.route('/transactions')
@login_required
def transactions():
    try:
        language = get_lang()

        # --- URL Parameters ---
        search = request.args.get("q", "").strip().lower()
        selected_month = request.args.get("month", "")
        selected_year = request.args.get("year", "")

        # Default to current month/year if not provided to secure data grid rendering stability
        if not selected_year:
            selected_year = str(datetime.today().year)
        if not selected_month:
            selected_month = datetime.today().strftime('%m')

        # --- Fetch all transactions ---
        all_transactions = Transaction.query.order_by(Transaction.date.desc()).all()
        today_str = datetime.today().strftime('%Y-%m-%d')

        # --- Load Expense Categories (Static/Custom) ---
        business_categories = {}
        cat_dir = app.config.get('CATEGORIES_DIR')
        if cat_dir and os.path.exists(cat_dir):
            for cat_id in os.listdir(cat_dir):
                data = load_category_file(cat_id)
                if data and "name" in data:
                    business_categories[str(cat_id)] = (
                        data["name"].get(language)
                        or data["name"].get("he")
                        or cat_id
                    )

        filtered_transactions = []
        trans_i18n_list = {}
        costs_at_time = {} 

        is_numeric_search = search.replace(".", "", 1).isdigit()

        for t in all_transactions:
            # --- Translated Description Extraction Loop ---
            trans_file = load_transaction_file(t.id)
            desc_obj = trans_file.get("description", {}) if trans_file else {}
            translated_desc = desc_obj.get(language) or desc_obj.get("he") or t.description or ""

            # --- Date Matching Scope ---
            t_month = t.date.strftime('%m')
            t_year = str(t.date.year)

            match_month = not selected_month or t_month == selected_month
            match_year = not selected_year or t_year == selected_year

            # --- Pattern Matching Smart Search Search Engine Logic ---
            if not search:
                match_search = True
            else:
                raw_desc = (t.description or "").lower()
                trans_desc_lower = translated_desc.lower()
                cat_name = business_categories.get(str(t.category_id), "").lower()
                amount_str = str(t.amount)
                date_str = t.date.strftime("%d/%m/%Y")

                if is_numeric_search:
                    match_search = (search == amount_str)
                else:
                    match_search = (
                        search in raw_desc or
                        search in trans_desc_lower or
                        search in cat_name or
                        search in amount_str or
                        search in date_str
                    )

            # Assemble compiled matrix arrays for display grid loops
            if match_month and match_year and match_search:
                filtered_transactions.append(t)
                trans_i18n_list[t.id] = translated_desc
                costs_at_time[t.id] = getattr(t, 'cost_price_at_time', 0.0) or 0.0

        # --- Lists for HTML filter drop selection tags ---
        months_list = ["01","02","03","04","05","06","07","08","09","10","11","12"]
        years_list = [str(y) for y in range(2024, 2031)]

        return render_template(
            'transactions.html',
            transactions=filtered_transactions,
            trans_i18n_list=trans_i18n_list,
            costs_at_time=costs_at_time, 
            business_categories=business_categories,
            search=search,
            selected_month=selected_month,
            selected_year=selected_year,
            months=months_list,
            years=years_list,
            today=today_str
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"❌ Critical Exception caught inside transactions index route handler: {e}")
        return f"Error: {e}", 500


@app.route('/transaction/add', methods=['POST'])
@login_required
def add_transaction():
    try:        
        date_str = request.form.get('date')
        trans_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else datetime.today().date()
        
        raw_amount = clean_float(request.form.get('amount', '0'))
        description = request.form.get('description', '')
        trans_type = request.form.get('type')  # 'income' or 'expense'
        category_id = request.form.get('category')
        
        # --- Activity Type & P&L Cost Tracking Configurations ---
        cost_price = 0.0
        income_cat = 'service'

        if trans_type == 'income':
            p_id = request.form.get('product_id')
            if p_id:
                product = Product.query.get(p_id)
                if product:
                    income_cat = getattr(product, 'income_category', 'service')
                    if income_cat == 'product':
                        cost_price = float(product.cost_price or 0.0)
        
        # Secure uploaded attachments
        filename = None
        if 'attachment' in request.files:
            file = request.files['attachment']
            if file and file.filename != '':
                filename = secure_filename(f"{int(time.time())}_{file.filename}")
                file.save(os.path.join(app.config.get('UPLOAD_FOLDER'), filename))

        # DB Session Persistent Commit Execution Path
        new_trans = Transaction(
            date=trans_date,
            description=description,
            amount=raw_amount,
            type=trans_type,
            category_id=category_id,
            attachment_path=filename,
            cost_price_at_time=cost_price,
            user_id=current_user.id
        )
        db.session.add(new_trans)
        db.session.commit()

        #  REPLACED CELERY WITH THREADING
        current_curr = get_currency() if 'get_currency' in globals() else 'ILS'

        translate_transaction_in_background(
            transaction_id=new_trans.id,
            description=description,
            amount=raw_amount,
            type_trans=trans_type,
            category_id=category_id,
            currency_code=current_curr,
            cost_price=cost_price,
            income_category=income_cat
        )

        flash('התנועה נוספה בהצלחה!', 'success')

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        print(f"❌ Error encountered inside add transaction POST execution loop: {e}")
        flash(f'שגיאה בשמירה: {e}', 'danger')
    
    return redirect(url_for('transactions'))


@app.route('/transaction/delete/<int:id>', methods=['POST'])
@login_required
def delete_transaction(id):
    try:
        trans = Transaction.query.get(id)
        if trans:
            #  Enforce ledger reference guardrail to isolate and protect active invoice trails
            if trans.invoice_id:
                flash('לא ניתן למחוק תנועה הקשורה לחשבונית. יש למחוק או לבטל את החשבונית עצמה.', 'danger')
                return redirect(url_for('transactions'))

            # 1. Safely remove physical receipt files from the upload mount
            if trans.attachment_path:
                file_path = os.path.join(app.config.get('UPLOAD_FOLDER'), trans.attachment_path)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        print(f"⚠️ Warning: Could not delete upload file block: {e}")

            # 2. Purge background localization file directory structures cleanly from disk
            trans_json_path = os.path.join(app.config.get('TRANSACTIONS_DIR'), str(id))
            if os.path.exists(trans_json_path):
                import shutil
                try:
                    shutil.rmtree(trans_json_path)
                except Exception as e:
                    print(f"⚠️ Warning: Could not purge translation folder trail: {e}")

            # 3. Commit row eviction directly on your PostgreSQL transaction context
            db.session.delete(trans)
            db.session.commit()
            
            flash('התנועה וכל הקבצים הקשורים אליה נמחקו בהצלחה', 'success')
        else:
            flash('התנועה לא נמצאה', 'warning')
            
    except Exception as e:
        db.session.rollback()
        print(f"❌ Transaction Excision Error: {e}")
        flash(f'שגיאה בתהליך המחיקה: {e}', 'danger')
    
    return redirect(url_for('transactions'))


# --------------------
#  Transactions List API (JSON)
# ----------------------

@app.get("/api/transactions_list")
@login_required
def transactions_list():
    language = get_lang()
    transactions = Transaction.query.order_by(Transaction.date.desc()).all()
    
    cat_map = {}
    cat_dir = app.config.get('CATEGORIES_DIR')
    if cat_dir and os.path.exists(cat_dir):
        for cat_id in os.listdir(cat_dir):
            data = load_category_file(cat_id)
            if data and "name" in data:
                names = data.get("name", {})
                cat_map[str(cat_id)] = names.get(language) or names.get('he') or f"Cat {cat_id}"

    result = []
    for t in transactions:
        trans_file = load_transaction_file(t.id)
        
        if trans_file and "description" in trans_file:
            desc_dict = trans_file["description"]
        else:
            desc_dict = {"he": t.description or ""}
        
        if isinstance(desc_dict, dict):
            p_desc = desc_dict.get(language) or desc_dict.get("he") or t.description or ""
        else:
            p_desc = t.description or ""
        
        cat_id_str = str(t.category_id) if t.category_id else None
        
        if cat_id_str and cat_id_str in cat_map:
            translated_cat = cat_map[cat_id_str]
        else:
            translated_cat = getattr(t, 'category', 'General') or 'General'
        
        result.append({
            "id": t.id,
            "date": t.date.strftime('%Y-%m-%d') if t.date else "",
            "description": p_desc,            
            "amount": float(t.amount or 0.0),
            "type": t.type,                  
                      "category_id": t.category_id,
            "category_display": translated_cat, 
            "attachment": t.attachment_path or "",
            "invoice_id": t.invoice_id,
            "cost_price": float(getattr(t, 'cost_price_at_time', 0.0) or 0.0) 
        })
        
    return jsonify(result)


# -----------------------------------------------------------
#  Page Categories All 
# -----------------------------------------------------------

@app.route('/categories', methods=['GET', 'POST'])
@login_required
def categories():
    try:
        language = get_lang()
        
        db_categories = Category.query.all()
        all_categories = []
        
        for cat in db_categories:
            data = load_category_file(cat.id)
            if data and "name" in data:
                names_dict = data.get("name", {})
                translated_name = names_dict.get(language) or names_dict.get('he') or cat.name or "Unknown"
            else:
                translated_name = cat.name or "Unknown"
                
            all_categories.append({
                "id": str(cat.id), 
                "name": translated_name
            })

        all_categories.sort(key=lambda x: x['name'])

        return render_template('categories.html', 
                               categories=all_categories, 
                               language=language)
    except Exception as e:
        print(f"❌ Error encountered inside custom categories view router: {e}")
        return redirect(url_for('home'))


@app.route('/category/add', methods=['POST'])
@login_required
def add_custom_category():
    category_name = request.form.get("new_category", "").strip()
    if not category_name:
        return redirect(url_for('categories'))

    new_cat = Category(name=category_name)
    db.session.add(new_cat)
    db.session.commit()

    #  REPLACED CELERY WITH THREADING
    translate_category_in_background(
        cat_id=new_cat.id,
        raw_name_text=category_name
    )

    flash('הקטגוריה נוספה בהצלחה! תהליך התרגום רץ ברקע.', 'success')
    return redirect(url_for('categories'))


@app.route('/category/delete/<int:cat_id>', methods=['POST'])
@login_required
def delete_custom_category(cat_id):
    try:
        cat = Category.query.get(cat_id)
        if cat:
            db.session.delete(cat)
            db.session.commit()

        import shutil
        cat_path = os.path.join(app.config.get('CATEGORIES_DIR'), str(cat_id))
        if os.path.exists(cat_path):
            try:
                shutil.rmtree(cat_path)
            except Exception as e:
                print(f"⚠️ Warning: Could not purge file system nodes for category folder: {e}")
                
        flash('הקטגוריה נמחקה בהצלחה', 'success')
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error encountered during custom category deletion routine: {e}")
        flash('שגיאה בתהליך מחיקת הקטגוריה', 'danger')
        
    return redirect(url_for('categories'))


# --------------------
#  Categories List API (JSON)
# ----------------------

@app.route('/api/categories_list')
@login_required
def categories_list():
    lang = get_lang()
    
    db_categories = Category.query.all()
    result = []
    
    for cat in db_categories:
        data = load_category_file(cat.id)
        if data and "name" in data:
            names_dict = data.get("name", {})
            translated_name = names_dict.get(lang) or names_dict.get('he') or cat.name or "Unknown"
        else:
            translated_name = cat.name or "Unknown"
            
        result.append({
            "id": str(cat.id), 
            "name": translated_name
        })
                
    return jsonify(result)


# -----------------------------------------------------------
#  Supplier Payment All Option Sync & Api Live Run
# -----------------------------------------------------------

@app.route('/payment')
@login_required
def payment():
    all_suppliers = Supplier.query.order_by(Supplier.supplier_name).all()

    return render_template(
        "payment.html",
        all_suppliers=all_suppliers
    )


@app.route('/api/payment_page_submit', methods=['POST'])
@login_required
def payment_page_submit():
    try:
        data = request.get_json() or {}

        supplier = data.get("supplier_payment", {})
        invoice = data.get("invoice_payment", {})
        authority = data.get("authority_payment", {})

        # ----- שמירה: תשלום לספק -----
        if supplier:
            save_supplier_payment(
                supplier_id=supplier.get("supplier_id"),
                supplier_name=supplier.get("supplier_name"),
                supplier_number=supplier.get("supplier_number"),
                amount=supplier.get("supplier_amount"),
                description=supplier.get("supplier_description"),
                reference=supplier.get("supplier_reference")
            )

        # ----- שמירה: תשלום חשבונית -----
        if invoice:
            save_invoice_payment(
                invoice_number=invoice.get("invoice_number"),
                customer=invoice.get("invoice_customer"),
                amount=invoice.get("invoice_amount"),
                description=invoice.get("invoice_description")
            )

        # ----- שמירה: תשלום לרשויות -----
        if authority:
            save_authority_payment(authority)

        return jsonify({
            "status": "ok",
            "message": "Payment page submitted successfully"
        })

    except Exception as e:
        print("❌ ERROR in payment_page_submit:", e)
        return jsonify({"status": "error", "message": str(e)}), 500


# -----------------------------------------------------------
#  Add Purchase & Api Live Run
# -----------------------------------------------------------

@app.route('/api/purchase', methods=['POST'])
@login_required
def add_purchase():
    data = request.json

    supplier_id = data.get("supplier_id")
    product_id = data.get("product_id")
    quantity = float(data.get("quantity", 0))
    cost_price = float(data.get("cost_price", 0))
    reference = data.get("reference", "")
    notes = data.get("notes", "")
    date = datetime.today().strftime("%d/%m/%Y")

    total = quantity * cost_price

    purchase = SupplierPurchase(
        supplier_id=supplier_id,
        product_id=product_id,
        quantity=quantity,
        cost_price=cost_price,
        total=total,
        reference=reference,
        notes=notes,
        date=date
    )

    db.session.add(purchase)

    # עדכון מלאי
    product = Product.query.get(product_id)
    if product:
        product.quantity += quantity
        product.cost_price = cost_price

    db.session.commit()

    return jsonify({"status": "success"})


@app.route('/api/suppliers_list')
@login_required
def suppliers_list():
    suppliers = Supplier.query.order_by(Supplier.supplier_name).all()
    return jsonify([
        {"id": s.id, "supplier_name": s.supplier_name}
        for s in suppliers
    ])


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
