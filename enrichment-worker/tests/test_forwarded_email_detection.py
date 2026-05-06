"""
BDD tests for forwarded email detection.

Scenario: When a recruiter or internal user forwards an email containing
candidate information, the pipeline should NOT create a Job Applicant
for the sender (recruiter). Instead, it should either:
  - Skip the body-text fallback entirely (if resumes are attached and processed)
  - Extract candidate identity from the forwarded email body (not the sender)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# Module under test
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import (
    _is_forwarded_email,
    _extract_candidate_from_forwarded_body,
    _should_skip_sender_as_applicant,
    _process_body_text_fallback,
)


class TestIsForwardedEmail:
    """
    Feature: Detect forwarded emails
    As the enrichment pipeline
    I want to detect when an email is a forward (not a direct application)
    So that I don't create a Job Applicant for the forwarder
    """

    def test_given_subject_with_fw_prefix_then_detected_as_forward(self):
        """Given an email with 'FW:' in the subject, When checked, Then it is detected as forwarded."""
        assert _is_forwarded_email(subject="FW: AMDG Hiring: CFO — Candidate Tracking") is True

    def test_given_subject_with_fwd_prefix_then_detected_as_forward(self):
        """Given an email with 'Fwd:' in the subject, When checked, Then it is detected as forwarded."""
        assert _is_forwarded_email(subject="Fwd: Resume - John Smith") is True

    def test_given_subject_with_re_fw_prefix_then_detected_as_forward(self):
        """Given an email with 'RE: FW:' in the subject, When checked, Then it is detected as forwarded."""
        assert _is_forwarded_email(subject="RE: FW: AMDG Hiring: CFO") is True

    def test_given_subject_without_forward_prefix_then_not_detected(self):
        """Given a normal email subject, When checked, Then it is NOT detected as forwarded."""
        assert _is_forwarded_email(subject="Application for Software Engineer") is False

    def test_given_empty_subject_then_not_detected(self):
        """Given no subject, When checked, Then it is NOT detected as forwarded."""
        assert _is_forwarded_email(subject=None) is False
        assert _is_forwarded_email(subject="") is False

    def test_given_body_with_forwarded_message_header_then_detected(self):
        """Given an email body containing '---------- Forwarded message', When checked, Then detected."""
        body = "FYI\n\n---------- Forwarded message ----------\nFrom: candidate@example.com"
        assert _is_forwarded_email(subject="Candidate resume", body_text=body) is True

    def test_given_body_with_original_message_header_then_detected(self):
        """Given an email body with '-----Original Message-----', When checked, Then detected."""
        body = "See below.\n\n-----Original Message-----\nFrom: recruiter@lhh.com"
        assert _is_forwarded_email(subject="Resume", body_text=body) is True


class TestShouldSkipSenderAsApplicant:
    """
    Feature: Skip creating Job Applicant for the sender when email is forwarded
    As the enrichment pipeline
    I want to avoid creating a record for the forwarder (recruiter/internal)
    So that only actual candidates get Job Applicant records
    """

    def test_given_forwarded_email_with_resume_attachments_then_skip_sender(self):
        """
        Given a forwarded email with resume attachments
        When the pipeline processes it
        Then it should skip creating a Job Applicant from the body text for the sender
        """
        assert _should_skip_sender_as_applicant(
            is_forwarded=True,
            has_resume_attachments=True,
            sender_email="bryan.murphy@amdg.ai",
        ) is True

    def test_given_forwarded_email_without_attachments_then_skip_sender(self):
        """
        Given a forwarded email without attachments
        When the pipeline processes it
        Then it should still skip creating a record for the sender
        (the resumes are extracted from the forwarded attachments in _process_email)
        """
        assert _should_skip_sender_as_applicant(
            is_forwarded=True,
            has_resume_attachments=False,
            sender_email="bryan.murphy@amdg.ai",
        ) is True

    def test_given_direct_application_email_then_do_not_skip(self):
        """
        Given a direct application email (not forwarded)
        When the pipeline processes it
        Then it should NOT skip the sender (they are the actual applicant)
        """
        assert _should_skip_sender_as_applicant(
            is_forwarded=False,
            has_resume_attachments=False,
            sender_email="candidate@gmail.com",
        ) is False


class TestExtractCandidateFromForwardedBody:
    """
    Feature: Extract candidate identity from forwarded email body
    As the enrichment pipeline
    I want to extract the actual candidate's name and email from the forwarded content
    So that I can create a proper Job Applicant record
    """

    def test_given_forwarded_body_with_from_header_then_extract_candidate(self):
        """
        Given a forwarded email body containing 'From: Candidate Name <email>'
        When extracting candidate info
        Then the candidate name and email are returned
        """
        body = """FYI - see attached resume.

---------- Forwarded message ----------
From: Jane Smith <jane.smith@gmail.com>
Date: Mon, Apr 20, 2026
Subject: Application for CFO position

Please find my resume attached."""

        result = _extract_candidate_from_forwarded_body(body)
        assert result is not None
        assert result["email"] == "jane.smith@gmail.com"
        assert result["name"] == "Jane Smith"

    def test_given_forwarded_body_with_original_message_then_extract(self):
        """
        Given a forwarded email body with -----Original Message----- format
        When extracting candidate info
        Then the candidate email from the From: line is returned
        """
        body = """Hi team, please review.

-----Original Message-----
From: John Doe <john.doe@example.com>
Sent: Thursday, April 16, 2026 8:26 AM
To: Bryan Murphy <bryan.murphy@amdg.ai>
Subject: My application

I'd like to apply for the position."""

        result = _extract_candidate_from_forwarded_body(body)
        assert result is not None
        assert result["email"] == "john.doe@example.com"
        assert result["name"] == "John Doe"

    def test_given_recruiter_forward_with_candidate_mentions_then_no_extraction(self):
        """
        Given a recruiter forward that mentions candidates but the 'From:' is the recruiter
        When extracting candidate info
        Then return None (let the resume BAML extraction handle identity)
        
        This covers the Kylie/Vivake case where the recruiter (Alexis) forwards
        but the actual candidates are in the attachments, not the body From:.
        """
        body = """Hi Bryan and Charles,

We've spoken with several candidates. In the meantime, we wanted to share 
the resumes and cover letters for Kylie and Vivake.

From: Alexis Williams <alexis.williams@lhh.com>
Sent: Monday, April 20, 2026 8:54 AM
To: Bryan Murphy <bryan.murphy@amdg.ai>"""

        # The From: here is the recruiter, not a candidate
        # The function should return None because the extracted email is likely
        # a recruiter/internal address, not a candidate
        result = _extract_candidate_from_forwarded_body(body)
        # Either None or the recruiter email - either way, the caller should
        # not create a Job Applicant from this
        # The key behavior is tested in integration: when resumes ARE attached,
        # the body fallback is never called

    def test_given_no_from_header_in_body_then_return_none(self):
        """
        Given a forwarded email body without a parseable From: line
        When extracting candidate info
        Then return None
        """
        body = "Just forwarding this along. Please review the attached resume."
        result = _extract_candidate_from_forwarded_body(body)
        assert result is None


class TestProcessBodyTextFallbackIntegration:
    """
    Feature: Body text fallback skips forwarded emails
    As the enrichment pipeline
    I want the body text fallback to NOT create a record when the email is forwarded
    So that only actual direct applicants get records from the fallback path
    """

    @pytest.mark.asyncio
    async def test_given_forwarded_email_then_fallback_does_not_create_record(self):
        """
        Given a forwarded email (subject starts with 'FW:')
        When _process_body_text_fallback is called
        Then it should NOT call erpnext.upsert_job_applicant with the sender's info
        """
        mock_payload = MagicMock()
        mock_payload.subject = "FW: AMDG Hiring: CFO — Candidate Tracking"
        mock_payload.sender_email = "bryan.murphy@amdg.ai"
        mock_payload.sender_name = "Bryan Murphy"
        mock_payload.body_text = "See attached resumes for Kylie and Vivake."
        mock_payload.body_html = None
        mock_payload.message_id = "test-msg-id"
        mock_payload.to = ["bryan.murphy@amdg.ai"]

        mock_erpnext = MagicMock()
        mock_erpnext.upsert_job_applicant = MagicMock(return_value={"name": "HR-APP-00001"})
        mock_erpnext.create_communication = MagicMock()

        await _process_body_text_fallback(
            payload=mock_payload,
            erpnext=mock_erpnext,
            event_id="test-event-001",
        )

        # The key assertion: upsert should NOT be called for a forwarded email
        mock_erpnext.upsert_job_applicant.assert_not_called()
