from struct import pack
import asyncio
import base64
import re
import os
import logging
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
logger = logging.getLogger(__name__)
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
    try:
        return (await mydb.command("dbstats"))["dataSize"]
    except Exception:
        if MULTIPLE_DB and DATABASE_URI2:
            try:
                return (await mydb2.command("dbstats"))["dataSize"]
            except Exception:
                logger.exception("Both movie database stats queries failed")
        return 0


async def _db_size_mb(database):
    stats = await database.command("dbstats")
    return (stats.get("dataSize", 0) + stats.get("indexSize", 0)) / (1024 * 1024)


async def save_file(media):
    """Save to the primary index until its threshold, then fail over safely.

    If the primary reaches its configured limit or fails while committing, try
    the secondary database. A missing/disabled secondary is always optional.
    """
    try:
        file_id, file_ref = unpack_new_file_id(media.file_id)
    except Exception:
        logger.exception("Could not decode incoming Telegram file ID")
        return "err"
    file_unique_id = getattr(media, "file_unique_id", None)
    file_name = re.sub(r"[_\-\.\+#$%^&*()!~`,;:\"?/<>{}\[\]=|\\]", " ", str(media.file_name))
    file_name = re.sub(r"\s+", " ", file_name).strip()

    duplicate_filter = {"_id": file_id}
    if file_unique_id:
        duplicate_filter = {"$or": [{"_id": file_id}, {"file_unique_id": file_unique_id}]}

    models = [Media]
    if MULTIPLE_DB and DATABASE_URI2:
        models.append(Media2)
    for model in models:
        try:
            if await model.find_one(duplicate_filter):
                logger.info("Movie already indexed: %s", getattr(media, "file_name", "NO_FILE"))
                return "dup"
        except Exception:
            logger.warning("Duplicate check failed for one movie database; continuing", exc_info=True)

    preferred = 0
    if len(models) > 1:
        try:
            threshold_mb = float(os.environ.get("PRIMARY_DB_MAX_MB", "407"))
            if await _db_size_mb(mydb) >= threshold_mb:
                preferred = 1
        except Exception:
            # A stats failure is not a reason to fail indexing. Try primary,
            # and if its write fails the secondary is attempted below.
            preferred = 0

    try:
        document = dict(
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            mime_type=media.mime_type,
            caption=media.caption.html if media.caption and INDEX_CAPTION else None,
            file_type=(media.mime_type.split("/")[0] if media.mime_type else None),
        )
    except Exception:
        logger.exception("Could not prepare movie document")
        return "err"

    order = [models[preferred]] + [m for i, m in enumerate(models) if i != preferred]
    for model in order:
        try:
            file = model(**document)
            await file.commit()
            logger.info("Movie indexed successfully in %s", "primary" if model is Media else "secondary")
            return "suc"
        except DuplicateKeyError:
            return "dup"
        except ValidationError:
            logger.exception("Movie document validation failed")
            return "err"
        except Exception:
            logger.exception("Movie database write failed; trying next configured database")
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

        tasks = [fetch(Media)]
        if MULTIPLE_DB and DATABASE_URI2:
            tasks.append(fetch(Media2))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        rows = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Title candidate query failed in one movie DB: %s", result)
            else:
                rows.extend(result)
    except Exception:
        logger.exception("Title candidate search failed")
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
        limit = max_results + 1
        fetch_limit = limit if offset == 0 else offset + limit

        async def safe_fetch(model):
            try:
                if ULTRA_FAST_MODE:
                    return await model.find(mongo_filter).sort("$natural", -1).limit(fetch_limit).to_list(length=fetch_limit)
                return await model.find(mongo_filter).sort("$natural", -1).limit(offset + max_results).to_list(length=offset + max_results)
            except Exception:
                logger.exception("Movie search failed in one configured database")
                return []

        async def safe_count(model):
            try:
                return await model.count_documents(mongo_filter)
            except Exception:
                logger.exception("Movie count failed in one configured database")
                return None

        fetch_tasks = [safe_fetch(Media), safe_fetch(Media2)]
        if ULTRA_FAST_MODE:
            primary, secondary = await asyncio.gather(*fetch_tasks)
            merged = secondary + primary
            files = merged[offset:offset + limit]
            has_next = len(files) > max_results
            if has_next:
                files = files[:-1]
            next_offset = offset + len(files) if has_next else ""
            total_results = offset + len(files) + (1 if has_next else 0)
            return files, next_offset, total_results

        primary_count, secondary_count, primary, secondary = await asyncio.gather(
            safe_count(Media), safe_count(Media2), *fetch_tasks
        )
        total_results = (primary_count or 0) + (secondary_count or 0)
        merged = secondary + primary
        files = merged[offset:offset + max_results]
        next_offset = offset + len(files)
        if next_offset >= total_results:
            next_offset = ""
        return files, next_offset, total_results

    # Single DB: database errors become an empty result, not a bot crash.
    try:
        if ULTRA_FAST_MODE:
            limit = max_results + 1
            files = await Media.find(mongo_filter).sort("$natural", -1).skip(offset).limit(limit).to_list(length=limit)
            has_next = len(files) > max_results
            if has_next:
                files = files[:-1]
            next_offset = offset + len(files) if has_next else ""
            total_results = offset + len(files) + (1 if has_next else 0)
            return files, next_offset, total_results

        total_results, files = await asyncio.gather(
            Media.count_documents(mongo_filter),
            Media.find(mongo_filter).sort("$natural", -1).skip(offset).limit(max_results).to_list(length=max_results),
        )
        next_offset = offset + len(files)
        if next_offset >= total_results:
            next_offset = ""
        return files, next_offset, total_results
    except Exception:
        logger.exception("Movie search failed; returning an empty result instead of propagating the database error")
        return [], "", 0


async def get_bad_files(query, file_type=None, offset=0, filter=False):
    mongo_filter = _build_filter(query)
    if mongo_filter is None:
        return [], 0
    if file_type:
        mongo_filter["file_type"] = file_type

    models = [Media]
    if MULTIPLE_DB and DATABASE_URI2:
        models.append(Media2)

    async def safe_fetch(model):
        try:
            return await model.find(mongo_filter).sort("$natural", -1).limit(300).to_list(300)
        except Exception:
            logger.exception("Bad-file query failed in one movie database")
            return []

    results = await asyncio.gather(*(safe_fetch(model) for model in models))
    files = []
    for result in results:
        files.extend(result)
    return files[:300], min(len(files), 300)


async def get_file_details(query):
    filt = {"file_id": query}
    models = [Media]
    if MULTIPLE_DB and DATABASE_URI2:
        models.append(Media2)
    for model in models:
        try:
            filedetails = await model.find(filt).to_list(length=1)
            if filedetails:
                return filedetails
        except Exception:
            logger.exception("File lookup failed in one movie database; trying the next")
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
