from flask_sqlalchemy import SQLAlchemy
from database import db, PasswordResetToken, Company, Customer, Employee, Payment, PaymentLink, Invoice, InvoiceItem, Product, Category, Supplier, SupplierPurchase, Transaction, ShiftState, Timesheet, TimeEntry, Task, User, OwnerUser 

# In-Memory Storage for Customers
customers = []

def get_customers():
    """Returns the list of customers."""
    return customers

def add_customer(customer_data):
    """Adds a new customer to the list."""
    customers.append(customer_data)

# In-Memory Storage for Employees
employees = []

def get_employees():
    """Returns the list of employees."""
    return employees

def add_employee(employee_data):
    """Adds a new employee to the list."""
    employees.append(employee_data)

# In-Memory Storage for Suppliers
suppliers = []

def get_suppliers():
    """Returns the list of suppliers."""
    return suppliers

def add_supplier(supplier_data):
    """Adds a new supplier to the list."""
    suppliers.append(supplier_data)
