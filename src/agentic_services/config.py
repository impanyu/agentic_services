from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPOSITORY_ROOT / ".env.local")


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None
    openai_model: str
    database_path: Path
    base_url: str
    snapshot_directory: Path | None = None
    provider_contact: str = "services@example.com"
    service_api_key: str | None = None
    payment_recipient: str | None = None
    price_usd: str = "0.05"
    max_tool_calls: int = 3
    max_output_tokens: int = 3000
    quick_price_usd: str = "0.02"
    deep_price_usd: str = "0.12"
    research_price_usd: str = "0.25"
    admin_api_key: str | None = None
    receipt_signing_secret: str | None = None
    openai_input_usd_per_million: str = "0.10"
    openai_cached_input_usd_per_million: str = "0.01"
    openai_output_usd_per_million: str = "0.50"
    openai_web_search_usd_per_thousand: str = "10.00"
    contact_recipient_email: str = "impanyu@gmail.com"
    contact_smtp_host: str = "smtp.gmail.com"
    contact_smtp_port: int = 465
    contact_smtp_username: str | None = None
    contact_smtp_app_password: str | None = None
    contact_ip_hash_secret: str | None = None
    contact_rate_limit_per_hour: int = 5
    photo_scout_intent_model: str | None = None
    photo_scout_intent_reasoning: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        database_value = os.getenv("WEB_EVIDENCE_DB", "data/web-evidence.db")
        database_path = Path(database_value)
        if not database_path.is_absolute():
            database_path = REPOSITORY_ROOT / database_path

        snapshot_value = os.getenv("WEB_EVIDENCE_SNAPSHOT_DIR")
        snapshot_directory = (
            Path(snapshot_value) if snapshot_value else database_path.parent / "snapshots"
        )
        if not snapshot_directory.is_absolute():
            snapshot_directory = REPOSITORY_ROOT / snapshot_directory

        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            photo_scout_intent_model=os.getenv("PHOTO_SCOUT_INTENT_MODEL", "gpt-6.1-sol") or None,
            photo_scout_intent_reasoning=os.getenv("PHOTO_SCOUT_INTENT_REASONING", "low") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-6-luna"),
            database_path=database_path,
            base_url=os.getenv("WEB_EVIDENCE_BASE_URL", "http://localhost:8000").rstrip("/"),
            snapshot_directory=snapshot_directory,
            provider_contact=os.getenv("WEB_EVIDENCE_PROVIDER_CONTACT", "services@example.com"),
            service_api_key=os.getenv("WEB_EVIDENCE_API_KEY") or None,
            payment_recipient=os.getenv("PAYMENT_RECIPIENT") or None,
            price_usd=os.getenv("WEB_EVIDENCE_PRICE_USD", "0.05"),
            max_tool_calls=int(os.getenv("OPENAI_MAX_TOOL_CALLS", "3")),
            max_output_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "3000")),
            quick_price_usd=os.getenv("WEB_EVIDENCE_QUICK_PRICE_USD", "0.02"),
            deep_price_usd=os.getenv("WEB_EVIDENCE_DEEP_PRICE_USD", "0.12"),
            research_price_usd=os.getenv("WEB_EVIDENCE_RESEARCH_PRICE_USD", "0.25"),
            admin_api_key=os.getenv("ADMIN_API_KEY") or None,
            receipt_signing_secret=os.getenv("RECEIPT_SIGNING_SECRET") or None,
            openai_input_usd_per_million=os.getenv("OPENAI_INPUT_USD_PER_MILLION", "0.10"),
            openai_cached_input_usd_per_million=os.getenv("OPENAI_CACHED_INPUT_USD_PER_MILLION", "0.01"),
            openai_output_usd_per_million=os.getenv("OPENAI_OUTPUT_USD_PER_MILLION", "0.50"),
            openai_web_search_usd_per_thousand=os.getenv("OPENAI_WEB_SEARCH_USD_PER_THOUSAND", "10.00"),
            contact_recipient_email=os.getenv("CONTACT_RECIPIENT_EMAIL", "impanyu@gmail.com"),
            contact_smtp_host=os.getenv("CONTACT_SMTP_HOST", "smtp.gmail.com"),
            contact_smtp_port=int(os.getenv("CONTACT_SMTP_PORT", "465")),
            contact_smtp_username=os.getenv("CONTACT_SMTP_USERNAME") or None,
            contact_smtp_app_password=os.getenv("CONTACT_SMTP_APP_PASSWORD") or None,
            contact_ip_hash_secret=os.getenv("CONTACT_IP_HASH_SECRET") or None,
            contact_rate_limit_per_hour=int(os.getenv("CONTACT_RATE_LIMIT_PER_HOUR", "5")),
        )
