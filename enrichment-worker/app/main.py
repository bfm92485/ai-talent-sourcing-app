"""
Enrichment Worker — FastAPI application for processing inbound email webhooks.

Architecture (self-hosted PrimitiveMail on Hetzner VPS):
- PrimitiveMail runs on same VPS: milter + watcher + this worker
- Watcher fires webhook to http://localhost:8090/webhook/inbound-email
- Attachments served by watcher at http://localhost:4001/download/...
- Worker extracts resume, runs BAML, pushes to ERPNext (Railway)
"""

import asyncio
import hashlib
import json
import logging
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


def _is_duplicate(event_id: str) -> bool:
    """Check if this event has already been processed (idempotency guard)."""
    return event_id in _processed_events


def _mark_processed(event_id: str) -> None:
    """Mark an event as processed. Evicts oldest entries if cache is full."""
    _processed_events[event_id] = True
    if len(_processed_events) > _DEDUP_MAX_SIZE:
        _processed_events.popitem(last=False)


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
    version="2.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "enrichment-worker", "version": "2.1.0"}


@app.post("/webhook/inbound-email")
async def handle_inbound_email(request: Request, background_tasks: BackgroundTasks):
    """
    Process an inbound email webhook from PrimitiveMail watcher.

    Returns 200 immediately to acknowledge receipt, then processes
    the email asynchronously in the background.

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
    Background task: download attachment, extract text, run BAML, push to ERPNext.
    """
    try:
        resume_text = None
        resume_bytes = None
        resume_filename = None

        # Download attachments from PrimitiveMail's tar.gz archive
        download_url = payload.attachments_download_url
        if download_url:
            # Rewrite localhost URLs to Docker service name (same network)
            download_url = download_url.replace(
                "http://localhost:4001", "http://primitivemail-watcher:4001"
            )
            logger.info(f"Downloading attachments from: {download_url}")
            resume_bytes, resume_filename = await _download_resume_from_archive(
                download_url,
                payload.attachments,
                settings.max_attachment_size,
            )

        # Extract text from attachment
        if resume_bytes and resume_filename:
            if resume_filename.lower().endswith(".pdf"):
                resume_text = extract_text_from_pdf(resume_bytes)
            elif resume_filename.lower().endswith((".docx", ".doc")):
                resume_text = extract_text_from_docx(resume_bytes)
            else:
                logger.warning(f"Unsupported attachment type: {resume_filename}")

        # Fallback: use email body text
        if not resume_text:
            resume_text = payload.body_text or ""
            if not resume_text:
                logger.warning("No resume text available — creating minimal record")

        # Run BAML extraction
        if resume_text and len(resume_text) > 50:
            try:
                enriched_data = await extract_resume(resume_text)
            except Exception as e:
                logger.error(f"BAML extraction failed: {e}")
                enriched_data = {
                    "applicant_name": payload.sender_name,
                    "email_id": payload.sender_email,
                }
        else:
            enriched_data = {
                "applicant_name": payload.sender_name,
                "email_id": payload.sender_email,
            }

        # Override email with sender if BAML didn't find one
        if not enriched_data.get("email_id"):
            enriched_data["email_id"] = payload.sender_email

        # Create/update Job Applicant
        job_applicant = erpnext.upsert_job_applicant(
            enriched_data,
            source="Resume Upload",
            message_id=payload.message_id,
        )

        # Use the name returned by ERPNext for all downstream references
        doc_name = job_applicant.get("name")
        if not doc_name:
            logger.error("ERPNext did not return a 'name' field — cannot attach files")
            return

        # Upload attachment (private — PII protection)
        if resume_bytes and resume_filename:
            try:
                erpnext.upload_file(
                    file_content=resume_bytes,
                    filename=resume_filename,
                    doctype="Job Applicant",
                    docname=doc_name,
                    is_private=True,
                )
            except Exception as e:
                logger.error(f"File upload failed (non-fatal): {e}")

        # Create Communication record for email thread
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

        logger.info(f"Successfully processed email -> Job Applicant: {doc_name}")

    except Exception as e:
        logger.error(f"Background processing failed for event {event_id}: {e}", exc_info=True)


async def _download_resume_from_archive(
    download_url: str,
    attachments: list,
    max_size: int,
) -> tuple[Optional[bytes], Optional[str]]:
    """
    Download attachments from PrimitiveMail's tar.gz archive.

    PrimitiveMail watcher serves the archive at http://localhost:4001/download/...
    with a 15-minute expiry token.
    """
    resume_extensions = {".pdf", ".docx", ".doc"}
    resume_att = None
    for att in attachments:
        filename = att.filename if hasattr(att, "filename") else att.get("filename", "")
        if any(filename.lower().endswith(ext) for ext in resume_extensions):
            resume_att = att
            break

    if not resume_att:
        return None, None

    tar_path = resume_att.tar_path if hasattr(resume_att, "tar_path") else resume_att.get("tar_path", "")
    filename = resume_att.filename if hasattr(resume_att, "filename") else resume_att.get("filename", "")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(download_url)
            if resp.status_code != 200:
                logger.error(f"Failed to download attachment archive: {resp.status_code}")
                return None, None

            archive_bytes = resp.content
            if len(archive_bytes) > max_size:
                logger.error(f"Attachment archive too large: {len(archive_bytes)} bytes")
                return None, None

        # Extract the specific file from the tar.gz archive
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
            member = None
            for name_to_try in [tar_path, filename]:
                if name_to_try:
                    try:
                        member = tar.getmember(name_to_try)
                        break
                    except KeyError:
                        continue

            if not member:
                # Fallback: find first PDF/DOCX in the archive
                for m in tar.getmembers():
                    if any(m.name.lower().endswith(ext) for ext in resume_extensions):
                        member = m
                        break

            if member:
                f = tar.extractfile(member)
                if f:
                    file_bytes = f.read()
                    logger.info(f"Extracted '{member.name}' ({len(file_bytes)} bytes) from archive")
                    return file_bytes, filename or member.name

    except Exception as e:
        logger.error(f"Failed to extract from archive: {e}")

    return None, None


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
