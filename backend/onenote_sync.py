"""Microsoft Graph OAuth + OneNote page sync into existing NoteScanner ingest pipeline."""

from __future__ import annotations

import os
import re
import time
import uuid
import html
from html.parser import HTMLParser
from urllib.parse import urlencode

import httpx

from backend.chroma_store import onenote_token_get, onenote_token_upsert
from backend.chroma_store import user_document_upsert
from backend.ingest_api import ingest_text_for_path
from backend.chroma_store import vfs_get_tree, vfs_set_tree
from backend.vfs_tree import tree_add_folder, tree_add_file

GRAPH_AUTHORIZE_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
GRAPH_TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
GRAPH_API = "https://graph.microsoft.com/v1.0"
ONENOTE_SCOPE = "offline_access User.Read Notes.Read"


def is_configured() -> bool:
    return bool((os.getenv("MICROSOFT_CLIENT_ID") or "").strip()) and bool(
        (os.getenv("MICROSOFT_CLIENT_SECRET") or "").strip()
    )


def redirect_uri() -> str:
    return (os.getenv("MICROSOFT_REDIRECT_URI") or "http://localhost:8000/integrations/onenote/callback").strip()


def _client_id() -> str:
    return (os.getenv("MICROSOFT_CLIENT_ID") or "").strip()


def _client_secret() -> str:
    return (os.getenv("MICROSOFT_CLIENT_SECRET") or "").strip()


def build_auth_url(state: str) -> str:
    params = {
        "client_id": _client_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri(),
        "response_mode": "query",
        "scope": ONENOTE_SCOPE,
        "state": state,
        "prompt": "select_account",
    }
    return f"{GRAPH_AUTHORIZE_URL}?{urlencode(params)}"


def _token_request(data: dict) -> dict:
    with httpx.Client(timeout=30.0) as c:
        res = c.post(GRAPH_TOKEN_URL, data=data)
    payload = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
    if res.status_code >= 400:
        msg = payload.get("error_description") or payload.get("error") or f"HTTP {res.status_code}"
        raise RuntimeError(f"Microsoft token request failed: {msg}")
    return payload


def exchange_code_for_token(code: str) -> dict:
    payload = _token_request(
        {
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(),
            "scope": ONENOTE_SCOPE,
        }
    )
    expires_in = int(payload.get("expires_in") or 0)
    payload["expires_at"] = int(time.time()) + max(0, expires_in - 30)
    return payload


def refresh_token(token_payload: dict) -> dict:
    rt = (token_payload or {}).get("refresh_token")
    if not rt:
        raise RuntimeError("Missing refresh token.")
    payload = _token_request(
        {
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "redirect_uri": redirect_uri(),
            "scope": ONENOTE_SCOPE,
        }
    )
    expires_in = int(payload.get("expires_in") or 0)
    payload["expires_at"] = int(time.time()) + max(0, expires_in - 30)
    return payload


def ensure_user_access_token(user_id: str) -> tuple[str, dict]:
    tok = onenote_token_get(user_id)
    if not tok:
        raise RuntimeError("OneNote is not connected. Authorize Microsoft first.")
    if int(tok.get("expires_at") or 0) <= int(time.time()):
        tok = refresh_token(tok)
        onenote_token_upsert(user_id, tok)
    at = (tok.get("access_token") or "").strip()
    if not at:
        raise RuntimeError("Missing access token. Reconnect OneNote.")
    return at, tok


class _HTMLToText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        if data:
            self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = html.unescape(raw)
        raw = re.sub(r"\r\n?", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        return raw.strip()


def _safe_name(name: str) -> str:
    n = (name or "").strip()
    n = re.sub(r"[^\w\-. ]+", "_", n, flags=re.UNICODE).strip(" ._")
    return n or "untitled"


def _graph_get_json(url: str, access_token: str) -> dict:
    with httpx.Client(timeout=45.0) as c:
        res = c.get(url, headers={"Authorization": f"Bearer {access_token}"})
    payload = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
    if res.status_code >= 400:
        msg = payload.get("error", {}).get("message") or f"HTTP {res.status_code}"
        raise RuntimeError(f"Microsoft Graph request failed: {msg}")
    return payload if isinstance(payload, dict) else {}


def _graph_get_text(url: str, access_token: str) -> str:
    with httpx.Client(timeout=45.0) as c:
        res = c.get(url, headers={"Authorization": f"Bearer {access_token}"})
    if res.status_code >= 400:
        raise RuntimeError(f"Failed to fetch OneNote page content (HTTP {res.status_code}).")
    return res.text or ""


def _list_onenote_pages(access_token: str, max_pages: int) -> list[dict]:
    top = max(1, min(int(max_pages), 100))
    # Pull notebook and section names so we can map pages under course-like folders.
    url = (
        f"{GRAPH_API}/me/onenote/pages"
        f"?$top={top}"
        "&$select=id,title,lastModifiedDateTime,contentUrl"
        "&$expand=parentSection($select=displayName,parentNotebook;"
        "$expand=parentNotebook($select=displayName))"
        "&$orderby=lastModifiedDateTime desc"
    )
    data = _graph_get_json(url, access_token)
    vals = data.get("value") or []
    return [x for x in vals if isinstance(x, dict)]


def _page_to_rel_path(page: dict) -> tuple[str, str]:
    title = _safe_name(str(page.get("title") or "OneNote page"))
    pid = str(page.get("id") or uuid.uuid4().hex)
    section = (page.get("parentSection") or {}) if isinstance(page.get("parentSection"), dict) else {}
    notebook = (section.get("parentNotebook") or {}) if isinstance(section.get("parentNotebook"), dict) else {}
    course = _safe_name(str(notebook.get("displayName") or section.get("displayName") or "OneNote"))
    filename = f"{title}-{pid[:8]}.txt"
    rel_path = f"{course}/{filename}".replace("\\", "/")
    return rel_path, title


def sync_onenote_pages_to_ingest(user_id: str, max_pages: int = 25) -> dict:
    access_token, _ = ensure_user_access_token(user_id)
    pages = _list_onenote_pages(access_token, max_pages=max_pages)
    tree = vfs_get_tree(user_id)
    ingested = 0
    skipped = 0
    errors: list[str] = []

    for page in pages:
        try:
            content_url = str(page.get("contentUrl") or "").strip()
            if not content_url:
                skipped += 1
                continue
            rel_path, source_title = _page_to_rel_path(page)
            html_doc = _graph_get_text(content_url, access_token)
            parser = _HTMLToText()
            parser.feed(html_doc)
            text = parser.text()
            if not text:
                skipped += 1
                continue
            folder = rel_path.rsplit("/", 1)[0]
            file_name = rel_path.rsplit("/", 1)[-1]
            tree = tree_add_folder(tree, "", folder)
            tree = tree_add_file(tree, folder, file_name)
            try:
                user_document_upsert(user_id, rel_path, text, source_title)
            except Exception:
                # user_documents is a cache; ingest chunks are the primary retrieval source.
                pass
            ingest_text_for_path(user_id, rel_path, source_title, text)
            ingested += 1
        except Exception as e:
            errors.append(str(e))
            continue

    vfs_set_tree(user_id, tree)
    return {
        "pages_seen": len(pages),
        "pages_ingested": ingested,
        "pages_skipped": skipped,
        "errors": errors[:10],
    }

