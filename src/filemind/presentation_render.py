"""Optional, bounded PPTX-to-PDF rendition for faithful slide previews."""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger("filemind.presentation_render")
MAX_RENDERED_PDF_BYTES = 100 * 1024 * 1024
MAX_RENDERED_SLIDES = 100


def rendered_pdf_path(root: Path, owner_id: UUID, version_id: UUID) -> Path:
    base = root.resolve()
    owner_dir = (base / str(owner_id)).resolve()
    if not owner_dir.is_relative_to(base):
        raise ValueError("invalid preview path")
    return owner_dir / f"{version_id}.pdf"


def render_presentation(
    source: Path, root: Path, owner_id: UUID, version_id: UUID, expected_pages: int
) -> bool:
    """Convert to a cache PDF; failure leaves extracted-text preview available."""
    soffice = shutil.which("soffice")
    if soffice is None or expected_pages < 1 or expected_pages > MAX_RENDERED_SLIDES:
        return False
    destination = rendered_pdf_path(root, owner_id, version_id)
    try:
        with tempfile.TemporaryDirectory(prefix="filemind-ppt-") as temporary:
            workdir = Path(temporary)
            input_path = workdir / "source.pptx"
            shutil.copyfile(source, input_path)
            profile = (workdir / "lo-profile").as_uri()
            result = subprocess.run(
                [
                    soffice,
                    f"-env:UserInstallation={profile}",
                    "--headless",
                    "--convert-to",
                    "pdf:impress_pdf_Export",
                    "--outdir",
                    str(workdir),
                    str(input_path),
                ],
                capture_output=True,
                check=False,
                timeout=180,
            )
            converted = workdir / "source.pdf"
            if result.returncode != 0 or not converted.is_file():
                raise RuntimeError("presentation conversion failed")
            if converted.stat().st_size > MAX_RENDERED_PDF_BYTES:
                raise RuntimeError("rendered presentation exceeds size limit")
            if len(PdfReader(converted, strict=True).pages) != expected_pages:
                raise RuntimeError("rendered page count differs from source")
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged = destination.with_name(f".{version_id}.{os.getpid()}.part")
            try:
                shutil.copyfile(converted, staged)
                os.replace(staged, destination)
            finally:
                staged.unlink(missing_ok=True)
        return True
    except (OSError, ValueError, RuntimeError, PdfReadError, subprocess.TimeoutExpired) as exc:
        logger.warning("PPT rendition unavailable error_type=%s", type(exc).__name__)
        return False
