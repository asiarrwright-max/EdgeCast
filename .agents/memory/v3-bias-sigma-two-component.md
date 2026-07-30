---
name: V3 bias/sigma two-component architecture
description: Phase 3 design split — sigma always applied for calibration; bias gated on three statistical conditions before adjusting mu.
---

## The rule

sigma_shrunk is **always** applied to every V3 prediction, regardless of the bias gate.
bias is **only** applied to mu (adjusting the forecast mean) when ALL THREE conditions hold:

1. `n_eff >= 50`   (higher bar than sigma's MIN_SAMPLE=30; bias converges slower)
2. `|bias_t_stat| >= 2.0`   (≈ 95% CI that bias ≠ 0; formula: `|bias| / (sigma_raw / sqrt(n_eff))`)
3. `|bias| >= 0.3°F`   (economically meaningful; below this, correction adds noise)

When any condition fails, `bias_gate_passed = False` and `mu_adjusted = raw_forecast`.  
sigma still widens/narrows the probability distribution from the preload.

**Why:** Phase 2 walk-forward showed 56% preload-hurt rate when bias was always applied.  
Denver's annual bias (+0.53°F) is real but too noisy (t=1.85, fails gate).  
OKC's annual bias (−0.80°F) is significant (t=3.33, passes gate).  
Sigma floor dominates coverage (90.6% within ±1σ vs ideal 68%) — the primary value of the preload is calibration, not mean adjustment.

## Live gate results (732 records, 2024)

| City       | Season | Level | n_eff | bias   | t    | Gate |
|------------|--------|-------|-------|--------|------|------|
| Denver     | summer | 0     | 55.2  | +1.064 | 2.30 | PASS |
| Denver     | all    | 1     | 219.6 | +0.531 | 1.85 | FAIL (t just below 2.0) |
| OKC        | spring | 0     | 55.2  | −0.901 | 2.04 | PASS |
| OKC        | all    | 1     | 219.6 | −0.797 | 3.33 | PASS |
| global     | all    | 3     | 439.2 | −0.133 | 0.70 | FAIL |

## Where the gate lives

- **Constants:** `BIAS_MIN_EFFECTIVE_N`, `BIAS_MIN_T_STAT`, `BIAS_MIN_MAGNITUDE` in `v3_error_stats.py`
- **Config:** `V3StatsConfig.bias_min_*` fields (all three thresholds configurable per run)
- **Computation:** `_compute_bias_gate()` helper stores result into `V3ErrorStats.bias_gate_passed/t_stat/suppressed_reason`
- **DB lookup:** `get_v3_prior()` → `V3Prior.bias_gate_passed/t_stat/bias_suppressed_reason`
- **Prediction:** `run_v3_prediction()` — sigma always used; bias only when `prior.bias_gate_passed`
- **Walk-forward:** `_check_wf_bias_gate()` + `_compute_wf_prior()` returns 6-tuple including gate result; `WalkForwardRecord.bias_applied` tracks per-record gate decision

## Output fields added

- `V3PredictionOutput.bias_applied: bool`
- `V3PredictionOutput.bias_suppressed_reason: str`
- `WalkForwardRecord.bias_applied: bool`, `.bias_suppressed_reason: str`
- `WalkForwardSummary.bias_applied_n: int`, `.bias_applied_pct: float`
- `V3ErrorStats.bias_t_stat`, `.bias_gate_passed`, `.bias_suppressed_reason` (DB columns + migration added)

**How to apply:** Any future change to bias application logic must go through `_compute_bias_gate()` / `_check_wf_bias_gate()`. Do not bypass the gate — if thresholds need adjusting, change `V3StatsConfig` fields and re-run `compute-error-stats`.
