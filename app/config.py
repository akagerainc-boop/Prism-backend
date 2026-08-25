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
    app_name: str = "Prism Scanner Backend"
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ---- MySQL (XAMPP) -----------------------------------------------------
    # Either supply a full DSN, or the individual parts below.
    mysql_dsn: str | None = Field(default=None, alias="MYSQL_DSN")
    mysql_host: str = Field(default="127.0.0.1", alias="MYSQL_HOST")
    mysql_port: int = Field(default=3306, alias="MYSQL_PORT")
    mysql_user: str = Field(default="root", alias="MYSQL_USER")
    mysql_password: str = Field(default="", alias="MYSQL_PASSWORD")
    mysql_database: str = Field(default="prism", alias="MYSQL_DATABASE")
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
    smtp_from_name: str = Field(default="Prism Scanner", alias="SMTP_FROM_NAME")
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

    @property
    def sqlalchemy_url(self) -> str:
        if self.mysql_dsn:
            return self.mysql_dsn
        from urllib.parse import quote_plus

        pwd = quote_plus(self.mysql_password)
        user = quote_plus(self.mysql_user)
        return (
            f"mysql+pymysql://{user}:{pwd}@{self.mysql_host}:{self.mysql_port}"
            f"/{self.mysql_database}?charset=utf8mb4"
        )

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
