"""Configuration module — loads all settings from environment variables."""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    # Webhook security
    webhook_secret: str = field(
        default_factory=lambda: os.environ.get("WEBHOOK_SECRET", "dev-secret-change-me")
    )

    # ERPNext connection
    erpnext_url: str = field(
        default_factory=lambda: os.environ.get(
            "ERPNEXT_URL",
            "https://erpnext-v16-talent-sourcing-production.up.railway.app",
        )
    )
    erpnext_api_key: str = field(
        default_factory=lambda: os.environ.get("ERPNEXT_API_KEY", "")
    )
    erpnext_api_secret: str = field(
        default_factory=lambda: os.environ.get("ERPNEXT_API_SECRET", "")
    )

    # PrimitiveMail attachment download
    primitivemail_base_url: str = field(
        default_factory=lambda: os.environ.get("PRIMITIVEMAIL_BASE_URL", "http://localhost:3000")
    )

    # LLM (Gemini / OpenAI-compatible)
    gemini_api_key: str = field(
        default_factory=lambda: os.environ.get("GEMINI_API_KEY", "")
    )
    openai_api_key: str = field(
        default_factory=lambda: os.environ.get("OPENAI_API_KEY", "")
    )

    # Worker settings
    max_attachment_size: int = field(
        default_factory=lambda: int(os.environ.get("MAX_ATTACHMENT_SIZE", "25000000"))
    )
    data_ttl_days: int = field(
        default_factory=lambda: int(os.environ.get("DATA_TTL_DAYS", "90"))
    )
    port: int = field(
        default_factory=lambda: int(os.environ.get("PORT", "8080"))
    )


def get_settings() -> Settings:
    """Factory function for dependency injection."""
    return Settings()
