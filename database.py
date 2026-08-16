from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy import Column, Integer, String, Text, ForeignKey, Date, DateTime, Boolean, Float, Numeric
from sqlalchemy.orm import relationship
from datetime import time, datetime, timedelta, timezone

db = SQLAlchemy()

OWNER_COMPANY_ID = 1

# -----------------------------
#  Owner Helper Class (Memory Only)
# -----------------------------
class OwnerUser(UserMixin):
    def __init__(self, email):
        self.id = 0
        self.email = email
        self.username = email
        self.role = 'owner'
        self.company_id = OWNER_COMPANY_ID  

    def get_id(self):
        return "0"

# -----------------------------
#  Company Profile
# -----------------------------
class Company(db.Model):
    __tablename__ = 'company'
    
    id                = db.Column(db.Integer, primary_key=True)
    name              = db.Column(db.String(255), default='')
    company_id_number = db.Column(db.String(100), default='')
    deduction_file    = db.Column(db.String(100), default='')
    address           = db.Column(db.String(255), default='')
    city              = db.Column(db.String(100), default='')
    postal_code       = db.Column(db.String(100), default='')
    phone             = db.Column(db.String(100), default='')
    email             = db.Column(db.String(255), default='')  
    logo              = db.Column(db.Text, default='')
    translations_json = db.Column(db.Text, default='{}')

    users = db.relationship('User', backref='company', lazy=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name or "",
            "company_id_number": self.company_id_number or "",
            "deduction_file": self.deduction_file or "",
            "address": self.address or "",
            "city": self.city or "",
            "postal_code": self.postal_code or "",
            "phone": self.phone or "",
            "email": self.email or "",
            "logo": self.logo or "",
            "translations_json": self.translations_json or "{}"
        }


# -----------------------------
#  Regular User 
# -----------------------------
class User(db.Model, UserMixin):
    __tablename__ = 'users'

    id         = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=True)

    email         = db.Column(db.String(120), unique=True, nullable=False)
    username      = db.Column(db.String(80), nullable=True)
    password_hash = db.Column(db.String(256), nullable=True)

    # Roles: owner / manager / customer / employee
    role = db.Column(db.String(50), nullable=False, default='customer')

    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login    = db.Column(db.DateTime, nullable=True)

    is_active   = db.Column(db.Boolean, default=True)
    is_approved = db.Column(db.Boolean, default=True)
    access_expires_at = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        from werkzeug.security import generate_password_hash
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        from werkzeug.security import check_password_hash
        return check_password_hash(self.password_hash or '', password)

    def has_valid_access(self):
        if not self.is_active or not self.is_approved:
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

    id        = db.Column(db.Integer, primary_key=True)
    user_id   = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    token     = db.Column(db.String(128), unique=True, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship('User', backref='reset_tokens')

# -----------------------------
#  Payment OPTION  
# -----------------------------
class Payment(db.Model):
    __tablename__ = 'payment'
    
    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)

    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'))
    payment_date = db.Column(db.Date)
    payment_method = db.Column(db.String(50))
    bank = db.Column(db.String(50))
    branch = db.Column(db.String(20))
    account_number = db.Column(db.String(50))
    payment_amount = db.Column(db.Numeric(10,2))

# -----------------------------
#  Payment Links Generator 
# -----------------------------
class PaymentLink(db.Model):
    __tablename__ = 'payment_links'

    id           = db.Column(db.Integer, primary_key=True)
    company_id   = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    local_id     = db.Column(db.Integer, nullable=False) 
    
    token        = db.Column(db.String(128), unique=True, nullable=False)
    
    amount       = db.Column(db.Numeric(10, 2), nullable=False)
    description  = db.Column(db.String(255), default='')
    status       = db.Column(db.String(50), default='pending') # pending / paid / expired
    
    invoice_id   = db.Column(db.Integer, nullable=True)
    customer_id  = db.Column(db.Integer, nullable=True)
    
    expires_at   = db.Column(db.DateTime, nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "local_id": self.local_id,
            "company_id": self.company_id,
            "token": self.token,
            "amount": float(self.amount),
            "description": self.description,
            "status": self.status,
            "expires_at": self.expires_at.strftime('%Y-%m-%d %H:%M:%S'),
            "created_at": self.created_at.strftime('%d/%m/%Y')
        }

# -----------------------------
#  Invoice All  
# -----------------------------
class Invoice(db.Model):
    __tablename__ = 'invoice'

    id = db.Column(db.Integer, primary_key=True)
    
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)

    invoice_number = db.Column(db.Integer, nullable=False)
    
    allocation_number = db.Column(db.String(50), unique=True, nullable=True)
    invoice_date = db.Column(db.Date, nullable=False)
    
    status = db.Column(db.String(20), default="active")

    cancellation_reason = db.Column(db.String(255), nullable=True)

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

    @property
    def total_cost(self):
        return sum(item.cost_price_at_time * item.quantity for item in self.items)

    @property
    def net_profit(self):
        return self.sub_total - self.total_cost

    def __repr__(self):
        return f'<Invoice number={self.invoice_number} company={self.company_id} allocation={self.allocation_number}>'

# -----------------------------
#  Invoice Items  
# -----------------------------
class InvoiceItem(db.Model):
    __tablename__ = 'invoice_item'

    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=False)
    
    product_id = db.Column(db.String(50), nullable=False)
    
    description = db.Column(db.String(255), nullable=True)
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    total_price = db.Column(db.Float, nullable=False)
    discount = db.Column(db.Float, default=0)   

    cost_price_at_time = db.Column(db.Float, nullable=False, default=0.0)
    income_category = db.Column(db.String(50), nullable=False, default='service')

    invoice = db.relationship('Invoice', back_populates='items')

    def __repr__(self):
        return f'<InvoiceItem Product_SKU:{self.product_id} Qty:{self.quantity}>'

# -----------------------------
#  Product All  
# -----------------------------
class Product(db.Model):
    __tablename__ = 'product'

    id = db.Column(db.Integer, primary_key=True)
    
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)

    local_id = db.Column(db.Integer, nullable=False)

    name = db.Column(db.String(100), nullable=False)
    price = db.Column(db.Float, nullable=False)        
    cost_price = db.Column(db.Float, default=0.0)      
    
    income_category = db.Column(db.String(50), default='service') 
    received_date = db.Column(db.String(20), nullable=True) 

    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    sku = db.Column(db.String(50), nullable=True)
    description = db.Column(db.String(255), nullable=True)

    quantity = db.Column(db.Float, default=0.0) 

    def to_dict(self):
        p_date = self.received_date
        if p_date and "/" in p_date:
            try:
                p_date = datetime.strptime(p_date, '%d/%m/%Y').strftime('%Y-%m-%d')
            except:
                pass

        return {
            'id': self.id,           
            'local_id': self.local_id, 
            'sku': self.sku if self.sku else (str(self.local_id) if self.local_id is not None else str(self.id)), 
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
        return f'<Product {self.name} Company:{self.company_id} Stock:{self.quantity}>'

# -----------------------------------------------------------
#  Category Model 
# -----------------------------------------------------------
class Category(db.Model):
    __tablename__ = 'categories'
    
    id = db.Column(db.Integer, primary_key=True)
    
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)

    local_id = db.Column(db.Integer, nullable=False)

    name = db.Column(db.String(100), nullable=False)
    
    products = db.relationship('Product', backref='category_ref', lazy=True)
    transactions = db.relationship('Transaction', backref='category_ref', lazy=True)

    def __repr__(self):
        return f'<Category {self.name} Company:{self.company_id}>'

# -----------------------------------------------------------
#  Transaction Model (Income & Expense)
# -----------------------------------------------------------
class Transaction(db.Model):
    __tablename__ = 'transactions'
    
    id = db.Column(db.Integer, primary_key=True)
    
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)

    local_id = db.Column(db.Integer, nullable=False)

    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    type = db.Column(db.String(10), nullable=False)     # 'income' / 'expense'
    amount = db.Column(db.Float, nullable=False)        
    
    vat_amount = db.Column(db.Float, nullable=False, default=0.0)
    
    description = db.Column(db.String(255), nullable=False)
    attachment_path = db.Column(db.String(255), nullable=True) 
    
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id'), nullable=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey('invoice.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    
    cost_price_at_time = db.Column(db.Float, nullable=True, default=0.0)
    quantity = db.Column(db.Integer, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'type': self.type,
            'amount': self.amount,
            'vat_amount': self.vat_amount,  
            'description': self.description,
            'date': self.date.isoformat() if self.date else None,
            'category_id': self.category_id,
            'customer_id': self.customer_id,
            'attachment_path': self.attachment_path, 
            'invoice_id': self.invoice_id,
            'cost_price_at_time': self.cost_price_at_time,
            'quantity': self.quantity
        }

    def __repr__(self):
        return f'<Transaction ID={self.id} Company={self.company_id} Amount={self.amount} VAT={self.vat_amount}>'

# -----------------------------
#  Customer Form Data 
# -----------------------------
class Customer(db.Model):
    __tablename__ = 'customer' 
    
    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)

    local_id = db.Column(db.Integer, nullable=False)

    customer_name = db.Column(db.String(100), nullable=False)
    customerMonth = db.Column(db.String(2))
    customerYear = db.Column(db.String(4))
    date = db.Column(db.String(10))
    id_number = db.Column(db.String(100))
    address = db.Column(db.String(100))
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(100))
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

    def __repr__(self):
        return f'<Customer {self.customer_name} CompanyID:{self.company_id}>'

# -----------------------------
#  Employee Form Data 
# -----------------------------
class Employee(db.Model):
    __tablename__ = 'employee' 
    
    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)

    local_id = db.Column(db.Integer, nullable=False)

    employee_name = db.Column(db.String(100), nullable=False)
    employeeMonth = db.Column(db.String(2))
    employeeYear = db.Column(db.String(4))
    date = db.Column(db.String(10))
    id_number = db.Column(db.String(100))
    address = db.Column(db.String(100))
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(100))
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

    role = db.Column(db.String(50), nullable=False, default='employee')
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def __repr__(self):
        return f'<Employee {self.employee_name} CompanyID:{self.company_id}>'

# -----------------------------
#  Supplier Form Data 
# -----------------------------
class Supplier(db.Model):
    __tablename__ = 'supplier'

    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)

    local_id = db.Column(db.Integer, nullable=False)

    supplier_name = db.Column(db.String(100), nullable=False)
    supplier_number = db.Column(db.String(50))
    date = db.Column(db.String(10))

    address = db.Column(db.String(100))
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(100))

    phone = db.Column(db.String(20))
    email = db.Column(db.String(100))

    payment_terms = db.Column(db.String(50))
    notes = db.Column(db.Text)

    new_field_name = db.Column(db.String(50))
    value = db.Column(db.String(100))
    row_data = db.Column(db.JSON, default={})

    role = db.Column(db.String(50), nullable=False, default='supplier')
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    purchases = db.relationship('SupplierPurchase', back_populates='supplier', lazy=True)

    def __repr__(self):
        return f'<Supplier {self.supplier_name} CompanyID:{self.company_id}>'


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

    def __repr__(self):
        return f'<SupplierPurchase ID:{self.id} Total:{self.total}>'

# -----------------------------
#  Employee Time Entry Data 
# -----------------------------
class TimeEntry(db.Model):
    __tablename__ = 'time_entries'
    id = db.Column(db.Integer, primary_key=True)
    
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    clock_in = db.Column(db.DateTime)
    clock_out = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
#  Tasks 
# -----------------------------
class Task(db.Model):
    __tablename__ = 'tasks'
    id = db.Column(db.Integer, primary_key=True)
    
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    is_completed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -----------------------------
#  Shift States 
# -----------------------------
class ShiftState(db.Model):
    __tablename__ = "shift_states"
    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)

    employee_name = db.Column(db.String(100), nullable=False)
    isClockedIn = db.Column(db.String(5), nullable=False, default="false")
    startTime = db.Column(db.String(64))
    startLocation = db.Column(db.Text)
    endTime = db.Column(db.String(64))
    endLocation = db.Column(db.Text)
    task = db.Column(db.Text)

    employee = db.relationship(
        'Employee', 
        primaryjoin="ShiftState.employee_id == Employee.id",
        backref=db.backref('shift_states', lazy='dynamic')
    )

# -----------------------------
#  Timesheets 
# -----------------------------
class Timesheet(db.Model):
    __tablename__ = "timesheets"
    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee.id'), nullable=False)

    employee_name = db.Column(db.String(100), nullable=False)
    id_number = db.Column(db.String(100))
    date = db.Column(db.String(32), nullable=False)
    
    startTime = db.Column(db.String(64), nullable=False)
    endTime = db.Column(db.String(64), nullable=False)
    startLocation = db.Column(db.Text)
    endLocation = db.Column(db.Text)
    task = db.Column(db.Text)
    totalHours = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    pass
    
