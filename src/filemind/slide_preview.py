"""Bounded, owner-authenticated PPTX slide layout and image previews."""

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

MAX_SLIDE_ELEMENTS = 120
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_RENDER_EDGE = 2048
SUPPORTED_IMAGE_FORMATS = {"PNG", "JPEG", "GIF", "BMP", "WEBP"}


def _image_is_previewable(blob: bytes) -> bool:
    if len(blob) > MAX_IMAGE_BYTES:
        return False
    try:
        with Image.open(BytesIO(blob)) as image:
            return (
                image.format in SUPPORTED_IMAGE_FORMATS
                and image.width > 0
                and image.height > 0
                and image.width * image.height <= MAX_IMAGE_PIXELS
            )
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return False


def _position(shape: Any, slide_width: int, slide_height: int) -> dict[str, float] | None:
    left = max(0.0, min(1.0, float(shape.left) / slide_width))
    top = max(0.0, min(1.0, float(shape.top) / slide_height))
    width = max(0.0, min(1.0 - left, float(shape.width) / slide_width))
    height = max(0.0, min(1.0 - top, float(shape.height) / slide_height))
    if width <= 0 or height <= 0:
        return None
    return {
        "x": round(left, 5),
        "y": round(top, 5),
        "width": round(width, 5),
        "height": round(height, 5),
    }


def slide_layout(path: Path, slide_number: int) -> dict[str, object] | None:
    presentation = Presentation(str(path))
    if slide_number < 1 or slide_number > len(presentation.slides):
        return None
    slide = presentation.slides[slide_number - 1]
    if presentation.slide_width is None or presentation.slide_height is None:
        return None
    slide_width = int(presentation.slide_width)
    slide_height = int(presentation.slide_height)
    if slide_width <= 0 or slide_height <= 0:
        return None
    elements: list[dict[str, object]] = []
    omitted_images = 0
    for shape in slide.shapes:
        if len(elements) >= MAX_SLIDE_ELEMENTS:
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                omitted_images += 1
            continue
        position = _position(shape, slide_width, slide_height)
        if position is None:
            continue
        if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
            if not _image_is_previewable(shape.image.blob):
                omitted_images += 1
                continue
            elements.append({"kind": "image", "shape_id": shape.shape_id, **position})
        elif shape.has_text_frame:
            text = shape.text.strip()
            if text:
                elements.append({
                    "kind": "text", "shape_id": shape.shape_id,
                    "text": text[:1200], "truncated": len(text) > 1200, **position,
                })
    return {
        "width": slide_width,
        "height": slide_height,
        "elements": elements,
        "omitted_images": omitted_images,
        "layout_note": "approximate",
    }


def slide_image_png(path: Path, slide_number: int, shape_id: int) -> bytes | None:
    presentation = Presentation(str(path))
    if slide_number < 1 or slide_number > len(presentation.slides):
        return None
    for shape in presentation.slides[slide_number - 1].shapes:
        if shape.shape_id != shape_id or shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
            continue
        blob = shape.image.blob
        if not _image_is_previewable(blob):
            return None
        try:
            with Image.open(BytesIO(blob)) as source:
                image = ImageOps.exif_transpose(source)
                image.thumbnail((MAX_RENDER_EDGE, MAX_RENDER_EDGE), Image.Resampling.LANCZOS)
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                output = BytesIO()
                image.save(output, format="PNG", optimize=True)
                return output.getvalue()
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
            return None
    return None
