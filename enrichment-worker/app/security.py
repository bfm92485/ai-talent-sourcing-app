"""Security utilities — HMAC signature verification for webhook payloads."""

import hashlib
import hmac
from fastapi import HTTPException, Request


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

    Args:
        request: FastAPI Request object.
        secret: Shared HMAC secret.

    Returns:
        Verified raw body bytes.
    """
    body = await request.body()
    signature = request.headers.get("X-Webhook-Signature", "")
    verify_hmac_signature(body, signature, secret)
    return body
