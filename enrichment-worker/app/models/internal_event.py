"""
Normalized Internal Event Schema — decouples the enrichment pipeline from
any specific email provider (PrimitiveMail, Gmail IMAP, Outlook, etc.).

Design rationale:
- Provider-agnostic: adapters translate raw payloads into this schema
- Immutable after creation: once built, the event is a frozen record
- Contains everything needed for enrichment without re-fetching
- Supports idempotency via message_id + provider_event_id
- Tracks provenance (which provider, which endpoint, when received)
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class EmailProvider(str, Enum):
    """Supported email ingestion providers."""
    PRIMITIVE_SELF_HOST = "primitive_self_host"
    PRIMITIVE_MANAGED = "primitive_managed"
    GMAIL_IMAP = "gmail_imap"
    ERPNEXT_EMAIL_ACCOUNT = "erpnext_email_account"
    MANUAL_UPLOAD = "manual_upload"


class AuthResult(str, Enum):
    """SPF/DKIM/DMARC result values."""
    PASS = "pass"
    FAIL = "fail"
    SOFTFAIL = "softfail"
    NONE = "none"
    UNKNOWN = "unknown"


class ProcessingStatus(str, Enum):
    """Lifecycle status of the inbound email event."""
    RECEIVED = "received"           # Webhook received, queued
    DOWNLOADING = "downloading"     # Fetching attachments
    SCANNING = "scanning"           # ClamAV scanning
    EXTRACTING = "extracting"       # Text extraction from attachments
    ENRICHING = "enriching"         # BAML AI extraction
    PUSHING = "pushing"             # Pushing to ERPNext
    COMPLETE = "complete"           # Successfully processed
    FAILED = "failed"               # Terminal failure
    QUARANTINED = "quarantined"     # ClamAV flagged attachment


@dataclass(frozen=True)
class EmailAttachment:
    """A single email attachment, normalized across providers."""
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    # Content is populated after download; None means not yet fetched
    content_bytes: Optional[bytes] = field(default=None, repr=False)
    # Provider-specific download URL (expires; fetch immediately)
    download_url: Optional[str] = None
    # Local path after download (persisted to disk)
    local_path: Optional[str] = None
    # ClamAV scan result
    scan_clean: Optional[bool] = None
    scan_result: Optional[str] = None


@dataclass(frozen=True)
class EmailAuth:
    """Authentication results from the sending MTA."""
    spf: AuthResult = AuthResult.UNKNOWN
    dkim: AuthResult = AuthResult.UNKNOWN
    dkim_domain: Optional[str] = None
    dmarc: AuthResult = AuthResult.UNKNOWN
    dmarc_policy: Optional[str] = None
    dmarc_from_domain: Optional[str] = None


@dataclass
class InboundEmailEvent:
    """
    Normalized inbound email event — the single internal representation
    that all provider adapters produce and all pipeline stages consume.

    Immutable fields are set at creation; mutable fields track processing state.
    """

    # === Identity (immutable after creation) ===
    # RFC 5322 Message-ID header (primary dedup key)
    message_id: str
    # Provider-specific event/delivery ID (secondary dedup key)
    provider_event_id: str
    # Which provider delivered this event
    provider: EmailProvider
    # Stable fingerprint for idempotency: sha256(message_id + provider_event_id)
    idempotency_key: str = field(init=False)

    # === Envelope (immutable) ===
    envelope_from: str          # SMTP MAIL FROM
    envelope_to: list[str]      # SMTP RCPT TO (may be multiple)
    # Parsed header fields
    header_from: str            # From: header (display name + address)
    header_from_address: str    # Bare email address extracted from From:
    header_to: str              # To: header
    header_subject: Optional[str] = None
    header_date: Optional[str] = None

    # === Body (immutable) ===
    body_text: Optional[str] = None
    body_html: Optional[str] = None

    # === Attachments (mutable: content_bytes populated after download) ===
    attachments: list[EmailAttachment] = field(default_factory=list)
    # Provider's bulk attachment download URL (e.g., Primitive's tar.gz URL)
    attachments_download_url: Optional[str] = None

    # === Authentication (immutable) ===
    auth: EmailAuth = field(default_factory=EmailAuth)

    # === Provenance (immutable) ===
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Provider endpoint ID (for Primitive: sha256(url+secret)[:16])
    provider_endpoint_id: Optional[str] = None
    # Raw .eml download URL (Primitive-specific, expires in 15min)
    raw_download_url: Optional[str] = None

    # === Processing State (mutable) ===
    status: ProcessingStatus = ProcessingStatus.RECEIVED
    status_detail: Optional[str] = None
    processing_started_at: Optional[datetime] = None
    processing_completed_at: Optional[datetime] = None
    retry_count: int = 0
    max_retries: int = 3
    # ERPNext document name after successful push
    erpnext_document_name: Optional[str] = None

    def __post_init__(self):
        """Compute idempotency key from message_id + provider_event_id."""
        raw = f"{self.message_id}:{self.provider_event_id}"
        object.__setattr__(
            self, "idempotency_key",
            hashlib.sha256(raw.encode()).hexdigest()[:32]
        )

    @property
    def has_resume_attachment(self) -> bool:
        """Check if any attachment looks like a resume (PDF/DOCX)."""
        resume_types = {
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        }
        resume_extensions = {".pdf", ".docx", ".doc"}
        for att in self.attachments:
            if att.content_type in resume_types:
                return True
            if att.filename and any(att.filename.lower().endswith(ext) for ext in resume_extensions):
                return True
        return False

    @property
    def resume_attachments(self) -> list[EmailAttachment]:
        """Return only attachments that look like resumes."""
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
            elif att.filename and any(att.filename.lower().endswith(ext) for ext in resume_extensions):
                results.append(att)
        return results

    def to_log_dict(self) -> dict:
        """Serialize to a dict suitable for the ATS Inbound Email Log."""
        return {
            "message_id": self.message_id,
            "provider_event_id": self.provider_event_id,
            "idempotency_key": self.idempotency_key,
            "provider": self.provider.value,
            "envelope_from": self.envelope_from,
            "envelope_to": ", ".join(self.envelope_to),
            "header_from": self.header_from,
            "header_from_address": self.header_from_address,
            "header_subject": self.header_subject,
            "attachment_count": len(self.attachments),
            "has_resume": self.has_resume_attachment,
            "auth_spf": self.auth.spf.value,
            "auth_dkim": self.auth.dkim.value,
            "auth_dmarc": self.auth.dmarc.value,
            "received_at": self.received_at.isoformat(),
            "status": self.status.value,
            "status_detail": self.status_detail,
            "retry_count": self.retry_count,
            "erpnext_document_name": self.erpnext_document_name,
        }
