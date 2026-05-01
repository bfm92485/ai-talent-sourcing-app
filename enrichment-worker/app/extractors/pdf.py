"""PDF text extraction using pdfplumber (MIT license).

Replaces PyMuPDF (AGPL) to avoid source-disclosure obligations for
network-accessible services. See ADR-004 for licensing rationale.
"""

import io
import logging

import pdfplumber

logger = logging.getLogger(__name__)


def extract_text_from_pdf(content: bytes) -> str:
    """
    Extract all text from a PDF file using pdfplumber.

    Args:
        content: Raw PDF bytes.

    Returns:
        Concatenated text from all pages.

    Raises:
        ValueError: If the PDF cannot be parsed or contains no text.
    """
    try:
        pdf = pdfplumber.open(io.BytesIO(content))
    except Exception as e:
        raise ValueError(f"Failed to open PDF: {e}") from e

    pages = []
    for page in pdf.pages:
        text = page.extract_text()
        if text and text.strip():
            pages.append(text)

    pdf.close()

    if not pages:
        raise ValueError("PDF contains no extractable text (may be image-only/scanned)")

    full_text = "\n\n".join(pages)
    logger.info(f"Extracted {len(full_text)} chars from {len(pages)} PDF pages")
    return full_text
