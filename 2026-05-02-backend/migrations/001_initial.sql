-- =============================================================================
-- Eksamens Hjelp — Initial Schema Migration
-- Database: Supabase (Postgres 15)
-- Run in Supabase SQL editor or via supabase db push
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- for fødselsnummer encryption

-- =============================================================================
-- USERS
-- =============================================================================

CREATE TABLE IF NOT EXISTS users (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email               TEXT UNIQUE NOT NULL,
  full_name           TEXT NOT NULL,
  avatar_url          TEXT,
  bio                 TEXT,
  location            TEXT,
  is_teacher          BOOLEAN NOT NULL DEFAULT false,
  is_student          BOOLEAN NOT NULL DEFAULT true,
  is_deleted          BOOLEAN NOT NULL DEFAULT false,
  deleted_at          TIMESTAMPTZ,
  bankid_verified     BOOLEAN NOT NULL DEFAULT false,
  criipto_subject_id  TEXT,               -- BankID de-duplication only; no fødselsnummer stored
  stripe_account_id   TEXT,
  stripe_payouts_enabled BOOLEAN NOT NULL DEFAULT false,
  vipps_msisdn        TEXT,
  avg_rating          NUMERIC(3,2),
  review_count        INTEGER NOT NULL DEFAULT 0,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- RLS
ALTER TABLE users ENABLE ROW LEVEL SECURITY;

-- Anyone can read non-deleted public profiles
CREATE POLICY "users_public_read" ON users
  FOR SELECT USING (is_deleted = false);

-- Users can update only their own row
CREATE POLICY "users_update_own" ON users
  FOR UPDATE USING (auth.uid() = id);

-- =============================================================================
-- SKILL LISTINGS
-- =============================================================================

CREATE TABLE IF NOT EXISTS skill_listings (
  id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  teacher_id               UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title                    TEXT NOT NULL,
  description              TEXT NOT NULL,
  category                 TEXT NOT NULL CHECK (category IN ('language','music','fitness','cooking','programming','other')),
  tags                     TEXT[] NOT NULL DEFAULT '{}',
  price_per_hour_ore       INTEGER NOT NULL CHECK (price_per_hour_ore > 0),
  currency                 TEXT NOT NULL DEFAULT 'NOK',
  delivery_mode            TEXT NOT NULL CHECK (delivery_mode IN ('online','in_person','both')),
  location                 TEXT,
  session_duration_minutes INTEGER NOT NULL DEFAULT 60 CHECK (session_duration_minutes >= 15),
  max_students             INTEGER NOT NULL DEFAULT 1 CHECK (max_students >= 1),
  cover_image_url          TEXT,
  is_active                BOOLEAN NOT NULL DEFAULT true,
  -- Postgres full-text search vector (updated by trigger below)
  fts                      TSVECTOR,
  created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS skill_listings_category_idx ON skill_listings(category) WHERE is_active = true;
CREATE INDEX IF NOT EXISTS skill_listings_fts_idx ON skill_listings USING GIN(fts);

-- Full-text search trigger
CREATE OR REPLACE FUNCTION skill_listings_fts_update() RETURNS trigger AS $$
BEGIN
  NEW.fts := to_tsvector('norwegian', COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.description, ''));
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER skill_listings_fts_trigger
  BEFORE INSERT OR UPDATE ON skill_listings
  FOR EACH ROW EXECUTE FUNCTION skill_listings_fts_update();

ALTER TABLE skill_listings ENABLE ROW LEVEL SECURITY;

-- Active listings are publicly readable
CREATE POLICY "listings_public_read" ON skill_listings
  FOR SELECT USING (is_active = true);

-- Teachers can CRUD their own listings
CREATE POLICY "listings_teacher_write" ON skill_listings
  FOR ALL USING (auth.uid() = teacher_id);

-- =============================================================================
-- BOOKINGS
-- =============================================================================

CREATE TABLE IF NOT EXISTS bookings (
  id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id                      UUID NOT NULL REFERENCES skill_listings(id),
  student_id                      UUID NOT NULL REFERENCES users(id),
  teacher_id                      UUID NOT NULL REFERENCES users(id),
  status                          TEXT NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending','confirmed','cancelled','completed')),
  scheduled_at                    TIMESTAMPTZ NOT NULL,
  duration_minutes                INTEGER NOT NULL CHECK (duration_minutes >= 15),
  total_amount_ore                INTEGER NOT NULL CHECK (total_amount_ore > 0),
  platform_fee_ore                INTEGER NOT NULL,
  payment_status                  TEXT NOT NULL DEFAULT 'unpaid'
                                    CHECK (payment_status IN ('unpaid','paid','refunded')),
  payment_method                  TEXT CHECK (payment_method IN ('vipps','stripe')),
  stripe_payment_intent_id        TEXT,
  vipps_order_id                  TEXT,
  cancellation_reason             TEXT,
  cancelled_by                    TEXT CHECK (cancelled_by IN ('student','teacher','admin')),
  -- GDPR / angrerettloven consent
  withdrawal_rights_accepted      BOOLEAN NOT NULL DEFAULT false,
  withdrawal_rights_accepted_at   TIMESTAMPTZ,
  created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at                      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;

-- Students and teachers can only see their own bookings
CREATE POLICY "bookings_read_own" ON bookings
  FOR SELECT USING (auth.uid() = student_id OR auth.uid() = teacher_id);

CREATE POLICY "bookings_student_insert" ON bookings
  FOR INSERT WITH CHECK (auth.uid() = student_id);

CREATE POLICY "bookings_update_own" ON bookings
  FOR UPDATE USING (auth.uid() = student_id OR auth.uid() = teacher_id);

-- =============================================================================
-- SESSIONS
-- =============================================================================

CREATE TABLE IF NOT EXISTS sessions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id  UUID NOT NULL REFERENCES bookings(id) UNIQUE,
  meeting_url TEXT,
  started_at  TIMESTAMPTZ,
  ended_at    TIMESTAMPTZ,
  notes       TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "sessions_read_participants" ON sessions
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM bookings b
      WHERE b.id = booking_id
        AND (b.student_id = auth.uid() OR b.teacher_id = auth.uid())
    )
  );

-- =============================================================================
-- PAYMENTS
-- =============================================================================

CREATE TABLE IF NOT EXISTS payments (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id              UUID NOT NULL REFERENCES bookings(id),
  payer_id                UUID NOT NULL REFERENCES users(id),
  payee_id                UUID NOT NULL REFERENCES users(id),
  amount_gross            INTEGER NOT NULL,
  platform_fee            INTEGER NOT NULL,
  amount_net              INTEGER NOT NULL,
  currency                TEXT NOT NULL DEFAULT 'NOK',
  payment_method          TEXT NOT NULL CHECK (payment_method IN ('vipps','stripe')),
  external_id             TEXT NOT NULL,
  status                  TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','succeeded','failed','refunded')),
  payout_transfer_id      TEXT,
  payout_initiated_at     TIMESTAMPTZ,
  paid_at                 TIMESTAMPTZ,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
  -- Note: payment records retained 5 years — Bokføringsloven (Norwegian accounting law)
  -- Do NOT delete these on user account deletion.
);

ALTER TABLE payments ENABLE ROW LEVEL SECURITY;

CREATE POLICY "payments_read_own" ON payments
  FOR SELECT USING (auth.uid() = payer_id OR auth.uid() = payee_id);

-- =============================================================================
-- REVIEWS
-- =============================================================================

CREATE TABLE IF NOT EXISTS reviews (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id  UUID NOT NULL REFERENCES bookings(id) UNIQUE,
  reviewer_id UUID NOT NULL REFERENCES users(id),
  reviewee_id UUID NOT NULL REFERENCES users(id),
  listing_id  UUID NOT NULL REFERENCES skill_listings(id),
  rating      SMALLINT NOT NULL CHECK (rating BETWEEN 1 AND 5),
  comment     TEXT CHECK (char_length(comment) <= 2000),
  is_public   BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE reviews ENABLE ROW LEVEL SECURITY;

CREATE POLICY "reviews_public_read" ON reviews
  FOR SELECT USING (is_public = true);

CREATE POLICY "reviews_write_own" ON reviews
  FOR INSERT WITH CHECK (auth.uid() = reviewer_id);

-- Trigger: update denormalised avg_rating and review_count on users table
CREATE OR REPLACE FUNCTION update_user_avg_rating() RETURNS trigger AS $$
BEGIN
  UPDATE users SET
    avg_rating = (
      SELECT ROUND(AVG(rating)::NUMERIC, 2)
      FROM reviews
      WHERE reviewee_id = NEW.reviewee_id AND is_public = true
    ),
    review_count = (
      SELECT COUNT(*) FROM reviews WHERE reviewee_id = NEW.reviewee_id AND is_public = true
    )
  WHERE id = NEW.reviewee_id;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER reviews_update_avg_rating
  AFTER INSERT ON reviews
  FOR EACH ROW EXECUTE FUNCTION update_user_avg_rating();

-- =============================================================================
-- CONVERSATIONS + MESSAGES
-- =============================================================================

CREATE TABLE IF NOT EXISTS conversations (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  booking_id      UUID REFERENCES bookings(id),
  participant_ids UUID[] NOT NULL,
  last_message_at TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS conversations_participants_idx ON conversations USING GIN(participant_ids);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;

CREATE POLICY "conversations_read_participants" ON conversations
  FOR SELECT USING (auth.uid() = ANY(participant_ids));

CREATE POLICY "conversations_insert_participants" ON conversations
  FOR INSERT WITH CHECK (auth.uid() = ANY(participant_ids));


CREATE TABLE IF NOT EXISTS messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  sender_id       UUID NOT NULL REFERENCES users(id),
  content         TEXT NOT NULL CHECK (char_length(content) <= 4000),
  is_read         BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages(conversation_id, created_at DESC);

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

CREATE POLICY "messages_read_participants" ON messages
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM conversations c
      WHERE c.id = conversation_id AND auth.uid() = ANY(c.participant_ids)
    )
  );

CREATE POLICY "messages_insert_sender" ON messages
  FOR INSERT WITH CHECK (auth.uid() = sender_id);

-- Enable Supabase Realtime on messages table
ALTER PUBLICATION supabase_realtime ADD TABLE messages;

-- =============================================================================
-- GDPR: CONSENT RECORDS
-- =============================================================================

CREATE TABLE IF NOT EXISTS consent_records (
  id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  consent_type            TEXT NOT NULL,   -- 'registration','marketing','analytics'
  privacy_policy_version  TEXT,
  terms_version           TEXT,
  accepted_at             TIMESTAMPTZ,
  withdrawn_at            TIMESTAMPTZ,
  ip_address              INET,
  created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
  -- Retained: consent records kept until withdrawal + 1 year (GDPR Art. 7 audit trail)
);

ALTER TABLE consent_records ENABLE ROW LEVEL SECURITY;

CREATE POLICY "consent_read_own" ON consent_records
  FOR SELECT USING (auth.uid() = user_id);

-- =============================================================================
-- DAC7 / TAX: INSTRUCTOR TAX INFO
-- =============================================================================

CREATE TABLE IF NOT EXISTS instructor_tax_info (
  id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                   UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT UNIQUE,
  legal_name                TEXT NOT NULL,
  -- fødselsnummer encrypted with pgcrypto using a server-side encryption key
  -- NEVER store in plaintext. Lawful basis: Legal Obligation (GDPR Art. 6(1)(c) — DAC7)
  foedselsnummer_encrypted  BYTEA NOT NULL,
  bank_account_no           TEXT NOT NULL,
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
  -- Retained: 5 years minimum (Skatteforvaltningsloven)
);

-- No RLS read for regular users — only service role can read for tax reporting
ALTER TABLE instructor_tax_info ENABLE ROW LEVEL SECURITY;

CREATE POLICY "tax_info_no_user_read" ON instructor_tax_info
  FOR SELECT USING (false);  -- only service role bypasses RLS

-- =============================================================================
-- GDPR PURGE LOG (audit trail for deletion requests)
-- =============================================================================

CREATE TABLE IF NOT EXISTS gdpr_purge_log (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL,   -- not a FK — user row may be gone
  purged_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  purge_type  TEXT NOT NULL
);

-- No RLS — admin-only table, accessed via service role
ALTER TABLE gdpr_purge_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY "purge_log_no_user_access" ON gdpr_purge_log FOR SELECT USING (false);
