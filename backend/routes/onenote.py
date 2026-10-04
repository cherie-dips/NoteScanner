"""Microsoft OneNote: connect (OAuth) and import pages as notes."""
import html
import logging
import time
import uuid

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, JSONResponse

from backend import onenote_sync, rate_limit
from backend.chroma_store import (
    onenote_oauth_state_create,
    onenote_oauth_state_pop_user_id,
    onenote_token_get,
    onenote_token_upsert,
)
from backend.deps import EffectiveUser, SignedInUser
from backend.settings import LIMIT_ONENOTE_CONNECT_PER_USER, LIMIT_ONENOTE_SYNC_PER_USER

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/integrations/onenote/status")
def onenote_status(effective: EffectiveUser = None):
    user_id, is_guest = effective
    connected = False
    if not is_guest:
        connected = bool(onenote_token_get(user_id))
    return {
        "configured": onenote_sync.is_configured(),
        "connected": connected,
        "requires_sign_in": is_guest,
        "redirect_uri": onenote_sync.redirect_uri(),
    }


@router.get("/integrations/onenote/auth_url")
def onenote_auth_url(user_id: SignedInUser = None):
    rate_limit.check("onenote-connect", user_id, *LIMIT_ONENOTE_CONNECT_PER_USER)
    if not onenote_sync.is_configured():
        return JSONResponse({"error": "Microsoft OAuth is not configured on server."}, status_code=503)
    state = str(uuid.uuid4())
    onenote_oauth_state_create(user_id, state, int(time.time()) + 600)
    return {"auth_url": onenote_sync.build_auth_url(state), "state": state}


def _onenote_page(title: str, message: str, status_code: int = 200) -> HTMLResponse:
    """Small HTML page for the OAuth popup. Text is escaped because it can come from the URL."""
    body = (
        f"<html><body><h3>{html.escape(title)}</h3>"
        f"<p>{html.escape(message)}</p><p>You can close this tab.</p></body></html>"
    )
    return HTMLResponse(content=body, status_code=status_code)


@router.get("/integrations/onenote/callback")
def onenote_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        return _onenote_page("OneNote connect failed", error_description or error, 400)
    if not code or not state:
        return _onenote_page("Missing OAuth parameters.", "Please restart the connection.", 400)
    user_id = onenote_oauth_state_pop_user_id(state)
    if not user_id:
        return _onenote_page("OAuth state is invalid or expired.", "Please restart the connection.", 400)
    try:
        token_payload = onenote_sync.exchange_code_for_token(code)
        onenote_token_upsert(user_id, token_payload)
        return _onenote_page("OneNote connected successfully.", "Return to NoteScanner to sync your pages.")
    except Exception as e:
        logger.exception("OneNote token exchange failed")
        return _onenote_page("Token exchange failed.", str(e), 500)


@router.post("/integrations/onenote/sync")
def onenote_sync_route(max_pages: int = Form(25), user_id: SignedInUser = None):
    rate_limit.check("onenote-sync", user_id, *LIMIT_ONENOTE_SYNC_PER_USER)
    if not onenote_sync.is_configured():
        return JSONResponse({"error": "Microsoft OAuth is not configured on server."}, status_code=503)
    try:
        out = onenote_sync.sync_onenote_pages_to_ingest(user_id, max_pages=max_pages)
        return JSONResponse({"message": "OneNote sync complete.", **out})
    except Exception as e:
        logger.exception("OneNote sync failed")
        return JSONResponse({"error": str(e)}, status_code=500)
