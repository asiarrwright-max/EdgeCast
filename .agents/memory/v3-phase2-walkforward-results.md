---
name: V3 Phase 2 Walk-Forward Results
description: Walk-forward validation outcome for V3 historical preload (Denver + OKC, 2024). Verdict and key findings for Phase 3 decision.
---

## Walk-Forward Validation Results (Phase 2)

**Data:** 732 records total, 702 test records (first 30 = training), Denver + OKC, 2024, GFS 1d bucket.  
**Verdict:** `no_clear_improvement`

### Overall (702 test records)

| Metric | Raw | Adjusted | Delta |
|--------|-----|----------|-------|
| MAE | 2.28°F | 2.42°F | +0.14°F worse |
| RMSE | 3.82°F | 3.78°F | −0.04°F |
| Mean error | −0.07°F | +0.33°F | bias introduced |
| CRPS | 1.917 | 1.918 | negligible |
| Brier score | 0.257 | — | |
| Coverage ±1σ | 90.6% | — | ideal = 68% |
| Coverage ±2σ | 95.7% | — | ideal = 95% |
| Preload hurt | — | 394/702 = 56% | |

### By City

| City | MAE raw | MAE adj | Delta | Mean error raw | Hurt rate |
|------|---------|---------|-------|----------------|-----------|
| Denver | 2.80°F | 2.70°F | −0.10°F ✓ | +0.59°F | 43% |
| OKC | 1.76°F | 2.14°F | +0.38°F ✗ | −0.73°F | 69% |

### By Season (overall)

| Season | MAE raw | MAE adj | Delta | Hurt rate |
|--------|---------|---------|-------|-----------|
| Winter | 2.53°F | 2.75°F | +0.22°F ✗ | 52% |
| Spring | 2.48°F | 2.82°F | +0.34°F ✗ | 70% |
| Summer | 2.27°F | 2.28°F | +0.01°F ≈ | — |
| Fall | 1.80°F | 2.01°F | +0.21°F ✗ | — |

### Key Diagnoses

1. **Near-zero global bias masked asymmetry.** The raw global mean error is −0.07°F — nearly unbiased. Denver runs warm (+0.59°F raw) and OKC runs cold (−0.73°F raw). They partially cancel, so the global prior applies a near-zero correction where a city-specific one is needed.

2. **Walk-forward bias correction overshot OKC.** OKC had a genuine cold bias (−0.73°F), but the walk-forward seasonal/city model overcorrected it — the adjusted mean error became +0.25°F (wrong direction). This is a shrinkage-timing issue: early in the year the bias estimate is noisy.

3. **SIGMA_FLOOR dominates coverage.** 90.6% of errors fall within ±1σ (ideal = 68%). The 3.5°F floor makes the distribution too wide relative to the actual errors (MAE ≈ 2.3°F). This is correct and conservative for live trading but means the model assigns low confidence even on good forecasts.

4. **Denver improved slightly.** Denver's bias (+0.59°F warm) was stable enough for the walk-forward to correct it, yielding −0.10°F MAE improvement. This is the signal that Phase 3 could exploit with more cities and data.

### What This Means for Phase 3

- Phase 3 should **not** apply a global bias correction. It should use **city-level** bias only.
- The city-level seasonal fallback (level 0) dominated the fallback distribution (492/702 = 70% of predictions used level 0), which is good — the model is mostly specific.
- More training data (multiple years, more cities) would reduce the shrinkage needed and stabilize the seasonal estimates.
- Forward learning weight should start at 0.0 and increase only after 30+ live observations per city.
- The conservative sigma (3.5°F floor) is appropriate for Phase 3 live trading — never trust a narrow CI from a small seasonal sample.

### Implementation State at Phase 2 Completion

- `v3.validation_enabled` = true (set during Phase 2 E2E test)
- `v3.predictions_enabled` = false ← must remain false until Phase 3 approved
- `v3.paper_trading_enabled` = false ← must remain false until Phase 3 approved
- `v3_error_stats` table: 14 rows (8 level-0, 2 level-1, 2 level-2, 1 level-3, 1 level-4)

**Why:** `no_clear_improvement` is the honest result. The preload is not harmful overall (CRPS negligible change), but the bias correction does not reliably improve MAE across both cities with only 1 year of data. Phase 3 should proceed with city-level bias only and forward learning starting at 0.0.
