import sys
import os
sys.path.insert(0, "/app")

try:
    from app.persistence.database import init_db
    init_db()
    print("DB_INITIALIZED_SUCCESSFULLY")
except Exception as e:
    print(f"ERROR: {e}")
    raise
