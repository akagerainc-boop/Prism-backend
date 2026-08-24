-- ---------------------------------------------------------------------------
-- Prism Scanner -- MySQL schema
--
-- Import via phpMyAdmin (Import tab) or the mysql CLI:
--     mysql -u root -p < schema.sql
--
-- Safe to re-run: every object uses IF NOT EXISTS. Dropping is NOT done here
-- on purpose so an accidental re-import can never destroy user data.
-- ---------------------------------------------------------------------------

CREATE DATABASE IF NOT EXISTS `prism`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `prism`;

-- ---------------------------------------------------------------------------
-- users -- one row per verified email. Created on first successful OTP verify
-- (signup and login are the same flow).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `users` (
  `id`            BIGINT       NOT NULL AUTO_INCREMENT,
  `email`         VARCHAR(320) NOT NULL,
  `created_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `last_login_at` DATETIME     NULL DEFAULT NULL,
  `is_active`     TINYINT(1)   NOT NULL DEFAULT 1,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_users_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- accounts -- Prism Cloud plan + the storage limit that plan grants.
-- Limits mirror lib/models/prism_plan.dart: free 50 MB, student 500 MB,
-- personal 5 GB (decimal MB/GB, matching how the plan copy reads).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `accounts` (
  `user_id`             BIGINT      NOT NULL,
  `plan`                VARCHAR(32) NOT NULL DEFAULT 'free',
  `storage_limit_bytes` BIGINT      NOT NULL DEFAULT 50000000,
  `created_at`          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  CONSTRAINT `fk_accounts_user` FOREIGN KEY (`user_id`)
    REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- otp_codes -- issued OTP challenges. The OTP itself is NEVER stored in
-- plaintext: otp_hash is PBKDF2-HMAC-SHA256(otp, otp_salt).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `otp_codes` (
  `id`                 BIGINT       NOT NULL AUTO_INCREMENT,
  `email`              VARCHAR(320) NOT NULL,
  `verification_token` VARCHAR(128) NOT NULL,
  `otp_hash`           VARCHAR(128) NOT NULL,
  `otp_salt`           VARCHAR(64)  NOT NULL,
  `expires_at`         DATETIME     NOT NULL,
  `consumed_at`        DATETIME     NULL DEFAULT NULL,
  `attempts`           INT          NOT NULL DEFAULT 0,
  `created_at`         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_otp_verification_token` (`verification_token`),
  KEY `ix_otp_codes_email` (`email`),
  KEY `ix_otp_codes_expires_at` (`expires_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- otp_requests -- request timestamps used for per-email rate limiting
-- (max 1 per 60s, max 5 per hour) so the Gmail sending quota can't be burned.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `otp_requests` (
  `id`           BIGINT       NOT NULL AUTO_INCREMENT,
  `email`        VARCHAR(320) NOT NULL,
  `requested_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `client_ip`    VARCHAR(64)  NULL DEFAULT NULL,
  `delivered`    TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  KEY `ix_otp_requests_email_time` (`email`, `requested_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- documents -- PDFs backed up to Prism Cloud. Bytes live on disk under
-- PRISM_STORAGE_ROOT/<user_id>/<document_id>.pdf; this table is the index.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `documents` (
  `id`              VARCHAR(36)   NOT NULL,
  `user_id`         BIGINT        NOT NULL,
  `name`            VARCHAR(512)  NOT NULL,
  `size_bytes`      BIGINT        NOT NULL DEFAULT 0,
  `storage_path`    VARCHAR(1024) NOT NULL,
  `content_type`    VARCHAR(128)  NOT NULL DEFAULT 'application/pdf',
  `checksum_sha256` VARCHAR(64)   NULL DEFAULT NULL,
  `created_at`      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `modified_at`     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                                  ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at`      DATETIME      NULL DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `ix_documents_user_modified` (`user_id`, `modified_at`),
  CONSTRAINT `fk_documents_user` FOREIGN KEY (`user_id`)
    REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- storage_usage -- cheap-to-read cache of consumed bytes. The authoritative
-- value is always SUM(documents.size_bytes) WHERE deleted_at IS NULL.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `storage_usage` (
  `user_id`        BIGINT   NOT NULL,
  `used_bytes`     BIGINT   NOT NULL DEFAULT 0,
  `document_count` INT      NOT NULL DEFAULT 0,
  `updated_at`     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  CONSTRAINT `fk_storage_usage_user` FOREIGN KEY (`user_id`)
    REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- sync_state -- per-device sync cursor.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `sync_state` (
  `id`             BIGINT       NOT NULL AUTO_INCREMENT,
  `user_id`        BIGINT       NOT NULL,
  `device_id`      VARCHAR(128) NOT NULL,
  `last_synced_at` DATETIME     NULL DEFAULT NULL,
  `cursor`         VARCHAR(255) NULL DEFAULT NULL,
  `updated_at`     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_sync_user_device` (`user_id`, `device_id`),
  CONSTRAINT `fk_sync_state_user` FOREIGN KEY (`user_id`)
    REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- student_applications -- submitted Student-plan applications. No
-- verification service is wired up yet; status stays 'pending' until
-- someone reviews it by hand.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `student_applications` (
  `id`          VARCHAR(36)   NOT NULL,
  `user_email`  VARCHAR(320)  NOT NULL,
  `full_name`   VARCHAR(255)  NOT NULL,
  `institution` VARCHAR(255)  NOT NULL,
  `student_id`  VARCHAR(128)  NULL DEFAULT NULL,
  `proof_path`  VARCHAR(1024) NULL DEFAULT NULL,
  `status`      VARCHAR(32)   NOT NULL DEFAULT 'pending',
  `created_at`  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_student_applications_email` (`user_email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- ai_chat_sessions -- Prism AI conversation history synced from the client.
-- messages_json mirrors the client's local JSON shape exactly (see
-- lib/services/chat_history_service.dart). Media attachments stay
-- device-local -- only the text conversation round-trips through here.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ai_chat_sessions` (
  `id`            VARCHAR(64)  NOT NULL,
  `user_email`    VARCHAR(320) NOT NULL,
  `title`         VARCHAR(255) NOT NULL DEFAULT 'Conversation',
  `messages_json` TEXT         NOT NULL,
  `created_at`    DATETIME     NOT NULL,
  `updated_at`    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                               ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_ai_chat_sessions_email_created` (`user_email`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ---------------------------------------------------------------------------
-- ocr_jobs -- structured-OCR / book-reconstruction jobs. Backs the
-- GET /document/book/{job_id}/file download of the merged, numbered PDF.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `ocr_jobs` (
  `id`         VARCHAR(36)   NOT NULL,
  `user_email` VARCHAR(320)  NULL DEFAULT NULL,
  `kind`       VARCHAR(32)   NOT NULL DEFAULT 'book',
  `status`     VARCHAR(32)   NOT NULL DEFAULT 'completed',
  `page_count` INT           NOT NULL DEFAULT 0,
  `pdf_path`   VARCHAR(1024) NULL DEFAULT NULL,
  `json_path`  VARCHAR(1024) NULL DEFAULT NULL,
  `error`      TEXT          NULL DEFAULT NULL,
  `created_at` DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `ix_ocr_jobs_created` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
