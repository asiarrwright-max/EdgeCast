# EdgeCast North Star Vision

## Purpose

This document is the canonical vision for EdgeCast. All coding agents, automated workflows, and development environments (GitHub Copilot, Replit, ChatGPT) must read this file before planning or implementing any work. No local task should be optimized in a way that moves EdgeCast away from this vision.

---

## North Star

EdgeCast should become a largely self-operating weather-market research and paper-trading intelligence system.

It should continuously collect trustworthy market and weather data, verify cities and settlement mappings, generate and track forecasts, settle paper trades using the correct official evidence, learn from accumulated verified outcomes when evidence thresholds are met, evaluate whether its predictions actually have durable edge, and surface the best supported opportunities in a clear decision interface.

The owner should not have to act as the messenger between EdgeCast, Replit, GitHub, ChatGPT, and coding agents. Routine engineering problems should be detected, diagnosed, repaired, tested, and proposed automatically. Changes that could alter forecasting or experimental behavior should be investigated automatically and brought to the owner with evidence and a simple approve/reject decision. Prohibited safety actions remain blocked.

The goal is not to manufacture more bets. The goal is to determine, using clean forward-tested evidence, whether EdgeCast has a repeatable predictive advantage and where that advantage exists.

Only verified cities and valid, traceable data should feed production-facing recommendations. Missing or uncertain data should cause EdgeCast to abstain rather than guess.

The system should become increasingly useful as verified observations accumulate, while preserving experiment boundaries so it never "learns" by rewriting history or optimizing against results it has already seen.

EdgeCast should eventually require the owner's attention mainly for meaningful decisions, not routine maintenance.

---

## Long-Term Kalshi Use

EdgeCast's long-term goal is to identify sufficiently reliable, repeatable edge in Kalshi weather markets that the owner can use EdgeCast's recommendations to make informed real-money trading decisions with the goal of generating profit.

EdgeCast is currently in a research, validation, and paper-trading phase. Before recommending real-money use, it must demonstrate through clean forward testing that any apparent edge persists out of sample, after realistic market prices, liquidity, timing, settlement rules, and other relevant trading constraints are considered.

The system should ultimately tell the owner, in plain language, what opportunities appear worth trading, why, how strong the evidence is, how much uncertainty remains, and when EdgeCast does not have enough evidence to recommend a trade.

EdgeCast should optimize for validated profitable decision quality, not trade volume. Abstaining is a successful outcome when the evidence is weak.

The current autonomous system must not place real-money trades itself. Any transition from research/paper trading to real-money recommendations or execution must be treated as a deliberate new project phase with appropriate validation, controls, and owner approval.

---

## Scientific Integrity

EdgeCast and its agents are not trying to prove the system works. They are trying to determine whether it works, where it works, and whether any apparent edge is durable.

Agents should be willing to conclude that a strategy, city, market type, or even the overall hypothesis does not have enough edge.

Forward-test integrity, settlement correctness, quote freshness, OFFICIAL/RESEARCH_ONLY/LEGACY separation, verified-city status, and data lineage must not be weakened merely to produce more trades or better-looking results.

---

## Agent Obligations

Every coding agent, automated workflow, and AI assistant operating in this repository must:

1. Read this file (`EDGECAST_VISION.md`) before planning or implementing any work.
2. Read `AGENTS.md` for change-authority classification (GREEN / YELLOW / RED).
3. Read `COLLABORATION.md` for branching and PR conventions.
4. Not optimize a local task in a way that moves EdgeCast away from this North Star.
5. Prefer a transparent skip or failure over a guessed value.
6. Never place or activate real-money trades.
