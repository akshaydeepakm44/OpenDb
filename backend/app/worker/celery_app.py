import os
from celery import Celery
from app.config import settings

redis_url = settings.REDIS_URL

celery_app = Celery(
    "opendb_worker",
    broker=redis_url,
    backend=redis_url,
    include=["app.worker.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes max per task
)
