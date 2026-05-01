"""
Job Applicant after_insert hook — triggers BAML AI enrichment.

When ERPNext's Email Account creates a Job Applicant from an inbound email,
this hook fires and enqueues a background job to:
1. Extract text from attached resume (PDF/DOCX)
2. Run BAML ExtractResume via the enrichment worker API
3. Update the Job Applicant with enriched data

This is Approach A from the architecture plan: ERPNext-native email pull
with server-side enrichment triggered by doc_events.

Usage in hooks.py:
    doc_events = {
        "Job Applicant": {
            "after_insert": "ai_talent_sourcing.enrichment_handler.after_insert"
        }
    }
"""

import frappe
from frappe import _
from frappe.utils.background_jobs import enqueue


def after_insert(doc, method=None):
    """
    Hook called after a new Job Applicant is created.

    Enqueues a background job to run AI enrichment so we don't block
    the email pull scheduler or the UI.
    """
    # Only enrich if not already enriched (idempotency)
    if doc.get("custom_enrichment_status") in ("Complete", "Processing"):
        frappe.logger().info(
            f"Job Applicant {doc.name} already enriched — skipping"
        )
        return

    # Mark as processing to prevent duplicate enrichment
    frappe.db.set_value(
        "Job Applicant", doc.name, "custom_enrichment_status", "Processing"
    )
    frappe.db.commit()

    # Enqueue background job (long queue for AI processing ~30s)
    enqueue(
        "ai_talent_sourcing.enrichment_handler.run_enrichment",
        queue="long",
        timeout=120,
        job_id=f"enrich_{doc.name}",
        doc_name=doc.name,
    )

    frappe.logger().info(
        f"Enqueued enrichment job for Job Applicant {doc.name}"
    )


def run_enrichment(doc_name: str):
    """
    Background job: extract resume text and call the enrichment worker API.

    This function runs in a Frappe background worker (RQ job).
    """
    import json
    import requests
    from frappe.utils import get_files_path

    doc = frappe.get_doc("Job Applicant", doc_name)

    # Step 1: Find attached resume file
    resume_text = _extract_resume_text(doc)

    if not resume_text:
        # Fallback: use the notes field or any available text
        resume_text = doc.get("notes") or ""
        if not resume_text:
            frappe.logger().warning(
                f"No resume text found for {doc_name} — marking as failed"
            )
            frappe.db.set_value(
                "Job Applicant", doc_name, "custom_enrichment_status", "Failed"
            )
            frappe.db.commit()
            return

    # Step 2: Call the enrichment worker API
    enrichment_url = frappe.conf.get(
        "enrichment_worker_url",
        "http://localhost:8090/webhook/test"
    )

    try:
        resp = requests.post(
            enrichment_url,
            json={
                "resume_text": resume_text,
                "source": doc.get("custom_source") or "Email Inbound",
                "email_override": doc.email_id,
                "doc_name": doc_name,
            },
            timeout=90,
        )
        resp.raise_for_status()
        result = resp.json()
    except Exception as e:
        frappe.logger().error(
            f"Enrichment worker call failed for {doc_name}: {e}"
        )
        frappe.db.set_value(
            "Job Applicant", doc_name, "custom_enrichment_status", "Failed"
        )
        frappe.db.commit()
        return

    # Step 3: Update the Job Applicant with enriched data
    enriched = result.get("enriched_data", {})
    if enriched:
        _apply_enrichment(doc_name, enriched)

    frappe.logger().info(
        f"Enrichment complete for {doc_name}: "
        f"{len(enriched.get('skills', '').split(','))} skills, "
        f"{len(enriched.get('experience', []))} experience entries"
    )


def _extract_resume_text(doc) -> str:
    """
    Extract text from the first PDF/DOCX attachment on the Job Applicant.

    Uses pdfplumber for PDF and python-docx for DOCX.
    Falls back to the Communication content if no attachment found.
    """
    import io

    # Check for file attachments
    attachments = frappe.get_all(
        "File",
        filters={
            "attached_to_doctype": "Job Applicant",
            "attached_to_name": doc.name,
        },
        fields=["name", "file_name", "file_url", "is_private"],
    )

    for att in attachments:
        filename = att.file_name or ""
        if filename.lower().endswith(".pdf"):
            file_content = _read_file(att)
            if file_content:
                try:
                    import pdfplumber
                    pdf = pdfplumber.open(io.BytesIO(file_content))
                    pages = []
                    for page in pdf.pages:
                        text = page.extract_text()
                        if text and text.strip():
                            pages.append(text)
                    pdf.close()
                    if pages:
                        return "\n\n".join(pages)
                except Exception as e:
                    frappe.logger().error(f"PDF extraction failed: {e}")

        elif filename.lower().endswith((".docx", ".doc")):
            file_content = _read_file(att)
            if file_content:
                try:
                    from docx import Document
                    doc_file = Document(io.BytesIO(file_content))
                    paragraphs = [p.text for p in doc_file.paragraphs if p.text.strip()]
                    if paragraphs:
                        return "\n".join(paragraphs)
                except Exception as e:
                    frappe.logger().error(f"DOCX extraction failed: {e}")

    # Fallback: check Communications for email body text
    comms = frappe.get_all(
        "Communication",
        filters={
            "reference_doctype": "Job Applicant",
            "reference_name": doc.name,
            "communication_type": "Communication",
        },
        fields=["content"],
        order_by="creation desc",
        limit=1,
    )
    if comms and comms[0].content:
        # Strip HTML tags for plain text
        from frappe.utils import strip_html
        return strip_html(comms[0].content)

    return ""


def _read_file(file_doc) -> bytes:
    """Read file content from Frappe's file system."""
    try:
        file_path = frappe.get_site_path(
            "private" if file_doc.is_private else "public",
            "files",
            file_doc.file_name,
        )
        with open(file_path, "rb") as f:
            return f.read()
    except Exception as e:
        frappe.logger().error(f"Failed to read file {file_doc.file_name}: {e}")
        return b""


def _apply_enrichment(doc_name: str, enriched: dict):
    """Apply enriched data to the Job Applicant document."""
    import json
    from datetime import date, timedelta

    update_fields = {}

    if enriched.get("applicant_name"):
        update_fields["applicant_name"] = enriched["applicant_name"]
    if enriched.get("designation"):
        # Ensure designation exists
        if not frappe.db.exists("Designation", enriched["designation"]):
            frappe.get_doc({
                "doctype": "Designation",
                "designation_name": enriched["designation"],
            }).insert(ignore_permissions=True)
        update_fields["designation"] = enriched["designation"]
    if enriched.get("current_company"):
        update_fields["custom_current_company"] = enriched["current_company"]
    if enriched.get("skills"):
        update_fields["custom_skills_list"] = enriched["skills"]
    if enriched.get("linkedin_url"):
        update_fields["custom_linkedin_url"] = enriched["linkedin_url"]
    if enriched.get("github_url"):
        update_fields["custom_github_url"] = enriched["github_url"]
    if enriched.get("summary"):
        update_fields["custom_enrichment_extras"] = json.dumps(
            {"summary": enriched["summary"]}, ensure_ascii=False
        )
        update_fields["notes"] = enriched["summary"][:140]

    update_fields["custom_enrichment_status"] = "Complete"
    update_fields["custom_data_ttl_expiry"] = (
        date.today() + timedelta(days=90)
    ).isoformat()

    # Update scalar fields
    for field, value in update_fields.items():
        frappe.db.set_value("Job Applicant", doc_name, field, value)

    # Update child tables (experience, education)
    doc = frappe.get_doc("Job Applicant", doc_name)

    if enriched.get("experience"):
        doc.set("custom_experience", [])
        for exp in enriched["experience"]:
            doc.append("custom_experience", {
                "company": exp.get("company", ""),
                "title": exp.get("title", ""),
                "start_date": exp.get("start_date") or None,
                "end_date": exp.get("end_date") or None,
                "responsibilities": exp.get("responsibilities", ""),
            })

    if enriched.get("education"):
        doc.set("custom_education", [])
        for edu in enriched["education"]:
            doc.append("custom_education", {
                "institution": edu.get("institution", ""),
                "degree": edu.get("degree", ""),
                "field_of_study": edu.get("field_of_study", ""),
            })

    doc.flags.ignore_permissions = True
    doc.save()
    frappe.db.commit()
