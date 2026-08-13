from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.routers.health import _check_db_status, _parallel_checks
from app.scheduler import get_scheduler_status

GITHUB_AUTONOMY_TOKEN_ENV = "GITHUB_AUTONOMY_TOKEN"
GITHUB_OWNER = "asiarrwright-max"
GITHUB_REPO = "EdgeCast"
GITHUB_BASE_URL = "https://api.github.com"
GITHUB_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

_YELLOW_RULES: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"probability_engine", re.I), "Calibration / model", "protected model path changed"),
    (re.compile(r"calibrat", re.I), "Calibration / model", "calibration logic changed"),
    (re.compile(r"forecast.?weight", re.I), "Calibration / model", "forecast weighting changed"),
    (re.compile(r"ensemble.?weight", re.I), "Calibration / model", "ensemble weighting changed"),
    (re.compile(r"confidence.?scor", re.I), "Edge / confidence", "confidence scoring changed"),
    (re.compile(r"edge.?calcul", re.I), "Edge / confidence", "edge calculation changed"),
    (re.compile(r"eligibility\.py$", re.I), "Eligibility", "eligibility guard changed"),
    (re.compile(r"verified.?cit", re.I), "Eligibility", "verified-city logic changed"),
    (re.compile(r"paper_trading", re.I), "Eligibility", "paper-trading eligibility path changed"),
    (re.compile(r"settlement\.py$", re.I), "Settlement", "settlement logic changed"),
    (re.compile(r"settlement_regime", re.I), "Settlement", "settlement regime changed"),
    (re.compile(r"station.?map", re.I), "Settlement", "station mapping changed"),
]


class AutonomyGithubError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _get_token() -> str | None:
    token = os.getenv(GITHUB_AUTONOMY_TOKEN_ENV, "").strip()
    return token or None


def _headers(token: str) -> dict[str, str]:
    auth_scheme = "Bearer"
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"{auth_scheme} {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _github_request(
    method: str,
    path: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    try:
        async with httpx.AsyncClient(
            base_url=GITHUB_BASE_URL,
            timeout=GITHUB_TIMEOUT,
            follow_redirects=True,
            headers=_headers(token),
        ) as client:
            response = await client.request(method, path, params=params, json=json)
    except httpx.TimeoutException as exc:
        raise AutonomyGithubError(504, "GitHub request timed out.") from exc
    except httpx.RequestError as exc:
        raise AutonomyGithubError(502, "GitHub request failed.") from exc

    if response.status_code >= 400:
        detail = None
        try:
            detail = response.json().get("message")
        except Exception:
            detail = response.text.strip() or None
        raise AutonomyGithubError(
            502 if response.status_code >= 500 else response.status_code,
            detail or f"GitHub request failed with HTTP {response.status_code}.",
        )

    if response.status_code == 204 or not response.content:
        return None
    return response.json()


def _label_names(issue: dict[str, Any]) -> set[str]:
    labels: set[str] = set()
    for label in issue.get("labels", []):
        if isinstance(label, str):
            labels.add(label)
        elif isinstance(label, dict) and label.get("name"):
            labels.add(str(label["name"]))
    return labels


def _is_pr(issue: dict[str, Any]) -> bool:
    return bool(issue.get("pull_request"))


def _dedupe_issues(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[int, dict[str, Any]] = {}
    for group in groups:
        for issue in group:
            seen[int(issue["number"])] = issue
    return sorted(seen.values(), key=lambda issue: issue.get("updated_at") or "", reverse=True)


def _strip_comments(text: str) -> str:
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _clean_line(text: str) -> str:
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[#>*\-\s]+", "", text).strip()
    return re.sub(r"\s+", " ", text).strip()


def _extract_section(body: str, heading_terms: tuple[str, ...]) -> str | None:
    if not body.strip():
        return None

    sections = re.split(r"(?m)^#{1,6}\s+", body)
    headings = re.findall(r"(?m)^#{1,6}\s+(.+)$", body)
    for heading, section_body in zip(headings, sections[1:]):
        heading_lower = heading.lower()
        if any(term in heading_lower for term in heading_terms):
            lines = [_clean_line(line) for line in section_body.splitlines()]
            text = " ".join(line for line in lines if line)
            return text[:400] if text else None

    for line in body.splitlines():
        normalized = _clean_line(line)
        if not normalized:
            continue
        lower = normalized.lower()
        for term in heading_terms:
            prefix = f"{term.lower()}:"
            if lower.startswith(prefix):
                value = normalized[len(prefix):].strip()
                return value[:400] if value else None
    return None


def _extract_summary(body: str) -> str | None:
    clean = _strip_comments(body or "")
    preferred = _extract_section(clean, ("summary", "overview", "what changed"))
    if preferred:
        return preferred

    for block in re.split(r"\n\s*\n", clean):
        text = " ".join(_clean_line(line) for line in block.splitlines())
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text[:280]
    return None


def _yellow_reasons_from_files(files: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    for file in files:
        filename = str(file.get("filename", ""))
        for pattern, _area, reason in _YELLOW_RULES:
            if pattern.search(filename):
                reasons.append(f"{reason}: {filename}")
    return list(dict.fromkeys(reasons))


def _affected_area(files: list[dict[str, Any]], title: str, body: str) -> str:
    counts: dict[str, int] = {}
    text = f"{title}\n{body}"
    for pattern, area, _reason in _YELLOW_RULES:
        if any(pattern.search(str(file.get("filename", ""))) for file in files) or pattern.search(text):
            counts[area] = counts.get(area, 0) + 1

    if counts:
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    if "official" in text.lower():
        return "OFFICIAL evidence"
    return "Protected behavior"


def _plain_ci_state(status_data: dict[str, Any] | None) -> str:
    state = (status_data or {}).get("state")
    if state == "success":
        return "Checks passed"
    if state == "pending":
        return "Checks still running"
    if state in {"failure", "error"}:
        return "Checks need attention"
    return "Checks not reported yet"


def _risk_state(labels: set[str]) -> str:
    if "owner-approved-yellow" in labels:
        return "Owner approved"
    if "owner-approval-required" in labels:
        return "Waiting for owner decision"
    return "No owner action required"


def _green_status_text(labels: set[str], issue: dict[str, Any]) -> str:
    if "agent-token-needed" in labels:
        return "Ready, but GitHub cloud-agent credentials still need to be configured."
    if "agent-ready" in labels and not _is_pr(issue):
        return "Ready for autonomous repair pickup."
    if _is_pr(issue):
        return "Pull request open for normal review."
    return "Autonomous repair is in progress."


def _red_status_text(labels: set[str]) -> str:
    if "safety-block" in labels:
        return "Safety block is active."
    return "Autonomous activation is prohibited."


async def _list_open_issues_for_label(token: str, label: str) -> list[dict[str, Any]]:
    data = await _github_request(
        "GET",
        f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/issues",
        token=token,
        params={"state": "open", "labels": label, "per_page": 100, "sort": "updated", "direction": "desc"},
    )
    return list(data or [])


async def _get_pull_details(token: str, number: int) -> dict[str, Any]:
    return await _github_request(
        "GET",
        f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/pulls/{number}",
        token=token,
    )


async def _get_pull_files(token: str, number: int) -> list[dict[str, Any]]:
    data = await _github_request(
        "GET",
        f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/pulls/{number}/files",
        token=token,
        params={"per_page": 100},
    )
    return list(data or [])


async def _get_commit_status(token: str, sha: str) -> dict[str, Any]:
    return await _github_request(
        "GET",
        f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/commits/{sha}/status",
        token=token,
    )


async def _build_system_health_summary() -> dict[str, Any] | None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        kalshi, openmeteo = await _parallel_checks()
        db_status = await _check_db_status(now)
        scheduler = get_scheduler_status()
    except Exception:
        return None

    statuses = [
        kalshi.get("status"),
        openmeteo.get("status"),
        db_status.get("status"),
        "ok" if scheduler.get("running") else "error",
    ]
    state = "ok" if all(status == "ok" for status in statuses) else "error"
    message = "All monitored systems look healthy." if state == "ok" else "One or more monitored systems need attention."
    return {"state": state, "checkedAt": now, "message": message}


async def get_autonomy_snapshot() -> dict[str, Any]:
    token = _get_token()
    health = await _build_system_health_summary()

    if token is None:
        return {
            "integrationConfigured": False,
            "readOnly": True,
            "readOnlyReason": (
                "GitHub approval integration is not configured. "
                f"Set {GITHUB_AUTONOMY_TOKEN_ENV} on the server to enable live GitHub data and approval actions."
            ),
            "summary": {
                "greenInProgress": 0,
                "greenReady": 0,
                "pullRequestsWaitingReview": 0,
                "yellowNeedsDecision": 0,
                "redSafetyBlocks": 0,
                "systemHealth": health,
            },
            "yellowProposals": [],
            "greenWork": [],
            "redWork": [],
        }

    green_items, yellow_items, red_items, safety_blocks = await asyncio.gather(
        _list_open_issues_for_label(token, "risk-green"),
        _list_open_issues_for_label(token, "risk-yellow"),
        _list_open_issues_for_label(token, "risk-red"),
        _list_open_issues_for_label(token, "safety-block"),
    )

    red_work_items = _dedupe_issues(red_items, safety_blocks)
    yellow_pr_issues = [issue for issue in yellow_items if _is_pr(issue)]

    pull_numbers = [int(issue["number"]) for issue in yellow_pr_issues]
    pull_details = await asyncio.gather(*[_get_pull_details(token, number) for number in pull_numbers])
    pull_files = await asyncio.gather(*[_get_pull_files(token, number) for number in pull_numbers])
    pull_statuses = await asyncio.gather(
        *[_get_commit_status(token, str(pr.get("head", {}).get("sha", ""))) for pr in pull_details]
    )

    yellow_cards = []
    for issue, pr, files, status in zip(yellow_pr_issues, pull_details, pull_files, pull_statuses):
        labels = _label_names(issue)
        body = issue.get("body") or ""
        reasons = _yellow_reasons_from_files(files)
        why_yellow = "; ".join(reasons) if reasons else (
            _extract_section(body, ("why yellow", "why edgecast classified this yellow"))
            or "This proposal touches protected EdgeCast behavior and requires owner approval before it can go live."
        )
        yellow_cards.append(
            {
                "number": issue["number"],
                "title": issue.get("title") or f"PR #{issue['number']}",
                "summary": _extract_summary(body) or "No short summary was provided.",
                "whyYellow": why_yellow,
                "affectedArea": _affected_area(files, issue.get("title") or "", body),
                "expectedImpact": _extract_section(body, ("expected impact", "evidence", "impact", "validation")),
                "ciState": _plain_ci_state(status),
                "riskState": _risk_state(labels),
                "ownerApproved": "owner-approved-yellow" in labels,
                "htmlUrl": issue.get("html_url"),
                "updatedAt": issue.get("updated_at"),
            }
        )

    green_work = []
    green_ready = 0
    green_in_progress = 0
    for issue in green_items:
        labels = _label_names(issue)
        if "agent-ready" in labels:
            green_ready += 1
        else:
            green_in_progress += 1
        green_work.append(
            {
                "number": issue["number"],
                "title": issue.get("title") or f"Item #{issue['number']}",
                "summary": _extract_summary(issue.get("body") or "") or "No summary provided.",
                "statusText": _green_status_text(labels, issue),
                "htmlUrl": issue.get("html_url"),
                "updatedAt": issue.get("updated_at"),
                "isPullRequest": _is_pr(issue),
            }
        )

    red_work = []
    for issue in red_work_items:
        labels = _label_names(issue)
        red_work.append(
            {
                "number": issue["number"],
                "title": issue.get("title") or f"Item #{issue['number']}",
                "summary": _extract_summary(issue.get("body") or "") or "No summary provided.",
                "statusText": _red_status_text(labels),
                "htmlUrl": issue.get("html_url"),
                "updatedAt": issue.get("updated_at"),
                "isPullRequest": _is_pr(issue),
            }
        )

    review_waiting_numbers = {
        int(issue["number"])
        for issue in [*green_items, *yellow_pr_issues, *red_work_items]
        if _is_pr(issue)
    }

    yellow_needs_decision = sum(1 for card in yellow_cards if not card["ownerApproved"])

    return {
        "integrationConfigured": True,
        "readOnly": False,
        "readOnlyReason": None,
        "summary": {
            "greenInProgress": green_in_progress,
            "greenReady": green_ready,
            "pullRequestsWaitingReview": len(review_waiting_numbers),
            "yellowNeedsDecision": yellow_needs_decision,
            "redSafetyBlocks": len(red_work),
            "systemHealth": health,
        },
        "yellowProposals": yellow_cards,
        "greenWork": green_work,
        "redWork": red_work,
    }


async def set_yellow_owner_approval(pull_request_number: int, *, approved: bool) -> dict[str, Any]:
    token = _get_token()
    if token is None:
        raise AutonomyGithubError(
            503,
            f"GitHub approval integration is not configured. Set {GITHUB_AUTONOMY_TOKEN_ENV} on the server.",
        )

    issue = await _github_request(
        "GET",
        f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/issues/{pull_request_number}",
        token=token,
    )
    labels = _label_names(issue)

    if not _is_pr(issue):
        raise AutonomyGithubError(400, "Only pull requests can receive an owner YELLOW decision.")
    if issue.get("state") != "open":
        raise AutonomyGithubError(400, "Only open pull requests can receive an owner YELLOW decision.")
    if "risk-yellow" not in labels:
        raise AutonomyGithubError(400, "This pull request is not currently classified as YELLOW.")

    if approved:
        await _github_request(
            "POST",
            f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/issues/{pull_request_number}/labels",
            token=token,
            json={"labels": ["owner-approved-yellow"]},
        )
    else:
        if "owner-approved-yellow" in labels:
            await _github_request(
                "DELETE",
                f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/issues/{pull_request_number}/labels/owner-approved-yellow",
                token=token,
            )
        await _github_request(
            "POST",
            f"/repos/{GITHUB_OWNER}/{GITHUB_REPO}/issues/{pull_request_number}/labels",
            token=token,
            json={"labels": ["owner-approval-required"]},
        )

    return {
        "number": pull_request_number,
        "ownerApproved": approved,
        "message": (
            "Owner approval label applied. This action does not merge or deploy the pull request."
            if approved
            else "Owner approval label removed. The pull request remains blocked pending owner review."
        ),
    }
