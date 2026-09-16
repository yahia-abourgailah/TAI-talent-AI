"""What an uploaded file really is, from its content, never its name (BR-102, D-WEB-1).

Proposed limits, until the website team answers D-WEB-1: PDF, DOCX, JPEG or PNG, up to 10 MB.
A DOCX is a ZIP holding a Word document; any other ZIP is refused.
"""

import io
import zipfile
from dataclasses import dataclass

MAX_BYTES = 10 * 1024 * 1024

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JPEG = "image/jpeg"
PNG = "image/png"

EXTENSION = {PDF: "pdf", DOCX: "docx", JPEG: "jpg", PNG: "png"}
ACCEPTED = tuple(EXTENSION)

_ZIP_ENTRIES_CHECKED = 2000


@dataclass(frozen=True, slots=True)
class FileKind:
    media_type: str
    extension: str


def _is_docx(content: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set()
            for index, info in enumerate(archive.infolist()):
                if index >= _ZIP_ENTRIES_CHECKED:
                    return False
                names.add(info.filename)
    except (zipfile.BadZipFile, ValueError, OSError):
        return False
    return "[Content_Types].xml" in names and "word/document.xml" in names


def sniff(content: bytes) -> FileKind | None:
    """The file's kind, or None when it is not one we accept."""
    if content.startswith(b"%PDF-"):
        kind = PDF
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = PNG
    elif content.startswith(b"\xff\xd8\xff"):
        kind = JPEG
    elif content.startswith(b"PK\x03\x04") and _is_docx(content):
        kind = DOCX
    else:
        return None
    return FileKind(kind, EXTENSION[kind])
