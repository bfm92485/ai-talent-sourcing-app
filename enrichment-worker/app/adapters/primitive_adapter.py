"""
PrimitiveMail Adapter — translates the EmailReceivedEvent webhook payload
from PrimitiveMail (self-hosted or managed SaaS) into our normalized
InboundEmailEvent schema.

The webhook payload structure is defined by @primitivedotdev/sdk/contract.
This adapter handles both self-hosted and managed Primitive deployments
(identical payload format, different download URL origins).

Key behaviors:
- Extracts bare email address from RFC 5322 "Name <addr>" format
- Maps SPF/DKIM/DMARC auth results to our AuthResult enum
- Preserves download URLs for immediate fetching (15-min token expiry)
- Falls back gracefully on missing/malformed fields
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from ..models.internal_event import (
    AuthResult,
    EmailAttachment,
    EmailAuth,
    EmailProvider,
    InboundEmailEvent,
)


def _extract_address(from_header: str | None) -> str:
    """Extract bare email address from 'Display Name <addr@domain>' format."""
    if not from_header:
        return ""
    match = re.search(r"<([^>]+)>", from_header)
    return match.group(1) if match else from_header.strip()


def _map_auth_result(value: str | None) -> AuthResult:
    """Map a string auth result to our enum, defaulting to UNKNOWN."""
    if not value:
        return AuthResult.UNKNOWN
    mapping = {
        "pass": AuthResult.PASS,
        "fail": AuthResult.FAIL,
        "softfail": AuthResult.SOFTFAIL,
        "none": AuthResult.NONE,
    }
    return mapping.get(value.lower(), AuthResult.UNKNOWN)


def _parse_iso_datetime(iso_str: str | None) -> datetime:
    """Parse an ISO 8601 datetime string, falling back to now()."""
    if not iso_str:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def adapt_primitive_webhook(payload: dict[str, Any]) -> InboundEmailEvent:
    """
    Convert a PrimitiveMail EmailReceivedEvent webhook payload into
    our normalized InboundEmailEvent.

    Expected payload structure (from @primitivedotdev/sdk/contract):
    {
      "id": "event-id",
      "email": {
        "id": "canonical-email-id",
        "headers": { "message_id", "subject", "from", "to", "date" },
        "smtp": { "helo", "mail_from", "rcpt_to": [...] },
        "parsed": { "status", "body_text", "body_html", "attachments": [...], "attachments_download_url" },
        "auth": { "spf", "dmarc", "dmarc_policy", "dmarc_from_domain", "dkim_signatures": [...] },
        "content": { "download": { "url", "expires_at" } }
      },
      "delivery": { "endpoint_id" }
    }
    """
    email = payload.get("email", {})
    headers = email.get("headers", {})
    smtp = email.get("smtp", {})
    parsed = email.get("parsed", {})
    auth_data = email.get("auth", {})
    content = email.get("content", {})
    delivery = payload.get("delivery", {})

    # Build attachments list
    raw_attachments = parsed.get("attachments", [])
    attachments = [
        EmailAttachment(
            filename=att.get("filename") or f"attachment_{i}",
            content_type=att.get("content_type", "application/octet-stream"),
            size_bytes=att.get("size_bytes", 0),
            sha256=att.get("sha256", ""),
            download_url=None,  # Individual download via tar.gz extraction
        )
        for i, att in enumerate(raw_attachments)
    ]

    # Build auth
    dkim_sigs = auth_data.get("dkim_signatures", [])
    first_dkim = dkim_sigs[0] if dkim_sigs else {}
    auth = EmailAuth(
        spf=_map_auth_result(auth_data.get("spf")),
        dkim=_map_auth_result(first_dkim.get("result")),
        dkim_domain=first_dkim.get("domain"),
        dmarc=_map_auth_result(auth_data.get("dmarc")),
        dmarc_policy=auth_data.get("dmarc_policy"),
        dmarc_from_domain=auth_data.get("dmarc_from_domain"),
    )

    # Determine provider type from download URL origin
    download_info = content.get("download", {})
    raw_download_url = download_info.get("url")
    provider = EmailProvider.PRIMITIVE_SELF_HOST
    if raw_download_url and "primitive.dev" in raw_download_url:
        provider = EmailProvider.PRIMITIVE_MANAGED

    # Extract envelope
    envelope_from = smtp.get("mail_from", "")
    envelope_to = smtp.get("rcpt_to", [])
    if isinstance(envelope_to, str):
        envelope_to = [envelope_to]

    return InboundEmailEvent(
        # Identity
        message_id=headers.get("message_id") or email.get("id", ""),
        provider_event_id=payload.get("id", ""),
        provider=provider,
        # Envelope
        envelope_from=envelope_from,
        envelope_to=envelope_to,
        header_from=headers.get("from", ""),
        header_from_address=_extract_address(headers.get("from")),
        header_to=headers.get("to", ""),
        header_subject=headers.get("subject"),
        header_date=headers.get("date"),
        # Body
        body_text=parsed.get("body_text"),
        body_html=parsed.get("body_html"),
        # Attachments
        attachments=attachments,
        attachments_download_url=parsed.get("attachments_download_url"),
        # Auth
        auth=auth,
        # Provenance
        received_at=_parse_iso_datetime(email.get("received_at")),
        provider_endpoint_id=delivery.get("endpoint_id"),
        raw_download_url=raw_download_url,
    )
