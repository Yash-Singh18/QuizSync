-- Phase 1: Core domain schema.
-- rooms, questions (options jsonb + server-only correct_option_ids),
-- participants, submissions (exactly-once), room_results, question_starts (timer).

-- ── Enums ──────────────────────────────────────────────────────────────────
CREATE TYPE room_status AS ENUM (
  'draft', 'ready', 'scheduled', 'live', 'closed', 'finalized'
);

CREATE TYPE participant_status AS ENUM (
  'in_lobby', 'active', 'finished'
);

-- ── rooms ──────────────────────────────────────────────────────────────────
CREATE TABLE rooms (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  host_id         uuid NOT NULL,            -- auth.users.id (Google OAuth)
  title           text NOT NULL,
  status          room_status NOT NULL DEFAULT 'draft',
  join_code       char(6),
  start_at        timestamptz,              -- T0; null until scheduled/started
  end_at          timestamptz,              -- T_end = T0 + Σduration + grace
  scoring_config  jsonb NOT NULL DEFAULT '{}',
  allow_late_join boolean NOT NULL DEFAULT false,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- join_code only needs to be unique among active rooms; recycled after finalize.
CREATE UNIQUE INDEX uniq_active_join_code ON rooms (join_code)
  WHERE status IN ('ready', 'scheduled', 'live');

-- scheduler poll: look up rooms by status + timing
CREATE INDEX idx_rooms_scheduler ON rooms (status, start_at, end_at);

ALTER TABLE rooms ENABLE ROW LEVEL SECURITY;

-- hosts can CRUD their own rooms
CREATE POLICY rooms_host_all ON rooms
  USING (host_id = auth.uid())
  WITH CHECK (host_id = auth.uid());

-- ── questions ─────────────────────────────────────────────────────────────
CREATE TABLE questions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  room_id            uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  order_index        int  NOT NULL,
  prompt             text NOT NULL,
  options            jsonb NOT NULL,        -- [{"id":"...","text":"..."}]
  correct_option_ids jsonb NOT NULL,        -- SERVER-ONLY; never in play responses
  duration_ms        int  NOT NULL,
  base_points        int  NOT NULL DEFAULT 1000,
  explanation        text,
  UNIQUE (room_id, order_index)
);

ALTER TABLE questions ENABLE ROW LEVEL SECURITY;

-- hosts can read/write questions for their rooms
CREATE POLICY questions_host_all ON questions
  USING (EXISTS (
    SELECT 1 FROM rooms r WHERE r.id = questions.room_id AND r.host_id = auth.uid()
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM rooms r WHERE r.id = questions.room_id AND r.host_id = auth.uid()
  ));

-- ── participants ──────────────────────────────────────────────────────────
CREATE TABLE participants (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  room_id                uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  display_name           text NOT NULL,
  session_token          uuid NOT NULL DEFAULT gen_random_uuid(),
  status                 participant_status NOT NULL DEFAULT 'in_lobby',
  current_question_index int  NOT NULL DEFAULT 0,
  score_total            int  NOT NULL DEFAULT 0,
  time_total_ms          bigint NOT NULL DEFAULT 0,
  joined_at              timestamptz NOT NULL DEFAULT now(),
  finished_at            timestamptz,
  UNIQUE (room_id, session_token)
);

CREATE INDEX idx_participants_room ON participants (room_id);

ALTER TABLE participants ENABLE ROW LEVEL SECURITY;
-- No direct client read; all play traffic goes through FastAPI with service role.

-- ── submissions ───────────────────────────────────────────────────────────
CREATE TABLE submissions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  room_id            uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  participant_id     uuid NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  question_id        uuid NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
  selected_option_id text,                 -- null = no answer / expired
  started_at         timestamptz NOT NULL,
  submitted_at       timestamptz NOT NULL,
  latency_ms         int  NOT NULL,
  is_correct         boolean NOT NULL,
  points_awarded     int  NOT NULL,
  created_at         timestamptz NOT NULL DEFAULT now(),
  UNIQUE (participant_id, question_id)     -- exactly-once guard
);

CREATE INDEX idx_submissions_room ON submissions (room_id);

ALTER TABLE submissions ENABLE ROW LEVEL SECURITY;
-- No direct client access; service role only.

-- ── room_results ──────────────────────────────────────────────────────────
-- Durable final standings; populated by finalize job (Phase 3).
-- Created now so the schema is complete; reads allowed after finalization.
CREATE TABLE room_results (
  room_id        uuid NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
  participant_id uuid NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  rank           int  NOT NULL,
  score_total    int  NOT NULL,
  time_total_ms  bigint NOT NULL,
  finalized_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (room_id, participant_id)
);

CREATE INDEX idx_results_rank ON room_results (room_id, rank);

ALTER TABLE room_results ENABLE ROW LEVEL SECURITY;

-- ── question_starts ───────────────────────────────────────────────────────
-- Phase-1 stand-in for Redis HSETNX; swapped for Redis in Phase 2.
-- Stores the canonical started_at per (participant, question) — idempotent.
CREATE TABLE question_starts (
  participant_id uuid NOT NULL REFERENCES participants(id) ON DELETE CASCADE,
  question_id    uuid NOT NULL REFERENCES questions(id)    ON DELETE CASCADE,
  started_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (participant_id, question_id)
);

ALTER TABLE question_starts ENABLE ROW LEVEL SECURITY;
-- Service role only.
