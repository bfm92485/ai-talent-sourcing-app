"""Security utilities — HMAC signature verification for webhook payloads.

Primitive (managed SaaS) signs webhook payloads with HMAC-SHA256 using the
secret configured in the Primitive dashboard. The signature is sent in the
X-Webhook-Signature header, optionally prefixed with 'sha256='.
"""

import hashlib
import hmac
import logging

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


def verify_hmac_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Verify HMAC-SHA256 signature of a webhook payload.

    Args:
        payload: Raw request body bytes.
        signature: The signature from X-Webhook-Signature header.
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

    In dev mode (secret = "dev-secret-change-me"), verification is skipped
    to allow local testing without a real Primitive webhook secret.

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

    signature = request.headers.get("X-Webhook-Signature", "")
    verify_hmac_signature(body, signature, secret)
    return body
