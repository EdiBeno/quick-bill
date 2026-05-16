import os
from dotenv import load_dotenv
from main import app, db

# 1. טעינת משתני סביבה קודם כל (כדי שנדע אם זה Postgres או SQLite)
load_dotenv()

def init_database():
    """
    מאתחל את בסיס הנתונים לפי ההגדרות ב-main.py.
    יוצר טבלאות רק אם הן חסרות ולא דורס נתונים קיימים.
    """
    # 2. ייבוא המודלים בתוך הפונקציה כדי למנוע Circular Import (לופ ייבוא)
    from database import (
        User, PasswordResetToken, Customer, 
        Payment, Invoice, InvoiceItem, Product, Category, Supplier, SupplierPurchase, Transaction, OwnerUser 
    )

    try:
        with app.app_context():
            # שליפת הכתובת מהקונפיג של האפליקציה (כבר כולל את התיקון של ה-postgresql://)
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
            masked_uri = db_uri.split('@')[-1] if '@' in db_uri else db_uri
            
            print(f"🔄 Connecting to: {masked_uri}")

            # 3. פקודת הקסם - יוצרת טבלאות חסרות בלבד (Safe Context)
            db.create_all()
            print("✅ Database tables created/synced successfully.")
            
            # 4. בדיקת דופק בטוחה (לא מוחק ולא משנה כלום, רק בודק קשר)
            try:
                user_count = db.session.query(User).count()
                print(f"📊 Current User count in DB: {user_count}")
                if user_count > 0:
                    print("ℹ️ Data is safe. No tables were overwritten.")
            except Exception as db_err:
                print(f"⚠️ Tables are ready, but pulse check skipped: {db_err}")

    except Exception as e:
        print(f"❌ CRITICAL ERROR: Database initialization failed!")
        print(f"Details: {str(e)}")

if __name__ == '__main__':
    init_database()
