import io
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import google_auth_httplib2
import httplib2
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from helpers.exceptions import GoogleAuthRequiredException, ValidationFailed
from helpers.google_auth import credentials_from_token, drive_access, refreshed_token
from stores.google_errors import translate_google_error

logger = logging.getLogger('uvicorn.error')

DOC = "application/vnd.google-apps.document"
SHEET = "application/vnd.google-apps.spreadsheet"
SLIDES = "application/vnd.google-apps.presentation"
FOLDER = "application/vnd.google-apps.folder"

# What to search for, by kind: a Drive `mimeType` clause.
KIND_CLAUSES = {
    "doc": f"mimeType = '{DOC}'",
    "sheet": f"mimeType = '{SHEET}'",
    "slides": f"mimeType = '{SLIDES}'",
    "pdf": "mimeType = 'application/pdf'",
    "folder": f"mimeType = '{FOLDER}'",
    "image": "mimeType contains 'image/'",
}

# Google files are exported as plain text; other files are downloaded.
EXPORT_TYPES = {DOC: "text/plain", SHEET: "text/csv", SLIDES: "text/plain"}
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {"application/json", "application/xml", "application/x-yaml"}

MAX_RESULTS = 25
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_CHARS = 20000
FILE_FIELDS = "id, name, mimeType, modifiedTime, webViewLink, size"


def _quote(value: str) -> str:
    """A value inside a Drive query string. The model never writes the query itself; this only escapes the terms."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def build_query(text: Optional[str] = None, name: Optional[str] = None, kind: str = "any",
                modified_days: Optional[int] = None, now: Optional[datetime] = None) -> str:
    """The Drive search query for a validated plan. Trashed files are never included."""
    clauses = ["trashed = false"]
    if name:
        clauses.append(f"name contains '{_quote(name)}'")
    if text:
        clauses.append(f"fullText contains '{_quote(text)}'")
    if kind in KIND_CLAUSES:
        clauses.append(KIND_CLAUSES[kind])
    if modified_days:
        since = (now or datetime.now(timezone.utc)) - timedelta(days=modified_days)
        clauses.append(f"modifiedTime > '{since.strftime('%Y-%m-%dT%H:%M:%SZ')}'")
    return " and ".join(clauses)


def is_readable_type(mime_type: str) -> bool:
    return (mime_type in EXPORT_TYPES or mime_type == "application/pdf" or mime_type in TEXT_MIME_TYPES
            or mime_type.startswith(TEXT_MIME_PREFIXES))


class DriveService:
    """
    Google Drive wrapper: search files, read their text, create documents and upload files.

    Authenticates from the per-user token stored in MongoDB, like the Gmail and Calendar services.
    Reading needs the `drive.readonly` permission; creating needs `drive.file` (only files the app made
    can then be changed). A user who connected Google before Drive was added must reconnect once.
    """

    def __init__(self):
        self.service = None
        self.token: Dict[str, Any] = {}
        self.lock = threading.Lock()

    def authenticate_from_dict(self, token_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Returns the (possibly refreshed) token so the caller can store it. Raises GoogleAuthRequiredException."""
        if not token_dict:
            raise GoogleAuthRequiredException("No Google token found. Please connect your Google account.")

        try:
            creds = credentials_from_token(token_dict)
        except Exception as exc:
            logger.error(f"Drive: Failed to load token from dict: {exc}")
            raise GoogleAuthRequiredException("Invalid Google token. Please reconnect your Google account.")

        if not creds.valid:
            if creds.expired and creds.refresh_token:
                logger.info("Drive: Token from DB expired, refreshing...")
                try:
                    creds.refresh(Request())
                except Exception as exc:
                    logger.error(f"Drive: Token refresh failed: {exc}")
                    raise GoogleAuthRequiredException("Google token expired and refresh failed. Please reconnect.")
            else:
                raise GoogleAuthRequiredException("Google token is invalid. Please reconnect your Google account.")

        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=60.0))
        self.service = build('drive', 'v3', http=authed_http, cache_discovery=False)
        self.token = token_dict
        logger.info("Google Drive API authenticated successfully (per-user DB token)")
        return refreshed_token(creds, token_dict)

    def _require(self, permission: str) -> None:
        """Fail early, with wording the user can act on, when the stored token was granted without Drive."""
        if drive_access(self.token).get(permission) is False:
            what = "read your files" if permission == "read" else "create files"
            raise GoogleAuthRequiredException(
                f"Google Drive access has not been granted ({what}). Reconnect your Google account and allow Drive.")

    def _call(self, request):
        try:
            with self.lock:
                return request.execute()
        except Exception as exc:
            error = translate_google_error(exc, "Google Drive")
            if "has not been used in project" in str(exc) or "accessNotConfigured" in str(exc):
                logger.error("The Google Drive API is not enabled in the Google Cloud project: enable it in "
                             "APIs & Services > Library > Google Drive API")
            raise error from exc

    # ── reading ──────────────────────────────────────────────────────────────
    def search_files(self, text: Optional[str] = None, name: Optional[str] = None, kind: str = "any",
                     modified_days: Optional[int] = None, max_results: int = 10) -> List[Dict[str, Any]]:
        """Files matching a validated plan, most recently changed first. An empty list means "no match"."""
        self._require("read")
        result = self._call(self.service.files().list(
            q=build_query(text, name, kind, modified_days),
            orderBy="modifiedTime desc",
            pageSize=max(1, min(int(max_results), MAX_RESULTS)),
            fields=f"files({FILE_FIELDS})",
            spaces="drive",
        ))
        return [
            {
                "file_id": item.get("id", ""),
                "name": item.get("name", ""),
                "mime_type": item.get("mimeType", ""),
                "modified": item.get("modifiedTime", ""),
                "link": item.get("webViewLink", ""),
                "size": int(item["size"]) if item.get("size") else None,
            }
            for item in result.get("files", [])
        ]

    def get_metadata(self, file_id: str) -> Dict[str, Any]:
        self._require("read")
        return self._call(self.service.files().get(fileId=file_id, fields=FILE_FIELDS))

    def read_file_text(self, file_id: str, max_chars: int = DEFAULT_MAX_CHARS) -> Dict[str, Any]:
        """The text of a file: Google Docs, Sheets and Slides exported, PDFs and text files downloaded.

        Raises ValidationFailed for a type that has no readable text or a file that is too large."""
        meta = self.get_metadata(file_id)
        mime_type = meta.get("mimeType", "")
        if not is_readable_type(mime_type):
            raise ValidationFailed(f"I can't read the content of this kind of file ({mime_type or 'unknown type'}).")
        if meta.get("size") and int(meta["size"]) > MAX_DOWNLOAD_BYTES:
            raise ValidationFailed("That file is too large for me to read (over 10 MB).")

        if mime_type in EXPORT_TYPES:
            request = self.service.files().export_media(fileId=file_id, mimeType=EXPORT_TYPES[mime_type])
        else:
            request = self.service.files().get_media(fileId=file_id)
        data = self._download(request)

        text = _pdf_text(data) if mime_type == "application/pdf" else data.decode("utf-8", errors="replace")
        text = text.strip()
        return {
            "file_id": meta.get("id", file_id),
            "name": meta.get("name", ""),
            "mime_type": mime_type,
            "link": meta.get("webViewLink", ""),
            "text": text[:max_chars],
            "truncated": len(text) > max_chars,
        }

    def _download(self, request) -> bytes:
        buffer = io.BytesIO()
        try:
            with self.lock:
                downloader = MediaIoBaseDownload(buffer, request, chunksize=1024 * 1024)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                    if buffer.tell() > MAX_DOWNLOAD_BYTES:
                        raise ValidationFailed("That file is too large for me to read (over 10 MB).")
        except ValidationFailed:
            raise
        except Exception as exc:
            raise translate_google_error(exc, "Google Drive") from exc
        return buffer.getvalue()

    # ── writing ──────────────────────────────────────────────────────────────
    def create_document(self, title: str, content: str, folder_id: Optional[str] = None) -> Dict[str, Any]:
        """A new Google Doc with `content` as its text."""
        self._require("write")
        if not title or not title.strip():
            raise ValidationFailed("A document needs a title.")
        body: Dict[str, Any] = {"name": title.strip()[:200], "mimeType": DOC}
        if folder_id:
            body["parents"] = [folder_id]
        media = MediaIoBaseUpload(io.BytesIO((content or "").encode("utf-8")), mimetype="text/plain", resumable=False)
        created = self._call(self.service.files().create(body=body, media_body=media, fields=FILE_FIELDS))
        return self._created(created)

    def upload_file(self, name: str, data: bytes, mime_type: str, folder_id: Optional[str] = None) -> Dict[str, Any]:
        """Upload a file as it is."""
        self._require("write")
        if not name or not name.strip():
            raise ValidationFailed("A file needs a name.")
        if len(data) > MAX_DOWNLOAD_BYTES:
            raise ValidationFailed("That file is too large to upload from here (over 10 MB).")
        body: Dict[str, Any] = {"name": name.strip()[:200]}
        if folder_id:
            body["parents"] = [folder_id]
        media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type or "application/octet-stream", resumable=False)
        created = self._call(self.service.files().create(body=body, media_body=media, fields=FILE_FIELDS))
        return self._created(created)

    @staticmethod
    def _created(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "file_id": item.get("id", ""),
            "name": item.get("name", ""),
            "mime_type": item.get("mimeType", ""),
            "link": item.get("webViewLink", ""),
        }


def _pdf_text(data: bytes) -> str:
    import fitz  # PyMuPDF, loaded only when a PDF is read

    with fitz.open(stream=data, filetype="pdf") as document:
        return "\n".join(page.get_text() for page in document)
