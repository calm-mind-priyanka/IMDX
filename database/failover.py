"""Dependency-light application-level Mongo collection failover helpers."""
import logging

class _FailoverCursor:
    """Lazy Motor cursor wrapper: use the secondary if primary iteration fails."""
    def __init__(self, primary_cursor, secondary_cursor):
        self._primary = primary_cursor
        self._secondary = secondary_cursor
        self._fallback = False

    def _cursor(self):
        return self._secondary if self._fallback else self._primary

    def __getattr__(self, name):
        if name in {"limit", "skip", "sort", "batch_size", "collation", "hint", "max_time_ms"}:
            def chained(*args, **kwargs):
                getattr(self._primary, name)(*args, **kwargs)
                if self._secondary is not None:
                    getattr(self._secondary, name)(*args, **kwargs)
                return self
            return chained
        if name == "to_list":
            async def to_list(length=None):
                try:
                    return await self._primary.to_list(length=length)
                except Exception:
                    if self._secondary is None:
                        raise
                    self._fallback = True
                    return await self._secondary.to_list(length=length)
            return to_list
        return getattr(self._cursor(), name)

    def __aiter__(self):
        async def iterate():
            seen = set()
            try:
                async for row in self._primary:
                    key = row.get("_id") if isinstance(row, dict) else None
                    if key is not None:
                        seen.add(str(key))
                    yield row
            except Exception:
                if self._secondary is None:
                    raise
                self._fallback = True
                async for row in self._secondary:
                    key = row.get("_id") if isinstance(row, dict) else None
                    if key is not None and str(key) in seen:
                        continue
                    yield row
        return iterate()


def _is_unmatched_write(result):
    """True when a Mongo update matched nothing and did not upsert a record."""
    if result is None:
        return False
    matched = getattr(result, "matched_count", None)
    upserted = getattr(result, "upserted_id", None)
    return matched == 0 and upserted is None


class _FailoverCollection:
    """Primary-first collection facade with secondary fallback on DB errors.

    Writes go to primary normally and to secondary only when primary fails; this
    avoids double-applying non-idempotent operations such as $inc. Reads retry on
    the secondary after primary failure. Existing collection/document schemas are
    preserved, and no extra database or storage engine is introduced.
    """
    _READS = {"find_one", "count_documents", "estimated_document_count", "distinct", "index_information"}

    def __init__(self, primary, secondary=None, name="collection", fallback_on_empty=False):
        self.primary, self.secondary, self.name = primary, secondary, name
        # Only opt-in collections should read the secondary when the primary
        # is reachable but does not contain the requested record. This is
        # needed for verification tokens written during a primary outage.
        self.fallback_on_empty = bool(fallback_on_empty)

    def __getattr__(self, name):
        primary_attr = getattr(self.primary, name)
        secondary_attr = getattr(self.secondary, name) if self.secondary is not None else None
        if name in {"find", "aggregate"}:
            def cursor_factory(*args, **kwargs):
                primary_cursor = primary_attr(*args, **kwargs)
                secondary_cursor = secondary_attr(*args, **kwargs) if secondary_attr else None
                return _FailoverCursor(primary_cursor, secondary_cursor)
            return cursor_factory
        if not callable(primary_attr):
            return primary_attr
        async def invoke(*args, **kwargs):
            try:
                result = primary_attr(*args, **kwargs)
                if hasattr(result, "__await__"):
                    result = await result
                fallback_empty = self.fallback_on_empty
                if callable(fallback_empty):
                    fallback_empty = bool(fallback_empty(name, args, kwargs))
                if (name == "find_one" and result is None and self.secondary is not None
                        and fallback_empty):
                    # A write may have been routed to the secondary while the
                    # primary was unavailable. Once the primary recovers, an
                    # empty primary lookup must not incorrectly mean "expired".
                    fallback = secondary_attr(*args, **kwargs)
                    if hasattr(fallback, "__await__"):
                        fallback = await fallback
                    return fallback
                if (name == "update_one" and self.secondary is not None
                        and _is_unmatched_write(result) and fallback_empty):
                    # The same targeted records may exist only on the
                    # secondary after an earlier failover write. Do not treat
                    # a zero-match primary update as a successful consumption.
                    fallback = secondary_attr(*args, **kwargs)
                    if hasattr(fallback, "__await__"):
                        fallback = await fallback
                    if not _is_unmatched_write(fallback):
                        return fallback
                return result
            except Exception as primary_error:
                if secondary_attr is None:
                    raise
                try:
                    fallback = secondary_attr(*args, **kwargs)
                    if hasattr(fallback, "__await__"):
                        fallback = await fallback
                    logging.warning("General MongoDB primary operation %s failed; secondary handled it (%s)", name, type(primary_error).__name__)
                    return fallback
                except Exception:
                    raise primary_error
        return invoke

