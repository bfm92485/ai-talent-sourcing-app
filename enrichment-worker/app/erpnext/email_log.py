"""
ATS Inbound Email Log — ERPNext DocType for observability of the email
ingestion pipeline. Provides a complete audit trail of every inbound email
event, its processing status, and outcome.

This module:
1. Creates the DocType via REST API if it doesn't exist
2. Provides CRUD operations for log entries
3. Maps from InboundEmailEvent to the DocType fields

Design rationale:
- Separate from Job Applicant to avoid polluting the HR workflow
- Stores idempotency_key for dedup verification
- Tracks full lifecycle: received → downloading → scanning → extracting → enriching → pushing → complete/failed
- Includes auth results for security auditing
- Links to the resulting Job Applicant document (if created)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from ..models.internal_event import InboundEmailEvent, ProcessingStatus

logger = logging.getLogger(__name__)

# DocType definition for ATS Inbound Email Log
DOCTYPE_DEFINITION = {
    "doctype": "DocType",
    "name": "ATS Inbound Email Log",
    "module": "AI Talent Sourcing",
    "naming_rule": "Expression (old style)",
    "autoname": "ATS-EMAIL-.#####",
    "is_submittable": 0,
    "istable": 0,
    "track_changes": 1,
    "fields": [
        # Identity Section
        {"fieldname": "section_identity", "fieldtype": "Section Break", "label": "Identity"},
        {"fieldname": "message_id", "fieldtype": "Data", "label": "Message-ID", "in_list_view": 1, "read_only": 1},
        {"fieldname": "idempotency_key", "fieldtype": "Data", "label": "Idempotency Key", "unique": 1, "read_only": 1},
        {"fieldname": "provider_event_id", "fieldtype": "Data", "label": "Provider Event ID", "read_only": 1},
        {"fieldname": "column_break_1", "fieldtype": "Column Break"},
        {"fieldname": "provider", "fieldtype": "Select", "label": "Provider",
         "options": "primitive_self_host\nprimitive_managed\ngmail_imap\nerpnext_email_account\nmanual_upload",
         "in_list_view": 1, "read_only": 1},
        {"fieldname": "received_at", "fieldtype": "Datetime", "label": "Received At", "in_list_view": 1, "read_only": 1},

        # Envelope Section
        {"fieldname": "section_envelope", "fieldtype": "Section Break", "label": "Envelope"},
        {"fieldname": "envelope_from", "fieldtype": "Data", "label": "Envelope From", "read_only": 1},
        {"fieldname": "header_from_address", "fieldtype": "Data", "label": "From Address", "in_list_view": 1, "read_only": 1},
        {"fieldname": "header_subject", "fieldtype": "Data", "label": "Subject", "in_list_view": 1, "read_only": 1},
        {"fieldname": "column_break_2", "fieldtype": "Column Break"},
        {"fieldname": "envelope_to", "fieldtype": "Data", "label": "Envelope To", "read_only": 1},
        {"fieldname": "header_to", "fieldtype": "Data", "label": "Header To", "read_only": 1},

        # Attachments Section
        {"fieldname": "section_attachments", "fieldtype": "Section Break", "label": "Attachments"},
        {"fieldname": "attachment_count", "fieldtype": "Int", "label": "Attachment Count", "read_only": 1},
        {"fieldname": "has_resume", "fieldtype": "Check", "label": "Has Resume", "read_only": 1},
        {"fieldname": "column_break_3", "fieldtype": "Column Break"},
        {"fieldname": "attachment_filenames", "fieldtype": "Small Text", "label": "Attachment Filenames", "read_only": 1},

        # Authentication Section
        {"fieldname": "section_auth", "fieldtype": "Section Break", "label": "Email Authentication", "collapsible": 1},
        {"fieldname": "auth_spf", "fieldtype": "Select", "label": "SPF",
         "options": "pass\nfail\nsoftfail\nnone\nunknown", "read_only": 1},
        {"fieldname": "auth_dkim", "fieldtype": "Select", "label": "DKIM",
         "options": "pass\nfail\nsoftfail\nnone\nunknown", "read_only": 1},
        {"fieldname": "column_break_4", "fieldtype": "Column Break"},
        {"fieldname": "auth_dmarc", "fieldtype": "Select", "label": "DMARC",
         "options": "pass\nfail\nsoftfail\nnone\nunknown", "read_only": 1},
        {"fieldname": "auth_dmarc_policy", "fieldtype": "Data", "label": "DMARC Policy", "read_only": 1},

        # Processing Section
        {"fieldname": "section_processing", "fieldtype": "Section Break", "label": "Processing"},
        {"fieldname": "status", "fieldtype": "Select", "label": "Status",
         "options": "received\ndownloading\nscanning\nextracting\nenriching\npushing\ncomplete\nfailed\nquarantined",
         "in_list_view": 1, "default": "received"},
        {"fieldname": "status_detail", "fieldtype": "Small Text", "label": "Status Detail"},
        {"fieldname": "column_break_5", "fieldtype": "Column Break"},
        {"fieldname": "retry_count", "fieldtype": "Int", "label": "Retry Count", "default": "0"},
        {"fieldname": "processing_started_at", "fieldtype": "Datetime", "label": "Processing Started"},
        {"fieldname": "processing_completed_at", "fieldtype": "Datetime", "label": "Processing Completed"},

        # Result Section
        {"fieldname": "section_result", "fieldtype": "Section Break", "label": "Result"},
        {"fieldname": "erpnext_document_name", "fieldtype": "Data", "label": "Job Applicant Name"},
        {"fieldname": "erpnext_document_link", "fieldtype": "Link", "label": "Job Applicant",
         "options": "Job Applicant"},
        {"fieldname": "column_break_6", "fieldtype": "Column Break"},
        {"fieldname": "scan_result", "fieldtype": "Data", "label": "ClamAV Scan Result"},
        {"fieldname": "extracted_text_length", "fieldtype": "Int", "label": "Extracted Text Length"},

        # Raw Data Section (collapsible)
        {"fieldname": "section_raw", "fieldtype": "Section Break", "label": "Raw Data", "collapsible": 1},
        {"fieldname": "body_text_preview", "fieldtype": "Text", "label": "Body Text (Preview)"},
        {"fieldname": "raw_event_json", "fieldtype": "Code", "label": "Raw Event JSON",
         "options": "JSON"},
    ],
    "permissions": [
        {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
        {"role": "HR Manager", "read": 1, "write": 0, "create": 0, "delete": 0},
    ],
}


class EmailLogClient:
    """Client for creating and updating ATS Inbound Email Log entries in ERPNext."""

    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"token {api_key}:{api_secret}",
            "Content-Type": "application/json",
        }
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=30.0,
        )

    async def ensure_doctype_exists(self) -> bool:
        """Create the ATS Inbound Email Log DocType if it doesn't exist."""
        try:
            resp = await self.client.get(
                "/api/resource/DocType/ATS Inbound Email Log"
            )
            if resp.status_code == 200:
                logger.info("ATS Inbound Email Log DocType already exists")
                return True
        except Exception:
            pass

        # Create the DocType
        try:
            resp = await self.client.post(
                "/api/resource/DocType",
                json={"data": json.dumps(DOCTYPE_DEFINITION)},
            )
            if resp.status_code in (200, 201):
                logger.info("Created ATS Inbound Email Log DocType")
                return True
            else:
                logger.error(f"Failed to create DocType: {resp.status_code} {resp.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"Exception creating DocType: {e}")
            return False

    async def check_idempotency(self, idempotency_key: str) -> Optional[str]:
        """
        Check if an event with this idempotency_key has already been processed.
        Returns the log entry name if found, None otherwise.
        """
        try:
            resp = await self.client.get(
                "/api/resource/ATS Inbound Email Log",
                params={
                    "filters": json.dumps([["idempotency_key", "=", idempotency_key]]),
                    "fields": json.dumps(["name", "status"]),
                    "limit_page_length": 1,
                },
            )
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if data:
                    return data[0]["name"]
        except Exception as e:
            logger.warning(f"Idempotency check failed: {e}")
        return None

    async def create_log(self, event: InboundEmailEvent) -> Optional[str]:
        """Create a new ATS Inbound Email Log entry. Returns the document name."""
        log_data = {
            "doctype": "ATS Inbound Email Log",
            "message_id": event.message_id[:140],
            "idempotency_key": event.idempotency_key,
            "provider_event_id": event.provider_event_id[:140],
            "provider": event.provider.value,
            "received_at": event.received_at.isoformat(),
            "envelope_from": event.envelope_from[:140],
            "header_from_address": event.header_from_address[:140],
            "header_subject": (event.header_subject or "")[:140],
            "envelope_to": ", ".join(event.envelope_to)[:140],
            "header_to": event.header_to[:140],
            "attachment_count": len(event.attachments),
            "has_resume": 1 if event.has_resume_attachment else 0,
            "attachment_filenames": "\n".join(a.filename for a in event.attachments),
            "auth_spf": event.auth.spf.value,
            "auth_dkim": event.auth.dkim.value,
            "auth_dmarc": event.auth.dmarc.value,
            "auth_dmarc_policy": event.auth.dmarc_policy or "",
            "status": event.status.value,
            "retry_count": event.retry_count,
            "body_text_preview": (event.body_text or "")[:2000],
            "raw_event_json": json.dumps(event.to_log_dict(), indent=2),
        }

        try:
            resp = await self.client.post(
                "/api/resource/ATS Inbound Email Log",
                json={"data": json.dumps(log_data)},
            )
            if resp.status_code in (200, 201):
                name = resp.json().get("data", {}).get("name")
                logger.info(f"Created email log: {name}")
                return name
            else:
                logger.error(f"Failed to create email log: {resp.status_code} {resp.text[:200]}")
                return None
        except Exception as e:
            logger.error(f"Exception creating email log: {e}")
            return None

    async def update_status(
        self,
        log_name: str,
        status: ProcessingStatus,
        detail: Optional[str] = None,
        extra_fields: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Update the processing status of an email log entry."""
        update_data: dict[str, Any] = {"status": status.value}
        if detail:
            update_data["status_detail"] = detail[:2000]
        if extra_fields:
            update_data.update(extra_fields)

        # Set processing timestamps
        if status == ProcessingStatus.DOWNLOADING:
            from datetime import datetime, timezone
            update_data["processing_started_at"] = datetime.now(timezone.utc).isoformat()
        elif status in (ProcessingStatus.COMPLETE, ProcessingStatus.FAILED, ProcessingStatus.QUARANTINED):
            from datetime import datetime, timezone
            update_data["processing_completed_at"] = datetime.now(timezone.utc).isoformat()

        try:
            resp = await self.client.put(
                f"/api/resource/ATS Inbound Email Log/{log_name}",
                json=update_data,
            )
            if resp.status_code == 200:
                logger.debug(f"Updated email log {log_name} → {status.value}")
                return True
            else:
                logger.error(f"Failed to update email log: {resp.status_code}")
                return False
        except Exception as e:
            logger.error(f"Exception updating email log: {e}")
            return False

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
