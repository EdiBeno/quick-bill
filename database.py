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

    invoice_number = db.Column(db.Integer, unique=True, nullable=False)
    allocation_number = db.Column(db.String(50), unique=True, nullable=True)
    invoice_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(20), default="active")

    sub_total = db.Column(db.Float, nullable=False)       
    vat_rate = db.Column(db.Float, default=0)             
    vat_amount = db.Column(db.Float, nullable=False)      
    grand_total = db.Column(db.Float, nullable=False)     

    is_sent_to_tax = db.Column(db.Boolean, default=False)   
    is_paid = db.Column(db.Boolean, default=False)          

    payment_transaction_id = db.Column(db.String(100), nullable=True)
    payment_date = db.Column(db.DateTime, nullable=True)
    payments = db.relationship('Payment', backref='invoice', lazy=True)

    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)

    customer = db.relationship('Customer', back_populates='invoices')
    items = db.relationship('InvoiceItem', back_populates='invoice', cascade="all, delete-orphan")
    transactions = db.relationship('Transaction', backref='invoice', lazy=True)

    # הוספה בתוך class Invoice:
    @property
    def total_cost(self):
        """מחשב את עלות המכר הכוללת של החשבונית מתוך הפריטים"""
        return sum(item.cost_price_at_time * item.quantity for item in self.items)

    @property
    def net_profit(self):
        """מחשב רווח נקי לחשבונית (ללא מע"מ)"""
        return self.sub_total - self.total_cost

    def __repr__(self):
        return f'<Invoice number={self.invoice_number} allocation={self.allocation_number}>'

# -----------------------------
#  Invoice Items  
# -----------------------------

class InvoiceItem(db.Model):
    __tablename__ = 'invoice_item'

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    
    description = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    discount = db.Column(db.Float, default=0)   

    cost_price_at_time = db.Column(db.Float, nullable=False, default=0.0)

    income_category = db.Column(db.String(50), nullable=False, default='service')

    invoice = db.relationship('Invoice', back_populates='items')
    product = db.relationship('Product')

    def __repr__(self):
        return f'<InvoiceItem Product:{self.product_id} Type:{self.income_category} Qty:{self.quantity}>'

# -----------------------------
#  Product All  
# -----------------------------

class Product(db.Model):
    __tablename__ = 'product'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)        
    cost_price = db.Column(db.Float, default=0.0)      
    
    income_category = db.Column(db.String(50), default='service') 
    
    received_date = db.Column(db.String(20), nullable=True) 

    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    sku = db.Column(db.String(50), nullable=True)
    description = db.Column(db.String(255), nullable=True)

    quantity = db.Column(db.Float, default=0.0) 
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    def to_dict(self):
        # המרת תאריך מפורמט DB (DD/MM/YYYY) לפורמט HTML (YYYY-MM-DD) עבור ה-fillForm
        p_date = self.received_date
        if p_date and "/" in p_date:
            try:
                p_date = datetime.strptime(p_date, '%d/%m/%Y').strftime('%Y-%m-%d')
            except:
                pass

        return {
            'id': self.id,
            'sku': self.id, 
            'name': self.name,
            'price': self.price,
            'cost_price': self.cost_price,
            'income_category': self.income_category,
            'received_date': p_date, 
            'quantity': self.quantity,  
            'category_id': self.category_id,
            'description': self.description
        }

    def __repr__(self):
        return f'<Product {self.name} Price:{self.price} Type:{self.income_category} Date:{self.received_date} Stock:{self.quantity}>'

# -----------------------------------------------------------
#  Category Model (The List for your Combobox)
# -----------------------------------------------------------

class Category(db.Model):
    __tablename__ = 'categories'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    
    products = db.relationship('Product', backref='category_ref', lazy=True)
    transactions = db.relationship('Transaction', backref='category_ref', lazy=True)

    def __repr__(self):
        return f'<Category {self.name}>'

# -----------------------------------------------------------
#  Transaction Model (Income & Expense)
# -----------------------------------------------------------

class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    
    type = db.Column(db.String(10), nullable=False)     # 'income' / 'expense'
    amount = db.Column(db.Float, nullable=False)        
    
    description = db.Column(db.String(255), nullable=False)
    attachment_path = db.Column(db.String(255), nullable=True) 
    
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    
    cost_price_at_time = db.Column(db.Float, nullable=True, default=0.0)
    quantity = db.Column(db.Integer, default=1)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'amount': self.amount,
            'description': self.description,
            'date': self.date.isoformat() if self.date else None,
            'category_id': self.category_id,
            'customer_id': self.customer_id,
            'attachment_path': self.attachment_path, 
            'invoice_id': self.invoice_id,
            'cost_price_at_time': self.cost_price_at_time
        }

    def __repr__(self):
        return f'<Transaction ID={self.id} Type={self.type} Amount={self.amount} Inv={self.invoice_id}>'

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

# -----------------------------
#  Supplier Form Data
# -----------------------------

class Supplier(db.Model):
    __tablename__ = 'supplier'

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    user = db.relationship('User', backref='supplier_profile')

    supplier_name = db.Column(db.String(100), nullable=False)
    supplier_number = db.Column(db.String(50))
    date = db.Column(db.String(10))

    address = db.Column(db.String(100))
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(20))

    phone = db.Column(db.String(20))
    email = db.Column(db.String(100))

    payment_terms = db.Column(db.String(50))
    notes = db.Column(db.Text)

    new_field_name = db.Column(db.String(50))
    value = db.Column(db.String(100))
    row_data = db.Column(db.JSON, default={})

    # תפקיד
    role = db.Column(db.String(50), nullable=False, default='supplier')

    # סטטוס
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    purchases = db.relationship('SupplierPurchase', back_populates='supplier', lazy=True)

# -----------------------------
#  Supplier Purchase
# -----------------------------

class SupplierPurchase(db.Model):
    __tablename__ = 'supplier_purchase'

    id = db.Column(db.Integer, primary_key=True)

    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)

    date = db.Column(db.String(10))
    quantity = db.Column(db.Float, nullable=False)
    cost_price = db.Column(db.Float, nullable=False)
    total = db.Column(db.Float, nullable=False)
    reference = db.Column(db.String(100))
    notes = db.Column(db.Text)

    supplier = db.relationship('Supplier', back_populates='purchases')
    product = db.relationship('Product', backref='supplier_purchases')

    pass
    
