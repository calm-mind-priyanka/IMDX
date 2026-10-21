# Patch notes — MongoDB failover, Premium payments, and force-subscription

- General-data collection wrappers now stay on the secondary after a successful primary-operation fallback. They attempt the primary again if the active secondary fails, and log the collection name plus a shortened error message.
- Premium pending-order lookup compares primary and secondary copies and uses the newest order timestamp, so a stale primary order does not hide a newer pending order saved during failover.
- Legacy Premium expiry notifications use a per-user/per-expiry marker stored in both configured general-data databases to prevent repeated `#Premium_Expire` log spam when a stale expired row remains visible.
- The dedicated payment bot now explains when a screenshot has no pending Premium order, and sends a user-facing temporary error message if processing raises before responding.
- `/settings` now exposes an owner-only **GLOBAL AUTO PAYMENT** toggle. ON shows the dedicated payment-bot screenshot button; OFF hides that button and shows the owner contact button. The setting is global, not per group, and is stored in both configured general-data databases when available.
- Force-subscription prompts now include the readable title/username and numeric ID of the channels the user still needs to join, where Telegram can resolve the channel details.
- Existing automated tests pass (16 tests) and all Python files compile. Live MongoDB/Telegram behaviour cannot be verified without the deployment credentials and running services.

## Important database note

This is operation-level failover, not MongoDB replication. The secondary database is not automatically kept as a complete copy of the primary; successful writes routed to one database do not copy all historical records to the other. For true replica behaviour, use a MongoDB replica set or an explicit synchronization/repair strategy.
