from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.services.autonomy_github import (
    AutonomyGithubError,
    get_autonomy_snapshot,
    set_yellow_owner_approval,
)

router = APIRouter(tags=["autonomy"])


@router.get("/autonomy")
async def get_autonomy(_user: dict = Depends(get_current_user)):
    try:
        return await get_autonomy_snapshot()
    except AutonomyGithubError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/autonomy/yellow-proposals/{pull_request_number}/approve")
async def approve_yellow_proposal(
    pull_request_number: int,
    _user: dict = Depends(get_current_user),
):
    try:
        return await set_yellow_owner_approval(pull_request_number, approved=True)
    except AutonomyGithubError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/autonomy/yellow-proposals/{pull_request_number}/reject")
async def reject_yellow_proposal(
    pull_request_number: int,
    _user: dict = Depends(get_current_user),
):
    try:
        return await set_yellow_owner_approval(pull_request_number, approved=False)
    except AutonomyGithubError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
