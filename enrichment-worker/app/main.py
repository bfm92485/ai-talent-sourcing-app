"""
Enrichment Worker — FastAPI application for processing inbound email webhooks.

Architecture (self-hosted PrimitiveMail on Hetzner VPS):
- PrimitiveMail runs on same VPS: milter + watcher + this worker
- Watcher fires webhook to http://localhost:8090/webhook/inbound-email
- Attachments served by watcher at http://localhost:4001/download/...
- Worker extracts resumes, runs BAML, pushes to ERPNext (local Docker)

Multi-resume handling:
- A single email may contain multiple resume attachments (e.g., recruiter forwarding)
- Each resume attachment is processed independently → one Job Applicant per resume
- Cover letters are associated with the nearest preceding/following resume by candidate name

Forwarded email handling:
- Emails with FW:/Fwd: subjects or forwarded-message markers in the body are detected
- When a forwarded email has resume attachments, each resume is processed normally
  (the candidate identity comes from the resume, not the sender)
- When a forwarded email has NO resume attachments, the body-text fallback is SKIPPED
  to avoid creating a Job Applicant for the forwarder (recruiter/internal user)
"""

import asyncio
import hashlib
import json
import logging
import re
import tarfile
import io
from collections import OrderedDict
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .enrichment.baml_runner import extract_resume
from .erpnext.client import ERPNextClient
from .extractors.docx import extract_text_from_docx
from .extractors.pdf import extract_text_from_pdf
from .models.webhook import PrimitiveWebhookPayload, parse_webhook_payload
from .security import get_verified_body

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory dedup cache (LRU, max 10000 entries)
_processed_events: OrderedDict[str, bool] = OrderedDict()
_DEDUP_MAX_SIZE = 10000

# File extensions considered as resumes
RESUME_EXTENSIONS = {".pdf", ".docx", ".doc"}

# Keywords that indicate a cover letter (not a resume)
COVER_LETTER_KEYWORDS = {"cover_letter", "cover letter", "coverletter", "cover-letter"}

# Patterns that indicate a forwarded email
_FORWARD_SUBJECT_PATTERN = re.compile(r"^\s*(fw|fwd)\s*:", re.IGNORECASE)
_FORWARD_BODY_MARKERS = [
    "---------- Forwarded message",
    "-----Original Message-----",
    "-------- Original Message --------",
    "Begin forwarded message:",
]

# Regex to extract "Name <email>" from a From: line in forwarded body
_FROM_LINE_PATTERN = re.compile(
    r"^From:\s*(.+?)\s*<([^>]+)>",
    re.MULTILINE,
)
_FROM_LINE_EMAIL_ONLY = re.compile(
    r"^From:\s*<?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>?",
    re.MULTILINE,
)


def _is_duplicate(event_id: str) -> bool:
    """Check if this event has already been processed (idempotency guard)."""
    return event_id in _processed_events


def _mark_processed(event_id: str) -> None:
    """Mark an event as processed. Evicts oldest entries if cache is full."""
    _processed_events[event_id] = True
    if len(_processed_events) > _DEDUP_MAX_SIZE:
        _processed_events.popitem(last=False)


def _is_cover_letter(filename: str) -> bool:
    """Heuristic: check if a filename suggests it's a cover letter, not a resume."""
    lower = filename.lower()
    return any(kw in lower for kw in COVER_LETTER_KEYWORDS)


def _is_resume_file(filename: str) -> bool:
    """Check if a filename has a resume-compatible extension and isn't a cover letter."""
    lower = filename.lower()
    has_resume_ext = any(lower.endswith(ext) for ext in RESUME_EXTENSIONS)
    return has_resume_ext and not _is_cover_letter(lower)


def _is_forwarded_email(
    subject: Optional[str] = None,
    body_text: Optional[str] = None,
) -> bool:
    """
    Detect whether an email is a forward based on subject and/or body content.

    Checks:
    1. Subject starts with FW:, Fwd:, or RE: FW: (case-insensitive)
    2. Body contains forwarded message markers (Gmail, Outlook, Apple Mail)

    Returns:
        True if the email appears to be forwarded.
    """
    # Check subject line
    if subject:
        # Strip any leading RE: prefixes first, then check for FW/Fwd
        cleaned_subject = re.sub(r"^\s*(re\s*:\s*)+", "", subject, flags=re.IGNORECASE)
        if _FORWARD_SUBJECT_PATTERN.match(cleaned_subject):
            return True
        # Also check the original subject directly
        if _FORWARD_SUBJECT_PATTERN.match(subject):
            return True

    # Check body for forwarded message markers
    if body_text:
        for marker in _FORWARD_BODY_MARKERS:
            if marker in body_text:
                return True

    return False


def _extract_candidate_from_forwarded_body(body_text: str) -> Optional[dict]:
    """
    Attempt to extract the original sender (candidate) from a forwarded email body.

    Looks for a 'From:' line after a forwarded-message marker and extracts
    the name and email address.

    Returns:
        Dict with 'name' and 'email' keys, or None if extraction fails.
    """
    if not body_text:
        return None

    # Find the position of the first forwarded-message marker
    marker_pos = -1
    for marker in _FORWARD_BODY_MARKERS:
        pos = body_text.find(marker)
        if pos >= 0 and (marker_pos < 0 or pos < marker_pos):
            marker_pos = pos

    # Only look for From: lines AFTER the forwarded message marker
    search_text = body_text[marker_pos:] if marker_pos >= 0 else body_text

    # Try to match "From: Name <email>"
    match = _FROM_LINE_PATTERN.search(search_text)
    if match:
        name = match.group(1).strip().strip('"')
        email = match.group(2).strip()
        return {"name": name, "email": email}

    # Try to match "From: email@domain.com" (without angle brackets)
    match = _FROM_LINE_EMAIL_ONLY.search(search_text)
    if match:
        email = match.group(1).strip()
        # Derive name from email local part
        local = email.split("@")[0]
        name = local.replace(".", " ").replace("_", " ").title()
        return {"name": name, "email": email}

    return None


def _should_skip_sender_as_applicant(
    is_forwarded: bool,
    has_resume_attachments: bool,
    sender_email: str,
) -> bool:
    """
    Determine whether to skip creating a Job Applicant for the email sender.

    When an email is forwarded, the sender is typically a recruiter or internal
    user — NOT the actual candidate. In this case, we should NOT create a
    Job Applicant record for the sender.

    The actual candidate identity comes from:
    - Resume attachments (processed by _process_single_resume, which extracts
      the candidate name/email from the resume content via BAML)
    - The forwarded email body (From: line after the forward marker)

    Returns:
        True if the sender should NOT be used as the applicant.
    """
    if not is_forwarded:
        return False

    # Forwarded emails: always skip the sender as applicant
    # The candidate identity should come from the resume content or forwarded body
    return True


def _extract_source_from_forwarded_email(
    subject: Optional[str],
    body_text: Optional[str],
    sender_name: str,
    sender_email: str,
) -> Optional[str]:
    """
    Extract the referral source identity from a forwarded email.

    For forwarded emails, the "source" is the original sender (typically a recruiter)
    whose email was forwarded. This function extracts that identity as a formatted string.

    Logic:
    1. If the email is NOT forwarded → return None (use default source)
    2. If forwarded and the body contains a From: line after a forward marker
       → return "Name <email>" or just "email" if no name
    3. If forwarded but no From: line is parseable
       → return "SenderName <sender_email>" (the forwarder is the referral source)

    Args:
        subject: Email subject line.
        body_text: Plain text body of the email.
        sender_name: Display name of the email sender (the forwarder).
        sender_email: Email address of the sender (the forwarder).

    Returns:
        Referral source string, or None if the email is not forwarded.
    """
    if not _is_forwarded_email(subject=subject, body_text=body_text):
        return None

    # Try to extract the original sender from the forwarded body
    original_sender = _extract_candidate_from_forwarded_body(body_text or "")
    if original_sender:
        name = original_sender.get("name", "")
        email = original_sender.get("email", "")
        if name and email:
            return f"{name} <{email}>"
        elif email:
            return email

    # Fallback: the forwarder themselves are the referral source
    if sender_name:
        return f"{sender_name} <{sender_email}>"
    return sender_email


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — initialize shared resources."""
    settings = get_settings()
    app.state.settings = settings
    app.state.erpnext = ERPNextClient(
        base_url=settings.erpnext_url,
        api_key=settings.erpnext_api_key,
        api_secret=settings.erpnext_api_secret,
    )
    logger.info(f"Enrichment Worker started. ERPNext: {settings.erpnext_url}")
    yield
    logger.info("Enrichment Worker shutting down.")


app = FastAPI(
    title="AI Talent Sourcing Enrichment Worker",
    description=(
        "Processes inbound email webhooks from PrimitiveMail (self-hosted) "
        "and creates enriched Job Applicants in ERPNext."
    ),
    version="2.3.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "enrichment-worker", "version": "2.3.0"}


@app.post("/webhook/inbound-email")
async def handle_inbound_email(request: Request, background_tasks: BackgroundTasks):
    """
    Process an inbound email webhook from PrimitiveMail watcher.

    Returns 200 immediately to acknowledge receipt, then processes
    the email asynchronously in the background.

    Multi-resume: If the email contains multiple resume attachments,
    each is processed as a separate Job Applicant.

    Idempotency: Uses event id as dedup key.
    """
    settings: Settings = request.app.state.settings
    erpnext: ERPNextClient = request.app.state.erpnext

    # Step 1: Verify HMAC signature
    body = await get_verified_body(request, settings.webhook_secret)
    logger.info("Webhook signature verified")

    # Step 2: Parse payload (SDK envelope or legacy flat format)
    try:
        payload = parse_webhook_payload(body)
    except Exception as e:
        logger.error(f"Payload parse error: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    # Step 3: Idempotency check
    event_id = payload.event_id
    if not event_id:
        # Generate a deterministic ID from sender + subject + timestamp
        raw = f"{payload.sender_email}:{payload.subject}:{payload.received_at}"
        event_id = hashlib.sha256(raw.encode()).hexdigest()[:16]

    if _is_duplicate(event_id):
        logger.info(f"Duplicate event {event_id} — skipping (idempotency)")
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "event_id": event_id},
        )

    # Mark as processing immediately to prevent concurrent duplicates
    _mark_processed(event_id)

    logger.info(
        f"Accepted email from {payload.sender_email} "
        f"(subject: {payload.subject}, event_id: {event_id})"
    )

    # Step 4: Return 200 immediately, process in background
    background_tasks.add_task(
        _process_email,
        payload=payload,
        settings=settings,
        erpnext=erpnext,
        event_id=event_id,
    )

    return JSONResponse(
        status_code=200,
        content={"status": "accepted", "event_id": event_id},
    )


async def _process_email(
    payload: PrimitiveWebhookPayload,
    settings: Settings,
    erpnext: ERPNextClient,
    event_id: str,
) -> None:
    """
    Background task: download attachments, extract text from each resume,
    run BAML, push to ERPNext.

    Multi-resume logic:
    - Downloads the tar.gz archive once
    - Identifies all resume files (PDF/DOCX, excluding cover letters)
    - Processes each resume independently → one Job Applicant per resume
    - If no resumes found, falls back to email body text
      (but ONLY if the email is not a forward)
    """
    try:
        # Download the attachments archive
        archive_files = {}  # filename -> bytes
        download_url = payload.attachments_download_url
        if download_url:
            download_url = download_url.replace(
                "http://localhost:4001", "http://primitivemail-watcher:4001"
            )
            logger.info(f"Downloading attachments from: {download_url}")
            archive_files = await _download_all_attachments(
                download_url,
                payload.attachments,
                settings.max_attachment_size,
            )

        # Separate resumes from cover letters
        resume_files = {}
        cover_letter_files = {}
        for filename, file_bytes in archive_files.items():
            if _is_resume_file(filename):
                resume_files[filename] = file_bytes
            elif _is_cover_letter(filename):
                cover_letter_files[filename] = file_bytes

        logger.info(
            f"Found {len(resume_files)} resume(s) and "
            f"{len(cover_letter_files)} cover letter(s) in archive"
        )

        if resume_files:
            # Determine the source for this email (recruiter referral or direct)
            referred_by = _extract_source_from_forwarded_email(
                subject=payload.subject,
                body_text=payload.body_text,
                sender_name=payload.sender_name,
                sender_email=payload.sender_email,
            )
            source = "Referral" if referred_by else "Resume Upload"

            # Process each resume as a separate Job Applicant
            for filename, file_bytes in resume_files.items():
                await _process_single_resume(
                    filename=filename,
                    file_bytes=file_bytes,
                    payload=payload,
                    erpnext=erpnext,
                    event_id=event_id,
                    cover_letters=cover_letter_files,
                    source=source,
                    referred_by=referred_by,
                )
        else:
            # No resume attachments — check if this is a forwarded email
            is_forwarded = _is_forwarded_email(
                subject=payload.subject,
                body_text=payload.body_text,
            )

            if _should_skip_sender_as_applicant(
                is_forwarded=is_forwarded,
                has_resume_attachments=False,
                sender_email=payload.sender_email,
            ):
                logger.info(
                    f"Forwarded email from {payload.sender_email} with no resume "
                    f"attachments — skipping body-text fallback to avoid creating "
                    f"a Job Applicant for the forwarder"
                )
                return

            # Not a forward — process body text as a direct application
            logger.info("No resume attachments found — using email body text")
            await _process_body_text_fallback(
                payload=payload,
                erpnext=erpnext,
                event_id=event_id,
            )

    except Exception as e:
        logger.error(
            f"Background processing failed for event {event_id}: {e}",
            exc_info=True,
        )


async def _process_single_resume(
    filename: str,
    file_bytes: bytes,
    payload: PrimitiveWebhookPayload,
    erpnext: ERPNextClient,
    event_id: str,
    cover_letters: dict[str, bytes],
    source: str = "Resume Upload",
    referred_by: Optional[str] = None,
) -> None:
    """Process a single resume attachment → one Job Applicant.

    Args:
        filename: Resume filename.
        file_bytes: Raw file content.
        payload: Original webhook payload.
        erpnext: ERPNext API client.
        event_id: Idempotency event ID.
        cover_letters: Dict of cover letter filename → bytes.
        source: The custom_source value (e.g., 'Resume Upload' or 'Referral').
        referred_by: If source is 'Referral', the identity of the referrer.
    """
    logger.info(f"Processing resume: {filename} ({len(file_bytes)} bytes)")

    # Extract text from the resume file
    resume_text = None
    if filename.lower().endswith(".pdf"):
        resume_text = extract_text_from_pdf(file_bytes)
    elif filename.lower().endswith((".docx", ".doc")):
        resume_text = extract_text_from_docx(file_bytes)

    if not resume_text or len(resume_text) < 50:
        logger.warning(f"Could not extract meaningful text from {filename}")
        return

    logger.info(f"Extracted {len(resume_text)} chars from {filename}")

    # Run BAML extraction
    try:
        enriched_data = await extract_resume(resume_text)
    except Exception as e:
        logger.error(f"BAML extraction failed for {filename}: {e}")
        # Create minimal record with filename-derived info
        enriched_data = {
            "applicant_name": _name_from_filename(filename),
            "email_id": None,
        }

    # If BAML didn't extract an email, use a generated placeholder
    # (do NOT use the sender email — they're the recruiter, not the candidate)
    candidate_email = enriched_data.get("email_id")
    if not candidate_email:
        # Generate a deterministic placeholder email for dedup
        candidate_name = enriched_data.get("applicant_name", "Unknown")
        safe_name = candidate_name.lower().replace(" ", ".").replace(",", "")
        candidate_email = f"{safe_name}@candidate.talent.amdg.ai"
        enriched_data["email_id"] = candidate_email
        logger.info(
            f"No email found in resume for {candidate_name} — "
            f"using placeholder: {candidate_email}"
        )

    # Add referral source to enriched_data if this is a forwarded/referred candidate
    if referred_by:
        enriched_data["referred_by"] = referred_by

    # Create/update Job Applicant
    job_applicant = erpnext.upsert_job_applicant(
        enriched_data,
        source=source,
        message_id=payload.message_id,
    )

    doc_name = job_applicant.get("name")
    if not doc_name:
        logger.error("ERPNext did not return a 'name' field — cannot attach files")
        return

    # Upload the resume file (private — PII protection)
    try:
        erpnext.upload_file(
            file_content=file_bytes,
            filename=filename,
            doctype="Job Applicant",
            docname=doc_name,
            is_private=True,
        )
    except Exception as e:
        logger.error(f"Resume upload failed for {filename} (non-fatal): {e}")

    # Upload associated cover letter if one exists
    associated_cover = _find_associated_cover_letter(filename, cover_letters)
    if associated_cover:
        cl_filename, cl_bytes = associated_cover
        try:
            erpnext.upload_file(
                file_content=cl_bytes,
                filename=cl_filename,
                doctype="Job Applicant",
                docname=doc_name,
                is_private=True,
            )
            logger.info(f"Attached cover letter: {cl_filename}")
        except Exception as e:
            logger.error(f"Cover letter upload failed (non-fatal): {e}")

    # Create Communication record for the email thread
    try:
        erpnext.create_communication(
            sender=payload.sender_email,
            recipients=", ".join(payload.to) if payload.to else "",
            subject=payload.subject or "(No Subject)",
            content=payload.body_html or payload.body_text or "",
            reference_doctype="Job Applicant",
            reference_name=doc_name,
        )
    except Exception as e:
        logger.error(f"Communication creation failed (non-fatal): {e}")

    logger.info(f"Successfully processed {filename} -> Job Applicant: {doc_name}")


async def _process_body_text_fallback(
    payload: PrimitiveWebhookPayload,
    erpnext: ERPNextClient,
    event_id: str,
) -> None:
    """
    Fallback: create a Job Applicant from email body text when no attachments.

    IMPORTANT: This function should only be called for DIRECT applications
    (not forwarded emails). The caller (_process_email) checks for forwards
    and skips this function if the email is forwarded.
    """
    # Double-check: if this is a forwarded email, do NOT create a record
    # for the sender (defensive guard in case caller logic changes)
    if _is_forwarded_email(subject=payload.subject, body_text=payload.body_text):
        logger.warning(
            f"_process_body_text_fallback called for forwarded email from "
            f"{payload.sender_email} — skipping to avoid incorrect record"
        )
        return

    resume_text = payload.body_text or ""
    if not resume_text or len(resume_text) < 50:
        logger.warning("No resume text available — creating minimal record")
        enriched_data = {
            "applicant_name": payload.sender_name,
            "email_id": payload.sender_email,
        }
    else:
        try:
            enriched_data = await extract_resume(resume_text)
        except Exception as e:
            logger.error(f"BAML extraction failed: {e}")
            enriched_data = {
                "applicant_name": payload.sender_name,
                "email_id": payload.sender_email,
            }

    if not enriched_data.get("email_id"):
        enriched_data["email_id"] = payload.sender_email

    job_applicant = erpnext.upsert_job_applicant(
        enriched_data,
        source="Resume Upload",
        message_id=payload.message_id,
    )

    doc_name = job_applicant.get("name")
    if not doc_name:
        logger.error("ERPNext did not return a 'name' field")
        return

    try:
        erpnext.create_communication(
            sender=payload.sender_email,
            recipients=", ".join(payload.to) if payload.to else "",
            subject=payload.subject or "(No Subject)",
            content=payload.body_html or payload.body_text or "",
            reference_doctype="Job Applicant",
            reference_name=doc_name,
        )
    except Exception as e:
        logger.error(f"Communication creation failed (non-fatal): {e}")

    logger.info(f"Successfully processed (body text fallback) -> Job Applicant: {doc_name}")


async def _download_all_attachments(
    download_url: str,
    attachments: list,
    max_size: int,
) -> dict[str, bytes]:
    """
    Download the tar.gz archive and extract all resume-compatible files.

    Returns a dict of filename -> file_bytes for all PDF/DOCX files.
    """
    result = {}

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(download_url)
            if resp.status_code != 200:
                logger.error(f"Failed to download attachment archive: {resp.status_code}")
                return result

            archive_bytes = resp.content
            if len(archive_bytes) > max_size:
                logger.error(f"Attachment archive too large: {len(archive_bytes)} bytes")
                return result

        # Extract all resume-compatible files from the tar.gz archive
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile():
                    continue
                lower_name = member.name.lower()
                # Skip tiny files (likely thumbnails/artifacts)
                if member.size < 1000:
                    continue
                # Extract PDF and DOCX files
                if any(lower_name.endswith(ext) for ext in RESUME_EXTENSIONS):
                    f = tar.extractfile(member)
                    if f:
                        file_bytes = f.read()
                        # Use the original filename from attachments metadata if available
                        original_name = _match_attachment_name(member.name, attachments)
                        result[original_name] = file_bytes
                        logger.info(
                            f"Extracted '{original_name}' ({len(file_bytes)} bytes) from archive"
                        )

    except Exception as e:
        logger.error(f"Failed to extract from archive: {e}")

    return result


def _match_attachment_name(tar_member_name: str, attachments: list) -> str:
    """
    Match a tar member path back to the original attachment filename.

    The tar archive uses tar_path (e.g., "0/filename.pdf") but we want
    the human-readable original filename.
    """
    for att in attachments:
        tar_path = att.tar_path if hasattr(att, "tar_path") else att.get("tar_path", "")
        filename = att.filename if hasattr(att, "filename") else att.get("filename", "")
        if tar_path == tar_member_name or tar_member_name.endswith(filename):
            return filename
    # Fallback: use the basename of the tar member
    return tar_member_name.split("/")[-1] if "/" in tar_member_name else tar_member_name


def _name_from_filename(filename: str) -> str:
    """Extract a candidate name from a resume filename (best effort)."""
    # Remove extension
    name = filename.rsplit(".", 1)[0]
    # Remove common suffixes
    for suffix in ["_resume", "_Resume", "-resume", " resume", " Resume",
                   "_AMDG", "_2026", "_2025", "(2026)", "(2025)"]:
        name = name.replace(suffix, "")
    # Replace separators with spaces
    name = name.replace("_", " ").replace("-", " ")
    # Title case
    return name.strip().title()


def _find_associated_cover_letter(
    resume_filename: str,
    cover_letters: dict[str, bytes],
) -> Optional[tuple[str, bytes]]:
    """
    Find a cover letter that belongs to the same candidate as the resume.

    Heuristic: match by shared name components in the filename.
    """
    if not cover_letters:
        return None

    # Extract name tokens from resume filename
    resume_name = _name_from_filename(resume_filename).lower()
    resume_tokens = set(resume_name.split())

    best_match = None
    best_score = 0

    for cl_filename, cl_bytes in cover_letters.items():
        cl_name = cl_filename.lower()
        # Count shared tokens
        score = sum(1 for token in resume_tokens if token in cl_name and len(token) > 2)
        if score > best_score:
            best_score = score
            best_match = (cl_filename, cl_bytes)

    # Require at least one meaningful name token match
    if best_score >= 1:
        return best_match

    return None


@app.post("/webhook/test")
async def handle_test_webhook(request: Request):
    """
    Test endpoint that processes a resume without HMAC verification.
    Only for development/testing — disable in production via DISABLE_TEST_ENDPOINT env var.
    """
    settings: Settings = request.app.state.settings
    if settings.disable_test_endpoint:
        raise HTTPException(status_code=404, detail="Not found")

    erpnext: ERPNextClient = request.app.state.erpnext

    body = await request.body()
    data = json.loads(body)

    resume_text = data.get("resume_text", "")
    source = data.get("source", "Resume Upload")

    if not resume_text:
        raise HTTPException(status_code=400, detail="resume_text is required")

    enriched_data = await extract_resume(resume_text)
    job_applicant = erpnext.upsert_job_applicant(enriched_data, source=source)

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "job_applicant": job_applicant.get("name"),
            "enriched_data": enriched_data,
        },
    )
