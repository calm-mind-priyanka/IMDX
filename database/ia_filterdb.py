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

    class Meta:
        indexes = ("$file_name",)
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

        class Meta:
            indexes = ("$file_name",)
            collection_name = COLLECTION_NAME
else:
    Media2 = Media


async def get_files_db_size():
    return (await mydb.command("dbstats"))["dataSize"]


async def _db_size_mb(database):
    stats = await database.command("dbstats")
    return (stats.get("dataSize", 0) + stats.get("indexSize", 0)) / (1024 * 1024)


_last_movie_storage_alert = 0.0

async def notify_movie_storage_failure(bot, reason="all configured movie databases rejected the write"):
    """Rate-limited best-effort owner notification; never includes secrets."""
    global _last_movie_storage_alert
    import time
    from info import OWNER_ID
    now = time.monotonic()
    if now - _last_movie_storage_alert < 300:
        return False
    _last_movie_storage_alert = now
    message = ("⚠️ <b>Movie database storage failed.</b>\n"
               "The incoming movie could not be saved. Please check the primary database, "
               "optional secondary database, storage capacity, permissions, and connection status.\n"
               f"Diagnostic: <code>{str(reason)[:180]}</code>")
    try:
        await bot.send_message(int(OWNER_ID), message)
        return True
    except Exception as exc:
        print(f"MOVIE_DATABASE_OWNER_ALERT_FAILED: {type(exc).__name__}")
        return False


async def _owner_storage_alert(reason):
    # save_file has no Telegram client in its public API. Callers send the alert
    # with notify_movie_storage_failure when they receive the explicit 'err' status.
    print(f"MOVIE_DATABASE_WRITE_FAILED: {str(reason)[:240]}")


async def save_file(media):
    """Persist one movie file; only return success after a confirmed write/duplicate."""
    file_id, file_ref = unpack_new_file_id(media.file_id)
    file_unique_id = getattr(media, "file_unique_id", None)
    file_name = re.sub(r"[_\-\.\+#$%^&*()!~`,;:\"?/<>{}\[\]=|\\]", " ", str(media.file_name))
    file_name = re.sub(r"\s+", " ", file_name).strip()
    duplicate_filter = {"_id": file_id}
    if file_unique_id:
        duplicate_filter = {"$or": [{"_id": file_id}, {"file_unique_id": file_unique_id}]}

    # A database read failure is not evidence that the record is absent. Try all
    # readable stores, but continue to a write attempt if reads are unavailable.
    for model in ([Media, Media2] if MULTIPLE_DB and DATABASE_URI2 else [Media]):
        try:
            if await model.find_one(duplicate_filter):
                print(f'{getattr(media, "file_name", "NO_FILE")} is already saved in database')
                return "dup"
        except Exception as exc:
            print(f"Movie duplicate lookup unavailable ({type(exc).__name__}); continuing safely")

    try:
        file = Media(
            file_id=file_id, file_unique_id=file_unique_id, file_ref=file_ref,
            file_name=file_name, file_size=media.file_size, mime_type=media.mime_type,
            caption=media.caption.html if media.caption and INDEX_CAPTION else None,
            file_type=(media.mime_type.split("/")[0] if media.mime_type else None),
        )
    except ValidationError as exc:
        print(f"Movie record validation failed: {exc}")
        return "err"

    write_errors = []
    # Prefer primary first. The secondary is a fallback for a failed write, not
    # an assumed replica; search always checks both configured stores.
    targets = [Media]
    if MULTIPLE_DB and DATABASE_URI2:
        targets.append(Media2)
    for model in targets:
        try:
            target = model(
                file_id=file_id, file_unique_id=file_unique_id, file_ref=file_ref,
                file_name=file_name, file_size=media.file_size, mime_type=media.mime_type,
                caption=media.caption.html if media.caption and INDEX_CAPTION else None,
                file_type=(media.mime_type.split("/")[0] if media.mime_type else None),
            )
            await target.commit()
            print(f'{getattr(media, "file_name", "NO_FILE")} saved to movie database')
            return "suc"
        except DuplicateKeyError:
            # A duplicate discovered during a race is not a storage failure.
            return "dup"
        except Exception as exc:
            write_errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
            print(f"Movie database write failed ({type(exc).__name__}); trying configured fallback if any")

    await _owner_storage_alert("; ".join(write_errors) or "all configured movie stores rejected the write")
    return "err"


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
            results = await asyncio.gather(fetch(Media), fetch(Media2), return_exceptions=True)
            rows = []
            for result in results:
                if not isinstance(result, Exception):
                    rows.extend(result)
        else:
            rows = await fetch(Media)
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


async def _read_model(model, mongo_filter, limit, offset=0, count=False):
    """Read one store independently; never let an optional count block movie reads.

    Atlas quota errors commonly block writes/index creation, but count/index or
    transient command failures must not silently suppress a readable collection.
    Fetch the actual matching documents first. Counting is best-effort only.
    """
    try:
        rows = await model.find(mongo_filter).sort("$natural", -1).skip(offset).limit(limit).to_list(length=limit)
    except Exception as exc:
        return [], None, exc
    total = None
    if count:
        try:
            total = await model.count_documents(mongo_filter)
        except Exception as exc:
            print(f"Movie database count failed ({type(exc).__name__}); using bounded search results")
    return rows, total, None


def _media_value(row, field, default=None):
    """Read a field from either a Mongo mapping or a umongo Document.

    Motor/umongo queries return Document instances, not plain dictionaries.
    Keep mapping support for callers/tests that provide raw Mongo documents.
    """
    if isinstance(row, dict):
        return row.get(field, default)
    try:
        value = getattr(row, field)
    except (AttributeError, KeyError, TypeError):
        value = default
    return default if value is None else value


def _dedupe_media(rows):
    """Deduplicate raw Mongo dictionaries and umongo Media documents safely."""
    result, seen = [], set()
    for row in rows:
        key_value = (
            _media_value(row, "file_unique_id")
            or _media_value(row, "file_id")
            or _media_value(row, "_id")
        )
        key = str(key_value or "")
        if not key:
            key = (
                str(_media_value(row, "file_name", "") or "")
                + "|"
                + str(_media_value(row, "file_size", "") or "")
            )
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


async def get_search_results(query, max_results=MAX_BTN, offset=0, lang=None, chat_id=None, file_type=None, filter=False):
    """Search every configured movie store; tolerate a store outage and dedupe merged results."""
    mongo_filter = _build_filter(query)
    if mongo_filter is None:
        return [], "", 0
    if file_type:
        mongo_filter["file_type"] = file_type
    max_results = max(1, int(max_results or MAX_BTN))
    models = [Media, Media2] if MULTIPLE_DB and DATABASE_URI2 else [Media]
    fetch_limit = max_results + offset + 1
    results = await asyncio.gather(*[_read_model(m, mongo_filter, fetch_limit, 0, False) for m in models])
    rows, read_errors, successful_stores = [], [], 0
    for found, _count, error in results:
        if error is None:
            successful_stores += 1
            rows.extend(found)
        else:
            read_errors.append(f"{type(error).__name__}: {str(error)[:180]}")
            print(f"Movie search database read failed ({type(error).__name__}): {str(error)[:180]}")
    if successful_stores == 0:
        # Do not turn an actual DB outage into an ordinary 'no results' response.
        raise RuntimeError("All configured movie databases failed to read: " + "; ".join(read_errors))
    rows = _dedupe_media(rows)
    # A bounded result window can undercount very large overlapping datasets;
    # never claim a sum of per-database counts because copies may overlap.
    has_next = len(rows) > offset + max_results
    page = rows[offset:offset + max_results]
    next_offset = offset + len(page) if has_next else ""
    total_results = max(offset + len(page) + (1 if has_next else 0), len(rows))
    return page, next_offset, total_results


async def get_bad_files(query, file_type=None, offset=0, filter=False):
    mongo_filter = _build_filter(query)
    if mongo_filter is None:
        return [], 0
    if file_type:
        mongo_filter["file_type"] = file_type
    tasks = [Media.find(mongo_filter).sort("$natural", -1).to_list(300)]
    if MULTIPLE_DB and DATABASE_URI2:
        tasks.append(Media2.find(mongo_filter).sort("$natural", -1).to_list(300))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    files = []
    for result in results:
        if not isinstance(result, Exception):
            files.extend(result)
    files = _dedupe_media(files)
    return files[:300], min(len(files), 300)


async def get_file_details(query):
    filt = {"file_id": query}
    tasks = [Media.find(filt).to_list(length=1)]
    if MULTIPLE_DB and DATABASE_URI2:
        tasks.append(Media2.find(filt).to_list(length=1))
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for filedetails in results:
        if not isinstance(filedetails, Exception) and filedetails:
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
