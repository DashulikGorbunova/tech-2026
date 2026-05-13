# bot/rating.py
from __future__ import annotations

import math
from dataclasses import dataclass
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from bot.storage import UserStorage, Profile, ProfileRating

logger = logging.getLogger(__name__)

# Веса уровня 3 (комбинированный): первичный, поведенческий, «реферал»
W_PRIMARY = 0.45
W_BEHAVIOR = 0.45
W_REFERRAL = 0.1

REF_BONUS_MAX = 0.08


@dataclass(frozen=True)
class Scores:
    primary: float
    behavior: float
    combined: float


def _clamp01(x: float) -> float:
    if math.isnan(x) or x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def compute_primary_rating(profile) -> float:
    """Уровень 1: данные анкеты, полнота, фото, базовые предпочтения"""
    parts: list[float] = []
    # Полнота текста
    bio_len = len((profile.bio or "").strip())
    parts.append(_clamp01(bio_len / 400.0))
    # Фото
    parts.append(_clamp01(profile.photo_count / 4.0))
    # Интересы
    interests_ok = 1.0 if len((profile.interests or "").strip()) >= 3 else 0.4
    parts.append(interests_ok)
    # Предпочтения
    pref_ok = 1.0 if profile.preferred_gender in ("male", "female", "any") else 0.5
    parts.append(pref_ok)
    if profile.age_max > profile.age_min:
        parts.append(1.0)
    else:
        parts.append(0.5)
    # Город
    parts.append(1.0 if len((profile.city or "").strip()) >= 2 else 0.3)
    return _clamp01(sum(parts) / len(parts))


def compute_behavior_rating(likes_in: int, skips_in: int, matches_in: int) -> float:
    """Уровень 2: реакции других пользователей"""
    total = likes_in + skips_in
    if total == 0:
        return 0.5
    like_ratio = likes_in / float(total)
    denom = max(1, likes_in)
    match_signal = _clamp01(matches_in / float(denom))
    return _clamp01(0.55 * like_ratio + 0.45 * match_signal)


def compute_referral_placeholder(profile) -> float:
    """Плейсхолдер реферальной системы"""
    base = 0.5 * _clamp01(len(profile.bio or "") / 500.0) + 0.5 * _clamp01(min(profile.photo_count, 4) / 4.0)
    return _clamp01(REF_BONUS_MAX * base)


def compute_combined_rating(primary: float, behavior: float, referral: float) -> float:
    """Уровень 3: Комбинированный рейтинг"""
    ref_01 = _clamp01(referral / REF_BONUS_MAX) if REF_BONUS_MAX > 0 else 0.0
    return _clamp01(W_PRIMARY * primary + W_BEHAVIOR * behavior + W_REFERRAL * ref_01)


def recompute_for_profile(store, profile) -> Scores:
    """Полный пересчёт рейтинга профиля"""
    try:
        li, sk, mt = store.recompute_aggregates_from_db(profile.id)
        primary = compute_primary_rating(profile)
        behavior = compute_behavior_rating(li, sk, mt)
        referral = compute_referral_placeholder(profile)
        comb = compute_combined_rating(primary, behavior, referral)

        store.upsert_rating(
            profile.id,
            primary,
            behavior,
            comb,
            li,
            sk,
            mt,
        )
        logger.info(f"Rating updated for {profile.id}: primary={primary:.2f}, combined={comb:.2f}")
        return Scores(primary, behavior, comb)
    except Exception as e:
        logger.error(f"Error recomputing rating for {profile.id}: {e}")
        raise


def ensure_rating(store, profile):
    """Создаёт/обновляет запись рейтинга"""
    recompute_for_profile(store, profile)
    row = store.get_rating_row(profile.id)
    assert row is not None
    return row


def update_behavioral_rating(store, profile) -> None:
    """
    Обновить поведенческий рейтинг после лайка/скипа
    """
    try:
        li, sk, mt = store.recompute_aggregates_from_db(profile.id)
        primary = compute_primary_rating(profile)
        behavior = compute_behavior_rating(li, sk, mt)
        referral = compute_referral_placeholder(profile)
        comb = compute_combined_rating(primary, behavior, referral)

        store.upsert_rating(
            profile.id,
            primary,
            behavior,
            comb,
            li,
            sk,
            mt,
        )
        logger.debug(f"Behavioral rating updated for {profile.id}: behavior={behavior:.2f}, combined={comb:.2f}")
    except Exception as e:
        logger.error(f"Error updating behavioral rating for {profile.id}: {e}")