import unittest

from pymongo.errors import DuplicateKeyError
from database.failover import _FailoverCollection


class _Cursor:
    def __init__(self, rows=None, fail=False):
        self.rows = list(rows or [])
        self.fail = fail
    def sort(self, *args, **kwargs):
        return self
    def limit(self, *args, **kwargs):
        return self
    def skip(self, *args, **kwargs):
        return self
    async def to_list(self, length=None):
        if self.fail:
            raise ConnectionError("primary down")
        return self.rows[:length] if length else self.rows
    def __aiter__(self):
        async def gen():
            if self.fail:
                raise ConnectionError("primary down")
            for row in self.rows:
                yield row
        return gen()


class _FakeCollection:
    def __init__(self, fail=False, rows=None, source="primary", find_one_result="default"):
        self.fail = fail
        self.rows = rows or []
        self.source = source
        self.find_one_result = find_one_result
        self.writes = 0
        self.update_result = None
        self.insert_calls = 0
        self.find_one_and_update_result = "default"
    async def insert_one(self, data):
        self.insert_calls += 1
        if self.fail:
            raise ConnectionError("primary down")
        return "inserted"
    async def find_one(self, query):
        if self.fail:
            raise ConnectionError("primary down")
        if self.find_one_result != "default":
            return self.find_one_result
        return {"_id": query.get("_id"), "source": self.source}
    async def update_one(self, *args, **kwargs):
        if self.fail:
            raise ConnectionError("primary down")
        self.writes += 1
        return self.update_result if self.update_result is not None else "primary-write"
    async def find_one_and_update(self, *args, **kwargs):
        if self.fail:
            raise ConnectionError("primary down")
        if self.find_one_and_update_result != "default":
            return self.find_one_and_update_result
        return {"_id": "primary-claim"}
    def find(self, *args, **kwargs):
        return _Cursor(self.rows, self.fail)


class _UpdateResult:
    def __init__(self, matched_count, upserted_id=None):
        self.matched_count = matched_count
        self.upserted_id = upserted_id


class GeneralFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_falls_back_after_primary_failure(self):
        primary = _FakeCollection(fail=True)
        secondary = _FakeCollection(rows=[{"_id": "secondary"}], source="secondary")
        collection = _FailoverCollection(primary, secondary, "test")
        result = await collection.find_one({"_id": "x"})
        self.assertEqual(result["source"], "secondary") if result else self.fail("expected fallback result")

    async def test_verification_lookup_falls_back_when_primary_has_no_record(self):
        primary = _FakeCollection(find_one_result=None)
        secondary = _FakeCollection(source="secondary")
        collection = _FailoverCollection(
            primary, secondary, "verify_id", fallback_on_empty=True
        )
        result = await collection.find_one({"return_token": "valid-token"})
        self.assertEqual(result["source"], "secondary")

    async def test_empty_result_does_not_fallback_unless_enabled(self):
        primary = _FakeCollection(find_one_result=None)
        secondary = _FakeCollection(source="secondary")
        collection = _FailoverCollection(primary, secondary, "groups")
        self.assertIsNone(await collection.find_one({"id": -100123}))

    async def test_targeted_shortener_update_falls_back_when_primary_has_no_record(self):
        primary = _FakeCollection()
        primary.update_result = _UpdateResult(0)
        secondary = _FakeCollection()
        secondary.update_result = _UpdateResult(1)
        fallback = lambda operation, args, kwargs: (
            operation == "find_one"
            or (operation == "update_one" and str(args[0].get("_id", "")).startswith("new_shortener:"))
        )
        collection = _FailoverCollection(primary, secondary, "misc", fallback_on_empty=fallback)
        result = await collection.update_one(
            {"_id": "new_shortener:-100:123"}, {"$set": {"completed_step": 1}}
        )
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(secondary.writes, 1)

    async def test_write_falls_back_after_primary_failure(self):
        primary = _FakeCollection(fail=True)
        secondary = _FakeCollection()
        collection = _FailoverCollection(primary, secondary, "test")
        result = await collection.update_one({"_id": "x"}, {"$set": {"x": 1}})
        self.assertEqual(result, "primary-write")
        self.assertEqual(secondary.writes, 1)

    async def test_cursor_to_list_falls_back(self):
        primary = _FakeCollection(fail=True)
        secondary = _FakeCollection(rows=[{"_id": "secondary"}])
        collection = _FailoverCollection(primary, secondary, "test")
        rows = await collection.find({}).to_list(length=10)
        self.assertEqual(rows, [{"_id": "secondary"}])

    async def test_verification_cursor_falls_back_when_primary_is_empty(self):
        primary = _FakeCollection(rows=[])
        secondary = _FakeCollection(rows=[{"_id": "secondary", "hash": "verify123"}])
        collection = _FailoverCollection(
            primary, secondary, "verify_id", fallback_on_empty=True
        )
        rows = await collection.find({}).sort("shortlink_created_at", -1).limit(200).to_list(length=200)
        self.assertEqual(rows, [{"_id": "secondary", "hash": "verify123"}])

    async def test_verification_claim_falls_back_when_primary_has_no_record(self):
        primary = _FakeCollection()
        primary.find_one_and_update_result = None
        secondary = _FakeCollection()
        secondary.find_one_and_update_result = {"_id": "secondary-claim"}
        collection = _FailoverCollection(
            primary, secondary, "verify_id", fallback_on_empty=True
        )
        result = await collection.find_one_and_update(
            {"_id": "secondary-claim"}, {"$set": {"recovery_sent_at": "now"}}
        )
        self.assertEqual(result["_id"], "secondary-claim")

    async def test_duplicate_key_does_not_fall_back_to_secondary(self):
        class DuplicateCollection(_FakeCollection):
            async def insert_one(self, data):
                self.insert_calls += 1
                raise DuplicateKeyError("already exists")

        primary = DuplicateCollection()
        secondary = _FakeCollection()
        collection = _FailoverCollection(primary, secondary, "payment_submissions")
        with self.assertRaises(DuplicateKeyError):
            await collection.insert_one({"payment_bot_message_id": 123})
        self.assertEqual(primary.insert_calls, 1)
        self.assertEqual(secondary.insert_calls, 0)

    async def test_callable_empty_fallback_is_not_treated_as_always_true(self):
        primary = _FakeCollection(find_one_result=None)
        secondary = _FakeCollection(source="secondary")
        predicate = lambda operation, args, kwargs: (
            operation == "find_one" and str(args[0].get("_id", "")).startswith("new_shortener:")
        )
        collection = _FailoverCollection(primary, secondary, "misc", fallback_on_empty=predicate)
        self.assertIsNone(await collection.find_one({"_id": "unrelated-record"}))
        result = await collection.find_one({"_id": "new_shortener:-100:123"})
        self.assertEqual(result["source"], "secondary")


if __name__ == "__main__":
    unittest.main()
