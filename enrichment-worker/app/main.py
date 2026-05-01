"""
Enrichment Worker — FastAPI application for processing inbound email webhooks.

Receives HMAC-signed webhook payloads from PrimitiveMail, extracts resume text,
runs BAML AI extraction, and creates enriched Job Applicant records in ERPNext.
"""

import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .enrichment.baml_runner import extract_resume
from .erpnext.client import ERPNextClient
from .extractors.docx import extract_text_from_docx
from .extractors.pdf import extract_text_from_pdf
from .models.webhook import InboundEmailPayload
from .security import get_verified_body

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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
    description="Processes inbound email webhooks and creates enriched Job Applicants in ERPNext.",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint for Railway and monitoring."""
    return {"status": "healthy", "service": "enrichment-worker"}


@app.post("/webhook/inbound-email")
async def handle_inbound_email(request: Request):
    """
    Process an inbound email webhook from PrimitiveMail.

    Steps:
    1. Verify HMAC signature
    2. Parse webhook payload
    3. Download resume attachment (if any)
    4. Extract text from attachment or email body
    5. Run BAML ExtractResume
    6. Create/update Job Applicant in ERPNext
    7. Upload attachment and create Communication
    """
    settings: Settings = request.app.state.settings
    erpnext: ERPNextClient = request.app.state.erpnext

    # Step 1: Verify HMAC signature
    body = await get_verified_body(request, settings.webhook_secret)
    logger.info("Webhook signature verified")

    # Step 2: Parse payload
    try:
        payload = InboundEmailPayload.model_validate_json(body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")

    logger.info(
        f"Processing email from {payload.sender_email} "
        f"(subject: {payload.subject}, attachments: {len(payload.attachments)})"
    )

    # Step 3: Download resume attachment
    resume_text = None
    resume_bytes = None
    resume_filename = None

    resume_attachments = payload.resume_attachments
    if resume_attachments:
        att = resume_attachments[0]  # Use first resume-like attachment

        # Check size limit
        if att.size > settings.max_attachment_size:
            raise HTTPException(
                status_code=413,
                detail=f"Attachment too large: {att.size} bytes (max {settings.max_attachment_size})",
            )

        # Download from PrimitiveMail storage
        download_url = f"{settings.primitivemail_base_url}/attachments/{att.path}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(download_url)
            if resp.status_code != 200:
                logger.error(f"Failed to download attachment: {resp.status_code}")
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to download attachment from PrimitiveMail",
                )
            resume_bytes = resp.content
            resume_filename = att.filename

        # Step 4: Extract text
        if att.content_type == "application/pdf" or att.filename.lower().endswith(".pdf"):
            resume_text = extract_text_from_pdf(resume_bytes)
        elif att.filename.lower().endswith((".docx", ".doc")):
            resume_text = extract_text_from_docx(resume_bytes)
        else:
            logger.warning(f"Unsupported attachment type: {att.content_type}")

    # Fallback: use email body text if no attachment or extraction failed
    if not resume_text:
        resume_text = payload.body_text or ""
        if not resume_text:
            logger.warning("No resume text available — creating minimal record")

    # Step 5: Run BAML extraction (if we have text)
    if resume_text and len(resume_text) > 50:
        try:
            enriched_data = await extract_resume(resume_text)
        except Exception as e:
            logger.error(f"BAML extraction failed: {e}")
            # Create minimal record on failure
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

    # Step 6: Create/update Job Applicant
    try:
        job_applicant = erpnext.upsert_job_applicant(enriched_data, source="Email Inbound")
    except Exception as e:
        logger.error(f"ERPNext upsert failed: {e}")
        raise HTTPException(status_code=502, detail=f"ERPNext API error: {e}")

    applicant_name = job_applicant.get("name", enriched_data["email_id"])

    # Step 7a: Upload attachment if we have one
    if resume_bytes and resume_filename:
        try:
            erpnext.upload_file(
                file_content=resume_bytes,
                filename=resume_filename,
                doctype="Job Applicant",
                docname=applicant_name,
            )
        except Exception as e:
            logger.error(f"File upload failed (non-fatal): {e}")

    # Step 7b: Create Communication record
    try:
        erpnext.create_communication(
            sender=payload.sender_email,
            recipients=", ".join(payload.to) if payload.to else "",
            subject=payload.subject or "(No Subject)",
            content=payload.body_html or payload.body_text or "",
            reference_doctype="Job Applicant",
            reference_name=applicant_name,
        )
    except Exception as e:
        logger.error(f"Communication creation failed (non-fatal): {e}")

    logger.info(f"Successfully processed email → Job Applicant: {applicant_name}")

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "job_applicant": applicant_name,
            "enrichment_status": enriched_data.get("enrichment_status", "Complete")
            if len(resume_text or "") > 50
            else "Minimal",
        },
    )


@app.post("/webhook/test")
async def handle_test_webhook(request: Request):
    """
    Test endpoint that processes a resume without HMAC verification.
    Only for development/testing — disable in production.
    """
    settings: Settings = request.app.state.settings
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
