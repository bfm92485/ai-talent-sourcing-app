"""Pydantic models for PrimitiveMail webhook payloads."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """Email attachment metadata from PrimitiveMail."""

    filename: str
    content_type: str
    size: int
    path: str  # Relative path on PrimitiveMail storage


class AuthResults(BaseModel):
    """SPF/DKIM/DMARC authentication results."""

    spf: Optional[str] = None
    dkim: Optional[str] = None
    dmarc: Optional[str] = None


class InboundEmailPayload(BaseModel):
    """
    Webhook payload sent by PrimitiveMail watcher when a new email arrives.

    This model represents the canonical JSON structure produced by the milter
    and delivered via the watcher's HTTP POST.
    """

    message_id: str = Field(..., description="RFC 2822 Message-ID")
    from_address: str = Field(..., alias="from", description="Sender (Name <email>)")
    to: list[str] = Field(default_factory=list, description="Recipient addresses")
    subject: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    attachments: list[Attachment] = Field(default_factory=list)
    received_at: Optional[datetime] = None
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
    def resume_attachments(self) -> list[Attachment]:
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
