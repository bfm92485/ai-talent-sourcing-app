"""
Gmail/IMAP Adapter — translates an ERPNext Communication record (created
by ERPNext's native Email Account IMAP pull) into our normalized
InboundEmailEvent schema.

This adapter is used in Approach A (ERPNext after_insert hook):
1. ERPNext Email Account pulls from Gmail via IMAP
2. ERPNext auto-creates Job Applicant + Communication
3. after_insert hook fires, reads Communication fields
4. This adapter normalizes the Communication into InboundEmailEvent
5. Pipeline proceeds with attachment download, BAML extraction, etc.

The Communication DocType fields we consume:
- sender (email address)
- sender_full_name
- subject
- content (HTML body)
- text_content (plain text body)
- message_id (RFC 5322 Message-ID)
- communication_date
- attachments (via File DocType linked to Communication)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models.internal_event import (
    AuthResult,
    EmailAttachment,
    EmailAuth,
    EmailProvider,
    InboundEmailEvent,
)


def adapt_erpnext_communication(comm: dict[str, Any], attachments: list[dict] | None = None) -> InboundEmailEvent:
    """
    Convert an ERPNext Communication record dict into our normalized
    InboundEmailEvent.

    Args:
        comm: Communication DocType fields (from frappe.get_doc or REST API)
        attachments: List of File DocType records attached to the Communication
                     Each has: file_name, file_url, file_size, content_type

    Returns:
        InboundEmailEvent ready for pipeline processing
    """
    sender_email = comm.get("sender", "")
    sender_name = comm.get("sender_full_name", "")
    header_from = f"{sender_name} <{sender_email}>" if sender_name else sender_email

    # Build attachment list from ERPNext File records
    att_list = []
    if attachments:
        for att in attachments:
            att_list.append(EmailAttachment(
                filename=att.get("file_name", "unknown"),
                content_type=att.get("content_type", "application/octet-stream"),
                size_bytes=att.get("file_size", 0),
                sha256="",  # ERPNext doesn't store sha256 of attachments
                download_url=att.get("file_url"),  # Relative URL on ERPNext
            ))

    # Parse communication_date
    comm_date_str = comm.get("communication_date")
    if comm_date_str:
        try:
            received_at = datetime.fromisoformat(str(comm_date_str))
            if not received_at.tzinfo:
                received_at = received_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            received_at = datetime.now(timezone.utc)
    else:
        received_at = datetime.now(timezone.utc)

    # ERPNext IMAP pull doesn't expose SPF/DKIM/DMARC — mark as unknown
    auth = EmailAuth(
        spf=AuthResult.UNKNOWN,
        dkim=AuthResult.UNKNOWN,
        dmarc=AuthResult.UNKNOWN,
    )

    return InboundEmailEvent(
        # Identity
        message_id=comm.get("message_id", ""),
        provider_event_id=comm.get("name", ""),  # ERPNext Communication name
        provider=EmailProvider.ERPNEXT_EMAIL_ACCOUNT,
        # Envelope (IMAP doesn't expose envelope; use header fields)
        envelope_from=sender_email,
        envelope_to=[comm.get("recipients", sender_email)],
        header_from=header_from,
        header_from_address=sender_email,
        header_to=comm.get("recipients", ""),
        header_subject=comm.get("subject"),
        header_date=comm_date_str,
        # Body
        body_text=comm.get("text_content"),
        body_html=comm.get("content"),
        # Attachments
        attachments=att_list,
        # Auth (not available from IMAP)
        auth=auth,
        # Provenance
        received_at=received_at,
    )
