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


async def save_file(media):
    """Save files using IMDX's existing schema with DreamX-style optional DB routing."""
    file_id, file_ref = unpack_new_file_id(media.file_id)
    file_unique_id = getattr(media, "file_unique_id", None)
    file_name = re.sub(r"[_\-\.\+#$%^&*()!~`,;:\"?/<>{}\[\]=|\\]", " ", str(media.file_name))
    file_name = re.sub(r"\s+", " ", file_name).strip()

    duplicate_filter = {"_id": file_id}
    if file_unique_id:
        duplicate_filter = {"$or": [{"_id": file_id}, {"file_unique_id": file_unique_id}]}

    try:
        if await Media.find_one(duplicate_filter):
            print(f'{getattr(media, "file_name", "NO_FILE")} is already saved in database')
            return "dup"
        if MULTIPLE_DB and DATABASE_URI2 and await Media2.find_one(duplicate_filter):
            print(f'{getattr(media, "file_name", "NO_FILE")} is already saved in secondary database')
            return "dup"
    except Exception:
        # A failed duplicate check must not prevent indexing; Mongo will still enforce _id uniqueness.
        pass

    target_model = Media
    if MULTIPLE_DB and DATABASE_URI2:
        try:
            # Same practical routing idea as DreamX: once primary grows beyond the
            # configured threshold, new files go to the secondary database.
            threshold_mb = float(__import__('os').environ.get("PRIMARY_DB_MAX_MB", "407"))
            if await _db_size_mb(mydb) >= threshold_mb:
                target_model = Media2
        except Exception:
            target_model = Media

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
            raw_pattern = r"(\b|[\.\+\-_])" + re.escape(query) + r"(\b|[\.\+\-_])"
        else:
            raw_pattern = r".*[\s\.\+\-_]".join(re.escape(w) for w in query.split())
    try:
        regex = compile_regex(raw_pattern)
    except re.error:
        return None
    if USE_CAPTION_FILTER:
        return {"$or": [{"file_name": regex}, {"caption": regex}]}
    return {"file_name": regex}


async def get_search_results(query, max_results=MAX_BTN, offset=0, lang=None, chat_id=None, file_type=None, filter=False):
    """DreamX-style fast search, while preserving IMDX's original call signature."""
    mongo_filter = _build_filter(query)
    if mongo_filter is None:
        return [], "", 0
    if file_type:
        mongo_filter["file_type"] = file_type

    max_results = max(1, int(max_results or MAX_BTN))

    if MULTIPLE_DB and DATABASE_URI2:
        # Search both databases concurrently, exactly like DreamX.
        if ULTRA_FAST_MODE:
            limit = max_results + 1
            # First-page searches are the latency-critical path.  Avoid fetching
            # offset+limit documents from BOTH databases when offset is zero.
            # Later pages keep the old merged-pagination behavior unchanged.
            fetch_limit = limit if offset == 0 else offset + limit
            primary_task = Media.find(mongo_filter).sort("$natural", -1).limit(fetch_limit).to_list(length=fetch_limit)
            secondary_task = Media2.find(mongo_filter).sort("$natural", -1).limit(fetch_limit).to_list(length=fetch_limit)
            primary, secondary = await asyncio.gather(primary_task, secondary_task)
            merged = secondary + primary
            files = merged[offset:offset + limit]
            has_next = len(files) > max_results
            if has_next:
                files = files[:-1]
            next_offset = offset + len(files) if has_next else ""
            total_results = offset + len(files) + (1 if has_next else 0)
            return files, next_offset, total_results

        counts, found = await asyncio.gather(
            asyncio.gather(Media.count_documents(mongo_filter), Media2.count_documents(mongo_filter)),
            asyncio.gather(
                Media.find(mongo_filter).sort("$natural", -1).limit(offset + max_results).to_list(length=offset + max_results),
                Media2.find(mongo_filter).sort("$natural", -1).limit(offset + max_results).to_list(length=offset + max_results),
            ),
        )
        total_results = sum(counts)
        files = (found[1] + found[0])[offset:offset + max_results]
        next_offset = offset + len(files)
        if next_offset >= total_results:
            next_offset = ""
        return files, next_offset, total_results

    # Single DB: DreamX ultra-fast path avoids count_documents when enabled.
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


async def get_bad_files(query, file_type=None, offset=0, filter=False):
    mongo_filter = _build_filter(query)
    if mongo_filter is None:
        return [], 0
    if file_type:
        mongo_filter["file_type"] = file_type
    tasks = [Media.find(mongo_filter).sort("$natural", -1).to_list(300)]
    if MULTIPLE_DB and DATABASE_URI2:
        tasks.append(Media2.find(mongo_filter).sort("$natural", -1).to_list(300))
    results = await asyncio.gather(*tasks)
    files = results[1] + results[0] if len(results) > 1 else results[0]
    return files[:300], min(len(files), 300)


async def get_file_details(query):
    filt = {"file_id": query}
    tasks = [Media.find(filt).to_list(length=1)]
    if MULTIPLE_DB and DATABASE_URI2:
        tasks.append(Media2.find(filt).to_list(length=1))
    results = await asyncio.gather(*tasks)
    for filedetails in results:
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
