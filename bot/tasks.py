# bot/tasks.py
from bot.celery import celery_app
from bot.rating import recalculate_profile_rating
from bot.storage import get_all_active_profiles
import logging

logger = logging.getLogger(__name__)

@celery_app.task
def recalculate_all_ratings():
    """Периодический пересчёт всех рейтингов"""
    logger.info("Starting full rating recalculation...")
    profiles = get_all_active_profiles()
    
    for profile_id in profiles:
        try:
            recalculate_profile_rating(profile_id)
        except Exception as e:
            logger.error(f"Failed to recalculate rating for profile {profile_id}: {e}")
    
    logger.info(f"Completed recalculation for {len(profiles)} profiles")