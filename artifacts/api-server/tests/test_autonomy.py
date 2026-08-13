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


class TestYellowApprovalMutations:
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
