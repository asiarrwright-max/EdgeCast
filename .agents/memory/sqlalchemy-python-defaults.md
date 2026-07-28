---
name: SQLAlchemy mapped_column defaults at Python level
description: mapped_column(default=X) is a DB INSERT default, not a Python __init__ default; tests must pass values explicitly.
---

In SQLAlchemy 2.0, `mapped_column(default=X)` sets the column default that gets emitted in INSERT statements. It does **not** set a Python-level attribute on the object at instantiation time.

**Why:** Tests that construct ORM model instances directly (not through a DB session) and assert on default column values will fail — the attribute will be `None` until the row is flushed/committed.

**How to apply:** In tests that check default values on model instances, pass the expected values explicitly: e.g. `KalshiMarket(ticker=..., status="active", weather_matched=False)`. Don't test SQLAlchemy's own default mechanism; test your application's logic.
