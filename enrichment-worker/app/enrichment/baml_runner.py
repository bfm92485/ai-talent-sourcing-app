"""BAML resume extraction wrapper.

This module provides a high-level interface to the BAML ExtractResume function,
handling the async execution and result normalization for ERPNext consumption.
"""

import asyncio
import logging
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Add the parent directory to sys.path so BAML generated client is importable
_WORKER_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKER_ROOT))


def _normalize_date(d: str | None) -> str | None:
    """Normalize partial dates (YYYY-MM, YYYY) to YYYY-MM-DD for ERPNext."""
    if not d:
        return None
    d = d.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return d
    if re.match(r"^\d{4}-\d{2}$", d):
        return f"{d}-01"
    if re.match(r"^\d{4}$", d):
        return f"{d}-01-01"
    return None


def _ensure_url_prefix(url: str | None) -> str | None:
    """Ensure URL has https:// prefix."""
    if not url:
        return None
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


async def extract_resume(resume_text: str) -> dict[str, Any]:
    """
    Run BAML ExtractResume and return a normalized dictionary ready for ERPNext.

    Args:
        resume_text: Plain text extracted from a resume/profile.

    Returns:
        Dictionary with keys matching ERPNext Job Applicant fields.

    Raises:
        RuntimeError: If BAML extraction fails.
    """
    try:
        from baml_client import b
        from baml_client.types import ParsedResume
    except ImportError as e:
        raise RuntimeError(
            f"BAML client not generated. Run 'baml-cli generate' first. Error: {e}"
        )

    try:
        result: ParsedResume = await b.ExtractResume(resume_text)
    except Exception as e:
        raise RuntimeError(f"BAML ExtractResume failed: {e}") from e

    # Calculate TTL expiry (90 days from now)
    ttl_expiry = (date.today() + timedelta(days=90)).isoformat()

    # Normalize experience entries
    experience = []
    if result.experience:
        for exp in result.experience:
            experience.append({
                "company": getattr(exp, "company", None) or "",
                "title": getattr(exp, "title", None) or "",
                "start_date": _normalize_date(getattr(exp, "start_date", None)),
                "end_date": _normalize_date(getattr(exp, "end_date", None)),
                "responsibilities": getattr(exp, "responsibilities", None) or getattr(exp, "description", None) or "",
            })

    # Normalize education entries
    education = []
    if result.education:
        for edu in result.education:
            education.append({
                "institution": getattr(edu, "institution", None) or "",
                "degree": getattr(edu, "degree", None) or "",
                "field_of_study": getattr(edu, "field_of_study", None) or "",
            })

    # Build the normalized output
    return {
        "applicant_name": result.applicant_name or "Unknown",
        "email_id": result.email_id,
        "phone_number": getattr(result, "phone_number", None),
        "designation": getattr(result, "designation", None),
        "current_company": getattr(result, "current_company", None),
        "skills": ", ".join(result.skills) if result.skills else "",
        "linkedin_url": _ensure_url_prefix(getattr(result, "linkedin_url", None)),
        "github_url": _ensure_url_prefix(getattr(result, "github_url", None)),
        "summary": getattr(result, "summary", None),
        "experience": experience,
        "education": education,
        "data_ttl_expiry": ttl_expiry,
    }


def extract_resume_sync(resume_text: str) -> dict[str, Any]:
    """Synchronous wrapper for extract_resume."""
    return asyncio.run(extract_resume(resume_text))
