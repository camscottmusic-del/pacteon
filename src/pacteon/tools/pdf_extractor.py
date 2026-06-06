"""Extract text and images from engineering drawing PDFs."""
import base64
from pathlib import Path

import pdfplumber


def extract_pdf_text(pdf_path: str | Path) -> dict[str, str]:
    """Return per-page text from a PDF drawing."""
    result = {}
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            result[f"page_{i + 1}"] = page.extract_text() or ""
    return result


def extract_pdf_images(pdf_path: str | Path) -> list[dict]:
    """
    Return each page rendered as a base64 PNG for vision model input.
    Requires pdfplumber's page-to-image conversion (uses pillow internally).
    """
    images = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            img = page.to_image(resolution=150)
            # Save to buffer and base64-encode for the Anthropic messages API
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            images.append({
                "page": i + 1,
                "media_type": "image/png",
                "data": base64.standard_b64encode(buf.getvalue()).decode("utf-8"),
            })
    return images
