"""Bounded upload validation and opaque per-account blob storage."""

import codecs
import hashlib
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile

_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_STORAGE_KEY_RE = re.compile(r"^[a-f0-9]{32}$")
_MAX_ARCHIVE_BYTES = 300 * 1024 * 1024
_MAX_ARCHIVE_ENTRIES = 10_000
_MAX_COMPRESSION_RATIO = 100


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    original_name: str
    suffix: str
    size_bytes: int
    sha256: str
    mime: str


def safe_filename(filename: str | None) -> tuple[str, str]:
    if not filename:
        raise HTTPException(422, "INVALID_FILENAME")
    name = filename.replace("\\", "/").split("/")[-1]
    name = "".join(char for char in name if ord(char) >= 32 and ord(char) != 127)
    name = name.strip().rstrip(". ")
    if not name or name in {".", ".."}:
        raise HTTPException(422, "INVALID_FILENAME")
    if len(name) > 255:
        raise HTTPException(422, "INVALID_FILENAME")
    suffix = Path(name).suffix.casefold()
    if suffix not in _MIME_BY_SUFFIX:
        raise HTTPException(415, "UNSUPPORTED_FILE_TYPE")
    return name, suffix


def _check_text(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                if b"\x00" in chunk:
                    raise UnicodeError("NUL byte in text")
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except UnicodeError as exc:
        raise HTTPException(415, "UNSUPPORTED_FILE_TYPE") from exc


def _check_ooxml(path: Path, suffix: str) -> None:
    expected_member = "word/document.xml" if suffix == ".docx" else "ppt/presentation.xml"
    try:
        with zipfile.ZipFile(path) as archive:
            items = archive.infolist()
            if len(items) > _MAX_ARCHIVE_ENTRIES:
                raise HTTPException(422, "ARCHIVE_LIMIT_EXCEEDED")
            names = {item.filename for item in items}
            if "[Content_Types].xml" not in names or expected_member not in names:
                raise HTTPException(415, "UNSUPPORTED_FILE_TYPE")
            total_size = 0
            for item in items:
                if item.flag_bits & 0x1:
                    raise HTTPException(422, "ENCRYPTED_DOCUMENT")
                total_size += item.file_size
                if total_size > _MAX_ARCHIVE_BYTES:
                    raise HTTPException(422, "ARCHIVE_LIMIT_EXCEEDED")
                ratio = item.file_size / max(item.compress_size, 1)
                if ratio > _MAX_COMPRESSION_RATIO:
                    raise HTTPException(422, "ARCHIVE_LIMIT_EXCEEDED")
    except zipfile.BadZipFile as exc:
        raise HTTPException(415, "UNSUPPORTED_FILE_TYPE") from exc


def validate_content(path: Path, suffix: str) -> str:
    with path.open("rb") as source:
        header = source.read(1024)
    if suffix == ".pdf":
        valid = b"%PDF-" in header
    elif suffix == ".png":
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif suffix in {".jpg", ".jpeg"}:
        valid = header.startswith(b"\xff\xd8\xff")
    elif suffix in {".docx", ".pptx"}:
        valid = header.startswith(b"PK\x03\x04")
        if valid:
            _check_ooxml(path, suffix)
    else:
        _check_text(path)
        valid = True
    if not valid:
        raise HTTPException(415, "UNSUPPORTED_FILE_TYPE")
    return _MIME_BY_SUFFIX[suffix]


async def stage_upload(upload: UploadFile, storage_root: Path, maximum_bytes: int) -> StagedUpload:
    original_name, suffix = safe_filename(upload.filename)
    staging_dir = storage_root / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=staging_dir)
    temp_path = Path(temp_name)
    size_bytes = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                size_bytes += len(chunk)
                if size_bytes > maximum_bytes:
                    raise HTTPException(413, "FILE_TOO_LARGE")
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        mime = validate_content(temp_path, suffix)
        return StagedUpload(temp_path, original_name, suffix, size_bytes, digest.hexdigest(), mime)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def publish_blob(staged: StagedUpload, storage_root: Path, owner_id: UUID) -> tuple[str, Path]:
    storage_key = uuid4().hex
    owner_dir = storage_root / str(owner_id)
    owner_dir.mkdir(parents=True, exist_ok=True)
    destination = owner_dir / storage_key
    os.replace(staged.path, destination)
    return storage_key, destination


def blob_path(storage_root: Path, owner_id: UUID, storage_key: str) -> Path:
    if not _STORAGE_KEY_RE.fullmatch(storage_key):
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    owner_dir = (storage_root / str(owner_id)).resolve()
    root = storage_root.resolve()
    if not owner_dir.is_relative_to(root):
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    return owner_dir / storage_key
