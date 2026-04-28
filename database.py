from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy import Column, Integer, String, Text, ForeignKey, Date, DateTime, Boolean, Float
from sqlalchemy.orm import relationship
from datetime import time, datetime, timedelta, timezone
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# -----------------------------
#  Owner Helper Class (Memory Only)
# -----------------------------
class OwnerUser(UserMixin):
    """ Helper for owner login since owner is in .env, not DB """
    def __init__(self, email):
        self.id = 0
        self.email = email
        self.username = email
        self.role = 'owner'
    
    def get_id(self):
        return "0"

# -----------------------------
#  Regular User (SQL Table)
# -----------------------------
class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(80), nullable=True)
    password_hash = db.Column(db.String(256), nullable=True) 

    # Roles: customer / manager
    role = db.Column(db.String(50), nullable=False, default='customer')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    is_active = db.Column(db.Boolean, default=True)
    access_expires_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def has_valid_access(self):
        if not self.is_active:
            return False
        if self.access_expires_at is None:
            return True
        return datetime.utcnow() < self.access_expires_at

    def seconds_left(self):
        if self.access_expires_at is None:
            return None
        diff = (self.access_expires_at - datetime.utcnow()).total_seconds()
        return max(0, int(diff))

    def get_id(self):
        return str(self.id)

# -----------------------------
#  Token Store for Clients
# -----------------------------
class PasswordResetToken(db.Model):
    __tablename__ = 'password_reset_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token = db.Column(db.String(128), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
#  Bank Account  
# -----------------------------

class BankAccount(db.Model):
    __tablename__ = 'bank_account'

    id = db.Column(db.Integer, primary_key=True)
    bank_code = db.Column(db.String(10), nullable=False)
    branch_code = db.Column(db.String(10), nullable=False)
    account_number = db.Column(db.String(20), nullable=False)
    
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)

    customer = db.relationship("Customer", back_populates="bank_account")
      
    # Optional: For better security, you should encrypt sensitive data
    # encrypted_account_number = db.Column(db.String(256)) 

# -----------------------------
#  Payment OPTION  
# -----------------------------

class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'))
    payment_date = db.Column(db.Date)
    payment_method = db.Column(db.String(50))
    bank = db.Column(db.String(50))
    branch = db.Column(db.String(20))
    account_number = db.Column(db.String(50))
    payment_amount = db.Column(db.Numeric(10,2))

# -----------------------------
#  Invoice All  
# -----------------------------

class Invoice(db.Model):
    __tablename__ = 'invoice'

    id = db.Column(db.Integer, primary_key=True)

    # מספר חשבונית פנימי (1,2,3...)
    invoice_number = db.Column(db.Integer, unique=True, nullable=False)

    # מספר הקצאה מרשות המיסים (רק מעל 25,000)
    allocation_number = db.Column(db.String(50), unique=True, nullable=True)

    # תאריך החשבונית
    invoice_date = db.Column(db.Date, nullable=False)

    # מצב ביטול חשבונית
    status = db.Column(db.String(20), default="active")

    # סכומים (תואם ל‑HTML ול‑JS)
    sub_total = db.Column(db.Float, nullable=False)       # סה״כ לפני מע״מ
    vat_rate = db.Column(db.Float, default=0)             # אחוז המע״מ
    vat_amount = db.Column(db.Float, nullable=False)      # סכום המע״מ
    grand_total = db.Column(db.Float, nullable=False)     # סה״כ לתשלום

    # סטטוסים
    is_sent_to_tax = db.Column(db.Boolean, default=False)   # האם נשלח לרשות המיסים
    is_paid = db.Column(db.Boolean, default=False)          # האם הלקוח שילם

    # פרטי תשלום
    payment_transaction_id = db.Column(db.String(100), nullable=True)
    payment_date = db.Column(db.DateTime, nullable=True)
    payments = db.relationship('Payment', backref='invoice', lazy=True)

    # קישור ללקוח
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)

    # קשר לטבלת הלקוחות
    customer = db.relationship('Customer', back_populates='invoices')

    # קשר לטבלת פריטים
    items = db.relationship('InvoiceItem', back_populates='invoice', cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Invoice number={self.invoice_number} allocation={self.allocation_number}>'

# -----------------------------
#  Invoice Items  
# -----------------------------

class InvoiceItem(db.Model):
    __tablename__ = 'invoice_item'

    id = db.Column(db.Integer, primary_key=True)

    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)

    product_id = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255), nullable=True)

    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    discount = db.Column(db.Float, default=0)   

    invoice = db.relationship('Invoice', back_populates='items')

    def __repr__(self):
        return f'<InvoiceItem {self.product_id} x {self.quantity}>'

# -----------------------------
#  Product All  
# -----------------------------

class Product(db.Model):
    __tablename__ = 'product'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)
    description = db.Column(db.String(255))
    
    def __repr__(self):
        return f'<Product {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'price': self.price,
            'description': self.description
        }

# -----------------------------
#  Customer Form Data
# -----------------------------
class Customer(db.Model):
    __tablename__ = 'customer' 
    
    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    user = db.relationship('User', backref='customer_profile')

    customer_name = db.Column(db.String(100), nullable=False)
    customerMonth = db.Column(db.String(2))
    customerYear = db.Column(db.String(4))
    date = db.Column(db.String(10))
    id_number = db.Column(db.String(100))
    address = db.Column(db.String(100))
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(20))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(100))

    start_date = db.Column(db.String(10))
    bank_number = db.Column(db.String(20))
    branch_number = db.Column(db.String(20))
    account_number = db.Column(db.String(20))
    message = db.Column(db.Text)
    contract_status = db.Column(db.String(20))

    new_field_name = db.Column(db.String(50))
    value = db.Column(db.String(100))  
    row_data = db.Column(db.JSON, default={})

    role = db.Column(db.String(50), nullable=False, default='customer')

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    invoices = db.relationship('Invoice', back_populates='customer')
    bank_account = db.relationship("BankAccount", back_populates="customer", uselist=False)
    
    pass
    
