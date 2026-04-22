from __future__ import annotations

import logging
import os
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

from bot.feed_cache import (
    invalidate,
    pop_next_id,
    publish_interaction_event,
    refill_if_needed,
)
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
) = range(8)

DELETE_ASK, DELETE_CONFIRM = 100, 101
(EDIT_BIO, EDIT_INTERESTS, EDIT_CITY) = 200, 201, 202


def _get_viewer_profile(update: Update):
    if update.effective_user is None:
        return None
    return storage.get_profile_by_telegram_id(update.effective_user.id)


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
            "Давай заполним анкету (этап 3: ранжирование и лента)."
        )
    else:
        text = (
            "С возвращением! Данные обновлены.\n\n"
            f"Твой Telegram ID: `{user.telegram_id}`\n"
            "Пройдём анкету снова — так проще вписать о себе и предпочтения."
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
    context.user_data["profile_city"] = city
    await update.message.reply_text(
        "Коротко о себе (1–2 предложения). Можно пропустить, отправь «-»."
    )
    return PROFILE_BIO


async def profile_bio_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_BIO
    t = update.message.text.strip()
    if t == "-":
        context.user_data["profile_bio"] = ""
    else:
        context.user_data["profile_bio"] = t
    await update.message.reply_text("Интересы через запятую. Пропустить: «-»")
    return PROFILE_INTERESTS


async def profile_interests_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PROFILE_INTERESTS
    t = update.message.text.strip()
    if t == "-":
        context.user_data["profile_interests"] = ""
    else:
        context.user_data["profile_interests"] = t
    await update.message.reply_text("Кого ищешь: м / ж / все (любой пол)")
    return PREF_GENDER


async def pref_gender_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PREF_GENDER
    raw = update.message.text.strip().lower()
    m = {
        "м": "male",
        "ж": "female",
        "все": "any",
        "all": "any",
        "any": "any",
        "всё": "any",
    }
    if raw not in m:
        await update.message.reply_text("Варианты: м / ж / все")
        return PREF_GENDER
    context.user_data["pref_gender"] = m[raw]
    await update.message.reply_text("Возраст партнёра: «от-до», например 20-32. Пропустить: «-» (будет 18-99).")
    return PREF_AGE


async def pref_age_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return PREF_AGE
    t = update.message.text.strip()
    if t == "-":
        amin, amax = 18, 99
    else:
        if "-" in t:
            a, b = t.split("-", 1)
            a, b = a.strip(), b.strip()
            if not a.isdigit() or not b.isdigit():
                await update.message.reply_text("Формат: 20-30 или «-»")
                return PREF_AGE
            amin, amax = int(a), int(b)
        else:
            await update.message.reply_text("Нужен диапазон, например 20-32")
            return PREF_AGE
        if amin > amax or amin < 18 or amax > 99:
            await update.message.reply_text("Проверь границы: 18-99, слева меньше")
            return PREF_AGE
    context.user_data["age_min"] = amin
    context.user_data["age_max"] = amax

    user_id = context.user_data.get("registered_user_id")
    if user_id is None:
        await update.message.reply_text("Сессия сброшена. /start")
        return ConversationHandler.END
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
    recompute_for_profile(storage, profile)
    await update.message.reply_text(
        "Анкета сохранена! Первичный и комбинированный рейтинг пересчитаны.\n"
        f"Имя: {profile.name}, {profile.age}, {profile.city}\n"
        f"Про себя: {profile.bio or '—'}\n"
        f"Интересы: {profile.interests or '—'}\n\n"
        "Команды: /profile — просмотр, /feed — лента, /edit — правки, /delete — удалить анкету."
    )
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is not None:
        await update.message.reply_text("Ок, отменил. Для старта снова: /start")
    return ConversationHandler.END


def _format_profile(p) -> str:
    return (
        f"Имя: {p.name}\n"
        f"Возраст: {p.age}, пол: {p.gender}\n"
        f"Город: {p.city}\n"
        f"О себе: {p.bio or '—'}\n"
        f"Интересы: {p.interests or '—'}\n"
        f"Ищу: {p.preferred_gender}, возраст {p.age_min}–{p.age_max}\n"
        f"Фото (учёт в рейтинге): {p.photo_count}"
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    profile = storage.get_profile_by_telegram_id(update.effective_user.id)
    if profile is None:
        await update.message.reply_text("Анкета не заполнена. /start")
        return
    r = ensure_rating(storage, profile)
    await update.message.reply_text(
        _format_profile(profile)
        + f"\n\nРейтинги: первич. {r.primary_rating:.2f}, повед. {r.behavior_rating:.2f}, итог {r.combined_rating:.2f}\n"
        f"вход.: ❤{r.likes_in}  ⏭{r.skips_in}  💫{r.matches_in}"
    )


def _card_keyboard(pid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("❤️ Лайк", callback_data=f"swipe:like:{pid}"),
                InlineKeyboardButton("⏭ Скип", callback_data=f"swipe:skip:{pid}"),
            ]
        ]
    )


async def feed_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    v = storage.get_profile_by_telegram_id(update.effective_user.id)
    if v is None:
        await update.message.reply_text("Сначала /start и анкета.")
        return
    refill_if_needed(storage, v, min_len=1)
    nxt = pop_next_id(v.id)
    if nxt is None:
        invalidate(v.id)
        refill_if_needed(storage, v, min_len=1)
        nxt = pop_next_id(v.id)
    if nxt is None:
        await update.message.reply_text("Пока некого показать: мало подходящих анкет или все просмотрены.")
        return
    p = storage.get_profile_by_id(nxt)
    if p is None:
        await update.message.reply_text("Карточка недоступна, попробуй /feed снова.")
        return
    r = ensure_rating(storage, p)
    await update.message.reply_text(
        f"{p.name}, {p.age}, {p.city}\n"
        f"{p.bio or 'Без описания.'}\n"
        f"Интересы: {p.interests or '—'}\n"
        f"Рейтинг (сводный): {r.combined_rating:.2f}",
        reply_markup=_card_keyboard(p.id),
    )


async def swipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or not q.data or update.effective_user is None:
        return
    await q.answer()
    parts = q.data.split(":")
    if len(parts) != 3 or parts[0] != "swipe":
        return
    action, spid = parts[1], parts[2]
    if not spid.isdigit():
        return
    target = int(spid)
    v = storage.get_profile_by_telegram_id(update.effective_user.id)
    if v is None or v.id == target:
        await q.message.reply_text("Нельзя поставить лайк самому себе.")
        return
    is_like = action == "like"
    created, mutual, _mid = storage.add_interaction(v.id, target, is_like)
    if not created:
        if q.message:
            await q.message.reply_text("Уже оценено. /feed — следующая.")
        return
    tprof = storage.get_profile_by_id(target)
    vprof = storage.get_profile_by_id(v.id)
    if tprof is not None:
        recompute_for_profile(storage, tprof)
    if vprof is not None:
        recompute_for_profile(storage, vprof)
    publish_interaction_event(
        storage,
        "profile_liked" if is_like else "profile_skipped",
        v.id,
        target,
        extra={"mutual": mutual} if is_like else None,
    )
    invalidate(v.id)
    lbl = "Лайк записан" if is_like else "Пропуск записан"
    if is_like and mutual and tprof is not None:
        lbl += f"\n\n💞 Мэтч с {tprof.name}!"
    ref = "\n/feed — следующая карточка"
    if q.message:
        await q.message.edit_reply_markup(None)
        await q.message.reply_text(lbl + ref)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "Команды:\n"
        "/start — регистрация\n"
        "/profile — анкета и рейтинги\n"
        "/feed — следующая карточка (лента, Redis + ранжирование)\n"
        "/edit — править о себе / интересы / город\n"
        "/delete — удалить анкету\n"
        "/cancel\n"
        "/help"
    )


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.effective_user is None:
        return ConversationHandler.END
    p = storage.get_profile_by_telegram_id(update.effective_user.id)
    if p is None:
        await update.message.reply_text("Нет анкеты. /start")
        return ConversationHandler.END
    context.user_data["e_uid"] = p.user_id
    await update.message.reply_text("Новый текст «о себе» (или - чтобы оставить как есть):")
    return EDIT_BIO


async def edit_bio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or not update.message.text:
        return EDIT_BIO
    uid = context.user_data.get("e_uid")
    if not isinstance(uid, int):
        return ConversationHandler.END
    t = update.message.text.strip()
    if t != "-":
        storage.update_profile_fields(uid, bio=t)
    await update.message.reply_text("Интересы через запятую (или - пропустить):")
    return EDIT_INTERESTS


async def edit_interests(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or not update.message.text:
        return EDIT_INTERESTS
    uid = context.user_data.get("e_uid")
    if not isinstance(uid, int):
        return ConversationHandler.END
    t = update.message.text.strip()
    if t != "-":
        storage.update_profile_fields(uid, interests=t)
    await update.message.reply_text("Новый город (или - пропустить):")
    return EDIT_CITY


async def edit_city_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or not update.message.text:
        return EDIT_CITY
    uid = context.user_data.get("e_uid")
    if not isinstance(uid, int):
        return ConversationHandler.END
    t = update.message.text.strip()
    if t != "-":
        storage.update_profile_fields(uid, city=t)
    p = None
    if update.effective_user is not None:
        p = storage.get_profile_by_telegram_id(update.effective_user.id)
    if p is not None:
        recompute_for_profile(storage, p)
    await update.message.reply_text("Сохранено. Рейтинг обновлён. /profile")
    return ConversationHandler.END


async def delete_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.effective_user is None:
        return ConversationHandler.END
    p = storage.get_profile_by_telegram_id(update.effective_user.id)
    if p is None:
        await update.message.reply_text("Анкеты нет.")
        return ConversationHandler.END
    context.user_data["d_uid"] = p.user_id
    context.user_data["d_profile_id"] = p.id
    await update.message.reply_text("Анкета будет скрыта. Для подтверждения пришли: УДАЛИТЬ")
    return DELETE_CONFIRM


async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or not update.message.text:
        return DELETE_CONFIRM
    if update.message.text.strip() != "УДАЛИТЬ":
        await update.message.reply_text("Ок, не трогаю. Если нужна очистка — снова /delete")
        return ConversationHandler.END
    uid = context.user_data.get("d_uid")
    if not isinstance(uid, int):
        return ConversationHandler.END
    pid = context.user_data.get("d_profile_id")
    storage.delete_profile(uid)
    if isinstance(pid, int):
        invalidate(pid)
    await update.message.reply_text("Анкета помечена удалённой. Снова /start для новой.")
    return ConversationHandler.END


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
            PROFILE_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_bio_step)],
            PROFILE_INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, profile_interests_step)],
            PREF_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, pref_gender_step)],
            PREF_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, pref_age_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    delete_flow = ConversationHandler(
        entry_points=[CommandHandler("delete", delete_ask)],
        states={
            DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    edit_flow = ConversationHandler(
        entry_points=[CommandHandler("edit", edit_start)],
        states={
            EDIT_BIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_bio)],
            EDIT_INTERESTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_interests)],
            EDIT_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_city_done)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
    )

    application.add_handler(registration_flow)
    application.add_handler(delete_flow)
    application.add_handler(edit_flow)
    application.add_handler(CallbackQueryHandler(swipe_callback, pattern=r"^swipe:(like|skip):\d+$"))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("feed", feed_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("help", help_command))

    logger.info("Bot service started (stage: profiles + ranking + redis cache)")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
