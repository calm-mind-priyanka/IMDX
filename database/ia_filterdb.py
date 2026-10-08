from struct import pack
import asyncio
import base64
import re
from functools import lru_cache
from datetime import datetime, timedelta

from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError
from info import (
    FILES_DATABASE, DATABASE_URI, DATABASE_URI2, DATABASE_NAME,
    COLLECTION_NAME, MAX_BTN, MULTIPLE_DB, ULTRA_FAST_MODE,
    USE_CAPTION_FILTER, INDEX_CAPTION,
)

# Primary database. Keep FILES_DATABASE compatibility with existing IMDX deployments.
PRIMARY_URI = FILES_DATABASE or DATABASE_URI
client = AsyncIOMotorClient(PRIMARY_URI)
mydb = client[DATABASE_NAME]
instance = Instance.from_db(mydb)

# Optional second database, matching DreamX's dual-DB architecture.
if MULTIPLE_DB and DATABASE_URI2:
    client2 = AsyncIOMotorClient(DATABASE_URI2)
    mydb2 = client2[DATABASE_NAME]
    instance2 = Instance.from_db(mydb2)
else:
    client2 = client
    mydb2 = mydb
    instance2 = instance


@lru_cache(maxsize=4096)
def compile_regex(pattern):
    return re.compile(pattern, re.IGNORECASE)


@instance.register
class Media(Document):
    file_id = fields.StrField(attribute="_id")
    file_unique_id = fields.StrField(allow_none=True)
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)
    file_type = fields.StrField(allow_none=True)
    source_chat_id = fields.IntField(allow_none=True)
    source_message_id = fields.IntField(allow_none=True)
    content_key = fields.StrField(allow_none=True)
    quality_label = fields.StrField(allow_none=True)
    quality_score = fields.IntField(allow_none=True)
    resolution_score = fields.IntField(allow_none=True)
    language_key = fields.StrField(allow_none=True)

    class Meta:
        indexes = ("$file_name", "content_key")
        collection_name = COLLECTION_NAME


if MULTIPLE_DB and DATABASE_URI2:
    @instance2.register
    class Media2(Document):
        file_id = fields.StrField(attribute="_id")
        file_unique_id = fields.StrField(allow_none=True)
        file_ref = fields.StrField(allow_none=True)
        file_name = fields.StrField(required=True)
        file_size = fields.IntField(required=True)
        mime_type = fields.StrField(allow_none=True)
        caption = fields.StrField(allow_none=True)
        file_type = fields.StrField(allow_none=True)
        source_chat_id = fields.IntField(allow_none=True)
        source_message_id = fields.IntField(allow_none=True)
        content_key = fields.StrField(allow_none=True)
        quality_label = fields.StrField(allow_none=True)
        quality_score = fields.IntField(allow_none=True)
        resolution_score = fields.IntField(allow_none=True)
        language_key = fields.StrField(allow_none=True)

        class Meta:
            indexes = ("$file_name", "content_key")
            collection_name = COLLECTION_NAME
else:
    Media2 = Media




_LEGACY_BAD_QUALITY_LABELS = {
    "CAM", "CAMRIP", "HDCAM", "HDTS", "HDTC", "PREHD", "PREDVD", "PRE-DVD", "DVDSCR"
}
_GOOD_QUALITY_MIN_SCORE = 65

async def cleanup_existing_bad_quality(dry_run=True, max_scan=100000):
    """Clean legacy bad-quality records only when a clearly better copy exists.

    This is intentionally MongoDB-only: it never deletes Telegram messages because
    legacy records may have come from many channels and may not have source IDs.
    """
    models = [Media]
    if MULTIPLE_DB and DATABASE_URI2:
        models.append(Media2)
    results = {"scanned": 0, "bad_found": 0, "would_delete": 0, "deleted": 0, "protected": 0, "errors": 0}

    for model in models:
        try:
            rows = await model.find({}).to_list(length=max_scan)
        except Exception as exc:
            print(f"Quality legacy cleanup: scan failed: {exc}")
            results["errors"] += 1
            continue

        # Build a snapshot of the database's effective quality information.
        snapshot = []
        for row in rows:
            results["scanned"] += 1
            label = str(getattr(row, "quality_label", "") or "").upper()
            score = getattr(row, "quality_score", None)
            resolution = getattr(row, "resolution_score", None)
            if score is None:
                label, score, resolution, lang = _quality_details(
                    f"{getattr(row, 'file_name', '')} {getattr(row, 'caption', '') or ''}"
                )
            else:
                lang = getattr(row, "language_key", None) or _quality_details(
                    f"{getattr(row, 'file_name', '')} {getattr(row, 'caption', '') or ''}"
                )[3]
            key = getattr(row, "content_key", None) or _content_key(
                getattr(row, "file_name", ""), getattr(row, "caption", None)
            )
            if not key:
                continue
            snapshot.append((row, key, lang or "", str(label).upper(), int(score or 0), int(resolution or 0)))

        # A good copy is a protected replacement candidate. Bad copies are only
        # deleted if the same title has one of these clearly good sources.
        good_by_key = {}
        for row, key, lang, label, score, resolution in snapshot:
            if score >= _GOOD_QUALITY_MIN_SCORE and label not in _LEGACY_BAD_QUALITY_LABELS:
                good_by_key.setdefault((key, lang), []).append((score, resolution))

        for row, key, lang, label, score, resolution in snapshot:
            if label not in _LEGACY_BAD_QUALITY_LABELS:
                continue
            results["bad_found"] += 1
            matches = good_by_key.get((key, lang), [])
            # If language metadata is absent, allow an exact-title good copy.
            if not matches and not lang:
                matches = [v for (k, _l), vals in good_by_key.items() if k == key for v in vals]
            if not matches:
                continue
            best = max(matches)
            if best <= (score, resolution):
                continue
            results["would_delete"] += 1
            if dry_run:
                continue
            try:
                await model.collection.delete_one({"_id": getattr(row, "file_id", None)})
                results["deleted"] += 1
            except Exception as exc:
                results["errors"] += 1
                print(f"Quality legacy cleanup: could not delete {getattr(row, 'file_id', '?')}: {exc}")

    return results

async def get_files_db_size():
    return (await mydb.command("dbstats"))["dataSize"]


async def _db_size_mb(database):
    stats = await database.command("dbstats")
    return (stats.get("dataSize", 0) + stats.get("indexSize", 0)) / (1024 * 1024)


_QUALITY_RULES = [
    ("remux", 110),
    ("bluray", 100),
    ("blu-ray", 100),
    ("brrip", 95),
    ("bdrip", 95),
    ("org", 90),
    ("dvdrip", 55),
    ("dvdscr", 50),
    ("predvd", 45),
    ("pre-dvd", 45),
    ("prehd", 40),
    ("hdtc", 35),
    ("hdts", 30),
    ("hdcam", 20),
    ("camrip", 15),
    ("cam", 10),
    ("webrip", 80),
    ("web-dl", 85),
    ("webdl", 85),
    ("hdrip", 65),
]
_RESOLUTION_RULES = [("2160p", 4), ("1440p", 3), ("1080p", 3), ("720p", 2), ("480p", 1)]
_LANGUAGE_TOKENS = {
    "hindi", "english", "tamil", "telugu", "malayalam", "kannada", "bengali",
    "bangla", "marathi", "punjabi", "gujarati", "gujrati", "assamese", "odia",
    "urdu", "korean", "japanese", "chinese", "arabic", "spanish", "french",
    "german", "russian", "portuguese", "bhojpuri",
}
_RELEASE_TOKENS = {
    "x264", "x265", "h264", "h265", "hevc", "av1", "aac", "ac3", "ddp", "dd5", "dd+",
    "dts", "truehd", "atmos", "hdr", "dv", "10bit", "8bit", "5.1", "7.1", "proper",
    "repack", "sample", "complete", "completed", "batch", "multi", "dual", "audio",
}

def _quality_details(text):
    raw = str(text or "").lower().replace("–", "-").replace("—", "-")
    normalized = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    label, score = "UNKNOWN", 0

    for token, rank in _QUALITY_RULES:
        # Permit separators inside compound release labels (WEB-DL, CAM-RIP, etc.)
        # while requiring real word boundaries so "camera" is never classified as CAM.
        pieces = [re.escape(x) for x in re.split(r"[^a-z0-9]+", token) if x]
        pattern = r"(?<![a-z0-9])" + r"[ ._-]*".join(pieces) + r"(?![a-z0-9])"
        if re.search(pattern, normalized):
            if rank > score:
                label, score = token.upper(), rank

    resolution = 0
    for token, rank in _RESOLUTION_RULES:
        if re.search(rf"(?<!\d){re.escape(token)}(?!\d)", normalized):
            resolution = max(resolution, rank)
    languages = sorted(
        x for x in _LANGUAGE_TOKENS
        if re.search(rf"(?<![a-z]){re.escape(x)}(?![a-z])", normalized)
    )
    return label, score, resolution, "+".join(languages)


def _content_key(file_name, caption=None):
    """Conservative normalized title key used only for quality replacement."""
    text = str(file_name or caption or "")
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.I)
    text = text.lower().replace("_", " ")
    # Remove technical/release markers but deliberately keep title/year/season/episode.
    for token in (
        "2160p", "1440p", "1080p", "720p", "480p", "web-dl", "webdl", "webrip", "bluray",
        "brrip", "bdrip", "remux", "hdrip", "hdtc", "hdts", "hdcam", "camrip", "cam", "prehd",
        "predvd", "dvdscr", "dvdrip", "org", "x264", "x265", "h264", "h265", "hevc", "av1",
        "aac", "ac3", "ddp", "dd5", "dts", "truehd", "atmos", "proper", "repack", "10bit", "8bit",
    ):
        text = re.sub(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", " ", text)
    text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:gb|mb|tb)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


async def _delete_source_message(bot, chat_id, message_id):
    if not bot or chat_id is None or message_id is None:
        return False
    try:
        await bot.delete_messages(int(chat_id), int(message_id))
        return True
    except Exception as exc:
        print(f"Quality cleanup: could not delete Telegram message {chat_id}/{message_id}: {exc}")
        return False


async def _quality_owner_report(bot, text):
    """Send automatic quality actions privately to the configured owner."""
    if not bot or not OWNER_ID:
        return
    try:
        now = datetime.now().strftime("%d-%m-%Y %I:%M:%S %p")
        await bot.send_message(
            int(OWNER_ID),
            f"<b>🕒 {now}</b>\n\n{text}",
            disable_web_page_preview=True,
        )
    except Exception as exc:
        print(f"Quality cleanup: could not notify owner: {exc}")


async def _remove_quality_record(model, row, bot=None):
    """Remove the indexed record independently of Telegram permissions.

    MongoDB deletion is the authoritative cleanup action. Telegram source-message
    deletion is best-effort only and must never prevent the MongoDB record from
    being removed.
    """
    source_chat_id = getattr(row, "source_chat_id", None)
    source_message_id = getattr(row, "source_message_id", None)
    source_deleted = False
    if source_chat_id is not None and source_message_id is not None:
        source_deleted = await _delete_source_message(bot, source_chat_id, source_message_id)
    try:
        result = await model.collection.delete_one({"_id": row.file_id})
        if getattr(result, "deleted_count", 0) != 1:
            print(f"Quality cleanup: MongoDB record was not found for {getattr(row, 'file_id', '?')}")
            return False, source_deleted
        return True, source_deleted
    except Exception as exc:
        print(f"Quality cleanup: could not delete MongoDB record {getattr(row, 'file_id', '?')}: {exc}")
        return False, source_deleted


async def _quality_replace_or_reject(models, new_key, new_lang, new_score, new_resolution, bot, source_chat_id, source_message_id, new_name):
    """Keep the best copy and report every automatic quality decision to the owner.

    Returns True when the new file may be indexed, False when it was rejected.
    """
    if not new_key:
        return True
    for model in models:
        try:
            existing = await model.find({"content_key": new_key}).to_list(length=100)
        except Exception:
            continue
        for row in existing:
            old_lang = getattr(row, "language_key", None) or ""
            if old_lang and new_lang and old_lang != new_lang:
                continue
            old_score = getattr(row, "quality_score", None)
            old_res = getattr(row, "resolution_score", None) or 0
            if old_score is None:
                continue
            new_rank = (int(new_score), int(new_resolution))
            old_rank = (int(old_score), int(old_res))
            old_name = getattr(row, "file_name", "Unknown")
            old_label = getattr(row, "quality_label", None) or "UNKNOWN"
            if new_rank > old_rank:
                removed, source_deleted = await _remove_quality_record(model, row, bot)
                if removed:
                    await _quality_owner_report(
                        bot,
                        "<b>♻️ QUALITY REPLACEMENT</b>\n\n"
                        f"<b>New:</b> <code>{new_name}</code>\n"
                        f"<b>Quality:</b> {new_score} / {new_resolution}\n\n"
                        f"<b>Removed old:</b> <code>{old_name}</code>\n"
                        f"<b>Old quality:</b> {old_score} / {old_res} ({old_label})\n"
                        f"<b>Telegram source deleted:</b> {'Yes' if source_deleted else 'No / not available'}"
                    )
                else:
                    await _quality_owner_report(
                        bot,
                        "<b>⚠️ QUALITY REPLACEMENT PARTIAL</b>\n\n"
                        f"New file: <code>{new_name}</code>\n"
                        f"Old file kept because its source could not be safely removed: <code>{old_name}</code>"
                    )
            else:
                # Reject the incoming lower/equal-quality copy regardless of
                # Telegram permissions. Telegram deletion is best-effort; the
                # important rule is that the worse copy is NOT inserted into MongoDB.
                telegram_deleted = False
                if source_chat_id is not None and source_message_id is not None:
                    telegram_deleted = await _delete_source_message(bot, source_chat_id, source_message_id)
                await _quality_owner_report(
                    bot,
                    "<b>🗑️ LOWER QUALITY REJECTED</b>\n\n"
                    f"<b>Rejected:</b> <code>{new_name}</code>\n"
                    f"<b>Quality:</b> {new_score} / {new_resolution}\n\n"
                    f"<b>Kept:</b> <code>{old_name}</code>\n"
                    f"<b>Kept quality:</b> {old_score} / {old_res} ({old_label})\n\n"
                    f"<b>Telegram source deleted:</b> {'Yes' if telegram_deleted else 'No / not available'}\n"
                    f"<b>MongoDB:</b> Not inserted"
                )
                return False
    return True


async def ensure_media_indexes():
    """Best-effort index creation.

    A full MongoDB cluster can reject writes (including create_index), but that
    must never prevent the bot from starting.  DB2 is attempted independently.
    """
    status = {"primary": False, "secondary": False}
    try:
        await Media.ensure_indexes()
        status["primary"] = True
        print("Media indexes ready on primary database")
    except Exception as exc:
        print(f"WARNING: primary media indexes unavailable; bot will continue: {exc}")

    if MULTIPLE_DB and DATABASE_URI2:
        try:
            await Media2.ensure_indexes()
            status["secondary"] = True
            print("Media indexes ready on secondary database")
        except Exception as exc:
            print(f"WARNING: secondary media indexes unavailable; bot will continue: {exc}")
    return status


def _file_identity(row):
    """Stable identity for cross-database duplicate suppression."""
    unique_id = getattr(row, "file_unique_id", None)
    if unique_id:
        return ("unique", str(unique_id))
    file_id = getattr(row, "file_id", None)
    if file_id:
        return ("id", str(file_id))
    return None


def _dedupe_files(rows):
    """Remove the same Telegram file when it exists in DB1 and DB2."""
    result = []
    seen = set()
    for row in rows:
        identity = _file_identity(row)
        if identity and identity in seen:
            continue
        if identity:
            seen.add(identity)
        result.append(row)
    return result


async def _safe_model_find(model, filt, limit=None):
    try:
        cursor = model.find(filt)
        if limit is not None:
            cursor = cursor.limit(limit)
        return await cursor.to_list(length=limit)
    except Exception as exc:
        print(f"MongoDB read failed for {getattr(model, '__name__', 'media')}: {exc}")
        return []


async def _safe_count(model, filt):
    try:
        return await model.count_documents(filt)
    except Exception as exc:
        print(f"MongoDB count failed for {getattr(model, '__name__', 'media')}: {exc}")
        return 0


async def save_file(media, bot=None, source_chat_id=None, source_message_id=None):
    """Save a file and optionally perform conservative automatic quality replacement."""
    file_id, file_ref = unpack_new_file_id(media.file_id)
    file_unique_id = getattr(media, "file_unique_id", None)
    file_name = re.sub(r"[_\-\.\+#$%^&*()!~`,;:\"?/<>{}\[\]=|\\]", " ", str(media.file_name))
    file_name = re.sub(r"\s+", " ", file_name).strip()
    source_text = f"{getattr(media, 'file_name', '')} {getattr(media, 'caption', '') or ''}"
    quality_label, quality_score, resolution_score, language_key = _quality_details(source_text)
    content_key = _content_key(getattr(media, "file_name", ""), getattr(media, "caption", None))

    duplicate_filter = {"_id": file_id}
    if file_unique_id:
        duplicate_filter = {"$or": [{"_id": file_id}, {"file_unique_id": file_unique_id}]}

    try:
        if await Media.find_one(duplicate_filter):
            print(f'{getattr(media, "file_name", "NO_FILE")} is already saved in primary database')
            return "dup"
    except Exception as exc:
        print(f"Primary duplicate check unavailable; continuing: {exc}")
    if MULTIPLE_DB and DATABASE_URI2:
        try:
            if await Media2.find_one(duplicate_filter):
                print(f'{getattr(media, "file_name", "NO_FILE")} is already saved in secondary database')
                return "dup"
        except Exception as exc:
            print(f"Secondary duplicate check unavailable; continuing: {exc}")

    target_model = Media
    if MULTIPLE_DB and DATABASE_URI2:
        # Prefer DB2 before DB1 becomes completely full. If DB1 is already full,
        # DB2 is used automatically. If DB1's stats command itself fails, also
        # fail over to DB2 rather than crashing or attempting a blocked write.
        try:
            threshold_mb = float(__import__('os').environ.get("PRIMARY_DB_MAX_MB", "407"))
            if await _db_size_mb(mydb) >= threshold_mb:
                target_model = Media2
        except Exception as exc:
            print(f"Primary DB health/size check failed; using secondary database: {exc}")
            target_model = Media2

    # Automatic replacement is opt-in by the presence of source message metadata.
    # That means old/manual indexing remains safe if a source ID is unavailable.
    if source_chat_id is not None and source_message_id is not None:
        try:
            models = [Media]
            if MULTIPLE_DB and DATABASE_URI2:
                models.append(Media2)
            if not await _quality_replace_or_reject(
                models, content_key, language_key, quality_score, resolution_score,
                bot, source_chat_id, source_message_id, getattr(media, "file_name", "Unknown")
            ):
                return "dup"
        except Exception as exc:
            print(f"Quality cleanup check failed; keeping new file: {exc}")

    try:
        file = target_model(
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            mime_type=media.mime_type,
            caption=media.caption.html if media.caption and INDEX_CAPTION else None,
            file_type=(media.mime_type.split("/")[0] if media.mime_type else None),
            source_chat_id=int(source_chat_id) if source_chat_id is not None else None,
            source_message_id=int(source_message_id) if source_message_id is not None else None,
            content_key=content_key,
            quality_label=quality_label,
            quality_score=quality_score,
            resolution_score=resolution_score,
            language_key=language_key,
        )
    except ValidationError:
        print("Error occurred while saving file in database")
        return "err"

    try:
        await file.commit()
    except DuplicateKeyError:
        print(f'{getattr(media, "file_name", "NO_FILE")} is already saved in database')
        return "dup"
    else:
        print(f'{getattr(media, "file_name", "NO_FILE")} is saved to database')
        return "suc"


def _build_filter(query):
    if isinstance(query, list):
        raw_pattern = "|".join(re.escape(q.strip()) for q in query if q and q.strip())
        if not raw_pattern:
            return None
    else:
        query = (query or "").strip()
        if not query:
            raw_pattern = "."
        elif " " not in query:
            season_match = re.fullmatch(r"(?i)s0?(\d{1,2})", query)
            if season_match:
                season = int(season_match.group(1))
                raw_pattern = r"(\b|[\.\+\-_])(?:s0?" + str(season) + r"|season\s*0?" + str(season) + r")(\b|[\.\+\-_])"
            else:
                raw_pattern = r"(\b|[\.\+\-_])" + re.escape(query) + r"(\b|[\.\+\-_])"
        else:
            parts = []
            for word in query.split():
                season_match = re.fullmatch(r"(?i)s0?(\d{1,2})", word)
                if season_match:
                    season = int(season_match.group(1))
                    parts.append(r"(?:s0?" + str(season) + r"|season\s*0?" + str(season) + r")")
                else:
                    parts.append(re.escape(word))
            raw_pattern = r".*[\s\.\+\-_]".join(parts)
    try:
        regex = compile_regex(raw_pattern)
    except re.error:
        return None
    if USE_CAPTION_FILTER:
        return {"$or": [{"file_name": regex}, {"caption": regex}]}
    return {"file_name": regex}


async def get_title_candidates(query, limit=240):
    """Return local filename/caption candidates for typo correction.

    Use several short chunks instead of requiring the whole misspelled prefix.
    This is important for corrections such as ``spiterman`` -> ``Spider-Man``:
    the first four letters do not match, but short chunks such as ``spi`` do.
    DB1/DB2 are still queried concurrently and the result set is capped.
    """
    raw = str(query or "").lower()
    compact = re.sub(r"[^a-z0-9]+", "", raw)
    if len(compact) < 3:
        return []

    # Three-character chunks give the fuzzy matcher an entry point even when
    # the typo contains inserted/deleted/substituted characters near the start.
    chunks = []
    positions = {0, max(0, len(compact) // 3), max(0, len(compact) // 2)}
    for pos in sorted(positions):
        chunk = compact[pos:pos + 3]
        if len(chunk) == 3 and chunk not in chunks:
            chunks.append(chunk)
    # Also include the beginning; this is the most useful and keeps common
    # title searches inexpensive.
    if compact[:3] not in chunks:
        chunks.insert(0, compact[:3])

    raw_pattern = "|".join(re.escape(x) for x in chunks)
    try:
        regex = compile_regex(raw_pattern)
        filt = {"$or": [{"file_name": regex}, {"caption": regex}]} if USE_CAPTION_FILTER else {"file_name": regex}

        async def fetch(model):
            return await model.find(filt, {"file_name": 1, "caption": 1}).limit(limit).to_list(length=limit)

        if MULTIPLE_DB and DATABASE_URI2:
            rows1, rows2 = await asyncio.gather(
                _safe_model_find(Media, filt, limit),
                _safe_model_find(Media2, filt, limit),
            )
            rows = _dedupe_files(rows1 + rows2)
        else:
            rows = await _safe_model_find(Media, filt, limit)
    except Exception:
        return []

    candidates = []
    seen = set()
    for row in rows:
        for field in ("file_name", "caption"):
            value = row.get(field) if isinstance(row, dict) else getattr(row, field, None)
            if not value:
                continue
            text = str(value).strip()
            key = text.lower()
            if key and key not in seen:
                seen.add(key)
                candidates.append(text)
    return candidates[:limit * (2 if MULTIPLE_DB and DATABASE_URI2 else 1)]


async def get_search_results(query, max_results=MAX_BTN, offset=0, lang=None, chat_id=None, file_type=None, filter=False):
    """DreamX-style fast search, while preserving IMDX's original call signature."""
    mongo_filter = _build_filter(query)
    if mongo_filter is None:
        return [], "", 0
    if file_type:
        mongo_filter["file_type"] = file_type

    max_results = max(1, int(max_results or MAX_BTN))

    if MULTIPLE_DB and DATABASE_URI2:
        # Search both databases independently. A failed/full DB1 is treated as
        # an unavailable source, not as a fatal search error. Results are then
        # merged and deduplicated by Telegram file_unique_id/file_id.
        if ULTRA_FAST_MODE:
            limit = max_results + 1
            fetch_limit = offset + limit
            counts, found = await asyncio.gather(
                asyncio.gather(_safe_count(Media, mongo_filter), _safe_count(Media2, mongo_filter)),
                asyncio.gather(
                    _safe_model_find(Media, mongo_filter, fetch_limit),
                    _safe_model_find(Media2, mongo_filter, fetch_limit),
                ),
            )
            total_results = sum(counts)
            merged = _dedupe_files(found[1] + found[0])
            # The exact count can include a cross-DB duplicate. Reconcile the
            # total from the merged result when both databases are readable.
            if offset == 0 and len(merged) < total_results:
                total_results = len(merged)
            files = merged[offset:offset + max_results + 1]
            has_next = len(files) > max_results
            if has_next:
                files = files[:-1]
            next_offset = offset + len(files) if has_next else ""
            return files, next_offset, total_results

        counts, found = await asyncio.gather(
            asyncio.gather(_safe_count(Media, mongo_filter), _safe_count(Media2, mongo_filter)),
            asyncio.gather(
                _safe_model_find(Media, mongo_filter, offset + max_results),
                _safe_model_find(Media2, mongo_filter, offset + max_results),
            ),
        )
        total_results = sum(counts)
        files = _dedupe_files(found[1] + found[0])
        if len(files) < total_results:
            total_results = len(files)
        files = files[offset:offset + max_results]
        next_offset = offset + len(files)
        if next_offset >= total_results:
            next_offset = ""
        return files, next_offset, total_results

    # Single DB: keep the limited fetch for speed, but use an exact count for
    # pagination so the initial page cannot display a stale/estimated total.
    if ULTRA_FAST_MODE:
        limit = max_results + 1
        count_task = Media.count_documents(mongo_filter)
        files_task = Media.find(mongo_filter).sort("$natural", -1).skip(offset).limit(limit).to_list(length=limit)
        total_results, files = await asyncio.gather(count_task, files_task)
        has_next = len(files) > max_results
        if has_next:
            files = files[:-1]
        next_offset = offset + len(files) if has_next else ""
        return files, next_offset, total_results

    total_results, files = await asyncio.gather(
        Media.count_documents(mongo_filter),
        Media.find(mongo_filter).sort("$natural", -1).skip(offset).limit(max_results).to_list(length=max_results),
    )
    next_offset = offset + len(files)
    if next_offset >= total_results:
        next_offset = ""
    return files, next_offset, total_results


async def get_bad_files(query, file_type=None, offset=0, filter=False):
    mongo_filter = _build_filter(query)
    if mongo_filter is None:
        return [], 0
    if file_type:
        mongo_filter["file_type"] = file_type
    if MULTIPLE_DB and DATABASE_URI2:
        results = await asyncio.gather(
            _safe_model_find(Media, mongo_filter, 300),
            _safe_model_find(Media2, mongo_filter, 300),
        )
        files = _dedupe_files(results[1] + results[0])
    else:
        files = await _safe_model_find(Media, mongo_filter, 300)
    return files[:300], min(len(files), 300)


async def get_file_details(query):
    filt = {"_id": query}
    if MULTIPLE_DB and DATABASE_URI2:
        results = await asyncio.gather(
            _safe_model_find(Media, filt, 1),
            _safe_model_find(Media2, filt, 1),
        )
        for filedetails in results:
            if filedetails:
                return filedetails
    else:
        filedetails = await _safe_model_find(Media, filt, 1)
        if filedetails:
            return filedetails
    return []


def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    """Return file_id, file_ref"""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash,
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref
