"""Dependency-light application-level Mongo collection failover helpers."""
import logging
from pymongo.errors import DuplicateKeyError

class _FailoverCursor:
    """Lazy Motor cursor wrapper: use the secondary if primary iteration fails."""
    def __init__(self, primary_cursor, secondary_cursor, fallback_on_empty=False, on_fallback=None):
        self._primary = primary_cursor
        self._secondary = secondary_cursor
        self._fallback_on_empty = bool(fallback_on_empty)
        self._on_fallback = on_fallback
        self._fallback = False

    def _mark_fallback(self):
        self._fallback = True
        if self._on_fallback is not None:
            try:
                self._on_fallback()
            except Exception:
                pass

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
                    rows = await self._primary.to_list(length=length)
                    # Verification recovery scans must also look in the secondary
                    # when the primary is healthy but has no matching records.
                    if not rows and self._fallback_on_empty and self._secondary is not None:
                        self._mark_fallback()
                        return await self._secondary.to_list(length=length)
                    return rows
                except Exception:
                    if self._secondary is None:
                        raise
                    self._mark_fallback()
                    return await self._secondary.to_list(length=length)
            return to_list
        return getattr(self._cursor(), name)

    def __aiter__(self):
        async def iterate():
            seen = set()
            primary_rows = []
            try:
                async for row in self._primary:
                    primary_rows.append(row)
            except Exception:
                if self._secondary is None:
                    raise
                self._mark_fallback()
                async for row in self._secondary:
                    yield row
                return

            if not primary_rows and self._fallback_on_empty and self._secondary is not None:
                self._mark_fallback()
                async for row in self._secondary:
                    yield row
                return

            for row in primary_rows:
                key = row.get("_id") if isinstance(row, dict) else None
                if key is not None:
                    seen.add(str(key))
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
        # Once an operation successfully falls back, route subsequent operations
        # through that database instead of continuing to read stale primary data.
        # If the active secondary later fails, the wrapper attempts the primary.
        self._using_secondary = False
        # Only opt-in collections should read the secondary when the primary
        # is reachable but does not contain the requested record. This is
        # needed for verification tokens written during a primary outage.
        # Preserve predicate callbacks; coercing a callable to bool would make
        # every lookup in that collection eligible for empty-result fallback.
        self.fallback_on_empty = fallback_on_empty

    def __getattr__(self, name):
        primary_attr = getattr(self.primary, name)
        secondary_attr = getattr(self.secondary, name) if self.secondary is not None else None
        if name in {"find", "aggregate"}:
            def cursor_factory(*args, **kwargs):
                primary_cursor = primary_attr(*args, **kwargs)
                secondary_cursor = secondary_attr(*args, **kwargs) if secondary_attr else None
                if self._using_secondary and secondary_cursor is not None:
                    primary_cursor, secondary_cursor = secondary_cursor, primary_cursor
                # If we are already switched, a cursor fallback returns to the
                # primary; otherwise it switches us to the secondary.
                was_using_secondary = self._using_secondary
                return _FailoverCursor(
                    primary_cursor,
                    secondary_cursor,
                    fallback_on_empty=bool(self.fallback_on_empty) and not callable(self.fallback_on_empty),
                    on_fallback=(
                        (lambda: setattr(self, "_using_secondary", not was_using_secondary))
                        if secondary_cursor is not None else None
                    ),
                )
            return cursor_factory
        if not callable(primary_attr):
            return primary_attr
        async def _call(target_attr, *args, **kwargs):
            result = target_attr(*args, **kwargs)
            if hasattr(result, "__await__"):
                result = await result
            return result

        async def invoke(*args, **kwargs):
            fallback_empty = self.fallback_on_empty
            if callable(fallback_empty):
                fallback_empty = bool(fallback_empty(name, args, kwargs))

            # Stay on the secondary after a successful failover. This is the
            # actual automatic switch: later reads must not keep seeing stale
            # primary records while writes are landing on the secondary.
            if self._using_secondary and secondary_attr is not None:
                try:
                    result = await _call(secondary_attr, *args, **kwargs)
                    if name in {"find_one", "find_one_and_update"} and result is None and fallback_empty:
                        try:
                            primary_result = await _call(primary_attr, *args, **kwargs)
                            if primary_result is not None:
                                return primary_result
                        except Exception as primary_error:
                            logging.warning("Primary empty-result fallback failed for %s.%s (%s)", self.name, name, type(primary_error).__name__)
                    if name == "update_one" and _is_unmatched_write(result) and fallback_empty:
                        try:
                            primary_result = await _call(primary_attr, *args, **kwargs)
                            if not _is_unmatched_write(primary_result):
                                return primary_result
                        except Exception as primary_error:
                            logging.warning("Primary unmatched-write fallback failed for %s.%s (%s)", self.name, name, type(primary_error).__name__)
                    return result
                except DuplicateKeyError:
                    raise
                except Exception as secondary_error:
                    try:
                        result = await _call(primary_attr, *args, **kwargs)
                        self._using_secondary = False
                        logging.warning(
                            "MongoDB secondary operation %s failed; primary resumed (%s)",
                            name, type(secondary_error).__name__,
                        )
                        return result
                    except DuplicateKeyError:
                        raise
                    except Exception:
                        raise secondary_error

            try:
                result = await _call(primary_attr, *args, **kwargs)
                if (name in {"find_one", "find_one_and_update"} and result is None
                        and secondary_attr is not None and fallback_empty):
                    fallback = await _call(secondary_attr, *args, **kwargs)
                    return fallback
                if (name == "update_one" and secondary_attr is not None
                        and _is_unmatched_write(result) and fallback_empty):
                    fallback = await _call(secondary_attr, *args, **kwargs)
                    if not _is_unmatched_write(fallback):
                        return fallback
                return result
            except DuplicateKeyError:
                # A duplicate-key violation is an idempotency result, not failover.
                raise
            except Exception as primary_error:
                if secondary_attr is None:
                    raise
                try:
                    fallback = await _call(secondary_attr, *args, **kwargs)
                    self._using_secondary = True
                    logging.warning(
                        "MongoDB primary operation %s on collection %s failed; switched to secondary (%s: %s)",
                        name, self.name, type(primary_error).__name__, str(primary_error)[:180],
                    )
                    return fallback
                except DuplicateKeyError:
                    raise
                except Exception:
                    raise primary_error
        return invoke

