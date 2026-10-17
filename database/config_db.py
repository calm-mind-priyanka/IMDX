from motor.motor_asyncio import AsyncIOMotorClient
import asyncio
import logging
from database.users_chats_db import _ResilientCollection
from info import DATABASE_URI, PREMIUM_PLANS, APP_DATA_BACKUP_URI, APP_DATA_BACKUP_NAME
from datetime import datetime


logger = logging.getLogger(__name__)


class Database:
    def __init__(self, uri, db_name):
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client[db_name]
        backup_uri = str(APP_DATA_BACKUP_URI or "").strip()
        self.backup_client = AsyncIOMotorClient(backup_uri, serverSelectionTimeoutMS=2000) if backup_uri else None
        self.backup_db = self.backup_client[APP_DATA_BACKUP_NAME] if self.backup_client is not None else None
        journal = self.backup_db["_admin_data_recovery_journal"] if self.backup_db is not None else None
        self.col = _ResilientCollection(
            self.db.user,
            self.backup_db.user if self.backup_db is not None else None,
            journal,
        )
        self.config_col = _ResilientCollection(
            self.db.configuration,
            self.backup_db.configuration if self.backup_db is not None else None,
            journal,
        )

    async def initialize_backup(self):
        """Seed and reconcile admin/configuration data in the same backup DB."""
        if self.backup_db is None:
            return False
        try:
            await self.backup_db.command("ping")
        except Exception:
            logger.exception("Admin-data backup is unavailable; continuing with primary only")
            return False
        try:
            names = await self.db.list_collection_names()
        except Exception:
            logger.exception("Admin data primary unavailable during backup sync")
            return False
        journal = self.backup_db["_admin_data_recovery_journal"]
        try:
            pending = await journal.find({}).sort("created_at", 1).to_list(length=1000)
        except Exception:
            logger.exception("Could not read admin-data recovery journal")
            pending = []
        for entry in pending:
            try:
                target = self.db[str(entry.get("collection") or "")]
                method = str(entry.get("method") or "")
                args = list(entry.get("args") or [])
                kwargs = dict(entry.get("kwargs") or {})
                if method == "insert_one" and args and isinstance(args[0], dict) and "_id" in args[0]:
                    await target.replace_one({"_id": args[0]["_id"]}, args[0], upsert=True)
                elif method in {"update_one", "update_many", "delete_one", "delete_many", "replace_one", "find_one_and_update"}:
                    await getattr(target, method)(*args, **kwargs)
                else:
                    continue
                await journal.delete_one({"_id": entry["_id"]})
            except Exception:
                logger.exception("Could not replay admin-data recovery journal entry")
        for name in names:
            try:
                source, target = self.db[name], self.backup_db[name]
                async for document in source.find({}):
                    await target.replace_one({"_id": document["_id"]}, document, upsert=True)
            except Exception:
                logger.exception("Could not sync admin-data collection %s", name)
        return True

    async def backup_sync_loop(self, interval=600):
        while self.backup_db is not None:
            await asyncio.sleep(max(60, int(interval)))
            try:
                await self.initialize_backup()
            except Exception:
                logger.exception("Periodic admin-data backup reconciliation failed")

    async def update_top_messages(self, user_id, message_text):
        user = await self.col.find_one(
            {"user_id": user_id, "messages.text": message_text}
        )

        if not user:
            await self.col.update_one(
                {"user_id": user_id},
                {"$push": {"messages": {"text": message_text, "count": 1}}},
                upsert=True,
            )
        else:
            await self.col.update_one(
                {"user_id": user_id, "messages.text": message_text},
                {"$inc": {"messages.$.count": 1}},
            )

    async def get_top_messages(self, limit=30):
        pipeline = [
            {"$unwind": "$messages"},
            {"$group": {"_id": "$messages.text", "count": {"$sum": "$messages.count"}}},
            {"$sort": {"count": -1}},
            {"$limit": limit},
        ]
        results = await self.col.aggregate(pipeline).to_list(limit)
        return [result["_id"] for result in results]

    async def delete_all_messages(self):
        await self.col.delete_many({})

    def create_configuration_data(self, advertisement=None):

        return {
            "advertisement": advertisement,
        }

    async def update_advirtisment(
        self, ads_string=None, ads_name=None, expiry=None, impression=None
    ):
        config = await self.config_col.find_one({})
        if not config:
            await self.config_col.insert_one(self.create_configuration_data())
            config = await self.config_col.find_one({})

        advertisement = config.get("advertisement")

        if advertisement is None:
            # If 'advertisement' field is not present, create it
            advertisement = {}
            config["advertisement"] = advertisement

        # Update the fields within the 'advertisement' field
        advertisement["ads_string"] = ads_string
        advertisement["ads_name"] = ads_name
        advertisement["expiry"] = expiry
        advertisement["impression_count"] = impression

        await self.config_col.update_one(
            {}, {"$set": {"advertisement": advertisement}}, upsert=True
        )

    async def update_advirtisment_impression(self, impression=None):
        await self.config_col.update_one(
            {}, {"$set": {"advertisement.impression_count": impression}}, upsert=True
        )

    async def get_advirtisment(self):
        configuration = await self.config_col.find_one({})
        if not configuration:
            await self.config_col.insert_one(self.create_configuration_data())
            configuration = await self.config_col.find_one({})
        advertisement = configuration.get("advertisement", False)
        if advertisement:
            return (
                advertisement.get("ads_string"),
                advertisement.get("ads_name"),
                advertisement.get("impression_count"),
            )
        return None, None, None

    async def reset_advertisement_if_expired(self):
        configuration = await self.config_col.find_one({})
        if configuration:
            advertisement = configuration.get("advertisement", False)
            if advertisement:
                impression_count = advertisement.get("impression_count", 0)
                expiry = advertisement.get("expiry", None)
                if (impression_count == 0) or (expiry and datetime.now() > expiry):
                    await self.config_col.update_one(
                        {}, {"$set": {"advertisement": None}}
                    )

    async def get_premium_plans(self):
        configuration = await self.config_col.find_one({})
        plans = configuration.get("premium_plans") if configuration else None
        return plans if isinstance(plans, dict) and plans else None

    async def save_premium_plans(self, plans):
        await self.config_col.update_one(
            {}, {"$set": {"premium_plans": plans}}, upsert=True
        )

    async def load_premium_plans(self):
        """Load persisted Premium plan values into the shared runtime mapping."""
        plans = await self.get_premium_plans()
        if not plans:
            await self.save_premium_plans(PREMIUM_PLANS)
            return PREMIUM_PLANS
        # Keep only known plan keys and required fields, while preserving the
        # code defaults for anything missing in an older database document.
        for key, default in PREMIUM_PLANS.items():
            value = plans.get(key)
            if isinstance(value, dict):
                default.update({k: value[k] for k in ("name", "duration", "days", "price") if k in value})
        return PREMIUM_PLANS

    async def update_configuration(self, key, value):
        try:
            await self.config_col.update_one({}, {"$set": {key: value}}, upsert=True)

        except Exception as e:
            print(f"An error occurred: {e}")

    async def get_configuration_value(self, key):
        configuration = await self.config_col.find_one({})
        if not configuration:
            await self.config_col.insert_one(self.create_configuration_data())
            configuration = await self.config_col.find_one({})
        return configuration.get(key, False)


mdb = Database(DATABASE_URI, "admin_database")
