"""Shared user-language helpers.

This module lives at project root intentionally: bot.py auto-loads every
plugins/*.py as a handler module, so shared language code must not be placed
inside plugins/.
"""
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.users_chats_db import db

# Exactly the languages already offered by the Premium language system.
# Labels remain in English so users can identify them before selecting one.
LANGUAGES = {
    "en": "🇬🇧 English",
    "hi": "🇮🇳 Hindi",
    "ta": "🇮🇳 Tamil",
    "te": "🇮🇳 Telugu",
    "kn": "🇮🇳 Kannada",
    "ml": "🇮🇳 Malayalam",
    "bn": "🇮🇳 Bengali",
    "mr": "🇮🇳 Marathi",
    "gu": "🇮🇳 Gujarati",
    "pa": "🇮🇳 Punjabi",
    "ur": "🇮🇳 Urdu",
    "as": "🇮🇳 Assamese",
    "ne": "🇳🇵 Nepali",
    "hinglish": "🇮🇳 Hinglish",
}

ALIASES = {
    "en": "en", "en-us": "en", "en-gb": "en",
    "hi": "hi", "hi-in": "hi", "hi-latn": "hinglish",
    "ta": "ta", "te": "te", "kn": "kn", "ml": "ml",
    "bn": "bn", "mr": "mr", "gu": "gu", "pa": "pa",
    "ur": "ur", "as": "as", "ne": "ne", "hinglish": "hinglish",
}


def language_markup(callback_prefix="paylang:"):
    codes = list(LANGUAGES)
    rows = []
    for i in range(0, len(codes), 2):
        row = [InlineKeyboardButton(LANGUAGES[codes[i]], callback_data=f"{callback_prefix}{codes[i]}")]
        if i + 1 < len(codes):
            row.append(InlineKeyboardButton(LANGUAGES[codes[i + 1]], callback_data=f"{callback_prefix}{codes[i + 1]}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def get_user_language(user_id, telegram_user=None):
    try:
        data = await db.get_user(int(user_id))
        saved = (data or {}).get("language") or (data or {}).get("language_code")
        if saved in LANGUAGES:
            return saved
    except Exception:
        pass
    code = str(getattr(telegram_user, "language_code", "") or "").lower().replace("_", "-")
    return ALIASES.get(code) or ALIASES.get(code.split("-", 1)[0]) or "en"


async def has_saved_language(user_id):
    try:
        data = await db.get_user(int(user_id))
        saved = (data or {}).get("language") or (data or {}).get("language_code")
        return saved in LANGUAGES
    except Exception:
        return False
