"""Central configuration.

Every value comes from the environment (loaded from ``backend/.env`` via
python-dotenv + pydantic-settings). Nothing sensitive is ever hardcoded here --
see ``.env.example`` for the template.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/
BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Load backend/.env before pydantic-settings reads os.environ so that both
# mechanisms see the same values regardless of the process working directory.
load_dotenv(BACKEND_ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ---------------------------------------------------------------
    app_name: str = "Docs Scanner Backend"
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ---- Postgres ------------------------------------------------------------
    # Full DSN, e.g. Aiven's `postgres://user:pass@host:port/db?sslmode=require`.
    # Accepted as either `postgres://` or `postgresql://` -- see
    # `sqlalchemy_url` below, which normalizes it to the psycopg2 driver URL
    # SQLAlchemy needs (`postgresql+psycopg2://...`).
    database_url: str = Field(default="", alias="DATABASE_URL")
    sql_echo: bool = Field(default=False, alias="SQL_ECHO")

    # ---- JWT / sessions ----------------------------------------------------
    jwt_secret: str = Field(default="", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_expires_minutes: int = Field(default=60 * 24 * 30, alias="JWT_EXPIRES_MINUTES")

    # ---- OTP ---------------------------------------------------------------
    otp_length: int = Field(default=5, alias="OTP_LENGTH")
    otp_ttl_seconds: int = Field(default=600, alias="OTP_TTL_SECONDS")  # 10 minutes
    otp_max_attempts: int = Field(default=5, alias="OTP_MAX_ATTEMPTS")
    otp_min_interval_seconds: int = Field(default=60, alias="OTP_MIN_INTERVAL_SECONDS")
    otp_max_per_hour: int = Field(default=5, alias="OTP_MAX_PER_HOUR")

    # ---- Email delivery ----------------------------------------------------
    email_provider: str = Field(default="smtp", alias="EMAIL_PROVIDER")
    email_from: str = Field(default="", alias="EMAIL_FROM")
    resend_api_key: str = Field(default="", alias="RESEND_API_KEY")

    # ---- Gmail SMTP --------------------------------------------------------
    smtp_host: str = Field(default="smtp.gmail.com", alias="SMTP_HOST")
    smtp_port: int = Field(default=465, alias="SMTP_PORT")  # 465 = implicit TLS
    smtp_use_ssl: bool = Field(default=True, alias="SMTP_USE_SSL")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_app_password: str = Field(default="", alias="SMTP_APP_PASSWORD")
    smtp_from_name: str = Field(default="Docs Scanner", alias="SMTP_FROM_NAME")
    smtp_timeout_seconds: int = Field(default=20, alias="SMTP_TIMEOUT_SECONDS")
    # When true, the OTP is logged instead of emailed (local dev without SMTP).
    smtp_dev_mode: bool = Field(default=False, alias="SMTP_DEV_MODE")

    # ---- Storage -----------------------------------------------------------
    storage_root: str = Field(
        default=str(BACKEND_ROOT / "storage"), alias="PRISM_STORAGE_ROOT"
    )
    max_upload_bytes: int = Field(default=100 * 1024 * 1024, alias="MAX_UPLOAD_BYTES")
    max_ocr_image_dimension: int = Field(
        default=2200, alias="MAX_OCR_IMAGE_DIMENSION"
    )

    # ---- Document scanning -------------------------------------------------
    # OpenCV-Document-Scanner performs corner detection, perspective correction,
    # sharpening, and adaptive thresholding. It does not recognize text.
    # Optional Unicode TTF used when exporting searchable PDFs containing
    # non-Latin text (Helvetica cannot encode CJK/Cyrillic/etc).
    pdf_unicode_font_path: str | None = Field(
        default=None, alias="PDF_UNICODE_FONT_PATH"
    )

    # ---- Passport photo ----------------------------------------------------
    rembg_model: str = Field(default="u2net_human_seg", alias="REMBG_MODEL")
    # Fraction of pixels that must be confidently "subject" for the result to
    # be considered a valid segmentation.
    passport_min_subject_ratio: float = Field(
        default=0.03, alias="PASSPORT_MIN_SUBJECT_RATIO"
    )
    passport_max_subject_ratio: float = Field(
        default=0.97, alias="PASSPORT_MAX_SUBJECT_RATIO"
    )
    passport_jpeg_quality: int = Field(default=95, alias="PASSPORT_JPEG_QUALITY")

    # ---- CORS --------------------------------------------------------------
    cors_allow_origins: str = Field(default="*", alias="CORS_ALLOW_ORIGINS")

    # ---- Firebase Cloud Messaging -------------------------------------------
    # Either a filesystem path to the service-account JSON, or the JSON
    # itself pasted inline (Railway env vars can't hold files -- see
    # `firebase_admin_credentials` below, which accepts either shape).
    firebase_service_account_json: str = Field(
        default="", alias="FIREBASE_SERVICE_ACCOUNT_JSON"
    )

    # ---- Force update --------------------------------------------------------
    # Seed values only -- the live values the app checks against live in the
    # `app_config` table (admin-editable); these just give that table
    # something sane to start from the first time it's read.
    min_supported_version: str = Field(default="1.0.0", alias="MIN_SUPPORTED_VERSION")
    play_store_url: str = Field(default="", alias="PLAY_STORE_URL")

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not set -- add it to backend/.env (see "
                ".env.example)."
            )
        # SQLAlchemy needs an explicit driver; psycopg2 is what's installed
        # (see requirements.txt). Aiven/Railway hand out `postgres://`, which
        # psycopg2 itself still accepts, but SQLAlchemy 2.x only recognizes
        # `postgresql://` as the base scheme.
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_root).expanduser().resolve()

    @property
    def cors_origins_list(self) -> list[str]:
        raw = (self.cors_allow_origins or "*").strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
