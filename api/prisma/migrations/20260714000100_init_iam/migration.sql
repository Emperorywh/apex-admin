/*
 * SPEC-0001 的首个 IAM 迁移。
 * 状态机、canonical email 与单活跃 refresh token 都由数据库最终兜底。
 */
CREATE TYPE "user_role" AS ENUM ('SUPER_ADMIN', 'ADMIN', 'OPERATOR', 'VIEWER');
CREATE TYPE "user_status" AS ENUM ('ACTIVE', 'DISABLED');
CREATE TYPE "auth_session_status" AS ENUM ('ACTIVE', 'REVOKED');
CREATE TYPE "refresh_token_status" AS ENUM ('ACTIVE', 'ROTATED', 'REVOKED');
CREATE TYPE "session_revocation_reason" AS ENUM ('LOGOUT', 'REFRESH_TOKEN_REPLAY', 'USER_DISABLED');
CREATE TYPE "security_audit_action" AS ENUM (
  'USER_CREATED',
  'USER_ROLE_CHANGED',
  'USER_STATUS_CHANGED',
  'SESSION_CREATED',
  'SESSION_REVOKED',
  'REFRESH_REPLAY_DETECTED'
);

CREATE TABLE "users" (
  "id" UUID NOT NULL,
  "email" VARCHAR(320) NOT NULL,
  "password_hash" TEXT NOT NULL,
  "role" "user_role" NOT NULL,
  "status" "user_status" NOT NULL DEFAULT 'ACTIVE',
  "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  "updated_at" TIMESTAMPTZ(3) NOT NULL,
  CONSTRAINT "users_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "ck_users_email_canonical" CHECK (
    email = lower(btrim(email))
    AND email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    AND length(email) <= 320
  )
);

CREATE TABLE "auth_sessions" (
  "id" UUID NOT NULL,
  "user_id" UUID NOT NULL,
  "status" "auth_session_status" NOT NULL DEFAULT 'ACTIVE',
  "expires_at" TIMESTAMPTZ(3) NOT NULL,
  "revoked_at" TIMESTAMPTZ(3),
  "revocation_reason" "session_revocation_reason",
  "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "auth_sessions_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "ck_auth_sessions_revocation" CHECK (
    expires_at > created_at
    AND (
      (status = 'ACTIVE' AND revoked_at IS NULL AND revocation_reason IS NULL)
      OR
      (
        status = 'REVOKED'
        AND revoked_at IS NOT NULL
        AND revoked_at >= created_at
        AND revocation_reason IS NOT NULL
      )
    )
  )
);

CREATE TABLE "refresh_tokens" (
  "id" UUID NOT NULL,
  "session_id" UUID NOT NULL,
  "token_hash" CHAR(64) NOT NULL,
  "status" "refresh_token_status" NOT NULL DEFAULT 'ACTIVE',
  "rotated_at" TIMESTAMPTZ(3),
  "revoked_at" TIMESTAMPTZ(3),
  "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "refresh_tokens_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "ck_refresh_tokens_status" CHECK (
    (status = 'ACTIVE' AND rotated_at IS NULL AND revoked_at IS NULL)
    OR
    (
      status = 'ROTATED'
      AND rotated_at IS NOT NULL
      AND rotated_at >= created_at
      AND revoked_at IS NULL
    )
    OR
    (
      status = 'REVOKED'
      AND rotated_at IS NULL
      AND revoked_at IS NOT NULL
      AND revoked_at >= created_at
    )
  )
);

CREATE TABLE "security_audit_events" (
  "id" UUID NOT NULL,
  "action" "security_audit_action" NOT NULL,
  "actor_user_id" UUID,
  "target_user_id" UUID,
  "session_id" UUID,
  "previous_role" "user_role",
  "next_role" "user_role",
  "previous_status" "user_status",
  "next_status" "user_status",
  "revocation_reason" "session_revocation_reason",
  "correlation_id" VARCHAR(64) NOT NULL,
  "created_at" TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT "security_audit_events_pkey" PRIMARY KEY ("id"),
  CONSTRAINT "ck_security_audit_events_state_payload" CHECK (
    (
      action = 'USER_CREATED'
      AND target_user_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NOT NULL
      AND previous_status IS NULL
      AND next_status IS NOT NULL
      AND revocation_reason IS NULL
    )
    OR
    (
      action = 'USER_ROLE_CHANGED'
      AND target_user_id IS NOT NULL
      AND previous_role IS NOT NULL
      AND next_role IS NOT NULL
      AND previous_role <> next_role
      AND previous_status IS NULL
      AND next_status IS NULL
      AND revocation_reason IS NULL
    )
    OR
    (
      action = 'USER_STATUS_CHANGED'
      AND target_user_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NULL
      AND previous_status IS NOT NULL
      AND next_status IS NOT NULL
      AND previous_status <> next_status
      AND revocation_reason IS NULL
    )
    OR
    (
      action = 'SESSION_CREATED'
      AND session_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NULL
      AND previous_status IS NULL
      AND next_status IS NULL
      AND revocation_reason IS NULL
    )
    OR
    (
      action = 'SESSION_REVOKED'
      AND session_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NULL
      AND previous_status IS NULL
      AND next_status IS NULL
      AND revocation_reason IS NOT NULL
    )
    OR
    (
      action = 'REFRESH_REPLAY_DETECTED'
      AND session_id IS NOT NULL
      AND previous_role IS NULL
      AND next_role IS NULL
      AND previous_status IS NULL
      AND next_status IS NULL
      AND revocation_reason = 'REFRESH_TOKEN_REPLAY'
    )
  )
);

CREATE UNIQUE INDEX "uq_users_email" ON "users"("email");
CREATE INDEX "idx_users_role_status" ON "users"("role", "status");
CREATE INDEX "idx_auth_sessions_user_status" ON "auth_sessions"("user_id", "status");
CREATE INDEX "idx_auth_sessions_expires" ON "auth_sessions"("expires_at");
CREATE UNIQUE INDEX "uq_refresh_tokens_hash" ON "refresh_tokens"("token_hash");
CREATE INDEX "idx_refresh_tokens_session_status" ON "refresh_tokens"("session_id", "status");
CREATE UNIQUE INDEX "uq_refresh_tokens_one_active_per_session"
  ON "refresh_tokens"("session_id")
  WHERE "status" = 'ACTIVE';
CREATE INDEX "idx_security_audit_events_created" ON "security_audit_events"("created_at");
CREATE INDEX "idx_security_audit_events_actor" ON "security_audit_events"("actor_user_id", "created_at");
CREATE INDEX "idx_security_audit_events_target" ON "security_audit_events"("target_user_id", "created_at");

ALTER TABLE "auth_sessions"
  ADD CONSTRAINT "auth_sessions_user_id_fkey"
  FOREIGN KEY ("user_id") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
ALTER TABLE "refresh_tokens"
  ADD CONSTRAINT "refresh_tokens_session_id_fkey"
  FOREIGN KEY ("session_id") REFERENCES "auth_sessions"("id") ON DELETE CASCADE ON UPDATE CASCADE;
