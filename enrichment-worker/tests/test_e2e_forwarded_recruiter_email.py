"""
End-to-end integration test: Recruiter forwards candidate resumes.

Scenario (real-world from production):
  Bryan Murphy (bryan.murphy@amdg.ai) forwards an email from recruiter
  Alexis Williams (LHH) containing resumes for Kylie Opelt and Vivake Persaud.
  The email subject is "FW: AMDG Hiring: CFO — Candidate Tracking".

Expected behavior:
  - The pipeline should NOT create a Job Applicant for Bryan Murphy
  - When resume attachments are present, each resume creates its own Job Applicant
  - When no resume attachments are present (forward without attachments),
    the pipeline should skip the body-text fallback entirely
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import _process_email, _is_forwarded_email


class TestE2ERecruiterForwardWithAttachments:
    """
    Feature: Recruiter forwards email with resume attachments
    As the talent sourcing pipeline
    I want to create Job Applicants from the attached resumes
    And NOT create a Job Applicant for the recruiter/forwarder

    Background:
      Given Bryan Murphy (bryan.murphy@amdg.ai) forwards an email
      And the subject is "FW: AMDG Hiring: CFO — Candidate Tracking"
      And the email contains resume PDFs for Kylie Opelt and Vivake Persaud
    """

    @pytest.mark.asyncio
    async def test_given_forwarded_email_with_resumes_then_no_sender_record(self):
        """
        Scenario: Forwarded email with resume attachments
          Given a forwarded email from Bryan with 2 resume PDFs attached
          When the pipeline processes the email
          Then Job Applicants are created for each resume candidate
          And NO Job Applicant is created for Bryan Murphy
        """
        # Build a mock payload simulating Bryan's forwarded email
        mock_payload = MagicMock()
        mock_payload.subject = "FW: AMDG Hiring: CFO — Candidate Tracking"
        mock_payload.sender_email = "bryan.murphy@amdg.ai"
        mock_payload.sender_name = "Bryan Murphy"
        mock_payload.body_text = (
            "Hi team,\n\n"
            "We've spoken with several candidates. In the meantime, we wanted to share\n"
            "the resumes and cover letters for Kylie and Vivake.\n\n"
            "---------- Forwarded message ----------\n"
            "From: Alexis Williams <alexis.williams@lhh.com>\n"
            "Date: Mon, Apr 20, 2026\n"
            "Subject: AMDG Hiring: CFO — Candidate Tracking\n\n"
            "Please find attached the resumes for your review.\n"
        )
        mock_payload.body_html = None
        mock_payload.message_id = "msg-fw-001"
        mock_payload.to = ["bryan.murphy@amdg.ai"]
        mock_payload.attachments_download_url = "http://localhost:4001/download/test-archive"
        mock_payload.attachments = [
            MagicMock(filename="Kylie_Opelt_Resume_AMDG.pdf", tar_path="0/Kylie_Opelt_Resume_AMDG.pdf"),
            MagicMock(filename="Vivake Persaud Resume (2026).pdf", tar_path="1/Vivake Persaud Resume (2026).pdf"),
        ]

        mock_settings = MagicMock()
        mock_settings.max_attachment_size = 25000000

        mock_erpnext = MagicMock()
        mock_erpnext.upsert_job_applicant = MagicMock(
            side_effect=[
                {"name": "HR-APP-00001"},  # Kylie
                {"name": "HR-APP-00002"},  # Vivake
            ]
        )
        mock_erpnext.upload_file = MagicMock()
        mock_erpnext.create_communication = MagicMock()

        # Mock the attachment download to return fake resume data
        fake_resume_text = (
            "Kylie Opelt\nkyliejoyopelt@gmail.com\n\n"
            "CFO, COO & Executive Strategist\n"
            "15+ years driving growth across Fortune 50 CPG\n"
        )

        with patch("app.main._download_all_attachments") as mock_download, \
             patch("app.main.extract_resume") as mock_extract:

            # Simulate downloading 2 resume files
            mock_download.return_value = {
                "Kylie_Opelt_Resume_AMDG.pdf": b"fake-pdf-bytes-kylie",
                "Vivake Persaud Resume (2026).pdf": b"fake-pdf-bytes-vivake",
            }

            # Simulate BAML extraction results
            mock_extract.side_effect = [
                {
                    "applicant_name": "Kylie Opelt",
                    "email_id": "kyliejoyopelt@gmail.com",
                    "designation": "CFO, COO & Executive Strategist",
                    "summary": "15+ years driving growth",
                },
                {
                    "applicant_name": "Vivake Persaud",
                    "email_id": "VAKE.PERSAUD@GMAIL.COM",
                    "designation": "Finance Transformation Expert",
                    "summary": "M&A and FP&A specialist",
                },
            ]

            # Mock PDF text extraction
            with patch("app.main.extract_text_from_pdf") as mock_pdf_extract:
                mock_pdf_extract.return_value = fake_resume_text

                await _process_email(
                    payload=mock_payload,
                    settings=mock_settings,
                    erpnext=mock_erpnext,
                    event_id="test-e2e-001",
                )

        # Verify: upsert was called twice (once per resume)
        assert mock_erpnext.upsert_job_applicant.call_count == 2

        # Verify: first call was for Kylie (from resume extraction)
        first_call_data = mock_erpnext.upsert_job_applicant.call_args_list[0]
        assert first_call_data[0][0]["applicant_name"] == "Kylie Opelt"
        assert first_call_data[0][0]["email_id"] == "kyliejoyopelt@gmail.com"

        # Verify: second call was for Vivake (from resume extraction)
        second_call_data = mock_erpnext.upsert_job_applicant.call_args_list[1]
        assert second_call_data[0][0]["applicant_name"] == "Vivake Persaud"
        assert second_call_data[0][0]["email_id"] == "VAKE.PERSAUD@GMAIL.COM"

        # Verify: NO call was made with Bryan's email
        for call in mock_erpnext.upsert_job_applicant.call_args_list:
            assert call[0][0]["email_id"] != "bryan.murphy@amdg.ai"


class TestE2ERecruiterForwardWithoutAttachments:
    """
    Feature: Recruiter forwards email WITHOUT resume attachments
    As the talent sourcing pipeline
    I want to skip creating any Job Applicant record
    Because the forwarder is not a candidate and there are no resumes to process

    Background:
      Given Bryan Murphy forwards an email with subject "FW: ..."
      And the email has no PDF/DOCX attachments
    """

    @pytest.mark.asyncio
    async def test_given_forwarded_email_without_attachments_then_no_record_created(self):
        """
        Scenario: Forwarded email with no attachments
          Given a forwarded email from Bryan with no resume attachments
          When the pipeline processes the email
          Then NO Job Applicant is created at all
          And the pipeline logs a skip message
        """
        mock_payload = MagicMock()
        mock_payload.subject = "FW: AMDG Hiring: CFO — Candidate Tracking"
        mock_payload.sender_email = "bryan.murphy@amdg.ai"
        mock_payload.sender_name = "Bryan Murphy"
        mock_payload.body_text = (
            "FYI - see the candidates below.\n\n"
            "---------- Forwarded message ----------\n"
            "From: Alexis Williams <alexis.williams@lhh.com>\n"
            "Subject: Candidate resumes\n\n"
            "Attached are the resumes for Kylie and Vivake.\n"
        )
        mock_payload.body_html = None
        mock_payload.message_id = "msg-fw-002"
        mock_payload.to = ["bryan.murphy@amdg.ai"]
        mock_payload.attachments_download_url = None  # No attachments
        mock_payload.attachments = []

        mock_settings = MagicMock()
        mock_settings.max_attachment_size = 25000000

        mock_erpnext = MagicMock()
        mock_erpnext.upsert_job_applicant = MagicMock()

        await _process_email(
            payload=mock_payload,
            settings=mock_settings,
            erpnext=mock_erpnext,
            event_id="test-e2e-002",
        )

        # Key assertion: NO Job Applicant was created
        mock_erpnext.upsert_job_applicant.assert_not_called()


class TestE2EDirectApplicationEmail:
    """
    Feature: Direct application email (not forwarded) still works
    As the talent sourcing pipeline
    I want direct application emails to still create Job Applicant records
    So that candidates who email directly are not affected by the forward detection

    Background:
      Given a candidate sends an email directly (no FW: prefix)
      And the email has no resume attachments (just body text)
    """

    @pytest.mark.asyncio
    async def test_given_direct_email_without_attachments_then_record_created(self):
        """
        Scenario: Direct application via email body
          Given a candidate emails directly with subject "Application for CFO"
          And the email body contains their qualifications
          When the pipeline processes the email
          Then a Job Applicant IS created using the sender's identity
        """
        mock_payload = MagicMock()
        mock_payload.subject = "Application for CFO Position"
        mock_payload.sender_email = "candidate@gmail.com"
        mock_payload.sender_name = "Direct Candidate"
        mock_payload.body_text = (
            "Dear Hiring Manager,\n\n"
            "I am writing to express my interest in the CFO position.\n"
            "I have 15 years of experience in financial leadership roles.\n"
            "Please find my qualifications below...\n" * 5  # Make it > 50 chars
        )
        mock_payload.body_html = None
        mock_payload.message_id = "msg-direct-001"
        mock_payload.to = ["careers@amdg.ai"]
        mock_payload.attachments_download_url = None
        mock_payload.attachments = []

        mock_settings = MagicMock()
        mock_settings.max_attachment_size = 25000000

        mock_erpnext = MagicMock()
        mock_erpnext.upsert_job_applicant = MagicMock(return_value={"name": "HR-APP-00003"})
        mock_erpnext.create_communication = MagicMock()

        with patch("app.main.extract_resume") as mock_extract:
            mock_extract.return_value = {
                "applicant_name": "Direct Candidate",
                "email_id": "candidate@gmail.com",
                "designation": "CFO",
                "summary": "15 years financial leadership",
            }

            await _process_email(
                payload=mock_payload,
                settings=mock_settings,
                erpnext=mock_erpnext,
                event_id="test-e2e-003",
            )

        # Key assertion: Job Applicant WAS created for the direct applicant
        mock_erpnext.upsert_job_applicant.assert_called_once()
        call_data = mock_erpnext.upsert_job_applicant.call_args[0][0]
        assert call_data["email_id"] == "candidate@gmail.com"
        assert call_data["applicant_name"] == "Direct Candidate"
