"""ZIP generation and lifecycle helpers for private export artifacts."""

import hashlib
import json
import logging
import os
import re
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from filemind.config import get_settings
from filemind.models import (
    BackgroundTask,
    Blob,
    Document,
    DocumentVersion,
    ExportArtifact,
    ExportItem,
    QuotaReservation,
    User,
)
from filemind.storage import blob_path

logger = logging.getLogger("filemind.export")
_STORAGE_KEY_RE = re.compile(r"^[a-f0-9]{32}$")


class ExportClaim(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def owner_id(self) -> UUID: ...

    @property
    def resource_id(self) -> UUID | None: ...

    @property
    def fencing_token(self) -> int: ...


class ExportFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def artifact_path(owner_id: UUID, storage_key: str) -> Path:
    if not _STORAGE_KEY_RE.fullmatch(storage_key):
        raise ExportFailure("SOURCE_UNAVAILABLE")
    root = get_settings().storage_root.resolve()
    target = (root / "exports" / str(owner_id) / f"{storage_key}.zip").resolve()
    if not target.is_relative_to(root):
        raise ExportFailure("SOURCE_UNAVAILABLE")
    return target


def _reservation(session: Session, artifact_id: UUID) -> QuotaReservation | None:
    return session.scalar(
        select(QuotaReservation).where(QuotaReservation.export_id == artifact_id).with_for_update()
    )


def _release_reservation(session: Session, artifact: ExportArtifact, *, consumed: int = 0) -> None:
    reservation = _reservation(session, artifact.id)
    if reservation is None or reservation.state != "reserved":
        return
    user = session.scalar(select(User).where(User.id == artifact.owner_id).with_for_update())
    if user is not None:
        user.reserved_bytes = max(0, user.reserved_bytes - reservation.bytes)
        if consumed:
            user.used_bytes += consumed
    reservation.state = "consumed" if consumed else "released"


def _finish_export_failure(engine: Engine, claimed: ExportClaim, code: str) -> None:
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        task = session.scalar(
            select(BackgroundTask)
            .where(
                BackgroundTask.id == claimed.id,
                BackgroundTask.owner_id == claimed.owner_id,
                BackgroundTask.fencing_token == claimed.fencing_token,
                BackgroundTask.status.in_(("running", "cancel_requested")),
            )
            .with_for_update()
        )
        artifact = session.scalar(
            select(ExportArtifact)
            .where(
                ExportArtifact.id == claimed.resource_id,
                ExportArtifact.owner_id == claimed.owner_id,
            )
            .with_for_update()
        )
        if task is None:
            return
        cancelled = task.cancel_requested or task.status == "cancel_requested"
        task.status = "cancelled" if cancelled else "failed"
        task.stage = task.status
        task.error_code = None if cancelled else code
        task.lease_until = None
        task.heartbeat_at = None
        task.updated_at = now
        if artifact is not None:
            if artifact.state not in ("invalidated", "expired"):
                artifact.state = "cancelled" if cancelled else "failed"
                artifact.error_code = None if cancelled else code
                artifact.updated_at = now
            _release_reservation(session, artifact)


def build_export(engine: Engine, claimed: ExportClaim) -> None:
    if claimed.resource_id is None:
        raise ExportFailure("INVALID_TASK")
    settings = get_settings()
    temp_path: Path | None = None
    published_path: Path | None = None
    try:
        with Session(engine, expire_on_commit=False) as session, session.begin():
            task = session.scalar(
                select(BackgroundTask)
                .where(
                    BackgroundTask.id == claimed.id,
                    BackgroundTask.owner_id == claimed.owner_id,
                    BackgroundTask.status == "running",
                    BackgroundTask.fencing_token == claimed.fencing_token,
                )
                .with_for_update()
            )
            artifact = session.scalar(
                select(ExportArtifact)
                .where(
                    ExportArtifact.id == claimed.resource_id,
                    ExportArtifact.owner_id == claimed.owner_id,
                )
                .with_for_update()
            )
            if task is None or artifact is None or artifact.state not in ("queued", "running"):
                raise ExportFailure("EXPORT_CANCELLED")
            artifact.state = "running"
            artifact.updated_at = datetime.now(UTC)
            rows = session.execute(
                select(ExportItem, Document, DocumentVersion, Blob)
                .join(
                    Document,
                    (Document.id == ExportItem.document_id)
                    & (Document.owner_id == ExportItem.owner_id),
                )
                .join(
                    DocumentVersion,
                    (DocumentVersion.id == ExportItem.version_id)
                    & (DocumentVersion.owner_id == ExportItem.owner_id),
                )
                .join(
                    Blob,
                    (Blob.id == DocumentVersion.blob_id)
                    & (Blob.owner_id == DocumentVersion.owner_id),
                )
                .where(
                    ExportItem.export_id == artifact.id,
                    ExportItem.owner_id == claimed.owner_id,
                    Document.state == "active",
                    Blob.state == "active",
                    DocumentVersion.parse_state == "ready",
                )
                .order_by(ExportItem.id)
            )
            items = rows.all()
            expected_count = session.scalar(
                select(func.count())
                .select_from(ExportItem)
                .where(
                    ExportItem.export_id == artifact.id,
                    ExportItem.owner_id == claimed.owner_id,
                )
            )
            if not items or expected_count is None or len(items) != expected_count:
                raise ExportFailure("SOURCE_UNAVAILABLE")
            manifest_base = dict(artifact.manifest)

        folder = settings.storage_root / "exports" / str(claimed.owner_id)
        folder.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix="export-", suffix=".part", dir=folder)
        os.close(fd)
        temp_path = Path(temp_name)
        manifest_items: list[dict[str, object]] = []
        used_names: set[str] = set()
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_STORED) as archive:
            for item, document, version, blob in items:
                source = blob_path(settings.storage_root, claimed.owner_id, blob.storage_key)
                if not source.is_file() or source.stat().st_size != item.size_bytes:
                    raise ExportFailure("SOURCE_UNAVAILABLE")
                digest = hashlib.sha256()
                with source.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                if digest.hexdigest() != item.sha256 or blob.sha256 != item.sha256:
                    raise ExportFailure("SOURCE_CHANGED")
                basename = item.original_name.replace("\\", "/").split("/")[-1]
                basename = "".join(c for c in basename if ord(c) >= 32 and ord(c) != 127)
                basename = basename.strip().rstrip(". ")[:200] or f"document-{document.id.hex[:8]}"
                candidate = basename
                stem, suffix = Path(basename).stem, Path(basename).suffix
                if candidate.casefold() in used_names:
                    candidate = f"{stem}-{document.id.hex[:8]}{suffix}"
                used_names.add(candidate.casefold())
                archive.write(source, f"documents/{candidate}")
                manifest_items.append(
                    {
                        "document_id": str(document.id),
                        "title": item.title,
                        "original_name": item.original_name,
                        "version_id": str(version.id),
                        "sha256": item.sha256,
                        "size_bytes": item.size_bytes,
                        "archive_path": f"documents/{candidate}",
                    }
                )
            manifest = {**manifest_base, "files": manifest_items}
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"),
            )
        actual_size = temp_path.stat().st_size
        storage_key = uuid4().hex
        published_path = artifact_path(claimed.owner_id, storage_key)
        os.replace(temp_path, published_path)
        temp_path = None
        now = datetime.now(UTC)
        with Session(engine) as session, session.begin():
            task = session.scalar(
                select(BackgroundTask)
                .where(
                    BackgroundTask.id == claimed.id,
                    BackgroundTask.owner_id == claimed.owner_id,
                    BackgroundTask.status == "running",
                    BackgroundTask.fencing_token == claimed.fencing_token,
                    BackgroundTask.lease_until > now,
                )
                .with_for_update()
            )
            artifact = session.scalar(
                select(ExportArtifact)
                .where(
                    ExportArtifact.id == claimed.resource_id,
                    ExportArtifact.owner_id == claimed.owner_id,
                )
                .with_for_update()
            )
            if task is None or artifact is None or artifact.state not in ("queued", "running"):
                raise ExportFailure("EXPORT_CANCELLED")
            active_count = session.scalar(
                select(ExportItem.id)
                .join(
                    Document,
                    (Document.id == ExportItem.document_id)
                    & (Document.owner_id == ExportItem.owner_id),
                )
                .where(
                    ExportItem.export_id == artifact.id,
                    ExportItem.owner_id == claimed.owner_id,
                    Document.state == "active",
                )
            )
            if active_count is None:
                raise ExportFailure("SOURCE_UNAVAILABLE")
            reservation = _reservation(session, artifact.id)
            if (
                reservation is None
                or reservation.state != "reserved"
                or actual_size > reservation.bytes
            ):
                raise ExportFailure("EXPORT_SIZE_LIMIT")
            artifact.state = "ready"
            artifact.storage_key = storage_key
            artifact.size_bytes = actual_size
            artifact.expires_at = now + timedelta(hours=settings.export_ttl_hours)
            artifact.updated_at = now
            artifact.error_code = None
            _release_reservation(session, artifact, consumed=actual_size)
            task.status = "succeeded"
            task.stage = "completed"
            task.progress_completed = len(items)
            task.progress_total = len(items)
            task.error_code = None
            task.lease_until = None
            task.heartbeat_at = None
            task.updated_at = now
            published_path = None
    except ExportFailure as exc:
        _finish_export_failure(engine, claimed, exc.code)
        raise
    except Exception:
        logger.exception("export build failed task_id=%s", claimed.id)
        _finish_export_failure(engine, claimed, "EXPORT_FAILED")
        raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if published_path is not None:
            published_path.unlink(missing_ok=True)


def invalidate_exports_for_document(session: Session, owner_id: UUID, document_id: UUID) -> int:
    artifacts = list(
        session.scalars(
            select(ExportArtifact)
            .join(ExportItem, ExportItem.export_id == ExportArtifact.id)
            .where(
                ExportArtifact.owner_id == owner_id,
                ExportArtifact.state.in_(("queued", "running", "ready")),
                ExportItem.owner_id == owner_id,
                ExportItem.document_id == document_id,
            )
            .with_for_update()
        )
    )
    now = datetime.now(UTC)
    for artifact in artifacts:
        artifact.state = "invalidated"
        artifact.updated_at = now
        task = session.scalar(
            select(BackgroundTask).where(BackgroundTask.id == artifact.task_id).with_for_update()
        )
        if task is not None and task.status == "queued":
            task.status = "cancelled"
            task.stage = "cancelled"
            task.cancel_requested = True
            task.updated_at = now
            _release_reservation(session, artifact)
        elif task is not None and task.status == "running":
            task.status = "cancel_requested"
            task.cancel_requested = True
            task.updated_at = now
    return len(artifacts)


def cleanup_obsolete_exports(engine: Engine, limit: int = 50) -> int:
    now = datetime.now(UTC)
    cleaned = 0
    with Session(engine) as session:
        candidates = list(
            session.scalars(
                select(ExportArtifact)
                .where(
                    (ExportArtifact.state == "invalidated")
                    | ((ExportArtifact.state == "ready") & (ExportArtifact.expires_at <= now))
                    | (
                        (ExportArtifact.state == "expired")
                        & (ExportArtifact.storage_key.is_not(None))
                    )
                )
                .order_by(ExportArtifact.expires_at, ExportArtifact.id)
                .limit(limit)
            )
        )
    for candidate in candidates:
        path: Path | None = None
        try:
            with Session(engine) as session, session.begin():
                artifact = session.scalar(
                    select(ExportArtifact)
                    .where(ExportArtifact.id == candidate.id)
                    .with_for_update()
                )
                if artifact is None or not (
                    artifact.state == "invalidated"
                    or (artifact.state == "ready" and artifact.expires_at <= now)
                    or (artifact.state == "expired" and artifact.storage_key is not None)
                ):
                    continue
                if artifact.state == "ready":
                    artifact.state = "expired"
                if artifact.storage_key:
                    path = artifact_path(artifact.owner_id, artifact.storage_key)
            if path is not None:
                path.unlink(missing_ok=True)
            with Session(engine) as session, session.begin():
                artifact = session.scalar(
                    select(ExportArtifact)
                    .where(ExportArtifact.id == candidate.id)
                    .with_for_update()
                )
                if artifact is None or artifact.state not in ("invalidated", "expired"):
                    continue
                reservation = _reservation(session, artifact.id)
                if reservation is not None and reservation.state == "reserved":
                    _release_reservation(session, artifact)
                if artifact.storage_key and artifact.size_bytes:
                    user = session.scalar(
                        select(User).where(User.id == artifact.owner_id).with_for_update()
                    )
                    if user is not None:
                        user.used_bytes = max(0, user.used_bytes - artifact.size_bytes)
                artifact.storage_key = None
                artifact.size_bytes = None
                artifact.updated_at = now
            cleaned += 1
        except OSError:
            logger.warning("export artifact cleanup failed export_id=%s", candidate.id)
    return cleaned
