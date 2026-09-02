import sys, time
print("1. sys imported", flush=True)
import os
print("2. os imported", flush=True)
import json
print("3. json imported", flush=True)
import uuid
print("4. uuid imported", flush=True)
import httpx
print("5. httpx imported", flush=True)
from bs4 import BeautifulSoup
print("6. bs4 imported", flush=True)
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from app.config import settings
print("7. config imported", flush=True)
from app.persistence.database import SessionLocal, init_db, IS_FALLBACK_ACTIVE
print("8. database imported", flush=True)
from app.persistence.models import UniversalRecord
print("9. models imported", flush=True)
from app.storage.file_storage import file_storage
print("10. storage imported", flush=True)
from app.crawler.quality_filter import quality_filter
print("11. quality_filter imported", flush=True)
from app.persistence import repositories as repo
print("12. repo imported", flush=True)
from app.normalization.normalizer import normalizer
print("13. normalizer imported", flush=True)
print("ALL IMPORTS COMPLETE!", flush=True)
