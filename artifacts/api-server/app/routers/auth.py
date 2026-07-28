from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.auth import create_access_token
from app.config import get_settings

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest):
    settings = get_settings()
    if body.username != settings.admin_username or body.password != settings.admin_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token({"sub": body.username})
    return {"accessToken": token, "tokenType": "bearer"}
