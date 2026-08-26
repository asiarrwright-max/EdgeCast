# EdgeCast Autonomy Status

This document is the plain-language control panel for EdgeCast autonomous engineering.

## What the colors mean

### GREEN — can be repaired autonomously
Routine, behavior-preserving engineering work. Examples include broken tests, TypeScript/build failures, logging, documentation, dependency/setup fixes, and UI defects that do not change forecasting or trading decisions.

GREEN work may be investigated, coded, validated, and proposed in a pull request by a cloud coding agent. It must not auto-merge or deploy.

### YELLOW — investigate automatically, owner decides activation
Anything that may change model probabilities, calibration, confidence/edge, quote freshness, verified-city eligibility, settlement behavior, OFFICIAL definitions/evidence, Bet Watch recommendations, or other decision behavior.

Agents may gather evidence and prepare a proposed change, but the owner must explicitly approve behavioral activation/merge.

### RED — autonomous implementation prohibited
Real-money execution, automatic betting/order placement, bankroll/stake automation, fabricated evidence/data, rewriting OFFICIAL evidence, or weakening safety/integrity controls.

Agents may identify and explain the problem, but must not implement or activate the prohibited behavior.

## Normal autonomous flow

1. EdgeCast health workflows run in GitHub even when the app, Replit, and ChatGPT are closed.
2. A detected issue is classified GREEN, YELLOW, or RED.
3. GREEN issues become `agent-ready` and can be dispatched to the cloud coding agent.
4. YELLOW issues are marked `owner-approval-required`.
5. RED issues stop at the safety boundary.
6. Agent-created pull requests run CI plus the RGY PR Risk Gate.
7. Nothing in this system authorizes real-money execution or automatic deployment.

## Human-readable states

- `agent-ready`: safe engineering task is ready for a cloud agent.
- `agent-token-needed`: automatic API dispatch is not configured; manual Assign to Agent still works.
- `owner-approval-required`: do not activate/merge the behavioral change without owner approval.
- `risk-green`: routine engineering change.
- `risk-yellow`: potentially behavior-changing; approval gate applies.
- `risk-red`: autonomous implementation is blocked.

## Current design goal

The desired end state is: detect -> classify -> investigate/repair within policy -> validate -> open PR -> surface only decisions that genuinely require the owner.

The owner should not need to keep EdgeCast, Replit, GitHub, or ChatGPT open for scheduled monitoring or cloud-agent work.