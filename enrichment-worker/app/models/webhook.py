"""Pydantic models for Primitive (managed SaaS) webhook payloads.

Primitive delivers emails as JSON webhook events with:
- event_id: unique event identifier for idempotency
- message_id: RFC 2822 Message-ID
- attachments_download_url: URL to tar.gz archive of all attachments
- attachments[]: metadata with tar_path for extraction from archive

See: https://primitive.dev/docs (webhook delivery format)
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PrimitiveAttachment(BaseModel):
    """Attachment metadata from Primitive webhook payload."""

    filename: str
    content_type: str = ""
    size: int = 0
    tar_path: str = ""  # Path within the tar.gz archive


class AuthResults(BaseModel):
    """SPF/DKIM/DMARC authentication results."""

    spf: Optional[str] = None
    dkim: Optional[str] = None
    dmarc: Optional[str] = None


class PrimitiveWebhookPayload(BaseModel):
    """
    Webhook payload sent by Primitive (managed SaaS) when a new email arrives.

    Primitive is a managed email receiving service — no self-hosted VPS needed.
    The webhook is HMAC-signed and retried up to 6 times on non-2xx responses.
    """

    # Idempotency / dedup
    event_id: Optional[str] = Field(None, description="Unique event ID from Primitive")
    message_id: str = Field("", description="RFC 2822 Message-ID")

    # Sender/recipient
    from_address: str = Field("", alias="from", description="Sender (Name <email>)")
    to: list[str] = Field(default_factory=list, description="Recipient addresses")
    cc: list[str] = Field(default_factory=list, description="CC addresses")

    # Content
    subject: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None

    # Attachments
    attachments: list[PrimitiveAttachment] = Field(default_factory=list)
    attachments_download_url: Optional[str] = Field(
        None, description="URL to download tar.gz archive of all attachments"
    )

    # Metadata
    received_at: Optional[str] = None
    auth_results: Optional[AuthResults] = None

    class Config:
        populate_by_name = True

    @property
    def sender_email(self) -> str:
        """Extract email address from 'Name <email>' format."""
        addr = self.from_address
        if "<" in addr and ">" in addr:
            return addr.split("<")[1].split(">")[0].strip()
        return addr.strip()

    @property
    def sender_name(self) -> str:
        """Extract display name from 'Name <email>' format."""
        addr = self.from_address
        if "<" in addr:
            name = addr.split("<")[0].strip().strip('"')
            return name if name else self.sender_email
        return addr.strip()

    @property
    def resume_attachments(self) -> list[PrimitiveAttachment]:
        """Filter attachments to only resume-like files (PDF, DOCX)."""
        resume_types = {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        }
        resume_extensions = {".pdf", ".docx", ".doc"}
        results = []
        for att in self.attachments:
            if att.content_type in resume_types:
                results.append(att)
            elif any(att.filename.lower().endswith(ext) for ext in resume_extensions):
                results.append(att)
        return results
