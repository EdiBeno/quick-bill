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
    customer_id = db.Column(db.Integer, nullable=True)

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
    mobile_phone = db.Column(db.String(20))
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
    local_id = db.Column(db.Integer, nullable=False)  
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    employee_data_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=True)

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
    local_id = db.Column(db.Integer, nullable=False)  
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
    local_id = db.Column(db.Integer, nullable=False)  
    
    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)

    employee_name = db.Column(db.String(100), nullable=False)
    isClockedIn = db.Column(db.String(5), nullable=False, default="false")
    startTime = db.Column(db.String(64))
    startLocation = db.Column(db.Text)
    endTime = db.Column(db.String(64))
    endLocation = db.Column(db.Text)
    task = db.Column(db.Text)

    employee = db.relationship(
        'EmployeeData', 
        primaryjoin="ShiftState.employee_id == EmployeeData.id",
        backref=db.backref('shift_states', lazy='dynamic')
    )

# -----------------------------
#  Timesheets 
# -----------------------------
class Timesheet(db.Model):
    __tablename__ = "timesheets"
    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    local_id = db.Column(db.Integer, nullable=False)  
    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)

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

    row_data = db.Column(db.JSON, default={})

    employee = db.relationship('EmployeeData', back_populates='timesheets')

# -----------------------------
#  Employee Data
# -----------------------------

class EmployeeData(db.Model):
    __tablename__ = 'employee_data'

    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    local_id = db.Column(db.Integer, nullable=False)  

    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    user = db.relationship('User', backref='employee_profile')

    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)

    employee_name = db.Column(db.String(100), nullable=False)
    id_number = db.Column(db.String(50), unique=True, nullable=False)  # Social security

    employeeMonth = db.Column(db.String(2))       
    employeeYear = db.Column(db.String(4))        
    date = db.Column(db.String(10))                 
    month_result = db.Column(db.String(20))       
    save_date = db.Column(db.String(10))

    month_key = db.Column(db.String(7))  
    
    address = db.Column(db.String(100))
    city = db.Column(db.String(100))
    postal_code = db.Column(db.String(20))
    mobile_phone = db.Column(db.String(20))
    home_phone = db.Column(db.String(20))
    mobile_value = db.Column(db.Float)
    clothing_value = db.Column(db.Float)
    cars_value = db.Column(db.Float)  
    email = db.Column(db.String(100))
    start_date = db.Column(db.String(10))
    aliyah_date = db.Column(db.String(10))
    date_of_birth = db.Column(db.String(10))
    monthly_city_tax_tops = db.Column(db.Float)
    bank_number = db.Column(db.String(20))
    branch_number = db.Column(db.String(20))
    account_number = db.Column(db.String(20))
    lunch_value = db.Column(db.Float)
    hourly_rate = db.Column(db.String(50))
    employee_number = db.Column(db.String(100))
    thirteenth_salary = db.Column(db.Float)
    message = db.Column(db.Text)
    work_apartment = db.Column(db.String(100))
    work_percent = db.Column(db.String(20))
    marital_status = db.Column(db.String(20))
    gender_status = db.Column(db.String(20))
    tax_credit_points = db.Column(db.String(20))
    resident_status = db.Column(db.String(100))
    hmo_member = db.Column(db.String(100))
    social_number = db.Column(db.String(20))
    irs_status = db.Column(db.String(20))
    contract_status = db.Column(db.String(20))
    kibbutz_member_status = db.Column(db.String(20))
    tax_point_child = db.Column(db.String(20))

    # Tax and salary
    final_city_tax_benefit = db.Column(db.Float)  
    total_hours = db.Column(db.Float)
    basic_salary = db.Column(db.Float)
    additional_payments = db.Column(db.Float)
    net_value = db.Column(db.Float)
    gross_salary = db.Column(db.Float)
    above_ceiling_value = db.Column(db.Float)
    above_ceiling_fund = db.Column(db.Float)
    above_ceiling_compensation = db.Column(db.Float)
    gross_taxable = db.Column(db.Float)
    pension_fund = db.Column(db.Float)
    compensation = db.Column(db.Float)
    study_fund = db.Column(db.Float)
    disability = db.Column(db.Float)
    miscellaneous = db.Column(db.Float)
    national_insurance = db.Column(db.Float)
    total_employer_contributions = db.Column(db.Float)
    total_salary_cost = db.Column(db.Float)
    employee_pension_fund = db.Column(db.Float)
    self_employed_pension_fund = db.Column(db.Float)
    study_fund_deductions = db.Column(db.Float)
    miscellaneous_deductions = db.Column(db.Float)
    national_insurance_deductions = db.Column(db.Float)
    health_insurance_deductions = db.Column(db.Float)
    income_tax = db.Column(db.Float)
    total_deductions = db.Column(db.Float)
    net_payment = db.Column(db.Float)

    advance_payment_salary = db.Column(db.Float)

    #  Car-related fields
    car_value = db.Column(db.Float)  
    car_year = db.Column(db.String(10))  
    car_model = db.Column(db.String(50))  
    car_type = db.Column(db.String(50)) 

    #  City-related fields
    monthly_city_top_tax = db.Column(db.Float)  
    city_name = db.Column(db.String(100))  
    city_sign = db.Column(db.String(10))  
    city_value_percentage = db.Column(db.Float)  

    salary_tax = db.Column(db.Float)
    income_tax_before_credit = db.Column(db.Float, default=0.0)
    tax_level_precente = db.Column(db.Float, default=0.0)
    amount_tax_credit_points_monthly = db.Column(db.Float, default=0.0)
    total_salary_pension_funds = db.Column(db.Float, default=0.0)
    hours_table_data = db.Column(db.Text, nullable=True)
    total_work_days = db.Column(db.Float, default=0.0)
    totals_lunch_value = db.Column(db.Float, default=0.0)
    total_missing_hours = db.Column(db.Float, default=0.0)
    final_extra_hours_weekend = Column(Float, default=0.0)
    final_extra_hours_regular = Column(Float, default=0.0)
    food_break_unpaid_salary = Column(Float, default=0.0)

    hours125_regular_salary = Column(Float, default=0.0)
    hours150_regular_salary = Column(Float, default=0.0)
    hours150_holidays_saturday_salary = Column(Float, default=0.0)
    hours175_holidays_saturday_salary = Column(Float, default=0.0)
    hours200_holidays_saturday_salary = Column(Float, default=0.0)

    # Yearly Sick Days Vacation Fields
    sick_days_salary = db.Column(db.Float, default=0.0)
    vacation_days_salary = db.Column(db.Float, default=0.0)
    sick_days_salary_yearly = db.Column(db.Float, default=0.0)
    vacation_days_salary_yearly = db.Column(db.Float, default=0.0)
    sick_days_entitlement = db.Column(db.Float, default=0.0)
    vacation_days_entitlement = db.Column(db.Float, default=0.0)
    sick_days_balance_yearly = db.Column(db.Float, default=0.0)
    vacation_balance_yearly = db.Column(db.Float, default=0.0)
    gross_taxable_yearly = db.Column(db.Float, default=0.0)

    # Yearly Deduction Fields
    employee_pension_fund_yearly = db.Column(db.Float, default=0.0)
    self_employed_pension_fund_yearly = db.Column(db.Float, default=0.0)
    study_fund_deductions_yearly = db.Column(db.Float, default=0.0)
    miscellaneous_deductions_yearly = db.Column(db.Float, default=0.0)
    national_insurance_deductions_yearly = db.Column(db.Float, default=0.0)
    health_insurance_deductions_yearly = db.Column(db.Float, default=0.0)
    income_tax_yearly = db.Column(db.Float, default=0.0)
    amount_tax_credit_points_monthly_yearly = db.Column(db.Float, default=0.0)
    final_city_tax_benefit_yearly = db.Column(db.Float, default=0.0)

    # Yearly Employer Contribution Fields
    pension_fund_yearly = db.Column(db.Float, default=0.0)
    compensation_yearly = db.Column(db.Float, default=0.0)
    study_fund_yearly = db.Column(db.Float, default=0.0)
    disability_yearly = db.Column(db.Float, default=0.0)
    miscellaneous_yearly = db.Column(db.Float, default=0.0)
    national_insurance_yearly = db.Column(db.Float, default=0.0)
    salary_tax_yearly = db.Column(db.Float, default=0.0)
    total_employer_contributions_yearly = db.Column(db.Float, default=0.0)
    total_salary_cost_yearly = db.Column(db.Float, default=0.0)

    # Form 102 Monthly Fields
    employee_count = Column(Integer, default=0)
    total_gross = Column(Float, default=0.0)
    total_income_tax = Column(Float, default=0.0)
    total_ni_employee = Column(Float, default=0.0)
    total_health = Column(Float, default=0.0)
    total_study_fund_deductions = Column(Float, default=0.0)
    emp_pension = Column(Float, default=0.0)
    self_pension = Column(Float, default=0.0)
    final_emp_pension_combined = Column(Float, default=0.0)
    pension_val = Column(Float, default=0.0)
    comp_val = Column(Float, default=0.0)
    disability_val = Column(Float, default=0.0)
    total_employer_pension_combined = Column(Float, default=0.0)
    study_fund_val = Column(Integer, default=0)
    national_insurance_val = Column(Integer, default=0)
    final_emp_deductions_total = Column(Float, default=0.0)
    final_totals_income_tax = Column(Float, default=0.0)

    # Form B102 Monthly Fields
    regular_salary = db.Column(db.Float)
    reduced_salary = db.Column(db.Float)
    regular_count = db.Column(db.Integer)
    reduced_count = db.Column(db.Integer)
    total_salary = db.Column(db.Float)

    # Form 102 Monthly Fields
    eilat_regular_tax = db.Column(db.Float)
    eilat_benefit_20 = db.Column(db.Float)
    eilat_total_tax_after_benefit = db.Column(db.Float)
    controlling_salary = db.Column(db.Float)
    controlling_tax = db.Column(db.Float)
    outside_eilat_salary = db.Column(db.Float)
    outside_eilat_tax = db.Column(db.Float)
    total_tax_all = db.Column(db.Float)

    regularSalary = db.Column(db.Float)
    eilatRegularTax = db.Column(db.Float)
    eilatBenefit20 = db.Column(db.Float)
    eilatTotalTaxAfterBenefit = db.Column(db.Float)
    controllingSalary = db.Column(db.Float)
    controllingTax = db.Column(db.Float)
    outsideEilatSalary = db.Column(db.Float)
    outsideEilatTax = db.Column(db.Float)
    totalTaxAll = db.Column(db.Float)

    totalDisabilityVal = db.Column(db.Float)
    totalStudyFundVal = db.Column(db.Float)
    totalProvidentDeduction = db.Column(db.Float)
    totalEmpPension = db.Column(db.Float)
    totalSelfPension = db.Column(db.Float)
    finalTotalsPaid = db.Column(db.Float)

    # =====All Form 102 Employer =====
    companyName = db.Column(db.String(255))
    companyAddress = db.Column(db.String(255))
    taxFileNumber = db.Column(db.String(50))
    reportMonth = db.Column(db.Integer)
    reportYear = db.Column(db.Integer)

    # ===== Row 216 =====
    totalIncomeTax = db.Column(db.Float)
    totalGrossSalary = db.Column(db.Float)
    NumEmployees = db.Column(db.Integer)

    # ===== Row 254 =====
    totalPensionVal = db.Column(db.Float)
    totalCompVal = db.Column(db.Float)
    NumEmployeesNonSalary = db.Column(db.Integer)

    # ===== Row 291 =====
    totalEmpDeductions = db.Column(db.Float)
    totalContributions = db.Column(db.Float)

    # ===== Row 329 =====
    employerPension = db.Column(db.Float)
    totalPensionDeduction = db.Column(db.Float)
    NumEmployeesCharges = db.Column(db.Integer)

    # ===== Row 367 =====
    totalNationalInsurance = db.Column(db.Float)
    totalHealthInsurance = db.Column(db.Float)
    NumEmployeesCharges2 = db.Column(db.Integer)

    # ===== Row 404 =====
    finalTotalIncomeTax = db.Column(db.Float)

    # =====All Signature Form 101 ,126 Data =====
    employee_email = db.Column(db.String(120), index=True)
    company_email = db.Column(db.String(120), index=True)

    signer_name = db.Column(db.String(120))
    signer_role = db.Column(db.String(120))
    signature_date = db.Column(db.String(20))
    signature_data = db.Column(db.Text)   # החתימה עצמה (base64)

    # Frontend compatibility
    new_field_name = db.Column(db.String(50))
    value = db.Column(String(100))
    row_data = db.Column(db.JSON, default={})
    role = db.Column(db.String, nullable=False, default='employee')

    # Timesheet Task Total Hours
    task = db.Column(db.Text)
    totalHours = db.Column(db.Float, nullable=False)

    #  Timestamp for when this record was entered (optional but useful)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    submission_time = db.Column(db.DateTime, default=datetime.utcnow)

    #  Relationship to EmployeeHours
    hours_data = db.relationship('HoursData', back_populates='employee', lazy=True)
    timesheets = db.relationship('Timesheet', back_populates='employee', lazy=True)

# -----------------------------
#  Form 102 Report (מתאים גם ל-SQLite וגם ל-Postgres)
# -----------------------------

class Form102Report(db.Model):
    __tablename__ = 'form102_reports'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)
    report_month = db.Column(db.String(2), nullable=False)
    report_year = db.Column(db.String(4), nullable=False)
    
    data_json = db.Column(db.JSON, nullable=False) 
    
    xml_content = db.Column(db.Text)               
    tax_response = db.Column(db.Text)               
    is_submitted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship('EmployeeData', backref='reports_102')

# -----------------------------
#  Form B102 Report
# -----------------------------

class FormB102Report(db.Model):
    __tablename__ = 'form_b102_reports'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)
    report_month = db.Column(db.String(2), nullable=False)
    report_year = db.Column(db.String(4), nullable=False)
    
    data_json = db.Column(db.JSON, nullable=False) 
    
    xml_content = db.Column(db.Text)
    tax_response = db.Column(db.Text)               
    is_submitted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship('EmployeeData', backref='reports_b102')

# -----------------------------
#  Form H102 Report
# -----------------------------

class FormH102Report(db.Model):
    __tablename__ = 'form_h102_reports'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)
    report_month = db.Column(db.String(2), nullable=False)
    report_year = db.Column(db.String(4), nullable=False)

    data_json = db.Column(db.JSON, nullable=False) 

    xml_content = db.Column(db.Text)
    tax_response = db.Column(db.Text)
    is_submitted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship('EmployeeData', backref='reports_h102')

# -----------------------------
#  Form 126 Report
# -----------------------------

class Form126Report(db.Model):
    __tablename__ = 'form_126_reports'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)
    report_year = db.Column(db.String(4), nullable=False)

    data_json = db.Column(db.JSON, nullable=False) 
    xml_content = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # =====All Signature Form 126 Data =====
    signer_name = db.Column(db.String(120))
    signer_role = db.Column(db.String(120))
    signature_date = db.Column(db.String(20))
    signature_data = db.Column(db.Text)   # החתימה עצמה (base64)

    employee = db.relationship('EmployeeData', backref='reports_126')

    def __repr__(self):
        return f'<Form126Report Emp:{self.employee_id} Date:{self.month}/{self.year}>'

# -----------------------------
#  Form 161 Report
# -----------------------------

class Form161Report(db.Model):
    __tablename__ = 'form_161_reports'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)

    data_json = db.Column(db.JSON, nullable=False) 
    xml_content = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    employee = db.relationship('EmployeeData', backref='reports_161')

# -----------------------------
#  Form 101 Report
# -----------------------------

class Tofes101Submission(db.Model):
    __tablename__ = 'tofes_101_submissions'
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)
    month = db.Column(db.String(2), nullable=False)
    year = db.Column(db.String(4), nullable=False)
    
    employee_email = db.Column(db.String(120), index=True)
    company_email = db.Column(db.String(120), index=True)
    
    data_json = db.Column(db.JSON, nullable=False) 
    submission_date = db.Column(db.DateTime, default=datetime.utcnow)

    # =====All Signature Form 101 Data =====
    signer_name = db.Column(db.String(120))
    signer_role = db.Column(db.String(120))
    signature_date = db.Column(db.String(20))
    signature_data = db.Column(db.Text)   # החתימה עצמה (base64)

    employee = db.relationship('EmployeeData', backref='submissions_101')

    def __repr__(self):
        return f'<Tofes101Submission Emp:{self.employee_id} Date:{self.month}/{self.year}>'

# -----------------------------
#  Employee Hours Record Hours Calculate
# -----------------------------

class HoursRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.String(50))
    employee_name = db.Column(db.String(200))
    month_key = db.Column(db.String(10))

    work_day_entries = db.Column(db.Text)      
    monthly_totals = db.Column(db.Text)        
    paid_totals = db.Column(db.Text)           
    tax_data = db.Column(db.Text)              
    row_data = db.Column(db.JSON, default={})

# -----------------------------
#  Employee Hours All Hours Calculate
# -----------------------------

class HoursData(db.Model):
    __tablename__ = 'hours_data'

    id = db.Column(db.Integer, primary_key=True)

    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=False)
    local_id = db.Column(db.Integer, nullable=False)  

    employee_name = db.Column(db.String(100), nullable=False)
    employeeMonth = db.Column(db.String(2), nullable=False)
    employeeYear = db.Column(db.String(4), nullable=False)

    section = db.Column(db.String, nullable=True)   
    month_key = db.Column(db.String(7))  

    # --- Daily fields ---
    date_day = db.Column(db.String(10))
    date_block = db.Column(db.String(10))
    day = db.Column(db.String(10))
    saturday = db.Column(db.String(10))
    holiday = db.Column(db.String(10))
    start_time = db.Column(db.String(10))
    end_time = db.Column(db.String(10))
    hours_calculated = db.Column(db.String(10))
    hours_calculated_regular_day = db.Column(db.String(10))
    total_extra_hours_regular_day = db.Column(db.String(10))
    extra_hours125_regular_day = db.Column(db.String(10))
    extra_hours150_regular_day = db.Column(db.String(10))
    hours_holidays_day = db.Column(db.String(10))
    extra_hours150_holidays_saturday = db.Column(db.String(10))
    extra_hours175_holidays_saturday = db.Column(db.String(10))
    extra_hours200_holidays_saturday = db.Column(db.String(10))
    sick_day = db.Column(db.String(10))
    day_off = db.Column(db.String(10))
    food_break = db.Column(db.String(10))
    final_totals_hours = db.Column(db.String(10))
    calc1 = db.Column(db.String(10))
    calc2 = db.Column(db.String(10))
    calc3 = db.Column(db.String(10))
    work_day = db.Column(db.String(10))
    missing_work_day = db.Column(db.String(10))
    advance_payment = db.Column(db.String(10))

    # --- Monthly totals ---
    hours_calculated_monthly = db.Column(db.String(10))
    hours_calculated_regular_day_monthly = db.Column(db.String(10))
    total_extra_hours_regular_day_monthly = db.Column(db.String(10))
    extra_hours125_regular_day_monthly = db.Column(db.String(10))
    extra_hours150_regular_day_monthly = db.Column(db.String(10))
    hours_holidays_day_monthly = db.Column(db.String(10))
    extra_hours150_holidays_saturday_monthly = db.Column(db.String(10))
    extra_hours175_holidays_saturday_monthly = db.Column(db.String(10))
    extra_hours200_holidays_saturday_monthly = db.Column(db.String(10))
    sick_day_monthly = db.Column(db.String(10))
    day_off_monthly = db.Column(db.String(10))
    food_break_monthly = db.Column(db.String(10))
    final_totals_hours_monthly = db.Column(db.String(10))
    calc1_monthly = db.Column(db.String(10))
    calc2_monthly = db.Column(db.String(10))
    calc3_monthly = db.Column(db.String(10))
    work_day_monthly = db.Column(db.String(10))
    missing_work_day_monthly = db.Column(db.String(10))
    advance_payment_monthly = db.Column(db.String(10))

    # --- Paid totals ---
    hours_calculated_paid = db.Column(db.String(10))
    hours_calculated_regular_day_paid = db.Column(db.String(10))
    total_extra_hours_regular_day_paid = db.Column(db.String(10))
    extra_hours125_regular_day_paid = db.Column(db.String(10))
    extra_hours150_regular_day_paid = db.Column(db.String(10))
    hours_holidays_day_paid = db.Column(db.String(10))
    extra_hours150_holidays_saturday_paid = db.Column(db.String(10))
    extra_hours175_holidays_saturday_paid = db.Column(db.String(10))
    extra_hours200_holidays_saturday_paid = db.Column(db.String(10))
    sick_day_paid = db.Column(db.String(10))
    day_off_paid = db.Column(db.String(10))
    food_break_unpaid = db.Column(db.String(10))
    final_totals_hours_paid = db.Column(db.String(10))
    calc1_paid = db.Column(db.String(10))
    calc2_paid = db.Column(db.String(10))
    calc3_paid = db.Column(db.String(10))
    final_totals_lunch_value_paid = db.Column(db.String(10))  
    final_total_extra_hours_weekend_monthly = db.Column(db.String(10))
    advance_payment_paid = db.Column(db.String(10))

    # --- שעות מצטברות ---
    hours = db.Column(db.Float)
    date = db.Column(db.String(32))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    row_data = db.Column(db.JSON, default={})

    employee_id = db.Column(db.Integer, db.ForeignKey('employee_data.id'), nullable=False)
    
    employee = db.relationship('EmployeeData', back_populates='hours_data', lazy=True)

    def to_dict(self):
        return {column.name: getattr(self, column.name) for column in self.__table__.columns}

# -----------------------------
#  Tax Credit Data
# -----------------------------
class TaxCredit(db.Model):
    __tablename__ = 'tax_credits'

    id = db.Column(db.Integer, primary_key=True)
    is_israeli_resident = db.Column(db.Boolean, nullable=False)
    gender = db.Column(db.String(10), nullable=False) 
    is_teenager = db.Column(db.Boolean, nullable=False)
    is_married = db.Column(db.Boolean, nullable=False)
    is_special_situation = db.Column(db.Boolean, nullable=False)
    has_children = db.Column(db.Boolean, nullable=False)
    married_to_widower = db.Column(db.Boolean, nullable=False)
    single_parent = db.Column(db.Boolean, nullable=False)
    separate_household = db.Column(db.Boolean, nullable=False)
    single_parent_no_spouse = db.Column(db.Boolean, nullable=False)
    paying_child_support = db.Column(db.Boolean, nullable=False)
    remarried_paying_alimony = db.Column(db.Boolean, nullable=False)
    newborn_count = db.Column(db.Integer, nullable=False)
    age_1_count = db.Column(db.Integer, nullable=False)
    age_2_count = db.Column(db.Integer, nullable=False)
    age_3_count = db.Column(db.Integer, nullable=False)
    age_4_count = db.Column(db.Integer, nullable=False)
    age_5_count = db.Column(db.Integer, nullable=False)
    age_6_17_count = db.Column(db.Integer, nullable=False)
    age_18_count = db.Column(db.Integer, nullable=False)
    children_not_in_custody = db.Column(db.Integer, nullable=False)
    total_tax_credits = db.Column(db.Float, nullable=False)

    pass
    
