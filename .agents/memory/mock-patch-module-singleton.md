---
name: Mock patching module-level singletons
description: How to correctly patch a module-level singleton (like `engine`) when the consuming code calls a getter function at request time.
---

# Patching module-level singletons in tests

## The rule
When `get_engine()` reads `app.database.engine` at call time, patch **the source** (`app.database.engine`), not the import binding in the consuming module (`app.routers.health.get_engine`). The getter is transparent — it doesn't hold its own copy of the value.

**Why:** `patch("app.routers.health.get_engine", return_value=X)` patches the function reference itself, not the value it returns. `patch("app.database.engine", X)` replaces the module-level variable that `get_engine()` reads, which is what actually controls behavior.

**How to apply:** Anywhere `get_engine()` is called at request time (not at import time), write tests as:
```python
with patch("app.database.engine", mock_engine):
    result = await _check_db_status(...)
```

## The related pattern: stale imports
Never `from app.database import engine` in a router/service module if `engine` starts as `None` and is assigned later by `init_db()`. The local binding captures `None` forever. Always use `get_engine()` called at request time.
