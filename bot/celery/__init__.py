# bot/celery/__init__.py
from celery import Celery
import os

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "dating_bot",
    broker=redis_url,
    backend=redis_url,
    include=["bot.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Moscow",
    enable_utc=True,
    beat_schedule={
        "recalculate-all-ratings-every-30-min": {
            "task": "bot.tasks.recalculate_all_ratings",
            "schedule": 1800.0,  # 30 минут
        },
    },
)