import time
import os

# 1. עדכון שם הקובץ ל-main.py
WATCHED_FILES = ["database.py", "models.py", "main.py"]

# 2. הגדרת משתנה סביבה כדי שפלאסק תדע איפה האפליקציה (קריטי!)
os.environ["FLASK_APP"] = "main.py"

last_modified = {f: os.path.getmtime(f) for f in WATCHED_FILES if os.path.exists(f)}

print("Auto‑migrate watcher running on main.py...")

while True:
    for f in WATCHED_FILES:
        if os.path.exists(f):
            current = os.path.getmtime(f)
            if current != last_modified.get(f):
                print(f"\nDetected change in {f} → running migration...")
                # Flask עכשיו יודעת להסתכל על main.py
                os.system("flask db migrate -m 'auto update'")
                os.system("flask db upgrade")
                last_modified[f] = current
    time.sleep(1)
