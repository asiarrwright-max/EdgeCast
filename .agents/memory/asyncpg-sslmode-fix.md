---
name: asyncpg sslmode fix
description: Replit's DATABASE_URL contains sslmode= which asyncpg rejects; must strip it and convert to connect_args.
---

Replit's managed PostgreSQL URL includes `?sslmode=disable` (or `require`). asyncpg does not accept `sslmode` as a query-string parameter at all — it raises `TypeError: connect() got an unexpected keyword argument 'sslmode'`.

**Fix:** In `app/config.py`, `get_async_db_url()` strips all SSL-related query params (`sslmode`, `sslcert`, `sslkey`, `sslrootcert`) using `urllib.parse`, then returns `(clean_url, connect_args)`. If `sslmode` was `require`/`verify-ca`/`verify-full`, `connect_args = {"ssl": True}`; if `disable` or `prefer`, `connect_args = {}`.

**How to apply:** Any time you create an asyncpg/SQLAlchemy async engine from a Replit DATABASE_URL, pass the cleaned URL and connect_args to `create_async_engine(url, connect_args=connect_args)`.
