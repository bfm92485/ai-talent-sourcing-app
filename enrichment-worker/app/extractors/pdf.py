"""PDF text extraction using PyMuPDF (fitz)."""

import io
import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def extract_text_from_pdf(content: bytes) -> str:
    """
    Extract all text from a PDF file.

    Args:
        content: Raw PDF bytes.

    Returns:
        Concatenated text from all pages.

    Raises:
        ValueError: If the PDF cannot be parsed or contains no text.
    """
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:
        raise ValueError(f"Failed to open PDF: {e}") from e

    pages = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text("text")
        if text.strip():
            pages.append(text)

    doc.close()

    if not pages:
        raise ValueError("PDF contains no extractable text (may be image-only)")

    full_text = "\n\n".join(pages)
    logger.info(f"Extracted {len(full_text)} chars from {len(pages)} PDF pages")
    return full_text
