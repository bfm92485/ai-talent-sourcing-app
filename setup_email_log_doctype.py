"""
Setup script: Create the ATS Inbound Email Log DocType on the live ERPNext instance.

Usage:
    source .env.erpnext_v16 && python3 setup_email_log_doctype.py

This is idempotent — safe to run multiple times.
"""

import json
import os
import sys

import requests

BASE_URL = os.environ.get("ERPNEXT_URL", "https://erpnext-v16-talent-sourcing-production.up.railway.app")
API_KEY = os.environ.get("ERPNEXT_API_KEY", "")
API_SECRET = os.environ.get("ERPNEXT_API_SECRET", "")

HEADERS = {
    "Authorization": f"token {API_KEY}:{API_SECRET}",
    "Content-Type": "application/json",
}

DOCTYPE_NAME = "ATS Inbound Email Log"

FIELDS = [
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
]

PERMISSIONS = [
    {"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
    {"role": "HR Manager", "read": 1, "write": 0, "create": 0, "delete": 0},
]


def check_exists() -> bool:
    """Check if the DocType already exists."""
    resp = requests.get(
        f"{BASE_URL}/api/resource/DocType/{DOCTYPE_NAME}",
        headers=HEADERS,
    )
    return resp.status_code == 200


def create_doctype():
    """Create the ATS Inbound Email Log DocType."""
    payload = {
        "doctype": "DocType",
        "name": DOCTYPE_NAME,
        "module": "HR",
        "naming_rule": "Expression (old style)",
        "autoname": "ATS-EMAIL-.#####",
        "is_submittable": 0,
        "istable": 0,
        "track_changes": 1,
        "custom": 1,
        "fields": FIELDS,
        "permissions": PERMISSIONS,
    }

    resp = requests.post(
        f"{BASE_URL}/api/resource/DocType",
        headers=HEADERS,
        json=payload,
    )

    if resp.status_code in (200, 201):
        print(f"✓ Created DocType: {DOCTYPE_NAME}")
        return True
    else:
        print(f"✗ Failed to create DocType: {resp.status_code}")
        print(f"  Response: {resp.text[:500]}")
        return False


def main():
    if not API_KEY or not API_SECRET:
        print("ERROR: ERPNEXT_API_KEY and ERPNEXT_API_SECRET must be set")
        sys.exit(1)

    print(f"Target: {BASE_URL}")
    print(f"DocType: {DOCTYPE_NAME}")
    print()

    if check_exists():
        print(f"✓ DocType '{DOCTYPE_NAME}' already exists (idempotent)")
        return

    print(f"Creating DocType '{DOCTYPE_NAME}'...")
    if create_doctype():
        print("\nDone. DocType is ready for use.")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
