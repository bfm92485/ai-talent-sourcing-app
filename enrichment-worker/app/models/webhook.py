"""Pydantic models for PrimitiveMail self-hosted webhook payloads.

The PrimitiveMail SDK wraps email data in an EmailReceivedEvent envelope:

{
  "id": "evt_...",
  "event": "email.received",
  "version": "2025-12-14",
  "delivery": { "endpoint_id": "...", "attempt": 1, "attempted_at": "..." },
  "email": {
    "id": "20260503T051750Z-69d8b034",
    "received_at": "2026-05-03T05:17:50Z",
    "smtp": { "helo": "...", "mail_from": "...", "rcpt_to": [...] },
    "headers": { "message_id": "...", "subject": "...", "from": "...", "to": "...", "date": "..." },
    "content": { "raw": { "included": true, "encoding": "base64", "data": "..." } },
    "parsed": {
      "status": "complete",
      "body_text": "...",
      "body_html": "...",
      "attachments": [{ "filename": "...", "content_type": "...", "size_bytes": 0, "tar_path": "..." }]
    },
    "auth": { ... }
  },
  "download": { "url": "http://...", "expires_at": "..." },
  "attachments_download": { "url": "http://...", "expires_at": "..." }
}
"""

import json
import logging
from typing import Any, Optional, Union

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


class PrimitiveAttachment(BaseModel):
    """Attachment metadata from PrimitiveMail webhook payload."""

    filename: str = ""
    content_type: str = ""
    size_bytes: int = Field(0, alias="size_bytes")
    size: int = 0  # Legacy field name
    tar_path: str = ""  # Path within the tar.gz archive
    sha256: str = ""
    part_index: int = 0

    class Config:
        populate_by_name = True


class SmtpEnvelope(BaseModel):
    """SMTP envelope data from the milter."""

    helo: str = ""
    mail_from: str = ""
    rcpt_to: list[str] = Field(default_factory=list)


class EmailHeaders(BaseModel):
    """Parsed email headers."""

    message_id: Optional[str] = None
    subject: Optional[str] = None
    from_address: str = Field("", alias="from")
    to: Optional[str] = None
    date: Optional[str] = None

    class Config:
        populate_by_name = True


class ParsedContent(BaseModel):
    """Parsed email body and attachments."""

    status: str = "complete"
    error: Optional[str] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    reply_to: Optional[str] = None
    cc: Optional[str] = None
    bcc: Optional[str] = None
    in_reply_to: Optional[Union[str, list]] = None
    references: Optional[Union[str, list]] = None

    attachments: list[PrimitiveAttachment] = Field(default_factory=list)
    attachments_download_url: Optional[str] = None

    @field_validator("in_reply_to", "references", mode="before")
    @classmethod
    def coerce_to_string(cls, v):
        """Coerce list values to comma-separated string."""
        if isinstance(v, list):
            return ", ".join(str(item) for item in v)
        return v


class AuthResults(BaseModel):
    """SPF/DKIM/DMARC authentication results."""

    spf: Optional[str] = None
    dkim: Optional[str] = None
    dmarc: Optional[str] = None


class DownloadInfo(BaseModel):
    """Download URL and expiry for raw email or attachments."""

    url: str = ""
    expires_at: Optional[str] = None


class DeliveryInfo(BaseModel):
    """Delivery metadata from the SDK event envelope."""

    endpoint_id: str = ""
    attempt: int = 1
    attempted_at: Optional[str] = None


class EmailData(BaseModel):
    """The email data nested inside the SDK event envelope."""

    id: str = ""
    received_at: Optional[str] = None
    smtp: SmtpEnvelope = Field(default_factory=SmtpEnvelope)
    headers: EmailHeaders = Field(default_factory=EmailHeaders)
    parsed: ParsedContent = Field(default_factory=ParsedContent)
    auth: Optional[AuthResults] = None
    # content.raw contains the base64-encoded .eml (we don't need it)
    content: Optional[dict] = None

    class Config:
        populate_by_name = True


class PrimitiveWebhookPayload(BaseModel):
    """
    Webhook payload sent by PrimitiveMail watcher (self-hosted).

    The SDK wraps email data in an EmailReceivedEvent envelope.
    This model handles both the SDK envelope format and the legacy flat format.
    """

    # SDK envelope fields
    id: str = Field("", description="Event ID (evt_... for SDK, timestamp-hash for legacy)")
    event: Optional[str] = None  # "email.received" for SDK format
    version: Optional[str] = None  # SDK version e.g. "2025-12-14"
    delivery: Optional[DeliveryInfo] = None

    # SDK format: email data is nested
    email: Optional[EmailData] = None

    # SDK format: download URLs at top level
    download: Optional[DownloadInfo] = None
    attachments_download: Optional[DownloadInfo] = None

    # Legacy flat format fields (used when 'email' key is absent)
    received_at: Optional[str] = None
    smtp: SmtpEnvelope = Field(default_factory=SmtpEnvelope)
    headers: EmailHeaders = Field(default_factory=EmailHeaders)
    parsed: ParsedContent = Field(default_factory=ParsedContent)
    auth: Optional[AuthResults] = None

    class Config:
        populate_by_name = True

    @property
    def _email_data(self) -> "EmailData":
        """Get the email data, whether from SDK envelope or legacy flat format."""
        if self.email is not None:
            return self.email
        # Legacy flat format — construct EmailData from top-level fields
        return EmailData(
            id=self.id,
            received_at=self.received_at,
            smtp=self.smtp,
            headers=self.headers,
            parsed=self.parsed,
            auth=self.auth,
        )

    @property
    def event_id(self) -> str:
        """Unique event identifier for idempotency."""
        return self.id

    @property
    def message_id(self) -> str:
        """RFC 2822 Message-ID."""
        return self._email_data.headers.message_id or ""

    @property
    def subject(self) -> Optional[str]:
        return self._email_data.headers.subject

    @property
    def from_address(self) -> str:
        return self._email_data.headers.from_address

    @property
    def sender_email(self) -> str:
        """Extract email address from 'Name <email>' format or SMTP envelope."""
        addr = self._email_data.headers.from_address
        if "<" in addr and ">" in addr:
            return addr.split("<")[1].split(">")[0].strip()
        if addr:
            return addr.strip()
        # Fallback to SMTP envelope
        return self._email_data.smtp.mail_from.strip()

    @property
    def sender_name(self) -> str:
        """Extract display name from 'Name <email>' format."""
        addr = self._email_data.headers.from_address
        if "<" in addr:
            name = addr.split("<")[0].strip().strip('"')
            return name if name else self.sender_email
        return addr.strip()

    @property
    def to(self) -> list[str]:
        """Recipient list from SMTP envelope."""
        return self._email_data.smtp.rcpt_to

    @property
    def body_text(self) -> Optional[str]:
        return self._email_data.parsed.body_text

    @property
    def body_html(self) -> Optional[str]:
        return self._email_data.parsed.body_html

    @property
    def attachments(self) -> list[PrimitiveAttachment]:
        return self._email_data.parsed.attachments

    @property
    def attachments_download_url(self) -> Optional[str]:
        """Get attachments download URL from SDK envelope or parsed content."""
        # SDK format: top-level attachments_download.url
        if self.attachments_download and self.attachments_download.url:
            return self.attachments_download.url
        # Legacy format: inside parsed content
        return self._email_data.parsed.attachments_download_url

    @property
    def resume_attachments(self) -> list[PrimitiveAttachment]:
        """Filter attachments to only resume-like files (PDF, DOCX)."""
        resume_extensions = {".pdf", ".docx", ".doc"}
        return [
            att for att in self._email_data.parsed.attachments
            if any(att.filename.lower().endswith(ext) for ext in resume_extensions)
        ]


def parse_webhook_payload(body: bytes) -> PrimitiveWebhookPayload:
    """
    Parse webhook payload, handling both SDK envelope and legacy flat formats.

    Args:
        body: Raw JSON bytes from the webhook request.

    Returns:
        Parsed PrimitiveWebhookPayload instance.
    """
    return PrimitiveWebhookPayload.model_validate_json(body)
