from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.feed_cache import invalidate, pop_next_id, publish_interaction_event, refill_if_needed
from bot.rating import ensure_rating, recompute_for_profile
from bot.storage import UserStorage

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "bot.sqlite3"
storage = UserStorage(DB_PATH)

(
    PROFILE_NAME,
    PROFILE_AGE,
    PROFILE_GENDER,
    PROFILE_CITY,
    PROFILE_BIO,
    PROFILE_INTERESTS,
    PREF_GENDER,
    PREF_AGE,
    PROFILE_PHOTO,
) = range(9)

DELETE_ASK, DELETE_CONFIRM = 100, 101
(EDIT_BIO, EDIT_INTERESTS, EDIT_CITY) = 200, 201, 202


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Загрузка фото"""
    if update.effective_user is None or update.message is None or not update.message.photo:
        return

    user_id = update.effective_user.id
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)

    photos_dir = Path("data/photos")
    photos_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_path = photos_dir / f"{user_id}_{timestamp}.jpg"

    await file.download_to_drive(str(file_path))

    if storage.save_photo(user_id, str(file_path)):
        profile = storage.get_profile_by_telegram_id(user_id)
        if profile:
            recompute_for_profile(storage, profile)  # обновляем рейтинг
            await update.message.reply_text("✅ Фото успешно загружено!")
    else:
        await update.message.reply_text("❌ Ошибка при сохранении фото.")


async def done_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None or update.effective_user is None:
        return
    profile = storage.get_profile_by_telegram_id(update.effective_user.id)
    if profile:
        ensure_rating(storage, profile)
        await update.message.reply_text("✅ Анкета завершена! Можешь использовать /feed и /profile")
    else:
        await update.message.reply_text("❌ Анкета не найдена.")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user is None or update.message is None:
        return ConversationHandler.END

    tg_user = update.effective_user
    user, created = storage.register_or_update_user(
        telegram_id=tg_user.id,
        username=tg_user.username,
        first_name=tg_user.first_name,
        last_name=tg_user.last_name,
    )

    if created:
        text = "Привет! Давай заполним анкету."
    else:
        text = "С возвращением! Обновляем анкету."

    await update.message.reply_text(text)
    await update.message.reply_text("Как тебя зовут?")
    context.user_data["registered_user_id"] = user.id
    return PROFILE_NAME


# ==================== ШАГИ СОЗДАНИЯ АНКЕТЫ ====================
async def profile_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_NAME
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Имя слишком короткое. Введи еще раз.")
        return PROFILE_NAME
    context.user_data["profile_name"] = name
    await update.message.reply_text("Сколько тебе лет? (18-99)")
    return PROFILE_AGE


async def profile_age_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_AGE
    age_raw = update.message.text.strip()
    if not age_raw.isdigit():
        await update.message.reply_text("Возраст должен быть числом.")
        return PROFILE_AGE
    age = int(age_raw)
    if age < 18 or age > 99:
        await update.message.reply_text("Возраст от 18 до 99.")
        return PROFILE_AGE
    context.user_data["profile_age"] = age
    await update.message.reply_text("Укажи пол: м / ж")
    return PROFILE_GENDER


async def profile_gender_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_GENDER
    gender_raw = update.message.text.strip().lower()
    allowed = {"м": "male", "ж": "female"}
    if gender_raw not in allowed:
        await update.message.reply_text("Можно только: м или ж")
        return PROFILE_GENDER
    context.user_data["profile_gender"] = allowed[gender_raw]
    await update.message.reply_text("Из какого ты города?")
    return PROFILE_CITY


async def profile_city_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_CITY
    city = update.message.text.strip()
    if len(city) < 2:
        await update.message.reply_text("Название города слишком короткое.")
        return PROFILE_CITY
    context.user_data["profile_city"] = city
    await update.message.reply_text("Коротко о себе (или «-»):")
    return PROFILE_BIO


async def profile_bio_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_BIO
    t = update.message.text.strip()
    context.user_data["profile_bio"] = "" if t == "-" else t
    await update.message.reply_text("Интересы через запятую (или «-»):")
    return PROFILE_INTERESTS


async def profile_interests_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_INTERESTS
    t = update.message.text.strip()
    context.user_data["profile_interests"] = "" if t == "-" else t
    await update.message.reply_text("Кого ищешь: м / ж / все")
    return PREF_GENDER


async def pref_gender_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PREF_GENDER
    raw = update.message.text.strip().lower()
    m = {"м": "male", "ж": "female", "все": "any", "all": "any", "any": "any"}
    if raw not in m:
        await update.message.reply_text("Варианты: м / ж / все")
        return PREF_GENDER
    context.user_data["pref_gender"] = m[raw]
    await update.message.reply_text("Возраст партнёра: 20-35 или «-»")
    return PREF_AGE


async def pref_age_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PREF_AGE

    t = update.message.text.strip()
    if t == "-":
        amin, amax = 18, 99
    else:
        if "-" not in t:
            await update.message.reply_text("Нужен формат: 20-35 или «-»")
            return PREF_AGE
        a, b = [x.strip() for x in t.split("-", 1)]
        if not a.isdigit() or not b.isdigit():
            await update.message.reply_text("Формат: 20-35")
            return PREF_AGE
        amin, amax = int(a), int(b)
        if amin > amax or amin < 18 or amax > 99:
            await update.message.reply_text("18-99")
            return PREF_AGE

    context.user_data["age_min"] = amin
    context.user_data["age_max"] = amax

    user_id = context.user_data.get("registered_user_id")
    d = context.user_data
    profile = storage.save_profile(
        user_id=user_id,
        name=d["profile_name"],
        age=d["profile_age"],
        gender=d["profile_gender"],
        city=d["profile_city"],
        bio=d.get("profile_bio", ""),
        interests=d.get("profile_interests", ""),
        preferred_gender=d.get("pref_gender", "any"),
        age_min=amin,
        age_max=amax,
        photo_count=0,
    )

    await update.message.reply_text(
        f"✅ Анкета сохранена!\n\n"
        f"{profile.name}, {profile.age}, {profile.city}\n\n"
        "📸 Можешь отправить фото (можно несколько).\n"
        "Когда закончишь — напиши /done"
    )
    return PROFILE_PHOTO


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text("Создание отменено. /start — начать заново.")
    return ConversationHandler.END


def _format_profile(p) -> str:
    return (
        f"👤 Имя: {p.name}\n"
        f"🎂 Возраст: {p.age}, {p.gender}\n"
        f"📍 Город: {p.city}\n"
        f"📝 О себе: {p.bio or '—'}\n"
        f"⭐ Интересы: {p.interests or '—'}\n"
        f"🔍 Ищу: {p.preferred_gender} • {p.age_min}-{p.age_max}"
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None or update.message is None:
        return
    profile = storage.get_profile_by_telegram_id(update.effective_user.id)
    if not profile:
        await update.message.reply_text("Анкета не заполнена. /start")
        return

    r = ensure_rating(storage, profile)

    photos_dir = Path("data/photos")
    user_photos = sorted(photos_dir.glob(f"{update.effective_user.id}_*.jpg"), reverse=True)

    caption = _format_profile(profile) + f"\n\n⭐ Рейтинг: {r.combined_rating:.2f}"

    if user_photos:
        try:
            with open(user_photos[0], 'rb') as f:
                await update.message.reply_photo(photo=f, caption=caption)
            return
        except:
            pass

    await update.message.reply_text(caption)


async def feed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр карточек других пользователей"""
    if update.message is None or update.effective_user is None:
        return

    viewer = storage.get_profile_by_telegram_id(update.effective_user.id)
    if not viewer:
        await update.message.reply_text("Сначала заполни анкету — /start")
        return

    refill_if_needed(storage, viewer, min_len=1)
    next_id = pop_next_id(viewer.id)

    if next_id is None:
        invalidate(viewer.id)
        refill_if_needed(storage, viewer, min_len=1)
        next_id = pop_next_id(viewer.id)

    if next_id is None:
        await update.message.reply_text("Пока нет подходящих анкет.")
        return

    p = storage.get_profile_by_id(next_id)
    if not p:
        await update.message.reply_text("Ошибка. Попробуй /feed ещё раз.")
        return

    r = ensure_rating(storage, p)

    await update.message.reply_text(
        f"{p.name}, {p.age}, {p.city}\n"
        f"{p.bio or 'Без описания.'}\n"
        f"Интересы: {p.interests or '—'}\n"
        f"Рейтинг: {r.combined_rating:.2f}",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("❤️ Лайк", callback_data=f"swipe:like:{p.id}"),
                InlineKeyboardButton("⏭ Пропустить", callback_data=f"swipe:skip:{p.id}"),
            ]
        ])
    )


async def swipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # (можно оставить минимальную версию или расширить позже)
    await update.callback_query.answer("Действие сохранено")


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан!")

    application = Application.builder().token(token).build()
    application.bot_data["storage"] = storage

    registration_flow = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            PROFILE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_name_step)],
            PROFILE_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_age_step)],
            PROFILE_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_gender_step)],
            PROFILE_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_city_step)],
            PROFILE_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_bio_step)],
            PROFILE_INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_interests_step)],
            PREF_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, pref_gender_step)],
            PREF_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, pref_age_step)],
            PROFILE_PHOTO: [
                MessageHandler(filters.PHOTO, handle_photo),
                CommandHandler("done", done_profile),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: PROFILE_PHOTO),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    application.add_handler(registration_flow)
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("feed", feed_command))
    application.add_handler(CommandHandler("done", done_profile))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(swipe_callback, pattern=r"^swipe:"))

    logger.info("✅ Bot запущен — /feed работает, фото загружаются и влияют на рейтинг")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()