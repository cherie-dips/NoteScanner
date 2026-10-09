"""NoteScanner API: app setup, error handling, CORS, and the route modules in backend/routes/."""
from dotenv import load_dotenv

# Load .env before importing backend modules: some of them read settings at import time.
load_dotenv()

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import alerts, library, mailer, onenote_sync, preferences, rate_limit, reminders, settings
from backend.auth import SESSION_HEADER
from backend.routes import account, admin, chat, files, onenote, study
from backend.routes import library as library_routes
from backend.settings import MAX_CHAT_FILES_PER_SESSION, MAX_UPLOAD_MB, allowed_origins

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("notescanner")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    reminders.start()  # daily review emails (only when SMTP is set up)
    library.start()  # re-checks the shared course library (only when LIBRARY_SYNC_HOURS is set)
    yield


app = FastAPI(title="NoteScanner API", lifespan=lifespan)


@app.middleware("http")
async def json_errors(request: Request, call_next):
    """Turn unexpected crashes into a JSON error. Added before CORS so the browser can read it."""
    try:
        return await call_next(request)
    except Exception as e:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        alerts.notify("server-error", f"{request.method} {request.url.path}: {type(e).__name__}: {e}")
        return JSONResponse(
            {"error": "Something went wrong on the server. Please try again."},
            status_code=500,
        )


@app.middleware("http")
async def request_limits(request: Request, call_next):
    """
    Overall cap on every request per network address and per signed-in browser, checked before
    any route runs (routes add their own tighter limits). Added before CORS so the 429 is readable.
    """
    if request.url.path not in settings.UNLIMITED_PATHS:
        rules = [("all-ip", rate_limit.client_ip(request), *settings.LIMIT_ALL_PER_IP, None)]
        session_id = (request.headers.get(SESSION_HEADER) or "").strip()
        if session_id:
            rules.append(("all-session", session_id, *settings.LIMIT_ALL_PER_SESSION, None))
        try:
            rate_limit.check_all(rules)
        except HTTPException as e:
            return JSONResponse({"detail": e.detail}, status_code=e.status_code, headers=e.headers)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,  # auth uses the X-Session-Id header, not cookies
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Retry-After"],
)

for module in (account, files, chat, study, onenote, admin, library_routes):
    app.include_router(module.router)


@app.get("/health")
def health():
    """Liveness check for Docker / the hosting platform (doesn't touch the database)."""
    return {"status": "ok"}


@app.get("/config")
def public_config():
    """Feature flags the frontend needs before sign-in."""
    return {
        "password_reset_enabled": mailer.is_configured(),
        "onenote_configured": onenote_sync.is_configured(),
        "max_upload_mb": MAX_UPLOAD_MB,
        "max_chat_files": MAX_CHAT_FILES_PER_SESSION,
        "reminders_available": preferences.reminders_available(),
        "languages": list(settings.ANSWER_LANGUAGES),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
