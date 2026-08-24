# Workflow approval diagnostic

## Observed failure mode

A pull request can have both EdgeCast CI and EdgeCast RGY Risk Gate finish with GitHub conclusion `action_required` while producing zero jobs. This is not a test failure and must not be treated as successful validation.

Concrete example: PR #30 at head `e6029260f3fdd4fa02b3c334902fdd75dcca43d4` produced EdgeCast CI run #82 and RGY Risk Gate run #30 with `action_required`; the CI run exposed zero jobs.

## Required control-plane behavior

Automation must distinguish this state from failed tests and from successful dispatch/validation:

1. If a required workflow concludes `action_required` and has no jobs, report `Workflow approval required — validation not started`.
2. Do not repeatedly rerun the same blocked workflow; reruns cannot substitute for GitHub approval.
3. Do not mark a PR ready, validated, mergeable-by-policy, or complete until actual CI/RGY jobs execute and pass.
4. Route persistent approval friction into GREEN control-plane work (#12/#13) rather than weakening CI/RGY or changing protected product behavior.
5. Preserve all forecasting, calibration, eligibility, settlement, readiness, evidence-population, and real-money safety boundaries.

This document is diagnostic/operational only and changes no application or trading behavior.
