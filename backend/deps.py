"""FastAPI dependencies for resolving the caller."""
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from backend.auth import get_effective_user, require_signed_in_user


def get_effective_user_dep(request: Request) -> tuple[str, bool]:
    try:
        return get_effective_user(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# Routes that store data or call paid AI services (OCR, chat, study tools) need an account.
SignedInUser = Annotated[str, Depends(require_signed_in_user)]
EffectiveUser = Annotated[tuple[str, bool], Depends(get_effective_user_dep)]
