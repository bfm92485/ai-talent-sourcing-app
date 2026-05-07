"""
BDD Tests for the Deterministic Email Parser Module.

Tests the parse_forwarded_email() function against all major email client
formats and edge cases.

Uses mail-parser-reply + thin header parser architecture.
"""

import pytest
from app.email_parser import (
    ForwardedEmailMetadata,
    is_forwarded_email,
    parse_forwarded_email,
)


# ============================================================
# SCENARIO: Outlook Calendar Forward (-----Original Appointment-----)
# This is the real .msg test case from production
# ============================================================


class TestOutlookCalendarForward:
    """Given a forwarded Outlook calendar invitation (-----Original Appointment-----)."""

    BODY = """
-----Original Appointment-----
From: Alexis Williams <Alexis.Williams@lhh.com> 
Sent: Wednesday, May 6, 2026 8:49 AM
To: Alexis Williams; Jon McDonald; Bryan Murphy; Charles Stoops
Subject: Jon R. McDonald TEAMS interview with Atlas Medical Data Group for CFO role 
When: Tuesday, May 12, 2026 9:00 AM-10:00 AM (UTC-06:00) Central Time (US & Canada).
Where: Microsoft Teams Meeting

Jon McDonald 
*	Healthcare-focused CFO with 15+ years of experience
*	Proven track record scaling organizations from ~$40M to $100M+

You are targeting a base of $300k depending on equity, bonus and ext.
"""
    SUBJECT = "FW: Jon R. McDonald TEAMS interview with Atlas Medical Data Group for CFO role"

    def test_detects_as_forwarded(self):
        """When parsed, then it should be detected as forwarded."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.is_forwarded is True

    def test_extracts_original_sender_name(self):
        """Then the original sender name should be 'Alexis Williams'."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.original_from_name == "Alexis Williams"

    def test_extracts_original_sender_email(self):
        """Then the original sender email should be 'Alexis.Williams@lhh.com'."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.original_from_email == "Alexis.Williams@lhh.com"

    def test_extracts_to_recipients(self):
        """Then the To: field should include all recipients."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert "Jon McDonald" in result.original_to
        assert "Bryan Murphy" in result.original_to

    def test_extracts_subject(self):
        """Then the original subject should reference the CFO role."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert "CFO role" in result.original_subject

    def test_extracts_date(self):
        """Then the sent date should be extracted."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert "May 6, 2026" in result.original_date

    def test_high_confidence(self):
        """Then confidence should be >= 0.8 (fully deterministic parse)."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.confidence >= 0.8

    def test_no_parse_errors(self):
        """Then there should be no parse errors."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.parse_errors == []

    def test_no_llm_fallback_needed(self):
        """Then LLM fallback should NOT be needed."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.needs_llm_fallback is False

    def test_referred_by_display(self):
        """Then referred_by_display should format as 'Name <email>'."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.referred_by_display == "Alexis Williams <Alexis.Williams@lhh.com>"


# ============================================================
# SCENARIO: Gmail Forward (---------- Forwarded message ---------)
# ============================================================


class TestGmailForward:
    """Given a forwarded email from Gmail."""

    BODY = """Hey, check out this candidate.

---------- Forwarded message ---------
From: Jane Recruiter <jane@recruitfirm.com>
Date: Mon, May 5, 2026 at 3:14 PM
Subject: Great CFO candidate - Sarah Johnson
To: Bryan Murphy <bryan.murphy@amdg.ai>

Hi Bryan,

I wanted to introduce you to Sarah Johnson who would be perfect for your CFO role.
She has 20 years of experience in healthcare finance.

Best,
Jane
"""
    SUBJECT = "Fwd: Great CFO candidate - Sarah Johnson"

    def test_detects_as_forwarded(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.is_forwarded is True

    def test_extracts_original_sender(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.original_from_name == "Jane Recruiter"
        assert result.original_from_email == "jane@recruitfirm.com"

    def test_extracts_subject(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert "Sarah Johnson" in result.original_subject

    def test_extracts_forwarding_message(self):
        """Then the forwarding message (text before the forward) should be captured."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert "check out this candidate" in (result.forwarding_message or "")

    def test_high_confidence(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.confidence >= 0.8


# ============================================================
# SCENARIO: Outlook -----Original Message----- format
# ============================================================


class TestOutlookOriginalMessage:
    """Given a forwarded email with Outlook's -----Original Message----- separator."""

    BODY = """FYI

-----Original Message-----
From: Bob Smith <bob.smith@headhunters.com>
Sent: Tuesday, May 6, 2026 10:00 AM
To: Bryan Murphy <bryan.murphy@amdg.ai>
Subject: RE: CFO Search Update

Bryan,

I have two candidates ready for you to review.
Please see attached resumes.

Regards,
Bob Smith
Managing Director
"""
    SUBJECT = "FW: RE: CFO Search Update"

    def test_detects_as_forwarded(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.is_forwarded is True

    def test_extracts_original_sender(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.original_from_name == "Bob Smith"
        assert result.original_from_email == "bob.smith@headhunters.com"

    def test_extracts_subject(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert "CFO Search Update" in result.original_subject

    def test_high_confidence(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.confidence >= 0.8


# ============================================================
# SCENARIO: Apple Mail (Begin forwarded message:)
# ============================================================


class TestAppleMailForward:
    """Given a forwarded email from Apple Mail."""

    BODY = """

Begin forwarded message:

From: Lisa Chen <lisa.chen@talentpartners.com>
Subject: Candidate Introduction - Michael Park
Date: May 5, 2026 at 2:30 PM CDT
To: Bryan Murphy <bryan.murphy@amdg.ai>

Hi Bryan,

Michael Park is an exceptional CFO candidate with PE-backed experience.

Lisa
"""
    SUBJECT = "Fwd: Candidate Introduction - Michael Park"

    def test_detects_as_forwarded(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.is_forwarded is True

    def test_extracts_original_sender(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.original_from_name == "Lisa Chen"
        assert result.original_from_email == "lisa.chen@talentpartners.com"

    def test_extracts_subject(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert "Michael Park" in result.original_subject

    def test_high_confidence(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.confidence >= 0.8


# ============================================================
# SCENARIO: Direct email (NOT forwarded)
# ============================================================


class TestDirectEmail:
    """Given a direct application email (not forwarded)."""

    BODY = """Hi,

I'm interested in the CFO position. Please find my resume attached.

Best regards,
Direct Applicant
"""
    SUBJECT = "Application for CFO Position"

    def test_not_detected_as_forwarded(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.is_forwarded is False

    def test_no_metadata_extracted(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.original_from_name is None
        assert result.original_from_email is None

    def test_referred_by_display_is_none(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.referred_by_display is None


# ============================================================
# SCENARIO: Forward detected by subject only (body not parseable)
# ============================================================


class TestSubjectOnlyForward:
    """Given an email with FW: subject but no parseable forward markers in body."""

    BODY = """Please review this candidate.

Thanks,
Bryan
"""
    SUBJECT = "FW: Candidate for review"

    def test_detects_as_forwarded_with_low_confidence(self):
        """Then it should be detected as forwarded but with low confidence."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.is_forwarded is True
        assert result.confidence < 0.5

    def test_flags_for_llm_fallback(self):
        """Then it should flag for LLM fallback."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.needs_llm_fallback is True

    def test_has_parse_errors(self):
        """Then parse_errors should explain what went wrong."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert len(result.parse_errors) > 0


# ============================================================
# SCENARIO: Email-only From line (no display name)
# ============================================================


class TestEmailOnlyFromLine:
    """Given a forwarded email where From: has only an email address."""

    BODY = """
---------- Forwarded message ---------
From: recruiter@agency.com
Date: May 5, 2026
Subject: Candidate intro
To: bryan.murphy@amdg.ai

Here's a great candidate.
"""
    SUBJECT = "Fwd: Candidate intro"

    def test_extracts_email(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.original_from_email == "recruiter@agency.com"

    def test_name_is_none(self):
        """Then name should be None since only email was provided."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.original_from_name is None

    def test_has_parse_error_about_name(self):
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert any("name" in e.lower() for e in result.parse_errors)

    def test_still_high_enough_confidence(self):
        """Confidence should be reduced but still above LLM threshold."""
        result = parse_forwarded_email(self.BODY, self.SUBJECT)
        assert result.confidence >= 0.5


# ============================================================
# SCENARIO: is_forwarded_email() convenience function
# ============================================================


class TestIsForwardedEmailHelper:
    """Test the lightweight is_forwarded_email() helper."""

    def test_returns_true_for_forwarded(self):
        assert is_forwarded_email(
            subject="FW: Test",
            body_text="-----Original Message-----\nFrom: test@test.com\nSubject: Hi\n\nBody",
        ) is True

    def test_returns_false_for_direct(self):
        assert is_forwarded_email(
            subject="Application",
            body_text="Hi, I'm applying for the role.",
        ) is False

    def test_returns_true_for_subject_only(self):
        assert is_forwarded_email(
            subject="Fwd: Something",
            body_text="Short body without markers",
        ) is True


# ============================================================
# SCENARIO: Empty/None inputs (defensive)
# ============================================================


class TestEdgeCases:
    """Test edge cases and defensive behavior."""

    def test_empty_body(self):
        result = parse_forwarded_email("", subject="FW: test")
        assert result.is_forwarded is True
        assert result.confidence < 0.5

    def test_none_subject(self):
        result = parse_forwarded_email("Just a normal email body.", subject=None)
        assert result.is_forwarded is False

    def test_very_short_body(self):
        result = parse_forwarded_email("Hi", subject=None)
        assert result.is_forwarded is False
