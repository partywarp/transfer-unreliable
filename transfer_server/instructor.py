# pyright: strict

import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, ConfigDict

from . import government_commands as government

security = HTTPBasic(auto_error=False, realm="Instructor")


def require_instructor(
    response: Response,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> None:
    password = os.environ.get("INSTRUCTOR_PASSWORD")
    unauthorized = HTTPException(
        status_code=401,
        detail="Invalid instructor credentials.",
        headers={"WWW-Authenticate": 'Basic realm="Instructor"', "Cache-Control": "no-store"},
    )
    if not password or credentials is None:
        raise unauthorized

    username_matches = secrets.compare_digest(credentials.username.encode("utf-8"), b"instructor")
    password_matches = secrets.compare_digest(credentials.password.encode("utf-8"), password.encode("utf-8"))
    if not (username_matches and password_matches):
        raise unauthorized
    response.headers["Cache-Control"] = "no-store"


# Router-level authentication covers the page and every instructor API route.
router = APIRouter(
    prefix="/instructor",
    dependencies=[Depends(require_instructor)],
    include_in_schema=False,
)


class AttackSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: government.AttackMode


@router.get("")
async def instructor_page() -> FileResponse:
    return FileResponse(
        "dist/instructor.html",
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/state")
async def instructor_state() -> dict[str, str | int | bool]:
    state = government.mitm_state
    return {
        "mode": state.mode.value,
        "receipt_count": state.receipt_count,
        "replay_available": state.previous_receipt is not None,
        "public_key": government.public_key_info()["public_key"],
    }


@router.post("/api/mode")
async def set_attack_mode(settings: AttackSettings) -> dict[str, str | int | bool]:
    government.mitm_state.mode = settings.mode
    return await instructor_state()
