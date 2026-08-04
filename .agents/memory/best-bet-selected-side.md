---
name: Best-bet selected-side probability
description: YES/NO probability rotation rule for best-bet-today endpoint; extracted into testable helper
---

## Rule
`ec_side_probability` is always pre-rotated to the chosen direction — use it as-is for both YES and NO trades.

`market_yes_probability` from the DB is **always** the YES-side Kalshi probability:
- YES trade → `selectedSideMarketProbability = market_yes_probability`
- NO  trade → `selectedSideMarketProbability = 1.0 − market_yes_probability`

Never use `market_yes_probability` directly as the NO market probability — that was the original bug.

## Why
`market_yes_probability` is stored as-collected from Kalshi (always YES). The best-bet endpoint must rotate it to the selected side before displaying or computing edge. Without the rotation, a NO trade with 35% YES probability shows "35% for the NO side" when the correct figure is 65%.

## How to apply
- Use the extracted helper `_selected_side_values(direction, ec_side_probability, market_yes_probability, side_market_price, edge_pct_points)` in `app/routers/paper_trades.py` — it is covered by regression tests in `tests/test_best_bet_logic.py`.
- `whyWeLikeThisTrade` must use `selectedSideMarketProbability` (rotated), never `market_yes_probability` raw.
- The legacy fields `ecSideProbability`, `marketYesProbability`, `sideMarketPrice`, `edgePctPoints` are kept in the response for frontend backward compatibility but are not used in the display copy.
