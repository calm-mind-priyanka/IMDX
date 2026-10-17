import logging
import asyncio
import datetime
import pytz
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

# from info import SETTINGS, IS_PM_SEARCH, IS_SEND_MOVIE_UPDATE, PREMIUM_POINT,REF_PREMIUM,IS_VERIFY, SHORTENER_WEBSITE3, SHORTENER_API3, THREE_VERIFY_GAP, LINK_MODE, FILE_CAPTION, TUTORIAL, DATABASE_NAME, DATABASE_URI, IMDB, IMDB_TEMPLATE, PROTECT_CONTENT, AUTO_DELETE, SPELL_CHECK, AUTO_FILTER, LOG_VR_CHANNEL, SHORTENER_WEBSITE, SHORTENER_API, SHORTENER_WEBSITE2, SHORTENER_API2, TWO_VERIFY_GAP
# from utils import get_seconds
from info import *


logger = logging.getLogger(__name__)


class _ResilientCursor:
    """Motor cursor wrapper that falls back to the mirrored backup on read errors."""
    def __init__(self, primary, backup=None):
        self.primary = primary
        self.backup = backup

    def _apply(self, method, *args, **kwargs):
        for cursor in (self.primary, self.backup):
            if cursor is not None:
                try:
                    getattr(cursor, method)(*args, **kwargs)
                except Exception:
                    logger.debug("Could not apply cursor option %s", method, exc_info=True)
        return self

    def sort(self, *args, **kwargs): return self._apply("sort", *args, **kwargs)
    def limit(self, *args, **kwargs): return self._apply("limit", *args, **kwargs)
    def skip(self, *args, **kwargs): return self._apply("skip", *args, **kwargs)

    async def to_list(self, length=None):
        try:
            return await self.primary.to_list(length=length)
        except Exception:
            logger.exception("Primary application-data cursor failed; trying backup")
            if self.backup is None:
                raise
            return await self.backup.to_list(length=length)

    async def _iterate(self):
        try:
            async for item in self.primary:
                yield item
            return
        except Exception:
            logger.exception("Primary application-data iteration failed; trying backup")
        if self.backup is not None:
            async for item in self.backup:
                yield item

    def __aiter__(self):
        return self._iterate()


class _ResilientCollection:
    """Read-fallback + dual-write wrapper with a replay journal for failover writes."""
    def __init__(self, primary, backup=None, journal=None):
        self.primary = primary
        self.backup = backup
        self.journal = journal

    async def _journal_write(self, method, args, kwargs):
        if self.journal is None:
            return
        if method not in {"update_one", "update_many", "delete_one", "delete_many", "replace_one", "insert_one", "find_one_and_update"}:
            return
        try:
            clean_kwargs = {k: v for k, v in kwargs.items() if k != "return_document"}
            await self.journal.insert_one({
                "collection": self.primary.name,
                "method": method,
                "args": list(args),
                "kwargs": clean_kwargs,
                "created_at": datetime.datetime.utcnow(),
            })
        except Exception:
            logger.exception("Could not journal application-data failover write for %s", self.primary.name)

    async def find_one(self, *args, **kwargs):
        try:
            return await self.primary.find_one(*args, **kwargs)
        except Exception:
            logger.exception("Primary application-data read failed; trying backup")
            if self.backup is None:
                raise
            return await self.backup.find_one(*args, **kwargs)

    def find(self, *args, **kwargs):
        primary_cursor = self.primary.find(*args, **kwargs)
        backup_cursor = self.backup.find(*args, **kwargs) if self.backup is not None else None
        return _ResilientCursor(primary_cursor, backup_cursor)

    def aggregate(self, *args, **kwargs):
        primary_cursor = self.primary.aggregate(*args, **kwargs)
        backup_cursor = self.backup.aggregate(*args, **kwargs) if self.backup is not None else None
        return _ResilientCursor(primary_cursor, backup_cursor)

    async def count_documents(self, *args, **kwargs):
        try:
            return await self.primary.count_documents(*args, **kwargs)
        except Exception:
            logger.exception("Primary application-data count failed; trying backup")
            if self.backup is None: raise
            return await self.backup.count_documents(*args, **kwargs)

    async def _write(self, method, *args, **kwargs):
        primary_error = None
        result = None
        try:
            result = await getattr(self.primary, method)(*args, **kwargs)
        except Exception as exc:
            primary_error = exc
            logger.exception("Primary application-data write %s failed", method)
        if primary_error is None and method == "find_one_and_update" and result is None:
            # Preserve conditional/atomic semantics: do not let a stale backup
            # match a filter that no longer matches the primary.
            return None
        backup_result = None
        backup_error = None
        if self.backup is not None:
            try:
                backup_result = await getattr(self.backup, method)(*args, **kwargs)
            except Exception as exc:
                backup_error = exc
                logger.exception("Backup application-data mirror write %s failed", method)
        if primary_error is not None:
            if backup_error is None and self.backup is not None:
                await self._journal_write(method, args, kwargs)
                return backup_result
            raise primary_error
        return result

    async def insert_one(self, document, *args, **kwargs):
        primary_error = None
        result = None
        try:
            result = await self.primary.insert_one(document, *args, **kwargs)
        except Exception as exc:
            primary_error = exc
            logger.exception("Primary application-data insert failed")
        backup_result = None
        backup_error = None
        if self.backup is not None:
            try:
                if isinstance(document, dict) and "_id" in document:
                    backup_result = await self.backup.replace_one({"_id": document["_id"]}, document, upsert=True)
                else:
                    backup_result = await self.backup.insert_one(document, *args, **kwargs)
            except Exception as exc:
                backup_error = exc
                logger.exception("Backup application-data insert mirror failed")
        if primary_error is not None:
            if self.backup is not None and backup_error is None:
                await self._journal_write("insert_one", (document, *args), kwargs)
                return backup_result
            raise primary_error
        return result

    async def update_one(self, *args, **kwargs): return await self._write("update_one", *args, **kwargs)
    async def update_many(self, *args, **kwargs): return await self._write("update_many", *args, **kwargs)
    async def delete_one(self, *args, **kwargs): return await self._write("delete_one", *args, **kwargs)
    async def delete_many(self, *args, **kwargs): return await self._write("delete_many", *args, **kwargs)
    async def find_one_and_update(self, *args, **kwargs): return await self._write("find_one_and_update", *args, **kwargs)
    async def replace_one(self, *args, **kwargs): return await self._write("replace_one", *args, **kwargs)

    async def create_index(self, *args, **kwargs):
        result = await self.primary.create_index(*args, **kwargs)
        if self.backup is not None:
            try: await self.backup.create_index(*args, **kwargs)
            except Exception: logger.exception("Could not mirror application-data index")
        return result

    async def drop(self):
        result = await self.primary.drop()
        if self.backup is not None:
            try: await self.backup.drop()
            except Exception: logger.exception("Could not mirror collection drop")
        return result

    def __getattr__(self, name):
        # Keep compatibility with the few less-common Motor collection methods.
        attr = getattr(self.primary, name)
        if not callable(attr): return attr
        async def invoke(*args, **kwargs):
            try:
                result = await attr(*args, **kwargs)
            except Exception:
                logger.exception("Primary application-data operation %s failed", name)
                if self.backup is None: raise
                return await getattr(self.backup, name)(*args, **kwargs)
            if self.backup is not None and name in {"bulk_write", "insert_many"}:
                try: await getattr(self.backup, name)(*args, **kwargs)
                except Exception: logger.exception("Could not mirror application-data operation %s", name)
            return result
        return invoke


client = AsyncIOMotorClient(DATABASE_URI, serverSelectionTimeoutMS=5000)
mydb = client[DATABASE_NAME]
_backup_uri = str(globals().get("APP_DATA_BACKUP_URI", "") or "").strip()
_backup_name = str(globals().get("APP_DATA_BACKUP_NAME", f"{DATABASE_NAME}_backup") or f"{DATABASE_NAME}_backup")
_backup_client = AsyncIOMotorClient(_backup_uri, serverSelectionTimeoutMS=2000) if _backup_uri and (_backup_uri != DATABASE_URI or _backup_name != DATABASE_NAME) else None
_backup_db = _backup_client[_backup_name] if _backup_client is not None else None


class Database:
    def __init__(self):
        self._primary_db = mydb
        self._backup_db = _backup_db
        self.col = self._collection("users")
        self.grp = self._collection("groups")
        self.misc = self._collection("misc")
        self.verify_id = self._collection("verify_id")
        self.users = self._collection("uersz")
        self.req = self._collection("requests")
        self.mGrp = self._collection("mGrp")
        self.pmMode = self._collection("pmMode")
        self.jisshu_ads_link = self._collection("jisshu_ads_link")
        self.movies_update_channel = self._collection("movies_update_channel")
        self.botcol = self._collection("botcol")
        self.premium_orders = self._collection("premium_orders")
        self.payment_submissions = self._collection("payment_submissions")
        self.support_feedback = self._collection("support_feedback")
        self.ad_cycle_verify = self._collection("ad_cycle_verify")
        self.ad_cycle_state = self._collection("ad_cycle_state")

    def _collection(self, name):
        primary = self._primary_db[name]
        backup = self._backup_db[name] if self._backup_db is not None else None
        journal = self._backup_db["_app_data_recovery_journal"] if self._backup_db is not None else None
        return _ResilientCollection(primary, backup, journal)

    async def initialize_backup(self):
        """Seed and refresh the backup from primary before normal bot startup.

        Every subsequent application-data write is mirrored. This startup copy
        also brings pre-existing users/groups/settings into the backup. It is
        intentionally best-effort so an unavailable backup never blocks startup.
        """
        if self._backup_db is None:
            return False
        try:
            await self._backup_db.command("ping")
        except Exception:
            logger.exception("Application-data backup is unavailable; continuing with primary only")
            return False
        try:
            names = await self._primary_db.list_collection_names()
        except Exception:
            logger.exception("Primary data store unavailable during backup sync; continuing with failover reads")
            return False
        # Replay writes accepted by the backup while the primary was down/full.
        journal = self._backup_db["_app_data_recovery_journal"]
        try:
            pending = await journal.find({}).sort("created_at", 1).to_list(length=1000)
        except Exception:
            logger.exception("Could not read application-data recovery journal")
            pending = []
        for entry in pending:
            try:
                collection_name = str(entry.get("collection") or "")
                method = str(entry.get("method") or "")
                args = list(entry.get("args") or [])
                kwargs = dict(entry.get("kwargs") or {})
                target = self._primary_db[collection_name]
                if method == "insert_one" and args and isinstance(args[0], dict) and "_id" in args[0]:
                    await target.replace_one({"_id": args[0]["_id"]}, args[0], upsert=True)
                elif method in {"update_one", "update_many", "delete_one", "delete_many", "replace_one", "find_one_and_update"}:
                    await getattr(target, method)(*args, **kwargs)
                else:
                    continue
                await journal.delete_one({"_id": entry["_id"]})
            except Exception:
                logger.exception("Could not replay recovery journal entry for %s", entry.get("collection"))
        for name in names:
            try:
                source = self._primary_db[name]
                target = self._backup_db[name]
                cursor = source.find({})
                async for document in cursor:
                    await target.replace_one({"_id": document["_id"]}, document, upsert=True)
            except Exception:
                logger.exception("Could not synchronize application-data collection %s", name)
        return True

    async def backup_sync_loop(self, interval=600):
        """Periodically reconcile primary and backup and replay failover writes."""
        while self._backup_db is not None:
            await asyncio.sleep(max(60, int(interval)))
            try:
                await self.initialize_backup()
            except Exception:
                logger.exception("Periodic application-data backup reconciliation failed")

    async def get_ad_cycle_state(self, user_id, group_id):
        return await self.ad_cycle_state.find_one({"user_id": int(user_id), "group_id": int(group_id)})

    async def create_ad_cycle_verification(self, token, user_id, group_id, delivery_key, step, required_steps, mode="file"):
        now = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        # A fresh movie request invalidates older unfinished links for this
        # user/group. This prevents old links being replayed in a later cycle.
        await self.ad_cycle_verify.update_many(
            {"user_id": int(user_id), "group_id": int(group_id), "verified": False, "superseded": {"$ne": True}},
            {"$set": {"superseded": True, "superseded_at": now}},
        )
        await self.ad_cycle_verify.insert_one({
            "token": str(token), "user_id": int(user_id), "group_id": int(group_id),
            "delivery_key": str(delivery_key), "mode": str(mode or "file"), "step": int(step),
            "required_steps": int(required_steps), "created_at": now,
            "verified": False, "shortlink": "", "clicked_at": None,
        })

    async def get_ad_cycle_verification(self, token):
        return await self.ad_cycle_verify.find_one({"token": str(token)})

    async def update_ad_cycle_verification(self, token, values):
        return await self.ad_cycle_verify.update_one({"token": str(token)}, {"$set": values})

    async def complete_ad_cycle_verification(self, token, user_id):
        """Accept only the current expected step and advance the cycle atomically."""
        now = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        uid = int(user_id)
        preview = await self.ad_cycle_verify.find_one({"token": str(token), "user_id": uid})
        if not preview or preview.get("verified") or preview.get("superseded"):
            return None, None
        gid = int(preview.get("group_id") or 0)
        state_query = {"user_id": uid, "group_id": gid}
        state = await self.ad_cycle_state.find_one(state_query)
        if state:
            access_until = state.get("access_until")
            if access_until is not None:
                if getattr(access_until, "tzinfo", None) is None:
                    access_until = access_until.replace(tzinfo=pytz.UTC)
                else:
                    access_until = access_until.astimezone(pytz.UTC)
                if access_until > now.astimezone(pytz.UTC):
                    return None, None
        completed = max(0, int((state or {}).get("step_count", 0)))
        expected_step = completed + 1
        required = max(2, min(3, int(preview.get("required_steps", 3))))
        if int(preview.get("step", 0)) != expected_step or expected_step > required:
            # A stale link from a prior request cannot increment the counter twice.
            return None, None

        # Ensure the state document exists before using a conditional transition.
        await self.ad_cycle_state.update_one(
            state_query,
            {"$setOnInsert": {"user_id": uid, "group_id": gid, "step_count": 0}},
            upsert=True,
        )
        record = await self.ad_cycle_verify.find_one_and_update(
            {"token": str(token), "user_id": uid, "verified": False, "superseded": {"$ne": True}, "step": expected_step},
            {"$set": {"verified": True, "verified_at": now}},
            return_document=ReturnDocument.AFTER,
        )
        if not record:
            return None, None

        count = completed + 1
        update = {
            "user_id": uid, "group_id": gid,
            "step_count": 0 if count >= required else count,
            "bypass_nonce": str(token),
            "bypass_expires_at": now + datetime.timedelta(minutes=15),
            "updated_at": now,
        }
        finished = count >= required
        if finished:
            update.update({"last_completed_count": required,
                           "access_until": now + datetime.timedelta(hours=24)})
        state_after = await self.ad_cycle_state.find_one_and_update(
            {"user_id": uid, "group_id": gid, "step_count": completed},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if not state_after:
            # A concurrent completion won the step. Release this token rather
            # than falsely recording a successful extra step.
            await self.ad_cycle_verify.update_one(
                {"token": str(token)},
                {"$set": {"verified": False}, "$unset": {"verified_at": ""}},
            )
            return None, None
        return record, {**update, "finished": finished, "completed_count": count, "required_steps": required}

    async def consume_ad_cycle_delivery_bypass(self, user_id, group_id, nonce):
        now = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        state = await self.ad_cycle_state.find_one_and_update(
            {"user_id": int(user_id), "group_id": int(group_id),
             "bypass_nonce": str(nonce), "bypass_expires_at": {"$gt": now}},
            {"$unset": {"bypass_nonce": "", "bypass_expires_at": ""}},
            return_document=ReturnDocument.BEFORE,
        )
        return bool(state)

    default = {
        "spell_check": SPELL_CHECK,
        "auto_filter": AUTO_FILTER,
        "file_secure": PROTECT_CONTENT,
        "auto_delete": AUTO_DELETE,
        "template": IMDB_TEMPLATE,
        "caption": FILE_CAPTION,
        "tutorial": TUTORIAL,
        "tutorial_2": TUTORIAL_2,
        "tutorial_3": TUTORIAL_3,
        "shortner": SHORTENER_WEBSITE,
        "api": SHORTENER_API,
        "shortner_two": SHORTENER_WEBSITE2,
        "api_two": SHORTENER_API2,
        "log": LOG_VR_CHANNEL,
        "imdb": IMDB,
        "fsub_id": AUTH_CHANNEL,
        "link": LINK_MODE,
        "is_verify": IS_VERIFY,
        "file_mode": False,
        "file_mode_type": "verify",
        "verify_time": TWO_VERIFY_GAP,
        "shortner_three": SHORTENER_WEBSITE3,
        "api_three": SHORTENER_API3,
        "third_verify_time": THREE_VERIFY_GAP,
        # Settings added by the master settings layer; old groups fall back to these values.
        "max_results": MAX_BTN,
        "delete_time": DELETE_TIME,
        "request_channel": REQUEST_CHANNEL,
        "fsub_channels": [AUTH_CHANNEL],
        # Separate 2/3-step shortener; independent from the legacy shortener keys.
        "ad_cycle_enabled": False,
        "ad_cycle_steps": 3,
        "ad_cycle_domain_1": "",
        "ad_cycle_api_1": "",
        "ad_cycle_domain_2": "",
        "ad_cycle_api_2": "",
        "ad_cycle_domain_3": "",
        "ad_cycle_api_3": "",
        "ad_cycle_tutorial": "",
    }

    def new_user(self, id, name):
        return dict(
            id=id, name=name, point=0, ban_status=dict(is_banned=False, ban_reason="")
        )

    async def get_settings(self, group_id):
        chat = await self.grp.find_one({"id": int(group_id)})
        if chat and "settings" in chat:
            return chat["settings"]
        else:
            return self.default.copy()

    async def get_owner_shortener_pairs(self):
        """Return only shortener slots registered to the current BOT OWNER_ID."""
        doc = await self.misc.find_one({"_id": "owner_shorteners"})
        if doc:
            try:
                if int(doc.get("owner_id")) != int(OWNER_ID):
                    return [("", ""), ("", ""), ("", "")]
            except (TypeError, ValueError):
                return [("", ""), ("", ""), ("", "")]
            if isinstance(doc.get("pairs"), list):
                pairs = []
                for item in doc["pairs"][:3]:
                    if isinstance(item, dict):
                        pairs.append((str(item.get("domain", "")).strip(), str(item.get("api", "")).strip()))
                    else:
                        pairs.append(("", ""))
                while len(pairs) < 3:
                    pairs.append(("", ""))
                return pairs
        # Initial environment shorteners belong to the bot owner.
        return [
            (str(SHORTENER_WEBSITE).strip(), str(SHORTENER_API).strip()),
            (str(SHORTENER_WEBSITE2).strip(), str(SHORTENER_API2).strip()),
            (str(SHORTENER_WEBSITE3).strip(), str(SHORTENER_API3).strip()),
        ]

    async def set_owner_shortener(self, slot, domain, api):
        """Persist one owner shortener slot (1, 2 or 3)."""
        slot = int(slot)
        if slot not in (1, 2, 3):
            raise ValueError("owner shortener slot must be 1, 2 or 3")
        pairs = await self.get_owner_shortener_pairs()
        while len(pairs) < 3:
            pairs.append(("", ""))
        pairs[slot - 1] = (str(domain or "").strip(), str(api or "").strip())
        await self.misc.update_one(
            {"_id": "owner_shorteners"},
            {"$set": {
                "pairs": [
                    {"domain": d, "api": a} for d, a in pairs
                ],
                "owner_id": int(OWNER_ID),
            }},
            upsert=True,
        )
        return pairs

    async def find_join_req(self, id):
        return bool(await self.req.find_one({"id": id}))

    async def add_join_req(self, id):
        await self.req.insert_one({"id": id})

    async def del_join_req(self):
        await self.req.drop()

    def new_group(self, id, title):
        return dict(id=id, title=title, chat_status=dict(is_disabled=False, reason=""))

    async def add_user(self, id, name):
        user = self.new_user(id, name)
        await self.col.insert_one(user)

    async def update_point(self, id):
        await self.col.update_one({"id": id}, {"$inc": {"point": 100}})
        point = (await self.col.find_one({"id": id}))["point"]
        if point >= PREMIUM_POINT:
            seconds = REF_PREMIUM * 24 * 60 * 60
            oldEx = await self.users.find_one({"id": id})
            if oldEx:
                expiry_time = oldEx["expiry_time"] + datetime.timedelta(seconds=seconds)
            else:
                expiry_time = datetime.datetime.now() + datetime.timedelta(
                    seconds=seconds
                )
            user_data = {"id": id, "expiry_time": expiry_time}
            await db.update_user(user_data)
            await self.col.update_one({"id": id}, {"$set": {"point": 0}})

    async def get_point(self, id):
        newPoint = await self.col.find_one({"id": id})
        return newPoint["point"] if newPoint else None

    async def is_user_exist(self, id):
        user = await self.col.find_one({"id": int(id)})
        return bool(user)

    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count

    async def get_all_users(self):
        return self.col.find({})

    async def delete_user(self, user_id):
        await self.col.delete_many({"id": int(user_id)})

    async def delete_chat(self, id):
        await self.grp.delete_many({"id": int(id)})

    async def get_banned(self):
        users = self.col.find({"ban_status.is_banned": True})
        chats = self.grp.find({"chat_status.is_disabled": True})
        b_chats = [chat["id"] async for chat in chats]
        b_users = [user["id"] async for user in users]
        return b_users, b_chats

    async def add_chat(self, chat, title):
        chat = self.new_group(chat, title)
        await self.grp.insert_one(chat)

    async def get_chat(self, chat):
        chat = await self.grp.find_one({"id": int(chat)})
        return False if not chat else chat.get("chat_status")

    async def update_settings(self, id, settings):
        # Upsert prevents settings silently disappearing when a group document
        # has not yet been created by the normal chat-registration handler.
        await self.grp.update_one(
            {"id": int(id)}, {"$set": {"settings": settings}}, upsert=True
        )

    async def total_chat_count(self):
        count = await self.grp.count_documents({})
        return count

    async def get_all_chats(self):
        return self.grp.find({})

    async def get_db_size(self):
        try:
            return (await mydb.command("dbstats"))["dataSize"]
        except Exception:
            if self._backup_db is not None:
                try:
                    return (await self._backup_db.command("dbstats"))["dataSize"]
                except Exception:
                    logger.exception("Primary and backup application-data stats failed")
            raise

    async def get_notcopy_user(self, user_id):
        user_id = int(user_id)
        user = await self.misc.find_one({"user_id": user_id})
        ist_timezone = pytz.timezone("Asia/Kolkata")
        if not user:
            res = {
                "user_id": user_id,
                "last_verified": datetime.datetime(
                    2020, 5, 17, 0, 0, 0, tzinfo=ist_timezone
                ),
                "second_time_verified": datetime.datetime(
                    2019, 5, 17, 0, 0, 0, tzinfo=ist_timezone
                ),
            }
            user = await self.misc.insert_one(res)
        return user

    async def update_notcopy_user(self, user_id, value: dict):
        user_id = int(user_id)
        myquery = {"user_id": user_id}
        newvalues = {"$set": value}
        return await self.misc.update_one(myquery, newvalues)

    async def is_user_verified(self, user_id):
        """Return whether the Step-1/master verification is still inside its 24h window.

        This is intentionally a rolling 24-hour window from the successful
        Step-1 verification time. It is NOT reset at midnight. Step 2 and Step 3
        keep their existing independent gap logic below.
        """
        user = await self.get_notcopy_user(user_id)
        try:
            past_date = user["last_verified"]
        except Exception:
            user = await self.get_notcopy_user(user_id)
            past_date = user["last_verified"]

        ist_timezone = pytz.timezone("Asia/Kolkata")
        past_date = past_date.astimezone(ist_timezone)
        current_time = datetime.datetime.now(tz=ist_timezone)
        time_diff = current_time - past_date

        # Step 1 is the master recycle window: exactly 24 hours from the
        # successful Step-1 callback. Do not use calendar-day/midnight logic.
        return datetime.timedelta(0) <= time_diff <= datetime.timedelta(hours=24)

    async def user_verified(self, user_id):
        user = await self.get_notcopy_user(user_id)
        try:
            pastDate = user["second_time_verified"]
        except Exception:
            user = await self.get_notcopy_user(user_id)
            pastDate = user["second_time_verified"]
        ist_timezone = pytz.timezone("Asia/Kolkata")
        pastDate = pastDate.astimezone(ist_timezone)
        current_time = datetime.datetime.now(tz=ist_timezone)
        seconds_since_midnight = (
            current_time
            - datetime.datetime(
                current_time.year,
                current_time.month,
                current_time.day,
                0,
                0,
                0,
                tzinfo=ist_timezone,
            )
        ).total_seconds()
        time_diff = current_time - pastDate
        total_seconds = time_diff.total_seconds()
        return total_seconds <= seconds_since_midnight

    async def use_second_shortener(self, user_id, time):
        user = await self.get_notcopy_user(user_id)
        if not user.get("second_time_verified"):
            ist_timezone = pytz.timezone("Asia/Kolkata")
            await self.update_notcopy_user(
                user_id,
                {
                    "second_time_verified": datetime.datetime(
                        2019, 5, 17, 0, 0, 0, tzinfo=ist_timezone
                    )
                },
            )
            user = await self.get_notcopy_user(user_id)
        if await self.is_user_verified(user_id):
            try:
                pastDate = user["last_verified"]
            except Exception:
                user = await self.get_notcopy_user(user_id)
                pastDate = user["last_verified"]
            ist_timezone = pytz.timezone("Asia/Kolkata")
            pastDate = pastDate.astimezone(ist_timezone)
            current_time = datetime.datetime.now(tz=ist_timezone)
            time_difference = current_time - pastDate
            if time_difference > datetime.timedelta(seconds=time):
                pastDate = user["last_verified"].astimezone(ist_timezone)
                second_time = user["second_time_verified"].astimezone(ist_timezone)
                return second_time < pastDate
        return False

    async def use_third_shortener(self, user_id, time):
        user = await self.get_notcopy_user(user_id)
        if not user.get("third_time_verified"):
            ist_timezone = pytz.timezone("Asia/Kolkata")
            await self.update_notcopy_user(
                user_id,
                {
                    "third_time_verified": datetime.datetime(
                        2018, 5, 17, 0, 0, 0, tzinfo=ist_timezone
                    )
                },
            )
            user = await self.get_notcopy_user(user_id)
        if await self.user_verified(user_id):
            try:
                pastDate = user["second_time_verified"]
            except Exception:
                user = await self.get_notcopy_user(user_id)
                pastDate = user["second_time_verified"]
            ist_timezone = pytz.timezone("Asia/Kolkata")
            pastDate = pastDate.astimezone(ist_timezone)
            current_time = datetime.datetime.now(tz=ist_timezone)
            time_difference = current_time - pastDate
            if time_difference > datetime.timedelta(seconds=time):
                pastDate = user["second_time_verified"].astimezone(ist_timezone)
                second_time = user["third_time_verified"].astimezone(ist_timezone)
                return second_time < pastDate
        return False

    async def create_verify_id(self, user_id: int, hash, **metadata):
        # A new verification attempt supersedes older unfinished attempts for the
        # same user/group. This prevents stale reminders from older file requests.
        group_id = int(metadata.get("group_id") or 0)
        await self.verify_id.update_many(
            {
                "user_id": int(user_id),
                "group_id": group_id,
                "verified": False,
                "superseded_at": {"$exists": False},
            },
            {"$set": {"superseded_at": datetime.datetime.now(pytz.timezone("Asia/Kolkata"))}},
        )
        res = {
            "user_id": int(user_id),
            "hash": hash,
            "verified": False,
            "shortlink_clicked": False,
            "return_accepted": False,
            **metadata,
        }
        return await self.verify_id.insert_one(res)

    async def get_pending_verification_recovery(self, cutoff, stage="generated"):
        base_query = {
            "verified": False,
            "superseded_at": {"$exists": False},
            "shortlink": {"$exists": True, "$ne": ""},
        }
        if stage == "midflow":
            base_query.update({
                "shortlink_clicked_at": {"$exists": True, "$lte": cutoff},
                "midflow_recovery_sent_at": {"$exists": False},
            })
        else:
            base_query.update({
                "shortlink_created_at": {"$exists": True, "$lte": cutoff},
                "shortlink_clicked_at": {"$exists": False},
                "recovery_sent_at": {"$exists": False},
            })
        cursor = self.verify_id.find(base_query).sort("shortlink_created_at", -1).limit(200)
        return await cursor.to_list(length=200)

    async def claim_verification_recovery(self, record_id, now, stage="generated"):
        field = "recovery_sent_at" if stage == "generated" else "midflow_recovery_sent_at"
        query = {
            "_id": record_id,
            "verified": False,
            "superseded_at": {"$exists": False},
            field: {"$exists": False},
        }
        if stage == "midflow":
            query["shortlink_clicked_at"] = {"$exists": True}
        else:
            query["shortlink_clicked_at"] = {"$exists": False}
        return await self.verify_id.find_one_and_update(
            query,
            {"$set": {field: now}},
            return_document=ReturnDocument.AFTER,
        )

    async def get_verify_id_by_hash_for_recovery(self, hash):
        return await self.verify_id.find_one({"hash": str(hash)})

    async def create_support_feedback_session(self, user_id, verify_id, group_id, step, log_chat):
        return await self.support_feedback.update_one(
            {"user_id": int(user_id)},
            {"$set": {
                "user_id": int(user_id), "verify_id": str(verify_id),
                "group_id": int(group_id), "step": int(step),
                "log_chat": int(log_chat), "created_at": datetime.datetime.now(pytz.timezone("Asia/Kolkata")),
            }},
            upsert=True,
        )

    async def get_support_feedback_session(self, user_id):
        return await self.support_feedback.find_one({"user_id": int(user_id)})

    async def clear_support_feedback_session(self, user_id):
        return await self.support_feedback.delete_one({"user_id": int(user_id)})

    async def get_latest_verification_for_delivery(self, user_id: int, group_id: int, delivery_key: str):
        """Return the newest verification record for this user/file delivery."""
        return await self.verify_id.find_one(
            {
                "user_id": int(user_id),
                "group_id": int(group_id),
                "delivery_key": str(delivery_key),
            },
            sort=[("created_at", -1)],
        )

    async def get_verify_id_info(self, user_id: int, hash):
        return await self.verify_id.find_one({"user_id": user_id, "hash": hash})

    async def get_verify_id_by_return_token(self, return_token: str):
        """Resolve a short verification-return token to its server-side state."""
        return await self.verify_id.find_one({"return_token": str(return_token)})

    async def update_verify_id_info(self, user_id, hash, value: dict):
        myquery = {"user_id": user_id, "hash": hash}
        newvalues = {"$set": value}
        return await self.verify_id.update_one(myquery, newvalues)

    async def get_user(self, user_id):
        user_data = await self.users.find_one({"id": user_id})
        return user_data

    async def remove_ban(self, id):
        ban_status = dict(is_banned=False, ban_reason="")
        await self.col.update_one({"id": id}, {"$set": {"ban_status": ban_status}})

    async def ban_user(self, user_id, ban_reason="No Reason"):
        ban_status = dict(is_banned=True, ban_reason=ban_reason)
        await self.col.update_one({"id": user_id}, {"$set": {"ban_status": ban_status}})

    async def get_ban_status(self, id):
        default = dict(is_banned=False, ban_reason="")
        user = await self.col.find_one({"id": int(id)})
        if not user:
            return default
        return user.get("ban_status", default)

    async def update_user(self, user_data):
        await self.users.update_one(
            {"id": user_data["id"]}, {"$set": user_data}, upsert=True
        )

    async def get_expired(self, current_time):
        expired_users = []
        if data := self.users.find({"expiry_time": {"$lt": current_time}}):
            async for user in data:
                expired_users.append(user)
        return expired_users

    async def has_premium_access(self, user_id):
        user_data = await self.get_user(user_id)
        if user_data:
            expiry_time = user_data.get("expiry_time")
            if expiry_time is None:
                # User previously used the free trial, but it has ended.
                return False
            elif (
                isinstance(expiry_time, datetime.datetime)
                and (
                    (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) <= expiry_time)
                    if expiry_time.tzinfo is None
                    else datetime.datetime.now(datetime.timezone.utc) <= expiry_time
                )
            ):
                return True
            else:
                await self.users.update_one(
                    {"id": user_id}, {"$set": {"expiry_time": None}}
                )
        return False

    async def check_remaining_uasge(self, user_id):
        user_id = user_id
        user_data = await self.get_user(user_id)
        expiry_time = user_data.get("expiry_time")
        # Calculate remaining time
        remaining_time = expiry_time - datetime.datetime.now()
        return remaining_time

    async def all_premium_users(self):
        count = await self.users.count_documents(
            {"expiry_time": {"$gt": datetime.datetime.now()}}
        )
        return count

    async def update_one(self, filter_query, update_data):
        try:
            # Assuming self.client and self.users are set up properly
            result = await self.users.update_one(filter_query, update_data)
            return result.matched_count == 1
        except Exception as e:
            print(f"Error updating document: {e}")
            return False

    async def remove_premium_access(self, user_id):
        return await self.update_one({"id": user_id}, {"$set": {"expiry_time": None}})

    async def check_trial_status(self, user_id):
        user_data = await self.get_user(user_id)
        if user_data:
            return user_data.get("has_free_trial", False)
        return False

    # Free Trail Remove Logic
    async def reset_free_trial(self, user_id=None):
        if user_id is None:
            # Reset for all users
            update_data = {"$set": {"has_free_trial": False}}
            result = await self.users.update_many(
                {}, update_data
            )  # Empty query to match all users
            return result.modified_count
        else:
            # Reset for a specific user
            update_data = {"$set": {"has_free_trial": False}}
            result = await self.users.update_one({"id": user_id}, update_data)
            return (
                1 if result.modified_count > 0 else 0
            )  # Return 1 if updated, 0 if not

    async def give_free_trial(self, user_id):
        # await set_free_trial_status(user_id)
        user_id = user_id
        seconds = 5 * 60
        expiry_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)
        user_data = {"id": user_id, "expiry_time": expiry_time, "has_free_trial": True}
        await self.users.update_one({"id": user_id}, {"$set": user_data}, upsert=True)


    # ------------------------------------------------------------------
    # Premium payment / subscription helpers
    # ------------------------------------------------------------------
    async def ensure_verification_indexes(self):
        """Create safe non-unique indexes used by verification tracking/recovery."""
        indexes = [
            [("return_token", 1)],
            [("hash", 1)],
            [("user_id", 1), ("group_id", 1), ("created_at", -1)],
            [("verified", 1), ("shortlink_created_at", 1), ("shortlink_clicked_at", 1)],
        ]
        for keys in indexes:
            try:
                await self.verify_id.create_index(keys)
            except Exception:
                # Index creation must never prevent the bot from starting.
                logging.exception("Could not create verification index: %s", keys)
        for collection, keys in (
            (self.ad_cycle_verify, [("token", 1)]),
            (self.ad_cycle_state, [("user_id", 1), ("group_id", 1)]),
        ):
            try:
                await collection.create_index(keys, unique=(keys == [("token", 1)]))
            except Exception:
                logging.exception("Could not create independent ad-cycle index: %s", keys)

    async def ensure_premium_indexes(self):
        await self.premium_orders.create_index("user_id", unique=True)
        await self.premium_orders.create_index([
            ("payment_status", 1), ("premium_status", 1), ("expires_at", 1)
        ])
        await self.payment_submissions.create_index(
            [("payment_chat_id", 1), ("payment_bot_message_id", 1)],
            unique=True,
        )
        await self.payment_submissions.create_index("user_id")
        await self.payment_submissions.create_index("file_sha256")

    async def create_or_update_premium_order(self, user_id, username, plan,
                                             plan_duration, plan_price):
        """Create the user's current pending order.
        Telegram user_id is the stable key; username is informational only.
        """
        now = datetime.datetime.utcnow()
        document = {
            "user_id": int(user_id),
            "username": username or "",
            "selected_plan": plan,
            "plan_duration": plan_duration,
            "plan_price": plan_price,
            "order_created_at": now,
            "screenshot_message_id": None,
            "screenshot_received_at": None,
            "payment_status": "waiting_for_payment",
            "premium_status": "inactive",
            "activated_at": None,
            "expires_at": None,
            "reminder_sent": False,
            "manually_verified": False,
            "manually_verified_at": None,
        }
        # A user has one current pending order. Selecting another plan replaces
        # the old pending selection, which prevents an old price/duration being
        # activated by a later screenshot.
        await self.premium_orders.update_one(
            {"user_id": int(user_id)},
            {"$set": document},
            upsert=True,
        )
        return await self.premium_orders.find_one({"user_id": int(user_id)})

    async def get_pending_premium_order(self, user_id):
        return await self.premium_orders.find_one({
            "user_id": int(user_id),
            "payment_status": "waiting_for_payment",
        })

    async def record_payment_submission(self, data):
        """Record one Telegram screenshot message exactly once.

        The payment bot can occasionally dispatch the same update more than
        once. The unique (chat_id, message_id) index is the source of truth,
        so a repeated delivery is treated as an already-processed submission
        instead of creating another user/admin warning.
        """
        try:
            result = await self.payment_submissions.insert_one(data)
            return True
        except DuplicateKeyError:
            return False


    async def update_payment_submission(self, user_id, message_id, data):
        return await self.payment_submissions.update_one(
            {"user_id": int(user_id), "payment_bot_message_id": int(message_id)},
            {"$set": data},
        )

    async def claim_payment_review(self, user_id, message_id, decision):
        """Atomically claim one exact screenshot review. Independent of order status."""
        now = datetime.datetime.utcnow()
        return await self.payment_submissions.update_one(
            {
                "user_id": int(user_id),
                "payment_bot_message_id": int(message_id),
                "review_status": {"$in": ["pending", "manual_review_required"]},
            },
            {"$set": {
                "review_status": decision,
                "reviewed_at": now,
            }},
        )

    async def get_payment_submission(self, user_id, message_id):
        return await self.payment_submissions.find_one({
            "user_id": int(user_id),
            "payment_bot_message_id": int(message_id),
        })

    async def find_duplicate_payment_submission(self, file_sha256, perceptual_hash, user_id, message_id):
        if file_sha256:
            row = await self.payment_submissions.find_one({
                "file_sha256": file_sha256,
                "$or": [{"user_id": {"$ne": int(user_id)}}, {"payment_bot_message_id": {"$ne": int(message_id)}}],
            })
            if row:
                return row
        if not perceptual_hash:
            return None
        # Compare a recent bounded set so near-identical crops/resizes can be flagged
        # without scanning an unbounded collection. Flag for manual review, never auto-reject.
        cursor = self.payment_submissions.find({"perceptual_hash": {"$exists": True}}).sort("received_at", -1).limit(300)
        async for row in cursor:
            other = row.get("perceptual_hash")
            if not other or row.get("user_id") == int(user_id) and row.get("payment_bot_message_id") == int(message_id):
                continue
            try:
                distance = sum(a != b for a, b in zip(bin(int(perceptual_hash, 16))[2:].zfill(1024), bin(int(other, 16))[2:].zfill(1024)))
                if distance <= 2:
                    return row
            except Exception:
                continue
        return None

    async def update_order_payment_review(self, user_id, message_id, check):
        return await self.premium_orders.update_one(
            {"user_id": int(user_id), "payment_status": "waiting_for_payment"},
            {"$set": {
                "screenshot_message_id": int(message_id),
                "screenshot_received_at": datetime.datetime.utcnow(),
                "payment_status": "manual_review_required",
                "premium_status": "inactive",
                "ocr_amount_found": check.get("amount_found"),
                "ocr_amount_match": check.get("amount_match"),
                "ocr_transaction_at": check.get("transaction_at"),
                "ocr_time_match": check.get("time_match"),
            }},
        )

    async def attach_screenshot_to_order(self, user_id, message_id, received_at):
        return await self.premium_orders.update_one(
            {"user_id": int(user_id), "payment_status": "waiting_for_payment"},
            {"$set": {
                "screenshot_message_id": int(message_id),
                "screenshot_received_at": received_at,
                "payment_status": "pending_manual_verification",
            }},
        )

    async def activate_premium_order(self, user_id, message_id):
        """Atomically claim the pending order before granting Premium."""
        order = await self.premium_orders.find_one_and_update(
            {"user_id": int(user_id), "payment_status": "waiting_for_payment"},
            {"$set": {
                "payment_status": "pending_manual_verification",
                "premium_status": "active",
                "screenshot_message_id": int(message_id),
                "screenshot_received_at": datetime.datetime.utcnow(),
            }},
            return_document=ReturnDocument.AFTER,
        )
        return order

    async def set_order_activation(self, user_id, activated_at, expires_at):
        return await self.premium_orders.update_one(
            {"user_id": int(user_id)},
            {"$set": {
                "premium_status": "active",
                "activated_at": activated_at,
                "expires_at": expires_at,
                "reminder_sent": False,
            }},
        )

    async def get_pending_manual_verifications(self):
        return self.premium_orders.find({
            "payment_status": {"$in": ["pending_manual_verification", "manual_review_required"]},
            "premium_status": {"$in": ["active", "inactive"]},
        })

    async def get_premium_order(self, user_id):
        return await self.premium_orders.find_one({"user_id": int(user_id)})

    async def mark_payment_verified(self, user_id):
        now = datetime.datetime.utcnow()
        return await self.premium_orders.update_one(
            {"user_id": int(user_id)},
            {"$set": {
                "payment_status": "manually_verified",
                "manually_verified": True,
                "manually_verified_at": now,
            }},
        )

    async def approve_manual_payment(self, user_id, screenshot_message_id=None):
        """Atomically approve only the exact screenshot currently awaiting manual review."""
        now = datetime.datetime.utcnow()
        query = {
            "user_id": int(user_id),
            "payment_status": {"$in": ["manual_review_required", "pending_manual_verification"]},
        }
        if screenshot_message_id is not None:
            query["screenshot_message_id"] = int(screenshot_message_id)
        return await self.premium_orders.update_one(
            query,
            {"$set": {
                "payment_status": "manually_verified",
                "manually_verified": True,
                "manually_verified_at": now,
            }},
        )

    async def reject_manual_payment(self, user_id, screenshot_message_id=None):
        """Atomically reject only the exact screenshot currently awaiting manual review."""
        query = {
            "user_id": int(user_id),
            "payment_status": {"$in": ["manual_review_required", "pending_manual_verification"]},
        }
        if screenshot_message_id is not None:
            query["screenshot_message_id"] = int(screenshot_message_id)
        return await self.premium_orders.update_one(
            query,
            {"$set": {
                "payment_status": "manually_rejected",
                "premium_status": "inactive",
                "manually_verified": False,
                "rejected_at": datetime.datetime.utcnow(),
            }},
        )

    async def set_subscription_expired(self, user_id, expired_at=None):
        return await self.premium_orders.update_one(
            {"user_id": int(user_id)},
            {"$set": {
                "premium_status": "expired",
                "expires_at": expired_at or datetime.datetime.utcnow(),
            }},
        )

    async def mark_reminder_sent(self, user_id):
        return await self.premium_orders.update_one(
            {"user_id": int(user_id)},
            {"$set": {"reminder_sent": True}},
        )

    # JISSHU BOTS
    async def jisshu_set_ads_link(self, link):
        await self.jisshu_ads_link.update_one({}, {"$set": {"link": link}}, upsert=True)

    async def jisshu_get_ads_link(self):
        link = await self.jisshu_ads_link.find_one({})
        if link is not None:
            return link.get("link")
        else:
            return None

    async def jisshu_del_ads_link(self):
        try:
            isDeleted = await self.jisshu_ads_link.delete_one({})
            if isDeleted.deleted_count > 0:
                return True
            else:
                return False
        except Exception as e:
            print(f"Got err in db set : {e}")
            return False

    async def get_send_movie_update_status(self, bot_id):
        bot = await self.botcol.find_one({"id": bot_id})
        if bot and bot.get("movie_update_feature"):
            return bot["movie_update_feature"]
        else:
            return IS_SEND_MOVIE_UPDATE

    async def update_send_movie_update_status(self, bot_id, enable):
        bot = await self.botcol.find_one({"id": int(bot_id)})
        if bot:
            await self.botcol.update_one(
                {"id": int(bot_id)}, {"$set": {"movie_update_feature": enable}}
            )
        else:
            await self.botcol.insert_one(
                {"id": int(bot_id), "movie_update_feature": enable}
            )

    async def get_pm_search_status(self, bot_id):
        bot = await self.botcol.find_one({"id": bot_id})
        if bot and bot.get("bot_pm_search"):
            return bot["bot_pm_search"]
        else:
            return IS_PM_SEARCH

    async def update_pm_search_status(self, bot_id, enable):
        bot = await self.botcol.find_one({"id": int(bot_id)})
        if bot:
            await self.botcol.update_one(
                {"id": int(bot_id)}, {"$set": {"bot_pm_search": enable}}
            )
        else:
            await self.botcol.insert_one({"id": int(bot_id), "bot_pm_search": enable})

    async def movies_update_channel_id(self, id=None):
        if id is None:
            myLinks = await self.movies_update_channel.find_one({})
            if myLinks is not None:
                return myLinks.get("id")
            else:
                return None
        return await self.movies_update_channel.update_one(
            {}, {"$set": {"id": id}}, upsert=True
        )

    async def reset_group_settings(self, id):
        await self.grp.update_one({"id": int(id)}, {"$set": {"settings": self.default}})


db = Database()
