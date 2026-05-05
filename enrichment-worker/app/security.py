"""Security utilities — HMAC signature verification for webhook payloads.

PrimitiveMail (self-hosted) signs webhook payloads using its SDK:
- Header: "Primitive-Signature" (or legacy "MyMX-Signature")
- Format: "t={unix_timestamp},v1={hmac_hex}"
- HMAC is computed over "{timestamp}.{body}" using SHA-256

We also support the simpler "X-Webhook-Signature" header with just the
hex digest (for backward compatibility with earlier implementations).
"""

import hashlib
import hmac
import logging
import time

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

# Maximum age of a webhook signature (5 minutes) to prevent replay attacks
MAX_SIGNATURE_AGE_SECONDS = 300


def _parse_primitive_signature(header_value: str) -> tuple[int, str]:
    """
    Parse PrimitiveMail's signature header format: "t={timestamp},v1={hmac_hex}"

    Returns:
        Tuple of (timestamp, v1_signature)

    Raises:
        ValueError if the header format is invalid.
    """
    parts = {}
    for segment in header_value.split(","):
        segment = segment.strip()
        if "=" in segment:
            key, _, value = segment.partition("=")
            parts[key.strip()] = value.strip()

    if "t" not in parts or "v1" not in parts:
        raise ValueError(f"Invalid Primitive-Signature format: {header_value}")

    try:
        timestamp = int(parts["t"])
    except (ValueError, TypeError):
        raise ValueError(f"Invalid timestamp in signature: {parts['t']}")

    return timestamp, parts["v1"]


def verify_primitive_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    """
    Verify PrimitiveMail SDK's webhook signature.

    The SDK signs "{timestamp}.{body}" with HMAC-SHA256 and sends:
    Primitive-Signature: t={timestamp},v1={hmac_hex}

    Args:
        payload: Raw request body bytes.
        signature_header: The full Primitive-Signature header value.
        secret: Shared HMAC secret.

    Returns:
        True if signature is valid.

    Raises:
        HTTPException(401) if signature is invalid, missing, or expired.
    """
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing signature header")

    try:
        timestamp, v1_signature = _parse_primitive_signature(signature_header)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=f"Invalid signature format: {e}")

    # Check timestamp freshness (prevent replay attacks)
    now = int(time.time())
    if abs(now - timestamp) > MAX_SIGNATURE_AGE_SECONDS:
        raise HTTPException(
            status_code=401,
            detail=f"Signature expired (age: {abs(now - timestamp)}s, max: {MAX_SIGNATURE_AGE_SECONDS}s)",
        )

    # Reconstruct the signed payload: "{timestamp}.{body}"
    body_str = payload.decode("utf-8", errors="replace")
    signed_payload = f"{timestamp}.{body_str}"

    expected = hmac.HMAC(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, v1_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    return True


def verify_simple_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify a simple HMAC-SHA256 signature (legacy format).

    Args:
        payload: Raw request body bytes.
        signature: The signature from X-Webhook-Signature header (optionally prefixed with 'sha256=').
        secret: Shared HMAC secret.

    Returns:
        True if signature is valid.

    Raises:
        HTTPException(401) if signature is invalid or missing.
    """
    if not signature:
        raise HTTPException(status_code=401, detail="Missing X-Webhook-Signature header")

    # Strip optional "sha256=" prefix
    if signature.startswith("sha256="):
        signature = signature[7:]

    expected = hmac.HMAC(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    return True


async def get_verified_body(request: Request, secret: str) -> bytes:
    """
    Read request body and verify its HMAC signature.

    Supports multiple signature formats:
    1. PrimitiveMail SDK format: "Primitive-Signature: t={ts},v1={hmac}" (preferred)
    2. Legacy PrimitiveMail: "MyMX-Signature: t={ts},v1={hmac}"
    3. Simple format: "X-Webhook-Signature: {hex_digest}" or "X-Webhook-Signature: sha256={hex_digest}"

    In dev mode (secret = "dev-secret-change-me"), verification is skipped.

    Args:
        request: FastAPI Request object.
        secret: Shared HMAC secret.

    Returns:
        Verified raw body bytes.
    """
    body = await request.body()

    # Dev mode bypass
    if secret == "dev-secret-change-me":
        logger.warning("HMAC verification SKIPPED (dev mode — set WEBHOOK_SECRET for production)")
        return body

    # Try Primitive-Signature header first (preferred for self-hosted PrimitiveMail)
    primitive_sig = request.headers.get("Primitive-Signature", "")
    if primitive_sig:
        verify_primitive_signature(body, primitive_sig, secret)
        logger.debug("Verified via Primitive-Signature header")
        return body

    # Try legacy MyMX-Signature header
    mymx_sig = request.headers.get("MyMX-Signature", "")
    if mymx_sig:
        verify_primitive_signature(body, mymx_sig, secret)
        logger.debug("Verified via MyMX-Signature header")
        return body

    # Fall back to simple X-Webhook-Signature
    simple_sig = request.headers.get("X-Webhook-Signature", "")
    if simple_sig:
        verify_simple_signature(body, simple_sig, secret)
        logger.debug("Verified via X-Webhook-Signature header")
        return body

    # No signature header found
    raise HTTPException(
        status_code=401,
        detail="Missing webhook signature header (expected Primitive-Signature, MyMX-Signature, or X-Webhook-Signature)",
    )
