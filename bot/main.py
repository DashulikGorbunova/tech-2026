from __future__ import annotations

import logging
import os
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot.storage import UserStorage


logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "bot.sqlite3"
storage = UserStorage(DB_PATH)
PROFILE_NAME, PROFILE_AGE, PROFILE_GENDER, PROFILE_CITY = range(4)


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
        text = (
            "Привет! Я зарегистрировал тебя в системе.\n\n"
            f"Твой Telegram ID: `{user.telegram_id}`\n"
            "Давай заполним базовую анкету."
        )
    else:
        text = (
            "С возвращением! Я обновил твои данные.\n\n"
            f"Твой Telegram ID: `{user.telegram_id}`\n"
            "Можно обновить анкету заново."
        )

    await update.message.reply_text(text=text, parse_mode="Markdown")
    await update.message.reply_text("Как тебя зовут?")
    context.user_data["registered_user_id"] = user.id
    return PROFILE_NAME


async def profile_name_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_NAME
    name = update.message.text.strip()
    if len(name) < 2:
        await update.message.reply_text("Имя слишком короткое. Введи имя еще раз.")
        return PROFILE_NAME
    context.user_data["profile_name"] = name
    await update.message.reply_text("Сколько тебе лет? (18-99)")
    return PROFILE_AGE


async def profile_age_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_AGE
    age_raw = update.message.text.strip()
    if not age_raw.isdigit():
        await update.message.reply_text("Возраст должен быть числом. Попробуй еще раз.")
        return PROFILE_AGE
    age = int(age_raw)
    if age < 18 or age > 99:
        await update.message.reply_text("Допустимый возраст: 18-99. Попробуй еще раз.")
        return PROFILE_AGE
    context.user_data["profile_age"] = age
    await update.message.reply_text("Укажи пол: м / ж")
    return PROFILE_GENDER


async def profile_gender_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_GENDER
    gender_raw = update.message.text.strip().lower()
    allowed = {"м": "male", "ж": "female", "male": "male", "female": "female"}
    if gender_raw not in allowed:
        await update.message.reply_text("Можно указать только: м или ж.")
        return PROFILE_GENDER
    context.user_data["profile_gender"] = allowed[gender_raw]
    await update.message.reply_text("Из какого ты города?")
    return PROFILE_CITY


async def profile_city_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_CITY
    city = update.message.text.strip()
    if len(city) < 2:
        await update.message.reply_text("Город слишком короткий. Попробуй еще раз.")
        return PROFILE_CITY

    user_id = context.user_data.get("registered_user_id")
    name = context.user_data.get("profile_name")
    age = context.user_data.get("profile_age")
    gender = context.user_data.get("profile_gender")
    if user_id is None or name is None or age is None or gender is None:
        await update.message.reply_text("Сессия заполнения прервалась. Нажми /start и начни заново.")
        return ConversationHandler.END

    profile = storage.save_profile(
        user_id=user_id,
        name=name,
        age=age,
        gender=gender,
        city=city,
    )
    await update.message.reply_text(
        "Анкета сохранена!\n"
        f"Имя: {profile.name}\n"
        f"Возраст: {profile.age}\n"
        f"Пол: {profile.gender}\n"
        f"Город: {profile.city}\n\n"
        "Команда /profile покажет текущую анкету."
    )
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is not None:
        await update.message.reply_text("Ок, отменил заполнение. Для старта снова нажми /start.")
    return ConversationHandler.END


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    profile = storage.get_profile_by_telegram_id(update.effective_user.id)
    if profile is None:
        await update.message.reply_text("Анкета пока не заполнена. Нажми /start.")
        return
    await update.message.reply_text(
        "Твоя анкета:\n"
        f"Имя: {profile.name}\n"
        f"Возраст: {profile.age}\n"
        f"Пол: {profile.gender}\n"
        f"Город: {profile.city}"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "Команды:\n"
        "/start — регистрация и заполнение анкеты\n"
        "/profile — показать анкету\n"
        "/cancel — отменить текущее заполнение\n"
        "/help — показать справку"
    )


def get_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Не задан TELEGRAM_BOT_TOKEN. Установи переменную окружения и перезапусти бота."
        )
    return token


def main() -> None:
    token = get_bot_token()
    application = Application.builder().token(token).build()

    registration_flow = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            PROFILE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_name_step)],
            PROFILE_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_age_step)],
            PROFILE_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_gender_step)],
            PROFILE_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_city_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    application.add_handler(registration_flow)
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("help", help_command))

    logger.info("Bot service started")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
