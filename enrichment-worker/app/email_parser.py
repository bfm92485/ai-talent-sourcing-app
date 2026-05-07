"""
Deterministic Forwarded Email Parser
=====================================

Extracts structured metadata from forwarded emails using `mail-parser-reply`
for reply splitting and a thin regex layer for header field extraction.

Architecture:
    1. mail-parser-reply splits the email body into replies (deterministic, multi-language)
    2. Thin regex layer parses the raw headers string into structured fields
    3. Confidence scoring flags anomalies for optional LLM fallback

Supported clients:
    - Gmail (---------- Forwarded message ---------)
    - Outlook (-----Original Message-----, -----Original Appointment-----)
    - Apple Mail (Begin forwarded message:)
    - Thunderbird, Yahoo Mail, HubSpot, and more (via mail-parser-reply)

Supported languages:
    Danish, Dutch, English, French, German, Italian, Japanese, Polish, Swedish,
    Czech, Spanish, Korean, Chinese (via mail-parser-reply)

Error handling:
    - Returns ForwardedEmailMetadata with confidence score (0.0-1.0)
    - parse_errors list provides clear diagnostic messages
    - Caller should escalate to LLM fallback when confidence < 0.5

Usage:
    from app.email_parser import parse_forwarded_email, ForwardedEmailMetadata

    result = parse_forwarded_email(body_text, subject)
    if result.is_forwarded:
        print(f"Referred by: {result.original_from_name} <{result.original_from_email}>")
    if result.confidence < 0.5:
        # Escalate to LLM fallback
        ...
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from mailparser_reply import EmailReplyParser

logger = logging.getLogger(__name__)

# Subject-line forward detection (handles FW:, Fwd:, and RE: FW: combinations)
_FORWARD_SUBJECT_PATTERN = re.compile(r"^\s*(fw|fwd)\s*:", re.IGNORECASE)

# Header field extraction patterns (applied to the raw headers string)
_FROM_NAME_EMAIL = re.compile(r"^From:\s*(.+?)\s*<([^>]+)>", re.MULTILINE)
_FROM_EMAIL_ONLY = re.compile(
    r"^From:\s*<?([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>?",
    re.MULTILINE,
)
_FROM_NAME_ONLY = re.compile(r"^From:\s*(.+)$", re.MULTILINE)
_TO_LINE = re.compile(r"^To:\s*(.+)$", re.MULTILINE)
_CC_LINE = re.compile(r"^Cc:\s*(.+)$", re.MULTILINE)
_SUBJECT_LINE = re.compile(r"^Subject:\s*(.+)$", re.MULTILINE)
_DATE_LINE = re.compile(r"^(?:Date|Sent):\s*(.+)$", re.MULTILINE)


@dataclass
class ForwardedEmailMetadata:
    """Structured metadata extracted from a forwarded email.

    Attributes:
        is_forwarded: Whether the email was detected as a forward.
        original_from_name: Display name of the original sender (e.g., recruiter).
        original_from_email: Email address of the original sender.
        original_to: To: line from the forwarded email (raw string).
        original_cc: Cc: line from the forwarded email (raw string).
        original_subject: Subject of the original forwarded email.
        original_date: Date/Sent value from the forwarded email.
        forwarding_message: Text the forwarder wrote above the forwarded content.
        original_body: Body text of the original forwarded email.
        confidence: Parse confidence (1.0 = fully parsed, <0.5 = needs LLM fallback).
        parse_errors: List of diagnostic error messages for debugging.
    """

    is_forwarded: bool = False
    original_from_name: Optional[str] = None
    original_from_email: Optional[str] = None
    original_to: Optional[str] = None
    original_cc: Optional[str] = None
    original_subject: Optional[str] = None
    original_date: Optional[str] = None
    forwarding_message: Optional[str] = None
    original_body: Optional[str] = None
    confidence: float = 1.0
    parse_errors: list[str] = field(default_factory=list)

    @property
    def needs_llm_fallback(self) -> bool:
        """Whether the parse confidence is too low and LLM fallback is recommended."""
        return self.confidence < 0.5

    @property
    def referred_by_display(self) -> Optional[str]:
        """Format the referral source as 'Name <email>' for display in the ATS."""
        if not self.is_forwarded:
            return None
        if self.original_from_name and self.original_from_email:
            return f"{self.original_from_name} <{self.original_from_email}>"
        if self.original_from_email:
            return self.original_from_email
        if self.original_from_name:
            return self.original_from_name
        return None


def parse_forwarded_email(
    body_text: str,
    subject: Optional[str] = None,
    languages: Optional[list[str]] = None,
) -> ForwardedEmailMetadata:
    """
    Deterministically parse a forwarded email to extract original sender metadata.

    Uses mail-parser-reply for reply splitting, then a thin regex layer to parse
    the extracted headers into structured fields.

    Args:
        body_text: Plain text body of the email.
        subject: Email subject line (used for forward detection).
        languages: Languages for mail-parser-reply (default: ['en']).

    Returns:
        ForwardedEmailMetadata with structured fields and confidence score.
        If confidence < 0.5, caller should escalate to LLM fallback.
    """
    if languages is None:
        languages = ["en"]

    result = ForwardedEmailMetadata()

    if not body_text:
        if subject and _FORWARD_SUBJECT_PATTERN.match(
            re.sub(r"^\s*(re\s*:\s*)+", "", subject, flags=re.IGNORECASE)
        ):
            result.is_forwarded = True
            result.confidence = 0.2
            result.parse_errors.append("Subject indicates forward but no body text available")
        return result

    # Step 1: Check subject for forward indicators
    forward_subject = False
    if subject:
        cleaned_subject = re.sub(r"^\s*(re\s*:\s*)+", "", subject, flags=re.IGNORECASE)
        forward_subject = bool(
            _FORWARD_SUBJECT_PATTERN.match(cleaned_subject)
            or _FORWARD_SUBJECT_PATTERN.match(subject)
        )

    # Step 2: Use mail-parser-reply to split the email into replies
    parser = EmailReplyParser(languages=languages)
    parsed = parser.read(text=body_text)

    if len(parsed.replies) < 2:
        # Body could not be split into multiple parts
        if forward_subject:
            result.is_forwarded = True
            result.confidence = 0.3
            result.parse_errors.append(
                "Subject indicates forward but body could not be split into replies"
            )
        return result

    # Step 3: Find the reply that contains actual headers (From:, To:, Subject:)
    # mail-parser-reply may split into 2 or 3+ parts depending on the separator format
    forwarded_reply = None
    for reply in parsed.replies[1:]:
        if reply.headers and re.search(r"^From:", reply.headers, re.MULTILINE):
            forwarded_reply = reply
            break

    if forwarded_reply is None:
        # No reply with From: header found
        if forward_subject or len(parsed.replies) > 1:
            result.is_forwarded = True
            result.confidence = 0.4
            result.parse_errors.append(
                "Email split into replies but no From: header found in forwarded content"
            )
        return result

    headers_raw = forwarded_reply.headers

    # Step 4: Parse the raw headers string into structured fields
    result.is_forwarded = True
    result.forwarding_message = (
        parsed.replies[0].body.strip() if parsed.replies[0].body else None
    )
    result.original_body = forwarded_reply.body if forwarded_reply.body else None

    # Parse From: line — try "Name <email>" first, then email-only, then name-only
    from_match = _FROM_NAME_EMAIL.search(headers_raw)
    if from_match:
        result.original_from_name = from_match.group(1).strip().strip('"')
        result.original_from_email = from_match.group(2).strip()
    else:
        from_email_only = _FROM_EMAIL_ONLY.search(headers_raw)
        if from_email_only:
            result.original_from_email = from_email_only.group(1)
            result.parse_errors.append("From name not found, only email extracted")
        else:
            from_name_only = _FROM_NAME_ONLY.search(headers_raw)
            if from_name_only:
                result.original_from_name = from_name_only.group(1).strip()
                result.parse_errors.append(
                    "From line found but email could not be extracted"
                )
            else:
                result.parse_errors.append("No From: line found in headers")

    # Parse To: line
    to_match = _TO_LINE.search(headers_raw)
    if to_match:
        result.original_to = to_match.group(1).strip()

    # Parse Cc: line
    cc_match = _CC_LINE.search(headers_raw)
    if cc_match:
        result.original_cc = cc_match.group(1).strip()

    # Parse Subject: line
    subj_match = _SUBJECT_LINE.search(headers_raw)
    if subj_match:
        result.original_subject = subj_match.group(1).strip()

    # Parse Date/Sent: line
    date_match = _DATE_LINE.search(headers_raw)
    if date_match:
        result.original_date = date_match.group(1).strip()

    # Step 5: Calculate confidence score
    confidence = 1.0
    if not result.original_from_email:
        confidence -= 0.3
    if not result.original_from_name:
        confidence -= 0.1
    if not result.original_subject:
        confidence -= 0.1
    if not result.original_date:
        confidence -= 0.1
    if result.parse_errors:
        confidence -= 0.1 * len(result.parse_errors)
    result.confidence = max(0.0, min(1.0, confidence))

    logger.info(
        f"Forwarded email parsed: from={result.original_from_name} "
        f"<{result.original_from_email}>, confidence={result.confidence:.2f}"
    )

    return result


def is_forwarded_email(
    subject: Optional[str] = None,
    body_text: Optional[str] = None,
    languages: Optional[list[str]] = None,
) -> bool:
    """
    Quick check: is this email a forward?

    This is a lightweight wrapper around parse_forwarded_email that only returns
    the boolean result. Use parse_forwarded_email() when you need the full metadata.
    """
    result = parse_forwarded_email(
        body_text=body_text or "",
        subject=subject,
        languages=languages,
    )
    return result.is_forwarded
