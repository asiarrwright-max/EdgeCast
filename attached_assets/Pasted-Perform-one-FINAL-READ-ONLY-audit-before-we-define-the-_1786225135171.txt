Perform one FINAL READ-ONLY audit before we define the EdgeCast correction plan.

Do not modify code, data, model logic, settings, calibration, or production.

Only audit these two remaining areas.

1. CALIBRATION ADJUSTMENT FACTORS

Trace the complete calibration system currently used by V2.2 and V3.

For every live calibration bucket, report:
- strategy version
- bucket definition
- sample size
- raw probability range
- calibration adjustment factor
- data period used to derive it
- whether observations came from V2.1, V2.2, V3, or mixed versions
- whether any observations came from the V2.1 inverted-bias era
- whether research-only / legacy observations were included
- whether calibration was derived from ERA5 outcomes, Kalshi settlements, or another target

Determine:
- whether the current multiplicative calibration approach is statistically appropriate
- whether it may contribute to the current 85–91% predicted probability vs ~56% observed win-rate gap
- whether calibration factors should be completely refit after settlement-rounding corrections
- whether existing calibration data should be discarded, segmented, or retained

Do not refit anything yet.

2. SETTLEMENT / ERA5 MATCHING PIPELINE

Trace the complete pipeline that determines:
- Kalshi trade settlement outcome
- ERA5 verification value
- ERA5_KALSHI_DISAGREE

Audit:
- ticker matching
- target-date matching
- city/station matching
- weather-variable matching
- timezone normalization
- daily high/low observation windows
- ERA5 grid coordinates selected
- rounding applied to ERA5 values, if any
- how Kalshi settlement is obtained and stored
- whether DST or UTC/local-date conversion can cause a one-day or observation-window mismatch

Investigate every current forward-test ERA5_KALSHI_DISAGREE record.

For each disagreement, state the most likely explanation:
- normal ERA5 grid vs physical-station difference
- rounding difference
- coordinate mismatch
- timezone/date mismatch
- incorrect join/matching
- genuine unexplained disagreement

Do not force a cause if evidence is insufficient.

FINAL OUTPUT

Give me one final table containing ONLY issues that could materially affect the validity or profitability of a future clean forward test.

Columns:
- issue
- confirmed or suspected
- severity
- must fix before new forward test? YES/NO
- exact recommended action

Then answer:

1. After these findings and the prior mechanics audit, have we now audited the full forecast → probability → trade → settlement chain?
2. Is there any known major correctness issue still unexamined?
3. Give the smallest possible set of changes required before starting Forward Test B.

Findings only.
No code changes.
No branch creation.
No production changes.