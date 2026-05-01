"""DOCX text extraction using python-docx."""

import io
import logging

logger = logging.getLogger(__name__)


def extract_text_from_docx(content: bytes) -> str:
    """
    Extract all text from a DOCX file.

    Args:
        content: Raw DOCX bytes.

    Returns:
        Concatenated text from all paragraphs.

    Raises:
        ValueError: If the DOCX cannot be parsed or contains no text.
    """
    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx is required: pip install python-docx")

    try:
        doc = Document(io.BytesIO(content))
    except Exception as e:
        raise ValueError(f"Failed to open DOCX: {e}") from e

    paragraphs = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)

    if not paragraphs:
        raise ValueError("DOCX contains no extractable text")

    full_text = "\n".join(paragraphs)
    logger.info(f"Extracted {len(full_text)} chars from DOCX ({len(paragraphs)} paragraphs)")
    return full_text
