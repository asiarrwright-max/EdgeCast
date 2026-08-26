---
name: GREEN repair
description: Routine behavior-preserving engineering repair eligible for cloud-agent work
title: "[GREEN] "
labels: ["green-candidate", "agent-ready"]
assignees: []
---

## Problem

Describe the engineering defect or maintenance task.

## Expected behavior

Describe the expected behavior after repair.

## GREEN boundary

This task must remain behavior-preserving. Do not change model probabilities/calibration, confidence/edge, quote freshness, verified-city eligibility, settlement logic, OFFICIAL evidence/definitions, Bet Watch recommendation behavior, bankroll/risk, or real-money execution.

If the required repair crosses that boundary, stop and reclassify/escalate to YELLOW or RED.

## Validation

Run the relevant tests plus TypeScript/build checks when applicable. Open a PR; do not auto-merge or deploy.
