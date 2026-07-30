---
name: AsyncMock session.add side_effect pattern
description: SQLAlchemy session.add() is synchronous; AsyncMock() makes it an AsyncMock which doesn't fire side_effect on a bare (unawaited) call — use MagicMock instead.
---

## Rule

When creating a mock `AsyncSession` for tests, `session.add` must be a `MagicMock`, not `AsyncMock`.

```python
# WRONG — side_effect never fires because the coroutine is never awaited
mock_session = AsyncMock()
mock_session.add.side_effect = my_capture_fn   # silent failure

# CORRECT
mock_session = AsyncMock()
mock_session.add = MagicMock(side_effect=my_capture_fn)
mock_session.flush = AsyncMock()   # flush IS async, so AsyncMock is right
```

**Why:** `AsyncMock()` attributes default to `AsyncMock`. When the production code calls `session.add(obj)` (no `await`), it creates a coroutine that's immediately discarded — the `side_effect` IS technically called, but only if the mock is awaited. Since `session.add()` is never awaited, `side_effect` effectively never runs and captured lists stay empty.

**How to apply:** Any test that tries to capture `session.add(...)` calls for assertion (e.g. "was this PaperTrade created?") must explicitly override `session.add = MagicMock(...)`. Also applies to `spec=SomeEmptyClass` — if the spec class has no attributes, mock attribute access raises `AttributeError` for `spec`-enforced mocks; use `MagicMock()` without spec for ad-hoc session mocks.
