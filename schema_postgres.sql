-- ---------------------------------------------------------------------------
-- Prism Scanner -- Postgres schema
--
-- Replaces schema.sql (MySQL/XAMPP), which is no longer used. Apply with:
--     psql "$DATABASE_URL" -f schema_postgres.sql
--
-- Safe to re-run: every object uses IF NOT EXISTS. Dropping is NOT done here
-- on purpose so an accidental re-apply can never destroy user data.
--
-- Timestamps are plain TIMESTAMP (no time zone), matching the app's existing
-- naive-datetime code (it never attached a time zone under MySQL's DATETIME
-- either) -- not TIMESTAMPTZ, to avoid changing behaviour during the
-- migration. `updated_at`/`modified_at`/`last_seen_at` columns are kept
-- current by SQLAlchemy's `onupdate=func.now()` on every ORM write (see
-- app/models.py) rather than a DB-level trigger -- MySQL's inline
-- `ON UPDATE CURRENT_TIMESTAMP` has no direct Postgres DDL equivalent, and
-- every write to these tables already goes through the ORM.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- users -- one row per verified email. Created on first successful OTP verify
-- (signup and login are the same flow).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id            BIGSERIAL    PRIMARY KEY,
  email         VARCHAR(320) NOT NULL UNIQUE,
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TIMESTAMP    NULL,
  is_active     BOOLEAN      NOT NULL DEFAULT TRUE
);

-- ---------------------------------------------------------------------------
-- accounts -- Prism Cloud plan + the storage limit that plan grants.
-- Free plan is 1 GB (see kCloudStorageLimitMb in the Flutter client).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
  user_id             BIGINT      PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  plan                VARCHAR(32) NOT NULL DEFAULT 'free',
  storage_limit_bytes BIGINT      NOT NULL DEFAULT 1000000000,
  created_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- otp_codes -- issued OTP challenges. The OTP itself is NEVER stored in
-- plaintext: otp_hash is PBKDF2-HMAC-SHA256(otp, otp_salt).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS otp_codes (
  id                 BIGSERIAL    PRIMARY KEY,
  email              VARCHAR(320) NOT NULL,
  verification_token VARCHAR(128) NOT NULL UNIQUE,
  otp_hash           VARCHAR(128) NOT NULL,
  otp_salt           VARCHAR(64)  NOT NULL,
  expires_at         TIMESTAMP    NOT NULL,
  consumed_at        TIMESTAMP    NULL,
  attempts           INT          NOT NULL DEFAULT 0,
  created_at         TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_otp_codes_email ON otp_codes (email);
CREATE INDEX IF NOT EXISTS ix_otp_codes_expires_at ON otp_codes (expires_at);

-- ---------------------------------------------------------------------------
-- otp_requests -- request timestamps used for per-email rate limiting
-- (max 1 per 60s, max 5 per hour) so the email-sending quota can't be burned.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS otp_requests (
  id           BIGSERIAL    PRIMARY KEY,
  email        VARCHAR(320) NOT NULL,
  requested_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  client_ip    VARCHAR(64)  NULL,
  delivered    BOOLEAN      NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_otp_requests_email_time ON otp_requests (email, requested_at);

-- ---------------------------------------------------------------------------
-- documents -- PDFs backed up to Prism Cloud. Bytes live in `file_data`
-- (BYTEA) so they survive Railway's ephemeral filesystem; this table is
-- both the index and the store.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
  id              VARCHAR(36)   PRIMARY KEY,
  user_id         BIGINT        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name            VARCHAR(512)  NOT NULL,
  size_bytes      BIGINT        NOT NULL DEFAULT 0,
  file_data       BYTEA         NOT NULL,
  content_type    VARCHAR(128)  NOT NULL DEFAULT 'application/pdf',
  checksum_sha256 VARCHAR(64)   NULL,
  created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  modified_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at      TIMESTAMP     NULL
);
CREATE INDEX IF NOT EXISTS ix_documents_user_modified ON documents (user_id, modified_at);

-- ---------------------------------------------------------------------------
-- cards -- Wallet cards (bank card, national ID, passport, driving license)
-- synced to Prism Cloud. `card_data` holds every non-image field as JSONB;
-- front/back photos (ID/passport/license only) live in their own BYTEA
-- columns, same blob-in-the-row approach as `documents`.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
  id           VARCHAR(36)  PRIMARY KEY,
  user_id      BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type         VARCHAR(32)  NOT NULL,
  card_data    JSONB        NOT NULL,
  front_image  BYTEA        NULL,
  back_image   BYTEA        NULL,
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  modified_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at   TIMESTAMP    NULL
);
CREATE INDEX IF NOT EXISTS ix_cards_user_modified ON cards (user_id, modified_at);

-- ---------------------------------------------------------------------------
-- scan_feedback -- "How was this scan?" prompt, shown every 5th document
-- scanned, capped at the first 2 prompts (client-side counter).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scan_feedback (
  id          VARCHAR(36) PRIMARY KEY,
  user_id     BIGINT      NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  rating      VARCHAR(16) NOT NULL,
  suggestion  TEXT        NULL,
  created_at  TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_scan_feedback_user ON scan_feedback (user_id);

-- ---------------------------------------------------------------------------
-- storage_usage -- cheap-to-read cache of consumed bytes. The authoritative
-- value is always SUM(documents.size_bytes) WHERE deleted_at IS NULL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS storage_usage (
  user_id        BIGINT    PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  used_bytes     BIGINT    NOT NULL DEFAULT 0,
  document_count INT       NOT NULL DEFAULT 0,
  updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- sync_state -- per-device sync cursor.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_state (
  id             BIGSERIAL    PRIMARY KEY,
  user_id        BIGINT       NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  device_id      VARCHAR(128) NOT NULL,
  last_synced_at TIMESTAMP    NULL,
  cursor         VARCHAR(255) NULL,
  updated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uq_sync_user_device UNIQUE (user_id, device_id)
);

-- ---------------------------------------------------------------------------
-- student_applications -- historical only; the Student plan (and every paid
-- plan) was removed, Prism is fully free now. Left in place, unwritten, so
-- applications submitted before the removal aren't silently dropped.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS student_applications (
  id          VARCHAR(36)   PRIMARY KEY,
  user_email  VARCHAR(320)  NOT NULL,
  full_name   VARCHAR(255)  NOT NULL,
  institution VARCHAR(255)  NOT NULL,
  student_id  VARCHAR(128)  NULL,
  proof_path  VARCHAR(1024) NULL,
  status      VARCHAR(32)   NOT NULL DEFAULT 'pending',
  created_at  TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_student_applications_email ON student_applications (user_email);

-- ---------------------------------------------------------------------------
-- ai_chat_sessions -- Prism AI conversation history synced from the client.
-- messages_json mirrors the client's local JSON shape exactly (see
-- lib/services/chat_history_service.dart). Media attachments stay
-- device-local -- only the text conversation round-trips through here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_chat_sessions (
  id            VARCHAR(64)  PRIMARY KEY,
  user_email    VARCHAR(320) NOT NULL,
  title         VARCHAR(255) NOT NULL DEFAULT 'Conversation',
  messages_json TEXT         NOT NULL,
  created_at    TIMESTAMP    NOT NULL,
  updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_ai_chat_sessions_email_created ON ai_chat_sessions (user_email, created_at);

-- ---------------------------------------------------------------------------
-- ocr_jobs -- structured-OCR / book-reconstruction jobs. Backs the
-- GET /document/book/{job_id}/file download of the merged, numbered PDF.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ocr_jobs (
  id         VARCHAR(36)   PRIMARY KEY,
  user_email VARCHAR(320)  NULL,
  kind       VARCHAR(32)   NOT NULL DEFAULT 'book',
  status     VARCHAR(32)   NOT NULL DEFAULT 'completed',
  page_count INT           NOT NULL DEFAULT 0,
  pdf_path   VARCHAR(1024) NULL,
  json_path  VARCHAR(1024) NULL,
  error      TEXT          NULL,
  created_at TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_ocr_jobs_created ON ocr_jobs (created_at);

-- ---------------------------------------------------------------------------
-- admin_users -- Prism Scanner admin-dashboard operators. Distinct from
-- `users` (app users sign in with email+OTP; admins sign in with
-- email+password against this table instead). Created by hand (there's no
-- public signup) -- see backend/README.md for how to seed the first one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_users (
  id            BIGSERIAL    PRIMARY KEY,
  email         VARCHAR(320) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  display_name  VARCHAR(255) NULL,
  is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login_at TIMESTAMP    NULL
);

-- ---------------------------------------------------------------------------
-- devices -- one installed app instance (an FCM registration). user_id is
-- nullable so guests still receive broadcast notifications before signing
-- in. Keyed by fcm_token, not a client device id -- Firebase rotates
-- tokens, so re-registering with a fresh token just updates this row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS devices (
  id           BIGSERIAL    PRIMARY KEY,
  user_id      BIGINT       NULL REFERENCES users(id) ON DELETE SET NULL,
  fcm_token    VARCHAR(512) NOT NULL UNIQUE,
  platform     VARCHAR(32)  NULL,
  app_version  VARCHAR(32)  NULL,
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_devices_user ON devices (user_id);

-- ---------------------------------------------------------------------------
-- notifications -- history of admin-sent pushes (sending itself is
-- synchronous via the Firebase Admin SDK, not queued).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notifications (
  id                VARCHAR(36)   PRIMARY KEY,
  title             VARCHAR(255)  NOT NULL,
  body              TEXT          NOT NULL,
  image_url         VARCHAR(1024) NULL,
  target            VARCHAR(16)   NOT NULL, -- 'all' | 'device'
  target_device_id  BIGINT        NULL REFERENCES devices(id) ON DELETE SET NULL,
  sent_by_admin_id  BIGINT        NULL REFERENCES admin_users(id) ON DELETE SET NULL,
  success_count     INT           NOT NULL DEFAULT 0,
  failure_count     INT           NOT NULL DEFAULT 0,
  created_at        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_notifications_created ON notifications (created_at);

-- ---------------------------------------------------------------------------
-- app_config -- admin-editable key/value settings. Currently just the
-- force-update fields, read by the public GET /app/version-check endpoint
-- and written by the admin dashboard. Key/value rather than named columns
-- so a new setting never needs a migration.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_config (
  key        VARCHAR(64) PRIMARY KEY,
  value      TEXT        NOT NULL,
  updated_at TIMESTAMP   NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Seed rows -- ON CONFLICT DO NOTHING so re-running this script never
-- clobbers values the admin has since changed via the dashboard.
INSERT INTO app_config (key, value) VALUES ('min_supported_version', '1.0.1')
  ON CONFLICT (key) DO NOTHING;
INSERT INTO app_config (key, value) VALUES ('latest_version', '1.0.1')
  ON CONFLICT (key) DO NOTHING;
INSERT INTO app_config (key, value) VALUES ('play_store_url', '')
  ON CONFLICT (key) DO NOTHING;
