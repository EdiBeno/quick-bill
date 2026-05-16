import os
from main import app, db
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

# טעינת משתני סביבה (.env)
load_dotenv()

def create_database():
    """
    מאתחל את הטבלאות במסד הנתונים שנבחר ב-main.py (Render או Local)
    """
    # ייבוא המודלים בתוך הפונקציה מונע שגיאת Circular Import
    from database import (
        PasswordResetToken, Customer, Payment, Invoice, 
        InvoiceItem, Product, Category, Supplier, SupplierPurchase, Transaction, User, OwnerUser 
    )
    
    try:
        with app.app_context():
            # שליפת ה-URI מהקונפיג שנקבע ב-main.py
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI')
            
            if not db_uri:
                print("❌ Error: SQLALCHEMY_DATABASE_URI is not set!")
                return

            # זיהוי סוג השרת להדפסה בלבד
            db_type = "PostgreSQL (Render)" if "postgresql" in db_uri else "SQLite (Local)"
            print(f"🚀 Environment detected. Initializing {db_type}...")
            
            # יצירת הטבלאות
            db.create_all()  
            
            # הדפסת סיכום
            table_count = len(db.metadata.tables)
            print(f"✅ Success! {table_count} tables are now synced on {db_type}.")
            print(f"📂 Database Path/URI: {db_uri.split('@')[-1]}") # מדפיס רק את סוף הכתובת לביטחון
            
    except SQLAlchemyError as e:
        print(f"❌ Database Error: {e}")
    except Exception as e:
        print(f"❌ General Error: {e}")

if __name__ == '__main__':
    create_database()
