import time, socket, sys
from urllib.parse import urlparse
print("D1: modules imported", flush=True)
from app.config import settings
print("D2: settings imported. URL:", settings.DATABASE_URL, flush=True)
import app.persistence.database as db_mod
print("D3: database module loaded!", flush=True)
