"""Bounded, local-only document extraction for previews and future indexing."""

import re
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from rapidocr import RapidOCR

MAX_BLOCKS = 50_000
MAX_TEXT_PER_BLOCK = 1_000_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_RENDERED_PAGE_PIXELS = 20_000_000
PIPELINE_VERSION = "filemind-parser-1"
_ocr_lock = threading.Lock()
_ocr_engine: RapidOCR | None = None


@dataclass(frozen=True)
class ExtractedBlockData:
    kind: str
    text: str
    locator: dict[str, object]
    ocr_confidence: float | None = None


class ParseFailure(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    return re.sub(r"[\t\r\f\v ]+", " ", text).strip()


def _add(
    blocks: list[ExtractedBlockData],
    kind: str,
    text: str,
    locator: dict[str, object],
    confidence: float | None = None,
) -> None:
    cleaned = _clean(text)
    if not cleaned:
        return
    if len(cleaned) > MAX_TEXT_PER_BLOCK:
        raise ParseFailure("BLOCK_SIZE_LIMIT_EXCEEDED")
    if len(blocks) >= MAX_BLOCKS:
        raise ParseFailure("BLOCK_LIMIT_EXCEEDED")
    blocks.append(ExtractedBlockData(kind, cleaned, locator, confidence))


def _ocr() -> RapidOCR:
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                try:
                    _ocr_engine = RapidOCR()
                except Exception as exc:
                    raise ParseFailure("OCR_UNAVAILABLE") from exc
    return _ocr_engine


def _recognize(
    image: Image.Image,
) -> tuple[list[tuple[str, list[list[float]], float]], float | None]:
    try:
        output = _ocr()(image)
    except ParseFailure:
        raise
    except Exception as exc:
        raise ParseFailure("OCR_FAILED") from exc
    result = cast(Any, output)
    if result is None or not getattr(result, "txts", None):
        return [], None
    lines: list[tuple[str, list[list[float]], float]] = []
    scores = result.scores or ()
    for text, points, score in zip(result.txts, result.boxes, scores, strict=False):
        polygon = [[round(float(point[0]), 2), round(float(point[1]), 2)] for point in points]
        if text and polygon:
            lines.append((str(text), polygon, float(score)))
    confidence = sum(line[2] for line in lines) / len(lines) if lines else None
    return lines, confidence


def _parse_pdf(
    path: Path,
    max_pages: int,
    add_progress: Callable[[str, int, int], None],
) -> tuple[list[ExtractedBlockData], int]:
    try:
        reader = PdfReader(path, strict=True)
        if reader.is_encrypted:
            raise ParseFailure("ENCRYPTED_PDF")
        count = len(reader.pages)
    except ParseFailure:
        raise
    except (PdfReadError, ValueError, OSError) as exc:
        raise ParseFailure("CORRUPT_DOCUMENT") from exc
    if count > max_pages:
        raise ParseFailure("PAGE_LIMIT_EXCEEDED")

    blocks: list[ExtractedBlockData] = []
    pdf = None
    try:
        for page_index, page in enumerate(reader.pages):
            add_progress("extracting", page_index, count)
            try:
                text = _clean(page.extract_text(extraction_mode="layout") or "")
            except Exception as exc:
                raise ParseFailure("CORRUPT_DOCUMENT") from exc
            if len(text) >= 30:
                _add(blocks, "text", text, {"page": page_index + 1, "source": "native"})
                continue

            add_progress("ocr", page_index, count)
            if pdf is None:
                try:
                    import pypdfium2 as pdfium  # type: ignore[import-untyped]

                    pdf = pdfium.PdfDocument(path)
                except Exception as exc:
                    raise ParseFailure("PDF_RENDER_FAILED") from exc
            try:
                pdf_page = pdf[page_index]
                width, height = pdf_page.get_size()
                if width * height * 1.5**2 > MAX_RENDERED_PAGE_PIXELS:
                    raise ParseFailure("PDF_PAGE_PIXEL_LIMIT_EXCEEDED")
                rendered = pdf_page.render(scale=1.5).to_pil().convert("RGB")
            except ParseFailure:
                raise
            except Exception as exc:
                raise ParseFailure("PDF_RENDER_FAILED") from exc
            lines, _confidence = _recognize(rendered)
            if lines:
                for line, polygon, score in sorted(
                    lines,
                    key=lambda item: (
                        sum(point[1] for point in item[1]) / len(item[1]),
                        sum(point[0] for point in item[1]) / len(item[1]),
                    ),
                ):
                    box = [
                        round(min(point[0] for point in polygon) / 1.5, 2),
                        round(min(point[1] for point in polygon) / 1.5, 2),
                        round(max(point[0] for point in polygon) / 1.5, 2),
                        round(max(point[1] for point in polygon) / 1.5, 2),
                    ]
                    _add(
                        blocks,
                        "ocr_line",
                        line,
                        {"page": page_index + 1, "source": "ocr", "bbox": box},
                        score,
                    )
            elif text:
                _add(blocks, "text", text, {"page": page_index + 1, "source": "native_sparse"})
    finally:
        if pdf is not None:
            pdf.close()
    add_progress("finalizing", count, count)
    return blocks, count


def _parse_docx(
    path: Path, add_progress: Callable[[str, int, int], None]
) -> tuple[list[ExtractedBlockData], int]:
    from docx import Document as WordDocument

    try:
        document = WordDocument(str(path))
        paragraphs = list(enumerate(document.paragraphs, start=1))
        table_paragraphs = [
            (table_index, row_index, column_index, paragraph)
            for table_index, table in enumerate(document.tables, start=1)
            for row_index, row in enumerate(table.rows, start=1)
            for column_index, cell in enumerate(row.cells, start=1)
            for paragraph in cell.paragraphs
        ]
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise ParseFailure("CORRUPT_DOCUMENT") from exc
    total = len(paragraphs) + len(table_paragraphs)
    if total > 5_000:
        raise ParseFailure("PARAGRAPH_LIMIT_EXCEEDED")
    blocks: list[ExtractedBlockData] = []
    for index, paragraph in paragraphs:
        _add(blocks, "paragraph", paragraph.text, {"paragraph": index})
    for table_index, row_index, column_index, paragraph in table_paragraphs:
        _add(
            blocks,
            "table_cell",
            paragraph.text,
            {"table": table_index, "row": row_index, "column": column_index},
        )
    add_progress("finalizing", total, total)
    return blocks, 0


def _parse_pptx(
    path: Path, add_progress: Callable[[str, int, int], None]
) -> tuple[list[ExtractedBlockData], int]:
    from pptx import Presentation

    try:
        presentation = Presentation(str(path))
        slides = list(presentation.slides)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        raise ParseFailure("CORRUPT_DOCUMENT") from exc
    if len(slides) > 300:
        raise ParseFailure("PAGE_LIMIT_EXCEEDED")
    blocks: list[ExtractedBlockData] = []
    for slide_index, slide in enumerate(slides, start=1):
        add_progress("extracting", slide_index - 1, len(slides))
        for shape_index, shape in enumerate(slide.shapes, start=1):
            if getattr(shape, "has_text_frame", False):
                for paragraph_index, paragraph in enumerate(shape.text_frame.paragraphs, start=1):
                    _add(
                        blocks,
                        "slide_text",
                        paragraph.text,
                        {
                            "slide": slide_index,
                            "shape": shape_index,
                            "paragraph": paragraph_index,
                        },
                    )
            if getattr(shape, "has_table", False):
                for row_index, row in enumerate(shape.table.rows, start=1):
                    for column_index, cell in enumerate(row.cells, start=1):
                        for paragraph_index, paragraph in enumerate(
                            cell.text_frame.paragraphs, start=1
                        ):
                            _add(
                                blocks,
                                "slide_table_cell",
                                paragraph.text,
                                {
                                    "slide": slide_index,
                                    "shape": shape_index,
                                    "row": row_index,
                                    "column": column_index,
                                    "paragraph": paragraph_index,
                                },
                            )
    add_progress("finalizing", len(slides), len(slides))
    return blocks, len(slides)


def _parse_image(path: Path) -> tuple[list[ExtractedBlockData], int]:
    try:
        with Image.open(path) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ParseFailure("IMAGE_PIXEL_LIMIT_EXCEEDED")
            image = ImageOps.exif_transpose(source).convert("RGB")
            width, height = image.size
            lines, _confidence = _recognize(image)
    except ParseFailure:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ParseFailure("CORRUPT_DOCUMENT") from exc
    blocks: list[ExtractedBlockData] = []
    for line, polygon, score in sorted(
        lines,
        key=lambda item: (
            sum(point[1] for point in item[1]) / len(item[1]),
            sum(point[0] for point in item[1]) / len(item[1]),
        ),
    ):
        box = [
            round(min(point[0] for point in polygon), 2),
            round(min(point[1] for point in polygon), 2),
            round(max(point[0] for point in polygon), 2),
            round(max(point[1] for point in polygon), 2),
        ]
        _add(
            blocks,
            "ocr_line",
            line,
            {"source": "ocr", "bbox": box, "width": width, "height": height},
            score,
        )
    return blocks, 1


def extract_document(
    path: Path,
    suffix: str,
    max_pdf_pages: int,
    add_progress: Callable[[str, int, int], None],
) -> tuple[list[ExtractedBlockData], int]:
    if suffix == ".pdf":
        return _parse_pdf(path, max_pdf_pages, add_progress)
    if suffix == ".docx":
        return _parse_docx(path, add_progress)
    if suffix == ".pptx":
        return _parse_pptx(path, add_progress)
    if suffix in {".txt", ".md"}:
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (UnicodeError, OSError) as exc:
            raise ParseFailure("CORRUPT_DOCUMENT") from exc
        if len(lines) > MAX_BLOCKS:
            raise ParseFailure("BLOCK_LIMIT_EXCEEDED")
        blocks: list[ExtractedBlockData] = []
        for line_number, line in enumerate(lines, start=1):
            _add(blocks, "line", line, {"line": line_number})
        add_progress("finalizing", len(lines), len(lines))
        return blocks, 0
    if suffix in {".png", ".jpg", ".jpeg"}:
        add_progress("ocr", 0, 1)
        blocks, count = _parse_image(path)
        add_progress("finalizing", 1, 1)
        return blocks, count
    raise ParseFailure("UNSUPPORTED_FILE_TYPE")
