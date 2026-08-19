from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.auth import get_current_user
from app.routers.autonomy import router


def _mock_response(status_code=200, json_data=None):
    response = MagicMock()
    response.status_code = status_code
    response.content = b"" if json_data is None else b"{}"
    response.json.return_value = json_data
    response.text = ""
    return response


def _make_issue(number, labels, is_pr=False):
    item = {
        "number": number,
        "state": "open",
        "title": f"Test item #{number}",
        "body": "## Classification\nYELLOW because this touches readiness semantics.\n\n## Expected impact\nAdds a readiness dashboard.",
        "html_url": f"https://github.com/asiarrwright-max/EdgeCast/issues/{number}",
        "updated_at": "2026-08-17T00:00:00Z",
        "labels": [{"name": lbl} for lbl in labels],
    }
    if is_pr:
        item["pull_request"] = {"url": f"https://api.github.com/repos/asiarrwright-max/EdgeCast/pulls/{number}"}
    return item


class TestAutonomyRoute:
    def test_missing_token_returns_read_only_state(self):
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: {"sub": "admin"}

        with patch("app.services.autonomy_github._build_system_health_summary", AsyncMock(return_value=None)):
            with patch("app.services.autonomy_github.os.getenv", return_value=""):
                client = TestClient(app)
                response = client.get("/api/autonomy", headers={"Authorization": "******"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["integrationConfigured"] is False
        assert payload["readOnly"] is True
        assert "not configured" in payload["readOnlyReason"].lower()

    def test_missing_token_rejects_label_mutation(self):
        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[get_current_user] = lambda: {"sub": "admin"}

        with patch("app.services.autonomy_github.os.getenv", return_value=""):
            client = TestClient(app)
            response = client.post("/api/autonomy/yellow-proposals/8/approve", headers={"Authorization": "******"})

        assert response.status_code == 503
        assert "not configured" in response.json()["detail"].lower()


class TestYellowProposalDiscovery:
    """Plain issues with risk-yellow label must appear in yellowProposals."""

    @pytest.mark.asyncio
    async def test_plain_yellow_issue_appears_in_snapshot(self):
        from app.services.autonomy_github import get_autonomy_snapshot

        yellow_issue = _make_issue(14, ["risk-yellow", "owner-approval-required"])
        green_issue = _make_issue(9, ["risk-green", "agent-ready"])

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        def _side_effect(method, path, **kwargs):
            params = kwargs.get("params") or {}
            label = params.get("labels", "")
            if label == "risk-yellow":
                return _mock_response(json_data=[yellow_issue])
            if label == "risk-green":
                return _mock_response(json_data=[green_issue])
            return _mock_response(json_data=[])

        mock_client.request = AsyncMock(side_effect=_side_effect)

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                with patch("app.services.autonomy_github._build_system_health_summary", AsyncMock(return_value=None)):
                    snapshot = await get_autonomy_snapshot()

        assert len(snapshot["yellowProposals"]) == 1
        card = snapshot["yellowProposals"][0]
        assert card["number"] == 14
        assert card["isPullRequest"] is False
        assert "owner" in card["riskState"].lower() or "waiting" in card["riskState"].lower()
        assert card["ciState"] == "Not applicable — no pull request yet."

    @pytest.mark.asyncio
    async def test_green_issue_does_not_appear_in_yellow_proposals(self):
        from app.services.autonomy_github import get_autonomy_snapshot

        green_issue = _make_issue(9, ["risk-green", "agent-ready"])

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        def _side_effect(method, path, **kwargs):
            params = kwargs.get("params", {})
            label = params.get("labels", "")
            if label == "risk-green":
                return _mock_response(json_data=[green_issue])
            return _mock_response(json_data=[])

        mock_client.request = AsyncMock(side_effect=_side_effect)

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                with patch("app.services.autonomy_github._build_system_health_summary", AsyncMock(return_value=None)):
                    snapshot = await get_autonomy_snapshot()

        assert snapshot["yellowProposals"] == []

    @pytest.mark.asyncio
    async def test_red_issue_does_not_appear_in_yellow_proposals(self):
        from app.services.autonomy_github import get_autonomy_snapshot

        red_issue = _make_issue(7, ["risk-red", "safety-block"])

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        def _side_effect(method, path, **kwargs):
            params = kwargs.get("params", {})
            label = params.get("labels", "")
            if label in ("risk-red", "safety-block"):
                return _mock_response(json_data=[red_issue])
            return _mock_response(json_data=[])

        mock_client.request = AsyncMock(side_effect=_side_effect)

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                with patch("app.services.autonomy_github._build_system_health_summary", AsyncMock(return_value=None)):
                    snapshot = await get_autonomy_snapshot()

        assert snapshot["yellowProposals"] == []

    @pytest.mark.asyncio
    async def test_yellow_pr_has_is_pull_request_true(self):
        from app.services.autonomy_github import get_autonomy_snapshot

        yellow_pr = _make_issue(8, ["risk-yellow", "owner-approval-required"], is_pr=True)
        pr_detail = {**yellow_pr, "head": {"sha": "abc123"}}
        files: list = []
        ci_status = {"state": "success"}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None

        def _side_effect(method, path, **kwargs):
            params = kwargs.get("params") or {}
            label = params.get("labels", "")
            if label == "risk-yellow":
                return _mock_response(json_data=[yellow_pr])
            if label in ("risk-green", "risk-red", "safety-block"):
                return _mock_response(json_data=[])
            if method == "GET" and "/pulls/8/files" in path:
                return _mock_response(json_data=files)
            if method == "GET" and "/pulls/8" in path:
                return _mock_response(json_data=pr_detail)
            if method == "GET" and "/commits/" in path:
                return _mock_response(json_data=ci_status)
            return _mock_response(json_data=[])

        mock_client.request = AsyncMock(side_effect=_side_effect)

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                with patch("app.services.autonomy_github._build_system_health_summary", AsyncMock(return_value=None)):
                    snapshot = await get_autonomy_snapshot()

        assert len(snapshot["yellowProposals"]) == 1
        assert snapshot["yellowProposals"][0]["isPullRequest"] is True


class TestYellowApprovalMutations:
    @pytest.mark.asyncio
    async def test_approve_plain_issue_adds_owner_approved_label(self):
        from app.services.autonomy_github import set_yellow_owner_approval

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(side_effect=[
            _mock_response(json_data=_make_issue(14, ["risk-yellow", "owner-approval-required"])),
            _mock_response(json_data={"labels": [{"name": "owner-approved-yellow"}]}),
        ])

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                result = await set_yellow_owner_approval(14, approved=True)

        assert result["ownerApproved"] is True
        issue_call, add_label_call = mock_client.request.await_args_list
        assert issue_call.args[0] == "GET"
        assert "/issues/14" in issue_call.args[1]
        assert add_label_call.args[0] == "POST"
        assert add_label_call.kwargs["json"] == {"labels": ["owner-approved-yellow"]}

    @pytest.mark.asyncio
    async def test_reject_plain_issue_keeps_block_label(self):
        from app.services.autonomy_github import set_yellow_owner_approval

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(side_effect=[
            _mock_response(json_data=_make_issue(14, ["risk-yellow", "owner-approval-required", "owner-approved-yellow"])),
            _mock_response(status_code=204),
            _mock_response(json_data={"labels": [{"name": "owner-approval-required"}]}),
        ])

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                result = await set_yellow_owner_approval(14, approved=False)

        assert result["ownerApproved"] is False
        _issue_call, delete_call, add_call = mock_client.request.await_args_list
        assert delete_call.args[0] == "DELETE"
        assert "owner-approved-yellow" in delete_call.args[1]
        assert add_call.args[0] == "POST"
        assert add_call.kwargs["json"] == {"labels": ["owner-approval-required"]}

    @pytest.mark.asyncio
    async def test_approve_adds_only_owner_approved_label(self):
        from app.services.autonomy_github import set_yellow_owner_approval

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(side_effect=[
            _mock_response(json_data={
                "number": 8,
                "state": "open",
                "pull_request": {"url": "https://api.github.com/repos/asiarrwright-max/EdgeCast/pulls/8"},
                "labels": [{"name": "risk-yellow"}, {"name": "owner-approval-required"}],
            }),
            _mock_response(json_data={"labels": [{"name": "owner-approved-yellow"}]}),
        ])

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                result = await set_yellow_owner_approval(8, approved=True)

        assert result["ownerApproved"] is True
        assert len(mock_client.request.await_args_list) == 2
        issue_call, add_label_call = mock_client.request.await_args_list
        assert issue_call.args[0] == "GET"
        assert issue_call.args[1] == "/repos/asiarrwright-max/EdgeCast/issues/8"
        assert add_label_call.args[0] == "POST"
        assert add_label_call.args[1] == "/repos/asiarrwright-max/EdgeCast/issues/8/labels"
        assert add_label_call.kwargs["json"] == {"labels": ["owner-approved-yellow"]}

    @pytest.mark.asyncio
    async def test_reject_removes_owner_approved_and_keeps_block_label(self):
        from app.services.autonomy_github import set_yellow_owner_approval

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(side_effect=[
            _mock_response(json_data={
                "number": 8,
                "state": "open",
                "pull_request": {"url": "https://api.github.com/repos/asiarrwright-max/EdgeCast/pulls/8"},
                "labels": [
                    {"name": "risk-yellow"},
                    {"name": "owner-approval-required"},
                    {"name": "owner-approved-yellow"},
                ],
            }),
            _mock_response(status_code=204),
            _mock_response(json_data={"labels": [{"name": "owner-approval-required"}]}),
        ])

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                result = await set_yellow_owner_approval(8, approved=False)

        assert result["ownerApproved"] is False
        _issue_call, delete_call, add_call = mock_client.request.await_args_list
        assert delete_call.args[0] == "DELETE"
        assert delete_call.args[1] == "/repos/asiarrwright-max/EdgeCast/issues/8/labels/owner-approved-yellow"
        assert add_call.args[0] == "POST"
        assert add_call.args[1] == "/repos/asiarrwright-max/EdgeCast/issues/8/labels"
        assert add_call.kwargs["json"] == {"labels": ["owner-approval-required"]}

    @pytest.mark.asyncio
    async def test_repo_scope_is_fixed_to_edgecast(self):
        from app.services.autonomy_github import set_yellow_owner_approval

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.request = AsyncMock(side_effect=[
            _mock_response(json_data={
                "number": 12,
                "state": "open",
                "pull_request": {"url": "https://api.github.com/repos/asiarrwright-max/EdgeCast/pulls/12"},
                "labels": [{"name": "risk-yellow"}, {"name": "owner-approval-required"}],
            }),
            _mock_response(json_data={"labels": [{"name": "owner-approved-yellow"}]}),
        ])

        with patch("app.services.autonomy_github.os.getenv", return_value="token"):
            with patch("app.services.autonomy_github.httpx.AsyncClient", return_value=mock_client):
                await set_yellow_owner_approval(12, approved=True)

        for call in mock_client.request.await_args_list:
            assert "/repos/asiarrwright-max/EdgeCast/" in call.args[1]
