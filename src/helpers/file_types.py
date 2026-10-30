"""Upload type rules: one table drives extension checks, content sniffing and what can be indexed.

The declared Content-Type header is chosen by the client, so it is only used to decide which
extensions the deployment accepts (FILE_ALLOWED_TYPES in settings). What is actually checked is the
file's extension together with its leading bytes.
"""
import os
from dataclasses import dataclass
from typing import Optional

SNIFF_BYTES = 8192


@dataclass(frozen=True)
class FileKind:
    extension: str
    content_types: frozenset  # MIME types a browser may send for this extension
    kind: str                 # "text" | "pdf" | "xlsx" | "xls"
    indexable: bool           # can be chunked into the knowledge base (the rest feed data analysis)


FILE_KINDS = {
    ".txt": FileKind(".txt", frozenset({"text/plain"}), "text", True),
    ".pdf": FileKind(".pdf", frozenset({"application/pdf"}), "pdf", True),
    ".csv": FileKind(".csv", frozenset({"text/csv", "application/vnd.ms-excel"}), "text", False),
    ".xlsx": FileKind(".xlsx", frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}), "xlsx", False),
    ".xls": FileKind(".xls", frozenset({"application/vnd.ms-excel"}), "xls", False),
}

_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def extension_of(filename: Optional[str]) -> str:
    return os.path.splitext(filename or "")[1].lower()


def allowed_extensions(configured_content_types) -> set:
    """Extensions the deployment accepts: those whose MIME types appear in FILE_ALLOWED_TYPES."""
    configured = set(configured_content_types or [])
    return {ext for ext, kind in FILE_KINDS.items() if kind.content_types & configured}


def is_indexable(filename: Optional[str]) -> bool:
    kind = FILE_KINDS.get(extension_of(filename))
    return bool(kind and kind.indexable)


def _looks_like_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        # A multi-byte character may be cut off at the end of the sniffed window.
        head.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 4:
            return False
    return True


def content_matches(extension: str, head: bytes) -> bool:
    """True if the leading bytes are consistent with the extension."""
    kind = FILE_KINDS.get(extension)
    if kind is None or not head:
        return False
    if kind.kind == "pdf":
        return _PDF_MAGIC in head[:1024]  # PDF readers accept a few bytes of leading junk
    if kind.kind == "xlsx":
        return head.startswith(_ZIP_MAGIC)
    if kind.kind == "xls":
        return head.startswith(_OLE_MAGIC)
    return _looks_like_text(head)
