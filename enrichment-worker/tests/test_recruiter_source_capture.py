"""
BDD tests: Capture recruiter/source from forwarded email body.

Feature: Extract recruiter identity from forwarded emails and store as structured source
  As the talent sourcing pipeline
  I want to capture who forwarded/sourced a candidate
  So that the ATS tracks the referral source for each Job Applicant

Design:
  - custom_source (Select field) = "Referral" for forwarded emails
  - custom_referred_by (Data field) = recruiter identity extracted from forward body
  - For direct applications: custom_source = "Resume Upload", custom_referred_by = ""

Scenarios:
  1. Forwarded email with "From: Recruiter Name <email>" →
     source="Referral", referred_by="Alexis Williams <alexis.williams@lhh.com>"
  2. Forwarded email with email-only From: →
     source="Referral", referred_by="recruiter@agency.com"
  3. Internal forward (Bryan forwards, no parseable From:) →
     source="Referral", referred_by="Bryan Murphy <bryan.murphy@amdg.ai>"
  4. Direct application (no forward) →
     source="Resume Upload", referred_by not set
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import (
    _extract_source_from_forwarded_email,
    _is_forwarded_email,
    _process_email,
)


class TestExtractSourceFromForwardedEmail:
    """
    Feature: Extract recruiter source from forwarded email
    """

    def test_given_forwarded_body_with_recruiter_from_line_then_returns_recruiter_identity(self):
        """
        Scenario: Gmail-style forward with recruiter From: line
          Given a forwarded email body containing:
            "---------- Forwarded message ----------
             From: Alexis Williams <alexis.williams@lhh.com>"
          When _extract_source_from_forwarded_email is called
          Then it returns "Alexis Williams <alexis.williams@lhh.com>"
        """
        body = (
            "FYI - candidates below.\n\n"
            "---------- Forwarded message ----------\n"
            "From: Alexis Williams <alexis.williams@lhh.com>\n"
            "Date: Mon, Apr 20, 2026\n"
            "Subject: AMDG Hiring: CFO\n\n"
            "Please find attached resumes.\n"
        )
        result = _extract_source_from_forwarded_email(
            subject="FW: AMDG Hiring: CFO",
            body_text=body,
            sender_name="Bryan Murphy",
            sender_email="bryan.murphy@amdg.ai",
        )
        assert result == "Alexis Williams <alexis.williams@lhh.com>"

    def test_given_forwarded_body_with_outlook_original_message_then_source_extracted(self):
        """
        Scenario: Outlook-style forward with -----Original Message-----
          Given a forwarded email body containing:
            "-----Original Message-----
             From: John Recruiter <john@staffing.com>"
          When _extract_source_from_forwarded_email is called
          Then it returns "John Recruiter <john@staffing.com>"
        """
        body = (
            "See below.\n\n"
            "-----Original Message-----\n"
            "From: John Recruiter <john@staffing.com>\n"
            "Sent: Tuesday, April 21, 2026\n"
            "To: Bryan Murphy\n"
            "Subject: Candidate for CFO role\n\n"
            "Hi Bryan, here is the candidate.\n"
        )
        result = _extract_source_from_forwarded_email(
            subject="FW: Candidate for CFO role",
            body_text=body,
            sender_name="Bryan Murphy",
            sender_email="bryan.murphy@amdg.ai",
        )
        assert result == "John Recruiter <john@staffing.com>"

    def test_given_forwarded_body_with_email_only_from_line_then_returns_email(self):
        """
        Scenario: Forward with email-only From: line (no display name)
          Given a forwarded email body containing:
            "---------- Forwarded message ----------
             From: recruiter@agency.com"
          When _extract_source_from_forwarded_email is called
          Then it returns the email address (possibly with derived name)
        """
        body = (
            "---------- Forwarded message ----------\n"
            "From: recruiter@agency.com\n"
            "Date: Mon, Apr 20, 2026\n"
            "Subject: Resume\n\n"
            "Attached.\n"
        )
        result = _extract_source_from_forwarded_email(
            subject="Fwd: Resume",
            body_text=body,
            sender_name="Bryan Murphy",
            sender_email="bryan.murphy@amdg.ai",
        )
        # The email-only From: line is parsed; result contains the email
        assert "recruiter@agency.com" in result

    def test_given_forwarded_email_with_no_parseable_from_line_then_fallback_to_sender(self):
        """
        Scenario: Forward where the original sender cannot be parsed from body
          Given a forwarded email with FW: subject but no From: line in body
          When _extract_source_from_forwarded_email is called
          Then it falls back to "Bryan Murphy <bryan.murphy@amdg.ai>"
        """
        body = (
            "Here are the resumes for your review.\n\n"
            "Best regards,\n"
            "Alexis\n"
        )
        result = _extract_source_from_forwarded_email(
            subject="FW: AMDG Hiring: CFO",
            body_text=body,
            sender_name="Bryan Murphy",
            sender_email="bryan.murphy@amdg.ai",
        )
        assert result == "Bryan Murphy <bryan.murphy@amdg.ai>"

    def test_given_direct_email_not_forwarded_then_returns_none(self):
        """
        Scenario: Direct application email (not forwarded)
          Given an email that is NOT forwarded
          When _extract_source_from_forwarded_email is called
          Then it returns None (caller should use default source)
        """
        body = (
            "Dear Hiring Manager,\n\n"
            "I am writing to apply for the CFO position.\n"
        )
        result = _extract_source_from_forwarded_email(
            subject="Application for CFO Position",
            body_text=body,
            sender_name="Jane Candidate",
            sender_email="jane@gmail.com",
        )
        assert result is None


class TestE2ERecruiterSourceCapturedInUpsert:
    """
    Feature: Recruiter source flows through to ERPNext upsert
      - custom_source = "Referral" for forwarded emails
      - custom_referred_by = recruiter identity string
    """

    @pytest.mark.asyncio
    async def test_given_forwarded_email_with_resumes_then_source_is_referral_and_referred_by_is_recruiter(self):
        """
        Scenario: Recruiter forwards resumes → source="Referral", referred_by=recruiter
          Given Bryan forwards an email originally from Alexis Williams at LHH
          And the email contains resume attachments
          When the pipeline processes each resume
          Then upsert_job_applicant is called with source="Referral"
          And the enriched_data includes referred_by="Alexis Williams <alexis.williams@lhh.com>"
        """
        mock_payload = MagicMock()
        mock_payload.subject = "FW: AMDG Hiring: CFO — Candidate Tracking"
        mock_payload.sender_email = "bryan.murphy@amdg.ai"
        mock_payload.sender_name = "Bryan Murphy"
        mock_payload.body_text = (
            "Hi team, see below.\n\n"
            "---------- Forwarded message ----------\n"
            "From: Alexis Williams <alexis.williams@lhh.com>\n"
            "Date: Mon, Apr 20, 2026\n"
            "Subject: AMDG Hiring: CFO — Candidate Tracking\n\n"
            "Please find attached the resumes.\n"
        )
        mock_payload.body_html = None
        mock_payload.message_id = "msg-source-001"
        mock_payload.to = ["bryan.murphy@amdg.ai"]
        mock_payload.attachments_download_url = "http://localhost:4001/download/test"
        mock_payload.attachments = [
            MagicMock(filename="Kylie_Opelt_Resume.pdf", tar_path="0/Kylie_Opelt_Resume.pdf"),
        ]

        mock_settings = MagicMock()
        mock_settings.max_attachment_size = 25000000

        mock_erpnext = MagicMock()
        mock_erpnext.upsert_job_applicant = MagicMock(return_value={"name": "HR-APP-00010"})
        mock_erpnext.upload_file = MagicMock()
        mock_erpnext.create_communication = MagicMock()

        with patch("app.main._download_all_attachments") as mock_download, \
             patch("app.main.extract_resume") as mock_extract, \
             patch("app.main.extract_text_from_pdf") as mock_pdf:

            mock_download.return_value = {
                "Kylie_Opelt_Resume.pdf": b"fake-pdf-bytes",
            }
            mock_extract.return_value = {
                "applicant_name": "Kylie Opelt",
                "email_id": "kyliejoyopelt@gmail.com",
                "designation": "CFO",
            }
            mock_pdf.return_value = "Kylie Opelt\nkyliejoyopelt@gmail.com\nCFO experience..."

            await _process_email(
                payload=mock_payload,
                settings=mock_settings,
                erpnext=mock_erpnext,
                event_id="test-source-001",
            )

        # Verify: upsert was called with source="Referral"
        mock_erpnext.upsert_job_applicant.assert_called_once()
        call_args = mock_erpnext.upsert_job_applicant.call_args
        assert call_args[1]["source"] == "Referral"

        # Verify: enriched_data passed to upsert includes referred_by
        enriched_data = call_args[0][0]
        assert enriched_data["referred_by"] == "Alexis Williams <alexis.williams@lhh.com>"

    @pytest.mark.asyncio
    async def test_given_direct_email_with_resume_then_source_is_resume_upload_no_referred_by(self):
        """
        Scenario: Direct application → source="Resume Upload", no referred_by
          Given a candidate emails directly with a resume attached
          When the pipeline processes the resume
          Then upsert_job_applicant is called with source="Resume Upload"
          And enriched_data does NOT contain referred_by
        """
        mock_payload = MagicMock()
        mock_payload.subject = "Application for CFO"
        mock_payload.sender_email = "candidate@gmail.com"
        mock_payload.sender_name = "Jane Candidate"
        mock_payload.body_text = "Please find my resume attached."
        mock_payload.body_html = None
        mock_payload.message_id = "msg-direct-source-001"
        mock_payload.to = ["careers@amdg.ai"]
        mock_payload.attachments_download_url = "http://localhost:4001/download/test2"
        mock_payload.attachments = [
            MagicMock(filename="Jane_Resume.pdf", tar_path="0/Jane_Resume.pdf"),
        ]

        mock_settings = MagicMock()
        mock_settings.max_attachment_size = 25000000

        mock_erpnext = MagicMock()
        mock_erpnext.upsert_job_applicant = MagicMock(return_value={"name": "HR-APP-00011"})
        mock_erpnext.upload_file = MagicMock()
        mock_erpnext.create_communication = MagicMock()

        with patch("app.main._download_all_attachments") as mock_download, \
             patch("app.main.extract_resume") as mock_extract, \
             patch("app.main.extract_text_from_pdf") as mock_pdf:

            mock_download.return_value = {
                "Jane_Resume.pdf": b"fake-pdf-bytes",
            }
            mock_extract.return_value = {
                "applicant_name": "Jane Candidate",
                "email_id": "candidate@gmail.com",
                "designation": "CFO",
            }
            mock_pdf.return_value = "Jane Candidate\ncandidate@gmail.com\nCFO with 15 years of experience in financial leadership and strategic planning across multiple industries"

            await _process_email(
                payload=mock_payload,
                settings=mock_settings,
                erpnext=mock_erpnext,
                event_id="test-source-002",
            )

        # Verify: source is the default "Resume Upload" for direct applications
        mock_erpnext.upsert_job_applicant.assert_called_once()
        call_args = mock_erpnext.upsert_job_applicant.call_args
        assert call_args[1]["source"] == "Resume Upload"

        # Verify: no referred_by in enriched_data
        enriched_data = call_args[0][0]
        assert "referred_by" not in enriched_data

    @pytest.mark.asyncio
    async def test_given_forward_without_parseable_from_then_referred_by_is_sender(self):
        """
        Scenario: Forward where original recruiter can't be parsed
          Given Bryan forwards an email but the body has no From: line
          When the pipeline processes the resume
          Then source="Referral", referred_by="Bryan Murphy <bryan.murphy@amdg.ai>"
        """
        mock_payload = MagicMock()
        mock_payload.subject = "Fwd: Great candidate"
        mock_payload.sender_email = "bryan.murphy@amdg.ai"
        mock_payload.sender_name = "Bryan Murphy"
        mock_payload.body_text = "Check out this candidate, looks great for us."
        mock_payload.body_html = None
        mock_payload.message_id = "msg-source-003"
        mock_payload.to = ["careers@amdg.ai"]
        mock_payload.attachments_download_url = "http://localhost:4001/download/test3"
        mock_payload.attachments = [
            MagicMock(filename="Candidate_Resume.pdf", tar_path="0/Candidate_Resume.pdf"),
        ]

        mock_settings = MagicMock()
        mock_settings.max_attachment_size = 25000000

        mock_erpnext = MagicMock()
        mock_erpnext.upsert_job_applicant = MagicMock(return_value={"name": "HR-APP-00012"})
        mock_erpnext.upload_file = MagicMock()
        mock_erpnext.create_communication = MagicMock()

        with patch("app.main._download_all_attachments") as mock_download, \
             patch("app.main.extract_resume") as mock_extract, \
             patch("app.main.extract_text_from_pdf") as mock_pdf:

            mock_download.return_value = {
                "Candidate_Resume.pdf": b"fake-pdf-bytes",
            }
            mock_extract.return_value = {
                "applicant_name": "Some Candidate",
                "email_id": "some.candidate@gmail.com",
                "designation": "VP Finance",
            }
            mock_pdf.return_value = "Some Candidate\nsome.candidate@gmail.com\nVP Finance..."

            await _process_email(
                payload=mock_payload,
                settings=mock_settings,
                erpnext=mock_erpnext,
                event_id="test-source-003",
            )

        # Verify: source is "Referral" and referred_by falls back to sender
        mock_erpnext.upsert_job_applicant.assert_called_once()
        call_args = mock_erpnext.upsert_job_applicant.call_args
        assert call_args[1]["source"] == "Referral"

        enriched_data = call_args[0][0]
        assert enriched_data["referred_by"] == "Bryan Murphy <bryan.murphy@amdg.ai>"
