import unittest

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
    def __init__(self, fail=False, rows=None, source="primary"):
        self.fail = fail
        self.rows = rows or []
        self.source = source
        self.writes = 0
    async def find_one(self, query):
        if self.fail:
            raise ConnectionError("primary down")
        return {"_id": query.get("_id"), "source": self.source}
    async def update_one(self, *args, **kwargs):
        if self.fail:
            raise ConnectionError("primary down")
        self.writes += 1
        return "primary-write"
    def find(self, *args, **kwargs):
        return _Cursor(self.rows, self.fail)


class GeneralFailoverTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_falls_back_after_primary_failure(self):
        primary = _FakeCollection(fail=True)
        secondary = _FakeCollection(rows=[{"_id": "secondary"}], source="secondary")
        collection = _FailoverCollection(primary, secondary, "test")
        result = await collection.find_one({"_id": "x"})
        self.assertEqual(result["source"], "secondary") if result else self.fail("expected fallback result")

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


if __name__ == "__main__":
    unittest.main()
