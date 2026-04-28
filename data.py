from flask_sqlalchemy import SQLAlchemy
from database import db, PasswordResetToken, Customer, BankAccount, Payment, Invoice, InvoiceItem, Product, User, OwnerUser 

# In-Memory Storage for Customers
customers = []

def get_customers():
    """Returns the list of customers."""
    return customers

def add_customer(customer_data):
    """Adds a new customer to the list."""
    customers.append(customer_data)

