from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, InputMediaPhoto
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

# Состояния для ConversationHandler
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
            recompute_for_profile(storage, profile)
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
        "📸 Можешь отправить фото.\n"
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
    """Просмотр карточек других пользователей с фото"""
    if update.message is None or update.effective_user is None:
        return

    viewer = storage.get_profile_by_telegram_id(update.effective_user.id)
    if not viewer:
        await update.message.reply_text("Сначала заполни анкету — /start")
        return

    # Получаем ID анкеты ДО того, как пытаемся её отправить
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

    text = (
        f"👤 {p.name}, {p.age}, {p.city}\n\n"
        f"📝 {p.bio or 'Без описания'}\n\n"
        f"⭐ Интересы: {p.interests or '—'}\n"
        f"📊 Рейтинг: {r.combined_rating:.2f}"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❤️ Лайк", callback_data=f"swipe:like:{p.id}"),
        InlineKeyboardButton("⏭ Пропустить", callback_data=f"swipe:skip:{p.id}"),
    ]])

    try:
        user_telegram_id = storage._get_telegram_id_for_user(p.user_id)
        photos_dir = Path("data/photos")
        user_photos = sorted(photos_dir.glob(f"{user_telegram_id}_*.jpg"), reverse=True)
        
        if user_photos:
            with open(user_photos[0], 'rb') as photo_file:
                await update.message.reply_photo(
                    photo=photo_file, 
                    caption=text, 
                    reply_markup=keyboard
                )
        else:
            await update.message.reply_text(text=text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при отправке анкеты {p.id}: {e}")
        # Если ошибка, анкета УЖЕ удалена из очереди (pop_next_id)
        # Просто показываем следующую
        await update.message.reply_text("⚠️ Ошибка при загрузке анкеты, пробую следующую...")
        await feed_command(update, context)  # Рекурсивно показываем следующую
    """Просмотр карточек других пользователей с фото"""
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

    text = (
        f"👤 {p.name}, {p.age}, {p.city}\n\n"
        f"📝 {p.bio or 'Без описания'}\n\n"
        f"⭐ Интересы: {p.interests or '—'}\n"
        f"📊 Рейтинг: {r.combined_rating:.2f}"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❤️ Лайк", callback_data=f"swipe:like:{p.id}"),
        InlineKeyboardButton("⏭ Пропустить", callback_data=f"swipe:skip:{p.id}"),
    ]])

    # Получаем telegram_id пользователя для поиска его фото
    try:
        user_telegram_id = storage._get_telegram_id_for_user(p.user_id)
        
        # Ищем фото пользователя
        photos_dir = Path("data/photos")
        user_photos = sorted(photos_dir.glob(f"{user_telegram_id}_*.jpg"), reverse=True)
        
        if user_photos:
            # Отправляем карточку с фото
            try:
                with open(user_photos[0], 'rb') as photo_file:
                    await update.message.reply_photo(
                        photo=photo_file, 
                        caption=text, 
                        reply_markup=keyboard
                    )
            except Exception as e:
                logger.error(f"Ошибка при отправке фото: {e}")
                await update.message.reply_text(text=text, reply_markup=keyboard)
        else:
            await update.message.reply_text(text=text, reply_markup=keyboard)
    except Exception as e:
        logger.error(f"Ошибка при получении фото: {e}")
        await update.message.reply_text(text=text, reply_markup=keyboard)


async def swipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка лайков и пропусков - простая версия без редактирования"""
    query = update.callback_query
    await query.answer()

    if query.data is None:
        return

    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "swipe":
        return

    action = parts[1]
    target_profile_id = int(parts[2])

    user_id = query.from_user.id
    viewer = storage.get_profile_by_telegram_id(user_id)

    if not viewer:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Сначала заполни анкету — /start"
        )
        return

    target = storage.get_profile_by_id(target_profile_id)
    if not target:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Анкета больше не доступна"
        )
        invalidate(viewer.id)
        return

    # Удаляем исходное сообщение с анкетой (не пытаемся редактировать)
    try:
        await query.delete_message()
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    # Сохраняем взаимодействие
    is_like = (action == "like")
    created, mutual, match_id = storage.add_interaction(viewer.id, target.id, is_like)

    if not created:
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ Ты уже оценил эту анкету!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➡️ Следующая анкета", callback_data="next_feed")
            ]])
        )
        return

    # Формируем сообщение о результате
    if action == "like":
        if mutual:
            await notify_match(context, viewer, target)
            message_text = f"💘 Взаимная симпатия с {target.name}!"
        else:
            message_text = f"👍 Ты лайкнул(а) {target.name}!"
    else:
        message_text = f"👎 Ты пропустил(а) {target.name}"

    # Отправляем сообщение о результате с кнопкой "дальше"
    await context.bot.send_message(
        chat_id=user_id,
        text=message_text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("➡️ Следующая анкета", callback_data="next_feed")
        ]])
    )

    # Отправляем событие в очередь для пересчёта рейтинга
    event_type = "profile_liked" if action == "like" else "profile_skipped"
    publish_interaction_event(storage, event_type, viewer.id, target.id)

    # Обновляем рейтинг цели
    recompute_for_profile(storage, target)

    # Инвалидируем кэш ленты у зрителя
    invalidate(viewer.id)
    """Обработка лайков и пропусков"""
    query = update.callback_query
    await query.answer()

    if query.data is None:
        return

    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "swipe":
        return

    action = parts[1]
    target_profile_id = int(parts[2])

    user_id = query.from_user.id
    viewer = storage.get_profile_by_telegram_id(user_id)

    if not viewer:
        # Проверяем тип сообщения перед редактированием
        if query.message.text:
            await query.edit_message_text("❌ Сначала заполни анкету — /start")
        else:
            await query.message.reply_text("❌ Сначала заполни анкету — /start")
        return

    target = storage.get_profile_by_id(target_profile_id)
    if not target:
        if query.message.text:
            await query.edit_message_text("❌ Анкета больше не доступна")
        else:
            await query.message.reply_text("❌ Анкета больше не доступна")
        invalidate(viewer.id)
        return

    # add_interaction возвращает кортеж (created, mutual_match, match_id)
    is_like = (action == "like")
    created, mutual, match_id = storage.add_interaction(viewer.id, target.id, is_like)

    if not created:
        # Если не создано (уже оценил), показываем сообщение и кнопку следующей анкеты
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("➡️ Следующая анкета", callback_data="next_feed")
        ]])
        
        try:
            # Пытаемся отредактировать существующее сообщение
            if query.message.text:
                await query.edit_message_text(
                    "⚠️ Ты уже оценил эту анкету!",
                    reply_markup=keyboard
                )
            else:
                # Если это фото, удаляем старое и отправляем новое
                await query.delete_message()
                await context.bot.send_message(
                    chat_id=user_id,
                    text="⚠️ Ты уже оценил эту анкету!",
                    reply_markup=keyboard
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения: {e}")
            await context.bot.send_message(
                chat_id=user_id,
                text="⚠️ Ты уже оценил эту анкету!",
                reply_markup=keyboard
            )
        return

    # Создаём клавиатуру для следующей анкеты
    next_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("➡️ Следующая анкета", callback_data="next_feed")
    ]])

    # Формируем сообщение в зависимости от действия
    if action == "like":
        if mutual:
            await notify_match(context, viewer, target)
            message_text = f"💘 Взаимная симпатия с {target.name}!"
        else:
            message_text = f"👍 Ты лайкнул(а) {target.name}!"
    else:  # skip
        message_text = f"👎 Ты пропустил(а) {target.name}"

    # Отправляем результат, пытаясь отредактировать исходное сообщение
    try:
        if query.message.text:
            await query.edit_message_text(message_text, reply_markup=next_keyboard)
        else:
            # Если это было фото, удаляем и отправляем текстовое сообщение
            await query.delete_message()
            await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
                reply_markup=next_keyboard
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке результата: {e}")
        # Если не удалось отредактировать, отправляем новое сообщение
        await context.bot.send_message(
            chat_id=user_id,
            text=message_text,
            reply_markup=next_keyboard
        )

    # Отправляем событие в очередь для пересчёта рейтинга
    event_type = "profile_liked" if action == "like" else "profile_skipped"
    publish_interaction_event(storage, event_type, viewer.id, target.id)

    # Обновляем рейтинг цели
    recompute_for_profile(storage, target)

    # Инвалидируем кэш ленты у зрителя
    invalidate(viewer.id)
    """Обработка лайков и пропусков"""
    query = update.callback_query
    await query.answer()

    if query.data is None:
        return

    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "swipe":
        return

    action = parts[1]
    target_profile_id = int(parts[2])

    user_id = query.from_user.id
    viewer = storage.get_profile_by_telegram_id(user_id)

    if not viewer:
        await query.edit_message_text("❌ Сначала заполни анкету — /start")
        return

    target = storage.get_profile_by_id(target_profile_id)
    if not target:
        await query.edit_message_text("❌ Анкета больше не доступна")
        invalidate(viewer.id)
        return

    is_like = (action == "like")
    created, mutual, match_id = storage.add_interaction(viewer.id, target.id, is_like)

    if not created:
        await query.edit_message_text("⚠️ Ты уже оценил эту анкету!")
        return

    if action == "like":
        if mutual:
            await notify_match(context, viewer, target)
            await query.edit_message_text(
                f"💘 Взаимная симпатия с {target.name}!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("➡️ Следующая анкета", callback_data="next_feed")
                ]])
            )
        else:
            await query.edit_message_text(
                f"👍 Ты лайкнул(а) {target.name}!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("➡️ Следующая анкета", callback_data="next_feed")
                ]])
            )
    else:
        await query.edit_message_text(
            f"👎 Ты пропустил(а) {target.name}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("➡️ Следующая анкета", callback_data="next_feed")
            ]])
        )

    event_type = "profile_liked" if action == "like" else "profile_skipped"
    publish_interaction_event(storage, event_type, viewer.id, target.id)
    recompute_for_profile(storage, target)
    invalidate(viewer.id)


async def next_feed_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать следующую анкету"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    viewer = storage.get_profile_by_telegram_id(user_id)

    if not viewer:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Сначала заполни анкету — /start"
        )
        return

    # Удаляем сообщение с кнопкой "дальше"
    try:
        await query.delete_message()
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    # Получаем следующую анкету
    refill_if_needed(storage, viewer, min_len=1)
    next_id = pop_next_id(viewer.id)

    if next_id is None:
        invalidate(viewer.id)
        refill_if_needed(storage, viewer, min_len=1)
        next_id = pop_next_id(viewer.id)

    if next_id is None:
        await context.bot.send_message(
            chat_id=user_id,
            text="📭 Пока нет подходящих анкет. Зайди позже!"
        )
        return

    target = storage.get_profile_by_id(next_id)
    if not target:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Ошибка. Попробуй /feed ещё раз"
        )
        return

    r = ensure_rating(storage, target)

    text = (
        f"👤 {target.name}, {target.age}, {target.city}\n\n"
        f"📝 {target.bio or 'Без описания'}\n\n"
        f"⭐ Интересы: {target.interests or '—'}\n"
        f"📊 Рейтинг: {r.combined_rating:.2f}"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❤️ Лайк", callback_data=f"swipe:like:{target.id}"),
        InlineKeyboardButton("⏭ Пропустить", callback_data=f"swipe:skip:{target.id}"),
    ]])

    try:
        user_telegram_id = storage._get_telegram_id_for_user(target.user_id)
        photos_dir = Path("data/photos")
        user_photos = sorted(photos_dir.glob(f"{user_telegram_id}_*.jpg"), reverse=True)
        
        if user_photos:
            with open(user_photos[0], 'rb') as photo_file:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file,
                    caption=text,
                    reply_markup=keyboard
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=keyboard
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке анкеты {target.id}: {e}")
        # Пробуем следующую
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ Ошибка при загрузке анкеты, пробую следующую..."
        )
        await next_feed_callback(update, context)
    """Показать следующую анкету после лайка/скипа с фото"""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    viewer = storage.get_profile_by_telegram_id(user_id)

    if not viewer:
        await query.message.reply_text("❌ Сначала заполни анкету — /start")
        return

    # Удаляем старое сообщение
    try:
        await query.delete_message()
    except Exception as e:
        logger.error(f"Не удалось удалить сообщение: {e}")

    # Получаем следующую анкету
    refill_if_needed(storage, viewer, min_len=1)
    next_id = pop_next_id(viewer.id)

    if next_id is None:
        invalidate(viewer.id)
        refill_if_needed(storage, viewer, min_len=1)
        next_id = pop_next_id(viewer.id)

    if next_id is None:
        await context.bot.send_message(
            chat_id=user_id,
            text="📭 Пока нет подходящих анкет. Зайди позже!"
        )
        return

    target = storage.get_profile_by_id(next_id)
    if not target:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Ошибка. Попробуй /feed ещё раз"
        )
        return

    r = ensure_rating(storage, target)

    text = (
        f"👤 {target.name}, {target.age}, {target.city}\n\n"
        f"📝 {target.bio or 'Без описания'}\n\n"
        f"⭐ Интересы: {target.interests or '—'}\n"
        f"📊 Рейтинг: {r.combined_rating:.2f}"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("❤️ Лайк", callback_data=f"swipe:like:{target.id}"),
        InlineKeyboardButton("⏭ Пропустить", callback_data=f"swipe:skip:{target.id}"),
    ]])

    try:
        user_telegram_id = storage._get_telegram_id_for_user(target.user_id)
        photos_dir = Path("data/photos")
        user_photos = sorted(photos_dir.glob(f"{user_telegram_id}_*.jpg"), reverse=True)
        
        if user_photos:
            with open(user_photos[0], 'rb') as photo_file:
                await context.bot.send_photo(
                    chat_id=user_id,
                    photo=photo_file,
                    caption=text,
                    reply_markup=keyboard
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                reply_markup=keyboard
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке следующей анкеты {target.id}: {e}")
        # Рекурсивно показываем следующую (текущая уже удалена из очереди)
        await context.bot.send_message(
            chat_id=user_id,
            text="⚠️ Ошибка при загрузке анкеты, пробую следующую..."
        )
        await next_feed_callback(update, context)

async def notify_match(context: ContextTypes.DEFAULT_TYPE, viewer, target):
    """Отправить уведомление о взаимной симпатии"""
    viewer_telegram_id = storage._get_telegram_id_for_user(viewer.user_id)
    target_telegram_id = storage._get_telegram_id_for_user(target.user_id)
    
    try:
        await context.bot.send_message(
            chat_id=viewer_telegram_id,
            text=f"💘 Взаимная симпатия с {target.name}! Теперь вы можете общаться."
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить {viewer_telegram_id}: {e}")

    try:
        await context.bot.send_message(
            chat_id=target_telegram_id,
            text=f"💘 Взаимная симпатия с {viewer.name}! Теперь вы можете общаться."
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить {target_telegram_id}: {e}")


async def refresh_feed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Очистить кэш и показать новую ленту"""
    if update.message is None or update.effective_user is None:
        return

    viewer = storage.get_profile_by_telegram_id(update.effective_user.id)
    if not viewer:
        await update.message.reply_text("❌ Сначала заполни анкету — /start")
        return

    invalidate(viewer.id)
    await update.message.reply_text("🔄 Лента обновлена! Начинаю показ с начала.")
    await feed_command(update, context)

async def reset_feed_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать все анкеты заново (включая уже оценённые)"""
    if update.message is None or update.effective_user is None:
        return

    viewer = storage.get_profile_by_telegram_id(update.effective_user.id)
    if not viewer:
        await update.message.reply_text("❌ Сначала заполни анкету — /start")
        return

    # Очищаем кэш
    invalidate(viewer.id)
    
    # Получаем ID всех анкет, которые подходят по фильтрам
    all_candidates = storage.list_candidate_profiles(viewer, set(), limit=500)
    
    if not all_candidates:
        await update.message.reply_text("📭 Нет доступных анкет.")
        return
    
    # Сортируем по рейтингу
    scored = []
    for p in all_candidates:
        r = ensure_rating(storage, p)
        scored.append((r.combined_rating, p.id))
    scored.sort(key=lambda x: x[0], reverse=True)
    
    # Создаём новую очередь вручную (минуя проверку на просмотренные)
    from bot.feed_cache import _get_backend, _key_for
    backend, _ = _get_backend()
    k = _key_for(viewer.id)
    backend.delete(k)
    
    # Добавляем все анкеты в обратном порядке
    for pid in reversed([sid for _, sid in scored]):
        backend.lpush(k, str(pid))
    
    await update.message.reply_text(
        f"✅ Лента полностью обновлена! Добавлено {len(scored)} анкет.\n"
        "Теперь будут показываться ВСЕ анкеты, даже те, которые ты уже видел.\n\n"
        "Используй /feed для просмотра."
    )


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
    application.add_handler(CommandHandler("refresh", refresh_feed_command))
    application.add_handler(CommandHandler("new", refresh_feed_command))
    application.add_handler(CommandHandler("reset_feed", reset_feed_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    application.add_handler(CallbackQueryHandler(swipe_callback, pattern=r"^swipe:"))
    application.add_handler(CallbackQueryHandler(next_feed_callback, pattern=r"^next_feed$"))

    logger.info("✅ Бот запущен — /feed работает, кнопки лайк/скип работают, /refresh работает!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()