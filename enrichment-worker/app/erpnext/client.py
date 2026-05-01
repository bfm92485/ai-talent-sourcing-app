"""ERPNext REST API client for Job Applicant CRUD and file upload."""

import logging
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


class ERPNextClient:
    """Client for interacting with ERPNext REST API."""

    def __init__(self, base_url: str, api_key: str, api_secret: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {api_key}:{api_secret}",
            "Content-Type": "application/json",
        })

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def get_job_applicant(self, email: str) -> Optional[dict]:
        """Check if a Job Applicant with this email already exists."""
        resp = self.session.get(
            self._url(f"/api/resource/Job Applicant/{email}"),
        )
        if resp.status_code == 200:
            return resp.json().get("data")
        return None

    def ensure_designation(self, designation: str) -> None:
        """Create Designation if it doesn't exist."""
        if not designation:
            return
        resp = self.session.get(
            self._url(f"/api/resource/Designation/{designation}"),
        )
        if resp.status_code == 404:
            self.session.post(
                self._url("/api/resource/Designation"),
                json={"designation_name": designation},
            )
            logger.info(f"Created Designation: {designation}")

    def create_job_applicant(self, data: dict[str, Any]) -> dict:
        """
        Create a new Job Applicant record.

        Args:
            data: Dictionary with Job Applicant fields.

        Returns:
            Created document data.

        Raises:
            requests.HTTPError on failure.
        """
        resp = self.session.post(
            self._url("/api/resource/Job Applicant"),
            json=data,
        )
        if resp.status_code >= 400:
            logger.error(f"Create failed ({resp.status_code}): {resp.text[:500]}")
        resp.raise_for_status()
        result = resp.json().get("data", {})
        logger.info(f"Created Job Applicant: {result.get('name')}")
        return result

    def update_job_applicant(self, name: str, data: dict[str, Any]) -> dict:
        """
        Update an existing Job Applicant record.

        Args:
            name: The document name (typically email address).
            data: Dictionary with fields to update.

        Returns:
            Updated document data.
        """
        resp = self.session.put(
            self._url(f"/api/resource/Job Applicant/{name}"),
            json=data,
        )
        if resp.status_code >= 400:
            logger.error(f"Update failed ({resp.status_code}): {resp.text[:500]}")
        resp.raise_for_status()
        result = resp.json().get("data", {})
        logger.info(f"Updated Job Applicant: {name}")
        return result

    def upload_file(
        self,
        file_content: bytes,
        filename: str,
        doctype: str = "Job Applicant",
        docname: str = "",
        is_private: bool = True,
    ) -> dict:
        """
        Upload a file and attach it to a document.

        Args:
            file_content: Raw file bytes.
            filename: Original filename.
            doctype: Target DocType.
            docname: Target document name.
            is_private: Whether file is private.

        Returns:
            File document data.
        """
        # Remove Content-Type for multipart
        headers = dict(self.session.headers)
        headers.pop("Content-Type", None)

        resp = requests.post(
            self._url("/api/method/upload_file"),
            headers=headers,
            files={"file": (filename, file_content)},
            data={
                "doctype": doctype,
                "docname": docname,
                "is_private": "1" if is_private else "0",
            },
        )
        resp.raise_for_status()
        result = resp.json().get("message", {})
        logger.info(f"Uploaded file '{filename}' → {result.get('file_url')}")
        return result

    def create_communication(
        self,
        sender: str,
        recipients: str,
        subject: str,
        content: str,
        reference_doctype: str = "Job Applicant",
        reference_name: str = "",
    ) -> dict:
        """
        Create a Communication record to preserve the email thread.

        Args:
            sender: From email address.
            recipients: To email address(es).
            subject: Email subject.
            content: Email body (HTML or text).
            reference_doctype: Linked DocType.
            reference_name: Linked document name.

        Returns:
            Created Communication data.
        """
        data = {
            "communication_type": "Communication",
            "communication_medium": "Email",
            "sender": sender,
            "recipients": recipients,
            "subject": subject or "(No Subject)",
            "content": content or "",
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "sent_or_received": "Received",
        }
        resp = self.session.post(
            self._url("/api/resource/Communication"),
            json=data,
        )
        resp.raise_for_status()
        result = resp.json().get("data", {})
        logger.info(f"Created Communication: {result.get('name')}")
        return result

    @staticmethod
    def _clean_child_table(rows: list[dict]) -> list[dict]:
        """Remove None values from child table rows (ERPNext rejects null for some fields)."""
        cleaned = []
        for row in rows:
            cleaned_row = {k: (v if v is not None else "") for k, v in row.items()}
            cleaned.append(cleaned_row)
        return cleaned

    def upsert_job_applicant(self, enriched_data: dict[str, Any], source: str = "Email Inbound") -> dict:
        """
        Create or update a Job Applicant with full enrichment data.

        This is the main entry point for the enrichment pipeline.

        Args:
            enriched_data: Output from baml_runner.extract_resume().
            source: The custom_source value.

        Returns:
            The created or updated Job Applicant data.
        """
        import json as _json

        email = enriched_data.get("email_id")
        if not email:
            raise ValueError("Cannot create Job Applicant without email_id")

        # Ensure designation exists
        designation = enriched_data.get("designation")
        if designation:
            self.ensure_designation(designation)

        # Build enrichment extras as proper JSON
        summary = enriched_data.get("summary") or ""
        extras = _json.dumps({"summary": summary}, ensure_ascii=False)

        # Build the payload
        payload = {
            "applicant_name": enriched_data.get("applicant_name", "Unknown"),
            "email_id": email,
            "designation": designation or "",
            "custom_source": source,
            "custom_current_company": enriched_data.get("current_company") or "",
            "custom_skills_list": enriched_data.get("skills") or "",
            "custom_linkedin_url": enriched_data.get("linkedin_url") or "",
            "custom_github_url": enriched_data.get("github_url") or "",
            "custom_enrichment_status": "Complete",
            "custom_data_ttl_expiry": enriched_data.get("data_ttl_expiry") or "",
            "custom_enrichment_extras": extras,
            "notes": summary[:140] if summary else "",
        }

        # Add phone if available
        if enriched_data.get("phone_number"):
            payload["phone_number"] = enriched_data["phone_number"]

        # Add child tables (clean None values)
        if enriched_data.get("experience"):
            payload["custom_experience"] = self._clean_child_table(enriched_data["experience"])
        if enriched_data.get("education"):
            payload["custom_education"] = self._clean_child_table(enriched_data["education"])

        # Check for existing record
        existing = self.get_job_applicant(email)
        if existing:
            return self.update_job_applicant(email, payload)
        else:
            return self.create_job_applicant(payload)
