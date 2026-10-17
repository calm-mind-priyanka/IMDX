# IMDX modified features and deployment notes

## Movie database resilience

- `DATABASE_URI` / `FILES_DATABASE` remain the primary movie index configuration.
- Configure `DATABASE_URI2` and `MULTIPLE_DB=True` to enable the optional second movie database.
- `PRIMARY_DB_MAX_MB` controls when indexing prefers the second movie database (default: `407`).
- New indexing attempts fall back to the other configured movie database if the preferred write fails.
- Searches, title candidates, file details, and bad-file queries isolate errors per database. A failure in one database no longer cancels a healthy database's results.
- A full/unavailable movie database does not delete existing records. If both databases are unavailable, search returns an empty result and indexing reports an error rather than crashing the bot.

## Application-data backup

Set `APP_DATA_BACKUP_URI` to a **separate MongoDB cluster/instance** for genuine outage protection. `APP_DATA_BACKUP_NAME` defaults to `<DATABASE_NAME>_backup` and can be overridden. The same backup database stores the user/group/settings data and the admin/configuration collections. Existing data is copied on startup; writes are mirrored; failed-primary writes are journaled for replay; and periodic reconciliation runs every 10 minutes.

If `APP_DATA_BACKUP_URI` is empty, the backup is disabled. A different database name on the same MongoDB cluster is not sufficient protection against a cluster outage or cluster-wide storage limit.

## Independent 2/3-step shortener

Open `/settings` and select **🆕 2/3-STEP URL SHORTENER** for the group. Configure each step's domain and API key, choose 2 or 3 successful steps, add the separate tutorial URL, then turn the feature ON.

- This feature uses its own `ad_cycle_*` group settings, verification collection, progress counter, and 24-hour access window. It does not modify the legacy shortener's `shortner`, `api`, `shortner_two`, `api_two`, `shortner_three`, or `api_three` fields.
- Each completed return advances one step. Time gaps do not reset partial progress. After the final configured step, direct access lasts 24 hours.
- The new feature is OFF by default. If enabled but a required step is not configured, it displays a friendly configuration error rather than crashing.
- If the legacy shortener is also ON, its own verification can still apply after the new gate's one-file delivery bypass; disable the legacy toggle if you want to use only the new shortener.

## Multi-FSub

The inline Force-Subscribe page lists all configured channels separately with the resolved channel name and ID. The bot must be able to access a channel to resolve its name; otherwise the page shows `Channel name unavailable` while preserving the ID. Legacy `/set_fsub` and `/remove_fsub` commands now update the multi-channel list too.

## Important security note

The original archive contains hard-coded MongoDB and shortener credentials in `info.py`. Rotate those credentials/API keys and supply replacements through environment variables before deploying or sharing the modified archive.
